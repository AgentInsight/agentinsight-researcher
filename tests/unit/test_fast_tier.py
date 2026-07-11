"""单元测试: ReportGenerator FAST tier 降级.

验证 src/skills/researcher/report_generator.py 的 FAST tier 优化:
- 短报告 (word_limit <= _FAST_TIER_WORD_THRESHOLD=2000) 优先用 FAST tier
- FAST tier 失败 (返回占位文本) → 回退 SMART tier
- 长报告 (word_limit > 阈值) 直接用 SMART tier
- FAST tier 成功 → 不调用 SMART tier (节省成本)

AGENTS.md 第 13 章: 单元测试不依赖外部服务 (LLM 全部 mock).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.llm.client import LLMResponse, LLMTier
from src.skills.researcher.report_generator import (
    _FAST_TIER_WORD_THRESHOLD,
    _SECTION_FAILURE_PLACEHOLDER,
    ReportGenerator,
)

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


# ========== FAST tier 阈值常量 ==========


def test_fast_tier_word_threshold_is_2000() -> None:
    """_FAST_TIER_WORD_THRESHOLD 应为 2000 (默认值)."""
    assert _FAST_TIER_WORD_THRESHOLD == 2000


# ========== FAST tier 优先 (短报告) ==========


@pytest.mark.asyncio
async def test_short_report_uses_fast_tier_first(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """短报告 (word_limit <= 2000) → 优先用 FAST tier, 成功则不调 SMART."""
    word_limit = 1000  # <= 2000 阈值
    fast_response = "fast tier report content"

    # 记录 achat 调用的 tier
    call_tiers: list[LLMTier] = []

    async def _track_achat(
        messages: list[dict[str, str]],
        *,
        tier: LLMTier,
        **kwargs: Any,
    ) -> LLMResponse:
        call_tiers.append(tier)
        return LLMResponse(content=fast_response, model="test")

    mock_llm.achat = _track_achat

    with patch.object(generator, "_achat_with_retry") as mock_retry:
        # _achat_with_retry 直接返回 fast_response (模拟 FAST tier 成功)
        mock_retry.return_value = fast_response

        # 调用 _generate_basic_report (内部根据 total_words 决定 tier)
        await generator._generate_basic_report(
            query="test query",
            contexts=["ctx1"],
            sources=[],
            total_words=word_limit,
            user_id="u1",
            session_id="s1",
        )

    # _achat_with_retry 第一次调用应使用 FAST tier
    first_call = mock_retry.call_args_list[0]
    assert first_call.kwargs["tier"] == LLMTier.FAST
    # FAST 成功 → 不应再调 SMART (只调一次)
    assert mock_retry.call_count == 1


@pytest.mark.asyncio
async def test_long_report_uses_smart_tier_directly(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """长报告 (word_limit > 2000) → 直接用 SMART tier (跳过 FAST)."""
    word_limit = 3000  # > 2000 阈值

    with patch.object(generator, "_achat_with_retry") as mock_retry:
        mock_retry.return_value = "smart tier report"

        await generator._generate_basic_report(
            query="test",
            contexts=["ctx"],
            sources=[],
            total_words=word_limit,
            user_id="u1",
            session_id="s1",
        )

    # 只调用一次, 且 tier 为 SMART
    assert mock_retry.call_count == 1
    call_kwargs = mock_retry.call_args.kwargs
    assert call_kwargs["tier"] == LLMTier.SMART


# ========== FAST tier 失败回退 SMART ==========


@pytest.mark.asyncio
async def test_fast_tier_failure_falls_back_to_smart(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """FAST tier 失败 (返回占位文本) → 回退 SMART tier."""
    word_limit = 1000  # 短报告, 优先 FAST
    smart_response = "smart tier report"

    call_tiers: list[LLMTier] = []

    async def _mock_retry(
        messages: list[dict[str, str]],
        *,
        tier: LLMTier,
        **kwargs: Any,
    ) -> str:
        call_tiers.append(tier)
        if tier == LLMTier.FAST:
            return _SECTION_FAILURE_PLACEHOLDER  # FAST 失败
        return smart_response  # SMART 成功

    with patch.object(generator, "_achat_with_retry", side_effect=_mock_retry):
        result = await generator._generate_basic_report(
            query="test",
            contexts=["ctx"],
            sources=[],
            total_words=word_limit,
            user_id="u1",
            session_id="s1",
        )

    # 第一次 FAST 失败, 第二次 SMART 成功
    assert len(call_tiers) == 2
    assert call_tiers[0] == LLMTier.FAST
    assert call_tiers[1] == LLMTier.SMART
    # 最终用 SMART 的响应 (返回 dict, report_md 为报告内容)
    assert "smart tier report" in result["report_md"]


@pytest.mark.asyncio
async def test_fast_tier_success_does_not_call_smart(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """FAST tier 成功 (非占位文本) → 不调用 SMART (节省成本)."""
    word_limit = 1500  # 短报告

    call_count = 0

    async def _mock_retry(
        messages: list[dict[str, str]],
        *,
        tier: LLMTier,
        **kwargs: Any,
    ) -> str:
        nonlocal call_count
        call_count += 1
        return "fast tier success content"

    with patch.object(generator, "_achat_with_retry", side_effect=_mock_retry):
        await generator._generate_basic_report(
            query="test",
            contexts=["ctx"],
            sources=[],
            total_words=word_limit,
            user_id="u1",
            session_id="s1",
        )

    # FAST 成功 → 只调一次, 不回退 SMART
    assert call_count == 1


# ========== FAST tier 用的 token 上限 ==========


@pytest.mark.asyncio
async def test_fast_tier_uses_fast_token_limit(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """FAST tier 调用使用 settings.fast_token_limit (非 smart_token_limit)."""
    word_limit = 1000

    with patch.object(generator, "_achat_with_retry") as mock_retry:
        mock_retry.return_value = "report"

        await generator._generate_basic_report(
            query="test",
            contexts=["ctx"],
            sources=[],
            total_words=word_limit,
            user_id="u1",
            session_id="s1",
        )

    call_kwargs = mock_retry.call_args.kwargs
    assert call_kwargs["max_tokens"] == generator.settings.fast_token_limit


@pytest.mark.asyncio
async def test_smart_tier_uses_smart_token_limit(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """SMART tier 调用使用 settings.smart_token_limit."""
    word_limit = 3000  # 长报告直接走 SMART

    with patch.object(generator, "_achat_with_retry") as mock_retry:
        mock_retry.return_value = "report"

        await generator._generate_basic_report(
            query="test",
            contexts=["ctx"],
            sources=[],
            total_words=word_limit,
            user_id="u1",
            session_id="s1",
        )

    call_kwargs = mock_retry.call_args.kwargs
    assert call_kwargs["max_tokens"] == generator.settings.smart_token_limit


# ========== 阈值边界 ==========


@pytest.mark.asyncio
async def test_word_limit_at_threshold_uses_fast(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """word_limit == _FAST_TIER_WORD_THRESHOLD → 仍走 FAST (<= 比较)."""
    word_limit = _FAST_TIER_WORD_THRESHOLD  # 边界值

    with patch.object(generator, "_achat_with_retry") as mock_retry:
        mock_retry.return_value = "report"

        await generator._generate_basic_report(
            query="test",
            contexts=["ctx"],
            sources=[],
            total_words=word_limit,
            user_id="u1",
            session_id="s1",
        )

    # 边界值 <= 阈值 → 走 FAST
    assert mock_retry.call_args.kwargs["tier"] == LLMTier.FAST


@pytest.mark.asyncio
async def test_word_limit_above_threshold_uses_smart(
    generator: ReportGenerator,
    mock_llm: MagicMock,
) -> None:
    """word_limit == _FAST_TIER_WORD_THRESHOLD + 1 → 走 SMART."""
    word_limit = _FAST_TIER_WORD_THRESHOLD + 1

    with patch.object(generator, "_achat_with_retry") as mock_retry:
        mock_retry.return_value = "report"

        await generator._generate_basic_report(
            query="test",
            contexts=["ctx"],
            sources=[],
            total_words=word_limit,
            user_id="u1",
            session_id="s1",
        )

    assert mock_retry.call_args.kwargs["tier"] == LLMTier.SMART
