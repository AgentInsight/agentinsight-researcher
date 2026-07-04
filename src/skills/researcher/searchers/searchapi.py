"""SearchAPI 搜索 - 国外搜索引擎.

P2-Future-04: 对标 GPT Researcher retrievers/searchapi/searchapi.py.
通过 SearchAPI.io 进行搜索, 适用于全球场景.
需 SEARCHAPI_API_KEY 环境变量 (query param 鉴权).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.settings import Settings
from src.observability.tracing import trace_tool
from src.skills.researcher.searchers import BaseSearcher, SearchRegion

logger = logging.getLogger(__name__)


class SearchApiSearcher(BaseSearcher):
    """SearchAPI 搜索引擎 (全球场景, query param 鉴权)."""

    name = "searchapi"
    region = SearchRegion.GLOBAL

    _api_url: str = "https://www.searchapi.io/api/v1/search"

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._api_key = self.settings.searchapi_api_key

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """SearchAPI 搜索 (GET, query param api_key).

        返回 [{"title","url","snippet","source","region"}].
        """
        if not self._api_key:
            logger.warning("SearchAPI Key 未配置, 跳过 SearchAPI 搜索")
            return []

        async with trace_tool(
            name="searchapi-search",
            input={"query": query[:100], "max_results": max_results},
            metadata={"tool_name": "searchapi", "region": "global"},
        ) as span:
            try:
                params: dict[str, Any] = {
                    "api_key": self._api_key,
                    "engine": "google",
                    "q": query,
                    "num": max_results,
                }
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(self._api_url, params=params)
                    response.raise_for_status()
                    data = response.json()

                results: list[dict[str, Any]] = []
                # SearchAPI 返回结构: {"organic_results": [{"title": "", "link": "", "snippet": ""}]}
                for item in data.get("organic_results", [])[:max_results]:
                    results.append(
                        self._normalize_result(
                            title=item.get("title", ""),
                            url=item.get("link", ""),
                            snippet=item.get("snippet", ""),
                        )
                    )

                results = self._filter_by_domains(results, query_domains)
                span.update(
                    output={"results_count": len(results)},
                    metadata={"tool_name": "searchapi", "success": True},
                )
                return results
            except Exception as e:  # noqa: BLE001
                logger.warning("SearchAPI 搜索失败: %s", e)
                span.update(metadata={"tool_name": "searchapi", "success": False, "error": str(e)})
                return []
