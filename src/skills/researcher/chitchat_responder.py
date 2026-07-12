"""闲聊响应器 (FAST_LLM 实时生成, 替代固定话术).

CHITCHAT_FAST_LLM_OPTIMIZATION_PLAN.md P0 核心组件:
- SHORT_QUERY/OFF_TOPIC 响应从"固定话术"升级为"FAST_LLM + Persona + 三段式"
- 保留 multi-template 作为兜底 (FAST 失败时降级, 不阻断主流程)

架构合规:
- 节点为纯函数, 单一职责无副作用
- LLM 调用经 llm/ 网关 (LiteLLM), 禁厂商 SDK 直连
- trace_chain span 包裹 (禁 agentinsight.observe 装饰器)
- 不依赖 Qdrant/Embeddings (仅 LLM + 配置)

依赖方向: skills/ → config/ + llm/ + observability/ (单向向内)
不依赖: agents/ / graph/ (保持架构边界)
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Coroutine
from typing import TYPE_CHECKING, Any

from src.config.researcher import (
    ChitchatConfigBundle,
    PersonaConfig,
    get_chitchat_config,
)
from src.llm.client import LLMTier, get_llm_client
from src.observability.tracing import trace_chain

if TYPE_CHECKING:
    from src.config.settings import Settings
    from src.llm.client import LLMClient

logger = logging.getLogger(__name__)


class ChitchatResponder:
    """闲聊响应器.

    职责:
    1. SHORT_QUERY 响应: FAST_LLM 生成简短引导 + 三段式
    2. OFF_TOPIC 响应: 按子类 (greeting/identity/emotion/...) 路由 prompt
    3. 兜底: multi-template 随机返回固定话术 (FAST 失败时降级)

    设计原则 (级联降级策略):
    - FAST_LLM 优先 (glm-4-flash, 免费层)
    - 失败降级到 multi-template (零成本, 不阻断)
    - 不升级到 SMART (闲聊不值得用 SMART 成本)

    符合架构约定:
    - 节点为纯函数, 单一职责
    - LLM 调用经 llm/ 网关 (LiteLLM)
    - trace_chain span 包裹
    """

    def __init__(
        self,
        settings: Settings,
        persona: PersonaConfig | None = None,
        config_bundle: ChitchatConfigBundle | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        """初始化闲聊响应器.

        Args:
            settings: 全局配置 (SSOT)
            persona: Persona 配置, 留空则从 config_bundle 取默认
            config_bundle: 闲聊配置包, 留空则用全局单例
            llm: LLM 客户端, 留空则用全局单例
        """
        self._settings = settings
        self._config = config_bundle or get_chitchat_config()
        self._persona = persona or self._config.persona
        self._llm = llm or get_llm_client()

    # ========== SHORT_QUERY 响应 ==========

    def respond_short_query(
        self,
        query: str,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        stream: bool = False,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str] | Coroutine[Any, Any, str]:
        """短查询响应.

        策略:
        1. FAST_LLM 生成三段式回复
        2. FAST 失败 → multi-template 随机返回固定话术

        Args:
            query: 用户输入
            session_id: 会话 ID (隔离键)
            user_id: 用户 ID (隔离键)
            history: 对话历史 (来自 checkpointer, [{"role":"user","content":...},{"role":"assistant","content":...}])
            stream: 是否流式响应

        Returns:
            stream=False → 返回 Coroutine (await 后得到完整字符串)
            stream=True → 返回 AsyncIterator[str] (逐块 yield)

        Note:
            非异步方法 (设计选择, 方案 B):
            - stream=True 时直接返回 _stream_short_query 的 AsyncGenerator
              (async generator function 调用即返回, 无需 await)
            - stream=False 时返回 _run_short_query 的 coroutine
              (由调用方 _run_chitchat await)
            - 避免 async def 包装导致 stream=True 路径返回 coroutine
              而非 AsyncIterator 的陷阱 (闲聊流式响应 '*未收到内容*')
        """
        if stream:
            return self._stream_short_query(
                query, session_id=session_id, user_id=user_id, history=history
            )
        return self._run_short_query(query, session_id=session_id, user_id=user_id, history=history)

    async def _run_short_query(
        self,
        query: str,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """非流式短查询响应."""
        async with trace_chain(
            name="chitchat-short-query",
            input={"query": query[:100], "intent": "short_query"},
            user_id=user_id,
            session_id=session_id,
        ) as span:
            # FAST_LLM 路径
            try:
                template = self._config.render_prompt(
                    "chitchat/short_query.j2",
                    persona=self._persona,
                    query=query,
                )
                messages: list[dict[str, str]] = [
                    {"role": "system", "content": template},
                ]
                # 注入对话历史 (来自 checkpointer, 支持会话持久化)
                if history:
                    messages.extend(history[-10:])  # 最近 10 条, 避免 token 过大
                messages.append({"role": "user", "content": query})
                response = await self._llm.achat(
                    messages,
                    tier=LLMTier.FAST,
                    temperature=self._settings.chitchat_temperature,
                    max_tokens=self._settings.chitchat_max_tokens,
                    user_id=user_id,
                    session_id=session_id,
                    span_name="chitchat-short-query-llm",
                    step="chitchat",
                )
                reply = response.content.strip()
                if not reply:
                    raise ValueError("FAST_LLM 返回空内容")
                span.update(
                    output={"reply_len": len(reply), "mode": "fast_llm"},
                    metadata={
                        "model": response.model,
                        "input_tokens": response.input_tokens,
                        "output_tokens": response.output_tokens,
                        "cost_usd": response.cost_usd,
                    },
                )
                return reply
            except Exception as e:  # noqa: BLE001
                logger.warning("FAST_LLM 短查询响应失败, 降级 multi-template: %s", e)
                span.update(
                    output={"mode": "template_fallback"},
                    metadata={"error": str(e)[:200]},
                )
                return self._fallback_reply("short_query")

    async def _stream_short_query(
        self,
        query: str,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        """流式短查询响应.

        FAST_LLM 失败时降级为一次性 yield 完整兜底话术.
        """
        async with trace_chain(
            name="chitchat-short-query-stream",
            input={"query": query[:100], "intent": "short_query"},
            user_id=user_id,
            session_id=session_id,
        ) as span:
            # FAST_LLM 流式路径
            try:
                template = self._config.render_prompt(
                    "chitchat/short_query.j2",
                    persona=self._persona,
                    query=query,
                )
                messages: list[dict[str, str]] = [
                    {"role": "system", "content": template},
                ]
                # 注入对话历史 (来自 checkpointer, 支持会话持久化)
                if history:
                    messages.extend(history[-10:])  # 最近 10 条, 避免 token 过大
                messages.append({"role": "user", "content": query})
                total_chars = 0
                async for chunk in self._llm.achat_stream(
                    messages,
                    tier=LLMTier.FAST,
                    temperature=self._settings.chitchat_temperature,
                    max_tokens=self._settings.chitchat_max_tokens,
                    user_id=user_id,
                    session_id=session_id,
                    span_name="chitchat-short-query-stream",
                    step="chitchat",
                ):
                    if chunk:
                        total_chars += len(chunk)
                        yield chunk
                span.update(output={"reply_len": total_chars, "mode": "fast_llm"})
            except Exception as e:  # noqa: BLE001
                logger.warning("FAST_LLM 流式短查询失败, 降级 multi-template: %s", e)
                span.update(
                    output={"mode": "template_fallback"},
                    metadata={"error": str(e)[:200]},
                )
                yield self._fallback_reply("short_query")

    # ========== OFF_TOPIC 响应 ==========

    def respond_off_topic(
        self,
        query: str,
        *,
        category: str = "greeting",
        session_id: str | None = None,
        user_id: str | None = None,
        stream: bool = False,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str] | Coroutine[Any, Any, str]:
        """离题闲聊响应.

        按 off_topic 子类路由 prompt 模板:
        - greeting: 问候
        - identity: 身份询问
        - emotion: 情绪表达
        - entertainment: 娱乐请求
        - common_sense: 常识问题
        - capability_check: 能力询问
        - topic_switch: 话题转移
        - evaluation: 评价

        Args:
            query: 用户输入
            category: 闲聊子类 (决定 prompt 模板)
            session_id: 会话 ID
            user_id: 用户 ID
            history: 对话历史 (来自 checkpointer, [{"role":"user","content":...},{"role":"assistant","content":...}])
            stream: 是否流式响应

        Returns:
            stream=False → 返回 Coroutine (await 后得到完整字符串)
            stream=True → 返回 AsyncIterator[str]

        Note:
            非异步方法 (设计选择, 方案 B), 同 respond_short_query.
        """
        if stream:
            return self._stream_off_topic(
                query,
                category=category,
                session_id=session_id,
                user_id=user_id,
                history=history,
            )
        return self._run_off_topic(
            query,
            category=category,
            session_id=session_id,
            user_id=user_id,
            history=history,
        )

    async def _run_off_topic(
        self,
        query: str,
        *,
        category: str = "greeting",
        session_id: str | None = None,
        user_id: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """非流式离题响应."""
        async with trace_chain(
            name="chitchat-off-topic",
            input={"query": query[:100], "intent": "off_topic", "category": category},
            user_id=user_id,
            session_id=session_id,
        ) as span:
            # FAST_LLM 路径
            try:
                template_name = self._off_topic_template_name(category)
                template = self._config.render_prompt(
                    template_name,
                    persona=self._persona,
                    query=query,
                )
                messages: list[dict[str, str]] = [
                    {"role": "system", "content": template},
                ]
                # 注入对话历史 (来自 checkpointer, 支持会话持久化)
                if history:
                    messages.extend(history[-10:])  # 最近 10 条, 避免 token 过大
                messages.append({"role": "user", "content": query})
                response = await self._llm.achat(
                    messages,
                    tier=LLMTier.FAST,
                    temperature=self._settings.chitchat_temperature,
                    max_tokens=self._settings.chitchat_max_tokens,
                    user_id=user_id,
                    session_id=session_id,
                    span_name="chitchat-off-topic-llm",
                    step="chitchat",
                )
                reply = response.content.strip()
                if not reply:
                    raise ValueError("FAST_LLM 返回空内容")
                span.update(
                    output={"reply_len": len(reply), "mode": "fast_llm", "category": category},
                    metadata={
                        "model": response.model,
                        "input_tokens": response.input_tokens,
                        "output_tokens": response.output_tokens,
                        "cost_usd": response.cost_usd,
                    },
                )
                return reply
            except Exception as e:  # noqa: BLE001
                logger.warning("FAST_LLM 离题响应失败, 降级 multi-template: %s", e)
                span.update(
                    output={"mode": "template_fallback", "category": category},
                    metadata={"error": str(e)[:200]},
                )
                return self._fallback_reply("off_topic", category)

    async def _stream_off_topic(
        self,
        query: str,
        *,
        category: str = "greeting",
        session_id: str | None = None,
        user_id: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        """流式离题响应."""
        async with trace_chain(
            name="chitchat-off-topic-stream",
            input={"query": query[:100], "intent": "off_topic", "category": category},
            user_id=user_id,
            session_id=session_id,
        ) as span:
            # FAST_LLM 流式路径
            try:
                template_name = self._off_topic_template_name(category)
                template = self._config.render_prompt(
                    template_name,
                    persona=self._persona,
                    query=query,
                )
                messages: list[dict[str, str]] = [
                    {"role": "system", "content": template},
                ]
                # 注入对话历史 (来自 checkpointer, 支持会话持久化)
                if history:
                    messages.extend(history[-10:])  # 最近 10 条, 避免 token 过大
                messages.append({"role": "user", "content": query})
                total_chars = 0
                async for chunk in self._llm.achat_stream(
                    messages,
                    tier=LLMTier.FAST,
                    temperature=self._settings.chitchat_temperature,
                    max_tokens=self._settings.chitchat_max_tokens,
                    user_id=user_id,
                    session_id=session_id,
                    span_name="chitchat-off-topic-stream",
                    step="chitchat",
                ):
                    if chunk:
                        total_chars += len(chunk)
                        yield chunk
                span.update(
                    output={"reply_len": total_chars, "mode": "fast_llm", "category": category}
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("FAST_LLM 流式离题失败, 降级 multi-template: %s", e)
                span.update(
                    output={"mode": "template_fallback", "category": category},
                    metadata={"error": str(e)[:200]},
                )
                yield self._fallback_reply("off_topic", category)

    # ========== 辅助方法 ==========

    @staticmethod
    def _off_topic_template_name(category: str) -> str:
        """根据 category 返回 Jinja2 模板路径.

        未知 category 兜底为 greeting 模板.
        """
        # category → 模板文件名映射
        # capability_check → off_topic_capability (capability_check 文件名简化为 capability)
        category_to_file = {
            "greeting": "greeting",
            "identity": "identity",
            "emotion": "emotion",
            "entertainment": "entertainment",
            "common_sense": "common_sense",
            "capability_check": "capability",
            "topic_switch": "topic_switch",
            "evaluation": "greeting",  # evaluation 兜底用 greeting
        }
        file_name = category_to_file.get(category, "greeting")
        return f"chitchat/off_topic_{file_name}.j2"

    def _fallback_reply(
        self,
        category: str,
        subcategory: str | None = None,
    ) -> str:
        """获取兜底话术 (multi-template 随机).

        chitchat_fallback_to_template=False 时返回 settings 里的固定话术.
        """
        if not self._settings.chitchat_fallback_to_template:
            # 返回 settings 固定话术
            if category == "short_query":
                return self._settings.short_query_reply
            return self._settings.off_topic_reply

        try:
            return self._config.random_reply(category, subcategory)
        except KeyError:
            # YAML 兜底话术缺失, 再降级到 settings 固定话术
            logger.warning(
                "YAML 兜底话术缺失 (category=%s, sub=%s), 降级 settings 固定话术",
                category,
                subcategory,
            )
            if category == "short_query":
                return self._settings.short_query_reply
            return self._settings.off_topic_reply


# ========== 全局单例 ==========
_responder: ChitchatResponder | None = None


def get_chitchat_responder() -> ChitchatResponder:
    """获取全局 ChitchatResponder 单例.

    首次调用时构造 (加载 LLMClient + ChitchatConfigBundle),
    后续调用返回同一实例.
    """
    global _responder
    if _responder is None:
        from src.config.settings import get_settings

        _responder = ChitchatResponder(get_settings())
    return _responder
