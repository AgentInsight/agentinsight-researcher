"""Reviewer 报告评审 Agent (P0-Future-01).

AGENTS.md 第 5 章: LangGraph StateGraph 唯一编排, 节点纯函数.
对标 GPT Researcher multi_agents/agents/reviewer.py + 章节级修订循环.

Reviewer 职责:
- 评审报告质量 (上下文覆盖 / 幻觉 / 结构完整性)
- 返回 review_decision ("accept"|"revise") + review_feedback
- 用 LLMClient tier=STRATEGIC 调用 (慢但精, 适合评审)
- 用 safe_json_parse 解析 LLM 返回的 JSON
- 用 trace_chain 包裹 (AGENTS.md 第 10 章, 禁 agentinsight.observe 装饰器)

行业适配采用 GPTR 风格 4 层机制, agent_role 注入角色 persona.
"""

from __future__ import annotations

import logging
from typing import Any

from src.common.json_utils import safe_json_parse
from src.config.settings import Settings, get_settings
from src.graph.state import ResearcherState
from src.llm.client import LLMClient, LLMTier
from src.observability.tracing import trace_chain

logger = logging.getLogger(__name__)


class Reviewer:
    """报告评审 Agent (P0-Future-01).

    用 strategic_llm 评审报告质量, 返回 accept/revise 决策与反馈.
    对标 GPT Researcher reviewer 角色 + 章节级修订循环入口.
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

    async def review(
        self,
        state: ResearcherState,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """评审报告质量.

        Args:
            state: 研究状态, 含 report_md / contexts / query
            user_id: 用户 ID (隔离键, AGENTS.md 第 8 章)
            session_id: 会话 ID (隔离键, AGENTS.md 第 6 章)

        Returns:
            {"review_decision": "accept"|"revise", "review_feedback": str}
            - review_decision: 接受或需修订
            - review_feedback: 评审反馈 (revise 时含具体修订建议)
        """
        async with trace_chain(
            name="reviewer",
            input={
                "query": state.get("query", "")[:100],
                "report_len": len(state.get("report_md", "")),
            },
            user_id=user_id,
            session_id=session_id,
        ) as span:
            report_md = state.get("report_md", "")
            if not report_md:
                span.update(
                    output={"review_decision": "revise", "reason": "empty_report"},
                    metadata={"error": "report_md 为空"},
                )
                return {
                    "review_decision": "revise",
                    "review_feedback": "报告内容为空, 请重新生成.",
                }

            role_persona = (
                state.get("agent_role") or "你是一位资深研究分析专家, 擅长多领域综合研究."
            )
            contexts = state.get("contexts", [])
            contexts_text = self._format_contexts(contexts)

            prompt = f"""{role_persona}

你的任务是: 评审以下研究报告的质量, 决定是否接受 (accept) 或需要修订 (revise).

评审维度:
1. 上下文覆盖: 报告是否充分使用了提供的上下文信息
2. 幻觉检查: 报告中是否有上下文不支持的事实声明
3. 结构完整性: 报告结构是否完整 (引言/正文/结论/引用)
4. 内容深度: 分析是否深入, 论证是否充分
5. 语言质量: 表达是否清晰专业

研究问题: {state.get("query", "")}

上下文 (节选):
{contexts_text}

报告内容:
{report_md[:8000]}

请返回 JSON:
{{
  "decision": "accept" 或 "revise",
  "feedback": "评审反馈, 若 revise 需含具体修订建议 (按章节/段落指出问题)",
  "issues": ["问题1", "问题2"]
}}

若报告质量合格返回 accept, 否则返回 revise. 仅返回 JSON:"""

            messages = [{"role": "user", "content": prompt}]
            response = await self._llm.achat(
                messages,
                tier=LLMTier.STRATEGIC,
                temperature=0.2,
                max_tokens=4000,
                user_id=user_id,
                session_id=session_id,
                span_name="reviewer-llm",
                step="reviewer",
            )

            result = safe_json_parse(
                response.content,
                fallback={"decision": "accept", "feedback": "", "issues": []},
            )
            if not isinstance(result, dict):
                result = {"decision": "accept", "feedback": "", "issues": []}

            decision = str(result.get("decision", "accept")).lower().strip()
            if decision not in ("accept", "revise"):
                decision = "accept"

            feedback = str(result.get("feedback", ""))
            issues = result.get("issues", [])
            if not isinstance(issues, list):
                issues = []

            # 合并 feedback 与 issues 列表
            issues_text = "\n".join(f"- {i}" for i in issues if i)
            if issues_text:
                feedback = f"{feedback}\n\n问题清单:\n{issues_text}".strip()

            span.update(
                output={"review_decision": decision, "feedback_len": len(feedback)},
                metadata={
                    "decision": decision,
                    "issues_count": len(issues),
                },
            )
            return {
                "review_decision": decision,
                "review_feedback": feedback,
            }

    @staticmethod
    def _format_contexts(contexts: list[Any]) -> str:
        """格式化上下文列表为文本 (截断避免 token 过大)."""
        if not contexts:
            return "(无上下文)"
        return "\n".join(f"[{i + 1}] {str(c)[:500]}" for i, c in enumerate(contexts[:20]))
