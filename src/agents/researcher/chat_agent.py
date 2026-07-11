"""ChatAgent 对话式追问 Agent (P2-Future-03).

AGENTS.md 第 5 章: LangGraph StateGraph 唯一编排, 节点纯函数.
设计参考: chat_with_report / 对话式追问模式.

ChatAgent 职责:
- 基于历史消息 + 已有报告上下文回答用户追问
- 系统提示含 report_md (截断 chat_report_truncate_chars 字符, P2 配置化)
- 历史 messages 取最近 chat_history_limit 条 (P2 配置化)
- CHITCHAT_FAST_LLM_OPTIMIZATION_PLAN.md P0: cascade 路由 (简单追问 FAST / 复杂追问 SMART)
- 用 trace_chain 包裹 (AGENTS.md 第 10 章, 禁 agentinsight.observe 装饰器)
- 用 PromptFamily.chat_prompt 注入 prompt (P1-Future-04 策略模式)

集成 (chat_builder.py):
    单节点 chat 图, 复用同一 PostgresSaver (同 thread_id 隔离).
    routes.py 检测追问 vs 新研究: has_report 且无 report_type → 走 chat graph.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from src.config.settings import Settings, get_settings
from src.graph.state import ResearcherState
from src.llm.client import LLMClient, LLMTier, get_llm_client
from src.observability.tracing import trace_chain
from src.skills.researcher.prompts import PromptFamily, get_prompt_family

logger = logging.getLogger(__name__)


class ChatAgent:
    """对话式追问 Agent (P2-Future-03).

    基于历史消息 + 已有报告上下文回答用户追问.
    设计参考: chat_with_report 对话模式.

    CHITCHAT_FAST_LLM_OPTIMIZATION_PLAN.md P0: cascade 路由
    - 简单追问 (短查询/总结/复述) → FAST_LLM (glm-4-flash, 免费)
    - 复杂追问 (跨章节分析/对比/推理) → SMART_LLM (deepseek-v4-flash)
    - SMART 失败 → 降级 FAST (FrugalGPT cascade)
    """

    settings: Settings
    _llm: LLMClient
    _prompt_family: PromptFamily

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        prompt_family: PromptFamily | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or get_llm_client()
        self._prompt_family = prompt_family or get_prompt_family(self.settings.prompt_family)

    def _assess_chat_complexity(self, query: str) -> LLMTier:
        """评估追问复杂度, 决定 LLM tier (cascade 路由).

        CHITCHAT_FAST_LLM_OPTIMIZATION_PLAN.md §5.3.1:
        - 简单追问 (tier=FAST): 总结/复述/单点确认/短问题
        - 复杂追问 (tier=SMART): 跨章节分析/对比/推理/需引用报告细节

        Args:
            query: 用户追问

        Returns:
            LLMTier.FAST 或 LLMTier.SMART
        """
        # 短查询走 FAST
        if len(query) < self.settings.chat_simple_query_threshold_chars:
            return LLMTier.FAST
        # 命中复杂关键词走 SMART
        if any(kw in query for kw in self.settings.chat_complex_keywords):
            return LLMTier.SMART
        # 默认走 FAST (闲聊/简单追问)
        return LLMTier.FAST

    async def chat(
        self,
        state: ResearcherState,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """基于历史消息 + 报告上下文回答用户追问.

        Args:
            state: 研究状态, 含 query (追问) / report_md (已有报告) / messages (历史)
            user_id: 用户 ID (隔离键, AGENTS.md 第 8 章)
            session_id: 会话 ID (隔离键, AGENTS.md 第 6 章)

        Returns:
            {"messages": [HumanMessage, AIMessage]} 追问 + 回答追加到消息流
            (add_messages reducer 自动合并到历史)
        """
        async with trace_chain(
            name="chat-agent",
            input={
                "query": state.get("query", "")[:100],
                "report_len": len(state.get("report_md", "")),
                "history_count": len(state.get("messages", [])),
            },
            user_id=user_id,
            session_id=session_id,
        ) as span:
            query = state.get("query", "")
            report_md = state.get("report_md", "")
            # 截断 report_md 避免 token 过大 (P2: 配置化, 替换 _REPORT_TRUNCATE_CHARS)
            report_md_truncated = report_md[: self.settings.chat_report_truncate_chars]

            # P1-Future-06: 首轮 chat (report_md 为空) 使用通用系统提示, 不依赖报告上下文
            if not report_md_truncated:
                system_prompt = (
                    "你是一个智能研究助手。用户可能想进行简短对话或询问简单问题。"
                    "请友好地回答，并在适当时引导用户提供研究主题。"
                )
            else:
                role_persona = (
                    state.get("agent_role") or "你是一位资深研究分析专家, 擅长多领域综合研究."
                )
                # P1-Future-04: 系统提示经 PromptFamily 策略注入 (含 report_md)
                system_prompt = self._prompt_family.chat_prompt(
                    query=query,
                    report_md=report_md_truncated,
                    agent_role=role_persona,
                )

            # 历史 messages 取最近 chat_history_limit 条 (P2: 配置化, 替换 _HISTORY_LIMIT)
            history: list[BaseMessage] = state.get("messages", []) or []
            recent_history = history[-self.settings.chat_history_limit :]
            history_dicts = self._convert_messages(recent_history)

            # 构建完整消息列表: system + history + current query
            messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
            messages.extend(history_dicts)
            # 当前追问作为最新 user 消息
            messages.append({"role": "user", "content": query})

            # CHITCHAT_FAST_LLM_OPTIMIZATION_PLAN.md P0: cascade 路由
            # 简单追问走 FAST (glm-4-flash, 免费), 复杂追问走 SMART (deepseek-v4-flash)
            tier = self._assess_chat_complexity(query)
            span_tier = tier.value
            logger.debug("ChatAgent cascade 路由: query=%s, tier=%s", query[:50], span_tier)

            try:
                response = await self._llm.achat(
                    messages,
                    tier=tier,
                    temperature=self.settings.chat_temperature,
                    max_tokens=self.settings.chat_max_tokens,
                    user_id=user_id,
                    session_id=session_id,
                    span_name="chat-agent-llm",
                    step="chat",
                )
            except Exception as e:  # noqa: BLE001
                # CHITCHAT_FAST_LLM_OPTIMIZATION_PLAN.md §5.5.1: SMART 失败降级 FAST
                if tier == LLMTier.SMART:
                    logger.warning("SMART 闲聊失败, 降级 FAST: %s", e)
                    span.update(
                        output={"error": "smart_failed_fallback_fast"},
                        metadata={"error": str(e)[:200], "original_tier": span_tier},
                    )
                    try:
                        response = await self._llm.achat(
                            messages,
                            tier=LLMTier.FAST,
                            temperature=self.settings.chat_temperature,
                            max_tokens=self.settings.chat_max_tokens,
                            user_id=user_id,
                            session_id=session_id,
                            span_name="chat-agent-llm-fallback",
                            step="chat",
                        )
                    except Exception as e2:  # noqa: BLE001
                        logger.warning("ChatAgent FAST 降级也失败: %s", e2)
                        span.update(
                            output={"error": "llm_failed"},
                            metadata={"error": str(e2)[:200]},
                        )
                        return {
                            "messages": [
                                HumanMessage(content=query),
                                AIMessage(content=f"抱歉, 对话服务暂时不可用: {str(e2)[:200]}"),
                            ]
                        }
                else:
                    logger.warning("ChatAgent LLM 调用失败: %s", e)
                    span.update(
                        output={"error": "llm_failed"},
                        metadata={"error": str(e)[:200]},
                    )
                    # 返回错误信息作为 AI 消息 (保持消息流连贯)
                    return {
                        "messages": [
                            HumanMessage(content=query),
                            AIMessage(content=f"抱歉, 对话服务暂时不可用: {str(e)[:200]}"),
                        ]
                    }

            ai_response = response.content.strip() or "(无响应)"

            span.update(
                output={
                    "response_len": len(ai_response),
                    "history_used": len(history_dicts),
                    "tier": response.model,
                },
                metadata={
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "cost_usd": response.cost_usd,
                    "tier_used": span_tier,
                },
            )

            # 追加 user 追问 + AI 回答到消息流 (add_messages reducer 自动合并)
            return {
                "messages": [
                    HumanMessage(content=query),
                    AIMessage(content=ai_response),
                ]
            }

    @staticmethod
    def _convert_messages(messages: list[BaseMessage]) -> list[dict[str, str]]:
        """将 BaseMessage 列表转换为 LLMClient 所需的 dict 格式.

        映射 (langchain_core -> OpenAI 兼容):
        - HumanMessage -> {"role": "user", ...}
        - AIMessage -> {"role": "assistant", ...}
        - SystemMessage -> {"role": "system", ...}
        - ToolMessage -> {"role": "tool", ...}
        - 其他 -> {"role": "user", ...} (兜底)
        """
        result: list[dict[str, str]] = []
        for msg in messages:
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            msg_type = msg.type
            if msg_type == "human":
                role = "user"
            elif msg_type == "ai":
                role = "assistant"
            elif msg_type == "system":
                role = "system"
            elif msg_type == "tool":
                role = "tool"
            else:
                role = "user"
            result.append({"role": role, "content": content})
        return result


async def chat_node(
    state: ResearcherState,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """ChatAgent 对话节点 (P2-Future-03).

    调用 ChatAgent.chat(), 返回 delta {"messages": [...]}.
    AGENTS.md 第 5 章: 节点为纯函数, 单一职责无副作用.
    AGENTS.md 第 10 章: 节点包裹在 trace span 内 (ChatAgent.chat 内部已包裹 trace_chain).
    """
    agent = ChatAgent(settings)
    return await agent.chat(
        state,
        user_id=state.get("user_id"),
        session_id=state.get("session_id"),
    )
