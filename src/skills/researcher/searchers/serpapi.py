"""SerpApi 搜索 - 国外搜索引擎.

P2-Future-04: 设计参考 retrievers/serpapi/serpapi.py.
通过 SerpApi 代理进行搜索, 适用于全球场景.
需 SERPAPI_KEY 环境变量 (query param 鉴权, 复用现有 serpapi_key).
与 google_searcher.py 共用 serpapi_key, 但作为独立引擎注册 (name=serpapi).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from src.config.settings import Settings
from src.observability.tracing import trace_tool
from src.skills.researcher.searchers import BaseSearcher, SearchRegion
from src.skills.researcher.searchers.exceptions import QuotaExceededError

logger = logging.getLogger(__name__)


class SerpApiSearcher(BaseSearcher):
    """SerpApi 搜索引擎 (全球场景, query param 鉴权, 复用 serpapi_key)."""

    name = "serpapi"
    region = SearchRegion.GLOBAL
    cost_tier = "paid"  # v1.1 新增
    quality_score = 82.2  # v1.1 新增

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
        """SerpApi 搜索 (GET, query param api_key).

        返回 [{"title","url","snippet","source","region"}].
        """
        if not self._api_key:
            logger.warning("SerpApi Key 未配置, 跳过 SerpApi 搜索")
            return []

        async with trace_tool(
            name="serpapi-search",
            input={"query": query[:100], "max_results": max_results},
            metadata={"tool_name": "serpapi", "region": "global"},
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
                    if response.status_code == 429:
                        reset_at = self._calc_quota_reset(response)
                        raise QuotaExceededError(
                            engine="serpapi",
                            reset_at=reset_at,
                            message="SerpApi 月度额度已满",
                        )
                    response.raise_for_status()
                    data = response.json()

                results: list[dict[str, Any]] = []
                # SerpApi 返回结构: {"organic_results": [{"title": "", "link": "", "snippet": ""}]}
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
                    metadata={"tool_name": "serpapi", "success": True},
                )
                return results
            except QuotaExceededError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("SerpApi 搜索失败: %s", e)
                span.update(metadata={"tool_name": "serpapi", "success": False, "error": str(e)})
                return []

    def _calc_quota_reset(self, resp: httpx.Response) -> datetime:
        """SerpApi 额度重置时间: 优先 Retry-After 头, 默认次月 1 日."""
        retry_after = resp.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return datetime.now(UTC) + timedelta(seconds=int(retry_after))
        now = datetime.now(UTC)
        if now.month == 12:
            return now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0)
        return now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0)
