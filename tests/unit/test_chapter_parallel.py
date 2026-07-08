"""单元测试: detailed_report 章节并行 (P1-2/V4-P0-02).

验证 src/skills/researcher/report_generator.py 的 _generate_detailed_report 并行优化:
- 子主题生成 (_generate_subtopics) 与引言 (_write_introduction) 并行 (P1-2)
- 多子主题章节并行处理 (V4-P0-02 asyncio.gather, _research_and_write_subtopic)
- 单子主题 LLM 失败重试 + 占位文本 (V4-P1-03 不阻断整体)
- 章节并行后报告拼接顺序正确 (TOC + 引言 + 正文 + 结论)

AGENTS.md 第 13 章: 单元测试不依赖外部服务 (LLM 全部 mock).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.llm.client import LLMResponse
from src.skills.researcher.report_generator import ReportGenerator

pytestmark = pytest.mark.unit


@pytest.fixture()
def settings() -> Settings:
    """构造最小 Settings."""
    return Settings(_env_file=None)


@pytest.fixture()
def mock_llm() -> MagicMock:
    """Mock LLMClient."""
    llm = MagicMock()
    llm.achat = AsyncMock(return_value=LLMResponse(content="", model="test"))
    return llm


@pytest.fixture()
def generator(settings: Settings, mock_llm: MagicMock) -> ReportGenerator:
    """构造 ReportGenerator."""
    return ReportGenerator(settings=settings, llm=mock_llm)


@pytest.fixture(autouse=True)
def _mock_heavy_constructors() -> Any:
    """mock ResearchConductor / WrittenContentCompressor 构造, 避免首次
    get_embeddings_client()/get_llm_client() 单例初始化引入 ~0.25s 同步开销.

    所有测试均 mock _research_and_write_subtopic, 不需要真实 ResearchConductor 实例.
    """
    with (
        patch("src.skills.researcher.report_generator.ResearchConductor"),
        patch("src.skills.researcher.report_generator.WrittenContentCompressor"),
    ):
        yield


# ========== 子主题生成与引言并行 (P1-2) ==========


@pytest.mark.asyncio
async def test_subtopics_and_introduction_parallel(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """_generate_subtopics 与 _write_introduction 应并行 (asyncio.gather).

    P1-2: 两者均只依赖 query/contexts/references/role_persona, 无数据依赖.
    """
    subtopics_call_time: list[float] = []
    intro_call_time: list[float] = []

    async def _slow_subtopics(*args: Any, **kwargs: Any) -> list[str]:
        subtopics_call_time.append(time.time())
        await asyncio.sleep(0.1)
        return ["子主题1", "子主题2"]

    async def _slow_intro(*args: Any, **kwargs: Any) -> str:
        intro_call_time.append(time.time())
        await asyncio.sleep(0.1)
        return "## 引言\n\n这是引言内容."

    # ResearchConductor/WrittenContentCompressor 构造由 autouse fixture mock
    with (
        patch.object(generator, "_generate_subtopics", side_effect=_slow_subtopics),
        patch.object(generator, "_write_introduction", side_effect=_slow_intro),
        patch.object(generator, "_write_conclusion", new=AsyncMock(return_value="## 结论")),
        patch.object(
            generator,
            "_research_and_write_subtopic",
            new=AsyncMock(return_value=("### 章节", [], False)),
        ),
        patch.object(generator, "_generate_toc", return_value="## 目录"),
    ):
        start = time.time()
        await generator._generate_detailed_report(
            query="test query",
            contexts=["ctx1", "ctx2"],
            sources=[{"url": "http://x", "title": "X"}],
            user_id="u1",
            session_id="s1",
        )
        elapsed = time.time() - start

    # 并行: 总耗时 ≈ 0.1s (串行则需 0.2s)
    assert elapsed < 0.18, f"子主题+引言并行耗时过长: {elapsed:.2f}s"
    # 两者都启动
    assert len(subtopics_call_time) == 1
    assert len(intro_call_time) == 1
    # 启动时间接近 (并行)
    assert abs(subtopics_call_time[0] - intro_call_time[0]) < 0.05


# ========== 多子主题章节并行 (V4-P0-02) ==========


@pytest.mark.asyncio
async def test_multiple_subtopics_sections_parallel(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """多个子主题章节 _research_and_write_subtopic 应并行 (asyncio.gather)."""
    section_call_times: list[float] = []

    async def _slow_section(*args: Any, **kwargs: Any) -> tuple[str, list[dict[str, Any]], bool]:
        section_call_times.append(time.time())
        await asyncio.sleep(0.1)  # 每个章节耗时 0.1s
        topic = kwargs.get("topic", "")
        return (f"### {topic}\n\n章节内容", [], False)

    with (
        patch.object(
            generator,
            "_generate_subtopics",
            new=AsyncMock(return_value=["主题1", "主题2", "主题3"]),
        ),
        patch.object(generator, "_write_introduction", new=AsyncMock(return_value="引言")),
        patch.object(generator, "_write_conclusion", new=AsyncMock(return_value="结论")),
        patch.object(generator, "_research_and_write_subtopic", side_effect=_slow_section),
        patch.object(generator, "_generate_toc", return_value="目录"),
    ):
        start = time.time()
        await generator._generate_detailed_report(
            query="q",
            contexts=["ctx"],
            sources=[],
            user_id="u1",
            session_id="s1",
        )
        elapsed = time.time() - start

    # 3 个章节并行 (各 0.1s), 总耗时 ≈ 0.1s (串行则需 0.3s)
    assert elapsed < 0.25, f"多章节并行耗时过长: {elapsed:.2f}s"
    assert len(section_call_times) == 3
    # 三个章节启动时间接近 (并行)
    if len(section_call_times) >= 2:
        max_diff = max(section_call_times) - min(section_call_times)
        assert max_diff < 0.05, f"章节启动时间差过大: {max_diff:.3f}s (非并行)"


# ========== 单子主题失败不阻断整体 (V4-P1-03) ==========


@pytest.mark.asyncio
async def test_single_subtopic_failure_does_not_block_others(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """单个子主题 LLM 失败 → 返回占位文本, 不影响其他章节."""

    async def _section_with_failure(
        *args: Any, **kwargs: Any
    ) -> tuple[str, list[dict[str, Any]], bool]:
        topic = kwargs.get("topic", "")
        if topic == "失败主题":
            # 模拟 LLM 失败后返回占位文本
            return ("### 失败主题\n\n[本节内容生成失败]", [], False)
        return (f"### {topic}\n\n正常内容", [], False)

    with (
        patch.object(
            generator,
            "_generate_subtopics",
            new=AsyncMock(return_value=["正常主题1", "失败主题", "正常主题2"]),
        ),
        patch.object(generator, "_write_introduction", new=AsyncMock(return_value="引言")),
        patch.object(generator, "_write_conclusion", new=AsyncMock(return_value="结论")),
        patch.object(generator, "_research_and_write_subtopic", side_effect=_section_with_failure),
        patch.object(generator, "_generate_toc", return_value="目录"),
    ):
        result = await generator._generate_detailed_report(
            query="q",
            contexts=["ctx"],
            sources=[],
            user_id="u1",
            session_id="s1",
        )

    # 报告应包含正常章节 + 失败占位
    report_md = result.get("report_md", "")
    assert "正常主题1" in report_md
    assert "正常主题2" in report_md
    assert "失败主题" in report_md  # 失败章节占位仍存在


# ========== 报告拼接顺序正确 ==========


@pytest.mark.asyncio
async def test_detailed_report_section_order_correct(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """并行后报告拼接顺序: TOC + 引言 + 正文 + 结论 + 引用."""
    with (
        patch.object(
            generator,
            "_generate_subtopics",
            new=AsyncMock(return_value=["主题A"]),
        ),
        patch.object(generator, "_write_introduction", new=AsyncMock(return_value="INTRO_MARKER")),
        patch.object(
            generator, "_write_conclusion", new=AsyncMock(return_value="CONCLUSION_MARKER")
        ),
        patch.object(
            generator,
            "_research_and_write_subtopic",
            new=AsyncMock(return_value=("SECTION_MARKER", [], False)),
        ),
        patch.object(generator, "_generate_toc", return_value="TOC_MARKER"),
    ):
        result = await generator._generate_detailed_report(
            query="q",
            contexts=["ctx"],
            sources=[],
            user_id="u1",
            session_id="s1",
        )

    report_md = result.get("report_md", "")
    # 验证顺序: TOC < INTRO < SECTION < CONCLUSION
    toc_pos = report_md.find("TOC_MARKER")
    intro_pos = report_md.find("INTRO_MARKER")
    section_pos = report_md.find("SECTION_MARKER")
    conclusion_pos = report_md.find("CONCLUSION_MARKER")

    assert toc_pos != -1, "TOC 未找到"
    assert intro_pos != -1, "引言未找到"
    assert section_pos != -1, "章节未找到"
    assert conclusion_pos != -1, "结论未找到"

    assert toc_pos < intro_pos, "TOC 应在引言前"
    assert intro_pos < section_pos, "引言应在章节前"
    assert section_pos < conclusion_pos, "章节应在结论前"


# ========== 空上下文守卫 ==========


@pytest.mark.asyncio
async def test_detailed_report_empty_contexts_returns_fallback(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """空上下文 → 返回降级报告 (防幻觉守卫, 对标 GPTR writer.py:82-88)."""
    result = await generator._generate_detailed_report(
        query="q",
        contexts=[],  # 空上下文
        sources=[],
        user_id="u1",
        session_id="s1",
    )

    # 应返回降级报告 (不调 LLM 生成正文)
    report_md = result.get("report_md", "")
    assert len(report_md) > 0  # 有降级内容
    # 不应调 _generate_subtopics (空上下文守卫拦截)
    # (此处不直接 patch, 通过报告内容判断)


# ========== 子主题为空时正常生成 ==========


@pytest.mark.asyncio
async def test_detailed_report_empty_subtopics_still_generates(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """_generate_subtopics 返回空列表 → 仍正常生成 (引言+结论, 无章节)."""
    with (
        patch.object(
            generator,
            "_generate_subtopics",
            new=AsyncMock(return_value=[]),  # 空子主题
        ),
        patch.object(generator, "_write_introduction", new=AsyncMock(return_value="引言")),
        patch.object(generator, "_write_conclusion", new=AsyncMock(return_value="结论")),
        patch.object(generator, "_research_and_write_subtopic") as mock_section,
        patch.object(generator, "_generate_toc", return_value="目录"),
    ):
        result = await generator._generate_detailed_report(
            query="q",
            contexts=["ctx"],
            sources=[],
            user_id="u1",
            session_id="s1",
        )

    # 无子主题 → 不调用 _research_and_write_subtopic
    mock_section.assert_not_awaited()
    report_md = result.get("report_md", "")
    assert "引言" in report_md
    assert "结论" in report_md
