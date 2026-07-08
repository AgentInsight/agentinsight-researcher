"""CrossRef 学术搜索 (crossref.org, v1.1 新增).

DOI 注册权威, 1.5 亿+ 元数据.
- cost_tier: free (完全免费, mailto polite pool)
- quality_score: 75.0
- region: ACADEMIC
- API: https://api.crossref.org/works
- 配置: CROSSREF_MAILTO (可选, polite pool 50 req/s)
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.settings import Settings
from src.skills.researcher.searchers import BaseSearcher, SearchRegion, register_searcher

logger = logging.getLogger(__name__)


@register_searcher("crossref")
class CrossRefSearcher(BaseSearcher):
    """CrossRef 学术搜索器.

    DOI 注册权威, 完全免费. 配置 mailto 进入 polite pool (50 req/s).
    """

    name = "crossref"
    region = SearchRegion.ACADEMIC
    cost_tier = "free"
    quality_score = 75.0

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://api.crossref.org/works"
        # mailto 用于 polite pool, 提高配额
        # P0-2: 字段已在 Settings 中声明, 直接访问 (消除 getattr 防御式编程)
        self.mailto = settings.crossref_mailto or ""

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """调用 CrossRef API."""
        params: dict[str, Any] = {
            "query": query,
            "rows": max_results,
        }
        if self.mailto:
            params["mailto"] = self.mailto

        headers = {"User-Agent": f"AIR/1.0 (mailto:{self.mailto})" if self.mailto else "AIR/1.0"}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(self.base_url, params=params, headers=headers)
        except Exception as e:
            logger.warning(f"crossref 调用失败: {e}")
            return []

        if resp.status_code != 200:
            logger.warning(f"crossref HTTP {resp.status_code}: {resp.text[:200]}")
            return []

        try:
            data = resp.json()
        except Exception as e:
            logger.warning(f"crossref JSON 解析失败: {e}")
            return []

        results: list[dict[str, Any]] = []
        for item in (data.get("message", {}).get("items") or [])[:max_results]:
            title = (item.get("title") or [""])[0]
            # DOI -> URL
            doi = item.get("DOI") or ""
            url = f"https://doi.org/{doi}" if doi else (item.get("URL") or "")
            # 摘要 (CrossRef 通常无摘要, 用 subtitle 兜底)
            subtitle = (item.get("subtitle") or [""])[0]
            snippet = subtitle or " ".join(item.get("abstract", "").split())[:300]
            if url:
                results.append(self._normalize_result(title, url, snippet))

        return self._filter_by_domains(results, query_domains)
