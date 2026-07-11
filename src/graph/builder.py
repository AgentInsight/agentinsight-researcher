"""LangGraph 图构建器.

对标 AgentInsightService insight/graph.py 的 build_insight_graph 模式.
AGENTS.md 第 5 章硬约束:
- 生产 StateGraph 必须挂 PostgresSaver (PostgreSQL ≥16); 内存 Checkpoint 仅 ENV=dev
- 路由必须显式 add_conditional_edges, 禁止隐式跳转
- 每个图必须有终止节点; max_iterations 为硬上限

阶段 2 实现: 研究流水线图骨架 (6+1 Skill 节点).
实际节点逻辑在阶段 3 实现, 此处先用占位节点验证图结构.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

from langgraph.graph import END, StateGraph

from src.config.settings import Settings, get_settings
from src.graph.state import ResearcherState

logger = logging.getLogger(__name__)


def _route_after_agent_creator(state: ResearcherState) -> str:
    """动态角色生成后路由 (P0-01 DeepResearch 条件边).

    AGENTS.md 第 5 章: 路由必须显式 add_conditional_edges.
    - research_mode == "deep" → deep_research 节点 (递归深度研究)
    - 否则 → research_conductor 节点 (常规并行检索)
    """
    if state.get("research_mode") == "deep":
        return "deep_research"
    return "research_conductor"


async def build_researcher_graph(
    settings: Settings | None = None,
    *,
    use_checkpointer: bool = True,
) -> Any:
    """构建研究智能体 LangGraph 图.

    AGENTS.md 第 5 章: StateGraph + PostgresSaver.
    图结构 (设计参考 Skills 流水线, 行业适配采用 4 层机制):

        START
          ↓
        agent_creator  (AgentCreator LLM 动态角色生成, 设计参考 choose_agent)
          ↓ (P0-01 条件边: research_mode == "deep")
          ├──────────────────────┐
          ↓ (deep)               ↓ (其他)
        deep_research          research_conductor
        (DeepResearch 递归)    (ResearchConductor 规划+并行检索)
          ↓                      ↓
          └──────────┬───────────┘
                     ↓
        source_curator       (SourceCurator 来源策展, 可选)
                     ↓
        report_generator     (ReportGenerator 报告生成)
                     ↓
        publisher            (Publisher 输出 MD/HTML/PDF)
                     ↓
        END

    阶段 2: 骨架占位, 节点逻辑在阶段 3 实现.
    """
    settings = settings or get_settings()

    # 延迟导入节点实现 (阶段 3 完整实现)
    from src.graph.nodes import (
        agent_creator_node,
        deep_research_node,
        publisher_node,
        report_generator_node,
        research_conductor_node,
        source_curator_node,
    )

    # 构建图
    graph = StateGraph(ResearcherState)

    # 添加节点 (functools.partial 注入 settings, AGENTS.md 第 5 章)
    graph.add_node("agent_creator", partial(agent_creator_node, settings=settings))
    graph.add_node("deep_research", partial(deep_research_node, settings=settings))
    graph.add_node("research_conductor", partial(research_conductor_node, settings=settings))
    graph.add_node("source_curator", partial(source_curator_node, settings=settings))
    graph.add_node("report_generator", partial(report_generator_node, settings=settings))
    graph.add_node("publisher", partial(publisher_node, settings=settings))

    # 添加边
    # P0-01: agent_creator 后条件边路由 (deep_research | research_conductor)
    graph.set_entry_point("agent_creator")
    graph.add_conditional_edges(
        "agent_creator",
        _route_after_agent_creator,
        {"deep_research": "deep_research", "research_conductor": "research_conductor"},
    )
    # deep_research 完成后 → source_curator (P0-01)
    graph.add_edge("deep_research", "source_curator")
    graph.add_edge("research_conductor", "source_curator")
    graph.add_edge("source_curator", "report_generator")
    graph.add_edge("report_generator", "publisher")
    graph.add_edge("publisher", END)

    # 注: 当前为线性流水线图, 无循环, 不需要迭代守卫.
    # 未来若引入循环图 (如 multi_agent/subtopics 迭代), 应使用
    # create_iteration_guard(settings.graph_max_iterations) 作为条件边守卫,
    # 达到上限时强制跳转 publisher 终止 (AGENTS.md 第 5 章: max_iterations 硬上限).

    # 编译图 (可选挂 Checkpointer)
    # 分支优化 P-Checkpointer: get_checkpointer 失败时降级为无 checkpointer (不阻断图构建)
    checkpointer = None
    if use_checkpointer:
        from src.memory.checkpointer import get_checkpointer

        try:
            checkpointer = await get_checkpointer(settings)
        except RuntimeError as e:
            logger.warning("Checkpointer 初始化失败, 图以无持久化模式编译: %s", e)
            checkpointer = None

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("研究智能体 LangGraph 图已构建 (checkpointer=%s)", type(checkpointer).__name__)
    return compiled
