"""博查搜索 (Bocha) - 国内中文搜索主力.

用户需求 5: 中文优先, 国内资料用国内搜索工具.
博查 API: https://api.bochaai.com/v1/web-search
需 BOCHA_API_KEY 环境变量.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.settings import Settings
from src.observability.tracing import trace_tool
from src.skills.researcher.searchers import BaseSearcher, SearchRegion

logger = logging.getLogger(__name__)


class BochaSearcher(BaseSearcher):
    """博查搜索引擎 (国内中文优先)."""

    name = "bocha"
    region = SearchRegion.CN

    _api_url: str = "https://api.bochaai.com/v1/web-search"

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._api_key = self.settings.bocha_api_key
        self._client = httpx.AsyncClient(timeout=30.0)

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """博查搜索.

        返回 [{"title","url","snippet","source","region"}].
        """
        if not self._api_key:
            logger.warning("Bocha API Key 未配置, 跳过博查搜索")
            return []

        async with trace_tool(
            name="bocha-search",
            input={"query": query[:100], "max_results": max_results},
            metadata={"tool_name": "bocha", "region": "cn"},
        ) as span:
            try:
                headers = {
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "query": query,
                    "freshness": "noLimit",
                    "summary": True,
                    "count": max_results,
                }
                response = await self._client.post(
                    self._api_url,
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

                results: list[dict[str, Any]] = []
                # 博查返回结构: {"data": {"webPages": {"value": [...]}}}
                web_pages = data.get("data", {}).get("webPages", {}).get("value", [])
                for item in web_pages[:max_results]:
                    results.append(
                        self._normalize_result(
                            title=item.get("name", ""),
                            url=item.get("url", ""),
                            snippet=item.get("snippet", "") or item.get("summary", ""),
                        )
                    )

                span.update(
                    output={"results_count": len(results)},
                    metadata={"tool_name": "bocha", "success": True},
                )
                return results
            except Exception as e:  # noqa: BLE001
                logger.warning("博查搜索失败: %s", e)
                span.update(metadata={"tool_name": "bocha", "success": False, "error": str(e)})
                return []

    async def close(self) -> None:
        await self._client.aclose()
