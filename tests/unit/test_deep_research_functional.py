"""功能测试: DeepResearcher GPTR 递归树探索 (mock 版).

验证 GPTR 深度研究核心功能 (对标 GPTR DeepResearchSkill):
- breadth=4/depth=2 → 12 子查询 (递归树规模验证)
- learnings 被正确提取 (mock _process_research_results)
- citation 标注出现在 context 中
- followUpQuestions 驱动递归查询

mock 版功能测试放在 tests/unit/ 下 (不依赖容器栈),
遵循 conftest.py 的跳过逻辑 (mark=unit 不被跳过).
测试数据隔离: user_id=test_gptr_func_*, session_id=test_deep_research_func_*.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings
from src.skills.researcher.deep_research import DeepResearcher

pytestmark = pytest.mark.unit


@pytest.fixture()
def settings() -> Settings:
    """构造最小 Settings (跳过 .env 加载, mcp_strategy=disabled)."""
    return Settings(_env_file=None, mcp_strategy="disabled")


@pytest.fixture()
def mock_llm() -> MagicMock:
    """Mock LLMClient."""
    llm = MagicMock()
    llm.achat = AsyncMock()
    return llm


@pytest.fixture()
def mock_context_manager() -> MagicMock:
    """Mock ContextManager."""
    cm = MagicMock()
    cm.get_similar_content = AsyncMock(return_value="compressed context")
    return cm


@pytest.fixture()
def researcher(
    settings: Settings,
    mock_llm: MagicMock,
    mock_context_manager: MagicMock,
) -> DeepResearcher:
    """构造 DeepResearcher (依赖全部 mock)."""
    return DeepResearcher(
        settings=settings,
        llm=mock_llm,
        context_manager=mock_context_manager,
    )


# ========== 功能 11: breadth=4/depth=2 → 12 子查询 (对标 GPTR) ==========


@pytest.mark.asyncio
async def test_breadth_4_depth_2_generates_12_sub_queries(
    researcher: DeepResearcher,
) -> None:
    """验证 breadth=4/depth=2 递归树生成 12 个子查询 (对标 GPTR 默认).

    递归树:
    - depth 0 (breadth=4): 4 子查询
    - depth 1 (next_breadth=max(2, 4//2)=2): 4 个递归调用 × 2 = 8 子查询
    - 总计: 4 + 8 = 12 子查询

    验证 _research_sub_query 被调用 12 次, children 数 4.
    """
    call_count = 0

    async def mock_research_sub_query(sq: str, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        return {
            "context": f"ctx-{sq}",
            "sources": [
                {"url": f"https://example.com/{call_count}", "title": sq, "snippet": "..."}
            ],
            "learnings": [],
            "followUpQuestions": [],
            "citations": {},
        }

    async def mock_generate_sub_queries(
        query: str, breadth: int, **kwargs: Any
    ) -> list[dict[str, str]]:
        return [{"query": f"{query}-sub{i}", "researchGoal": f"goal-{i}"} for i in range(breadth)]

    researcher._research_sub_query = mock_research_sub_query  # type: ignore[method-assign]
    researcher._generate_sub_queries = mock_generate_sub_queries  # type: ignore[method-assign]

    result = await researcher.research("GPTR test", breadth=4, depth=2)

    # 总调用次数: 4 (depth 0) + 8 (depth 1) = 12
    assert call_count == 12, f"期望 12 次 _research_sub_query 调用, 实际 {call_count}"

    # children 数: 4 (depth 0 的 4 个 result 都有 context, 都递归)
    assert len(result["children"]) == 4

    # 每个 child 含 depth 1 的 2 个 sources
    for child in result["children"]:
        assert len(child["sources"]) == 2

    # 根节点 sources: depth 0 的 4 个
    assert len(result["sources"]) == 4


# ========== learnings 被正确提取 ==========


@pytest.mark.asyncio
async def test_learnings_extracted_correctly(researcher: DeepResearcher) -> None:
    """验证 learnings 从子查询结果中被正确提取并累积.

    mock _research_sub_query 返回含 learnings 的结果,
    验证 research() 返回的 learnings 包含所有子查询的 learnings.
    """

    async def mock_research_sub_query(sq: str, **kwargs: Any) -> dict[str, Any]:
        # 每个子查询返回不同的 learning
        idx = int(sq[-1]) if sq[-1].isdigit() else 0
        return {
            "context": f"ctx-{sq}",
            "sources": [],
            "learnings": [f"learning-{idx}"],
            "followUpQuestions": [],
            "citations": {f"learning-{idx}": f"https://example.com/{idx}"},
        }

    async def mock_generate_sub_queries(
        query: str, breadth: int, **kwargs: Any
    ) -> list[dict[str, str]]:
        return [{"query": f"q{i}", "researchGoal": f"g{i}"} for i in range(breadth)]

    researcher._research_sub_query = mock_research_sub_query  # type: ignore[method-assign]
    researcher._generate_sub_queries = mock_generate_sub_queries  # type: ignore[method-assign]

    result = await researcher.research("learnings test", breadth=3, depth=1)

    # 3 个子查询, 每个返回 1 个不同的 learning
    assert len(result["learnings"]) == 3
    assert "learning-0" in result["learnings"]
    assert "learning-1" in result["learnings"]
    assert "learning-2" in result["learnings"]

    # citations 累积: 3 个 entry
    assert len(result["citations"]) == 3
    assert result["citations"]["learning-0"] == "https://example.com/0"
    assert result["citations"]["learning-1"] == "https://example.com/1"
    assert result["citations"]["learning-2"] == "https://example.com/2"


# ========== citation 标注出现在 context 中 ==========


@pytest.mark.asyncio
async def test_citation_annotation_in_context(researcher: DeepResearcher) -> None:
    """验证 citation 标注出现在聚合 context 中 (功能 7).

    有 citation 的 learning 在 context 中追加 "[Source: url]" 标注.
    """

    async def mock_research_sub_query(sq: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "context": f"ctx-{sq}",
            "sources": [],
            "learnings": ["重要发现"],
            "followUpQuestions": [],
            "citations": {"重要发现": "https://example.com/source"},
        }

    async def mock_generate_sub_queries(
        query: str, breadth: int, **kwargs: Any
    ) -> list[dict[str, str]]:
        return [{"query": f"q{i}", "researchGoal": f"g{i}"} for i in range(breadth)]

    researcher._research_sub_query = mock_research_sub_query  # type: ignore[method-assign]
    researcher._generate_sub_queries = mock_generate_sub_queries  # type: ignore[method-assign]

    result = await researcher.research("citation test", breadth=2, depth=1)

    # citation 标注出现在 context 中
    assert "重要发现 [Source: https://example.com/source]" in result["context"]


# ========== followUpQuestions 驱动递归查询 (功能 3) ==========


@pytest.mark.asyncio
async def test_follow_up_questions_drive_recursion(researcher: DeepResearcher) -> None:
    """验证 followUpQuestions 驱动递归查询构建 (功能 3).

    _build_next_query 由 researchGoal + followUpQuestions 拼接,
    递归调用时传入的 query 包含 followUpQuestions 内容.
    """
    captured_queries: list[str] = []

    async def mock_research_sub_query(sq: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "context": f"ctx-{sq}",
            "sources": [],
            "learnings": [],
            "followUpQuestions": ["深入问题1?", "深入问题2?"],
            "citations": {},
        }

    async def mock_generate_sub_queries(
        query: str, breadth: int, **kwargs: Any
    ) -> list[dict[str, str]]:
        captured_queries.append(query)
        return [{"query": f"{query}-sub{i}", "researchGoal": f"goal-{i}"} for i in range(breadth)]

    researcher._research_sub_query = mock_research_sub_query  # type: ignore[method-assign]
    researcher._generate_sub_queries = mock_generate_sub_queries  # type: ignore[method-assign]

    # depth=2 触发递归
    result = await researcher.research("root query", breadth=2, depth=2)

    # 递归发生 (children 非空)
    assert len(result["children"]) == 2

    # depth 1 的 _generate_sub_queries 调用传入的 query 应包含 followUpQuestions
    # captured_queries[0] 是 depth 0 的根 query
    # captured_queries[1] 和 [2] 是 depth 1 的递归 query (由 _build_next_query 生成)
    assert captured_queries[0] == "root query"
    # 递归 query 应包含 "Follow-up questions" (由 _build_next_query 拼接)
    assert any("Follow-up questions" in q for q in captured_queries[1:])
    assert any("深入问题1?" in q for q in captured_queries[1:])


# ========== sources 跨层聚合 ==========


@pytest.mark.asyncio
async def test_sources_aggregation_across_layers(researcher: DeepResearcher) -> None:
    """验证 sources 聚合: 根节点仅含 depth 0 的 sources, children 含 depth 1 的 sources.

    GPTR 对标: 每层 sources 独立, 不跨层聚合 (避免来源重复).
    """
    call_count = 0

    async def mock_research_sub_query(sq: str, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        return {
            "context": f"ctx-{sq}",
            "sources": [
                {
                    "url": f"https://example.com/{call_count}",
                    "title": f"src-{call_count}",
                    "snippet": "...",
                }
            ],
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

    result = await researcher.research("sources test", breadth=3, depth=2)

    # 根节点 sources: depth 0 的 3 个 (call_count 1-3)
    assert len(result["sources"]) == 3
    root_src_titles = [s["title"] for s in result["sources"]]
    assert "src-1" in root_src_titles
    assert "src-2" in root_src_titles
    assert "src-3" in root_src_titles

    # children: 3 个, 每个含 depth 1 的 2 个 sources
    assert len(result["children"]) == 3
    for child in result["children"]:
        assert len(child["sources"]) == 2

    # 根节点 sources 不应包含 depth 1 的 sources
    assert "src-4" not in root_src_titles
    assert "src-5" not in root_src_titles


# ========== 空结果不触发递归 (功能 1) ==========


@pytest.mark.asyncio
async def test_empty_results_no_recursion(researcher: DeepResearcher) -> None:
    """验证空结果 (context="") 不触发递归 (功能 1: 仅对有 context 的 result 递归).

    所有子查询返回空 context 时, children 为空, 不递归.
    """

    async def mock_research_sub_query(sq: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "context": "",  # 空上下文
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

    result = await researcher.research("empty test", breadth=3, depth=2)

    # 所有子查询返回空 context, 不触发递归
    assert result["children"] == []
    # context 也为空 (无子查询上下文, 无 learnings)
    assert result["context"] == ""
