"""单元测试: SourceCurator.curate_sources 来源策展 (补充).

验证 src/skills/researcher/source_curator.py 的 curate_sources 方法:
- LLM 评分映射回原 sources + 综合排序 (relevance*0.6 + credibility*0.4)
- 低可信度来源过滤 (combined_score 排序后截断 max_results)
- 重复 URL 去重 (LLM 仅评分唯一来源时, 映射逻辑自动去重)
- 相同分数保持原始顺序 (Python stable sort)
- 空输入返回空列表

补充 tests/unit/test_skills_source_curator.py (仅测 _score_credibility 纯函数).

AGENTS.md 第 13 章: 单元测试不依赖外部服务 (LLM/PromptFamily 全部 mock).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings
from src.llm.client import LLMResponse
from src.skills.researcher.source_curator import SourceCurator

pytestmark = pytest.mark.unit


@pytest.fixture()
def settings() -> Settings:
    """构造最小 Settings (跳过 .env 加载)."""
    return Settings(_env_file=None)


@pytest.fixture()
def mock_llm() -> MagicMock:
    """Mock LLMClient (achat 为 AsyncMock)."""
    llm = MagicMock()
    llm.achat = AsyncMock()
    return llm


@pytest.fixture()
def mock_prompt_family() -> MagicMock:
    """Mock PromptFamily (curator_prompt 返回字符串)."""
    pf = MagicMock()
    pf.curator_prompt.return_value = "test curator prompt"
    return pf


@pytest.fixture()
def curator(
    settings: Settings,
    mock_llm: MagicMock,
    mock_prompt_family: MagicMock,
) -> SourceCurator:
    """构造 SourceCurator (依赖全部 mock)."""
    return SourceCurator(
        settings=settings,
        llm=mock_llm,
        prompt_family=mock_prompt_family,
    )


# ========== curate_sources() 综合测试 ==========


@pytest.mark.asyncio
async def test_source_curator_curate_sources_filters_low_credibility(
    curator: SourceCurator,
    mock_llm: MagicMock,
) -> None:
    """测试低可信度来源被过滤 (combined_score 排序后截断 max_results).

    combined_score = relevance * 0.6 + credibility * 0.4:
    - arxiv 来源: credibility ~0.95, LLM score 8 -> relevance=0.8
      combined = 0.8*0.6 + 0.95*0.4 = 0.48 + 0.38 = 0.86
    - 随机博客: credibility ~0.5, LLM score 8 -> relevance=0.8
      combined = 0.8*0.6 + 0.5*0.4 = 0.48 + 0.20 = 0.68

    max_results=1 时, 高可信度 arxiv 来源保留, 低可信度博客被截断.
    """
    sources = [
        {
            "title": "arxiv 论文",
            "url": "https://arxiv.org/abs/2401.00001",
            "snippet": "a" * 500,  # 内容 >= 200 避免短内容扣分
        },
        {
            "title": "随机博客",
            "url": "https://random-blog.example.com/post",
            "snippet": "a" * 500,
        },
    ]

    # LLM 对两个来源给相同评分 (8 分), 让 credibility 成为区分因素
    mock_llm.achat.return_value = LLMResponse(
        content=(
            '[{"index": 1, "score": 8, "reason": "相关"}, '
            '{"index": 2, "score": 8, "reason": "相关"}]'
        ),
        model="test",
    )

    result = await curator.curate_sources("test query", sources, max_results=1)

    # max_results=1, 仅返回 combined_score 最高的来源
    assert len(result) == 1
    # arxiv 可信度高 (0.95), combined_score 更高, 被保留
    assert result[0]["url"] == "https://arxiv.org/abs/2401.00001"
    # 验证评分字段存在
    assert "credibility_score" in result[0]
    assert "combined_score" in result[0]
    assert result[0]["credibility_score"] >= 0.9
    assert result[0]["combined_score"] > 0.8


@pytest.mark.asyncio
async def test_source_curator_curate_sources_deduplicates(
    curator: SourceCurator,
    mock_llm: MagicMock,
) -> None:
    """测试相同 URL 的来源在 LLM 评分映射后只保留一次.

    curate_sources 按 LLM 返回的 index 映射回原 sources:
    - 若 LLM 仅返回唯一来源的 index (跳过重复 URL), 映射后无重复.
    - 3 个来源中 2 个 URL 相同, LLM 仅评分 index 1 和 3 -> 输出 2 条.
    """
    sources = [
        {"title": "src1", "url": "https://example.com/dup", "snippet": "a" * 500},
        {"title": "src2", "url": "https://example.com/dup", "snippet": "a" * 500},
        {"title": "src3", "url": "https://example.com/unique", "snippet": "a" * 500},
    ]

    # LLM 仅返回 index 1 和 3 的评分 (跳过重复的 index 2)
    mock_llm.achat.return_value = LLMResponse(
        content=(
            '[{"index": 1, "score": 7, "reason": "..."}, {"index": 3, "score": 6, "reason": "..."}]'
        ),
        model="test",
    )

    result = await curator.curate_sources("test query", sources, max_results=10)

    # 仅 index 1 和 3 被映射, 无重复 URL
    assert len(result) == 2
    urls = [r["url"] for r in result]
    assert urls.count("https://example.com/dup") == 1
    assert urls.count("https://example.com/unique") == 1


@pytest.mark.asyncio
async def test_source_curator_curate_sources_preserves_order(
    curator: SourceCurator,
    mock_llm: MagicMock,
) -> None:
    """测试相同综合分数时保持原始顺序 (Python stable sort).

    3 个来源域名相同 (example.com) -> credibility 相同,
    LLM 返回相同 score -> combined_score 相同,
    curated.sort(key=combined_score, reverse=True) 为稳定排序, 保持原始顺序.
    """
    sources = [
        {"title": "first", "url": "https://example.com/1", "snippet": "a" * 500},
        {"title": "second", "url": "https://example.com/2", "snippet": "a" * 500},
        {"title": "third", "url": "https://example.com/3", "snippet": "a" * 500},
    ]

    # LLM 返回相同 score (5 分), credibility 也相同 (相同域名)
    mock_llm.achat.return_value = LLMResponse(
        content=(
            '[{"index": 1, "score": 5, "reason": "..."}, '
            '{"index": 2, "score": 5, "reason": "..."}, '
            '{"index": 3, "score": 5, "reason": "..."}]'
        ),
        model="test",
    )

    result = await curator.curate_sources("test query", sources, max_results=10)

    # 相同 combined_score, stable sort 保持原始顺序
    assert len(result) == 3
    assert result[0]["title"] == "first"
    assert result[1]["title"] == "second"
    assert result[2]["title"] == "third"


@pytest.mark.asyncio
async def test_source_curator_curate_sources_empty_input(
    curator: SourceCurator,
    mock_llm: MagicMock,
) -> None:
    """测试空来源列表返回空列表 (不调用 LLM)."""
    result = await curator.curate_sources("test query", [], max_results=10)

    assert result == []
    # LLM 不应被调用 (空输入提前返回)
    mock_llm.achat.assert_not_awaited()
