"""Exa 搜索 - 国外搜索引擎.

P2-Future-04: 对标 GPT Researcher retrievers/exa/exa.py.
通过 Exa API 进行语义搜索, 适用于全球场景.
需 EXA_API_KEY 环境变量 (Bearer token 鉴权).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.settings import Settings
from src.observability.tracing import trace_tool
from src.skills.researcher.searchers import BaseSearcher, SearchRegion

logger = logging.getLogger(__name__)


class ExaSearcher(BaseSearcher):
    """Exa 搜索引擎 (全球场景, Bearer token 鉴权)."""

    name = "exa"
    region = SearchRegion.GLOBAL

    _api_url: str = "https://api.exa.ai/search"

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._api_key = self.settings.exa_api_key

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Exa 搜索 (POST, Bearer token).

        返回 [{"title","url","snippet","source","region"}].
        """
        if not self._api_key:
            logger.warning("Exa API Key 未配置, 跳过 Exa 搜索")
            return []

        async with trace_tool(
            name="exa-search",
            input={"query": query[:100], "max_results": max_results},
            metadata={"tool_name": "exa", "region": "global"},
        ) as span:
            try:
                headers = {
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                }
                payload: dict[str, Any] = {
                    "query": query,
                    "num_results": max_results,
                    "use_autoprompt": True,
                    "contents": {
                        "text": {"maxCharacters": 1000},
                    },
                }
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.post(self._api_url, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()

                results: list[dict[str, Any]] = []
                # Exa 返回结构: {"results": [{"title": "", "url": "", "text": ""}]}
                for item in data.get("results", [])[:max_results]:
                    results.append(
                        self._normalize_result(
                            title=item.get("title", ""),
                            url=item.get("url", ""),
                            snippet=item.get("text", ""),
                        )
                    )

                results = self._filter_by_domains(results, query_domains)
                span.update(
                    output={"results_count": len(results)},
                    metadata={"tool_name": "exa", "success": True},
                )
                return results
            except Exception as e:  # noqa: BLE001
                logger.warning("Exa 搜索失败: %s", e)
                span.update(metadata={"tool_name": "exa", "success": False, "error": str(e)})
                return []
