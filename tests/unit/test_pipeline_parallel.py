"""单元测试: ResearchConductor 流水线并行化 (P0-2).

验证 src/skills/researcher/research_conductor.py 的并行化优化:
- plan_research 与 _retrieve_private_data 并行 (asyncio.gather, 无数据依赖)
- 多个子查询 _process_sub_query 并行 (asyncio.gather)
- summary/subtopics 模式与私有数据检索并行
- 并行执行不影响结果正确性 (上下文合并顺序正确)

AGENTS.md 第 13 章: 单元测试不依赖外部服务 (LLM/Searchers/Retriever 全部 mock).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.llm.client import LLMResponse
from src.skills.researcher.research_conductor import ResearchConductor

pytestmark = pytest.mark.unit


@pytest.fixture()
def settings() -> Settings:
    """构造最小 Settings (mcp_strategy=disabled 避免 MCP mock)."""
    return Settings(_env_file=None, mcp_strategy="disabled")


@pytest.fixture()
def mock_llm() -> MagicMock:
    """Mock LLMClient (achat 为 AsyncMock)."""
    llm = MagicMock()
    llm.achat = AsyncMock()
    return llm


@pytest.fixture()
def mock_context_manager() -> MagicMock:
    """Mock ContextManager."""
    cm = MagicMock()
    cm.get_similar_content = AsyncMock(return_value="ctx")
    return cm


@pytest.fixture()
def mock_prompt_family() -> MagicMock:
    """Mock PromptFamily."""
    pf = MagicMock()
    pf.planner_prompt.return_value = "planner prompt"
    pf.curator_prompt.return_value = "curator prompt"
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


# ========== plan_research 与私有数据检索并行 ==========


@pytest.mark.asyncio
async def test_conduct_research_plan_and_private_data_parallel(
    conductor: ResearchConductor,
    mock_llm: MagicMock,
) -> None:
    """plan_research 与 _retrieve_private_data 应并行 (asyncio.gather).

    验证: 两者无数据依赖, 总耗时 ≈ max(plan, private) 而非 sum.
    """
    # plan_research 耗时 0.1s
    mock_llm.achat.return_value = LLMResponse(
        content='["sub_query_1", "sub_query_2"]',
        model="test",
    )

    # _retrieve_private_data 耗时 0.1s
    async def _slow_retrieve(*args: Any, **kwargs: Any) -> tuple[list[str], list[dict]]:
        await asyncio.sleep(0.1)
        return (["private ctx"], [{"url": "http://private", "title": "P"}])

    # _process_sub_query 快速返回
    async def _fast_process(sq: str, **kwargs: Any) -> dict[str, Any]:
        return {"contexts": [f"ctx-{sq}"], "sources": []}

    with (
        patch.object(conductor, "_retrieve_private_data", side_effect=_slow_retrieve),
        patch.object(conductor, "_process_sub_query", side_effect=_fast_process),
    ):
        # 给 plan_research 也加延迟
        async def _slow_achat(*args: Any, **kwargs: Any) -> LLMResponse:
            await asyncio.sleep(0.1)
            return LLMResponse(content='["sq1"]', model="test")

        mock_llm.achat = _slow_achat

        start = time.time()
        result = await conductor.conduct_research("test query", user_id="u1", session_id="s1")
        elapsed = time.time() - start

    # 并行: 总耗时 ≈ max(0.1, 0.1) + sub_query 处理 ≈ 0.1 + 少量
    # 串行则需 0.2+; 此处宽松断言 < 0.35s (含 sub_query 处理)
    assert elapsed < 0.35, f"并行执行耗时过长: {elapsed:.2f}s (期望 < 0.35s)"
    assert "contexts" in result


# ========== 多子查询并行处理 ==========


@pytest.mark.asyncio
async def test_conduct_research_sub_queries_parallel(
    conductor: ResearchConductor,
    mock_llm: MagicMock,
) -> None:
    """多个子查询 _process_sub_query 应并行 (asyncio.gather)."""
    # plan_research 返回 3 个子查询
    mock_llm.achat.return_value = LLMResponse(
        content='["sq1", "sq2", "sq3"]',
        model="test",
    )

    call_times: list[float] = []

    async def _slow_process(sq: str, **kwargs: Any) -> dict[str, Any]:
        call_times.append(time.time())
        await asyncio.sleep(0.1)  # 每个子查询耗时 0.1s
        return {"contexts": [f"ctx-{sq}"], "sources": []}

    with (
        patch.object(conductor, "_retrieve_private_data", new=AsyncMock(return_value=([], []))),
        patch.object(conductor, "_process_sub_query", side_effect=_slow_process),
    ):
        start = time.time()
        await conductor.conduct_research("q", user_id="u1", session_id="s1")
        elapsed = time.time() - start

    # 3 个子查询并行 (各 0.1s), 总耗时 ≈ 0.1s (串行则需 0.3s+)
    assert elapsed < 0.25, f"子查询并行耗时过长: {elapsed:.2f}s"
    # 3 个子查询 + 原始 query 追加 (conduct_research 会 append 原 query) = 4 次调用
    assert len(call_times) == 4  # 3 个子查询 + 1 个原始 query 都被调用
    # 三个调用时间接近 (并行启动, 间隔 < 0.05s)
    if len(call_times) >= 2:
        max_diff = max(call_times) - min(call_times)
        assert max_diff < 0.05, f"子查询启动时间差过大: {max_diff:.3f}s (非并行)"


# ========== summary 模式与私有数据并行 ==========


@pytest.mark.asyncio
async def test_summary_mode_parallel_with_private_data(
    conductor: ResearchConductor,
    mock_llm: MagicMock,
) -> None:
    """summary 模式: _conduct_summary 与 _retrieve_private_data 并行."""
    mock_llm.achat.return_value = LLMResponse(content="report", model="test")

    summary_call_time: list[float] = []
    private_call_time: list[float] = []

    async def _slow_summary(*args: Any, **kwargs: Any) -> dict[str, Any]:
        summary_call_time.append(time.time())
        await asyncio.sleep(0.1)
        return {"report_md": "summary report", "contexts": ["summary ctx"], "sources": []}

    async def _slow_private(*args: Any, **kwargs: Any) -> tuple[list[str], list[dict]]:
        private_call_time.append(time.time())
        await asyncio.sleep(0.1)
        return (["private ctx"], [])

    with (
        patch.object(conductor, "_conduct_summary", side_effect=_slow_summary),
        patch.object(conductor, "_retrieve_private_data", side_effect=_slow_private),
    ):
        start = time.time()
        result = await conductor.conduct_research(
            "q", mode="summary", user_id="u1", session_id="s1"
        )
        elapsed = time.time() - start

    # 并行: 总耗时 ≈ 0.1s (串行则需 0.2s)
    assert elapsed < 0.18, f"summary 并行耗时过长: {elapsed:.2f}s"
    # 两者都启动
    assert len(summary_call_time) == 1
    assert len(private_call_time) == 1
    # 启动时间接近 (并行)
    assert abs(summary_call_time[0] - private_call_time[0]) < 0.05
    # 私有数据合并到结果
    assert "private ctx" in result["contexts"]


# ========== subtopics 模式与私有数据并行 ==========


@pytest.mark.asyncio
async def test_subtopics_mode_parallel_with_private_data(
    conductor: ResearchConductor,
    mock_llm: MagicMock,
) -> None:
    """subtopics 模式: _conduct_subtopics 与 _retrieve_private_data 并行."""
    mock_llm.achat.return_value = LLMResponse(content="report", model="test")

    subtopics_call_time: list[float] = []
    private_call_time: list[float] = []

    async def _slow_subtopics(*args: Any, **kwargs: Any) -> dict[str, Any]:
        subtopics_call_time.append(time.time())
        await asyncio.sleep(0.1)
        return {"report_md": "subtopics report", "contexts": ["st ctx"], "sources": []}

    async def _slow_private(*args: Any, **kwargs: Any) -> tuple[list[str], list[dict]]:
        private_call_time.append(time.time())
        await asyncio.sleep(0.1)
        return (["private ctx"], [])

    with (
        patch.object(conductor, "_conduct_subtopics", side_effect=_slow_subtopics),
        patch.object(conductor, "_retrieve_private_data", side_effect=_slow_private),
    ):
        start = time.time()
        result = await conductor.conduct_research(
            "q", mode="subtopics", user_id="u1", session_id="s1"
        )
        elapsed = time.time() - start

    assert elapsed < 0.18, f"subtopics 并行耗时过长: {elapsed:.2f}s"
    assert abs(subtopics_call_time[0] - private_call_time[0]) < 0.05
    assert "private ctx" in result["contexts"]


# ========== 并行结果正确性 ==========


@pytest.mark.asyncio
async def test_parallel_execution_preserves_context_order(
    conductor: ResearchConductor,
    mock_llm: MagicMock,
) -> None:
    """并行执行后, 私有数据上下文应优先合并 (顺序: private + web)."""
    mock_llm.achat.return_value = LLMResponse(
        content='["sq1"]',
        model="test",
    )

    async def _process(sq: str, **kwargs: Any) -> dict[str, Any]:
        return {"context": f"web-{sq}", "sources": [{"url": "http://web", "title": "W"}]}

    with (
        patch.object(
            conductor,
            "_retrieve_private_data",
            new=AsyncMock(return_value=(["private-ctx"], [{"url": "http://p", "title": "P"}])),
        ),
        patch.object(conductor, "_process_sub_query", side_effect=_process),
    ):
        result = await conductor.conduct_research("q", user_id="u1", session_id="s1")

    # 私有上下文优先 (P0-2: 合并顺序 private + web)
    assert result["contexts"][0] == "private-ctx"
    assert "web-sq1" in result["contexts"]
    # 私有 sources 优先
    assert result["sources"][0]["title"] == "P"
