"""单元测试: DeepResearcher GPTR 深度研究功能补全测试.

补充 tests/unit/test_deep_research.py 未覆盖的代码分支:
- _build_next_query: 仅 researchGoal / 仅 followUpQuestions / 两者皆空回退 query
- _trim_context_to_word_limit: 空列表 / 单条超限截断
- _parse_research_results: 空响应 / 部分字段缺失 (缺 followUpQuestions / sourceUrl) /
  learning 字段别名 (learning 而非 insight) / citation 字段别名
- _parse_search_queries: 空字符串响应 / 混合 dict+str 数组
- _process_research_results: 空上下文短路 / 正常提取 / LLM 失败降级
- _generate_sub_queries: 正常返回 list[dict] / LLM 异常降级
- _assess_complexity: 非 dict 响应 / complexity 非整数 / complexity 越界
- citation 标注: 有/无 citation 在 context 中的差异

单元测试不依赖外部服务 (LLM/Searchers/Scrapers 全部 mock).
测试数据隔离: user_id=test_gptr_*, session_id=test_deep_research_gptr_*.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

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


# ========== _build_next_query 分支补全 (功能 3) ==========


def test_build_next_query_only_research_goal(researcher: DeepResearcher) -> None:
    """测试递归查询构建: 仅 researchGoal 无 followUpQuestions.

    仅包含 "Previous research goal:" 前缀, 不包含 "Follow-up questions:".
    """
    result = {"researchGoal": "研究 AI 趋势", "followUpQuestions": []}
    next_query = researcher._build_next_query(result)
    assert "Previous research goal: 研究 AI 趋势" in next_query
    assert "Follow-up questions" not in next_query


def test_build_next_query_only_follow_ups(researcher: DeepResearcher) -> None:
    """测试递归查询构建: 仅 followUpQuestions 无 researchGoal.

    仅包含 "Follow-up questions:" 前缀, 不包含 "Previous research goal:".
    """
    result = {"researchGoal": "", "followUpQuestions": ["Q1?", "Q2?"]}
    next_query = researcher._build_next_query(result)
    assert "Follow-up questions: Q1? Q2?" in next_query
    assert "Previous research goal" not in next_query


def test_build_next_query_no_fields_no_query(researcher: DeepResearcher) -> None:
    """测试递归查询构建: 无 researchGoal/followUpQuestions/query 时返回空字符串."""
    result: dict[str, Any] = {}
    next_query = researcher._build_next_query(result)
    assert next_query == ""


# ========== _trim_context_to_word_limit 空列表 (功能 4) ==========


def test_trim_context_to_word_limit_empty_list() -> None:
    """测试上下文裁剪: 空列表返回空列表."""
    trimmed = DeepResearcher._trim_context_to_word_limit([], max_words=100)
    assert trimmed == []


def test_trim_context_to_word_limit_multiple_over_limit() -> None:
    """测试上下文裁剪: 多块超限时从后向前保留, 中间块因超限被丢弃."""
    # 4 块各 3 词, max_words=6 → 仅保留最后 2 块 (6 词)
    context_list = ["a b c", "d e f", "g h i", "j k l"]
    trimmed = DeepResearcher._trim_context_to_word_limit(context_list, max_words=6)
    assert trimmed == ["g h i", "j k l"]


# ========== _parse_research_results 分支补全 (功能 6) ==========


def test_parse_research_results_empty_response() -> None:
    """测试 learnings 解析: 空字符串响应返回空结构."""
    result = DeepResearcher._parse_research_results("", num_learnings=3)
    assert result == {"learnings": [], "followUpQuestions": [], "citations": {}}


def test_parse_research_results_missing_follow_up_questions() -> None:
    """测试 learnings 解析: 缺失 followUpQuestions 字段时返回空列表."""
    response = '{"learnings": [{"insight": "L1", "sourceUrl": ""}]}'
    result = DeepResearcher._parse_research_results(response, num_learnings=3)
    assert result["learnings"] == ["L1"]
    assert result["followUpQuestions"] == []


def test_parse_research_results_learning_field_alias() -> None:
    """测试 learnings 解析: learning 字段别名 (item.learning 而非 item.insight)."""
    response = '{"learnings": [{"learning": "alias-insight", "sourceUrl": "https://x.com"}]}'
    result = DeepResearcher._parse_research_results(response, num_learnings=3)
    assert result["learnings"] == ["alias-insight"]
    assert result["citations"]["alias-insight"] == "https://x.com"


def test_parse_research_results_citation_field_alias() -> None:
    """测试 learnings 解析: citation 字段别名 (item.citation 而非 item.sourceUrl)."""
    response = '{"learnings": [{"insight": "L1", "citation": "https://y.com"}]}'
    result = DeepResearcher._parse_research_results(response, num_learnings=3)
    assert result["learnings"] == ["L1"]
    assert result["citations"]["L1"] == "https://y.com"


def test_parse_research_results_questions_alias() -> None:
    """测试 learnings 解析: questions 字段别名 (followUpQuestions 缺失时回退 questions)."""
    response = '{"learnings": [], "questions": ["Q1?", "Q2?"]}'
    result = DeepResearcher._parse_research_results(response, num_learnings=3)
    assert result["followUpQuestions"] == ["Q1?", "Q2?"]


def test_parse_research_results_empty_learnings_with_questions() -> None:
    """测试 learnings 解析: learnings 为空但 questions 非空时正常返回."""
    response = '{"learnings": [], "followUpQuestions": ["Q?"]}'
    result = DeepResearcher._parse_research_results(response, num_learnings=3)
    assert result["learnings"] == []
    assert result["followUpQuestions"] == ["Q?"]


def test_parse_research_results_both_empty_returns_empty() -> None:
    """测试 learnings 解析: learnings 和 questions 均空时返回空结构 (降级)."""
    response = '{"learnings": [], "followUpQuestions": []}'
    result = DeepResearcher._parse_research_results(response, num_learnings=3)
    assert result == {"learnings": [], "followUpQuestions": [], "citations": {}}


# ========== _parse_search_queries 分支补全 (功能 3) ==========


def test_parse_search_queries_empty_string() -> None:
    """测试子查询解析: 空字符串响应降级返回单个 query (用 'query' 占位)."""
    queries = DeepResearcher._parse_search_queries("", num_queries=3)
    assert len(queries) == 1
    assert queries[0]["query"] == "query"
    assert queries[0]["researchGoal"] == "query"


def test_parse_search_queries_whitespace_only() -> None:
    """测试子查询解析: 仅空白字符响应降级返回单个 query."""
    queries = DeepResearcher._parse_search_queries("   \n  \t  ", num_queries=3)
    assert len(queries) == 1
    assert queries[0]["query"] == "query"


def test_parse_search_queries_mixed_dict_and_string() -> None:
    """测试子查询解析: 混合 dict + str 数组 (dict 正常解析, str 降级 researchGoal=query)."""
    response = '[{"query": "Q1", "researchGoal": "G1"}, "string-query", {"query": "Q2", "researchGoal": "G2"}]'
    queries = DeepResearcher._parse_search_queries(response, num_queries=5)
    assert len(queries) == 3
    assert queries[0] == {"query": "Q1", "researchGoal": "G1"}
    assert queries[1] == {"query": "string-query", "researchGoal": "string-query"}
    assert queries[2] == {"query": "Q2", "researchGoal": "G2"}


def test_parse_search_queries_dict_missing_fields() -> None:
    """测试子查询解析: dict 缺 query 或 researchGoal 字段时跳过该项."""
    response = '[{"query": "Q1", "researchGoal": "G1"}, {"query": ""}, {"researchGoal": "G2"}]'
    queries = DeepResearcher._parse_search_queries(response, num_queries=5)
    # 仅第一项有效 (后两项缺字段被跳过)
    assert len(queries) == 1
    assert queries[0] == {"query": "Q1", "researchGoal": "G1"}


def test_parse_search_queries_non_list_json() -> None:
    """测试子查询解析: JSON 解析为非 list (如 dict) 时降级返回单个 query."""
    response = '{"not": "a list"}'
    queries = DeepResearcher._parse_search_queries(response, num_queries=3)
    assert len(queries) == 1
    # 降级: 用响应文本前 100 字符作为 query
    assert "not" in queries[0]["query"]


# ========== _process_research_results 空上下文短路 / LLM 失败降级 (功能 6) ==========


@pytest.mark.asyncio
async def test_process_research_results_empty_context_short_circuit(
    researcher: DeepResearcher,
    mock_llm: MagicMock,
) -> None:
    """测试 learnings 提取: 空上下文直接返回空结果 (不调用 LLM).

    避免无意义 LLM 调用, 节省成本.
    """
    result = await researcher._process_research_results(
        "query",
        "",
        num_learnings=3,
        user_id="test_gptr_user",
        session_id="test_deep_research_gptr_empty",
    )
    assert result == {"learnings": [], "followUpQuestions": [], "citations": {}}
    # LLM 不应被调用
    mock_llm.achat.assert_not_called()


@pytest.mark.asyncio
async def test_process_research_results_whitespace_context_short_circuit(
    researcher: DeepResearcher,
    mock_llm: MagicMock,
) -> None:
    """测试 learnings 提取: 仅空白字符的上下文也短路返回空结果."""
    result = await researcher._process_research_results(
        "query",
        "   \n\t  ",
        num_learnings=3,
    )
    assert result == {"learnings": [], "followUpQuestions": [], "citations": {}}
    mock_llm.achat.assert_not_called()


@pytest.mark.asyncio
async def test_process_research_results_normal_extraction(
    researcher: DeepResearcher,
    mock_llm: MagicMock,
) -> None:
    """测试 learnings 提取: 正常 LLM 返回时解析 learnings/citations/followUpQuestions."""
    mock_llm.achat.return_value = LLMResponse(
        content=(
            '{"learnings": ['
            '{"insight": "AI Agent 市场增长", "sourceUrl": "https://example.com/1"},'
            '{"insight": "多模态成为趋势", "sourceUrl": ""}'
            '], "followUpQuestions": ["Q1?", "Q2?"]}'
        ),
        model="test",
    )
    result = await researcher._process_research_results(
        "AI 趋势",
        "some context about AI agents",
        num_learnings=3,
        user_id="test_gptr_user",
        session_id="test_deep_research_gptr_normal",
    )
    assert len(result["learnings"]) == 2
    assert result["learnings"][0] == "AI Agent 市场增长"
    assert result["citations"]["AI Agent 市场增长"] == "https://example.com/1"
    # sourceUrl 为空不入 citations
    assert "多模态成为趋势" not in result["citations"]
    assert result["followUpQuestions"] == ["Q1?", "Q2?"]


@pytest.mark.asyncio
async def test_process_research_results_llm_failure_degradation(
    researcher: DeepResearcher,
    mock_llm: MagicMock,
) -> None:
    """测试 learnings 提取: LLM 调用失败时降级返回空结果 (不抛异常)."""
    mock_llm.achat.side_effect = RuntimeError("LLM 服务不可用")
    result = await researcher._process_research_results(
        "query",
        "some context",
        num_learnings=3,
    )
    assert result == {"learnings": [], "followUpQuestions": [], "citations": {}}


# ========== _generate_sub_queries 正常 / 降级 (功能 3) ==========


@pytest.mark.asyncio
async def test_generate_sub_queries_normal(
    researcher: DeepResearcher,
    mock_llm: MagicMock,
) -> None:
    """测试子查询生成: LLM 正常返回 JSON 数组时解析为 list[dict]."""
    mock_llm.achat.return_value = LLMResponse(
        content='[{"query": "Q1", "researchGoal": "G1"}, {"query": "Q2", "researchGoal": "G2"}]',
        model="test",
    )
    queries = await researcher._generate_sub_queries(
        "test query",
        breadth=2,
        user_id="test_gptr_user",
        session_id="test_deep_research_gptr_gen",
    )
    assert len(queries) == 2
    assert queries[0] == {"query": "Q1", "researchGoal": "G1"}
    assert queries[1] == {"query": "Q2", "researchGoal": "G2"}


@pytest.mark.asyncio
async def test_generate_sub_queries_llm_failure_degradation(
    researcher: DeepResearcher,
    mock_llm: MagicMock,
) -> None:
    """测试子查询生成: LLM 异常时 _parse_search_queries 降级返回单个 query.

    _generate_sub_queries 不捕获异常, 异常向上传播;
    但 _parse_search_queries 对无效响应降级返回单个 query.
    此处验证 LLM 返回无效 JSON 时, _parse_search_queries 降级处理.
    """
    mock_llm.achat.return_value = LLMResponse(
        content="这不是 JSON",
        model="test",
    )
    queries = await researcher._generate_sub_queries("test query", breadth=3)
    # 降级: 返回单个 query (用响应文本前 100 字符)
    assert len(queries) == 1
    assert "query" in queries[0]
    assert "researchGoal" in queries[0]


# ========== _assess_complexity 边界补全 (功能 11) ==========


@pytest.mark.asyncio
async def test_assess_complexity_non_dict_response(
    researcher: DeepResearcher,
    mock_llm: MagicMock,
    settings: Settings,
) -> None:
    """测试复杂度评估: LLM 返回非 dict (如 list) 时降级默认值."""
    mock_llm.achat.return_value = LLMResponse(
        content='["not", "a", "dict"]',
        model="test",
    )
    params = await researcher._assess_complexity("任意查询")
    assert params == {
        "breadth": settings.deep_research_breadth,
        "depth": 1,  # 安全网
        "concurrency": settings.deep_research_concurrency,
    }


@pytest.mark.asyncio
async def test_assess_complexity_non_int_complexity(
    researcher: DeepResearcher,
    mock_llm: MagicMock,
    settings: Settings,
) -> None:
    """测试复杂度评估: complexity 非整数 (如字符串) 时降级默认值."""
    mock_llm.achat.return_value = LLMResponse(
        content='{"complexity": "三", "reason": "字符串非整数"}',
        model="test",
    )
    params = await researcher._assess_complexity("任意查询")
    assert params["depth"] == 1  # 安全网


@pytest.mark.asyncio
async def test_assess_complexity_out_of_range(
    researcher: DeepResearcher,
    mock_llm: MagicMock,
    settings: Settings,
) -> None:
    """测试复杂度评估: complexity 越界 (0 或 6) 时降级默认值."""
    # complexity=0 (低于下限 1)
    mock_llm.achat.return_value = LLMResponse(
        content='{"complexity": 0, "reason": "越界低"}',
        model="test",
    )
    params = await researcher._assess_complexity("任意查询")
    assert params["depth"] == 1

    # complexity=6 (高于上限 5)
    mock_llm.achat.return_value = LLMResponse(
        content='{"complexity": 6, "reason": "越界高"}',
        model="test",
    )
    params = await researcher._assess_complexity("任意查询")
    assert params["depth"] == 1


@pytest.mark.asyncio
async def test_assess_complexity_boundary_values(
    researcher: DeepResearcher,
    mock_llm: MagicMock,
) -> None:
    """测试复杂度评估: 边界值 1/3/4 映射正确."""
    # complexity=1 → 简单 (breadth=4, depth=1)
    mock_llm.achat.return_value = LLMResponse(
        content='{"complexity": 1, "reason": "最简单"}',
        model="test",
    )
    params = await researcher._assess_complexity("定义查询")
    assert params == {"breadth": 4, "depth": 1, "concurrency": 4}

    # complexity=4 → 复杂 (breadth=4, depth=3, concurrency=6)
    mock_llm.achat.return_value = LLMResponse(
        content='{"complexity": 4, "reason": "复杂"}',
        model="test",
    )
    params = await researcher._assess_complexity("复杂查询")
    assert params == {"breadth": 4, "depth": 3, "concurrency": 6}


# ========== citation 标注差异 (功能 7) ==========


@pytest.mark.asyncio
async def test_citation_annotation_with_and_without(
    researcher: DeepResearcher,
) -> None:
    """测试 citation 标注: 有 citation 追加 [Source: url], 无 citation 仅追加 learning.

    功能 7: learnings 累积到上下文时, 有 citation 的 learning 追加来源标注,
    无 citation 的 learning 仅追加原文.
    """

    async def mock_research_sub_query(sq: str, **kwargs: Any) -> dict[str, Any]:
        if sq == "with-citation":
            return {
                "context": f"ctx-{sq}",
                "sources": [],
                "learnings": ["有引用的发现"],
                "followUpQuestions": [],
                "citations": {"有引用的发现": "https://example.com/src"},
            }
        return {
            "context": f"ctx-{sq}",
            "sources": [],
            "learnings": ["无引用的发现"],
            "followUpQuestions": [],
            "citations": {},
        }

    async def mock_generate_sub_queries(
        query: str, breadth: int, **kwargs: Any
    ) -> list[dict[str, str]]:
        return [
            {"query": "with-citation", "researchGoal": "g1"},
            {"query": "without-citation", "researchGoal": "g2"},
        ]

    researcher._research_sub_query = mock_research_sub_query  # type: ignore[method-assign]
    researcher._generate_sub_queries = mock_generate_sub_queries  # type: ignore[method-assign]

    # depth=1 避免递归 (聚焦 citation 标注验证)
    result = await researcher.research("test", breadth=2, depth=1)

    # 有 citation 的 learning 追加 [Source: ...]
    assert "有引用的发现 [Source: https://example.com/src]" in result["context"]
    # 无 citation 的 learning 仅追加原文
    assert "无引用的发现" in result["context"]
    assert "无引用的发现 [Source:" not in result["context"]

    # citations 累积: 仅 1 个 entry (有 citation 的)
    assert len(result["citations"]) == 1
    assert result["citations"]["有引用的发现"] == "https://example.com/src"


# ========== research() depth=1 不递归 (功能 1 边界) ==========


@pytest.mark.asyncio
async def test_research_depth_1_no_recursion(researcher: DeepResearcher) -> None:
    """测试 research depth=1 时不递归 (children 为空).

    depth=1: 仅生成子查询 + 检索 + 聚合, 不进入下一层递归.
    """

    async def mock_research_sub_query(sq: str, **kwargs: Any) -> dict[str, Any]:
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
        return [{"query": f"q{i}", "researchGoal": f"g{i}"} for i in range(breadth)]

    researcher._research_sub_query = mock_research_sub_query  # type: ignore[method-assign]
    researcher._generate_sub_queries = mock_generate_sub_queries  # type: ignore[method-assign]

    result = await researcher.research("test", breadth=3, depth=1)

    # depth=1: 不递归, children 为空
    assert result["children"] == []
    # breadth=3: 3 个子查询, 3 个 sources
    assert len(result["sources"]) == 3
    # 上下文含所有子查询的 context
    assert "ctx-q0" in result["context"]
    assert "ctx-q1" in result["context"]
    assert "ctx-q2" in result["context"]


# ========== research() 父上下文继承 (功能 4) ==========


@pytest.mark.asyncio
async def test_research_parent_context_inherited(researcher: DeepResearcher) -> None:
    """测试 research 递归时 _parent_context 被前置到聚合上下文.

    功能 4: 递归调用时传入 aggregated_context 作为 _parent_context,
    子层聚合上下文前置父上下文 (用 --- 分隔).
    """

    async def mock_research_sub_query(sq: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "context": f"child-ctx-{sq}",
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

    result = await researcher.research(
        "test",
        breadth=2,
        depth=1,
        _parent_context="PARENT CONTEXT HERE",
    )

    # 父上下文被前置, 用 --- 分隔
    assert result["context"].startswith("PARENT CONTEXT HERE")
    assert "---" in result["context"]
    assert "child-ctx-q0" in result["context"]


# ========== max_sub_queries 守卫不触发 (正常 depth=2) ==========


@pytest.mark.asyncio
async def test_max_sub_queries_guard_not_triggered(
    researcher: DeepResearcher,
    settings: Settings,
) -> None:
    """测试 max_sub_queries 守卫: breadth=4, depth=2 不触发守卫 (12 < 28).

    递归树: 4 + 4*2 = 12 < 28 (deep_research_max_sub_queries), 不降级.
    """
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

    result = await researcher.research("test", breadth=4, depth=2)

    # 守卫未触发: children 非空 (depth=2 递归)
    assert len(result["children"]) > 0
    # 调用次数: depth 0 (4 次) + depth 1 (4 个递归 × next_breadth=max(2, 4//2)=2 = 8 次) = 12 次
    assert call_count == 12
    # 验证 max_sub_queries 默认值
    assert settings.deep_research_max_sub_queries == 28
