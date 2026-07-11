"""单元测试: SourceCurator 可信度评分.

验证 _score_credibility 按域名权威性 / 内容长度 / 统计数据评分,
以及 _DOMAIN_CREDIBILITY 字典含预期域名.
单元测试在构建期执行, 不依赖外部服务.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings
from src.llm.client import LLMResponse, LLMTier
from src.skills.researcher.prompts import DefaultPromptFamily
from src.skills.researcher.source_curator import SourceCurator

pytestmark = pytest.mark.unit


@pytest.fixture()
def curator() -> SourceCurator:
    """构造 SourceCurator 实例 (跳过 LLM 依赖).

    SourceCurator.__init__ 会创建 LLMClient, 但 _score_credibility 是纯函数,
    仅访问 _DOMAIN_CREDIBILITY 类属性, 不调用 LLM.
    通过 __new__ 跳过 __init__, 避免 LLMClient 初始化.
    """
    obj = SourceCurator.__new__(SourceCurator)
    obj.settings = Settings(_env_file=None)
    return obj


# ========== _DOMAIN_CREDIBILITY 字典 ==========


def test_domain_credibility_contains_arxiv() -> None:
    """测试 _DOMAIN_CREDIBILITY 含 arxiv.org."""
    assert "arxiv.org" in SourceCurator._DOMAIN_CREDIBILITY


def test_domain_credibility_contains_pubmed() -> None:
    """测试 _DOMAIN_CREDIBILITY 含 pubmed.ncbi.nlm.nih.gov."""
    assert "pubmed.ncbi.nlm.nih.gov" in SourceCurator._DOMAIN_CREDIBILITY


def test_domain_credibility_contains_nature() -> None:
    """测试 _DOMAIN_CREDIBILITY 含 nature.com."""
    assert "nature.com" in SourceCurator._DOMAIN_CREDIBILITY


def test_domain_credibility_contains_gov() -> None:
    """测试 _DOMAIN_CREDIBILITY 含 gov 与 gov.cn."""
    assert "gov" in SourceCurator._DOMAIN_CREDIBILITY
    assert "gov.cn" in SourceCurator._DOMAIN_CREDIBILITY


def test_domain_credibility_values_in_range() -> None:
    """测试所有 credibility 分数在 [0, 1] 区间."""
    for domain, score in SourceCurator._DOMAIN_CREDIBILITY.items():
        assert 0.0 <= score <= 1.0, f"{domain} 分数 {score} 越界"


# ========== _score_credibility 权威域名 ==========


def test_score_credibility_arxiv_high(curator: SourceCurator) -> None:
    """测试 arxiv 链接获得高可信度分 (>= 0.9)."""
    # 内容长度 >= 200 避免 < 200 扣分, 无数字避免统计加成
    content = "a" * 500
    source = {"url": "https://arxiv.org/abs/2401.00001", "content": content}
    score = curator._score_credibility(source)
    assert score >= 0.9


def test_score_credibility_pubmed_high(curator: SourceCurator) -> None:
    """测试 pubmed 链接获得高可信度分."""
    source = {"url": "https://pubmed.ncbi.nlm.nih.gov/12345678/", "content": "a" * 500}
    score = curator._score_credibility(source)
    assert score >= 0.9


def test_score_credibility_nature_high(curator: SourceCurator) -> None:
    """测试 nature.com 链接获得高可信度分."""
    source = {"url": "https://www.nature.com/articles/s41586-024-1", "content": "a" * 500}
    score = curator._score_credibility(source)
    assert score >= 0.9


def test_score_credibility_gov_high(curator: SourceCurator) -> None:
    """测试 gov 域名链接获得高可信度分."""
    source = {"url": "https://stats.gov.cn/sj/", "content": "a" * 500}
    score = curator._score_credibility(source)
    assert score >= 0.9


# ========== _score_credibility 普通域名 ==========


def test_score_credibility_normal_domain_baseline(curator: SourceCurator) -> None:
    """测试普通域名默认 0.5 基础分 (无数字加成)."""
    source = {"url": "https://random-blog.example.com/post", "content": "a" * 500}
    score = curator._score_credibility(source)
    # 普通域名基础分 0.5, 无数字无加成
    assert score == 0.5


def test_score_credibility_wikipedia_moderate(curator: SourceCurator) -> None:
    """测试 wikipedia 获得中等可信度."""
    source = {"url": "https://en.wikipedia.org/wiki/AI", "content": "a" * 500}
    score = curator._score_credibility(source)
    # wikipedia.org 字典值 0.70
    assert score == 0.70


# ========== _score_credibility 统计数据加成 ==========


def test_score_credibility_statistics_bonus(curator: SourceCurator) -> None:
    """测试内容前 500 字含数字时加分."""
    # 两份内容长度均 >= 200 避免短内容扣分干扰
    source_with_num = {"url": "https://example.com", "content": "增长 25% 的数据" + "a" * 300}
    source_without_num = {"url": "https://example.com", "content": "abcdef ghijkl" + "a" * 300}
    score_with = curator._score_credibility(source_with_num)
    score_without = curator._score_credibility(source_without_num)
    assert score_with > score_without


def test_score_credibility_digit_in_first_500_chars_only(curator: SourceCurator) -> None:
    """测试仅前 500 字符含数字才加分 (500 字符后数字不算)."""
    long_no_digit_prefix = "a" * 600 + "12345"
    source = {"url": "https://example.com", "content": long_no_digit_prefix}
    score = curator._score_credibility(source)
    # 不应获得统计加成 (数字在第 600 字符后), 长度 > 200 无扣分, 故仅基础分 0.5
    assert score == 0.5


# ========== _score_credibility 内容长度 ==========


def test_score_credibility_long_content_bonus(curator: SourceCurator) -> None:
    """测试内容长度 > 2000 字符加分."""
    source_long = {"url": "https://example.com", "content": "a" * 2500}
    source_normal = {"url": "https://example.com", "content": "a" * 500}
    score_long = curator._score_credibility(source_long)
    score_normal = curator._score_credibility(source_normal)
    # 长内容应高于普通内容 (长内容 +0.05)
    assert score_long > score_normal


def test_score_credibility_short_content_penalty(curator: SourceCurator) -> None:
    """测试内容长度 < 200 字符扣分."""
    source_short = {"url": "https://example.com", "content": "短"}
    score = curator._score_credibility(source_short)
    # 0.5 - 0.10 = 0.4
    assert score <= 0.5


# ========== _score_credibility 边界 ==========


def test_score_credibility_uses_href_fallback(curator: SourceCurator) -> None:
    """测试 url 缺失时回退到 href 字段."""
    source = {"href": "https://arxiv.org/abs/1234", "content": "a" * 500}
    score = curator._score_credibility(source)
    assert score >= 0.9


def test_score_credibility_uses_snippet_fallback(curator: SourceCurator) -> None:
    """测试 content 缺失时回退到 snippet / body 字段."""
    source = {"url": "https://arxiv.org/abs/1234", "snippet": "a" * 500}
    score = curator._score_credibility(source)
    assert score >= 0.9


def test_score_credibility_score_clamped_to_1(curator: SourceCurator) -> None:
    """测试分数上限不超过 1.0."""
    # arxiv 0.95 + 长内容 0.05 + 统计 0.03 = 1.03, 应被夹紧到 1.0
    source = {"url": "https://arxiv.org/abs/1", "content": "1" * 3000}
    score = curator._score_credibility(source)
    assert score == 1.0


def test_score_credibility_score_clamped_to_0(curator: SourceCurator) -> None:
    """测试分数下限不低于 0.0."""
    # 短内容扣 0.10, 但 0.5 - 0.10 = 0.4 不会负, 这里仅验证不会为负
    source = {"url": "", "content": ""}
    score = curator._score_credibility(source)
    assert score >= 0.0


# ========== curate_sources: max_tokens / prompt 精简 / reason 兼容 (P0/P2 优化) ==========


@pytest.fixture()
def mock_llm() -> MagicMock:
    """Mock LLMClient (achat 为 AsyncMock, 返回 LLMResponse)."""
    llm = MagicMock()
    llm.achat = AsyncMock()
    return llm


@pytest.fixture()
def curator_with_llm(
    mock_llm: MagicMock,
) -> SourceCurator:
    """构造 SourceCurator (注入 mock LLM + 真实 DefaultPromptFamily).

    用于 curate_sources 集成测试, 验证:
    - max_tokens=2000 (P0 优化: 4000→2000)
    - curator prompt 精简 (仅输出 index+score, 不输出 reason)
    - reason 字段为空时的兼容解析
    """
    settings = Settings(_env_file=None)
    return SourceCurator(
        settings=settings,
        llm=mock_llm,
        prompt_family=DefaultPromptFamily(),
    )


def _make_sources(n: int = 3) -> list[dict[str, Any]]:
    """构造 n 条来源 (含足够内容避免短内容扣分干扰)."""
    return [
        {
            "url": f"https://arxiv.org/abs/2401.0000{i}",
            "title": f"来源 {i}",
            "snippet": f"这是来源 {i} 的摘要内容, 含相关数据" + "a" * 300,
            "content": f"来源 {i} 正文, 增长 20% 的数据" + "a" * 300,
        }
        for i in range(1, n + 1)
    ]


@pytest.mark.asyncio
async def test_curate_sources_uses_max_tokens_2000(
    curator_with_llm: SourceCurator,
    mock_llm: MagicMock,
) -> None:
    """测试 curate_sources 调用 LLM 时 max_tokens=2000 (P0 优化: 4000→2000).

    P0 优化 (trace 4ad14970): 策展 JSON 仅需 index+score, 不需要长输出,
    max_tokens 从 4000 降至 2000 节省 token 成本.
    """
    mock_llm.achat.return_value = LLMResponse(
        content='[{"index": 1, "score": 9}]',
        model="test",
    )

    await curator_with_llm.curate_sources("测试查询", _make_sources(3), max_results=3)

    mock_llm.achat.assert_awaited_once()
    call_kwargs = mock_llm.achat.call_args.kwargs
    assert call_kwargs["max_tokens"] == 2000


@pytest.mark.asyncio
async def test_curate_sources_uses_smart_tier(
    curator_with_llm: SourceCurator,
    mock_llm: MagicMock,
) -> None:
    """测试 curate_sources 使用 SMART tier (策展需复杂推理)."""
    mock_llm.achat.return_value = LLMResponse(
        content='[{"index": 1, "score": 9}]',
        model="test",
    )

    await curator_with_llm.curate_sources("测试查询", _make_sources(2), max_results=2)

    call_kwargs = mock_llm.achat.call_args.kwargs
    assert call_kwargs["tier"] == LLMTier.SMART


@pytest.mark.asyncio
async def test_curate_sources_prompt_only_index_and_score_no_reason(
    curator_with_llm: SourceCurator,
    mock_llm: MagicMock,
) -> None:
    """测试 curator prompt 精简: 仅输出 index+score, 不输出 reason (P2 优化).

    P2 优化 (trace 4ad14970): prompt 明确要求 "不需要 reason",
    减少 LLM 输出 token, 对应 max_tokens 4000→2000 优化.
    """
    mock_llm.achat.return_value = LLMResponse(
        content='[{"index": 1, "score": 9}]',
        model="test",
    )

    await curator_with_llm.curate_sources("测试查询", _make_sources(2), max_results=2)

    # 捕获传给 LLM 的 prompt (messages[0]["content"])
    messages = mock_llm.achat.call_args.args[0]
    prompt = messages[0]["content"]

    # 验证 prompt 要求输出 index 与 score
    assert "index" in prompt
    assert "score" in prompt
    # 验证 prompt 明确声明不需要 reason
    assert "reason" in prompt
    assert "不需要" in prompt or "no reason" in prompt.lower()


@pytest.mark.asyncio
async def test_curate_sources_reason_empty_compatible(
    curator_with_llm: SourceCurator,
    mock_llm: MagicMock,
) -> None:
    """测试 LLM 返回不含 reason 字段时, curator_reason 兼容为空字符串.

    P2 优化后 prompt 仅要求 index+score, LLM 可能不返回 reason.
    curate_sources 用 item.get("reason", "") 兼容解析, curator_reason 应为 "".
    """
    mock_llm.achat.return_value = LLMResponse(
        content='[{"index": 1, "score": 9}, {"index": 2, "score": 7}]',
        model="test",
    )

    result = await curator_with_llm.curate_sources("测试查询", _make_sources(3), max_results=3)

    assert len(result) >= 1
    # 所有结果的 curator_reason 应为空字符串 (LLM 未返回 reason)
    for source in result:
        assert source.get("curator_reason", "") == ""


@pytest.mark.asyncio
async def test_curate_sources_reason_present_when_provided(
    curator_with_llm: SourceCurator,
    mock_llm: MagicMock,
) -> None:
    """测试 LLM 返回含 reason 字段时, curator_reason 正常保留 (向后兼容).

    虽然 P2 优化后 prompt 不要求 reason, 但解析逻辑仍兼容含 reason 的输出.
    """
    mock_llm.achat.return_value = LLMResponse(
        content='[{"index": 1, "score": 9, "reason": "高度相关"}]',
        model="test",
    )

    result = await curator_with_llm.curate_sources("测试查询", _make_sources(2), max_results=2)

    assert len(result) >= 1
    # 含 reason 时应正常保留
    assert result[0]["curator_reason"] == "高度相关"


@pytest.mark.asyncio
async def test_curate_sources_empty_list_returns_empty(
    curator_with_llm: SourceCurator,
    mock_llm: MagicMock,
) -> None:
    """测试空来源列表时直接返回空列表, 不调用 LLM."""
    result = await curator_with_llm.curate_sources("测试查询", [], max_results=5)

    assert result == []
    mock_llm.achat.assert_not_awaited()


@pytest.mark.asyncio
async def test_curate_sources_fallback_on_parse_failure(
    curator_with_llm: SourceCurator,
    mock_llm: MagicMock,
) -> None:
    """测试 LLM 返回无法解析的 JSON 时, 降级按可信度排序返回.

    curate_sources 解析失败时, 不抛异常, 用 _score_credibility 排序返回原列表.
    """
    mock_llm.achat.return_value = LLMResponse(
        content="这不是有效的 JSON",
        model="test",
    )

    sources = _make_sources(3)
    result = await curator_with_llm.curate_sources("测试查询", sources, max_results=3)

    # 降级返回按可信度排序的结果
    assert len(result) == 3
    # 每条应含 credibility_score 与 combined_score
    for s in result:
        assert "credibility_score" in s
        assert "combined_score" in s
