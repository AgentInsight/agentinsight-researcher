"""GDELT 新闻搜索 (gdeltproject.org, v1.1 新增).

40 年全球新闻事件数据库, 完全免费.
- cost_tier: free (完全免费)
- quality_score: 65.0
- region: AUTO (CN+GLOBAL 都可用)
- API: https://api.gdeltproject.org/api/v2/doc/doc
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.settings import Settings
from src.skills.researcher.searchers import BaseSearcher, SearchRegion, register_searcher

logger = logging.getLogger(__name__)


@register_searcher("gdelt")
class GDELTSearcher(BaseSearcher):
    """GDELT 新闻搜索器.

    40 年全球新闻事件数据库, 完全免费.
    """

    name = "gdelt"
    region = SearchRegion.AUTO
    cost_tier = "free"
    quality_score = 65.0

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://api.gdeltproject.org/api/v2/doc/doc"

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """调用 GDELT API."""
        params = {
            "query": query,
            "mode": "ArtList",
            "maxrecords": str(max_results),
            "format": "json",
            "sort": "DateDesc",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(self.base_url, params=params)
        except Exception as e:
            logger.warning(f"gdelt 调用失败: {e}")
            return []

        if resp.status_code != 200:
            logger.warning(f"gdelt HTTP {resp.status_code}: {resp.text[:200]}")
            return []

        try:
            data = resp.json()
        except Exception as e:
            # GDELT 有时返回非标准 JSON, 降级处理
            logger.warning(f"gdelt JSON 解析失败: {e}")
            return []

        results: list[dict[str, Any]] = []
        for item in (data.get("articles") or [])[:max_results]:
            title = item.get("title") or ""
            url = item.get("url") or ""
            snippet = item.get("socialimage") or item.get("summary") or ""
            if url:
                results.append(self._normalize_result(title, url, snippet))

        return self._filter_by_domains(results, query_domains)
