"""Bing Web Search API 搜索器.

AGENTS.md 第 9 章: 统一 httpx 异步.
Bing Web Search API (Microsoft Azure), 适用于全球场景.
需 BING_API_KEY 环境变量.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.settings import Settings
from src.observability.tracing import trace_tool
from src.skills.researcher.searchers import BaseSearcher, SearchRegion

logger = logging.getLogger(__name__)


class BingSearcher(BaseSearcher):
    """Bing Web Search 搜索引擎 (全球场景)."""

    name = "bing"
    region = SearchRegion.GLOBAL

    _api_url: str = "https://api.bing.microsoft.com/v7.0/search"

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._api_key = self.settings.bing_api_key

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Bing 搜索.

        返回 [{"title","url","snippet","source","region"}].
        """
        if not self._api_key:
            logger.warning("Bing API Key 未配置, 跳过 Bing 搜索")
            return []

        async with trace_tool(
            name="bing-search",
            input={"query": query[:100], "max_results": max_results},
            metadata={"tool_name": "bing", "region": "global"},
        ) as span:
            try:
                headers = {"Ocp-Apim-Subscription-Key": self._api_key}
                params: dict[str, Any] = {"q": query, "count": max_results}
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(self._api_url, headers=headers, params=params)
                    response.raise_for_status()
                    data = response.json()

                results: list[dict[str, Any]] = []
                # Bing 返回结构: {"webPages": {"value": [...]}}
                web_pages = data.get("webPages", {}).get("value", [])
                for item in web_pages[:max_results]:
                    results.append(
                        self._normalize_result(
                            title=item.get("name", ""),
                            url=item.get("url", ""),
                            snippet=item.get("snippet", ""),
                        )
                    )

                results = self._filter_by_domains(results, query_domains)
                span.update(
                    output={"results_count": len(results)},
                    metadata={"tool_name": "bing", "success": True},
                )
                return results
            except Exception as e:  # noqa: BLE001
                logger.warning("Bing 搜索失败: %s", e)
                span.update(metadata={"tool_name": "bing", "success": False, "error": str(e)})
                return []
