"""Brave Search API 搜索器.

AGENTS.md 第 9 章: 统一 httpx 异步.
Brave Search 无中国境内可用, 适用于全球场景.
需 BRAVE_API_KEY 环境变量.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.settings import Settings
from src.observability.tracing import trace_tool
from src.skills.researcher.searchers import BaseSearcher, SearchRegion

logger = logging.getLogger(__name__)


class BraveSearcher(BaseSearcher):
    """Brave Search 搜索引擎 (全球场景, 无中国境内可用)."""

    name = "brave"
    region = SearchRegion.GLOBAL
    cost_tier = "paid"  # v1.1 新增
    quality_score = 76.1  # v1.1 新增

    _api_url: str = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._api_key = self.settings.brave_api_key

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Brave 搜索.

        返回 [{"title","url","snippet","source","region"}].
        """
        if not self._api_key:
            logger.warning("Brave API Key 未配置, 跳过 Brave 搜索")
            return []

        async with trace_tool(
            name="brave-search",
            input={"query": query[:100], "max_results": max_results},
            metadata={"tool_name": "brave", "region": "global"},
        ) as span:
            try:
                headers = {
                    "X-Subscription-Token": self._api_key,
                    "Accept": "application/json",
                }
                params: dict[str, Any] = {"q": query, "count": max_results}
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(self._api_url, headers=headers, params=params)
                    response.raise_for_status()
                    data = response.json()

                results: list[dict[str, Any]] = []
                for item in data.get("web", {}).get("results", [])[:max_results]:
                    results.append(
                        self._normalize_result(
                            title=item.get("title", ""),
                            url=item.get("url", ""),
                            snippet=item.get("description", ""),
                        )
                    )

                results = self._filter_by_domains(results, query_domains)
                span.update(
                    output={"results_count": len(results)},
                    metadata={"tool_name": "brave", "success": True},
                )
                return results
            except Exception as e:  # noqa: BLE001
                logger.warning("Brave 搜索失败: %s", e)
                span.update(metadata={"tool_name": "brave", "success": False, "error": str(e)})
                return []
