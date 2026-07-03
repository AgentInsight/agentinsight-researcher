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
from enum import StrEnum
from typing import Any

from src.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class SearchRegion(StrEnum):
    """搜索区域 (中文优先路由)."""

    CN = "cn"  # 国内 (博查 + DuckDuckGo)
    GLOBAL = "global"  # 国外 (Tavily + arxiv + Semantic Scholar)
    AUTO = "auto"  # 自动判断 (基于查询语言)


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
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """搜索, 返回 [{"title","url","snippet","source","region"}]."""
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


def get_searchers(
    region: SearchRegion = SearchRegion.AUTO,
    settings: Settings | None = None,
) -> list[BaseSearcher]:
    """按区域获取搜索引擎列表.

    用户需求 5:
    - CN: 博查 (主) + DuckDuckGo (兜底)
    - GLOBAL: Tavily + Arxiv + Semantic Scholar
    - AUTO: 混合 (全部可用引擎)
    """
    settings = settings or get_settings()
    searchers: list[BaseSearcher] = []

    if region in (SearchRegion.CN, SearchRegion.AUTO):
        # 国内搜索 (中文优先)
        from src.skills.researcher.searchers.bocha import BochaSearcher
        from src.skills.researcher.searchers.duckduckgo import DuckDuckGoSearcher

        if settings.bocha_api_key:
            searchers.append(BochaSearcher(settings))
        searchers.append(DuckDuckGoSearcher(settings))  # 兜底, 无需 Key

    if region in (SearchRegion.GLOBAL, SearchRegion.AUTO):
        # 国外搜索
        from src.skills.researcher.searchers.arxiv import ArxivSearcher
        from src.skills.researcher.searchers.tavily import TavilySearcher

        if settings.tavily_api_key:
            searchers.append(TavilySearcher(settings))
        searchers.append(ArxivSearcher(settings))  # 学术, 无需 Key

    return searchers


def detect_region(query: str) -> SearchRegion:
    """检测查询语言/区域 (中文优先).

    用户需求 5: 简单启发式判断.
    """
    if not query:
        return SearchRegion.AUTO

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
