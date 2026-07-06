"""FactChecker 事实核查 Agent (P0-Future-02).

AGENTS.md 第 5 章: LangGraph StateGraph 唯一编排, 节点纯函数.
对标 GPT Researcher fact_checker + DeepEval 幻觉率门禁 (AGENTS.md 第 10 章).

FactChecker 职责:
- 核查报告事实是否与上下文一致
- LLM 提取报告中的事实声明, 逐条核对上下文
- 返回 fact_check_accepted (bool) + fact_check_issues (list[str])
- 用 LLMClient tier=STRATEGIC, 用 safe_json_parse 解析
- 用 trace_chain 包裹 (AGENTS.md 第 10 章, 禁 agentinsight.observe 装饰器)

启用开关: settings.fact_check_enabled (默认 True).
"""

from __future__ import annotations

import logging
from typing import Any

from src.common.json_utils import safe_json_parse
from src.config.settings import Settings, get_settings
from src.graph.state import ResearcherState
from src.llm.client import LLMClient, LLMTier, get_llm_client
from src.observability.tracing import trace_chain

logger = logging.getLogger(__name__)


class FactChecker:
    """事实核查 Agent (P0-Future-02).

    核查报告中的事实声明是否与上下文一致, 返回 accepted/issues.
    对标 GPT Researcher fact_checker + DeepEval 幻觉率门禁.
    """

    settings: Settings
    _llm: LLMClient

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or get_llm_client()

    async def check(
        self,
        state: ResearcherState,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """核查报告事实是否与上下文一致.

        Args:
            state: 研究状态, 含 report_md / contexts
            user_id: 用户 ID (隔离键, AGENTS.md 第 8 章)
            session_id: 会话 ID (隔离键, AGENTS.md 第 6 章)

        Returns:
            {"fact_check_accepted": bool, "fact_check_issues": list[str]}
            - fact_check_accepted: True 表示通过事实核查
            - fact_check_issues: 不一致的事实声明列表
        """
        async with trace_chain(
            name="fact-checker",
            input={
                "report_len": len(state.get("report_md", "")),
                "contexts_count": len(state.get("contexts", [])),
            },
            user_id=user_id,
            session_id=session_id,
        ) as span:
            # 配置开关: fact_check_enabled=False 时跳过核查
            if not self.settings.fact_check_enabled:
                span.update(
                    output={"accepted": True, "reason": "disabled"},
                    metadata={"fact_check_enabled": False},
                )
                return {
                    "fact_check_accepted": True,
                    "fact_check_issues": [],
                }

            report_md = state.get("report_md", "")
            contexts = state.get("contexts", [])

            if not report_md:
                span.update(
                    output={"accepted": False, "reason": "empty_report"},
                    metadata={"error": "report_md 为空"},
                )
                return {
                    "fact_check_accepted": False,
                    "fact_check_issues": ["报告内容为空"],
                }

            if not contexts:
                # 无上下文无法核查, 默认通过
                span.update(
                    output={"accepted": True, "reason": "no_contexts"},
                    metadata={"warning": "无上下文, 跳过核查"},
                )
                return {
                    "fact_check_accepted": True,
                    "fact_check_issues": [],
                }

            contexts_text = self._format_contexts(contexts)

            prompt = f"""你的任务是: 事实核查员. 核查以下报告中的事实声明是否与提供的上下文一致.

核查规则:
1. 提取报告中的关键事实声明 (数据/结论/引用/事件等)
2. 逐条核对每个事实声明是否有上下文支持
3. 上下文未提及但合理推断的不算不一致 (仅标记明确矛盾或编造)
4. 标记所有"上下文中找不到支持"或"与上下文矛盾"的事实声明

可用上下文:
{contexts_text}

待核查报告:
{report_md[:8000]}

请返回 JSON:
{{
  "accepted": true 或 false,
  "issues": ["不一致的事实声明1: 报告说X, 但上下文中无此信息/与之矛盾"]
}}

判定标准:
- accepted=true: 无不一致声明, 或仅有少量无法核对的边缘声明
- accepted=false: 存在明确与上下文矛盾或编造的事实声明

仅返回 JSON:"""

            messages = [{"role": "user", "content": prompt}]
            response = await self._llm.achat(
                messages,
                tier=LLMTier.STRATEGIC,
                temperature=0.0,
                max_tokens=4000,
                user_id=user_id,
                session_id=session_id,
                span_name="fact-checker-llm",
                step="fact_checker",
            )

            result = safe_json_parse(
                response.content,
                fallback={"accepted": True, "issues": []},
            )
            if not isinstance(result, dict):
                result = {"accepted": True, "issues": []}

            accepted = bool(result.get("accepted", True))
            issues = result.get("issues", [])
            if not isinstance(issues, list):
                issues = [str(issues)] if issues else []
            else:
                issues = [str(i) for i in issues if i]

            span.update(
                output={"accepted": accepted, "issues_count": len(issues)},
                metadata={
                    "accepted": accepted,
                    "issues_count": len(issues),
                },
            )
            return {
                "fact_check_accepted": accepted,
                "fact_check_issues": issues,
            }

    @staticmethod
    def _format_contexts(contexts: list[Any]) -> str:
        """格式化上下文列表为文本 (截断避免 token 过大)."""
        if not contexts:
            return "(无上下文)"
        return "\n".join(f"[{i + 1}] {str(c)[:600]}" for i, c in enumerate(contexts[:25]))
