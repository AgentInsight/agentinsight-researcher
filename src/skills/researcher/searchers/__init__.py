"""搜索引擎注册中心与工厂.

用户需求 5: 中文优先原则.
- 国内资料: 博查搜索 (Bocha) 为主 + DuckDuckGo 兜底
- 国外资料: Tavily + arxiv + Semantic Scholar
- 混合: 双引擎并行

对标 GPT Researcher retrievers/ 体系, 但统一走 httpx 异步.
所有 retriever 共享同一规约: search(query, max_results) -> list[dict].
返回 dict 字段: {"title", "url", "snippet", "source", "region"}.

v1.1 新增:
- BaseSearcher 加入 cost_tier (free/freemium/paid) + quality_score (0-100) 双字段
- 质量评分 + 免费额度综合排序 (优先级组 0-3)
- QuotaCache 额度缓存机制 (额度已满引擎跳过, TTL 最高 24 小时)
- get_searchers_async() 异步版本支持额度缓存检查

P1-1 重构: 引入 @register_searcher 装饰器注册表, 取代 11 个 if-else 散点.
各 searcher 在自身类定义处用装饰器声明 name/regions/require_key 等元数据,
get_searchers() 遍历注册表按 require_key 自动过滤, 新增引擎只需加装饰器.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from src.config.settings import Settings, get_settings

if TYPE_CHECKING:
    from src.skills.researcher.searchers.quota_cache import QuotaCache

logger = logging.getLogger(__name__)


class SearchRegion(StrEnum):
    """搜索区域 (中文优先路由)."""

    CN = "cn"  # 国内 (博查 + 秘塔 + DuckDuckGo + Tavily + Exa)
    GLOBAL = "global"  # 国外 (Tavily + arxiv + Brave + Bing + Google + Serper)
    ACADEMIC = "academic"  # 学术 (PubMed + Semantic Scholar + arxiv + OpenAlex + CrossRef)
    AUTO = "auto"  # 自动判断 (基于查询语言/学术关键词)


# v1.1 新增: 引擎免费额度配置 (用于综合排序)
FREE_QUOTA_MAP: dict[str, str] = {
    # 完全免费 (无额度限制)
    "duckduckgo": "unlimited",
    "searxng": "unlimited",
    "arxiv": "unlimited",
    "pubmed": "unlimited",
    "openalex": "unlimited",
    "crossref": "unlimited",
    "unpaywall": "100k/day",
    "gdelt": "unlimited",
    "hackernews": "10k/h",
    "semantic_scholar": "100/5min",  # v1.1 修复: 免费引擎遗漏
    # 有免费额度 (freemium 或 paid-with-free-tier)
    "metaso": "freemium",
    "github": "freemium",
    "tavily": "1000/month",
    "exa": "20000/month",
    "bocha": "1000+口令",
    "serpapi": "250/month",
    "serper": "2500/trial",
    "searchapi": "100/trial",
    # 纯付费 (无免费额度)
    "google": "none",
    "bing": "none",
    "brave": "none",
}


def _sort_key(searcher: BaseSearcher) -> tuple[int, int, float]:
    """综合排序键: (优先级组, cost_tier 权重, quality_score 倒序).

    优先级组 (v1.1):
      0 = 质量高且有免费额度 (quality_score >= 70 且有免费额度)
      1 = 完全免费引擎 (cost_tier == "free")
      2 = 有免费额度的付费引擎
      3 = 纯付费引擎
    """
    cost_tier = getattr(searcher, "cost_tier", "paid")
    quality = getattr(searcher, "quality_score", 0.0)
    quota = FREE_QUOTA_MAP.get(searcher.name, "none")

    has_free_quota = quota != "none"
    is_high_quality = quality >= 70.0

    if is_high_quality and has_free_quota:
        priority_group = 0
    elif cost_tier == "free":
        priority_group = 1
    elif has_free_quota:
        priority_group = 2
    else:
        priority_group = 3

    cost_order = {"free": 0, "freemium": 1, "paid": 2}
    return (priority_group, cost_order.get(cost_tier, 2), -quality)


class BaseSearcher:
    """搜索引擎基类.

    所有 searcher 共享 search 方法签名, 返回统一格式.

    v1.1 新增字段:
        cost_tier: 成本层级 ("free" / "freemium" / "paid"), 默认 "paid"
        quality_score: 质量评分 (0-100), 基于 SimpleQA 或领域经验, 默认 0.0
    """

    name: str = "base"
    region: SearchRegion = SearchRegion.AUTO
    # v1.1 新增双字段 (默认值保持向后兼容)
    cost_tier: Literal["free", "freemium", "paid"] = "paid"
    quality_score: float = 0.0

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """搜索, 返回 [{"title","url","snippet","source","region"}].

        Args:
            query: 搜索查询.
            max_results: 最大返回结果数.
            query_domains: 域名过滤白名单 (P1-Future-02). 仅保留 url 命中任一域名的结果.
                原生支持域名过滤的引擎 (如 Tavily include_domains) 直接传给 API;
                不支持的引擎由 _filter_by_domains 后置过滤.
        """
        raise NotImplementedError

    def _normalize_result(
        self,
        title: str,
        url: str,
        snippet: str,
    ) -> dict[str, Any]:
        """归一化结果格式."""
        return {
            "title": title or "",
            "url": url or "",
            "snippet": snippet or "",
            "source": self.name,
            "region": self.region.value,
        }

    @staticmethod
    def _filter_by_domains(
        results: list[dict[str, Any]],
        query_domains: list[str] | None,
    ) -> list[dict[str, Any]]:
        """按域名白名单后置过滤结果 (P1-Future-02).

        适用于原生不支持域名过滤的引擎. query_domains 为 None 或空时不过滤.
        匹配规则: url 包含任一 query_domains 字符串即保留 (子串匹配, 对齐 task 规格).
        """
        if not query_domains:
            return results
        return [r for r in results if any(d in r.get("url", "") for d in query_domains)]

    async def close(self) -> None:
        """释放资源 (httpx 客户端等).

        任务2 内存优化: BaseSearcher 基类提供默认空实现,
        持有 httpx.AsyncClient 的子类需 override 此方法.
        调用方 (research_conductor._process_sub_query) 在 finally 中调用.
        """
        pass


def get_searchers(
    region: SearchRegion = SearchRegion.AUTO,
    settings: Settings | None = None,
) -> list[BaseSearcher]:
    """按区域获取搜索引擎列表.

    用户需求 5:
    - CN: 博查 (主) + 秘塔 (国内AI) + DuckDuckGo (兜底) + Tavily + Exa + GDELT + HackerNews
    - GLOBAL: Tavily + Arxiv + Brave + Bing + Google + Serper + Exa + SearchApi + SearXNG + GDELT + HackerNews + GitHub + CrossRef + PubMed + SemanticScholar
    - ACADEMIC: PubMed + Semantic Scholar + Arxiv + OpenAlex + CrossRef + Unpaywall (学术优先)
    - AUTO: 混合 (全部可用引擎)

    v1.1 新增:
    - CN 区域加入秘塔 + Exa + GDELT + HackerNews
    - GLOBAL 区域加入 GDELT + HackerNews + GitHub + CrossRef
    - ACADEMIC 区域加入 CrossRef + Unpaywall
    - 末尾应用质量评分 + 免费额度综合排序

    P1-1 重构: 遍历 _SEARCHER_REGISTRY 注册表按 require_key 自动过滤,
    取代原 11 个 if settings.<engine>_api_key: 散点. 新增引擎只需在类定义处
    加 @register_searcher 装饰器, 无需改本函数.
    """
    settings = settings or get_settings()
    # P1-1: 首次调用时触发延迟注册 (避免模块加载时循环导入)
    if not _SEARCHER_REGISTRY:
        _register_all_searchers()
    searchers: list[BaseSearcher] = []

    # P1-1: 遍历注册表, 按 region + require_key 自动过滤
    for _name, spec in _SEARCHER_REGISTRY.items():
        # 区域过滤
        if region not in spec["regions"]:
            continue

        # Key 校验 (require_key 为 None 表示无需 Key 的免费引擎)
        require_key = spec["require_key"]
        if require_key is not None:
            # 多 Key 支持: require_key 为 tuple 时任一 Key 配置即启用 (如 serpapi 同时注册 Google+SerpApi)
            if isinstance(require_key, tuple):
                if not any(getattr(settings, k, None) for k in require_key):
                    continue
            elif not getattr(settings, require_key, None):
                continue

        # 实例化
        cls = spec["class"]
        searchers.append(cls(settings))

    # v1.1 综合排序
    searchers.sort(key=_sort_key)
    return searchers


async def get_searchers_async(
    region: SearchRegion = SearchRegion.AUTO,
    settings: Settings | None = None,
    quota_cache: QuotaCache | None = None,
) -> list[BaseSearcher]:
    """获取搜索器列表 (异步, v1.1 加入额度缓存检查).

    Args:
        region: 搜索区域
        settings: 全局配置
        quota_cache: 额度缓存实例 (可选, None 表示不启用缓存)

    Returns:
        排序后的搜索器列表 (已跳过额度已满的引擎)
    """
    searchers = get_searchers(region, settings)

    # v1.1 新增: 额度缓存检查, 跳过不可用引擎
    if quota_cache is not None:
        filtered: list[BaseSearcher] = []
        for s in searchers:
            if await quota_cache.is_exceeded(s.name):
                logger.info(f"跳过 {s.name} (额度已满, 缓存标记不可用)")
                continue
            filtered.append(s)
        searchers = filtered

    return searchers


def detect_region(query: str) -> SearchRegion:
    """检测查询语言/区域 (中文优先).

    用户需求 5: 简单启发式判断.
    - 学术关键词命中 -> ACADEMIC (PubMed/Semantic Scholar/Arxiv 优先)
    - 中文字符比例 > 30% -> CN
    - 无中文字符 -> GLOBAL
    - 其他 -> AUTO

    P1-03: 学术关键词列表外提到 settings.academic_keywords, 避免硬编码.
    P1-03: academic_route_enabled=False 时跳过学术路由, 走 AUTO.
    """
    if not query:
        return SearchRegion.AUTO

    settings = get_settings()
    # P1-03: 学术路由开关 (默认 True)
    if settings.academic_route_enabled:
        query_lower = query.lower()
        # P1-03: 关键词从 settings 读取 (支持运行时配置覆盖)
        academic_keywords = tuple(settings.academic_keywords)
        if any(kw in query_lower for kw in academic_keywords):
            return SearchRegion.ACADEMIC

    # 统计中文字符比例
    chinese_chars = sum(1 for c in query if "\u4e00" <= c <= "\u9fff")
    total_chars = len(query)
    if total_chars == 0:
        return SearchRegion.AUTO

    chinese_ratio = chinese_chars / total_chars
    if chinese_ratio > 0.3:
        return SearchRegion.CN
    if chinese_chars == 0:
        return SearchRegion.GLOBAL
    return SearchRegion.AUTO


# ========== P1-1: 搜索引擎注册表 (对标 GPTR VALID_RETRIEVERS + 装饰器模式) ==========
# @register_searcher 装饰器在类定义处声明元数据 (name/regions/require_key),
# get_searchers() 遍历注册表自动按 region + require_key 过滤实例化.
# 新增引擎只需在引擎类上加 @register_searcher 装饰器, 无需改 get_searchers.
_SEARCHER_REGISTRY: dict[str, dict[str, Any]] = {}


def register_searcher(
    name: str,
    *,
    regions: tuple[SearchRegion, ...] = (SearchRegion.GLOBAL, SearchRegion.AUTO),
    require_key: str | tuple[str, ...] | None = None,
) -> Callable[[type[BaseSearcher]], type[BaseSearcher]]:
    """搜索引擎注册装饰器 (P1-1).

    Args:
        name: 引擎注册键名 (如 "bocha")
        regions: 适用的区域列表 (默认 GLOBAL+AUTO)
        require_key: 必需的 Settings API Key 字段名; None 表示无需 Key 的免费引擎;
            tuple 表示多 Key 任一配置即启用 (如 serpapi 同时注册 Google+SerpApi)

    Returns:
        类装饰器, 将 cls + 元数据注册到 _SEARCHER_REGISTRY 后原样返回.
    """

    def decorator(cls: type[BaseSearcher]) -> type[BaseSearcher]:
        _SEARCHER_REGISTRY[name] = {
            "class": cls,
            "regions": regions,
            "require_key": require_key,
        }
        return cls

    return decorator


def _register_all_searchers() -> None:
    """P1-1: 集中注册所有内置搜索引擎 (延迟导入避免循环依赖).

    各 searcher 按区域 + Key 要求注册到 _SEARCHER_REGISTRY:
    - ACADEMIC 区域: PubMed/SemanticScholar/Arxiv/OpenAlex/CrossRef/Unpaywall (全免费)
    - CN 区域: Bocha(require_key)/Metaso(require_key)/DuckDuckGo/GDELT/HackerNews/
              Tavily(require_key, 跨区域兜底)/Exa(require_key, 跨区域兜底)
    - GLOBAL 区域: Tavily/Brave/Bing/Google+SerpApi(共享 serpapi_key)/Serper/Exa/
                   SearchApi/SearXNG/GDELT/HackerNews/GitHub(require_key)/CrossRef/
                   Arxiv/PubMed/SemanticScholar/Custom(require env var)
    - AUTO 区域: CN + GLOBAL 引擎并集

    注: 先 clear() 清除各 searcher 模块导入时装饰器预注册的默认条目
    (如 unpaywall_doi 不应出现在 GLOBAL/AUTO), 以本函数的显式注册为准.
    """
    # 清除装饰器预注册的默认条目, 以本函数的显式注册为准
    _SEARCHER_REGISTRY.clear()
    # ACADEMIC 区域引擎 (全部免费, 无需 Key)
    from src.skills.researcher.searchers.arxiv import ArxivSearcher
    from src.skills.researcher.searchers.crossref import CrossRefSearcher
    from src.skills.researcher.searchers.openalex import OpenAlexSearcher
    from src.skills.researcher.searchers.pubmed_searcher import PubMedSearcher
    from src.skills.researcher.searchers.semantic_scholar_searcher import (
        SemanticScholarSearcher,
    )
    from src.skills.researcher.searchers.unpaywall import UnpaywallSearcher

    _SEARCHER_REGISTRY["pubmed"] = {
        "class": PubMedSearcher,
        "regions": (SearchRegion.ACADEMIC, SearchRegion.GLOBAL, SearchRegion.AUTO),
        "require_key": None,
    }
    _SEARCHER_REGISTRY["semantic_scholar"] = {
        "class": SemanticScholarSearcher,
        "regions": (SearchRegion.ACADEMIC, SearchRegion.GLOBAL, SearchRegion.AUTO),
        "require_key": None,
    }
    _SEARCHER_REGISTRY["arxiv"] = {
        "class": ArxivSearcher,
        "regions": (SearchRegion.ACADEMIC, SearchRegion.GLOBAL, SearchRegion.AUTO),
        "require_key": None,
    }
    _SEARCHER_REGISTRY["openalex"] = {
        "class": OpenAlexSearcher,
        "regions": (SearchRegion.ACADEMIC,),
        "require_key": None,
    }
    _SEARCHER_REGISTRY["crossref"] = {
        "class": CrossRefSearcher,
        "regions": (
            SearchRegion.ACADEMIC,
            SearchRegion.GLOBAL,
            SearchRegion.AUTO,
        ),
        "require_key": None,
    }
    _SEARCHER_REGISTRY["unpaywall"] = {
        "class": UnpaywallSearcher,
        "regions": (SearchRegion.ACADEMIC,),
        "require_key": None,
    }

    # CN 区域引擎 (DuckDuckGo/GDELT/HackerNews 免费; Bocha/Metaso/Tavily/Exa 需 Key)
    from src.skills.researcher.searchers.bocha import BochaSearcher
    from src.skills.researcher.searchers.duckduckgo import DuckDuckGoSearcher
    from src.skills.researcher.searchers.exa import ExaSearcher
    from src.skills.researcher.searchers.gdelt import GDELTSearcher
    from src.skills.researcher.searchers.hackernews import HackerNewsSearcher
    from src.skills.researcher.searchers.metaso import MetasoSearcher
    from src.skills.researcher.searchers.tavily import TavilySearcher

    _SEARCHER_REGISTRY["bocha"] = {
        "class": BochaSearcher,
        "regions": (SearchRegion.CN, SearchRegion.AUTO),
        "require_key": "bocha_api_key",
    }
    _SEARCHER_REGISTRY["metaso"] = {
        "class": MetasoSearcher,
        "regions": (SearchRegion.CN, SearchRegion.AUTO),
        "require_key": "metaso_api_key",
    }
    _SEARCHER_REGISTRY["duckduckgo"] = {
        "class": DuckDuckGoSearcher,
        "regions": (SearchRegion.CN, SearchRegion.AUTO),
        "require_key": None,
    }
    _SEARCHER_REGISTRY["gdelt_cn"] = {
        "class": GDELTSearcher,
        "regions": (SearchRegion.CN, SearchRegion.GLOBAL, SearchRegion.AUTO),
        "require_key": None,
    }
    _SEARCHER_REGISTRY["hackernews_cn"] = {
        "class": HackerNewsSearcher,
        "regions": (SearchRegion.CN, SearchRegion.GLOBAL, SearchRegion.AUTO),
        "require_key": None,
    }
    # CN 区域跨区域兜底引擎 (Tavily/Exa 在 CN/GLOBAL/AUTO 均注册)
    _SEARCHER_REGISTRY["tavily"] = {
        "class": TavilySearcher,
        "regions": (SearchRegion.CN, SearchRegion.GLOBAL, SearchRegion.AUTO),
        "require_key": "tavily_api_key",
    }
    _SEARCHER_REGISTRY["exa"] = {
        "class": ExaSearcher,
        "regions": (SearchRegion.CN, SearchRegion.GLOBAL, SearchRegion.AUTO),
        "require_key": "exa_api_key",
    }

    # GLOBAL 区域专属引擎
    from src.skills.researcher.searchers.bing_searcher import BingSearcher
    from src.skills.researcher.searchers.brave_searcher import BraveSearcher
    from src.skills.researcher.searchers.github import GitHubSearcher
    from src.skills.researcher.searchers.google_searcher import GoogleSearcher
    from src.skills.researcher.searchers.searchapi import SearchApiSearcher
    from src.skills.researcher.searchers.searx import SearXNGSearcher
    from src.skills.researcher.searchers.serpapi import SerpApiSearcher
    from src.skills.researcher.searchers.serper_searcher import SerperSearcher

    _SEARCHER_REGISTRY["brave"] = {
        "class": BraveSearcher,
        "regions": (SearchRegion.GLOBAL, SearchRegion.AUTO),
        "require_key": "brave_api_key",
    }
    _SEARCHER_REGISTRY["bing"] = {
        "class": BingSearcher,
        "regions": (SearchRegion.GLOBAL, SearchRegion.AUTO),
        "require_key": "bing_api_key",
    }
    # serpapi_key 同时启用 GoogleSearcher + SerpApiSearcher (用 tuple 多 Key 机制)
    _SEARCHER_REGISTRY["google"] = {
        "class": GoogleSearcher,
        "regions": (SearchRegion.GLOBAL, SearchRegion.AUTO),
        "require_key": "serpapi_key",
    }
    _SEARCHER_REGISTRY["serpapi"] = {
        "class": SerpApiSearcher,
        "regions": (SearchRegion.GLOBAL, SearchRegion.AUTO),
        "require_key": "serpapi_key",
    }
    _SEARCHER_REGISTRY["serper"] = {
        "class": SerperSearcher,
        "regions": (SearchRegion.GLOBAL, SearchRegion.AUTO),
        "require_key": "serper_api_key",
    }
    _SEARCHER_REGISTRY["searchapi"] = {
        "class": SearchApiSearcher,
        "regions": (SearchRegion.GLOBAL, SearchRegion.AUTO),
        "require_key": "searchapi_api_key",
    }
    _SEARCHER_REGISTRY["searxng"] = {
        "class": SearXNGSearcher,
        "regions": (SearchRegion.GLOBAL, SearchRegion.AUTO),
        "require_key": None,
    }
    _SEARCHER_REGISTRY["github"] = {
        "class": GitHubSearcher,
        "regions": (SearchRegion.GLOBAL, SearchRegion.AUTO),
        "require_key": "github_token",
    }
    # Custom retriever (企业私有端点, 仅当环境变量配置时启用)
    # 注: CUSTOM_RETRIEVER_ENDPOINT 尚未收敛到 Settings (P0-1 记录需求),
    # 此处用 env var 检查作为 require_key 的等价判断
    if os.getenv("CUSTOM_RETRIEVER_ENDPOINT"):
        from src.skills.researcher.searchers.custom import CustomSearcher

        _SEARCHER_REGISTRY["custom"] = {
            "class": CustomSearcher,
            "regions": (SearchRegion.GLOBAL, SearchRegion.AUTO),
            "require_key": None,  # 已通过 env var 检查, 不再要求 settings Key
        }


def get_registered_searchers() -> dict[str, dict[str, Any]]:
    """返回已注册的搜索引擎字典 (浅拷贝, 防外部篡改).

    P1-1: 首次调用时触发 _register_all_searchers() 延迟注册.
    """
    if not _SEARCHER_REGISTRY:
        _register_all_searchers()
    return dict(_SEARCHER_REGISTRY)


def deduplicate_results(
    results: list[dict[str, Any]],
    *,
    key: str = "url",
) -> list[dict[str, Any]]:
    """跨搜索引擎 URL 去重 (P1-01).

    保留首次出现, 后续重复项丢弃. 保序输出.

    Args:
        results: 多引擎聚合后的结果列表.
        key: 去重键 (默认 "url").

    Returns:
        去重后的结果列表.
    """
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for r in results:
        k = r.get(key, "")
        if not k or k not in seen:
            if k:
                seen.add(k)
            deduped.append(r)
    return deduped
