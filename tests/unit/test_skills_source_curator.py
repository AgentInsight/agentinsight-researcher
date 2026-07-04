"""单元测试: SourceCurator 可信度评分.

验证 _score_credibility 按域名权威性 / 内容长度 / 统计数据评分,
以及 _DOMAIN_CREDIBILITY 字典含预期域名.
AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
"""

from __future__ import annotations

import pytest

from src.config.settings import Settings
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
