"""性能测试: DeepResearcher 核心方法性能基准.

验证 GPTR 深度研究核心方法的性能:
- _trim_context_to_word_limit 大列表性能 (10000 块上下文裁剪延迟)
- learnings 去重大量数据性能 (10000 个 learnings 去重延迟)

mock 版性能测试, mark=unit 避免被 conftest 跳过.
阈值宽松 enough 容忍 CI 环境抖动, 严格 enough 捕获性能退化.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings
from src.skills.researcher.deep_research import DeepResearcher

pytestmark = pytest.mark.unit


@pytest.fixture()
def settings() -> Settings:
    """构造最小 Settings."""
    return Settings(_env_file=None, mcp_strategy="disabled")


# ========== _trim_context_to_word_limit 大列表性能 ==========


def test_trim_context_large_list_performance(settings: Settings) -> None:
    """测试 _trim_context_to_word_limit 处理 10000 块上下文的性能.

    场景: 深度递归研究可能累积大量上下文块, 裁剪应快速完成.
    阈值: 10000 块裁剪延迟 < 1.0s (宽松阈值, 容忍 CI 抖动).
    """
    # 生成 10000 块上下文, 每块 10 词
    large_list = [f"word{i} " * 10 for i in range(10000)]

    start = time.perf_counter()
    trimmed = DeepResearcher._trim_context_to_word_limit(large_list, max_words=5000)
    elapsed = time.perf_counter() - start

    # 裁剪后应 ≤ 5000 词
    total_words = sum(len(item.split()) for item in trimmed)
    assert total_words <= 5000
    # 性能阈值: < 1.0s
    assert elapsed < 1.0, f"_trim_context_to_word_limit 10000 块耗时 {elapsed:.3f}s, 超过 1.0s 阈值"


def test_trim_context_huge_single_block_performance(settings: Settings) -> None:
    """测试 _trim_context_to_word_limit 处理超大单块的性能.

    场景: 单个上下文块含 100000 词, 裁剪应快速截断.
    阈值: 截断延迟 < 0.5s.
    """
    # 生成 100000 词的单块
    huge_block = "word " * 100000

    start = time.perf_counter()
    trimmed = DeepResearcher._trim_context_to_word_limit([huge_block], max_words=1000)
    elapsed = time.perf_counter() - start

    # 截断后应 ≤ 1000 词
    assert len(trimmed) == 1
    assert len(trimmed[0].split()) <= 1000
    # 性能阈值: < 0.5s
    assert elapsed < 0.5, f"超大单块截断耗时 {elapsed:.3f}s, 超过 0.5s 阈值"


# ========== learnings 去重大量数据性能 ==========


@pytest.mark.asyncio
async def test_learnings_dedup_large_scale_performance(settings: Settings) -> None:
    """测试 learnings 跨子查询去重大量数据的性能.

    场景: 100 个子查询, 每个返回 10 个 learnings (共 1000 个, 50% 重复),
    去重应快速完成.
    阈值: 1000 个 learnings 去重延迟 < 2.0s (含 research() 调用开销).
    """
    mock_llm = MagicMock()
    mock_llm.achat = AsyncMock()
    mock_cm = MagicMock()
    mock_cm.get_similar_content = AsyncMock(return_value="ctx")

    researcher = DeepResearcher(
        settings=settings,
        llm=mock_llm,
        context_manager=mock_cm,
    )

    # 生成 1000 个 learnings (50% 重复)
    # 100 个子查询 × 10 learnings, 其中 5 个唯一 × 20 次重复
    unique_learnings = [f"unique-learning-{i}" for i in range(50)]
    all_learnings = []
    for i in range(100):
        # 每个子查询返回 10 个 learnings (从 50 个唯一中循环取)
        learnings = [unique_learnings[(i * 10 + j) % 50] for j in range(10)]
        all_learnings.append(learnings)

    call_idx = 0

    async def mock_research_sub_query(sq: str, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_idx
        learnings = all_learnings[call_idx]
        call_idx += 1
        return {
            "context": f"ctx-{sq}",
            "sources": [],
            "learnings": learnings,
            "followUpQuestions": [],
            "citations": {},
        }

    async def mock_generate_sub_queries(
        query: str, breadth: int, **kwargs: Any
    ) -> list[dict[str, str]]:
        return [{"query": f"q{i}", "researchGoal": f"g{i}"} for i in range(breadth)]

    researcher._research_sub_query = mock_research_sub_query  # type: ignore[method-assign]
    researcher._generate_sub_queries = mock_generate_sub_queries  # type: ignore[method-assign]

    start = time.perf_counter()
    # breadth=100, depth=1 (聚焦去重性能, 不递归)
    result = await researcher.research("dedup perf test", breadth=100, depth=1)
    elapsed = time.perf_counter() - start

    # 去重后应仅 50 个唯一 learnings
    assert len(result["learnings"]) == 50
    # 性能阈值: < 2.0s
    assert elapsed < 2.0, f"1000 个 learnings 去重耗时 {elapsed:.3f}s, 超过 2.0s 阈值"


# ========== _parse_research_results 大量 learnings 解析性能 ==========


def test_parse_research_results_large_scale_performance() -> None:
    """测试 _parse_research_results 解析大量 learnings 的性能.

    场景: LLM 返回 100 个 learnings 的 JSON, 解析应快速完成.
    阈值: 100 个 learnings 解析延迟 < 0.1s.
    """
    # 构造 100 个 learnings 的 JSON
    learnings_json = (
        '{"learnings": ['
        + ", ".join(
            f'{{"insight": "learning-{i}", "sourceUrl": "https://example.com/{i}"}}'
            for i in range(100)
        )
        + '], "followUpQuestions": []}'
    )

    start = time.perf_counter()
    result = DeepResearcher._parse_research_results(learnings_json, num_learnings=100)
    elapsed = time.perf_counter() - start

    # 解析后应 100 个 learnings
    assert len(result["learnings"]) == 100
    # 性能阈值: < 0.1s
    assert elapsed < 0.1, f"100 个 learnings 解析耗时 {elapsed:.3f}s, 超过 0.1s 阈值"


# ========== _parse_search_queries 大量子查询解析性能 ==========


def test_parse_search_queries_large_scale_performance() -> None:
    """测试 _parse_search_queries 解析大量子查询的性能.

    场景: LLM 返回 100 个子查询的 JSON, 解析应快速完成.
    阈值: 100 个子查询解析延迟 < 0.1s.
    """
    # 构造 100 个子查询的 JSON
    queries_json = (
        "["
        + ", ".join(f'{{"query": "query-{i}", "researchGoal": "goal-{i}"}}' for i in range(100))
        + "]"
    )

    start = time.perf_counter()
    queries = DeepResearcher._parse_search_queries(queries_json, num_queries=100)
    elapsed = time.perf_counter() - start

    # 解析后应 100 个子查询
    assert len(queries) == 100
    # 性能阈值: < 0.1s
    assert elapsed < 0.1, f"100 个子查询解析耗时 {elapsed:.3f}s, 超过 0.1s 阈值"
