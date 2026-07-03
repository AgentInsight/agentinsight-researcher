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
