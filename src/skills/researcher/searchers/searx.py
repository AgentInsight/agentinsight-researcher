"""SearXNG 搜索 - 自托管元搜索引擎.

P2-Future-04: 对标 GPT Researcher retrievers/searx/searx.py.
通过自托管 SearXNG 实例进行搜索, 适用于全球场景.
无需 API Key, 需配置 SEARX_URL 环境变量 (默认 http://localhost:8080).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.settings import Settings
from src.observability.tracing import trace_tool
from src.skills.researcher.searchers import BaseSearcher, SearchRegion

logger = logging.getLogger(__name__)


class SearXNGSearcher(BaseSearcher):
    """SearXNG 自托管元搜索引擎 (全球场景, 无需 Key)."""

    name = "searx"
    region = SearchRegion.GLOBAL

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        # 拼接完整搜索端点: {searx_url}/search (去除尾部斜杠避免双斜杠)
        self._api_url = f"{self.settings.searx_url.rstrip('/')}/search"

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """SearXNG 搜索 (GET, JSON 格式).

        返回 [{"title","url","snippet","source","region"}].
        """
        async with trace_tool(
            name="searx-search",
            input={"query": query[:100], "max_results": max_results},
            metadata={"tool_name": "searx", "region": "global"},
        ) as span:
            try:
                params: dict[str, Any] = {
                    "q": query,
                    "format": "json",
                    "pageno": 1,
                }
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(self._api_url, params=params)
                    response.raise_for_status()
                    data = response.json()

                results: list[dict[str, Any]] = []
                # SearXNG 返回结构: {"results": [{"title": "", "url": "", "content": ""}]}
                for item in data.get("results", [])[:max_results]:
                    results.append(
                        self._normalize_result(
                            title=item.get("title", ""),
                            url=item.get("url", ""),
                            snippet=item.get("content", ""),
                        )
                    )

                results = self._filter_by_domains(results, query_domains)
                span.update(
                    output={"results_count": len(results)},
                    metadata={"tool_name": "searx", "success": True},
                )
                return results
            except Exception as e:  # noqa: BLE001
                logger.warning("SearXNG 搜索失败: %s", e)
                span.update(metadata={"tool_name": "searx", "success": False, "error": str(e)})
                return []
