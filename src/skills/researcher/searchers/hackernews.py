"""Hacker News 技术社区搜索 (hn.algolia.com, v1.1 新增).

技术社区质量高, 10,000 req/h/IP 免费.
- cost_tier: free (完全免费)
- quality_score: 60.0
- region: GLOBAL (技术内容以英文为主)
- API: https://hn.algolia.com/api/v1/search
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.settings import Settings
from src.skills.researcher.searchers import BaseSearcher, SearchRegion, register_searcher

logger = logging.getLogger(__name__)


@register_searcher("hackernews")
class HackerNewsSearcher(BaseSearcher):
    """Hacker News 技术社区搜索器.

    10,000 req/h/IP 免费, 技术社区质量高.
    """

    name = "hackernews"
    region = SearchRegion.GLOBAL
    cost_tier = "free"
    quality_score = 60.0

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://hn.algolia.com/api/v1/search"

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """调用 Hacker News Algolia API."""
        params = {
            "query": query,
            "tags": "story",
            "hitsPerPage": str(max_results),
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(self.base_url, params=params)
        except Exception as e:
            logger.warning(f"hackernews 调用失败: {e}")
            return []

        if resp.status_code != 200:
            logger.warning(f"hackernews HTTP {resp.status_code}: {resp.text[:200]}")
            return []

        try:
            data = resp.json()
        except Exception as e:
            logger.warning(f"hackernews JSON 解析失败: {e}")
            return []

        results: list[dict[str, Any]] = []
        for hit in (data.get("hits") or [])[:max_results]:
            title = hit.get("title") or hit.get("story_title") or ""
            # HN 链接优先, 否则用 HN 讨论页
            url = hit.get("url") or ""
            if not url and hit.get("objectID"):
                url = f"https://news.ycombinator.com/item?id={hit['objectID']}"
            snippet = hit.get("story_text") or hit.get("comment_text") or ""
            if url:
                results.append(self._normalize_result(title, url, snippet))

        return self._filter_by_domains(results, query_domains)
