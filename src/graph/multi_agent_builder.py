"""多 Agent 图构建器 (P0-02 + P0-Future-01/02 + P2-Future-01 + P1-03).

AGENTS.md 第 5 章: LangGraph StateGraph 唯一编排, 节点纯函数, 显式条件边.
对标 GPT Researcher multi_agents/main.py + GPTR orchestrator 线性+条件边模式.

P0-Future-01/02 重构: 由 Supervisor 循环模式改为线性+条件边模式.
原因: reviewer/fact_checker 的 accept|revise 条件边与 Supervisor "回到 supervisor" 循环冲突.
ResearcherSupervisor 类保留 (供未来单 Agent 模式或子图复用), 但不再作为图节点.

P2-Future-01 新增: Visualizer 节点插入 reviewer accept 与 publisher 之间,
对最终报告生成 Mermaid 图表 (基于已通过评审的报告, 避免修订后图表过时).

P1-03 子图复用: reviewer↔reviser 评审-修订循环提取为可复用子图
(build_revision_subgraph), 主图通过子图节点 "revision" 调用.
子图共享同一份 ResearcherState schema, 内部用 add_conditional_edges 控制循环,
max_revisions 守卫封装在子图内. 子图不挂 checkpointer, 由父图统一持久化.

完整多 Agent 流程 (线性+条件边 + 子图复用):
    START → agent_creator → researcher → writer → fact_checker
    fact_checker → (accept → revision 子图 | revise → writer)
    revision 子图: START → reviewer → (accept → END | revise → reviser)
                   reviser → reviewer
    revision 子图 → visualizer → publisher → END

守卫 (V4-P0-01 重构, 提取到 edges.py 作为可复用守卫工厂):
- fact_checker revise → writer 循环: create_fact_check_guard(graph_max_iterations)
  - iteration_count 由 fact_checker 节点累加, 达 graph_max_iterations 强制 accept
- reviewer revise → reviser 循环: create_revision_guard(max_revisions)
  - revision_count 由 reviser 节点累加, 达 max_revisions 强制 accept
- 两守卫均返回语义化 "accept"|"revise", 由 conditional_edges mapping 映射到具体节点
- AGENTS.md 第 5 章: max_iterations 为硬上限, 不可软超时
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

from langgraph.graph import END, StateGraph

from src.config.settings import Settings, get_settings
from src.graph.edges import create_fact_check_guard, create_revision_guard
from src.graph.state import ResearcherState

logger = logging.getLogger(__name__)


def build_revision_subgraph(settings: Settings | None = None) -> Any:
    """构建 reviewer↔reviser 评审-修订可复用子图 (P1-03 子图复用).

    子图结构:
        START → reviewer → (accept → END | revise → reviser)
        reviser → reviewer

    守卫: max_revisions (达上限强制 accept), 由 create_revision_guard 实现.
    revision_count 由 reviser 节点每次返回 1 累加 (Annotated[int, operator.add] reducer).

    子图共享同一份 ResearcherState schema, 可作为父图节点嵌入:
        parent.add_node("revision", compiled_subgraph)

    子图不挂 checkpointer — 由父图 checkpointer 统一持久化全部状态
    (含 review_decision / revision_count 等字段). 子图 accept → END 后控制权回到父图.

    Args:
        settings: 全局配置, 读取 max_revisions

    Returns:
        编译后的子图 (compiled StateGraph)
    """
    settings = settings or get_settings()

    from src.graph.nodes import reviewer_node, reviser_node

    subgraph = StateGraph(ResearcherState)

    subgraph.add_node("reviewer", partial(reviewer_node, settings=settings))
    subgraph.add_node("reviser", partial(reviser_node, settings=settings))

    subgraph.set_entry_point("reviewer")

    # reviewer 条件边: accept → END (退出子图) | revise → reviser
    # (含 max_revisions 守卫, 达到上限强制 accept, AGENTS.md 第 5 章 max_iterations 硬上限)
    revision_guard = create_revision_guard(settings.max_revisions)
    subgraph.add_conditional_edges(
        "reviewer",
        revision_guard,
        {
            "accept": END,
            "revise": "reviser",
        },
    )

    # reviser 完成后回到 reviewer (形成评审-修订循环)
    subgraph.add_edge("reviser", "reviewer")

    return subgraph.compile()


async def build_multi_agent_graph(
    settings: Settings | None = None,
    *,
    use_checkpointer: bool = True,
) -> Any:
    """构建多 Agent 协作图 (线性+条件边模式, P0-Future-01/02 + P2-Future-01 + P1-03).

    图结构:
        START → agent_creator → researcher → writer → fact_checker
        fact_checker → (accept → revision 子图 | revise → writer)
        revision 子图 → visualizer → publisher → END

    P2-Future-01: revision 子图 accept 后先经 visualizer 生成 Mermaid 图表, 再进 publisher.
    P1-03: reviewer↔reviser 循环封装为可复用子图, 主图以 "revision" 节点调用.
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
    )

    graph.add_node("agent_creator", partial(agent_creator_node, settings=settings))
    graph.add_node("researcher", partial(research_conductor_node, settings=settings))
    graph.add_node("writer", partial(report_generator_node, settings=settings))
    graph.add_node("fact_checker", partial(fact_checker_node, settings=settings))
    # P2-Future-01: Visualizer 节点 (revision 子图 accept 后, publisher 前)
    graph.add_node("visualizer", partial(visualizer_node, settings=settings))
    graph.add_node("publisher", partial(publisher_node, settings=settings))

    # P1-03: reviewer↔reviser 循环提取为可复用子图, 作为 "revision" 节点嵌入主图
    # 子图共享 ResearcherState, max_revisions 守卫封装在子图内
    revision_subgraph = build_revision_subgraph(settings)
    graph.add_node("revision", revision_subgraph)

    # 入口
    graph.set_entry_point("agent_creator")

    # 线性边: agent_creator → researcher → writer → fact_checker
    graph.add_edge("agent_creator", "researcher")
    graph.add_edge("researcher", "writer")
    graph.add_edge("writer", "fact_checker")

    # fact_checker 条件边: accept → revision 子图 | revise → writer
    # (含 graph_max_iterations 守卫, 防止 fact_checker revise → writer 无限循环)
    # AGENTS.md 第 5 章: max_iterations 为硬上限, 不可软超时
    fact_check_guard = create_fact_check_guard(settings.graph_max_iterations)
    graph.add_conditional_edges(
        "fact_checker",
        fact_check_guard,
        {
            "accept": "revision",
            "revise": "writer",
        },
    )

    # P2-Future-01: revision 子图 accept 后 → visualizer → publisher
    graph.add_edge("revision", "visualizer")
    graph.add_edge("visualizer", "publisher")

    # publisher → END (终止节点)
    graph.add_edge("publisher", END)

    # 编译
    checkpointer = None
    if use_checkpointer:
        from src.memory.checkpointer import get_checkpointer

        checkpointer = await get_checkpointer(settings)

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info(
        "多 Agent 线性+条件边图已构建 (含 revision 子图 + visualizer, "
        "max_revisions=%d, graph_max_iterations=%d)",
        settings.max_revisions,
        settings.graph_max_iterations,
    )
    return compiled
