"""搜索引擎注册中心与工厂.

用户需求 5: 中文优先原则.
- 国内资料: 博查搜索 (Bocha) 为主 + DuckDuckGo 兜底
- 国外资料: Tavily + arxiv + Semantic Scholar
- 混合: 双引擎并行

对标 GPT Researcher retrievers/ 体系, 但统一走 httpx 异步.
所有 retriever 共享同一规约: search(query, max_results) -> list[dict].
返回 dict 字段: {"title", "url", "snippet", "source", "region"}.
"""

from __future__ import annotations

import logging
import os
from enum import StrEnum
from typing import Any

from src.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class SearchRegion(StrEnum):
    """搜索区域 (中文优先路由)."""

    CN = "cn"  # 国内 (博查 + DuckDuckGo)
    GLOBAL = "global"  # 国外 (Tavily + arxiv + Brave + Bing + Google + Serper)
    ACADEMIC = "academic"  # 学术 (PubMed + Semantic Scholar + arxiv)
    AUTO = "auto"  # 自动判断 (基于查询语言/学术关键词)


class BaseSearcher:
    """搜索引擎基类.

    所有 searcher 共享 search 方法签名, 返回统一格式.
    """

    name: str = "base"
    region: SearchRegion = SearchRegion.AUTO

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


def get_searchers(
    region: SearchRegion = SearchRegion.AUTO,
    settings: Settings | None = None,
) -> list[BaseSearcher]:
    """按区域获取搜索引擎列表.

    用户需求 5:
    - CN: 博查 (主) + DuckDuckGo (兜底)
    - GLOBAL: Tavily + Arxiv + Brave + Bing + Google + Serper + PubMed + Semantic Scholar
    - ACADEMIC: PubMed + Semantic Scholar + Arxiv (学术优先)
    - AUTO: 混合 (全部可用引擎)
    """
    settings = settings or get_settings()
    searchers: list[BaseSearcher] = []

    # 学术区域优先: PubMed + Semantic Scholar + Arxiv
    if region == SearchRegion.ACADEMIC:
        from src.skills.researcher.searchers.arxiv import ArxivSearcher
        from src.skills.researcher.searchers.openalex import OpenAlexSearcher
        from src.skills.researcher.searchers.pubmed_searcher import PubMedSearcher
        from src.skills.researcher.searchers.semantic_scholar_searcher import (
            SemanticScholarSearcher,
        )

        searchers.append(PubMedSearcher(settings))  # 学术, 无需 Key
        searchers.append(SemanticScholarSearcher(settings))  # 学术, 无需 Key
        searchers.append(ArxivSearcher(settings))  # 学术, 无需 Key
        searchers.append(OpenAlexSearcher(settings))  # 学术, 无需 Key (P2-Future-04)
        return searchers

    if region in (SearchRegion.CN, SearchRegion.AUTO):
        # 国内搜索 (中文优先)
        from src.skills.researcher.searchers.bocha import BochaSearcher
        from src.skills.researcher.searchers.duckduckgo import DuckDuckGoSearcher

        if settings.bocha_api_key:
            searchers.append(BochaSearcher(settings))
        searchers.append(DuckDuckGoSearcher(settings))  # 兜底, 无需 Key

    if region in (SearchRegion.GLOBAL, SearchRegion.AUTO):
        # 国外搜索 (按 API Key 是否配置决定是否加入)
        from src.skills.researcher.searchers.arxiv import ArxivSearcher
        from src.skills.researcher.searchers.bing_searcher import BingSearcher
        from src.skills.researcher.searchers.brave_searcher import BraveSearcher
        from src.skills.researcher.searchers.exa import ExaSearcher
        from src.skills.researcher.searchers.google_searcher import GoogleSearcher
        from src.skills.researcher.searchers.pubmed_searcher import PubMedSearcher
        from src.skills.researcher.searchers.searchapi import SearchApiSearcher
        from src.skills.researcher.searchers.searx import SearXNGSearcher
        from src.skills.researcher.searchers.semantic_scholar_searcher import (
            SemanticScholarSearcher,
        )
        from src.skills.researcher.searchers.serpapi import SerpApiSearcher
        from src.skills.researcher.searchers.serper_searcher import SerperSearcher
        from src.skills.researcher.searchers.tavily import TavilySearcher

        if settings.tavily_api_key:
            searchers.append(TavilySearcher(settings))
        if settings.brave_api_key:
            searchers.append(BraveSearcher(settings))
        if settings.bing_api_key:
            searchers.append(BingSearcher(settings))
        if settings.serpapi_key:
            searchers.append(GoogleSearcher(settings))
            searchers.append(SerpApiSearcher(settings))  # 复用 serpapi_key (P2-Future-04)
        if settings.serper_api_key:
            searchers.append(SerperSearcher(settings))
        if settings.exa_api_key:
            searchers.append(ExaSearcher(settings))  # P2-Future-04
        if settings.searchapi_api_key:
            searchers.append(SearchApiSearcher(settings))  # P2-Future-04
        searchers.append(SearXNGSearcher(settings))  # 自托管, 无需 Key (P2-Future-04)
        # Custom retriever (企业私有端点, 仅当环境变量配置时启用)
        # 对标 GPTR retrievers/custom/custom.py
        if os.getenv("CUSTOM_RETRIEVER_ENDPOINT"):
            from src.skills.researcher.searchers.custom import CustomSearcher

            searchers.append(CustomSearcher(settings))
        # 学术引擎 (无需 Key, GLOBAL/AUTO 也加入)
        searchers.append(ArxivSearcher(settings))
        searchers.append(PubMedSearcher(settings))
        searchers.append(SemanticScholarSearcher(settings))

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


# ========== 插件注册表 (可选增强, 对标 GPTR 字典静态注册) ==========
# 用装饰器注册 searcher, 不破坏现有 get_searchers() 函数式工厂.
# 第三方扩展可通过 @register_searcher("xxx") 自注册, 再由
# get_registered_searchers() 查询, 未来可逐步迁移工厂到注册表驱动.
_SEARCHER_REGISTRY: dict[str, type[BaseSearcher]] = {}


def register_searcher(name: str):
    """搜索引擎注册装饰器.

    Args:
        name: 注册键名 (如 "custom").

    Returns:
        类装饰器, 将 cls 注册到 _SEARCHER_REGISTRY 后原样返回.
    """

    def decorator(cls: type[BaseSearcher]) -> type[BaseSearcher]:
        _SEARCHER_REGISTRY[name] = cls
        return cls

    return decorator


def get_registered_searchers() -> dict[str, type[BaseSearcher]]:
    """返回已注册的搜索引擎字典 (浅拷贝, 防外部篡改)."""
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
