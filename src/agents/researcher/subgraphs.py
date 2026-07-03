"""Researcher 子图构建 (P0-02 多 Agent 协作).

AGENTS.md 第 3 章: 子智能体代码按名称隔离在 agents/<agent_name>/ 下.
AGENTS.md 第 5 章: 多 Agent 协作限 Supervisor 模式, 子图复用 StateGraph.
每个子图接收 ResearcherState, 返回 delta dict. 子图内部节点为简化版 (1-2 个节点).
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

from langgraph.graph import END, StateGraph

from src.config.settings import Settings, get_settings
from src.graph.state import ResearcherState

logger = logging.getLogger(__name__)


async def build_researcher_subgraph(settings: Settings | None = None) -> Any:
    """Researcher 子图: 复用现有 build_researcher_graph (完整研究流水线)."""
    settings = settings or get_settings()
    from src.graph.builder import build_researcher_graph

    return await build_researcher_graph(settings, use_checkpointer=False)


async def build_reviewer_subgraph(settings: Settings | None = None) -> Any:
    """Reviewer 子图: 报告评审 (P0-Future-01, 单节点简化版).

    P0-Future-01 重构: 改用 reviewer_node (Reviewer 类) 替代 source_curator_node.
    """
    settings = settings or get_settings()
    from src.graph.nodes import reviewer_node

    graph = StateGraph(ResearcherState)
    graph.add_node("reviewer", partial(reviewer_node, settings=settings))
    graph.set_entry_point("reviewer")
    graph.add_edge("reviewer", END)
    return graph.compile()


async def build_writer_subgraph(settings: Settings | None = None) -> Any:
    """Writer 子图: ReportGenerator 报告生成 (单节点简化版)."""
    settings = settings or get_settings()
    from src.graph.nodes import report_generator_node

    graph = StateGraph(ResearcherState)
    graph.add_node("writer", partial(report_generator_node, settings=settings))
    graph.set_entry_point("writer")
    graph.add_edge("writer", END)
    return graph.compile()


async def build_publisher_subgraph(settings: Settings | None = None) -> Any:
    """Publisher 子图: Publisher 输出 (单节点简化版)."""
    settings = settings or get_settings()
    from src.graph.nodes import publisher_node

    graph = StateGraph(ResearcherState)
    graph.add_node("publisher", partial(publisher_node, settings=settings))
    graph.set_entry_point("publisher")
    graph.add_edge("publisher", END)
    return graph.compile()
