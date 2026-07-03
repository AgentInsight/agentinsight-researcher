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


async def build_researcher_graph(
    settings: Settings | None = None,
    *,
    use_checkpointer: bool = True,
) -> Any:
    """构建研究智能体 LangGraph 图.

    AGENTS.md 第 5 章: StateGraph + PostgresSaver.
    图结构 (对标 GPT Researcher Skills 流水线):

        START
          ↓
        industry_classifier  (IndustryClassifier 行业识别)
          ↓
        research_conductor   (ResearchConductor 规划+并行检索)
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
        industry_classifier_node,
        publisher_node,
        report_generator_node,
        research_conductor_node,
        source_curator_node,
    )

    # 构建图
    graph = StateGraph(ResearcherState)

    # 添加节点 (functools.partial 注入 settings, AGENTS.md 第 5 章)
    graph.add_node("industry_classifier", partial(industry_classifier_node, settings=settings))
    graph.add_node("research_conductor", partial(research_conductor_node, settings=settings))
    graph.add_node("source_curator", partial(source_curator_node, settings=settings))
    graph.add_node("report_generator", partial(report_generator_node, settings=settings))
    graph.add_node("publisher", partial(publisher_node, settings=settings))

    # 添加边 (线性流水线, 阶段 3 可加条件边)
    graph.set_entry_point("industry_classifier")
    graph.add_edge("industry_classifier", "research_conductor")
    graph.add_edge("research_conductor", "source_curator")
    graph.add_edge("source_curator", "report_generator")
    graph.add_edge("report_generator", "publisher")
    graph.add_edge("publisher", END)

    # 编译图 (可选挂 Checkpointer)
    checkpointer = None
    if use_checkpointer:
        from src.memory.checkpointer import get_checkpointer

        checkpointer = await get_checkpointer(settings)

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("研究智能体 LangGraph 图已构建 (checkpointer=%s)", type(checkpointer).__name__)
    return compiled
