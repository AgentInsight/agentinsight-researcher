"""LangGraph 条件边路由.

AGENTS.md 第 5 章: 路由必须显式 add_conditional_edges, 禁止隐式跳转.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from src.graph.state import ResearcherState

logger = logging.getLogger(__name__)


def create_iteration_guard(
    max_iterations: int = 10,
) -> Callable[[ResearcherState], str]:
    """创建迭代上限守卫路由函数 (AGENTS.md 第 5 章: max_iterations 硬上限).

    用于循环图 (如 multi_agent) 的条件边路由, 达到上限时强制跳转 publisher 终止.
    线性流水线图无循环, 不需要此守卫.
    """

    def guard(state: ResearcherState) -> str:
        current = state.get("iteration_count", 0)
        if current >= max_iterations:
            logger.warning("达到迭代上限 %d, 强制终止", max_iterations)
            return "publisher"
        return "continue"

    return guard


def create_revision_guard(
    max_revisions: int = 3,
) -> Callable[[ResearcherState], str]:
    """创建修订循环守卫路由函数 (AGENTS.md 第 5 章: max_iterations 硬上限).

    专用于 reviewer → reviser → reviewer 评审-修订循环的条件边路由.
    返回 "accept" (通过) 或 "revise" (需修订), 由调用方映射到具体节点:
        reviewer: {"accept": "publisher", "revise": "reviser"}

    守卫: revision_count >= max_revisions 时强制 accept, 防止无限循环.
    revision_count 由 reviser 节点每次返回 1 累加 (Annotated[int, operator.add] reducer).

    Args:
        max_revisions: 修订循环上限 (settings.max_revisions)

    Returns:
        路由函数, 返回 "accept" 或 "revise"
    """

    def guard(state: ResearcherState) -> str:
        decision = state.get("review_decision", "accept")
        if decision == "accept":
            return "accept"

        # max_revisions 守卫: 达到修订上限强制 accept (AGENTS.md 第 5 章 max_iterations 硬上限)
        revision_count = state.get("revision_count", 0)
        if revision_count >= max_revisions:
            logger.warning(
                "Reviewer 达到修订上限 %d, 强制 accept",
                max_revisions,
            )
            return "accept"

        return "revise"

    return guard


def create_fact_check_guard(
    max_iterations: int = 10,
) -> Callable[[ResearcherState], str]:
    """创建事实核查循环守卫路由函数 (AGENTS.md 第 5 章: max_iterations 硬上限).

    专用于 fact_checker → writer 事实核查-重写循环的条件边路由.
    返回 "accept" (通过) 或 "revise" (需重写), 由调用方映射到具体节点:
        fact_checker: {"accept": "reviewer", "revise": "writer"}

    守卫: iteration_count >= max_iterations 时强制 accept, 防止无限循环.
    iteration_count 由 fact_checker 节点每次返回 1 累加 (Annotated[int, operator.add] reducer).

    Args:
        max_iterations: 图迭代硬上限 (settings.graph_max_iterations)

    Returns:
        路由函数, 返回 "accept" 或 "revise"
    """

    def guard(state: ResearcherState) -> str:
        accepted = state.get("fact_check_accepted", True)
        if accepted:
            return "accept"

        # graph_max_iterations 守卫: 达到图迭代上限强制 accept (AGENTS.md 第 5 章 max_iterations 硬上限)
        iter_count = state.get("iteration_count", 0)
        if iter_count >= max_iterations:
            logger.warning(
                "FactChecker 达到图迭代上限 %d, 强制 accept 进入 reviewer",
                max_iterations,
            )
            return "accept"

        return "revise"

    return guard
