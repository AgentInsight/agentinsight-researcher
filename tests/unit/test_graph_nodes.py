"""单元测试: LangGraph 节点函数.

验证 src/graph/nodes.py 中所有节点函数:
- agent_creator_node: AgentCreator 动态角色生成, 返回 agent_role/agent_role_server/status
- research_conductor_node: ResearchConductor 研究总指挥, 返回聚合上下文
- source_curator_node: SourceCurator 来源策展, 返回 curated_sources
- report_generator_node: ReportGenerator 报告生成, 返回 report_md/report_formats
- publisher_node: Publisher 发布, 格式转换写入 report_formats
- fact_checker_node: FactChecker 事实核查, 返回 fact_check_accepted + iteration_count
- reviewer_node: Reviewer 评审, 返回 review_decision/review_feedback
- reviser_node: Reviser 修订, revision_count 累加 1

节点为纯函数 async def node(state: State) -> dict, 返回 delta 由 reducer 合并.
单元测试不依赖外部服务 (LLM/Qdrant/Redis/Postgres 全部 mock).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.graph.nodes import (
    agent_creator_node,
    fact_checker_node,
    publisher_node,
    report_generator_node,
    research_conductor_node,
    reviewer_node,
    reviser_node,
    source_curator_node,
)
from src.graph.state import ResearcherState

pytestmark = pytest.mark.unit


@pytest.fixture()
def settings() -> Settings:
    """构造最小 Settings (跳过 .env 加载, 使用默认值)."""
    return Settings(_env_file=None)


@pytest.fixture()
def base_state() -> ResearcherState:
    """基础研究状态 (含必要隔离键)."""
    return {
        "query": "分析新能源汽车市场",
        "session_id": "test-session",
        "user_id": "test-user",
        "agent_id": "agentinsight-researcher",
    }


# ========== agent_creator_node ==========


@pytest.mark.asyncio
@patch("src.graph.nodes.AgentCreator")
async def test_agent_creator_node_returns_delta(
    mock_creator_cls: MagicMock,
    settings: Settings,
    base_state: ResearcherState,
) -> None:
    """测试 agent_creator_node 返回 delta dict (agent_role/agent_role_server/status).

    AgentCreator.create_agent 返回 {server, agent_role_prompt},
    节点映射为 {agent_role, agent_role_server, status="running"}.
    """
    # Arrange: 模拟 AgentCreator 实例
    mock_creator = MagicMock()
    mock_creator.create_agent = AsyncMock(
        return_value={
            "server": "financial_analyst",
            "agent_role_prompt": "你是一位资深的金融分析师",
        }
    )
    mock_creator_cls.return_value = mock_creator

    # Act
    delta = await agent_creator_node(base_state, settings=settings)

    # Assert: delta 含三个键
    assert delta["agent_role"] == "你是一位资深的金融分析师"
    assert delta["agent_role_server"] == "financial_analyst"
    assert delta["status"] == "running"
    # AgentCreator 用 settings 实例化
    mock_creator_cls.assert_called_once_with(settings)
    # create_agent 调用参数
    mock_creator.create_agent.assert_awaited_once()
    call_args, call_kwargs = mock_creator.create_agent.call_args
    assert call_args[0] == "分析新能源汽车市场"
    assert call_kwargs["user_id"] == "test-user"
    assert call_kwargs["session_id"] == "test-session"


@pytest.mark.asyncio
@patch("src.graph.nodes.AgentCreator")
async def test_agent_creator_node_uses_preset_role_from_state(
    mock_creator_cls: MagicMock,
    settings: Settings,
    base_state: ResearcherState,
) -> None:
    """测试 state 中已注入 agent_role 时, 优先传递给 create_agent.

    AgentCreator.create_agent 内部 agent_role 非空时直接返回该值作为 agent_role_prompt
    (AGENT_ROLE 配置优先级).
    """
    mock_creator = MagicMock()
    # 模拟 AgentCreator 收到 preset_role 后回传该值
    mock_creator.create_agent = AsyncMock(
        return_value={"server": "custom", "agent_role_prompt": "预设角色 persona"}
    )
    mock_creator_cls.return_value = mock_creator

    base_state["agent_role"] = "预设角色 persona"
    delta = await agent_creator_node(base_state, settings=settings)

    assert delta["agent_role"] == "预设角色 persona"
    assert delta["agent_role_server"] == "custom"
    # 验证 preset_role 参数传递给 create_agent
    _, call_kwargs = mock_creator.create_agent.call_args
    assert call_kwargs["agent_role"] == "预设角色 persona"


# ========== research_conductor_node ==========


@pytest.mark.asyncio
@patch("src.graph.nodes.ResearchConductor")
async def test_research_conductor_node_returns_context(
    mock_conductor_cls: MagicMock,
    settings: Settings,
    base_state: ResearcherState,
) -> None:
    """测试 research_conductor_node 返回聚合上下文 (sub_queries/contexts/sources/visited_urls).

    ResearchConductor.conduct_research 返回含上述四键的 dict,
    节点直接映射到 delta.
    """
    mock_conductor = MagicMock()
    mock_conductor.conduct_research = AsyncMock(
        return_value={
            "sub_queries": ["新能源市场", "电池技术"],
            "contexts": ["上下文1", "上下文2"],
            "sources": [{"title": "src1", "url": "https://example.com"}],
            "visited_urls": {"https://example.com"},
        }
    )
    mock_conductor_cls.return_value = mock_conductor

    base_state["research_mode"] = "basic"
    base_state["agent_role"] = "金融分析师"
    delta = await research_conductor_node(base_state, settings=settings)

    assert delta["sub_queries"] == ["新能源市场", "电池技术"]
    assert delta["contexts"] == ["上下文1", "上下文2"]
    assert len(delta["sources"]) == 1
    assert delta["sources"][0]["url"] == "https://example.com"
    assert delta["visited_urls"] == {"https://example.com"}
    # 验证 conduct_research 调用参数
    mock_conductor.conduct_research.assert_awaited_once()
    _, kwargs = mock_conductor.conduct_research.call_args
    assert kwargs["mode"] == "basic"
    assert kwargs["agent_role"] == "金融分析师"
    assert kwargs["user_id"] == "test-user"
    assert kwargs["session_id"] == "test-session"


@pytest.mark.asyncio
@patch("src.graph.nodes.ResearchConductor")
async def test_research_conductor_node_handles_non_list_uploaded_files(
    mock_conductor_cls: MagicMock,
    settings: Settings,
    base_state: ResearcherState,
) -> None:
    """测试 uploaded_files_context 非列表时降级为空列表."""
    mock_conductor = MagicMock()
    mock_conductor.conduct_research = AsyncMock(
        return_value={
            "sub_queries": [],
            "contexts": [],
            "sources": [],
            "visited_urls": set(),
        }
    )
    mock_conductor_cls.return_value = mock_conductor

    base_state["uploaded_files_context"] = "not-a-list"
    await research_conductor_node(base_state, settings=settings)

    _, kwargs = mock_conductor.conduct_research.call_args
    assert kwargs["uploaded_files_context"] == []


# ========== source_curator_node ==========


@pytest.mark.asyncio
@patch("src.graph.nodes.SourceCurator")
async def test_source_curator_node_filters_sources(
    mock_curator_cls: MagicMock,
    base_state: ResearcherState,
) -> None:
    """测试 source_curator_node 返回 curated_sources (curate_sources=True 时).

    SourceCurator.curate_sources 返回过滤后的来源列表,
    节点写入 delta["curated_sources"].
    """
    settings = Settings(_env_file=None, curate_sources=True)
    mock_curator = MagicMock()
    curated_sources = [
        {"title": "高质量来源", "url": "https://arxiv.org/abs/1234", "score": 0.95},
    ]
    mock_curator.curate_sources = AsyncMock(return_value=curated_sources)
    mock_curator_cls.return_value = mock_curator

    base_state["sources"] = [
        {"title": "来源1", "url": "https://arxiv.org/abs/1234"},
        {"title": "来源2", "url": "https://blog.example.com"},
    ]
    delta = await source_curator_node(base_state, settings=settings)

    assert delta["curated_sources"] == curated_sources
    assert len(delta["curated_sources"]) == 1
    mock_curator_cls.assert_called_once_with(settings)
    mock_curator.curate_sources.assert_awaited_once()


@pytest.mark.asyncio
async def test_source_curator_node_skips_when_disabled(
    base_state: ResearcherState,
) -> None:
    """测试 curate_sources=False 时返回空 delta (跳过策展)."""
    settings = Settings(_env_file=None, curate_sources=False)
    base_state["sources"] = [{"title": "src1"}]
    delta = await source_curator_node(base_state, settings=settings)
    assert delta == {}


@pytest.mark.asyncio
async def test_source_curator_node_skips_when_no_sources(
    base_state: ResearcherState,
) -> None:
    """测试 curate_sources=True 但 sources 为空时返回空 delta."""
    settings = Settings(_env_file=None, curate_sources=True)
    delta = await source_curator_node(base_state, settings=settings)
    assert delta == {}


# ========== report_generator_node ==========


@pytest.mark.asyncio
@patch("src.llm.token_budget.get_token_budget_allocator")
@patch("src.graph.nodes.ReportGenerator")
async def test_report_generator_node_returns_report(
    mock_generator_cls: MagicMock,
    mock_get_allocator: AsyncMock,
    settings: Settings,
    base_state: ResearcherState,
) -> None:
    """测试 report_generator_node 返回 report_md/report_formats/status.

    ReportGenerator.generate_report 返回 {report_md, image_url, image_b64},
    节点写入 report_md + report_formats["md"] + status="completed".
    """
    # Mock token_budget allocator (成本回写)
    mock_allocator = MagicMock()
    mock_allocator.get_total_cost = AsyncMock(
        return_value={"total_cost_usd": 0.012, "total_tokens": 1500}
    )
    mock_allocator.get_step_costs = AsyncMock(return_value={})
    mock_get_allocator.return_value = mock_allocator

    mock_generator = MagicMock()
    mock_generator.generate_report = AsyncMock(
        return_value={
            "report_md": "# 新能源汽车市场报告\n\n正文内容",
            "image_url": None,
            "image_b64": None,
        }
    )
    mock_generator_cls.return_value = mock_generator

    base_state["contexts"] = ["上下文1", "上下文2"]
    base_state["sources"] = [{"title": "src1"}]
    delta = await report_generator_node(base_state, settings=settings)

    assert delta["report_md"] == "# 新能源汽车市场报告\n\n正文内容"
    assert delta["status"] == "completed"
    assert delta["report_formats"]["md"] == "# 新能源汽车市场报告\n\n正文内容"
    # 成本回写字段
    assert delta["total_cost_usd"] == 0.012
    assert delta["total_tokens"] == 1500
    assert "token_logs" in delta


@pytest.mark.asyncio
@patch("src.llm.token_budget.get_token_budget_allocator")
@patch("src.graph.nodes.ReportGenerator")
async def test_report_generator_node_with_image(
    mock_generator_cls: MagicMock,
    mock_get_allocator: AsyncMock,
    settings: Settings,
    base_state: ResearcherState,
) -> None:
    """测试 report_generator_node 含配图字段时回写 report_image_url/report_image_b64."""
    mock_allocator = MagicMock()
    mock_allocator.get_total_cost = AsyncMock(
        return_value={"total_cost_usd": 0.0, "total_tokens": 0}
    )
    mock_allocator.get_step_costs = AsyncMock(return_value={})
    mock_get_allocator.return_value = mock_allocator

    mock_generator = MagicMock()
    mock_generator.generate_report = AsyncMock(
        return_value={
            "report_md": "# 报告",
            "image_url": "https://example.com/img.png",
            "image_b64": "base64data",
        }
    )
    mock_generator_cls.return_value = mock_generator

    base_state["contexts"] = ["ctx"]
    delta = await report_generator_node(base_state, settings=settings)

    assert delta["report_image_url"] == "https://example.com/img.png"
    assert delta["report_image_b64"] == "base64data"


@pytest.mark.asyncio
@patch("src.graph.nodes.ReportGenerator")
async def test_report_generator_node_cost_writeback_failure_non_blocking(
    mock_generator_cls: MagicMock,
    settings: Settings,
    base_state: ResearcherState,
) -> None:
    """测试 token_budget 异常时不阻断主流程 (delta 仍含 report_md)."""
    mock_generator = MagicMock()
    mock_generator.generate_report = AsyncMock(
        return_value={
            "report_md": "# 报告",
            "image_url": None,
            "image_b64": None,
        }
    )
    mock_generator_cls.return_value = mock_generator

    base_state["contexts"] = ["ctx"]
    with patch(
        "src.llm.token_budget.get_token_budget_allocator",
        side_effect=Exception("Redis 不可用"),
    ):
        delta = await report_generator_node(base_state, settings=settings)

    # 主字段仍存在, 成本字段缺失 (异常被 catch)
    assert delta["report_md"] == "# 报告"
    assert delta["status"] == "completed"
    assert "total_cost_usd" not in delta


# ========== publisher_node ==========


@pytest.mark.asyncio
@patch("src.graph.nodes.Publisher")
async def test_publisher_node_converts_format_html(
    mock_publisher_cls: MagicMock,
    settings: Settings,
    base_state: ResearcherState,
) -> None:
    """测试 publisher_node HTML 格式转换写入 report_formats["html"]."""
    mock_publisher = MagicMock()
    mock_publisher.publish = AsyncMock(
        return_value={
            "format": "html",
            "content": "<html><body>报告</body></html>",
            "path": None,
        }
    )
    mock_publisher_cls.return_value = mock_publisher

    base_state["report_formats"] = {"md": "# 报告"}
    base_state["report_format"] = "html"
    delta = await publisher_node(base_state, settings=settings)

    assert delta["status"] == "completed"
    assert delta["report_format"] == "html"
    assert delta["report_formats"]["html"] == "<html><body>报告</body></html>"
    # md 格式保留
    assert delta["report_formats"]["md"] == "# 报告"
    # report_md 同步写入 (deprecated 兼容)
    assert delta["report_md"] == "# 报告"


@pytest.mark.asyncio
@patch("src.graph.nodes.Publisher")
async def test_publisher_node_markdown_passthrough(
    mock_publisher_cls: MagicMock,
    settings: Settings,
    base_state: ResearcherState,
) -> None:
    """测试 publisher_node markdown 格式直接透传."""
    mock_publisher = MagicMock()
    mock_publisher.publish = AsyncMock(
        return_value={
            "format": "markdown",
            "content": "# MD 报告",
            "path": None,
        }
    )
    mock_publisher_cls.return_value = mock_publisher

    base_state["report_md"] = "# MD 报告"
    base_state["report_format"] = "markdown"
    delta = await publisher_node(base_state, settings=settings)

    assert delta["report_format"] == "markdown"
    assert delta["report_formats"]["md"] == "# MD 报告"
    assert "html" not in delta["report_formats"]


@pytest.mark.asyncio
@patch("src.graph.nodes.Publisher")
async def test_publisher_node_pdf_format(
    mock_publisher_cls: MagicMock,
    settings: Settings,
    base_state: ResearcherState,
) -> None:
    """测试 publisher_node PDF 格式写入 report_formats["pdf"] (path)."""
    mock_publisher = MagicMock()
    mock_publisher.publish = AsyncMock(
        return_value={
            "format": "pdf",
            "content": None,
            "path": "/tmp/report.pdf",
        }
    )
    mock_publisher_cls.return_value = mock_publisher

    base_state["report_md"] = "# 报告"
    base_state["report_format"] = "pdf"
    delta = await publisher_node(base_state, settings=settings)

    assert delta["report_format"] == "pdf"
    assert delta["report_formats"]["pdf"] == "/tmp/report.pdf"


# ========== fact_checker_node ==========


@pytest.mark.asyncio
@patch("src.graph.nodes.FactChecker")
async def test_fact_checker_node_returns_decision(
    mock_checker_cls: MagicMock,
    settings: Settings,
    base_state: ResearcherState,
) -> None:
    """测试 fact_checker_node 返回 fact_check_accepted/fact_check_issues/iteration_count.

    iteration_count 累加 1 (Annotated[int, operator.add] reducer),
    用于 graph_max_iterations 守卫.
    """
    mock_checker = MagicMock()
    mock_checker.check = AsyncMock(
        return_value={
            "fact_check_accepted": False,
            "fact_check_issues": ["报告称增长 50%, 但上下文无此数据"],
        }
    )
    mock_checker_cls.return_value = mock_checker

    base_state["report_md"] = "# 报告\n增长 50%"
    base_state["contexts"] = ["上下文1"]
    delta = await fact_checker_node(base_state, settings=settings)

    assert delta["fact_check_accepted"] is False
    assert len(delta["fact_check_issues"]) == 1
    assert "增长 50%" in delta["fact_check_issues"][0]
    # iteration_count 累加 1 (reducer 用 operator.add)
    assert delta["iteration_count"] == 1


@pytest.mark.asyncio
@patch("src.graph.nodes.FactChecker")
async def test_fact_checker_node_accepted_returns_empty_issues(
    mock_checker_cls: MagicMock,
    settings: Settings,
    base_state: ResearcherState,
) -> None:
    """测试 fact_check_accepted=True 时 issues 为空."""
    mock_checker = MagicMock()
    mock_checker.check = AsyncMock(
        return_value={
            "fact_check_accepted": True,
            "fact_check_issues": [],
        }
    )
    mock_checker_cls.return_value = mock_checker

    base_state["report_md"] = "# 报告"
    delta = await fact_checker_node(base_state, settings=settings)

    assert delta["fact_check_accepted"] is True
    assert delta["fact_check_issues"] == []
    assert delta["iteration_count"] == 1


# ========== reviewer_node ==========


@pytest.mark.asyncio
@patch("src.graph.nodes.Reviewer")
async def test_reviewer_node_returns_decision(
    mock_reviewer_cls: MagicMock,
    settings: Settings,
    base_state: ResearcherState,
) -> None:
    """测试 reviewer_node 返回 review_decision/review_feedback.

    Reviewer.review 返回 {review_decision, review_feedback, review_scores},
    节点映射 review_decision + review_feedback 到 delta.
    """
    mock_reviewer = MagicMock()
    mock_reviewer.review = AsyncMock(
        return_value={
            "review_decision": "revise",
            "review_feedback": "事实性: 4/10 — 存在幻觉",
            "review_scores": {
                "factual": {"score": 4, "issues": ["幻觉"]},
            },
        }
    )
    mock_reviewer_cls.return_value = mock_reviewer

    base_state["report_md"] = "# 报告"
    base_state["revision_count"] = 1
    delta = await reviewer_node(base_state, settings=settings)

    assert delta["review_decision"] == "revise"
    assert "事实性" in delta["review_feedback"]
    # review_scores 不在节点 delta 中 (仅 review_decision + review_feedback)
    assert "review_scores" not in delta


@pytest.mark.asyncio
@patch("src.graph.nodes.Reviewer")
async def test_reviewer_node_accept_decision(
    mock_reviewer_cls: MagicMock,
    settings: Settings,
    base_state: ResearcherState,
) -> None:
    """测试 reviewer_node accept 决策."""
    mock_reviewer = MagicMock()
    mock_reviewer.review = AsyncMock(
        return_value={
            "review_decision": "accept",
            "review_feedback": "报告质量合格, 予以接受.",
            "review_scores": {},
        }
    )
    mock_reviewer_cls.return_value = mock_reviewer

    base_state["report_md"] = "# 报告"
    delta = await reviewer_node(base_state, settings=settings)

    assert delta["review_decision"] == "accept"


# ========== reviser_node ==========


@pytest.mark.asyncio
@patch("src.graph.nodes.Reviser")
async def test_reviser_node_increments_count(
    mock_reviser_cls: MagicMock,
    settings: Settings,
    base_state: ResearcherState,
) -> None:
    """测试 reviser_node 返回修订后报告 + revision_count 累加 1.

    revision_count 用 Annotated[int, operator.add] reducer,
    节点返回 1 由 reducer 累加.
    """
    mock_reviser = MagicMock()
    mock_reviser.revise = AsyncMock(return_value={"report_md": "# 修订后报告\n\n改进内容"})
    mock_reviser_cls.return_value = mock_reviser

    base_state["report_md"] = "# 原报告"
    base_state["review_feedback"] = "事实性不足, 需补充数据"
    base_state["revision_count"] = 1
    delta = await reviser_node(base_state, settings=settings)

    assert delta["report_md"] == "# 修订后报告\n\n改进内容"
    assert delta["report_formats"]["md"] == "# 修订后报告\n\n改进内容"
    # revision_count 返回 1 (由 operator.add reducer 累加)
    assert delta["revision_count"] == 1
    # 验证 Reviser.revise 调用参数
    mock_reviser.revise.assert_awaited_once()
    _, kwargs = mock_reviser.revise.call_args
    assert kwargs["user_id"] == "test-user"
    assert kwargs["session_id"] == "test-session"


@pytest.mark.asyncio
@patch("src.graph.nodes.Reviser")
async def test_reviser_node_preserves_existing_formats(
    mock_reviser_cls: MagicMock,
    settings: Settings,
    base_state: ResearcherState,
) -> None:
    """测试 reviser_node 保留已有 report_formats 其他格式 (仅更新 md)."""
    mock_reviser = MagicMock()
    mock_reviser.revise = AsyncMock(return_value={"report_md": "# 新报告"})
    mock_reviser_cls.return_value = mock_reviser

    base_state["report_md"] = "# 旧报告"
    base_state["review_feedback"] = "需修订"
    base_state["report_formats"] = {"md": "# 旧报告", "html": "<html>旧</html>"}
    delta = await reviser_node(base_state, settings=settings)

    # md 更新, html 保留
    assert delta["report_formats"]["md"] == "# 新报告"
    assert delta["report_formats"]["html"] == "<html>旧</html>"
