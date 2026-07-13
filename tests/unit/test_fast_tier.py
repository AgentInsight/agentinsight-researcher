"""单元测试: ReportGenerator LLM tier 策略.

验证 src/skills/researcher/report_generator.py 的 tier 分配:
- 报告写作 (basic_report/引言/章节/结论) 统一用 SMART tier (deepseek-v4-flash)
- 报告写作启用推理模式 (reasoning_effort=high)
- 子主题生成用 STRATEGIC tier (deepseek-v4-pro, 规划任务)
- FAST tier 不用于产生报告内容 (质量不足, 仅用于轻量任务如意图分类)

LLM 分层: 报告写作用 smart_llm, 规划/推理任务用 strategic_llm.
单元测试不依赖外部服务 (LLM 全部 mock).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.llm.client import LLMTier
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
    llm.achat = AsyncMock()
    return llm


@pytest.fixture()
def generator(settings: Settings, mock_llm: MagicMock) -> ReportGenerator:
    """构造 ReportGenerator (依赖 mock)."""
    return ReportGenerator(settings=settings, llm=mock_llm)


# ========== 报告写作统一用 SMART tier ==========


@pytest.mark.asyncio
async def test_basic_report_uses_smart_tier(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """基础报告生成 → 用 SMART tier (deepseek-v4-flash).

    报告写作用 smart_llm, 不用 fast_llm (质量不足).
    """
    with patch.object(generator, "_achat_with_retry") as mock_retry:
        mock_retry.return_value = "smart tier report"

        await generator._generate_basic_report(
            query="test",
            contexts=["ctx"],
            sources=[],
            total_words=1000,  # 短报告也用 SMART
            user_id="u1",
            session_id="s1",
        )

    # 只调用一次, 且 tier 为 SMART
    assert mock_retry.call_count == 1
    call_kwargs = mock_retry.call_args.kwargs
    assert call_kwargs["tier"] == LLMTier.SMART


@pytest.mark.asyncio
async def test_long_report_uses_smart_tier(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """长报告 (word_limit > 2000) → 用 SMART tier.

    报告写作用 smart_llm (deepseek-v4-flash).
    """
    with patch.object(generator, "_achat_with_retry") as mock_retry:
        mock_retry.return_value = "smart tier report"

        await generator._generate_basic_report(
            query="test",
            contexts=["ctx"],
            sources=[],
            total_words=3000,
            user_id="u1",
            session_id="s1",
        )

    assert mock_retry.call_count == 1
    assert mock_retry.call_args.kwargs["tier"] == LLMTier.SMART


# ========== 报告写作启用推理模式 ==========


@pytest.mark.asyncio
async def test_basic_report_enables_reasoning(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """基础报告生成 → 启用推理模式 (reasoning_effort=high).

    DeepSeek V4 Flash 是推理模型, 启用 reasoning_effort 确保深度推理.
    与图片生成 (thinking=disabled) 不同, 报告内容生成需要推理.
    """
    with patch.object(generator, "_achat_with_retry") as mock_retry:
        mock_retry.return_value = "report"

        await generator._generate_basic_report(
            query="test",
            contexts=["ctx"],
            sources=[],
            total_words=1000,
            user_id="u1",
            session_id="s1",
        )

    call_kwargs = mock_retry.call_args.kwargs
    assert call_kwargs["reasoning_effort"] == generator.settings.deep_research_reasoning_effort


@pytest.mark.asyncio
async def test_write_introduction_enables_reasoning(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """报告引言 → 启用推理模式."""
    with patch.object(generator, "_achat_with_retry") as mock_retry:
        mock_retry.return_value = "## 引言\n\n内容"

        await generator._write_introduction(
            query="test",
            context="ctx",
            references="",
            role_persona="analyst",
            user_id="u1",
            session_id="s1",
        )

    call_kwargs = mock_retry.call_args.kwargs
    assert call_kwargs["tier"] == LLMTier.SMART
    assert call_kwargs["reasoning_effort"] == generator.settings.deep_research_reasoning_effort


@pytest.mark.asyncio
async def test_write_section_enables_reasoning(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """报告章节正文 → 启用推理模式."""
    with patch.object(generator, "_achat_with_retry") as mock_retry:
        mock_retry.return_value = "## 章节\n\n内容"

        await generator._write_section(
            topic="topic",
            context="ctx",
            references="",
            role_persona="analyst",
            user_id="u1",
            session_id="s1",
        )

    call_kwargs = mock_retry.call_args.kwargs
    assert call_kwargs["tier"] == LLMTier.SMART
    assert call_kwargs["reasoning_effort"] == generator.settings.deep_research_reasoning_effort


@pytest.mark.asyncio
async def test_write_conclusion_enables_reasoning(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """报告结论 → 启用推理模式."""
    with patch.object(generator, "_achat_with_retry") as mock_retry:
        mock_retry.return_value = "## 结论\n\n内容"

        await generator._write_conclusion(
            query="test",
            sections=["section1"],
            role_persona="analyst",
            user_id="u1",
            session_id="s1",
        )

    call_kwargs = mock_retry.call_args.kwargs
    assert call_kwargs["tier"] == LLMTier.SMART
    assert call_kwargs["reasoning_effort"] == generator.settings.deep_research_reasoning_effort


# ========== 子主题生成用 STRATEGIC tier ==========


@pytest.mark.asyncio
async def test_generate_subtopics_uses_strategic_tier(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """子主题生成 → 用 STRATEGIC tier (deepseek-v4-pro, 规划任务).

    子查询生成用 strategic_llm (deepseek-v4-pro).
    """
    with patch.object(generator, "_achat_with_retry") as mock_retry:
        mock_retry.return_value = '["topic1", "topic2"]'

        await generator._generate_subtopics(
            query="test",
            context="ctx",
            role_persona="analyst",
            user_id="u1",
            session_id="s1",
        )

    call_kwargs = mock_retry.call_args.kwargs
    assert call_kwargs["tier"] == LLMTier.STRATEGIC
    assert call_kwargs["reasoning_effort"] == generator.settings.deep_research_reasoning_effort


# ========== SMART tier 用的 token 上限 ==========


@pytest.mark.asyncio
async def test_smart_tier_uses_smart_token_limit(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """SMART tier 调用使用 settings.smart_token_limit."""
    with patch.object(generator, "_achat_with_retry") as mock_retry:
        mock_retry.return_value = "report"

        await generator._generate_basic_report(
            query="test",
            contexts=["ctx"],
            sources=[],
            total_words=1000,
            user_id="u1",
            session_id="s1",
        )

    call_kwargs = mock_retry.call_args.kwargs
    assert call_kwargs["max_tokens"] == generator.settings.smart_token_limit
