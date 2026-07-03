"""LiteLLM 网关封装.

AGENTS.md 第 9 章硬约束:
- 全部 LLM 调用经 llm/ 的 LLMClient (底层 LiteLLM ≥1.6)
- 禁止直接 openai/anthropic 等 SDK
- 模型名以 LiteLLM 路由前缀声明 (如 deepseek/deepseek-chat), 由配置注入, 禁止硬编码
- 流式统一 achat_stream; 同步 chat 仅用于非交互式批处理

GPT Researcher 三级 LLM 模式 (用户需求 10 Token 优化):
- FAST_LLM: 快速任务 (摘要)
- SMART_LLM: 复杂推理 (报告写作, 支持 2k+ 字长响应)
- STRATEGIC_LLM: 规划 (agent 选择, 慢但精)
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from src.config.settings import Settings, get_settings
from src.observability.tracing import trace_generation

logger = logging.getLogger(__name__)


class LLMTier(StrEnum):
    """LLM 三级分层 (GPT Researcher 模式)."""

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
    raw: Any = None


@dataclass
class LLMClient:
    """LiteLLM 网关客户端.

    AGENTS.md 第 9 章: 全部 LLM 调用经此客户端, 禁厂商 SDK 直连.
    所有调用必须包裹在 trace_generation span 内 (AGENTS.md 第 10 章).
    """

    settings: Settings = field(default_factory=get_settings)

    def _get_model(self, tier: LLMTier) -> str:
        """按层级获取模型名."""
        if tier == LLMTier.FAST:
            return self.settings.fast_llm
        if tier == LLMTier.SMART:
            return self.settings.smart_llm
        if tier == LLMTier.STRATEGIC:
            return self.settings.strategic_llm
        raise ValueError(f"未知 LLM 层级: {tier}")

    def _get_token_limit(self, tier: LLMTier) -> int:
        """按层级获取 token 上限."""
        if tier == LLMTier.FAST:
            return self.settings.fast_token_limit
        if tier == LLMTier.SMART:
            return self.settings.smart_token_limit
        if tier == LLMTier.STRATEGIC:
            return self.settings.strategic_token_limit
        return 4000

    def _get_api_key(self, model: str) -> str | None:
        """按 LiteLLM 路由前缀获取对应 API Key."""
        if model.startswith("deepseek/"):
            return self.settings.deepseek_api_key
        if model.startswith("openai/"):
            return self.settings.openai_api_key
        if model.startswith("anthropic/"):
            return self.settings.anthropic_api_key
        if model.startswith("zhipu/"):
            return self.settings.zhipu_api_key
        return None

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
    ) -> LLMResponse:
        """异步 LLM 调用 (非流式).

        AGENTS.md 第 10 章: 必须包裹在 trace_generation span 内.
        """
        model = self._get_model(tier)
        token_limit = max_tokens or self._get_token_limit(tier)
        temp = temperature if temperature is not None else self.settings.temperature
        api_key = self._get_api_key(model)

        # 构建调用参数
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

        model_params = {
            "temperature": temp,
            "max_tokens": token_limit,
            "timeout": self.settings.llm_timeout,
        }

        async with trace_generation(
            name=span_name,
            input=messages,
            model=model,
            model_parameters=model_params,
            user_id=user_id,
            session_id=session_id,
        ) as span:
            try:
                # 延迟导入 litellm, 避免模块加载时强依赖
                import litellm

                response = await litellm.acompletion(**kwargs)

                # 提取 usage 信息
                usage = getattr(response, "usage", None)
                input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
                output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
                cost_usd = self._compute_cost(model, input_tokens, output_tokens)

                content = response.choices[0].message.content or ""

                span.update(
                    output=content[:2000],  # 截断避免 span 过大
                    usage_details={
                        "prompt_tokens": input_tokens,
                        "completion_tokens": output_tokens,
                    },
                    cost_details={"cost_usd": cost_usd},
                )

                return LLMResponse(
                    content=content,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost_usd,
                    raw=response,
                )
            except Exception as e:  # noqa: BLE001
                logger.error("LLM 调用失败 (model=%s): %s", model, e)
                span.update(metadata={"error": str(e)})
                raise

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
    ) -> AsyncIterator[str]:
        """异步流式 LLM 调用 (SSE 流式统一入口).

        AGENTS.md 第 9 章: 流式统一 achat_stream.
        yield 逐块文本 (delta content).
        """
        model = self._get_model(tier)
        token_limit = max_tokens or self._get_token_limit(tier)
        temp = temperature if temperature is not None else self.settings.temperature
        api_key = self._get_api_key(model)

        kwargs: dict[str, Any] = {
            "model": model,
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

        model_params = {
            "temperature": temp,
            "max_tokens": token_limit,
            "stream": True,
        }

        total_input = 0
        total_output = 0

        async with trace_generation(
            name=span_name,
            input=messages,
            model=model,
            model_parameters=model_params,
            user_id=user_id,
            session_id=session_id,
        ) as span:
            try:
                import litellm

                stream = await litellm.acompletion(**kwargs)
                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        delta = chunk.choices[0].delta.content
                        total_output += len(delta)
                        yield delta

                # 流式调用 usage 可能不完整, 用字符数粗估
                total_input = sum(len(m.get("content", "")) for m in messages) // 4
                cost_usd = self._compute_cost(model, total_input, total_output)

                span.update(
                    output=f"[streamed {total_output} chars]",
                    usage_details={
                        "prompt_tokens": total_input,
                        "completion_tokens": total_output,
                    },
                    cost_details={"cost_usd": cost_usd},
                )
            except Exception as e:  # noqa: BLE001
                logger.error("LLM 流式调用失败 (model=%s): %s", model, e)
                span.update(metadata={"error": str(e)})
                raise

    def _compute_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """粗略计算 LLM 调用成本 (USD).

        简化模型, 精确成本由 LiteLLM 内置 cost_calculator 处理.
        """
        # 简化定价表 (每 1K token, USD), 实际由 litellm.completion_cost 精确计算
        pricing = {
            "deepseek/deepseek-chat": {"input": 0.0014, "output": 0.0028},
            "deepseek/deepseek-reasoner": {"input": 0.0055, "output": 0.022},
            "openai/gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
            "openai/gpt-4.1": {"input": 0.002, "output": 0.008},
        }
        rate = pricing.get(model, {"input": 0.001, "output": 0.002})
        return (input_tokens / 1000) * rate["input"] + (output_tokens / 1000) * rate["output"]


# ========== 全局单例 ==========
_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """获取全局 LLMClient 单例."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
