"""Semantic Scholar Graph API 学术搜索器.

AGENTS.md 第 9 章: 统一 httpx 异步.
Semantic Scholar Graph API 学术论文搜索, 适用于学术场景.
可选配置 SEMANTIC_SCHOLAR_API_KEY 提升配额.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.settings import Settings
from src.observability.tracing import trace_tool
from src.skills.researcher.searchers import BaseSearcher, SearchRegion

logger = logging.getLogger(__name__)


class SemanticScholarSearcher(BaseSearcher):
    """Semantic Scholar 学术论文搜索 (Graph API)."""

    name = "semantic_scholar"
    region = SearchRegion.ACADEMIC

    _api_url: str = "https://api.semanticscholar.org/graph/v1/paper/search"

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._api_key = self.settings.semantic_scholar_api_key

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Semantic Scholar 搜索.

        返回 [{"title","url","snippet","source","region"}].
        """
        async with trace_tool(
            name="semantic-scholar-search",
            input={"query": query[:100], "max_results": max_results},
            metadata={"tool_name": "semantic_scholar", "region": "academic"},
        ) as span:
            try:
                headers: dict[str, str] = {}
                if self._api_key:
                    headers["x-api-key"] = self._api_key
                params: dict[str, Any] = {
                    "query": query,
                    "limit": max_results,
                    "fields": "title,url,abstract,year",
                }
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(self._api_url, headers=headers, params=params)
                    response.raise_for_status()
                    data = response.json()

                results: list[dict[str, Any]] = []
                # Semantic Scholar 返回结构: {"data": [...]}
                for item in data.get("data", [])[:max_results]:
                    results.append(
                        self._normalize_result(
                            title=item.get("title", ""),
                            url=item.get("url", ""),
                            snippet=item.get("abstract", "") or "",
                        )
                    )

                results = self._filter_by_domains(results, query_domains)
                span.update(
                    output={"results_count": len(results)},
                    metadata={"tool_name": "semantic_scholar", "success": True},
                )
                return results
            except Exception as e:  # noqa: BLE001
                logger.warning("Semantic Scholar 搜索失败: %s", e)
                span.update(
                    metadata={
                        "tool_name": "semantic_scholar",
                        "success": False,
                        "error": str(e),
                    }
                )
                return []
