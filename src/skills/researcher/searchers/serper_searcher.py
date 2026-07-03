"""Serper.dev Google Search API 搜索器.

AGENTS.md 第 9 章: 统一 httpx 异步.
Serper.dev Google Search API, 适用于全球场景.
需 SERPER_API_KEY 环境变量.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.settings import Settings
from src.observability.tracing import trace_tool
from src.skills.researcher.searchers import BaseSearcher, SearchRegion

logger = logging.getLogger(__name__)


class SerperSearcher(BaseSearcher):
    """Serper.dev Google 搜索引擎 (全球场景)."""

    name = "serper"
    region = SearchRegion.GLOBAL

    _api_url: str = "https://google.serper.dev/search"

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._api_key = self.settings.serper_api_key

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Serper.dev Google 搜索.

        返回 [{"title","url","snippet","source","region"}].
        """
        if not self._api_key:
            logger.warning("Serper API Key 未配置, 跳过 Serper 搜索")
            return []

        async with trace_tool(
            name="serper-search",
            input={"query": query[:100], "max_results": max_results},
            metadata={"tool_name": "serper", "region": "global"},
        ) as span:
            try:
                headers = {
                    "X-API-KEY": self._api_key,
                    "Content-Type": "application/json",
                }
                payload = {"q": query, "num": max_results}
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.post(self._api_url, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()

                results: list[dict[str, Any]] = []
                # Serper 返回结构: {"organic": [...]}
                for item in data.get("organic", [])[:max_results]:
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
                    metadata={"tool_name": "serper", "success": True},
                )
                return results
            except Exception as e:  # noqa: BLE001
                logger.warning("Serper 搜索失败: %s", e)
                span.update(metadata={"tool_name": "serper", "success": False, "error": str(e)})
                return []
