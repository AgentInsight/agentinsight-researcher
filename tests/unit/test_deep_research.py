"""单元测试: DeepResearcher 递归深度研究器.

验证 src/skills/researcher/deep_research.py:
- research(): breadth x depth 递归树探索, 每层聚合上下文 (对标 GPTR: 对每个 result 递归)
- _assess_complexity(): 自适应复杂度评估 (LLM 返回 1-5 映射 breadth/depth)
- _research_sub_query(): 搜索 + 抓取 + 压缩 + learnings 提取 (含 _visited_urls 跨子查询去重)

单元测试不依赖外部服务
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


# ========== research() 递归探索 (对标 GPTR: 对每个 result 递归) ==========


@pytest.mark.asyncio
async def test_deep_researcher_recursive_exploration(
    researcher: DeepResearcher,
) -> None:
    """测试 breadth=2, depth=2 递归探索 (对标 GPTR: 对每个 result 递归).

    递归树 (功能 1+2: 对每个 result 递归, next_breadth=max(2, breadth//2)):
    - depth 0 (breadth=2): 生成 2 子查询, _research_sub_query 调用 2 次
      两个 result 都有 context → 2 个递归调用
    - depth 1 (next_breadth=max(2, 2//2)=2): 每个递归调用生成 2 子查询,
      _research_sub_query 调用 2 次 × 2 = 4 次
    - depth 2: 终止 (depth - _current_depth > 1 为 False)

    总计 _research_sub_query 调用 6 次 (2 + 4), children 数 2, sources 聚合正确.
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
            # 功能 6: 返回值含 learnings/followUpQuestions/citations
            "learnings": [],
            "followUpQuestions": [],
            "citations": {},
        }

    async def mock_generate_sub_queries(
        query: str, breadth: int, **kwargs: Any
    ) -> list[dict[str, str]]:
        # 功能: 返回 list[dict[str, str]] (含 query + researchGoal)
        return [{"query": f"{query}-sub{i}", "researchGoal": f"goal-{i}"} for i in range(breadth)]

    researcher._research_sub_query = mock_research_sub_query  # type: ignore[method-assign]
    researcher._generate_sub_queries = mock_generate_sub_queries  # type: ignore[method-assign]

    result = await researcher.research("test query", breadth=2, depth=2)

    # 验证返回结构 (含 learnings/citations, 功能 6/9)
    assert "query" in result
    assert "context" in result
    assert "sources" in result
    assert "children" in result
    assert "learnings" in result
    assert "citations" in result

    # 验证递归发生 (children 非空, 对每个 result 递归)
    assert len(result["children"]) == 2

    # 验证 _research_sub_query 调用次数:
    # depth 0: breadth=2 → 2 次
    # depth 1: 2 个递归调用 × next_breadth=max(2, 2//2)=2 → 4 次
    # 总计: 6 次
    assert call_count == 6

    # 验证 sources 聚合: 根节点仅聚合当前层 (depth 0) 的 sources (2 个)
    assert len(result["sources"]) == 2

    # 验证 children 含 depth 1 的 sources (每个 child 2 个)
    assert len(result["children"][0]["sources"]) == 2
    assert len(result["children"][1]["sources"]) == 2

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

    # Mock _process_research_results 避免触发 LLM 调用 (功能 6 新增的 LLM 提取)
    async def mock_process_research_results(
        query: str, context: str, **kwargs: Any
    ) -> dict[str, Any]:
        return {"learnings": [], "followUpQuestions": [], "citations": {}}

    researcher._process_research_results = mock_process_research_results  # type: ignore[method-assign]

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


# ========== _assess_complexity() 自适应复杂度 (对标 GPTR breadth=4 默认) ==========


@pytest.mark.asyncio
async def test_deep_researcher_adaptive_complexity(
    researcher: DeepResearcher,
    mock_llm: MagicMock,
    settings: Settings,
) -> None:
    """测试 _assess_complexity 按复杂度 (1-5) 返回自适应参数.

    映射表 (功能 11 + 自适应深度, 对标 GPTR breadth=4 默认):
    - complexity 1-2 (简单): breadth=4, depth=1, concurrency=4 (depth=1 安全网)
    - complexity 3   (中等): breadth=4, depth=2, concurrency=4 (4+8=12 子查询)
    - complexity 4-5 (复杂): breadth=4, depth=3, concurrency=6 (4+8+16=28 子查询)

    LLM 失败时返回 depth=1 安全网 (避免 LLM 失败时触发深度递归).
    """
    # 简单查询 (complexity 2)
    mock_llm.achat.return_value = LLMResponse(
        content='{"complexity": 2, "reason": "单一事实查询"}',
        model="test",
    )
    params = await researcher._assess_complexity("什么是 RAG")
    assert params == {"breadth": 4, "depth": 1, "concurrency": 4}

    # 中等查询 (complexity 3)
    mock_llm.achat.return_value = LLMResponse(
        content='{"complexity": 3, "reason": "多维度分析"}',
        model="test",
    )
    params = await researcher._assess_complexity("对比 React 和 Vue 的优缺点")
    assert params == {"breadth": 4, "depth": 2, "concurrency": 4}

    # 复杂查询 (complexity 5)
    mock_llm.achat.return_value = LLMResponse(
        content='{"complexity": 5, "reason": "综合性深度研究"}',
        model="test",
    )
    params = await researcher._assess_complexity("分析 2026 年 AI Agent 行业趋势")
    assert params == {"breadth": 4, "depth": 3, "concurrency": 6}

    # LLM 失败时降级默认值 (depth=1 安全网)
    mock_llm.achat.side_effect = Exception("LLM 不可用")
    params = await researcher._assess_complexity("任意查询")
    assert params == {
        "breadth": settings.deep_research_breadth,
        "depth": 1,  # 安全网: LLM 失败时不递归
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
    终止时返回 {query, context=_parent_context, sources=[], children=[], learnings=[], citations={}}.
    """
    # _current_depth(1) >= depth(1) -> 终止
    result = await researcher.research("query", depth=1, _current_depth=1)

    assert result["sources"] == []
    assert result["children"] == []
    assert result["query"] == "query"
    # _parent_context 默认为空字符串
    assert result["context"] == ""
    # 功能 6/9: 终止返回值含 learnings/citations 空结构
    assert result["learnings"] == []
    assert result["citations"] == {}

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

    功能 1: 递归仅对有 context 的 result 执行, 失败子查询 (context="") 不递归.
    """
    call_count = 0

    async def mock_research_sub_query(sq: str, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if sq == "fail-query":
            # 模拟失败: 返回空结果 (与 _research_sub_query 的 except 行为一致)
            return {
                "context": "",
                "sources": [],
                "learnings": [],
                "followUpQuestions": [],
                "citations": {},
            }
        return {
            "context": f"ctx-{sq}",
            "sources": [{"url": f"https://example.com/{sq}", "title": sq, "snippet": "..."}],
            "learnings": [],
            "followUpQuestions": [],
            "citations": {},
        }

    async def mock_generate_sub_queries(
        query: str, breadth: int, **kwargs: Any
    ) -> list[dict[str, str]]:
        return [
            {"query": "good-query", "researchGoal": "goal-good"},
            {"query": "fail-query", "researchGoal": "goal-fail"},
        ]

    researcher._research_sub_query = mock_research_sub_query  # type: ignore[method-assign]
    researcher._generate_sub_queries = mock_generate_sub_queries  # type: ignore[method-assign]

    # depth=2 触发递归 (功能 1: 仅 good-query 有 context, 仅 1 个递归调用)
    result = await researcher.research("test", breadth=2, depth=2)

    # 失败子查询上下文为空, 但成功子查询上下文仍被聚合
    assert "ctx-good-query" in result["context"]
    # "fail-query" 上下文为空, 不应出现在聚合结果中
    assert "ctx-fail-query" not in result["context"]

    # sources 仅含成功子查询的来源 (fail-query 的 sources 为空)
    source_titles = [s["title"] for s in result["sources"]]
    assert "good-query" in source_titles
    assert "fail-query" not in source_titles

    # 功能 1: 仅对有 context 的 result 递归 (1 个递归调用, children=1)
    assert len(result["children"]) == 1

    # _research_sub_query 被调用 4 次:
    # depth 0: 2 次 (good-query + fail-query)
    # depth 1: 1 个递归调用 × next_breadth=max(2, 2//2)=2 → 2 次
    # 总计: 4 次
    assert call_count == 4


# ========== 功能 4: _trim_context_to_word_limit 上下文裁剪 ==========


def test_trim_context_to_word_limit_preserves_recent(researcher: DeepResearcher) -> None:
    """测试上下文裁剪: 从后向前保留最近内容, 超限丢弃早期 (对标 GPTR L213-231)."""
    # 5 个上下文块, 每块 3 词
    context_list = ["a b c", "d e f", "g h i", "j k l", "m n o"]
    # max_words=9 → 保留最近 3 块 (9 词)
    trimmed = DeepResearcher._trim_context_to_word_limit(context_list, max_words=9)
    assert trimmed == ["g h i", "j k l", "m n o"]


def test_trim_context_to_word_limit_all_fit(researcher: DeepResearcher) -> None:
    """测试上下文裁剪: 全部满足上限时保留全部."""
    context_list = ["short one", "another short"]
    trimmed = DeepResearcher._trim_context_to_word_limit(context_list, max_words=100)
    assert trimmed == context_list


def test_trim_context_to_word_limit_first_truncated(researcher: DeepResearcher) -> None:
    """测试上下文裁剪: 单块超限时截断到 max_words."""
    # 单块 5 词, max_words=3 → 截断到前 3 词
    context_list = ["one two three four five"]
    trimmed = DeepResearcher._trim_context_to_word_limit(context_list, max_words=3)
    assert trimmed == ["one two three"]


# ========== 功能 3: _build_next_query 递归查询生成 ==========


def test_build_next_query_with_goal_and_followups(researcher: DeepResearcher) -> None:
    """测试递归查询构建: researchGoal + followUpQuestions 拼接 (对标 GPTR L500-503)."""
    result = {
        "researchGoal": "研究 AI Agent 趋势",
        "followUpQuestions": ["Q1?", "Q2?"],
    }
    next_query = researcher._build_next_query(result)
    assert "Previous research goal: 研究 AI Agent 趋势" in next_query
    assert "Follow-up questions: Q1? Q2?" in next_query


def test_build_next_query_empty_returns_query(researcher: DeepResearcher) -> None:
    """测试递归查询构建: 无 researchGoal/followUpQuestions 时回退到 query."""
    result = {"query": "fallback query"}
    next_query = researcher._build_next_query(result)
    assert next_query == "fallback query"


# ========== 功能 6: _parse_research_results learnings 解析 ==========


def test_parse_research_results_standard_format() -> None:
    """测试 learnings 解析: 标准格式 (insight + sourceUrl)."""
    response = """{
        "learnings": [
            {"insight": "AI Agent 市场增长", "sourceUrl": "https://example.com/1"},
            {"insight": "多 Agent 协作成为趋势", "sourceUrl": ""}
        ],
        "followUpQuestions": ["Q1?", "Q2?"]
    }"""
    result = DeepResearcher._parse_research_results(response, num_learnings=3)
    assert len(result["learnings"]) == 2
    assert result["learnings"][0] == "AI Agent 市场增长"
    assert result["citations"]["AI Agent 市场增长"] == "https://example.com/1"
    assert "多 Agent 协作成为趋势" in result["learnings"]
    assert "多 Agent 协作成为趋势" not in result["citations"]  # sourceUrl 为空不入 citations
    assert len(result["followUpQuestions"]) == 2


def test_parse_research_results_degraded_format() -> None:
    """测试 learnings 解析: 降级格式 (字符串数组)."""
    response = '{"learnings": ["insight1", "insight2"], "followUpQuestions": ["Q?"]}'
    result = DeepResearcher._parse_research_results(response, num_learnings=3)
    assert result["learnings"] == ["insight1", "insight2"]
    assert result["citations"] == {}
    assert result["followUpQuestions"] == ["Q?"]


def test_parse_research_results_invalid_json() -> None:
    """测试 learnings 解析: 无效 JSON 返回空结构."""
    result = DeepResearcher._parse_research_results("not json", num_learnings=3)
    assert result == {"learnings": [], "followUpQuestions": [], "citations": {}}


def test_parse_research_results_num_limit() -> None:
    """测试 learnings 解析: num_learnings 限制数量."""
    response = """{
        "learnings": [
            {"insight": "L1", "sourceUrl": ""},
            {"insight": "L2", "sourceUrl": ""},
            {"insight": "L3", "sourceUrl": ""},
            {"insight": "L4", "sourceUrl": ""}
        ],
        "followUpQuestions": []
    }"""
    result = DeepResearcher._parse_research_results(response, num_learnings=2)
    assert len(result["learnings"]) == 2


# ========== 功能 3: _parse_search_queries 子查询解析 ==========


def test_parse_search_queries_standard_format() -> None:
    """测试子查询解析: 标准格式 (query + researchGoal)."""
    response = '[{"query": "Q1", "researchGoal": "G1"}, {"query": "Q2", "researchGoal": "G2"}]'
    queries = DeepResearcher._parse_search_queries(response, num_queries=3)
    assert len(queries) == 2
    assert queries[0] == {"query": "Q1", "researchGoal": "G1"}
    assert queries[1] == {"query": "Q2", "researchGoal": "G2"}


def test_parse_search_queries_string_array_degraded() -> None:
    """测试子查询解析: 字符串数组降级格式 (researchGoal=query)."""
    response = '["query1", "query2"]'
    queries = DeepResearcher._parse_search_queries(response, num_queries=3)
    assert len(queries) == 2
    assert queries[0] == {"query": "query1", "researchGoal": "query1"}


def test_parse_search_queries_invalid_json() -> None:
    """测试子查询解析: 无效 JSON 返回单个降级 query."""
    queries = DeepResearcher._parse_search_queries("not json", num_queries=3)
    assert len(queries) == 1
    assert "query" in queries[0]
    assert "researchGoal" in queries[0]


def test_parse_search_queries_num_limit() -> None:
    """测试子查询解析: num_queries 限制数量."""
    response = '[{"query": "Q1", "researchGoal": "G1"}, {"query": "Q2", "researchGoal": "G2"}, {"query": "Q3", "researchGoal": "G3"}]'
    queries = DeepResearcher._parse_search_queries(response, num_queries=2)
    assert len(queries) == 2


# ========== 功能 9: learnings 去重 ==========


@pytest.mark.asyncio
async def test_deep_researcher_learnings_dedup(researcher: DeepResearcher) -> None:
    """测试 learnings 跨子查询去重 (功能 9, 对标 GPTR list(set(all_learnings))).

    多个子查询返回相同 learning 时, self._learnings set 仅记录一次,
    上下文累积时也仅追加一次.
    """

    async def mock_research_sub_query(sq: str, **kwargs: Any) -> dict[str, Any]:
        # 两个子查询都返回相同 learning
        return {
            "context": f"ctx-{sq}",
            "sources": [],
            "learnings": ["duplicate learning"],
            "followUpQuestions": [],
            "citations": {"duplicate learning": "https://example.com/src"},
        }

    async def mock_generate_sub_queries(
        query: str, breadth: int, **kwargs: Any
    ) -> list[dict[str, str]]:
        return [
            {"query": "q1", "researchGoal": "g1"},
            {"query": "q2", "researchGoal": "g2"},
        ]

    researcher._research_sub_query = mock_research_sub_query  # type: ignore[method-assign]
    researcher._generate_sub_queries = mock_generate_sub_queries  # type: ignore[method-assign]

    # depth=1 避免递归 (聚焦 learnings 去重验证)
    result = await researcher.research("test", breadth=2, depth=1)

    # learnings 去重: 仅 1 个 (不是 2 个)
    assert len(result["learnings"]) == 1
    assert result["learnings"][0] == "duplicate learning"

    # citations 累积: 仅 1 个 entry
    assert len(result["citations"]) == 1
    assert result["citations"]["duplicate learning"] == "https://example.com/src"

    # 上下文含 citation 标注 (功能 7)
    assert "duplicate learning [Source: https://example.com/src]" in result["context"]


# ========== 自适应深度触发 ==========


@pytest.mark.asyncio
async def test_deep_researcher_adaptive_trigger(
    researcher: DeepResearcher,
    mock_llm: MagicMock,
) -> None:
    """测试自适应深度触发: breadth=None + depth=None 时调用 _assess_complexity.

    修复 nodes.py 自适应缺陷: 原显式传参导致 breadth is None 永远不满足.
    """

    # Mock _assess_complexity 返回简单参数 (depth=1 避免递归)
    async def mock_assess_complexity(query: str, **kwargs: Any) -> dict[str, int]:
        return {"breadth": 4, "depth": 1, "concurrency": 4}

    researcher._assess_complexity = mock_assess_complexity  # type: ignore[method-assign]

    async def mock_research_sub_query(sq: str, **kwargs: Any) -> dict[str, Any]:
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

    # 不传 breadth/depth → 触发自适应 (使用 mock_assess_complexity 返回 breadth=4/depth=1)
    result = await researcher.research("complex query")

    assert result["query"] == "complex query"
    # depth=1 不递归, children=[]
    assert result["children"] == []
    # breadth=4 → 4 个子查询
    assert len(result["sources"]) == 0  # mock 返回 sources=[]


# ========== max_sub_queries 守卫 ==========


@pytest.mark.asyncio
async def test_deep_researcher_max_sub_queries_guard(
    researcher: DeepResearcher,
) -> None:
    """测试 max_sub_queries 守卫: 递归树超限时降级到 depth=1.

    场景: breadth=10, depth=3 → next_breadth=max(2, 5)=5
    递归树规模: 10 * (1 + 5 + 25) = 310 > 28 (deep_research_max_sub_queries)
    → 降级到 depth=1, 仅 10 个子查询, 不递归.
    """

    async def mock_research_sub_query(sq: str, **kwargs: Any) -> dict[str, Any]:
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

    # breadth=10, depth=3 → 触发守卫降级到 depth=1
    result = await researcher.research("test", breadth=10, depth=3)

    # depth=1 不递归, children=[]
    assert result["children"] == []
    # breadth=10 → 10 个子查询, _research_sub_query 调用 10 次
    assert "ctx-q0" in result["context"]
    assert "ctx-q9" in result["context"]
