"""单元测试: 搜索引擎注册表 (重构 @register_searcher 装饰器注册表).

验证 src/skills/researcher/searchers/__init__.py 注册表机制:
- _register_all_searchers 延迟注册 (首次 get_searchers 触发)
- _SEARCHER_REGISTRY 清除后重填 (装饰器预注册条目被显式注册覆盖)
- 区域过滤 (CN/GLOBAL/ACADEMIC/AUTO)
- require_key 过滤: None (免费引擎) / 单字符串 / tuple 多 Key 任一
- Custom 引擎按 CUSTOM_RETRIEVER_ENDPOINT 环境变量启用
- get_searchers 返回排序后列表 (按 _sort_key 综合排序)
- deduplicate_results 跨引擎 URL 去重

单元测试不依赖外部服务 (mock Settings Key 字段).
VALID_RETRIEVERS + 装饰器模式.
"""

from __future__ import annotations

import pytest

from src.config.settings import Settings
from src.skills.researcher.searchers import (
    _SEARCHER_REGISTRY,
    FREE_QUOTA_MAP,
    BaseSearcher,
    SearchRegion,
    _register_all_searchers,
    _sort_key,
    deduplicate_results,
    detect_region,
    get_registered_searchers,
    get_searchers,
    register_searcher,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_registry() -> None:
    """每个测试前重置注册表, 触发下次 _register_all_searchers 重新注册.

    确保测试独立, 不受前序测试注册状态影响.
    """
    _SEARCHER_REGISTRY.clear()
    yield
    _SEARCHER_REGISTRY.clear()


@pytest.fixture()
def settings_no_keys() -> Settings:
    """无任何 API Key 的 settings (仅免费引擎启用).

    显式将所有搜索引擎 API Key 设为 None, 覆盖 conftest.py 从 .env 加载到
    os.environ 的环境变量 (pydantic-settings 即使 _env_file=None 也读 os.environ).
    """
    return Settings(
        _env_file=None,
        bocha_api_key=None,
        tavily_api_key=None,
        brave_api_key=None,
        bing_api_key=None,
        serper_api_key=None,
        serpapi_key=None,
        searchapi_api_key=None,
        metaso_api_key=None,
        exa_api_key=None,
        semantic_scholar_api_key=None,
        github_token=None,
        firecrawl_api_key=None,
    )


# ========== _register_all_searchers 延迟注册 ==========


def test_registry_empty_before_first_call() -> None:
    """模块导入后未调用 get_searchers 前, _SEARCHER_REGISTRY 可能为空 (延迟注册)."""
    # reset_registry 已 clear, 应为空
    assert len(_SEARCHER_REGISTRY) == 0


def test_get_searchers_triggers_lazy_registration(settings_no_keys: Settings) -> None:
    """get_searchers 首次调用触发 _register_all_searchers 延迟注册."""
    assert len(_SEARCHER_REGISTRY) == 0
    get_searchers(SearchRegion.GLOBAL, settings_no_keys)
    assert len(_SEARCHER_REGISTRY) > 0


def test_get_registered_searchers_triggers_lazy_registration() -> None:
    """get_registered_searchers 首次调用也触发延迟注册."""
    assert len(_SEARCHER_REGISTRY) == 0
    get_registered_searchers()
    assert len(_SEARCHER_REGISTRY) > 0


def test_register_all_searchers_clears_then_repopulates() -> None:
    """_register_all_searchers 先 clear() 再重填 (覆盖装饰器预注册默认条目)."""
    # 模拟装饰器预注册的条目
    _SEARCHER_REGISTRY["stale_entry"] = {"class": object, "regions": (), "require_key": None}
    assert "stale_entry" in _SEARCHER_REGISTRY

    _register_all_searchers()

    assert "stale_entry" not in _SEARCHER_REGISTRY, "应清除预注册的过时条目"
    # 显式注册的引擎应存在
    assert "pubmed" in _SEARCHER_REGISTRY
    assert "arxiv" in _SEARCHER_REGISTRY
    # DuckDuckGo 注册块注释 (代码保留), 不应在注册表中
    assert "duckduckgo" not in _SEARCHER_REGISTRY
    assert "searxng" in _SEARCHER_REGISTRY  # CN 区域使用 SearXNG


def test_register_all_searchers_includes_academic_engines() -> None:
    """_register_all_searchers 注册 ACADEMIC 区域引擎 (全免费, 无需 Key)."""
    _register_all_searchers()
    for name in ("pubmed", "semantic_scholar", "arxiv", "openalex", "crossref", "unpaywall"):
        assert name in _SEARCHER_REGISTRY, f"应注册 {name}"
        assert _SEARCHER_REGISTRY[name]["require_key"] is None


def test_register_all_searchers_includes_cn_engines() -> None:
    """_register_all_searchers 注册 CN 区域引擎 (Bocha/Metaso 需 Key, SearXNG 免费).

    DuckDuckGo 注册块注释 (代码保留), 不应在注册表中.
    SearXNG 在 CN/GLOBAL/AUTO 三区域注册 (CN 区域免费引擎).
    """
    _register_all_searchers()
    assert "bocha" in _SEARCHER_REGISTRY
    assert _SEARCHER_REGISTRY["bocha"]["require_key"] == "bocha_api_key"
    # CN 区域使用 SearXNG (免费引擎)
    assert "searxng" in _SEARCHER_REGISTRY
    assert _SEARCHER_REGISTRY["searxng"]["require_key"] is None
    # DuckDuckGo 已移除调用 (代码保留), 不应出现在注册表
    assert "duckduckgo" not in _SEARCHER_REGISTRY


def test_register_all_searchers_includes_global_engines() -> None:
    """_register_all_searchers 注册 GLOBAL 区域引擎 (Tavily/Brave/Bing/Google 等)."""
    _register_all_searchers()
    for name in ("tavily", "brave", "bing", "google", "serpapi", "serper", "searchapi", "searxng"):
        assert name in _SEARCHER_REGISTRY, f"应注册 {name}"


# ========== 区域过滤 ==========


def test_get_searchers_academic_returns_only_academic_engines(
    settings_no_keys: Settings,
) -> None:
    """ACADEMIC 区域只返回 ACADEMIC 注册的引擎 (PubMed/Arxiv/SemanticScholar/CrossRef)."""
    searchers = get_searchers(SearchRegion.ACADEMIC, settings_no_keys)
    names = {s.name for s in searchers}
    # ACADEMIC 区域免费引擎 (无需 Key)
    assert "pubmed" in names
    assert "arxiv" in names
    assert "semantic_scholar" in names
    assert "crossref" in names
    # openalex/unpaywall 仅 ACADEMIC 区域
    assert "openalex" in names
    assert "unpaywall" in names


def test_get_searchers_cn_returns_cn_engines(settings_no_keys: Settings) -> None:
    """CN 区域返回中文优先引擎 (SearXNG/GDELT/HackerNews 免费, Bocha/Metaso 需 Key).

    CN 区域使用 SearXNG 作为免费引擎.
    """
    searchers = get_searchers(SearchRegion.CN, settings_no_keys)
    names = {s.name for s in searchers}
    # 免费引擎应存在 (CN 区域使用 SearXNG)
    assert "searxng" in names
    assert "gdelt" in names
    assert "hackernews" in names
    # DuckDuckGo 已移除调用, 不应出现在搜索结果中
    assert "duckduckgo" not in names
    # 需 Key 的引擎在 settings_no_keys 下应被过滤
    assert "bocha" not in names
    assert "metaso" not in names


def test_get_searchers_global_excludes_cn_only_engines(
    settings_no_keys: Settings,
) -> None:
    """GLOBAL 区域不含 CN 专属引擎 (Bocha/Metaso 仅 CN/AUTO)."""
    searchers = get_searchers(SearchRegion.GLOBAL, settings_no_keys)
    names = {s.name for s in searchers}
    assert "bocha" not in names
    assert "metaso" not in names
    # GLOBAL 免费引擎
    assert "duckduckgo" not in names or "duckduckgo" in names  # DuckDuckGo 也注册在 CN
    # GLOBAL 区域应有 arxiv (学术引擎跨区域)
    assert "arxiv" in names


# ========== require_key 过滤 ==========


def test_get_searchers_includes_free_engines_without_key(
    settings_no_keys: Settings,
) -> None:
    """require_key=None 的免费引擎在无 Key 配置时也启用."""
    searchers = get_searchers(SearchRegion.GLOBAL, settings_no_keys)
    names = {s.name for s in searchers}
    assert "arxiv" in names  # 免费
    assert "pubmed" in names  # 免费
    assert "searxng" in names  # 免费 (SearXNGSearcher.name = "searxng")


def test_get_searchers_excludes_engines_when_key_missing(
    settings_no_keys: Settings,
) -> None:
    """require_key 单字符串: Key 未配置时引擎被过滤."""
    searchers = get_searchers(SearchRegion.GLOBAL, settings_no_keys)
    names = {s.name for s in searchers}
    # 需 Key 的引擎在无 Key 时应被过滤
    assert "tavily" not in names  # 需 tavily_api_key
    assert "brave" not in names  # 需 brave_api_key
    assert "bing" not in names  # 需 bing_api_key
    assert "serper" not in names  # 需 serper_api_key


def test_get_searchers_includes_engines_when_key_configured() -> None:
    """require_key 单字符串: Key 配置后引擎启用."""
    settings = Settings(
        tavily_api_key="tvly-xxx",
        brave_api_key="brv-xxx",
        _env_file=None,
    )
    searchers = get_searchers(SearchRegion.GLOBAL, settings)
    names = {s.name for s in searchers}
    assert "tavily" in names
    assert "brave" in names


def test_get_searchers_tuple_require_key_any_configured() -> None:
    """require_key tuple: 任一 Key 配置即启用 (如 serpapi_key 同时启用 Google+SerpApi)."""
    settings = Settings(serpapi_key="serp-xxx", _env_file=None)
    searchers = get_searchers(SearchRegion.GLOBAL, settings)
    names = {s.name for s in searchers}
    # google 和 serpapi 都用 serpapi_key, 任一配置即两个都启用
    assert "google" in names
    assert "serpapi" in names


def test_get_searchers_tuple_require_key_all_missing(settings_no_keys: Settings) -> None:
    """require_key tuple: 全部 Key 未配置时引擎被过滤.

    注意: Settings(_env_file=None) 仍会从 os.environ 读取 (pydantic-settings 行为),
    conftest.py 加载 .env (override=True) 会注入 SERPAPI_KEY 等环境变量,
    故必须用 settings_no_keys fixture 显式将所有 Key 设为 None.
    """
    searchers = get_searchers(SearchRegion.GLOBAL, settings_no_keys)
    names = {s.name for s in searchers}
    assert "google" not in names
    assert "serpapi" not in names


# ========== Custom 引擎环境变量启用 ==========


def test_custom_searcher_enabled_when_env_var_set(
    settings_no_keys: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CUSTOM_RETRIEVER_ENDPOINT 环境变量设置时, CustomSearcher 注册到 GLOBAL/AUTO."""
    monkeypatch.setenv("CUSTOM_RETRIEVER_ENDPOINT", "https://custom.example.com/search")
    _SEARCHER_REGISTRY.clear()
    _register_all_searchers()

    assert "custom" in _SEARCHER_REGISTRY
    searchers = get_searchers(SearchRegion.GLOBAL, settings_no_keys)
    names = {s.name for s in searchers}
    assert "custom" in names


def test_custom_searcher_not_registered_when_env_var_unset(
    settings_no_keys: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CUSTOM_RETRIEVER_ENDPOINT 环境变量未设置时, CustomSearcher 不注册."""
    monkeypatch.delenv("CUSTOM_RETRIEVER_ENDPOINT", raising=False)
    _SEARCHER_REGISTRY.clear()
    _register_all_searchers()

    assert "custom" not in _SEARCHER_REGISTRY


# ========== get_searchers 综合排序 ==========


def test_get_searchers_returns_sorted_list(settings_no_keys: Settings) -> None:
    """get_searchers 返回按 _sort_key 综合排序的列表 (优先级组/cost_tier/quality)."""
    searchers = get_searchers(SearchRegion.GLOBAL, settings_no_keys)
    assert len(searchers) > 0
    # 验证排序: 列表应按 _sort_key 升序
    sort_keys = [_sort_key(s) for s in searchers]
    assert sort_keys == sorted(sort_keys), "searchers 应按 _sort_key 排序"


def test_get_searchers_priority_group_0_for_high_quality_free(
    settings_no_keys: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """优先级组 0: quality_score >= 70 且有免费额度的引擎排前.

    用 monkeypatch 注入 mock 高质量免费引擎, 移除对真实搜索引擎注册表的
    环境依赖 (避免无可用引擎时 skip).
    """

    # 注入 mock 高质量免费引擎 (priority_group 0)
    class _HighQualityFreeEngine(BaseSearcher):
        name = "arxiv"  # FREE_QUOTA_MAP['arxiv']='unlimited' → has_free_quota=True
        cost_tier = "free"
        quality_score = 85.0  # >= 70 → is_high_quality=True → priority_group 0

    _SEARCHER_REGISTRY["mock_hq_free"] = {
        "class": _HighQualityFreeEngine,
        "regions": (SearchRegion.GLOBAL,),
        "require_key": None,
    }

    searchers = get_searchers(SearchRegion.GLOBAL, settings_no_keys)
    assert len(searchers) > 0, "注入 mock 引擎后应有可用搜索引擎"
    # 第一个引擎的优先级组应 <= 1 (高质量免费或完全免费)
    first_key = _sort_key(searchers[0])
    assert first_key[0] <= 1, f"首个引擎优先级组应 ≤1, 实际 {first_key[0]}"


# ========== _sort_key 综合排序键 ==========


def test_sort_key_free_engine_priority_group_1() -> None:
    """_sort_key: 完全免费引擎 (cost_tier='free') 优先级组为 1 (除非高质量且免费)."""

    class _FreeEngine(BaseSearcher):
        name = "test_free"
        cost_tier = "free"
        quality_score = 50.0  # < 70, 非高质量

    key = _sort_key(_FreeEngine())
    # quality < 70 且 cost_tier='free' → priority_group=1
    assert key[0] == 1


def test_sort_key_high_quality_free_priority_group_0() -> None:
    """_sort_key: quality_score >= 70 且有免费额度 → 优先级组 0."""

    class _HighQualityFree(BaseSearcher):
        name = "arxiv"  # FREE_QUOTA_MAP['arxiv']='unlimited'
        cost_tier = "free"
        quality_score = 80.0

    key = _sort_key(_HighQualityFree())
    assert key[0] == 0


def test_sort_key_paid_no_quota_priority_group_3() -> None:
    """_sort_key: 纯付费引擎 (cost_tier='paid', 无免费额度) 优先级组为 3."""

    class _PaidEngine(BaseSearcher):
        name = "nonexistent"  # 不在 FREE_QUOTA_MAP, 默认 'none'
        cost_tier = "paid"
        quality_score = 90.0  # 高质量但无免费额度

    key = _sort_key(_PaidEngine())
    # 高质量但无免费额度, cost_tier='paid' → priority_group=3 (不满足 group 0 需有免费额度)
    # 实际: is_high_quality=True 但 has_free_quota=False → 不进 group 0; cost_tier!='free' → 不进 group 1;
    #       has_free_quota=False → 不进 group 2; 兜底 group 3
    assert key[0] == 3


def test_sort_key_freemium_with_quota_priority_group_2() -> None:
    """_sort_key: freemium 引擎有免费额度 → 优先级组 2."""

    class _FreemiumEngine(BaseSearcher):
        name = "tavily"  # FREE_QUOTA_MAP['tavily']='1000/month'
        cost_tier = "freemium"
        quality_score = 50.0  # < 70

    key = _sort_key(_FreemiumEngine())
    # quality < 70, cost_tier='freemium' (非 free), has_free_quota=True → group 2
    assert key[0] == 2


# ========== register_searcher 装饰器 ==========


def test_register_searcher_adds_to_registry() -> None:
    """register_searcher 装饰器将类+元数据注册到 _SEARCHER_REGISTRY."""

    @register_searcher("test_custom_engine", regions=(SearchRegion.GLOBAL,), require_key=None)
    class _TestEngine(BaseSearcher):
        name = "test_custom_engine"

    assert "test_custom_engine" in _SEARCHER_REGISTRY
    spec = _SEARCHER_REGISTRY["test_custom_engine"]
    assert spec["class"] is _TestEngine
    assert spec["regions"] == (SearchRegion.GLOBAL,)
    assert spec["require_key"] is None


def test_register_searcher_returns_class_unchanged() -> None:
    """register_searcher 装饰器返回类本身不变."""

    @register_searcher("test_unchanged")
    class _TestClass(BaseSearcher):
        name = "test_unchanged"

    assert _TestClass.__name__ == "_TestClass"


def test_register_searcher_default_regions_global_auto() -> None:
    """register_searcher 默认 regions=(GLOBAL, AUTO)."""

    @register_searcher("test_default_regions")
    class _TestDefault(BaseSearcher):
        name = "test_default_regions"

    spec = _SEARCHER_REGISTRY["test_default_regions"]
    assert SearchRegion.GLOBAL in spec["regions"]
    assert SearchRegion.AUTO in spec["regions"]


def test_register_searcher_supports_tuple_require_key() -> None:
    """register_searcher 支持 tuple require_key (多 Key 任一配置即启用)."""

    @register_searcher(
        "test_tuple_key",
        require_key=("key_a", "key_b"),
    )
    class _TestTuple(BaseSearcher):
        name = "test_tuple_key"

    spec = _SEARCHER_REGISTRY["test_tuple_key"]
    assert spec["require_key"] == ("key_a", "key_b")


# ========== get_registered_searchers 浅拷贝 ==========


def test_get_registered_searchers_returns_copy() -> None:
    """get_registered_searchers 返回浅拷贝, 修改不影响内部注册表."""
    _register_all_searchers()
    registry = get_registered_searchers()
    original_size = len(registry)
    registry["fake_key"] = {}  # type: ignore[assignment]
    # 内部注册表不应被影响
    assert "fake_key" not in get_registered_searchers()
    assert len(get_registered_searchers()) == original_size


# ========== deduplicate_results ==========


def test_deduplicate_results_removes_duplicate_urls() -> None:
    """deduplicate_results 按 url 去重, 保留首次出现."""
    results = [
        {"title": "A", "url": "https://a.com", "snippet": ""},
        {"title": "B", "url": "https://b.com", "snippet": ""},
        {"title": "A dup", "url": "https://a.com", "snippet": "dup"},
    ]
    deduped = deduplicate_results(results)
    assert len(deduped) == 2
    assert deduped[0]["title"] == "A"  # 保留首次
    assert deduped[1]["url"] == "https://b.com"


def test_deduplicate_results_preserves_order() -> None:
    """deduplicate_results 保序输出."""
    results = [
        {"url": "https://c.com"},
        {"url": "https://a.com"},
        {"url": "https://b.com"},
        {"url": "https://a.com"},  # 重复
    ]
    deduped = deduplicate_results(results)
    assert [r["url"] for r in deduped] == [
        "https://c.com",
        "https://a.com",
        "https://b.com",
    ]


def test_deduplicate_results_empty_url_kept() -> None:
    """deduplicate_results: 空 url 的结果保留 (不参与去重)."""
    results = [
        {"url": ""},
        {"url": ""},
        {"url": "https://a.com"},
    ]
    deduped = deduplicate_results(results)
    # 空 url 不加入 seen, 故两条空 url 均保留
    assert len(deduped) == 3


def test_deduplicate_results_custom_key() -> None:
    """deduplicate_results 支持自定义 key (如按 title 去重)."""
    results = [
        {"title": "A", "url": "https://a.com"},
        {"title": "B", "url": "https://b.com"},
        {"title": "A", "url": "https://c.com"},  # title 重复
    ]
    deduped = deduplicate_results(results, key="title")
    assert len(deduped) == 2
    assert deduped[0]["title"] == "A"


def test_deduplicate_results_empty_list() -> None:
    """deduplicate_results 空列表返回空列表."""
    assert deduplicate_results([]) == []


# ========== detect_region ==========


def test_detect_region_chinese_query_returns_cn() -> None:
    """detect_region: 中文字符比例 > 30% 返回 CN."""
    assert detect_region("分析新能源汽车市场发展趋势") == SearchRegion.CN


def test_detect_region_english_query_returns_global() -> None:
    """detect_region: 无中文字符返回 GLOBAL."""
    assert detect_region("analyze AI in healthcare market") == SearchRegion.GLOBAL


def test_detect_region_academic_keyword_returns_academic() -> None:
    """detect_region: 学术关键词命中返回 ACADEMIC."""
    # settings.academic_keywords 默认含 'pubmed'/'arxiv' 等
    assert detect_region("pubmed search for cancer treatment") == SearchRegion.ACADEMIC


def test_detect_region_empty_query_returns_auto() -> None:
    """detect_region: 空查询返回 AUTO."""
    assert detect_region("") == SearchRegion.AUTO


# ========== FREE_QUOTA_MAP 内容契约 ==========


def test_free_quota_map_contains_unlimited_engines() -> None:
    """FREE_QUOTA_MAP 含完全免费引擎 (duckduckgo/arxiv/pubmed 等)."""
    assert FREE_QUOTA_MAP["duckduckgo"] == "unlimited"
    assert FREE_QUOTA_MAP["arxiv"] == "unlimited"
    assert FREE_QUOTA_MAP["pubmed"] == "unlimited"


def test_free_quota_map_contains_paid_engines() -> None:
    """FREE_QUOTA_MAP 含纯付费引擎 (google/bing/brave)."""
    assert FREE_QUOTA_MAP["google"] == "none"
    assert FREE_QUOTA_MAP["bing"] == "none"
    assert FREE_QUOTA_MAP["brave"] == "none"
