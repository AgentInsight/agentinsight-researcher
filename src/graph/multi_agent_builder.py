"""多 Agent 图构建器 (P0-02 + P0-Future-01/02 + P2-Future-01).

AGENTS.md 第 5 章: LangGraph StateGraph 唯一编排, 节点纯函数, 显式条件边.
对标 GPT Researcher multi_agents/main.py + GPTR orchestrator 线性+条件边模式.

P0-Future-01/02 重构: 由 Supervisor 循环模式改为线性+条件边模式.
原因: reviewer/fact_checker 的 accept|revise 条件边与 Supervisor "回到 supervisor" 循环冲突.
ResearcherSupervisor 类保留 (供未来单 Agent 模式或子图复用), 但不再作为图节点.

P2-Future-01 新增: Visualizer 节点插入 reviewer accept 与 publisher 之间,
对最终报告生成 Mermaid 图表 (基于已通过评审的报告, 避免修订后图表过时).

完整多 Agent 流程 (线性+条件边):
    START → agent_creator → researcher → writer → fact_checker
    fact_checker → (accept → reviewer | revise → writer)
    reviewer → (accept → visualizer → publisher | revise → reviser)
    reviser → reviewer
    publisher → END

守卫:
- fact_checker revise → writer 循环: graph_max_iterations 守卫 (iteration_count 累加)
- reviewer revise → reviser 循环: max_revisions 守卫 (revision_count 累加)
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

from langgraph.graph import END, StateGraph

from src.config.settings import Settings, get_settings
from src.graph.state import ResearcherState

logger = logging.getLogger(__name__)


async def build_multi_agent_graph(
    settings: Settings | None = None,
    *,
    use_checkpointer: bool = True,
) -> Any:
    """构建多 Agent 协作图 (线性+条件边模式, P0-Future-01/02 + P2-Future-01).

    图结构:
        START → agent_creator → researcher → writer → fact_checker
        fact_checker → (accept → reviewer | revise → writer)
        reviewer → (accept → visualizer → publisher | revise → reviser)
        reviser → reviewer
        publisher → END

    P2-Future-01: reviewer accept 后先经 visualizer 生成 Mermaid 图表, 再进 publisher.
    """
    settings = settings or get_settings()

    graph = StateGraph(ResearcherState)

    # 节点 (复用 graph/nodes.py 中的节点定义)
    from src.agents.researcher.visualizer import visualizer_node
    from src.graph.nodes import (
        agent_creator_node,
        fact_checker_node,
        publisher_node,
        report_generator_node,
        research_conductor_node,
        reviewer_node,
        reviser_node,
    )

    graph.add_node("agent_creator", partial(agent_creator_node, settings=settings))
    graph.add_node("researcher", partial(research_conductor_node, settings=settings))
    graph.add_node("writer", partial(report_generator_node, settings=settings))
    graph.add_node("fact_checker", partial(fact_checker_node, settings=settings))
    graph.add_node("reviewer", partial(reviewer_node, settings=settings))
    graph.add_node("reviser", partial(reviser_node, settings=settings))
    # P2-Future-01: Visualizer 节点 (reviewer accept 后, publisher 前)
    graph.add_node("visualizer", partial(visualizer_node, settings=settings))
    graph.add_node("publisher", partial(publisher_node, settings=settings))

    # 入口
    graph.set_entry_point("agent_creator")

    # 线性边: agent_creator → researcher → writer → fact_checker
    graph.add_edge("agent_creator", "researcher")
    graph.add_edge("researcher", "writer")
    graph.add_edge("writer", "fact_checker")

    # fact_checker 条件边: accept → reviewer | revise → writer
    # (含 graph_max_iterations 守卫, 防止 fact_checker revise → writer 无限循环)
    graph.add_conditional_edges(
        "fact_checker",
        partial(_fact_checker_router, settings=settings),
        {
            "reviewer": "reviewer",
            "writer": "writer",
        },
    )

    # reviewer 条件边: accept → visualizer | revise → reviser
    # (含 max_revisions 守卫, 达到上限强制 accept)
    # P2-Future-01: accept 后先经 visualizer, 再进 publisher
    graph.add_conditional_edges(
        "reviewer",
        partial(_reviewer_router, settings=settings),
        {
            "visualizer": "visualizer",
            "reviser": "reviser",
        },
    )

    # P2-Future-01: visualizer → publisher (reviewer accept 路径)
    graph.add_edge("visualizer", "publisher")

    # reviser 完成后回到 reviewer (形成评审-修订循环)
    graph.add_edge("reviser", "reviewer")

    # publisher → END (终止节点)
    graph.add_edge("publisher", END)

    # 编译
    checkpointer = None
    if use_checkpointer:
        from src.memory.checkpointer import get_checkpointer

        checkpointer = await get_checkpointer(settings)

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info(
        "多 Agent 线性+条件边图已构建 (含 visualizer, max_revisions=%d, graph_max_iterations=%d)",
        settings.max_revisions,
        settings.graph_max_iterations,
    )
    return compiled


def _fact_checker_router(state: ResearcherState, *, settings: Settings) -> str:
    """fact_checker 条件边路由: accept → reviewer | revise → writer.

    守卫: iteration_count >= graph_max_iterations 时强制 accept (防止无限循环).
    AGENTS.md 第 5 章: max_iterations 为硬上限, 不可软超时.
    """
    accepted = state.get("fact_check_accepted", True)
    if accepted:
        return "reviewer"

    # graph_max_iterations 守卫: 达到图迭代上限强制 accept
    iter_count = state.get("iteration_count", 0)
    if iter_count >= settings.graph_max_iterations:
        logger.warning(
            "FactChecker 达到图迭代上限 %d, 强制 accept 进入 reviewer",
            settings.graph_max_iterations,
        )
        return "reviewer"

    return "writer"


def _reviewer_router(state: ResearcherState, *, settings: Settings) -> str:
    """reviewer 条件边路由: accept → visualizer | revise → reviser.

    P2-Future-01: accept 后路由到 visualizer (生成 Mermaid 图表), 再进 publisher.
    守卫: revision_count >= max_revisions 时强制 accept (P0-Future-01 章节级修订上限).
    AGENTS.md 第 5 章: max_iterations 为硬上限, 不可软超时.
    """
    decision = state.get("review_decision", "accept")
    if decision == "accept":
        return "visualizer"

    # max_revisions 守卫: 达到修订上限强制 accept
    revision_count = state.get("revision_count", 0)
    if revision_count >= settings.max_revisions:
        logger.warning(
            "Reviewer 达到修订上限 %d, 强制 accept 进入 visualizer",
            settings.max_revisions,
        )
        return "visualizer"

    return "reviser"
