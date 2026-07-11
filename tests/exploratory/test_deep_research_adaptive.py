"""探索性测试: DeepResearcher 自适应深度调用路径 (mock 版).

验证 src/graph/nodes.py 的 deep_research_node 在自适应深度开关不同状态下的调用路径:
- 自适应深度开启 (deep_research_adaptive=True): 不传 breadth/depth, 走 _assess_complexity
- 自适应深度关闭 (deep_research_adaptive=False): 显式传 breadth/depth
- max_sub_queries 守卫触发降级: 递归树超限时降级到 depth=1

mock 版探索性测试放在 tests/exploratory/ 下, mark=unit 避免被 conftest 跳过.
测试数据隔离: user_id=test_gptr_explore_*, session_id=test_deep_research_explore_*.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.graph.nodes import deep_research_node
from src.graph.state import ResearcherState

pytestmark = pytest.mark.unit


@pytest.fixture()
def base_state() -> ResearcherState:
    """基础研究状态 (含必要隔离键)."""
    return {
        "query": "分析 2026 年 AI Agent 行业趋势",
        "session_id": "test_deep_research_explore_session",
        "user_id": "test_gptr_explore_user",
        "agent_id": "agentinsight-researcher",
        "research_mode": "deep",
    }


# ========== 自适应深度开启时调用路径 ==========


@pytest.mark.asyncio
async def test_adaptive_enabled_calls_assess_complexity(
    base_state: ResearcherState,
) -> None:
    """测试自适应深度开启时, deep_research_node 不传 breadth/depth.

    验证: settings.deep_research_adaptive=True 时, researcher.research() 调用
    不包含 breadth/depth 参数 (触发内部 _assess_complexity).
    """
    settings = Settings(
        _env_file=None,
        mcp_strategy="disabled",
        deep_research_adaptive=True,
    )

    captured_kwargs: dict[str, Any] = {}

    async def mock_research(self: Any, query: str, **kwargs: Any) -> dict[str, Any]:
        captured_kwargs.update(kwargs)
        captured_kwargs["query"] = query
        return {
            "query": query,
            "context": "mock context",
            "sources": [],
            "learnings": [],
            "citations": {},
            "children": [],
        }

    with patch(
        "src.skills.researcher.deep_research.DeepResearcher.research",
        mock_research,
    ):
        result = await deep_research_node(base_state, settings=settings)

    # 自适应开启: 不传 breadth/depth (让 research 内部 _assess_complexity 评估)
    assert "breadth" not in captured_kwargs
    assert "depth" not in captured_kwargs
    # 传了 user_id/session_id/query_domains
    assert captured_kwargs.get("user_id") == "test_gptr_explore_user"
    assert captured_kwargs.get("session_id") == "test_deep_research_explore_session"
    # 返回结构正确
    assert result["contexts"] == ["mock context"]
    assert result["sources"] == []
    assert result["sub_queries"] == []


# ========== 自适应深度关闭时调用路径 ==========


@pytest.mark.asyncio
async def test_adaptive_disabled_passes_explicit_params(
    base_state: ResearcherState,
) -> None:
    """测试自适应深度关闭时, deep_research_node 显式传 breadth/depth.

    验证: settings.deep_research_adaptive=False 时, researcher.research() 调用
    包含显式 breadth/depth 参数 (从 settings 读取).
    """
    settings = Settings(
        _env_file=None,
        mcp_strategy="disabled",
        deep_research_adaptive=False,
        deep_research_breadth=4,
        deep_research_depth=2,
    )

    captured_kwargs: dict[str, Any] = {}

    async def mock_research(self: Any, query: str, **kwargs: Any) -> dict[str, Any]:
        captured_kwargs.update(kwargs)
        captured_kwargs["query"] = query
        return {
            "query": query,
            "context": "mock context",
            "sources": [],
            "learnings": [],
            "citations": {},
            "children": [],
        }

    with patch(
        "src.skills.researcher.deep_research.DeepResearcher.research",
        mock_research,
    ):
        await deep_research_node(base_state, settings=settings)

    # 自适应关闭: 显式传 breadth/depth
    assert captured_kwargs.get("breadth") == 4
    assert captured_kwargs.get("depth") == 2


# ========== 自适应深度关闭时 state 覆盖 settings ==========


@pytest.mark.asyncio
async def test_adaptive_disabled_state_overrides_settings(
    base_state: ResearcherState,
) -> None:
    """测试自适应深度关闭时, state 中的 breadth/depth 覆盖 settings 默认值."""
    settings = Settings(
        _env_file=None,
        mcp_strategy="disabled",
        deep_research_adaptive=False,
        deep_research_breadth=4,
        deep_research_depth=2,
    )
    # state 覆盖
    state = {**base_state, "deep_research_breadth": 6, "deep_research_depth": 3}

    captured_kwargs: dict[str, Any] = {}

    async def mock_research(self: Any, query: str, **kwargs: Any) -> dict[str, Any]:
        captured_kwargs.update(kwargs)
        return {
            "query": query,
            "context": "ctx",
            "sources": [],
            "learnings": [],
            "citations": {},
            "children": [],
        }

    with patch(
        "src.skills.researcher.deep_research.DeepResearcher.research",
        mock_research,
    ):
        await deep_research_node(state, settings=settings)

    # state 中的值覆盖 settings 默认值
    assert captured_kwargs.get("breadth") == 6
    assert captured_kwargs.get("depth") == 3


# ========== max_sub_queries 守卫触发降级 ==========


@pytest.mark.asyncio
async def test_max_sub_queries_guard_triggers_degradation() -> None:
    """测试 max_sub_queries 守卫触发降级: breadth=10/depth=3 → 降级到 depth=1.

    递归树规模: 10 * (1 + 5 + 25) = 310 > 28 (max_sub_queries)
    → 降级到 depth=1, 仅 10 个子查询, 不递归.
    """
    settings = Settings(
        _env_file=None,
        mcp_strategy="disabled",
        deep_research_adaptive=False,
        deep_research_max_sub_queries=28,
    )

    mock_llm = MagicMock()
    mock_llm.achat = AsyncMock()
    mock_cm = MagicMock()
    mock_cm.get_similar_content = AsyncMock(return_value="ctx")

    from src.skills.researcher.deep_research import DeepResearcher

    researcher = DeepResearcher(
        settings=settings,
        llm=mock_llm,
        context_manager=mock_cm,
    )

    call_count = 0

    async def mock_research_sub_query(sq: str, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        return {
            "context": f"ctx-{sq}",
            "sources": [],
            "learnings": [],
            "followUpQuestions": [],
            "citations": {},
        }

    async def mock_generate_sub_queries(
        query: str, breadth: int, **kwargs: Any
    ) -> list[dict[str, str]]:
        return [{"query": f"q{i}", "researchGoal": f"g{i}"} for i in range(breadth)]

    researcher._research_sub_query = mock_research_sub_query  # type: ignore[method-assign]
    researcher._generate_sub_queries = mock_generate_sub_queries  # type: ignore[method-assign]

    # breadth=10, depth=3 → 310 > 28, 降级到 depth=1
    result = await researcher.research("guard test", breadth=10, depth=3)

    # 降级到 depth=1: 不递归, children 为空
    assert result["children"] == []
    # 仅 10 个子查询 (depth=1 不递归)
    assert call_count == 10
    # 上下文含所有 10 个子查询
    assert "ctx-q0" in result["context"]
    assert "ctx-q9" in result["context"]


# ========== 自适应深度开启 + _assess_complexity 返回复杂查询 ==========


@pytest.mark.asyncio
async def test_adaptive_enabled_with_complex_query() -> None:
    """测试自适应深度开启 + 复杂查询 → _assess_complexity 返回 depth=3.

    验证自适应深度机制: 复杂查询 (complexity=5) 映射到 depth=3,
    但受 max_sub_queries 守卫限制 (4+8+16=28 = max_sub_queries, 刚好不触发降级).
    """
    settings = Settings(
        _env_file=None,
        mcp_strategy="disabled",
        deep_research_adaptive=True,
        deep_research_max_sub_queries=28,
    )

    mock_llm = MagicMock()
    mock_llm.achat = AsyncMock()
    mock_cm = MagicMock()
    mock_cm.get_similar_content = AsyncMock(return_value="ctx")

    from src.skills.researcher.deep_research import DeepResearcher

    researcher = DeepResearcher(
        settings=settings,
        llm=mock_llm,
        context_manager=mock_cm,
    )

    # Mock _assess_complexity 返回复杂查询参数 (depth=3)
    async def mock_assess_complexity(query: str, **kwargs: Any) -> dict[str, int]:
        return {"breadth": 4, "depth": 3, "concurrency": 6}

    researcher._assess_complexity = mock_assess_complexity  # type: ignore[method-assign]

    call_count = 0

    async def mock_research_sub_query(sq: str, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        return {
            "context": f"ctx-{sq}",
            "sources": [],
            "learnings": [],
            "followUpQuestions": [],
            "citations": {},
        }

    async def mock_generate_sub_queries(
        query: str, breadth: int, **kwargs: Any
    ) -> list[dict[str, str]]:
        return [{"query": f"q{i}", "researchGoal": f"g{i}"} for i in range(breadth)]

    researcher._research_sub_query = mock_research_sub_query  # type: ignore[method-assign]
    researcher._generate_sub_queries = mock_generate_sub_queries  # type: ignore[method-assign]

    # 不传 breadth/depth → 触发自适应 → _assess_complexity 返回 breadth=4/depth=3
    result = await researcher.research("complex query")

    # depth=3: 递归树 4+8+16=28 = max_sub_queries, 守卫不触发
    # children 非空 (depth=3 递归)
    assert len(result["children"]) > 0
    # 调用次数: 4 (depth 0) + 8 (depth 1) + 16 (depth 2) = 28
    assert call_count == 28


# ========== deep_research_node 返回结构验证 ==========


@pytest.mark.asyncio
async def test_deep_research_node_return_structure(
    base_state: ResearcherState,
) -> None:
    """测试 deep_research_node 返回结构含 sub_queries/contexts/sources/visited_urls."""
    settings = Settings(
        _env_file=None,
        mcp_strategy="disabled",
        deep_research_adaptive=False,
    )

    async def mock_research(self: Any, query: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "query": query,
            "context": "research context",
            "sources": [{"url": "https://example.com/1", "title": "src1", "snippet": "..."}],
            "learnings": [],
            "citations": {},
            "children": [],
        }

    with patch(
        "src.skills.researcher.deep_research.DeepResearcher.research",
        mock_research,
    ):
        result = await deep_research_node(base_state, settings=settings)

    # 返回结构验证
    assert "sub_queries" in result
    assert "contexts" in result
    assert "sources" in result
    assert "visited_urls" in result
    # DeepResearch 内部递归, sub_queries 为空
    assert result["sub_queries"] == []
    # contexts 含研究上下文
    assert result["contexts"] == ["research context"]
    # sources 透传
    assert len(result["sources"]) == 1
    assert result["sources"][0]["url"] == "https://example.com/1"
    # visited_urls 为列表 (初始为空)
    assert isinstance(result["visited_urls"], list)
