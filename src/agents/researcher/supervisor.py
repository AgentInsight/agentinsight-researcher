"""Researcher Supervisor (P0-02 多 Agent 协作).

AGENTS.md 第 5 章: 多 Agent 协作限 Supervisor 模式.
对标 GPT Researcher multi_agents/agents/manager.py.
Supervisor 决策下一个执行的 Agent, 含 max_iterations 守卫.
"""

from __future__ import annotations

import logging

from src.config.settings import Settings, get_settings
from src.graph.state import ResearcherState

logger = logging.getLogger(__name__)


class ResearcherSupervisor:
    """研究 Agent Supervisor (多 Agent 协作入口)."""

    settings: Settings

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def route(self, state: ResearcherState) -> str:
        """Supervisor 路由决策.

        Returns:
            "researcher" | "reviewer" | "writer" | "publisher" | "END"
        """
        # max_iterations 守卫 (AGENTS.md 第 5 章)
        iter_count = state.get("iteration_count", 0)
        if iter_count >= self.settings.graph_max_iterations:
            logger.warning(
                "Supervisor 达到迭代上限 %d, 强制终止", self.settings.graph_max_iterations
            )
            return "END"

        # 状态机路由
        status = state.get("status", "pending")
        has_contexts = bool(state.get("contexts"))
        has_curated = bool(state.get("curated_sources"))
        has_report = bool(state.get("report_md"))

        if status == "pending" or (status == "running" and not has_contexts):
            return "researcher"
        if has_contexts and not has_curated and self.settings.curate_sources:
            return "reviewer"
        if has_contexts and (has_curated or not self.settings.curate_sources) and not has_report:
            return "writer"
        if has_report and state.get("report_format", "markdown") != "markdown":
            return "publisher"
        return "END"
