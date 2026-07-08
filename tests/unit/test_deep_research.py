"""单元测试: DeepResearcher 递归深度研究器.

验证 src/skills/researcher/deep_research.py:
- research(): breadth x depth 递归树探索, 每层聚合上下文
- _assess_complexity(): 自适应复杂度评估 (LLM 返回 1-5 映射 breadth/depth)
- _research_sub_query(): 搜索 + 抓取 + 压缩 (含 _visited_urls 跨子查询去重)

AGENTS.md 第 13 章: 单元测试不依赖外部服务
(LLM/Searchers/Scrapers/QuotaCache/ContextManager 全部 mock).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.llm.client import LLMResponse
from src.skills.researcher.deep_research import DeepResearcher

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


# ========== research() 递归探索 ==========


@pytest.mark.asyncio
async def test_deep_researcher_recursive_exploration(
    researcher: DeepResearcher,
) -> None:
    """测试 breadth=2, depth=2 递归探索.

    递归树 (breadth 每层减半, next_breadth = max(1, breadth // 2)):
    - depth 0 (breadth=2): 生成 2 子查询, _research_sub_query 调用 2 次
    - depth 1 (breadth=1): 仅第一个子查询递归, _research_sub_query 调用 1 次
    - depth 2: 终止 (leaf, _current_depth >= depth)

    总计 _research_sub_query 调用 3 次, children 非空, sources 聚合正确.
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
        }

    async def mock_generate_sub_queries(query: str, breadth: int, **kwargs: Any) -> list[str]:
        return [f"{query}-sub{i}" for i in range(breadth)]

    researcher._research_sub_query = mock_research_sub_query  # type: ignore[method-assign]
    researcher._generate_sub_queries = mock_generate_sub_queries  # type: ignore[method-assign]

    result = await researcher.research("test query", breadth=2, depth=2)

    # 验证返回结构
    assert "query" in result
    assert "context" in result
    assert "sources" in result
    assert "children" in result

    # 验证递归发生 (children 非空)
    assert len(result["children"]) > 0

    # 验证 _research_sub_query 调用次数:
    # depth 0: breadth=2 -> 2 次
    # depth 1: breadth=1 (next_breadth=max(1,2//2)=1), 仅 1 个子查询递归 -> 1 次
    # depth 2: 终止, 0 次
    # 总计: 3 次
    assert call_count == 3

    # 验证 sources 聚合: 根节点仅聚合当前层 (depth 0) 的 sources (2 个),
    # depth 1 的 sources 在 children[0]["sources"] 中.
    assert len(result["sources"]) == 2

    # 验证 children 含 depth 1 的 sources (1 个)
    assert len(result["children"]) == 1
    assert len(result["children"][0]["sources"]) == 1

    # 验证 context 聚合 (含所有子查询的上下文)
    assert "ctx-test query-sub0" in result["context"]
    assert "ctx-test query-sub1" in result["context"]


@pytest.mark.asyncio
async def test_deep_researcher_visited_urls_dedup(
    researcher: DeepResearcher,
    mock_context_manager: MagicMock,
) -> None:
    """测试相同 URL 在不同子查询中只被访问一次.

    _visited_urls 集合跨子查询去重: 第一次出现的 URL 被加入集合,
    后续子查询中相同 URL 被跳过 (不进入 scrape_urls).
    """
    shared_url = "https://example.com/shared"
    unique_url_1 = "https://example.com/unique1"
    unique_url_2 = "https://example.com/unique2"

    # 搜索引擎: 第一次返回 shared + unique1, 第二次返回 shared + unique2
    mock_searcher = MagicMock()
    mock_searcher.name = "test_searcher"
    mock_searcher.search = AsyncMock(
        side_effect=[
            [
                {"url": shared_url, "title": "shared", "snippet": "..."},
                {"url": unique_url_1, "title": "unique1", "snippet": "..."},
            ],
            [
                {"url": shared_url, "title": "shared", "snippet": "..."},
                {"url": unique_url_2, "title": "unique2", "snippet": "..."},
            ],
        ]
    )

    # scrape_urls: 返回与传入 urls 对应的 docs
    def scrape_side_effect(urls: list[str], **kwargs: Any) -> Any:
        return [{"url": u, "content": f"content-{u}"} for u in urls]

    # quota_cache 模块依赖 redis (单元测试环境未安装), 通过 sys.modules 注入 mock 模块,
    # 使 _research_sub_query 内的 `from ...quota_cache import QuotaCache` 获取 mock.
    mock_quota_cache_module = MagicMock()
    mock_quota_cache_module.QuotaCache = MagicMock(return_value=MagicMock())

    with (
        patch.dict(
            "sys.modules",
            {"src.skills.researcher.searchers.quota_cache": mock_quota_cache_module},
        ),
        patch(
            "src.skills.researcher.deep_research.detect_region",
            return_value="auto",
        ),
        patch(
            "src.skills.researcher.searchers.get_searchers_async",
            new_callable=AsyncMock,
            return_value=[mock_searcher],
        ),
        patch(
            "src.skills.researcher.deep_research.scrape_urls",
            new_callable=AsyncMock,
            side_effect=scrape_side_effect,
        ),
    ):
        # 第一次子查询: shared + unique1 都被访问
        result1 = await researcher._research_sub_query("query1")
        # 第二次子查询: shared 已在 _visited_urls, 仅 unique2 被访问
        result2 = await researcher._research_sub_query("query2")

    # 第一次: 两个 URL 都被加入 sources
    result1_urls = [s["url"] for s in result1["sources"]]
    assert shared_url in result1_urls
    assert unique_url_1 in result1_urls

    # 第二次: shared_url 被跳过, 仅 unique2
    result2_urls = [s["url"] for s in result2["sources"]]
    assert shared_url not in result2_urls
    assert unique_url_2 in result2_urls
    assert len(result2["sources"]) == 1

    # _visited_urls 总计 3 个 (shared + unique1 + unique2)
    assert len(researcher._visited_urls) == 3
    assert shared_url in researcher._visited_urls
    assert unique_url_1 in researcher._visited_urls
    assert unique_url_2 in researcher._visited_urls


# ========== _assess_complexity() 自适应复杂度 ==========


@pytest.mark.asyncio
async def test_deep_researcher_adaptive_complexity(
    researcher: DeepResearcher,
    mock_llm: MagicMock,
    settings: Settings,
) -> None:
    """测试 _assess_complexity 按复杂度 (1-5) 返回自适应参数.

    映射表 (硬约束):
    - complexity 1-2 (简单): breadth=2, depth=1, concurrency=2
    - complexity 3   (中等): breadth=3, depth=2, concurrency=4
    - complexity 4-5 (复杂): breadth=4, depth=3, concurrency=6

    LLM 失败时返回 settings 中的默认配置.
    """
    # 简单查询 (complexity 2)
    mock_llm.achat.return_value = LLMResponse(
        content='{"complexity": 2, "reason": "单一事实查询"}',
        model="test",
    )
    params = await researcher._assess_complexity("什么是 RAG")
    assert params == {"breadth": 2, "depth": 1, "concurrency": 2}

    # 中等查询 (complexity 3)
    mock_llm.achat.return_value = LLMResponse(
        content='{"complexity": 3, "reason": "多维度分析"}',
        model="test",
    )
    params = await researcher._assess_complexity("对比 React 和 Vue 的优缺点")
    assert params == {"breadth": 3, "depth": 2, "concurrency": 4}

    # 复杂查询 (complexity 5)
    mock_llm.achat.return_value = LLMResponse(
        content='{"complexity": 5, "reason": "综合性深度研究"}',
        model="test",
    )
    params = await researcher._assess_complexity("分析 2026 年 AI Agent 行业趋势")
    assert params == {"breadth": 4, "depth": 3, "concurrency": 6}

    # LLM 失败时降级默认值
    mock_llm.achat.side_effect = Exception("LLM 不可用")
    params = await researcher._assess_complexity("任意查询")
    assert params == {
        "breadth": settings.deep_research_breadth,
        "depth": settings.deep_research_depth,
        "concurrency": settings.deep_research_concurrency,
    }


# ========== depth=0 递归终止 ==========


@pytest.mark.asyncio
async def test_deep_researcher_depth_0_returns_empty(
    researcher: DeepResearcher,
) -> None:
    """测试递归终止条件: _current_depth >= depth 时返回空 sources 和 children.

    注: depth=0 因 `depth or settings.deep_research_depth` 的 falsy 语义
    会回退到默认值 (0 为 falsy), 故通过 _current_depth=depth 触发终止.
    终止时返回 {query, context=_parent_context, sources=[], children=[]}.
    """
    # _current_depth(1) >= depth(1) -> 终止
    result = await researcher.research("query", depth=1, _current_depth=1)

    assert result["sources"] == []
    assert result["children"] == []
    assert result["query"] == "query"
    # _parent_context 默认为空字符串
    assert result["context"] == ""

    # 验证带 _parent_context 时终止返回该上下文
    result_with_ctx = await researcher.research(
        "query",
        depth=1,
        _current_depth=1,
        _parent_context="parent context",
    )
    assert result_with_ctx["context"] == "parent context"
    assert result_with_ctx["sources"] == []
    assert result_with_ctx["children"] == []


# ========== 子查询失败容错 ==========


@pytest.mark.asyncio
async def test_deep_researcher_handles_sub_query_failure(
    researcher: DeepResearcher,
) -> None:
    """测试单个子查询失败时, 其他子查询仍正常返回.

    _research_sub_query 内部 catch 所有异常返回 {context:"", sources:[]},
    asyncio.gather 聚合时其他成功子查询的上下文不受影响.
    """
    call_count = 0

    async def mock_research_sub_query(sq: str, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if sq == "fail-query":
            # 模拟失败: 返回空结果 (与 _research_sub_query 的 except 行为一致)
            return {"context": "", "sources": []}
        return {
            "context": f"ctx-{sq}",
            "sources": [{"url": f"https://example.com/{sq}", "title": sq, "snippet": "..."}],
        }

    async def mock_generate_sub_queries(query: str, breadth: int, **kwargs: Any) -> list[str]:
        return ["good-query", "fail-query"]

    researcher._research_sub_query = mock_research_sub_query  # type: ignore[method-assign]
    researcher._generate_sub_queries = mock_generate_sub_queries  # type: ignore[method-assign]

    result = await researcher.research("test", breadth=2, depth=1)

    # 失败子查询上下文为空, 但成功子查询上下文仍被聚合
    assert "ctx-good-query" in result["context"]
    # "fail-query" 上下文为空, 不应出现在聚合结果中
    assert "ctx-fail-query" not in result["context"]

    # sources 仅含成功子查询的来源 (fail-query 的 sources 为空)
    source_titles = [s["title"] for s in result["sources"]]
    assert "good-query" in source_titles
    assert "fail-query" not in source_titles

    # 递归仍正常进行 (children 非空, 第一个子查询 "good-query" 递归到 depth 1)
    assert len(result["children"]) > 0

    # _research_sub_query 被调用 2 次 (depth 0 的两个子查询)
    # depth 1 终止, 不调用 _research_sub_query
    assert call_count == 2
