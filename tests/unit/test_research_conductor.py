"""单元测试: ResearchConductor 研究总指挥.

验证 src/skills/researcher/research_conductor.py:
- plan_research(): Planner 拆解子查询 (LLM JSON 解析 + 三级容错)
- _process_sub_query(): 搜索 -> 抓取 -> 压缩流程
- conduct_research(): 聚合多个子查询的上下文

AGENTS.md 第 13 章: 单元测试不依赖外部服务
(LLM/Searchers/Scrapers/ContextManager 全部 mock).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.llm.client import LLMResponse
from src.skills.researcher.research_conductor import ResearchConductor

pytestmark = pytest.mark.unit


@pytest.fixture()
def settings() -> Settings:
    """构造最小 Settings (跳过 .env 加载, mcp_strategy=disabled 避免 MCP mock)."""
    return Settings(_env_file=None, mcp_strategy="disabled")


@pytest.fixture()
def mock_llm() -> MagicMock:
    """Mock LLMClient (achat 为 AsyncMock)."""
    llm = MagicMock()
    llm.achat = AsyncMock()
    return llm


@pytest.fixture()
def mock_context_manager() -> MagicMock:
    """Mock ContextManager (get_similar_content 为 AsyncMock)."""
    cm = MagicMock()
    cm.get_similar_content = AsyncMock(return_value="compressed context")
    return cm


@pytest.fixture()
def mock_prompt_family() -> MagicMock:
    """Mock PromptFamily (planner_prompt / curator_prompt 返回字符串)."""
    pf = MagicMock()
    pf.planner_prompt.return_value = "test planner prompt"
    pf.curator_prompt.return_value = "test curator prompt"
    return pf


@pytest.fixture()
def conductor(
    settings: Settings,
    mock_llm: MagicMock,
    mock_context_manager: MagicMock,
    mock_prompt_family: MagicMock,
) -> ResearchConductor:
    """构造 ResearchConductor (依赖全部 mock)."""
    return ResearchConductor(
        settings=settings,
        llm=mock_llm,
        context_manager=mock_context_manager,
        prompt_family=mock_prompt_family,
    )


# ========== plan_research() ==========


@pytest.mark.asyncio
async def test_plan_research_returns_sub_queries(
    conductor: ResearchConductor,
    mock_llm: MagicMock,
) -> None:
    """测试 LLM 返回 JSON 数组时, plan_research 返回子查询列表.

    plan_research 用 strategic tier 调用 LLM, 解析 JSON 数组,
    截断到 max_iterations, 返回字符串列表.
    """
    mock_llm.achat.return_value = LLMResponse(
        content='["子查询1", "子查询2", "子查询3"]',
        model="test",
    )

    sub_queries = await conductor.plan_research("分析新能源汽车市场")

    assert isinstance(sub_queries, list)
    assert len(sub_queries) == 3
    assert sub_queries[0] == "子查询1"
    assert sub_queries[1] == "子查询2"
    assert sub_queries[2] == "子查询3"
    # 验证 LLM 调用参数
    mock_llm.achat.assert_awaited_once()


@pytest.mark.asyncio
async def test_plan_research_handles_llm_error(
    conductor: ResearchConductor,
    mock_llm: MagicMock,
) -> None:
    """测试 LLM 返回无效 JSON 时, 降级返回原始查询.

    plan_research 三级容错:
    1. safe_json_parse 解析失败 -> fallback=[]
    2. 空列表 / 非列表 -> 跳过 return
    3. 最终降级: return [原始 query]
    """
    # 场景 1: LLM 返回非 JSON 内容
    mock_llm.achat.return_value = LLMResponse(
        content="抱歉, 我无法处理这个请求.",
        model="test",
    )
    sub_queries = await conductor.plan_research("test query")
    assert sub_queries == ["test query"]

    # 场景 2: LLM 返回空 JSON 数组
    mock_llm.achat.return_value = LLMResponse(
        content="[]",
        model="test",
    )
    sub_queries = await conductor.plan_research("test query")
    assert sub_queries == ["test query"]

    # 场景 3: LLM 返回非列表 JSON (dict)
    mock_llm.achat.return_value = LLMResponse(
        content='{"error": "invalid"}',
        model="test",
    )
    sub_queries = await conductor.plan_research("test query")
    assert sub_queries == ["test query"]


# ========== _process_sub_query() ==========


@pytest.mark.asyncio
@patch("src.skills.researcher.research_conductor.detect_region")
@patch("src.skills.researcher.research_conductor.get_searchers")
@patch("src.skills.researcher.research_conductor.scrape_urls", new_callable=AsyncMock)
async def test_process_sub_query_searches_and_scrapes(
    mock_scrape_urls: AsyncMock,
    mock_get_searchers: MagicMock,
    mock_detect_region: MagicMock,
    conductor: ResearchConductor,
    mock_context_manager: MagicMock,
) -> None:
    """测试 _process_sub_query 执行 搜索 -> 抓取 -> 压缩 流程.

    验证:
    1. detect_region 被调用 (区域检测)
    2. get_searchers 被调用 (获取搜索引擎)
    3. searcher.search 被调用 (执行搜索)
    4. scrape_urls 被调用 (抓取 URL 内容)
    5. context_manager.get_similar_content 被调用 (压缩 + 去重)
    6. 返回 {context, sources, urls} 结构
    """
    # 模拟搜索引擎
    mock_searcher = MagicMock()
    mock_searcher.search = AsyncMock(
        return_value=[
            {
                "url": "https://example.com/1",
                "title": "result1",
                "snippet": "snippet1",
            },
            {
                "url": "https://example.com/2",
                "title": "result2",
                "snippet": "snippet2",
            },
        ]
    )
    mock_get_searchers.return_value = [mock_searcher]
    mock_detect_region.return_value = "auto"

    # 模拟抓取结果
    mock_scrape_urls.return_value = [
        {"url": "https://example.com/1", "content": "scraped content 1", "title": "result1"},
        {"url": "https://example.com/2", "content": "scraped content 2", "title": "result2"},
    ]

    # 模拟压缩结果
    mock_context_manager.get_similar_content.return_value = "compressed context"

    result = await conductor._process_sub_query("test query")

    # 验证搜索流程
    mock_detect_region.assert_called_once_with("test query")
    mock_get_searchers.assert_called_once()
    mock_searcher.search.assert_awaited_once()

    # 验证抓取流程
    mock_scrape_urls.assert_awaited_once()

    # 验证压缩流程
    mock_context_manager.get_similar_content.assert_awaited_once()

    # 验证返回结构
    assert "context" in result
    assert "sources" in result
    assert "urls" in result
    assert result["context"] == "compressed context"
    assert len(result["sources"]) == 2
    assert "https://example.com/1" in result["urls"]
    assert "https://example.com/2" in result["urls"]


@pytest.mark.asyncio
@patch("src.skills.researcher.research_conductor.detect_region")
@patch("src.skills.researcher.research_conductor.get_searchers")
async def test_process_sub_query_handles_search_failure(
    mock_get_searchers: MagicMock,
    mock_detect_region: MagicMock,
    conductor: ResearchConductor,
    mock_context_manager: MagicMock,
) -> None:
    """测试所有搜索引擎失败时, 返回空上下文.

    asyncio.gather(return_exceptions=True) 捕获异常, all_results 为空,
    _process_sub_query 提前返回 {context:"", sources:[], urls:set()}.
    """
    # 模拟搜索引擎抛异常
    mock_searcher = MagicMock()
    mock_searcher.search = AsyncMock(side_effect=Exception("search engine down"))
    mock_get_searchers.return_value = [mock_searcher]
    mock_detect_region.return_value = "auto"

    result = await conductor._process_sub_query("test query")

    # 验证返回空结果
    assert result["context"] == ""
    assert result["sources"] == []
    assert result["urls"] == set()

    # 压缩不应被调用 (提前返回)
    mock_context_manager.get_similar_content.assert_not_awaited()


# ========== conduct_research() 聚合 ==========


@pytest.mark.asyncio
async def test_aggregates_context_from_sub_queries(
    conductor: ResearchConductor,
) -> None:
    """测试 conduct_research 聚合多个子查询的上下文.

    conduct_research 流程:
    1. plan_research 拆解子查询
    2. 追加原始 query (若不在列表中)
    3. asyncio.gather 并行 _process_sub_query
    4. 聚合 contexts / sources / visited_urls
    """
    # 模拟 plan_research 返回 2 个子查询
    conductor.plan_research = AsyncMock(  # type: ignore[method-assign]
        return_value=["sub1", "sub2"]
    )

    # 模拟 _process_sub_query 返回不同上下文
    async def mock_process(sq: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "context": f"context-for-{sq}",
            "sources": [{"url": f"https://example.com/{sq}", "title": sq}],
            "urls": {f"https://example.com/{sq}"},
        }

    conductor._process_sub_query = mock_process  # type: ignore[method-assign]

    result = await conductor.conduct_research("test query")

    # plan_research 返回 ["sub1", "sub2"], 追加原始 query 后为 ["sub1", "sub2", "test query"]
    assert "sub1" in result["sub_queries"]
    assert "sub2" in result["sub_queries"]
    assert "test query" in result["sub_queries"]
    assert len(result["sub_queries"]) == 3

    # 上下文聚合 (3 个子查询各产生 1 个 context)
    assert len(result["contexts"]) == 3
    assert "context-for-sub1" in result["contexts"]
    assert "context-for-sub2" in result["contexts"]
    assert "context-for-test query" in result["contexts"]

    # sources 聚合
    assert len(result["sources"]) == 3

    # visited_urls 聚合
    assert "https://example.com/sub1" in result["visited_urls"]
    assert "https://example.com/sub2" in result["visited_urls"]
    assert "https://example.com/test query" in result["visited_urls"]


@pytest.mark.asyncio
async def test_research_conductor_respects_max_sub_queries(
    conductor: ResearchConductor,
    mock_llm: MagicMock,
    settings: Settings,
) -> None:
    """测试 plan_research 截断超过 max_iterations 的子查询.

    plan_research 解析 LLM 返回的 JSON 数组后,
    执行 [:max_iterations] 截断 (默认 max_iterations=3).
    """
    # LLM 返回 5 个子查询, 超过 max_iterations (默认 3)
    mock_llm.achat.return_value = LLMResponse(
        content='["q1", "q2", "q3", "q4", "q5"]',
        model="test",
    )

    sub_queries = await conductor.plan_research("test query")

    # 截断到 max_iterations
    assert len(sub_queries) == settings.max_iterations
    assert sub_queries == ["q1", "q2", "q3"]
    # q4, q5 被截断
    assert "q4" not in sub_queries
    assert "q5" not in sub_queries
