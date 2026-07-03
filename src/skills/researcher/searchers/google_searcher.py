"""Google 搜索器 (经 SerpApi).

AGENTS.md 第 9 章: 统一 httpx 异步.
通过 SerpApi 代理访问 Google 搜索结果, 适用于全球场景.
需 SERPAPI_KEY 环境变量.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.settings import Settings
from src.observability.tracing import trace_tool
from src.skills.researcher.searchers import BaseSearcher, SearchRegion

logger = logging.getLogger(__name__)


class GoogleSearcher(BaseSearcher):
    """Google 搜索引擎 (经 SerpApi 代理, 全球场景)."""

    name = "google"
    region = SearchRegion.GLOBAL

    _api_url: str = "https://serpapi.com/search"

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._api_key = self.settings.serpapi_key

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Google 搜索 (经 SerpApi).

        返回 [{"title","url","snippet","source","region"}].
        """
        if not self._api_key:
            logger.warning("SerpApi Key 未配置, 跳过 Google 搜索")
            return []

        async with trace_tool(
            name="google-search",
            input={"query": query[:100], "max_results": max_results},
            metadata={"tool_name": "google", "region": "global"},
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
                # SerpApi 返回结构: {"organic_results": [...]}
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
                    metadata={"tool_name": "google", "success": True},
                )
                return results
            except Exception as e:  # noqa: BLE001
                logger.warning("Google 搜索失败: %s", e)
                span.update(metadata={"tool_name": "google", "success": False, "error": str(e)})
                return []
