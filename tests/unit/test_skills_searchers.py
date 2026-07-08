"""单元测试: 搜索引擎注册中心与 BaseSearcher 纯函数.

验证 detect_region / BaseSearcher._normalize_result /
_filter_by_domains / get_registered_searchers / register_searcher.
AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
"""

from __future__ import annotations

import pytest

from src.config.settings import Settings
from src.skills.researcher.searchers import (
    BaseSearcher,
    SearchRegion,
    detect_region,
    get_registered_searchers,
    register_searcher,
)

pytestmark = pytest.mark.unit


# ========== detect_region ==========


def test_detect_region_chinese_returns_cn() -> None:
    """测试中文为主查询 → CN."""
    assert detect_region("中国新能源汽车行业发展趋势") == SearchRegion.CN


def test_detect_region_high_chinese_ratio() -> None:
    """测试中文字符比例 > 30% → CN."""
    result = detect_region("研究 AI 在医疗的应用")  # 大部分中文字符
    assert result == SearchRegion.CN


def test_detect_region_arxiv_keyword_returns_academic() -> None:
    """测试 'arxiv' 学术关键词 → ACADEMIC."""
    assert detect_region("transformer architecture arxiv paper") == SearchRegion.ACADEMIC


def test_detect_region_pubmed_keyword_returns_academic() -> None:
    """测试 'pubmed' 学术关键词 → ACADEMIC."""
    assert detect_region("cancer immunotherapy pubmed") == SearchRegion.ACADEMIC


def test_detect_region_paper_keyword_returns_academic() -> None:
    """测试 '论文' 中文学术关键词 → ACADEMIC."""
    assert detect_region("深度学习论文综述") == SearchRegion.ACADEMIC


def test_detect_region_research_keyword_returns_academic() -> None:
    """测试 'research' 学术关键词 → ACADEMIC."""
    assert detect_region("AI research trends") == SearchRegion.ACADEMIC


def test_detect_region_english_returns_global() -> None:
    """测试纯英文查询 → GLOBAL."""
    assert detect_region("analyze the global semiconductor market") == SearchRegion.GLOBAL


def test_detect_region_empty_returns_auto() -> None:
    """测试空查询 → AUTO."""
    assert detect_region("") == SearchRegion.AUTO


def test_detect_region_mixed_low_chinese_returns_auto() -> None:
    """测试中文字符比例 <= 30% 且非纯英文 → AUTO."""
    # 中文字符 1 个, 总字符 10+, 比例 ~10%, 应为 AUTO
    result = detect_region("analyze the 中 market trends")
    assert result == SearchRegion.AUTO


def test_detect_region_academic_keyword_priority_over_chinese() -> None:
    """测试学术关键词优先级高于中文检测."""
    # 含 '论文' 关键词, 即使中文为主也应判 ACADEMIC
    result = detect_region("关于人工智能的论文综述")
    assert result == SearchRegion.ACADEMIC


# ========== BaseSearcher._normalize_result ==========


@pytest.fixture()
def searcher() -> BaseSearcher:
    """构造 BaseSearcher 实例 (隔离 .env)."""
    return BaseSearcher(settings=Settings(_env_file=None))


def test_normalize_result_returns_dict(searcher: BaseSearcher) -> None:
    """测试 _normalize_result 返回含 5 个固定字段的 dict."""
    result = searcher._normalize_result("title", "https://x.com", "snippet")
    assert isinstance(result, dict)
    assert set(result.keys()) == {"title", "url", "snippet", "source", "region"}


def test_normalize_result_field_values(searcher: BaseSearcher) -> None:
    """测试 _normalize_result 字段值正确."""
    result = searcher._normalize_result("My Title", "https://example.com", "My Snippet")
    assert result["title"] == "My Title"
    assert result["url"] == "https://example.com"
    assert result["snippet"] == "My Snippet"
    assert result["source"] == "base"  # BaseSearcher.name = "base"
    assert result["region"] == SearchRegion.AUTO.value


def test_normalize_result_empty_strings(searcher: BaseSearcher) -> None:
    """测试空 title/url/snippet 返回空串 (而非 None)."""
    result = searcher._normalize_result("", "", "")
    assert result["title"] == ""
    assert result["url"] == ""
    assert result["snippet"] == ""


def test_normalize_result_none_inputs_become_empty(searcher: BaseSearcher) -> None:
    """测试 None 输入转为空串."""
    result = searcher._normalize_result(None, None, None)  # type: ignore[arg-type]
    assert result["title"] == ""
    assert result["url"] == ""
    assert result["snippet"] == ""


# ========== BaseSearcher._filter_by_domains ==========


def test_filter_by_domains_none_query_domains_returns_all() -> None:
    """测试 query_domains 为 None 时不过滤."""
    results = [{"url": "https://a.com/1"}, {"url": "https://b.com/2"}]
    filtered = BaseSearcher._filter_by_domains(results, None)
    assert filtered == results


def test_filter_by_domains_empty_query_domains_returns_all() -> None:
    """测试 query_domains 为空列表时不过滤."""
    results = [{"url": "https://a.com/1"}]
    filtered = BaseSearcher._filter_by_domains(results, [])
    assert filtered == results


def test_filter_by_domains_keeps_matching() -> None:
    """测试仅保留 url 含任一白名单域名的结果."""
    results = [
        {"url": "https://arxiv.org/abs/1"},
        {"url": "https://example.com/x"},
        {"url": "https://nature.com/articles/2"},
    ]
    filtered = BaseSearcher._filter_by_domains(results, ["arxiv.org", "nature.com"])
    assert len(filtered) == 2
    assert filtered[0]["url"] == "https://arxiv.org/abs/1"
    assert filtered[1]["url"] == "https://nature.com/articles/2"


def test_filter_by_domains_substring_match() -> None:
    """测试子串匹配 (而非精确域名)."""
    results = [{"url": "https://blog.example.com/post"}]
    filtered = BaseSearcher._filter_by_domains(results, ["example.com"])
    assert len(filtered) == 1  # 'example.com' 是 url 的子串, 保留


def test_filter_by_domains_no_match_returns_empty() -> None:
    """测试无匹配时返回空列表."""
    results = [{"url": "https://random.com/x"}]
    filtered = BaseSearcher._filter_by_domains(results, ["arxiv.org"])
    assert filtered == []


def test_filter_by_domains_empty_url_skipped() -> None:
    """测试 url 缺失的结果不保留."""
    results = [{"title": "no url"}, {"url": "https://arxiv.org/abs/1"}]
    filtered = BaseSearcher._filter_by_domains(results, ["arxiv.org"])
    assert len(filtered) == 1
    assert filtered[0]["url"] == "https://arxiv.org/abs/1"


# ========== get_registered_searchers / register_searcher ==========


def test_get_registered_searchers_returns_dict() -> None:
    """测试 get_registered_searchers 返回 dict (浅拷贝)."""
    registry = get_registered_searchers()
    assert isinstance(registry, dict)


def test_get_registered_searchers_returns_copy() -> None:
    """测试 get_registered_searchers 返回浅拷贝, 修改不影响内部注册表."""
    registry = get_registered_searchers()
    registry["fake_key"] = BaseSearcher  # type: ignore[assignment]
    # 内部注册表不应被污染
    assert "fake_key" not in get_registered_searchers()


def test_register_searcher_adds_to_registry() -> None:
    """测试 register_searcher 注册新 searcher."""

    @register_searcher("test_custom_searcher")
    class _TestSearcher(BaseSearcher):
        name = "test_custom"

    registry = get_registered_searchers()
    assert "test_custom_searcher" in registry
    assert registry["test_custom_searcher"]["class"] is _TestSearcher


def test_register_searcher_returns_class_unchanged() -> None:
    """测试 register_searcher 装饰器返回类本身不变."""

    @register_searcher("test_returns_cls")
    class _TestSearcher(BaseSearcher):
        pass

    # 装饰器应原样返回类
    assert _TestSearcher.name == "base"  # 继承自 BaseSearcher


def test_register_searcher_overrides_existing() -> None:
    """测试重复注册同名 searcher 会覆盖旧值."""

    @register_searcher("test_override")
    class _TestSearcher1(BaseSearcher):
        name = "v1"

    @register_searcher("test_override")
    class _TestSearcher2(BaseSearcher):
        name = "v2"

    registry = get_registered_searchers()
    assert registry["test_override"]["class"] is _TestSearcher2
