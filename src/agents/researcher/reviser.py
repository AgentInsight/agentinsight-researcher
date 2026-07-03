"""Reviser 报告修订 Agent (P0-Future-01).

AGENTS.md 第 5 章: LangGraph StateGraph 唯一编排, 节点纯函数.
对标 GPT Researcher reviser 角色 + 章节级修订循环.

Reviser 职责:
- 根据 Reviewer 反馈修订报告, 返回新的 report_md
- 用 LLMClient tier=SMART 调用 (适合长文本写作)
- 用 trace_chain 包裹 (AGENTS.md 第 10 章, 禁 agentinsight.observe 装饰器)

行业适配采用 GPTR 风格 4 层机制, agent_role 注入角色 persona.
修订循环上限由 settings.max_revisions 控制 (默认 3, 见 multi_agent_builder 守卫).
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.settings import Settings, get_settings
from src.graph.state import ResearcherState
from src.llm.client import LLMClient, LLMTier
from src.observability.tracing import trace_chain

logger = logging.getLogger(__name__)


class Reviser:
    """报告修订 Agent (P0-Future-01).

    根据 Reviewer 反馈修订报告, 返回新的 report_md.
    对标 GPT Researcher reviser 角色 + 章节级修订循环.
    """

    settings: Settings
    _llm: LLMClient

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or LLMClient(self.settings)

    async def revise(
        self,
        state: ResearcherState,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """根据 Reviewer 反馈修订报告.

        Args:
            state: 研究状态, 含 report_md / review_feedback / contexts / query
            user_id: 用户 ID (隔离键, AGENTS.md 第 8 章)
            session_id: 会话 ID (隔离键, AGENTS.md 第 6 章)

        Returns:
            {"report_md": str} 修订后的报告
        """
        async with trace_chain(
            name="reviser",
            input={
                "query": state.get("query", "")[:100],
                "report_len": len(state.get("report_md", "")),
                "feedback_len": len(state.get("review_feedback", "")),
            },
            user_id=user_id,
            session_id=session_id,
        ) as span:
            report_md = state.get("report_md", "")
            feedback = state.get("review_feedback", "")

            if not report_md:
                span.update(
                    output={"error": "empty_report"},
                    metadata={"error": "report_md 为空"},
                )
                return {"report_md": ""}

            if not feedback:
                span.update(
                    output={"revised": False, "reason": "no_feedback"},
                    metadata={"revised": False},
                )
                return {"report_md": report_md}  # 无反馈, 不修订

            role_persona = (
                state.get("agent_role") or "你是一位资深研究分析专家, 擅长多领域综合研究."
            )
            contexts = state.get("contexts", [])
            contexts_text = self._format_contexts(contexts)

            prompt = f"""{role_persona}

你的任务是: 根据评审反馈, 修订以下研究报告.

修订要求:
1. 逐条针对评审反馈中的问题进行修订
2. 保持报告整体结构与原文风格
3. 确保修订后的内容有上下文支持, 不得编造
4. 保留原文中正确的部分, 仅修改有问题的章节/段落
5. 输出完整的修订后报告 (Markdown 格式)

研究问题: {state.get("query", "")}

可用上下文 (节选):
{contexts_text}

评审反馈:
{feedback}

原报告:
{report_md[:8000]}

请输出修订后的完整报告 (仅 Markdown 内容, 不要其他说明):"""

            messages = [{"role": "user", "content": prompt}]
            response = await self._llm.achat(
                messages,
                tier=LLMTier.SMART,
                temperature=0.4,
                max_tokens=6000,
                user_id=user_id,
                session_id=session_id,
                span_name="reviser-llm",
                step="reviser",
            )

            revised = response.content.strip()
            if not revised:
                # LLM 返回空, 保留原报告
                span.update(
                    output={"revised": False, "reason": "empty_llm_output"},
                    metadata={"revised": False},
                )
                return {"report_md": report_md}

            span.update(
                output={"revised": True, "new_report_len": len(revised)},
                metadata={"revised": True, "length_delta": len(revised) - len(report_md)},
            )
            return {"report_md": revised}

    @staticmethod
    def _format_contexts(contexts: list[Any]) -> str:
        """格式化上下文列表为文本 (截断避免 token 过大)."""
        if not contexts:
            return "(无上下文)"
        return "\n".join(f"[{i + 1}] {str(c)[:500]}" for i, c in enumerate(contexts[:20]))
