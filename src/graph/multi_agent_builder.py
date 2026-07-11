"""多 Agent 图构建器 (P0-02 + P0-Future-01/02 + P2-Future-01 + P1-03 + P0-Future-03).

AGENTS.md 第 5 章: LangGraph StateGraph 唯一编排, 节点纯函数, 显式条件边.
设计参考 multi_agents/main.py + orchestrator 线性+条件边模式.

P0-Future-01/02 重构: 由 Supervisor 循环模式改为线性+条件边模式.
原因: reviewer/fact_checker 的 accept|revise 条件边与 Supervisor "回到 supervisor" 循环冲突.
ResearcherSupervisor 类保留 (供未来单 Agent 模式或子图复用), 但不再作为图节点.

P2-Future-01 新增: Visualizer 节点插入 reviewer accept 与 publisher 之间,
对最终报告生成 Mermaid 图表 (基于已通过评审的报告, 避免修订后图表过时).

P1-03 子图复用: reviewer↔reviser 评审-修订循环提取为可复用子图
(build_revision_subgraph), 主图通过子图节点 "revision" 调用.
子图共享同一份 ResearcherState schema, 内部用 add_conditional_edges 控制循环,
max_revisions 守卫封装在子图内. 子图不挂 checkpointer, 由父图统一持久化.

P0-Future-03 人在回路: settings.human_review_enabled=True 时, agent_creator 与
researcher 之间插入 human 节点, 审核研究计划/大纲. human 节点通过 WebSocket 推送
计划给前端, 阻塞等待用户反馈 (asyncio.Future, 带超时). revisions_count 达
max_plan_revisions 强制 accept. False 时跳过 human 节点, 保持原 agent_creator → researcher.

完整多 Agent 流程 (线性+条件边 + 子图复用 + 人在回路):
    START → agent_creator → [human] → researcher → writer → fact_checker
    [human]: human_review_enabled=True 时启用
        agent_creator → human → (accept → researcher | revise → agent_creator)
    fact_checker → (accept → revision 子图 | revise → writer)
    revision 子图: START → reviewer → (accept → END | revise → reviser)
                   reviser → reviewer
    revision 子图 → visualizer → publisher → END

守卫 (V4-P0-01 重构, 提取到 edges.py 作为可复用守卫工厂):
- fact_checker revise → writer 循环: create_fact_check_guard(graph_max_iterations)
  - iteration_count 由 fact_checker 节点累加, 达 graph_max_iterations 强制 accept
- reviewer revise → reviser 循环: create_revision_guard(max_revisions)
  - revision_count 由 reviser 节点累加, 达 max_revisions 强制 accept
- human revise → agent_creator 循环: create_human_review_guard(max_plan_revisions)
  - revisions_count 由 human 节点累加, 达 max_plan_revisions 强制 accept
- 三守卫均返回语义化 "accept"|"revise", 由 conditional_edges mapping 映射到具体节点
- AGENTS.md 第 5 章: max_iterations 为硬上限, 不可软超时
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial
from typing import Any

from langgraph.graph import END, StateGraph

from src.config.settings import Settings, get_settings
from src.graph.edges import create_fact_check_guard, create_revision_guard
from src.graph.state import ResearcherState

logger = logging.getLogger(__name__)


def create_human_review_guard(
    max_plan_revisions: int = 3,
) -> Callable[[ResearcherState], str]:
    """创建人在回路审核守卫路由函数 (P0-Future-03).

    专用于 human → agent_creator 评审-修订循环的条件边路由.
    返回 "accept" (通过, 进入 researcher) 或 "revise" (回 agent_creator 重新生成角色),
    由调用方映射到具体节点:
        human: {"accept": "researcher", "revise": "agent_creator"}

    守卫: revisions_count >= max_plan_revisions 时强制 accept, 防止无限循环.
    revisions_count 由 human 节点每次返回 1 累加 (Annotated[int, operator.add] reducer).
    human_feedback 为 None 表示用户接受 (accept), 非 None 表示要求修订 (revise).

    Args:
        max_plan_revisions: 研究计划修订上限 (settings.max_plan_revisions)

    Returns:
        路由函数, 返回 "accept" 或 "revise"
    """

    def guard(state: ResearcherState) -> str:
        feedback = state.get("human_feedback")
        if feedback is None:
            return "accept"

        # max_plan_revisions 守卫: 达到修订上限强制 accept (AGENTS.md 第 5 章 max_iterations 硬上限)
        revisions_count = state.get("revisions_count", 0)
        if revisions_count >= max_plan_revisions:
            logger.warning(
                "Human review 达到修订上限 %d, 强制 accept",
                max_plan_revisions,
            )
            return "accept"

        return "revise"

    return guard


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
    """构建多 Agent 协作图.

    图结构 (线性 + 条件边):
        agent_creator → researcher → writer → fact_checker → revision 子图
        agent_creator → human → (accept → researcher | revise → agent_creator)
        fact_checker → (accept → revision 子图 | revise → writer)
        revision 子图 → publisher → END

    P1-03: reviewer↔reviser 循环封装为可复用子图, 主图以 "revision" 节点调用.
    P0-Future-03: settings.human_review_enabled=True 时插入 human 节点审核研究计划,
        False 时跳过 (保持 agent_creator → researcher 直连).
    """
    settings = settings or get_settings()

    graph = StateGraph(ResearcherState)

    # 节点 (复用 graph/nodes.py 中的节点定义)
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
    graph.add_node("publisher", partial(publisher_node, settings=settings))

    # P0-Future-03: 人在回路节点 (human_review_enabled=True 时启用)
    # HumanAgent 通过 WebSocket 推送计划给前端, 阻塞等待用户反馈 (asyncio.Future, 带超时)
    human_review_enabled = bool(settings.human_review_enabled)
    if human_review_enabled:
        from src.agents.researcher.human import HumanAgent, human_node

        human_agent = HumanAgent(settings)
        graph.add_node("human", partial(human_node, human_agent=human_agent))

    # P1-03: reviewer↔reviser 循环提取为可复用子图, 作为 "revision" 节点嵌入主图
    # 子图共享 ResearcherState, max_revisions 守卫封装在子图内
    revision_subgraph = build_revision_subgraph(settings)
    graph.add_node("revision", revision_subgraph)

    # 入口
    graph.set_entry_point("agent_creator")

    # agent_creator → human | researcher (P0-Future-03 人在回路分支)
    if human_review_enabled:
        # agent_creator → human → (accept → researcher | revise → agent_creator)
        # 含 max_plan_revisions 守卫, 达上限强制 accept (AGENTS.md 第 5 章 max_iterations 硬上限)
        graph.add_edge("agent_creator", "human")
        human_review_guard = create_human_review_guard(settings.max_plan_revisions)
        graph.add_conditional_edges(
            "human",
            human_review_guard,
            {
                "accept": "researcher",
                "revise": "agent_creator",
            },
        )
    else:
        # 人在回路关闭: agent_creator → researcher 直连
        graph.add_edge("agent_creator", "researcher")

    # 线性边: researcher → writer → fact_checker
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

    # revision 子图 accept 后 → publisher
    graph.add_edge("revision", "publisher")

    # publisher → END (终止节点)
    graph.add_edge("publisher", END)

    # 编译
    # 分支优化 P-Checkpointer: get_checkpointer 失败时降级为无 checkpointer (不阻断图构建)
    checkpointer = None
    if use_checkpointer:
        from src.memory.checkpointer import get_checkpointer

        try:
            checkpointer = await get_checkpointer(settings)
        except RuntimeError as e:
            logger.warning("Checkpointer 初始化失败, 多 Agent 图以无持久化模式编译: %s", e)
            checkpointer = None

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info(
        "多 Agent 线性+条件边图已构建 (含 revision 子图 + human_review=%s, "
        "max_revisions=%d, graph_max_iterations=%d, max_plan_revisions=%d)",
        human_review_enabled,
        settings.max_revisions,
        settings.graph_max_iterations,
        settings.max_plan_revisions,
    )
    return compiled
