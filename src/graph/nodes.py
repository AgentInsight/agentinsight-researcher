"""LangGraph 节点定义 (完整实现).

对标 GPT Researcher skills/ 流水线.
AGENTS.md 第 5 章硬约束:
- 节点为纯函数 async def node(state: State) -> dict, 单一职责无副作用
- 节点禁止原地修改入参 State, 必须返回 delta dict 由 reducer 合并
- 节点内禁止直连厂商 LLM SDK, 统一走 llm/ 网关 (LiteLLM)
- 每个节点必须包裹在 AgentInsight trace span 内 (AGENTS.md 第 10 章)

流水线 (对标 GPT Researcher Skills):
    industry_classifier → research_conductor → source_curator → report_generator → publisher
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.settings import Settings
from src.graph.state import ResearcherState
from src.observability.tracing import trace_chain
from src.skills.researcher.industry_classifier import IndustryClassifier
from src.skills.researcher.publisher import Publisher
from src.skills.researcher.report_generator import ReportGenerator
from src.skills.researcher.research_conductor import ResearchConductor
from src.skills.researcher.source_curator import SourceCurator

logger = logging.getLogger(__name__)


async def industry_classifier_node(
    state: ResearcherState,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """IndustryClassifier 行业识别节点.

    用户需求 4:
    1. Qdrant 检索 GICS 知识库识别行业
    2. 命中失败时 LLM 兜底识别
    3. 加载对应行业 prompt_family
    """
    async with trace_chain(
        name="industry-classifier",
        input={"query": state.get("query", "")[:200]},
        session_id=state.get("session_id"),
        user_id=state.get("user_id"),
    ):
        classifier = IndustryClassifier(settings)
        result = await classifier.classify(
            state.get("query", ""),
            user_id=state.get("user_id"),
            session_id=state.get("session_id"),
        )
        return {
            "industry_code": result["industry_code"],
            "industry_name": result["industry_name"],
            "industry_sector": result.get("industry_sector", ""),
            "industry_group": result.get("industry_group", ""),
            "industry_sub": result.get("industry_sub", ""),
            "industry_prompt_family": result["industry_prompt_family"],
            "status": "running",
        }


async def research_conductor_node(
    state: ResearcherState,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """ResearchConductor 研究总指挥节点.

    对标 GPT Researcher skills/researcher.py:
    1. plan_research: 按行业提示词拆解子查询
    2. asyncio.gather 并行 _process_sub_query:
       - 搜索 (中文优先路由)
       - 抓取 (BrowserManager)
       - 压缩去重 (ContextManager)
    """
    async with trace_chain(
        name="research-conductor",
        input={"query": state.get("query", "")[:200]},
        session_id=state.get("session_id"),
        user_id=state.get("user_id"),
    ):
        conductor = ResearchConductor(settings)
        uploaded_files_ctx = state.get("uploaded_files_context", [])
        if not isinstance(uploaded_files_ctx, list):
            uploaded_files_ctx = []
        result = await conductor.conduct_research(
            state.get("query", ""),
            industry_prompt_family=state.get("industry_prompt_family"),
            user_id=state.get("user_id"),
            session_id=state.get("session_id"),
            uploaded_files_context=uploaded_files_ctx,
        )
        return {
            "sub_queries": result["sub_queries"],
            "contexts": result["contexts"],
            "sources": result["sources"],
            "visited_urls": result["visited_urls"],
        }


async def source_curator_node(
    state: ResearcherState,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """SourceCurator 来源策展节点 (可选).

    对标 GPT Researcher skills/curator.py:
    LLM 评估来源可信度与相关性 (Reviewer 职责).
    cfg.CURATE_SOURCES=True 时启用.
    """
    async with trace_chain(
        name="source-curator",
        input={"sources_count": len(state.get("sources", []))},
        session_id=state.get("session_id"),
        user_id=state.get("user_id"),
    ):
        if not settings.curate_sources:
            return {}  # 跳过策展

        sources = state.get("sources", [])
        if not sources:
            return {}

        curator = SourceCurator(settings)
        curated = await curator.curate_sources(
            state.get("query", ""),
            sources,
            max_results=10,
            industry_prompt_family=state.get("industry_prompt_family"),
            user_id=state.get("user_id"),
            session_id=state.get("session_id"),
        )
        return {"curated_sources": curated}


async def report_generator_node(
    state: ResearcherState,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """ReportGenerator 报告生成节点.

    对标 GPT Researcher skills/writer.py:
    按行业模板合成长报告 (Writer 职责).
    """
    async with trace_chain(
        name="report-generator",
        input={
            "contexts_count": len(state.get("contexts", [])),
            "industry": state.get("industry_name", "通用研究"),
        },
        session_id=state.get("session_id"),
        user_id=state.get("user_id"),
    ):
        generator = ReportGenerator(settings)
        # 优先用策展后的来源, 否则用原来源
        sources = state.get("curated_sources") or state.get("sources", [])
        report_md = await generator.generate_report(
            state.get("query", ""),
            state.get("contexts", []),
            sources,
            report_type=state.get("report_type", settings.default_report_type),
            tone=state.get("tone", "objective"),
            total_words=state.get("total_words", settings.total_words),
            industry_prompt_family=state.get("industry_prompt_family"),
            user_id=state.get("user_id"),
            session_id=state.get("session_id"),
        )
        return {
            "report_md": report_md,
            "status": "completed",
        }


async def publisher_node(
    state: ResearcherState,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """Publisher 发布节点.

    对标 GPT Researcher multi_agents/agents/publisher.py:
    Markdown/HTML/PDF 输出, 引用规范化.
    """
    async with trace_chain(
        name="publisher",
        input={"format": state.get("report_format", "markdown")},
        session_id=state.get("session_id"),
        user_id=state.get("user_id"),
    ):
        publisher = Publisher(settings)
        result = await publisher.publish(
            state.get("report_md", ""),
            output_format=state.get("report_format", "markdown"),
            user_id=state.get("user_id"),
            session_id=state.get("session_id"),
        )
        delta: dict[str, Any] = {"status": "completed"}
        if result.get("format") == "html":
            delta["report_html"] = result["content"]
        elif result.get("format") == "pdf":
            delta["report_pdf_path"] = result["path"]
        return delta
