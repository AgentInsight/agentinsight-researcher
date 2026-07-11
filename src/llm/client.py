"""LiteLLM 网关封装.

AGENTS.md 第 9 章硬约束:
- 全部 LLM 调用经 llm/ 的 LLMClient (底层 LiteLLM ≥1.6)
- 禁止直接 openai/anthropic 等 SDK
- 模型名以 LiteLLM 路由前缀声明 (如 deepseek/deepseek-chat), 由配置注入, 禁止硬编码
- 流式统一 achat_stream; 同步 chat 仅用于非交互式批处理

三级 LLM 模式 (用户需求 10 Token 优化):
- FAST_LLM: 快速任务 (摘要)
- SMART_LLM: 复杂推理 (报告写作, 支持 2k+ 字长响应)
- STRATEGIC_LLM: 规划 (agent 选择, 慢但精)

P1-Future-01 step_costs 分步成本追踪:
- _accumulate(step, ...) 按步骤累加成本, get_session_cost 返回 step_costs 分布.

P1-Future-05 LLM 降级链 (strategic → smart → fast):
- achat/achat_stream 在 tier 调用失败时按 _FALLBACK_TIER 逐级降级, FAST 失败则抛出.
- 降级仅在 "调用失败" 时触发; 流式已开始 yield 后不降级 (无法回滚已输出内容).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, cast

import litellm
import orjson

from src.common.llm_key_resolver import resolve_api_key
from src.config.settings import Settings, get_settings
from src.observability.tracing import trace_generation

logger = logging.getLogger(__name__)


# ========== 模块级定价表 (USD per 1K tokens, 参考 2026 年公开定价) ==========
# 支持模型名前缀匹配: 如 "deepseek/deepseek-chat-2026-01-01" 命中 "deepseek/deepseek-chat".
# 命中失败时 _compute_cost 返回 0.0 并记录 warning 日志 (不再用兜底费率避免误算,
# 详见 AGENTS.md 第 4 章避免静默错误).
LITELLM_PRICING_TABLE: dict[str, dict[str, float]] = {
    # ========== DeepSeek ==========
    "deepseek/deepseek-chat": {"input": 0.0014, "output": 0.0028},
    "deepseek/deepseek-reasoner": {"input": 0.0055, "output": 0.022},
    # deepseek-v4-flash (新模型, 用于图像生成): 待官方公布精确价格, 暂用 deepseek-chat 同档.
    "deepseek/deepseek-v4-flash": {"input": 0.0014, "output": 0.0028},
    # deepseek-v4-pro (STRATEGIC_LLM 默认): 2026-05 永久降价 75%, 约为 v4-flash 的 3 倍
    # (官方折扣后 3 元/M input, 6 元/M output vs v4-flash 1 元/M input, 2 元/M output).
    "deepseek/deepseek-v4-pro": {"input": 0.0042, "output": 0.0084},
    # ========== OpenAI ==========
    "openai/gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "openai/gpt-4o": {"input": 0.0025, "output": 0.01},
    "openai/gpt-4.1": {"input": 0.002, "output": 0.008},
    "openai/gpt-4.1-mini": {"input": 0.0004, "output": 0.0016},
    "openai/gpt-4-turbo": {"input": 0.01, "output": 0.03},
    "openai/gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
    "openai/o1-mini": {"input": 0.0011, "output": 0.0044},
    "openai/o1-preview": {"input": 0.0015, "output": 0.006},
    # ========== Anthropic ==========
    "anthropic/claude-3-5-sonnet": {"input": 0.003, "output": 0.015},
    "anthropic/claude-3-5-haiku": {"input": 0.0008, "output": 0.004},
    "anthropic/claude-3-opus": {"input": 0.015, "output": 0.075},
    "anthropic/claude-3-sonnet": {"input": 0.003, "output": 0.015},
    "anthropic/claude-3-haiku": {"input": 0.00025, "output": 0.00125},
    # ========== Google Gemini ==========
    "gemini/gemini-1.5-flash": {"input": 0.000075, "output": 0.0003},
    "gemini/gemini-1.5-pro": {"input": 0.00125, "output": 0.005},
    "gemini/gemini-2.0-flash": {"input": 0.0001, "output": 0.0004},
    # ========== 通义 Qwen (DashScope) ==========
    "dashscope/qwen-plus": {"input": 0.00057, "output": 0.00171},
    "dashscope/qwen-turbo": {"input": 0.00014, "output": 0.00028},
    "dashscope/qwen-max": {"input": 0.0028, "output": 0.0084},
    # ========== 智谱 GLM ==========
    # 项目配置用 zhipuai/ 前缀, _adapt_zhipu 适配到 litellm 原生 zai/ 路由.
    # _compute_cost 用原始 zhipuai/ 前缀查定价 (无需 zai/ 双重注册).
    "zhipuai/glm-4-plus": {"input": 0.007, "output": 0.007},
    "zhipuai/glm-4-flash": {"input": 0.0001, "output": 0.0001},
    "zhipuai/glm-4-air": {"input": 0.0005, "output": 0.0005},
    # ========== 月之暗面 Moonshot ==========
    "moonshot/moonshot-v1-8k": {"input": 0.0017, "output": 0.0017},
    "moonshot/moonshot-v1-32k": {"input": 0.0034, "output": 0.0034},
    "moonshot/moonshot-v1-128k": {"input": 0.0085, "output": 0.0085},
    # ========== Mistral ==========
    "mistral/mistral-large-latest": {"input": 0.002, "output": 0.006},
    "mistral/mistral-small-latest": {"input": 0.0002, "output": 0.0006},
    # ========== Meta Llama via Together ==========
    "together_ai/Meta-Llama-3.1-70B-Instruct-Turbo": {"input": 0.00088, "output": 0.00088},
    "together_ai/Meta-Llama-3.1-405B-Instruct-Turbo": {"input": 0.005, "output": 0.005},
}


class LLMTier(StrEnum):
    """LLM 三级分层."""

    FAST = "fast"  # 快速任务 (摘要)
    SMART = "smart"  # 复杂推理 (报告写作)
    STRATEGIC = "strategic"  # 规划 (agent 选择)


@dataclass
class LLMResponse:
    """LLM 调用响应."""

    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    # 成本明细: input_cost/output_cost/total_cost (USD, 各项保留 6 位小数)
    cost_breakdown: dict[str, float] | None = None
    raw: Any = None


@dataclass
class LLMClient:
    """LiteLLM 网关客户端.

    AGENTS.md 第 9 章: 全部 LLM 调用经此客户端, 禁厂商 SDK 直连.
    所有调用必须包裹在 trace_generation span 内 (AGENTS.md 第 10 章).

    P1-Future-01: _step_costs 按业务步骤累计成本, get_session_cost 返回分布.
    P1-Future-05: achat/achat_stream 支持 tier 降级链 (strategic → smart → fast).
    """

    settings: Settings = field(default_factory=get_settings)
    # P0-1: 成本追踪改为 per-session 隔离 (全局单例不再累积跨会话成本)
    # key = session_id, value = {call_count, input_tokens, output_tokens, cost_usd, step_costs}
    _session_costs: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)

    # P1-Future-05: tier 降级链映射. FAST 失败后无降级 (None), 抛出原异常.
    _FALLBACK_TIER: ClassVar[dict[LLMTier, LLMTier | None]] = {
        LLMTier.STRATEGIC: LLMTier.SMART,
        LLMTier.SMART: LLMTier.FAST,
        LLMTier.FAST: None,
    }
    # P2/P1-3: tier → Settings 字段名字典查表, 取代 3 个 if-elif 链
    _TIER_MODEL_FIELD: ClassVar[dict[LLMTier, str]] = {
        LLMTier.FAST: "fast_llm",
        LLMTier.SMART: "smart_llm",
        LLMTier.STRATEGIC: "strategic_llm",
    }
    _TIER_TOKEN_LIMIT_FIELD: ClassVar[dict[LLMTier, str]] = {
        LLMTier.FAST: "fast_token_limit",
        LLMTier.SMART: "smart_token_limit",
        LLMTier.STRATEGIC: "strategic_token_limit",
    }

    def _get_model(self, tier: LLMTier) -> str:
        """按层级获取模型名 (P2: 字典查表取代 if-elif)."""
        field = self._TIER_MODEL_FIELD.get(tier)
        if field is None:
            raise ValueError(f"未知 LLM 层级: {tier}")
        return cast(str, getattr(self.settings, field))

    def _get_token_limit(self, tier: LLMTier) -> int:
        """按层级获取 token 上限 (P2: 字典查表取代 if-elif)."""
        field = self._TIER_TOKEN_LIMIT_FIELD.get(tier)
        if field is None:
            return 4000
        return cast(int, getattr(self.settings, field))

    def _adapt_zhipu(self, model: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """智谱 AI litellm 原生 zai/ 路由适配.

        根因分析 (任务6 治本修复):
        - litellm 1.83.7 原生支持 zai/ 路由前缀 (智谱 GLM), 基于 OpenAIGPTConfig
        - 默认 API base: https://api.z.ai/api/paas/v4 (智谱国际端点)
        - 项目配置用 zhipuai/ 前缀 (非 litellm 标准), 需适配到 zai/

        原方案 (已废弃): 适配为 openai/ + api_base hack → 导致 _compute_cost 查
        openai/glm-4-flash 定价表查不到 (定价表 key 是 zhipuai/glm-4-flash).

        治本方案: 适配到 zai/ (litellm 原生路由), 保留原始 model 用于成本计算.
        - litellm 用 zai/glm-4-flash 调用 (原生支持, 无需 api_base hack)
        - _compute_cost 用原始 zhipuai/glm-4-flash 查定价 (定价表已有)
        - LLMResponse.model 显示原始 zhipuai/glm-4-flash (用户透明)

        Args:
            model: 原始模型名 (如 zhipuai/glm-4-flash)
            kwargs: LiteLLM acompletion kwargs

        Returns:
            (适配后 model 用于 litellm 调用, 适配后 kwargs)
            注意: 调用方应保留原始 model 用于 _compute_cost 和 LLMResponse.model
        """
        if not (model.startswith("zhipu/") or model.startswith("zhipuai/")):
            return model, kwargs
        # 兼容 zhipu/ 和 zhipuai/ 两种前缀, 统一适配到 litellm 原生 zai/
        prefix = "zhipuai/" if model.startswith("zhipuai/") else "zhipu/"
        model_name = model[len(prefix) :]
        adapted_model = f"zai/{model_name}"
        kwargs["model"] = adapted_model
        kwargs["api_key"] = self.settings.zhipu_api_key
        # 用国内端点 (open.bigmodel.cn) 替代默认国际端点 (api.z.ai), 国内访问更快
        kwargs["api_base"] = self.settings.zhipu_api_base
        return adapted_model, kwargs

    @staticmethod
    def _lookup_pricing(model: str) -> dict[str, float] | None:
        """查定价表, 支持前缀匹配.

        - 精确匹配优先.
        - 前缀匹配: 如 "deepseek/deepseek-chat-2026-01-01" 命中 "deepseek/deepseek-chat".
        - 多个前缀命中时取最长前缀 (最精确), 避免短前缀误命中.

        任务6 治本: 不再需要智谱 GLM 回退逻辑, 因为 _compute_cost 收到的是原始 model
        (如 zhipuai/glm-4-flash), 而非适配后的 zai/glm-4-flash.
        """
        # 1. 精确命中
        if model in LITELLM_PRICING_TABLE:
            return LITELLM_PRICING_TABLE[model]
        # 2. 前缀匹配 (取最长 key, 避免短前缀冲突)
        matched: dict[str, float] | None = None
        matched_key_len = -1
        for key, rate in LITELLM_PRICING_TABLE.items():
            if model.startswith(key) and len(key) > matched_key_len:
                matched = rate
                matched_key_len = len(key)
        return matched

    def _compute_cost(self, model: str, input_tokens: int, output_tokens: int) -> dict[str, float]:
        """计算 LLM 调用成本 (USD).

        返回 {"input_cost", "output_cost", "total_cost"} dict.
        - 单价: USD per 1K tokens (参考 2026 公开定价, 见 LITELLM_PRICING_TABLE).
        - 支持模型名前缀匹配 (如 "deepseek/deepseek-chat-2026-01-01" 也能命中).
        - 命中失败时记录 warning 日志, 返回全 0 (不再用兜底 0.001/0.002, 避免误算).
        - 各项保留 6 位小数.
        """
        rate = self._lookup_pricing(model)
        if rate is None:
            logger.warning("未找到模型定价: %s, cost 返回 0.0", model)
            return {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0}
        input_cost = (input_tokens / 1000) * rate["input"]
        output_cost = (output_tokens / 1000) * rate["output"]
        return {
            "input_cost": round(input_cost, 6),
            "output_cost": round(output_cost, 6),
            "total_cost": round(input_cost + output_cost, 6),
        }

    def _accumulate(
        self,
        session_id: str,
        step: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        """累加 per-session 成本统计 (每次 achat/achat_stream 成功后调用).

        P0-1: 成本按 session_id 隔离, 全局单例不再累积跨会话成本.
        P1-Future-01: 同时按 step 累计分步成本, 供 get_session_cost 返回分布.
        """
        sid = session_id or "_default"
        if sid not in self._session_costs:
            self._session_costs[sid] = {
                "call_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "step_costs": {},
            }
        sc = self._session_costs[sid]
        sc["call_count"] += 1
        sc["input_tokens"] += input_tokens
        sc["output_tokens"] += output_tokens
        sc["cost_usd"] = round(sc["cost_usd"] + cost_usd, 6)
        sc["step_costs"][step] = round(sc["step_costs"].get(step, 0.0) + cost_usd, 6)

    def get_session_cost(self, session_id: str = "") -> dict[str, Any]:
        """返回指定会话的累计成本统计 (P0-1: per-session 隔离).

        Args:
            session_id: 会话 ID. 空字符串返回空统计 (不返回全局累计).

        Returns:
            含 call_count / input_tokens / output_tokens / cost_usd / step_costs.
        """
        sid = session_id or "_default"
        sc = self._session_costs.get(sid)
        if sc is None:
            return {
                "call_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "step_costs": {},
            }
        return {
            "call_count": sc["call_count"],
            "input_tokens": sc["input_tokens"],
            "output_tokens": sc["output_tokens"],
            "cost_usd": round(sc["cost_usd"], 6),
            "step_costs": dict(sc["step_costs"]),
        }

    def cleanup_session_cost(self, session_id: str) -> None:
        """清理指定会话的成本数据 (会话结束时调用, 防止内存泄漏)."""
        self._session_costs.pop(session_id, None)

    async def _achat_with_tier(
        self,
        messages: list[dict[str, str]],
        tier: LLMTier,
        *,
        temperature: float | None,
        max_tokens: int | None,
        stop: list[str] | None,
    ) -> LLMResponse:
        """按指定 tier 执行单次非流式 LLM 调用 (不含 trace span, 由 achat 包裹).

        P1-Future-05: 抽取为独立方法, 供 achat 降级链逐 tier 调用.
        """
        model = self._get_model(tier)
        token_limit = max_tokens or self._get_token_limit(tier)
        temp = temperature if temperature is not None else self.settings.temperature
        api_key = resolve_api_key(model, self.settings)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": token_limit,
            "timeout": self.settings.llm_timeout,
            "num_retries": self.settings.llm_max_retries,
        }
        if stop:
            kwargs["stop"] = stop
        if api_key:
            kwargs["api_key"] = api_key

        # V2-P0: 智谱 AI 用 litellm 原生 zai/ 路由 (zhipuai/ → zai/)
        # 任务6 治本: 保留原始 model 用于成本计算, adapted_model 仅用于 litellm 调用
        original_model = model  # 保留原始 model (如 zhipuai/glm-4-flash) 用于 _compute_cost
        _, kwargs = self._adapt_zhipu(model, kwargs)

        response = await litellm.acompletion(**kwargs)

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
        output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
        breakdown = self._compute_cost(original_model, input_tokens, output_tokens)
        cost_usd = breakdown["total_cost"]
        content = response.choices[0].message.content or ""

        return LLMResponse(
            content=content,
            model=original_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            cost_breakdown=breakdown,
            raw=response,
        )

    # ========== P2-2: LLM 响应缓存 (Redis) ==========
    # 用户硬约束: "出错了不要存缓存" — 仅缓存成功响应, 异常/错误响应绝不缓存.
    # P1-4: 放宽缓存条件 — temperature ≤ _CACHE_MAX_TEMPERATURE 时缓存
    # (planner/curator/context-summarize 等场景 temperature=0.2/0.3, 结构化 JSON 解析
    # 不受轻微随机性影响; 可通过本常量回退到 0.0 严格模式).
    # 流式响应 (achat_stream) 不缓存 (流式无法等价复用).
    # Redis 不可用时降级为不缓存, 不阻断主流程; 缓存写入失败仅 warn 不抛出.

    # P1-4: 缓存允许的最大温度 (≤ 此值才缓存). 设为 0.0 即回退严格模式.
    _CACHE_MAX_TEMPERATURE: ClassVar[float] = 0.3

    def _llm_cache_key(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int,
        stop: list[str] | None,
    ) -> str:
        """构建 LLM 响应缓存键 (P2-2).

        AGENTS.md 第 7 章 Redis 约定: {agent_id}:{user_id}:{module}:{type}:{id}
        LLM 响应缓存为全局级 (不区分用户/会话, 因 temp=0 时 LLM 输出确定性),
        使用 _global 占位. 缓存维度: model + messages + temperature + max_tokens + stop.

        Args:
            messages: 消息列表.
            model: 模型名.
            temperature: 温度 (P1-4: ≤ _CACHE_MAX_TEMPERATURE 时调用方才会缓存).
            max_tokens: 最大输出 token 数.
            stop: 停止序列 (影响输出, 必须纳入 key 保证正确性).

        Returns:
            Redis 缓存键字符串.
        """
        agent_id = self.settings.agent_name
        # 序列化 messages 用于 hash (sort_keys 保证顺序稳定, default=str 兜底不可序列化)
        try:
            msgs_bytes = orjson.dumps(messages, option=orjson.OPT_SORT_KEYS, default=str)
        except (TypeError, ValueError):
            msgs_bytes = repr(messages).encode("utf-8")
        stop_bytes = (
            orjson.dumps(stop, option=orjson.OPT_SORT_KEYS, default=str) if stop else b"None"
        )
        payload = (
            f"{model}\x1f{temperature}\x1f{max_tokens}\x1f".encode()
            + msgs_bytes
            + b"\x1f"
            + stop_bytes
        )
        key_hash = hashlib.sha256(payload).hexdigest()
        return f"{agent_id}:_global:llm:response:{key_hash}"

    async def _get_llm_cache(self, key: str) -> LLMResponse | None:
        """读取 LLM 响应缓存 (P2-2).

        Redis 不可用或读取异常时降级返回 None, 不阻断主流程.
        """
        if not self.settings.llm_response_cache_enabled:
            return None
        try:
            # 延迟导入避免循环依赖 (common/ 不依赖 llm/)
            from src.common.redis_client import get_redis_client

            r = await get_redis_client(self.settings)
            if r is None:
                return None
            data = await r.get(key)
            if data is None:
                return None
            cached = orjson.loads(data)
            return LLMResponse(
                content=cached["content"],
                model=cached["model"],
                input_tokens=cached.get("input_tokens", 0),
                output_tokens=cached.get("output_tokens", 0),
                cost_usd=cached.get("cost_usd", 0.0),
                cost_breakdown=cached.get("cost_breakdown"),
                raw=None,  # raw 不缓存 (可能含不可序列化对象, 缓存命中无需 raw)
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM 响应缓存读取失败, 降级无缓存: %s", e)
            return None

    async def _set_llm_cache(self, key: str, response: LLMResponse) -> None:
        """写入 LLM 响应缓存 (P2-2).

        仅在 LLM 调用成功后由调用方触发 (用户硬约束: 出错了不要存缓存).
        缓存写入失败仅 warn, 不抛出 (不影响主流程).
        """
        if not self.settings.llm_response_cache_enabled:
            return
        try:
            from src.common.redis_client import get_redis_client

            r = await get_redis_client(self.settings)
            if r is None:
                return
            payload = orjson.dumps(
                {
                    "content": response.content,
                    "model": response.model,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "cost_usd": response.cost_usd,
                    "cost_breakdown": response.cost_breakdown,
                },
                default=str,
            )
            await r.setex(key, self.settings.llm_response_cache_ttl, payload)
            logger.debug(
                "LLM 响应缓存已写入: model=%s, ttl=%ds",
                response.model,
                self.settings.llm_response_cache_ttl,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM 响应缓存写入失败 (不阻断): %s", e)

    async def achat(
        self,
        messages: list[dict[str, str]],
        *,
        tier: LLMTier = LLMTier.SMART,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        span_name: str = "llm-chat",
        step: str = "unknown",
    ) -> LLMResponse:
        """异步 LLM 调用 (非流式), 含降级链 (strategic → smart → fast).

        AGENTS.md 第 10 章: 必须包裹在 trace_generation span 内.
        P1-Future-01: step 标识业务步骤, 计入 step_costs 分布.
        P1-Future-05: tier 调用失败时按 _FALLBACK_TIER 逐级降级, FAST 失败则抛出原异常.
        外层一个 trace span, 内部记录每次尝试的 tier 与最终结果.
        P2-2: temperature ≤ _CACHE_MAX_TEMPERATURE 时接入 Redis 响应缓存,
              命中直接返回 (跳过 LLM 调用);
              仅缓存成功响应, 异常/错误响应绝不缓存 (用户硬约束).
        P1-4: 放宽缓存条件 (0.0 → 0.3), 覆盖 planner/curator/context-summarize 等场景.
        """
        # span 用初始 tier 的 model/params (降级后实际 model 在 cost_details.model 记录)
        initial_model = self._get_model(tier)
        initial_token_limit = max_tokens or self._get_token_limit(tier)
        initial_temp = temperature if temperature is not None else self.settings.temperature
        model_params: dict[str, Any] = {
            "temperature": initial_temp,
            "max_tokens": initial_token_limit,
            "timeout": self.settings.llm_timeout,
        }

        # P1-4: LLM 响应缓存 — temperature ≤ _CACHE_MAX_TEMPERATURE 时缓存
        # (planner=0.2 / curator=0.2 / context-summarize=0.3 均可命中; >0.3 仍不缓存)
        # 缓存命中直接返回, 跳过 trace span (无 LLM 调用, 无需追踪 generation)
        cache_key: str | None = None
        if self.settings.llm_response_cache_enabled and initial_temp <= self._CACHE_MAX_TEMPERATURE:
            cache_key = self._llm_cache_key(
                messages, initial_model, initial_temp, initial_token_limit, stop
            )
            cached = await self._get_llm_cache(cache_key)
            if cached is not None:
                logger.debug(
                    "LLM 响应缓存命中: model=%s, step=%s (跳过 LLM 调用)",
                    initial_model,
                    step,
                )
                return cached

        attempted_tiers: list[str] = []
        async with trace_generation(
            name=span_name,
            input=messages,
            model=initial_model,
            model_parameters=model_params,
            user_id=user_id,
            session_id=session_id,
        ) as span:
            current_tier = tier
            last_exc: Exception | None = None
            while current_tier is not None:
                try:
                    response = await self._achat_with_tier(
                        messages,
                        current_tier,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        stop=stop,
                    )
                    # 成功: 更新 span (含最终 tier 与全部尝试记录)
                    # cost_breakdown 由 _achat_with_tier 保证非 None, 兜底满足 mypy --strict
                    breakdown = response.cost_breakdown or {
                        "input_cost": 0.0,
                        "output_cost": 0.0,
                        "total_cost": response.cost_usd,
                    }
                    span.update(
                        output=response.content[:2000],  # 截断避免 span 过大
                        usage_details={
                            "prompt_tokens": response.input_tokens,
                            "completion_tokens": response.output_tokens,
                        },
                        cost_details={
                            "input": breakdown["input_cost"],
                            "output": breakdown["output_cost"],
                            "total": response.cost_usd,
                        },
                        metadata={
                            "step": step,
                            "final_tier": current_tier.value,
                            "attempted_tiers": attempted_tiers + [current_tier.value],
                        },
                    )
                    # 累计会话级 + 分步成本 (成功后)
                    # P0-1: 成本按 session_id 隔离, 不再累积到全局单例
                    self._accumulate(
                        session_id or "",
                        step,
                        response.input_tokens,
                        response.output_tokens,
                        response.cost_usd,
                    )
                    # P1-04: 同步回写 TokenBudgetAllocator (统一两套成本系统)
                    # P0-2: allocator 按 session_id 隔离, 避免跨会话预算串扰
                    try:
                        from src.llm.token_budget import get_token_budget_allocator

                        allocator = await get_token_budget_allocator(session_id or "")
                        await allocator.add_cost(
                            step,
                            prompt_tokens=response.input_tokens,
                            completion_tokens=response.output_tokens,
                            model=response.model,
                            cost_usd=response.cost_usd,
                            check_budget=False,  # achat 内不抛超支异常, 仅记录
                        )
                    except Exception as budget_err:  # noqa: BLE001
                        logger.debug(
                            "TokenBudgetAllocator 回写失败 (非阻断): %s",
                            budget_err,
                        )
                    # P2-2: 写入 LLM 响应缓存 (仅成功响应)
                    # 用户硬约束: "出错了不要存缓存" — 此处仅在成功路径, 异常路径不会到达
                    if cache_key is not None:
                        await self._set_llm_cache(cache_key, response)
                    return response
                except Exception as e:  # noqa: BLE001
                    last_exc = e
                    attempted_tiers.append(current_tier.value)
                    fallback = self._FALLBACK_TIER.get(current_tier)
                    if fallback is None:
                        # 降级链耗尽: 记录错误并抛出
                        logger.error(
                            "LLM 调用失败且降级链耗尽 (tier=%s): %s",
                            current_tier.value,
                            e,
                        )
                        span.update(
                            metadata={
                                "step": step,
                                "error": str(e),
                                "attempted_tiers": attempted_tiers,
                            }
                        )
                        raise
                    logger.warning(
                        "LLM 调用 tier=%s 失败, 降级到 %s: %s",
                        current_tier.value,
                        fallback.value,
                        e,
                    )
                    current_tier = fallback
            # 理论上不会到达 (降级链耗尽会在循环内 raise)
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("LLM 降级链耗尽但无异常")

    async def achat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        tier: LLMTier = LLMTier.SMART,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        span_name: str = "llm-chat-stream",
        step: str = "unknown",
    ) -> AsyncIterator[str]:
        """异步流式 LLM 调用 (SSE 流式统一入口), 含降级链.

        AGENTS.md 第 9 章: 流式统一 achat_stream.
        yield 逐块文本 (delta content).
        P1-Future-01: step 标识业务步骤, 计入 step_costs 分布.
        P1-Future-05: 流式连接建立失败时按 _FALLBACK_TIER 逐级降级;
        一旦开始 yield 内容后不降级 (已输出内容无法回滚).
        """
        # span 用初始 tier 的 model/params
        initial_model = self._get_model(tier)
        initial_token_limit = max_tokens or self._get_token_limit(tier)
        initial_temp = temperature if temperature is not None else self.settings.temperature
        model_params: dict[str, Any] = {
            "temperature": initial_temp,
            "max_tokens": initial_token_limit,
            "stream": True,
        }

        # 流式 usage: litellm ≥1.6 在末块聚合时返回, 优先用真实 prompt_tokens/completion_tokens.
        # 退化路径: 字符数粗估 (//4 ≈ token 估算), 仅在 usage 缺失时使用.
        total_input_chars = sum(len(m.get("content", "")) for m in messages)
        total_output_chars = 0
        total_input_tokens = 0
        total_output_tokens = 0
        stream_usage: Any = None
        attempted_tiers: list[str] = []
        used_model = initial_model
        used_tier = tier

        async with trace_generation(
            name=span_name,
            input=messages,
            model=initial_model,
            model_parameters=model_params,
            user_id=user_id,
            session_id=session_id,
        ) as span:
            # Phase 1: 建立流式连接 (含降级链)
            stream: Any = None
            current_tier = tier
            last_exc: Exception | None = None
            while current_tier is not None:
                used_model = self._get_model(current_tier)
                used_tier = current_tier
                token_limit = max_tokens or self._get_token_limit(current_tier)
                temp = temperature if temperature is not None else self.settings.temperature
                api_key = resolve_api_key(used_model, self.settings)
                kwargs: dict[str, Any] = {
                    "model": used_model,
                    "messages": messages,
                    "temperature": temp,
                    "max_tokens": token_limit,
                    "timeout": self.settings.llm_timeout,
                    "num_retries": self.settings.llm_max_retries,
                    "stream": True,
                }
                if stop:
                    kwargs["stop"] = stop
                if api_key:
                    kwargs["api_key"] = api_key
                try:
                    # V2-P0: 智谱 AI 用 litellm 原生 zai/ 路由 (zhipuai/ → zai/)
                    # 任务6 治本: 保留原始 used_model 用于成本计算, adapted_model 仅用于 litellm 调用
                    original_used_model = used_model  # 保留原始 model 用于 _compute_cost
                    _, kwargs = self._adapt_zhipu(used_model, kwargs)
                    stream = await litellm.acompletion(**kwargs)
                    break  # 流式连接建立成功
                except Exception as e:  # noqa: BLE001
                    last_exc = e
                    attempted_tiers.append(current_tier.value)
                    fallback = self._FALLBACK_TIER.get(current_tier)
                    if fallback is None:
                        logger.error(
                            "LLM 流式连接失败且降级链耗尽 (tier=%s): %s",
                            current_tier.value,
                            e,
                        )
                        span.update(
                            metadata={
                                "step": step,
                                "error": str(e),
                                "attempted_tiers": attempted_tiers,
                            }
                        )
                        raise
                    logger.warning(
                        "LLM 流式 tier=%s 失败, 降级到 %s: %s",
                        current_tier.value,
                        fallback.value,
                        e,
                    )
                    current_tier = fallback

            if stream is None:
                # 理论上不会到达 (降级链耗尽会在循环内 raise)
                if last_exc is not None:
                    raise last_exc
                raise RuntimeError("LLM 流式降级链耗尽但无异常")

            # Phase 2: 消费流 (mid-stream 失败不降级, 已 yield 部分内容无法回滚)
            try:
                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        delta = chunk.choices[0].delta.content
                        total_output_chars += len(delta)
                        yield delta
                    # 部分模型在末块返回 usage (litellm 已聚合)
                    chunk_usage = getattr(chunk, "usage", None)
                    if chunk_usage is not None:
                        stream_usage = chunk_usage

                # 真实 usage 优先 (litellm 流式末块聚合), 否则字符数粗估 (//4)
                if stream_usage is not None:
                    total_input_tokens = int(getattr(stream_usage, "prompt_tokens", 0) or 0)
                    total_output_tokens = int(getattr(stream_usage, "completion_tokens", 0) or 0)
                else:
                    total_input_tokens = total_input_chars // 4
                    total_output_tokens = total_output_chars // 4

                breakdown = self._compute_cost(
                    original_used_model, total_input_tokens, total_output_tokens
                )
                cost_usd = breakdown["total_cost"]

                span.update(
                    output=f"[streamed {total_output_chars} chars]",
                    usage_details={
                        "prompt_tokens": total_input_tokens,
                        "completion_tokens": total_output_tokens,
                    },
                    cost_details={
                        "input": breakdown["input_cost"],
                        "output": breakdown["output_cost"],
                        "total": cost_usd,
                    },
                    metadata={
                        "step": step,
                        "final_tier": used_tier.value,
                        "attempted_tiers": attempted_tiers + [used_tier.value],
                    },
                )

                # 累计会话级 + 分步成本 (成功后)
                # P0-1: 成本按 session_id 隔离, 不再累积到全局单例
                self._accumulate(
                    session_id or "",
                    step,
                    total_input_tokens,
                    total_output_tokens,
                    cost_usd,
                )
                # P1-04: 同步回写 TokenBudgetAllocator (流式分支)
                # P0-2: allocator 按 session_id 隔离
                try:
                    from src.llm.token_budget import get_token_budget_allocator

                    allocator = await get_token_budget_allocator(session_id or "")
                    await allocator.add_cost(
                        step,
                        prompt_tokens=total_input_tokens,
                        completion_tokens=total_output_tokens,
                        model=used_model,
                        cost_usd=cost_usd,
                        check_budget=False,
                    )
                except Exception as budget_err:  # noqa: BLE001
                    logger.debug(
                        "TokenBudgetAllocator 流式回写失败 (非阻断): %s",
                        budget_err,
                    )
            except Exception as e:  # noqa: BLE001
                logger.error("LLM 流式调用失败 (model=%s): %s", used_model, e)
                span.update(
                    metadata={
                        "step": step,
                        "error": str(e),
                        "attempted_tiers": attempted_tiers + [used_tier.value],
                    }
                )
                raise


# ========== 全局单例 ==========
_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """获取全局 LLMClient 单例."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
