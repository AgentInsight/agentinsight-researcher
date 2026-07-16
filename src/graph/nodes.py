"""LangGraph 节点定义 (完整实现).

节点约束:
- 节点为纯函数 async def node(state: State) -> dict, 单一职责无副作用
- 节点禁止原地修改入参 State, 必须返回 delta dict 由 reducer 合并
- 节点内禁止直连厂商 LLM SDK, 统一走 llm/ 网关 (LiteLLM)
- 每个节点必须包裹在 AgentInsight trace span 内

流水线 (行业适配采用 4 层机制):
    agent_creator → research_conductor → source_curator → report_generator → publisher
"""

from __future__ import annotations

import logging
from typing import Any

from src.agents.researcher.fact_checker import FactChecker
from src.agents.researcher.reviewer import Reviewer
from src.agents.researcher.reviser import Reviser
from src.config.settings import Settings
from src.graph.state import ResearcherState
from src.observability.tracing import trace_chain
from src.skills.researcher.agent_creator import AgentCreator
from src.skills.researcher.publisher import Publisher
from src.skills.researcher.report_generator import ReportGenerator
from src.skills.researcher.research_conductor import ResearchConductor
from src.skills.researcher.source_curator import SourceCurator

logger = logging.getLogger(__name__)


async def agent_creator_node(
    state: ResearcherState,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """AgentCreator 动态角色生成节点.

    4 层隐形机制 (Prompt 层):
    1. settings.agent_role 配置注入 (优先级最高, AGENT_ROLE)
    2. 否则 LLM 根据 query 语义动态生成行业 persona
    """
    async with trace_chain(
        name="agent-creator",
        input={"query": state.get("query", "")[:200]},
        session_id=state.get("session_id"),
        user_id=state.get("user_id"),
    ):
        creator = AgentCreator(settings)
        # 优先用 state 中已注入的 agent_role (来自 ChatRequest / settings),
        # 否则 LLM 动态生成 (AgentCreator 内部判断 agent_role 优先级)
        preset_role = state.get("agent_role") or settings.agent_role
        result = await creator.create_agent(
            state.get("query", ""),
            user_id=state.get("user_id"),
            session_id=state.get("session_id"),
            agent_role=preset_role,
        )
        return {
            "agent_role": result["agent_role_prompt"],
            "agent_role_server": result["server"],
            "status": "running",
        }


async def research_conductor_node(
    state: ResearcherState,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """ResearchConductor 研究总指挥节点.

    1. plan_research: 按动态角色 persona 拆解子查询
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
            mode=state.get("research_mode", ""),
            agent_role=state.get("agent_role"),
            user_id=state.get("user_id"),
            session_id=state.get("session_id"),
            uploaded_files_context=uploaded_files_ctx,
            query_domains=state.get("query_domains"),
        )
        return {
            "sub_queries": result["sub_queries"],
            "contexts": result["contexts"],
            "sources": result["sources"],
            "visited_urls": result["visited_urls"],
        }


async def deep_research_node(
    state: ResearcherState,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """DeepResearch 递归深度研究节点.

    通过 breadth×depth 递归树探索, 每层聚合上下文.
    agent_creator 条件边: research_mode == "deep" 时路由到此节点.
    """
    async with trace_chain(
        name="deep-research",
        input={"query": state.get("query", "")[:200], "mode": "deep"},
        session_id=state.get("session_id"),
        user_id=state.get("user_id"),
    ):
        from src.skills.researcher.deep_research import DeepResearcher

        researcher = DeepResearcher(settings)
        # 自适应深度: 开启时不传 breadth/depth, 让 research() 内部 _assess_complexity 评估
        # (修复自适应深度机制缺陷: 原显式传参导致 breadth is None 永远不满足)
        if settings.deep_research_adaptive:
            result = await researcher.research(
                state.get("query", ""),
                user_id=state.get("user_id"),
                session_id=state.get("session_id"),
                query_domains=state.get("query_domains"),
            )
        else:
            result = await researcher.research(
                state.get("query", ""),
                breadth=state.get("deep_research_breadth", settings.deep_research_breadth),
                depth=state.get("deep_research_depth", settings.deep_research_depth),
                user_id=state.get("user_id"),
                session_id=state.get("session_id"),
                query_domains=state.get("query_domains"),
            )
        return {
            "sub_queries": [],  # DeepResearch 内部递归, 不走外层 sub_queries
            "contexts": [result["context"]] if result["context"] else [],
            "sources": result["sources"],
            "visited_urls": list(researcher._visited_urls),
        }


async def source_curator_node(
    state: ResearcherState,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """SourceCurator 来源策展节点 (可选).

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
            agent_role=state.get("agent_role"),
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

    按动态角色 persona 合成长报告 (Writer 职责).
    image_generation_enabled=True 时报告含配图 (deepseek-v4-flash).
    """
    async with trace_chain(
        name="report-generator",
        input={
            "contexts_count": len(state.get("contexts", [])),
            "agent_role_server": state.get("agent_role_server", "researcher"),
        },
        session_id=state.get("session_id"),
        user_id=state.get("user_id"),
    ):
        generator = ReportGenerator(settings)
        # 优先用策展后的来源, 否则用原来源
        sources = state.get("curated_sources") or state.get("sources", [])
        result = await generator.generate_report(
            state.get("query", ""),
            state.get("contexts", []),
            sources,
            report_type=state.get("report_type", settings.default_report_type),
            tone=state.get("tone", "objective"),
            total_words=state.get("total_words", settings.total_words),
            agent_role=state.get("agent_role"),
            user_id=state.get("user_id"),
            session_id=state.get("session_id"),
            language=state.get("report_language", settings.report_language or "zh"),
        )
        report_md = result["report_md"]
        # 同步写入 report_formats["md"] 与 report_md (兼容字段)
        existing_formats = state.get("report_formats") or {}
        new_formats: dict[str, str] = dict(existing_formats)
        new_formats["md"] = report_md
        delta: dict[str, Any] = {
            "report_md": report_md,  # 兼容字段
            "report_formats": new_formats,
            "status": "completed",
        }
        # 补充报告配图字段 (若生成了图像)
        if result.get("image_url"):
            delta["report_image_url"] = result["image_url"]
        if result.get("image_b64"):
            delta["report_image_b64"] = result["image_b64"]

        # 回写 LLM 成本到 State (打通 LLMClient → State 最后一公里)
        # 读取 per-session allocator, 避免全局污染数据
        # 让 final_state 含 costs 字段, 供 routes.py usage 读取.
        try:
            from src.llm.token_budget import get_token_budget_allocator

            allocator = await get_token_budget_allocator(state.get("session_id", ""))
            total_cost = await allocator.get_total_cost()
            step_costs = await allocator.get_step_costs()
            delta["total_cost_usd"] = total_cost.get("total_cost_usd", 0.0)
            delta["total_tokens"] = total_cost.get("total_tokens", 0)
            # token_logs: 节点级调用日志 (按 step 展平)
            token_logs = [
                {
                    "step": step,
                    "prompt_tokens": sc.get("prompt_tokens", 0),
                    "completion_tokens": sc.get("completion_tokens", 0),
                    "total_tokens": sc.get("total_tokens", 0),
                    "cost_usd": sc.get("cost_usd", 0.0),
                    "call_count": sc.get("call_count", 0),
                    "model_breakdown": sc.get("model_breakdown", {}),
                }
                for step, sc in step_costs.items()
            ]
            delta["token_logs"] = token_logs
        except Exception as cost_err:  # noqa: BLE001
            logger.debug("成本回写 State 失败 (非阻断): %s", cost_err)

        return delta


async def publisher_node(
    state: ResearcherState,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """Publisher 发布节点.

    Markdown/HTML/PDF 输出, 引用规范化.

    节点保持纯函数无副作用, 仅生成 report_md/report_formats/file_path 等到 state,
    不直接调用 report_store.save_report. 报告持久化由 API 层 (routes.py) 在
    graph 完成后异步调用 report_store.save_report 完成 (节点纯函数约束).

    报告格式字段统一写入 report_formats dict (key 为 md/html/pdf/docx/json),
    report_md 同步写入兼容字段.
    """
    async with trace_chain(
        name="publisher",
        input={"format": state.get("report_format", "markdown")},
        session_id=state.get("session_id"),
        user_id=state.get("user_id"),
    ):
        publisher = Publisher(settings)
        # 优先读 report_formats["md"], 兼容期回退 report_md
        report_formats = state.get("report_formats") or {}
        report_md = report_formats.get("md") or state.get("report_md", "")
        result = await publisher.publish(
            report_md,
            output_format=state.get("report_format", "markdown"),
            title=state.get("query", ""),
            sources=state.get("curated_sources") or state.get("sources", []),
            agent_role_server=state.get("agent_role_server", ""),
            research_mode=state.get("research_mode", ""),
            user_id=state.get("user_id"),
            session_id=state.get("session_id"),
        )
        # 合并到 report_formats dict (key 为格式名), 同时同步 report_md 兼容旧代码
        new_formats: dict[str, str] = dict(report_formats)
        new_formats["md"] = report_md
        result_format = result.get("format", "markdown")
        if result_format == "html":
            new_formats["html"] = result["content"]
        elif result_format == "pdf":
            new_formats["pdf"] = result["path"]
        elif result_format == "docx":
            new_formats["docx"] = result["content"]
        elif result_format == "json":
            new_formats["json"] = result["content"]

        # P2-21: LLM 响应消息分块保留
        # 完整报告已写入 research_reports 表 (由 routes.py 调用 save_report),
        # State 同时保留摘要字段; 后续追问场景可优先读 report_summary 降低内存占用,
        # 完整报告从 research_reports 表读取 (避免每次 aget_state 加载 30K+ 字符).
        # 不删除 report_md: routes.py 在 graph.ainvoke 完成后仍需读取它构造响应与持久化,
        # publisher_node 本身也依赖 report_md 输出最终结果.
        if len(report_md) > 2000:
            report_summary = report_md[:500] + "..."
        else:
            report_summary = report_md

        delta: dict[str, Any] = {
            "status": "completed",
            "report_format": result_format,
            "report_formats": new_formats,
            "report_md": report_md,  # 兼容字段
            "report_summary": report_summary,  # P2-21: 报告摘要, 降低追问场景内存占用
        }
        # 报告持久化由 API 层 (routes.py) 在 graph 完成后调用 report_store.save_report,
        # 节点不直接写 DB (节点纯函数无副作用约束).
        return delta


async def fact_checker_node(
    state: ResearcherState,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """FactChecker 事实核查节点.

    核查报告中的事实声明是否与上下文一致.
    fact_check_enabled=False 时节点内部跳过 (返回 accepted=True).
    条件边: accept → reviewer | revise → writer.
    """
    async with trace_chain(
        name="fact-checker-node",
        input={
            "report_len": len(state.get("report_md", "")),
            "enabled": settings.fact_check_enabled,
        },
        session_id=state.get("session_id"),
        user_id=state.get("user_id"),
    ):
        checker = FactChecker(settings)
        result = await checker.check(
            state,
            user_id=state.get("user_id"),
            session_id=state.get("session_id"),
        )
        # iteration_count 累加 1: fact_checker 为分支节点, 用于 graph_max_iterations 守卫
        # 防止 fact_checker revise → writer 无限循环 (max_iterations 硬上限)
        return {
            "fact_check_accepted": result["fact_check_accepted"],
            "fact_check_issues": result["fact_check_issues"],
            "iteration_count": 1,
        }


async def reviewer_node(
    state: ResearcherState,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """Reviewer 报告评审节点.

    评审报告质量 (上下文覆盖/幻觉/结构完整性), 返回 accept|revise 决策.
    条件边: accept → publisher | revise → reviser (含 max_revisions 守卫).
    """
    async with trace_chain(
        name="reviewer-node",
        input={
            "query": state.get("query", "")[:200],
            "report_len": len(state.get("report_md", "")),
            "revision_count": state.get("revision_count", 0),
        },
        session_id=state.get("session_id"),
        user_id=state.get("user_id"),
    ):
        reviewer = Reviewer(settings)
        result = await reviewer.review(
            state,
            user_id=state.get("user_id"),
            session_id=state.get("session_id"),
        )
        return {
            "review_decision": result["review_decision"],
            "review_feedback": result["review_feedback"],
        }


async def reviser_node(
    state: ResearcherState,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """Reviser 报告修订节点.

    根据 Reviewer 反馈修订报告, 返回新的 report_md.
    revision_count 用 Annotated[int, operator.add] reducer, 返回 1 累加.
    修订完成后回到 reviewer (由 multi_agent_builder 边定义).
    """
    async with trace_chain(
        name="reviser-node",
        input={
            "query": state.get("query", "")[:200],
            "report_len": len(state.get("report_md", "")),
            "revision_count": state.get("revision_count", 0),
        },
        session_id=state.get("session_id"),
        user_id=state.get("user_id"),
    ):
        reviser = Reviser(settings)
        result = await reviser.revise(
            state,
            user_id=state.get("user_id"),
            session_id=state.get("session_id"),
        )
        # 同步写入 report_formats["md"] 与 report_md (兼容字段)
        revised_md = result["report_md"]
        existing_formats = state.get("report_formats") or {}
        new_formats: dict[str, str] = dict(existing_formats)
        new_formats["md"] = revised_md
        # revision_count 累加 1 (Annotated[int, operator.add] reducer)
        delta: dict[str, Any] = {
            "report_md": revised_md,  # 兼容字段
            "report_formats": new_formats,
            "revision_count": 1,
        }
        return delta
