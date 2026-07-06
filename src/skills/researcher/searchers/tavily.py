"""Tavily 搜索 - 国外搜索引擎.

用户需求 5: 国外资料搜索, 参考 GPT Researcher 方式.
对标 GPT Researcher retrievers/tavily/tavily_search.py.
需 TAVILY_API_KEY 环境变量.
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


class TavilySearcher(BaseSearcher):
    """Tavily 搜索引擎 (国外)."""

    name = "tavily"
    region = SearchRegion.GLOBAL
    cost_tier = "paid"  # v1.1 新增
    quality_score = 93.3  # v1.1 新增

    _api_url: str = "https://api.tavily.com/search"

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._api_key = self.settings.tavily_api_key
        self._client = httpx.AsyncClient(timeout=30.0)

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Tavily 搜索."""
        if not self._api_key:
            logger.warning("Tavily API Key 未配置, 跳过 Tavily 搜索")
            return []

        async with trace_tool(
            name="tavily-search",
            input={"query": query[:100], "max_results": max_results},
            metadata={"tool_name": "tavily", "region": "global"},
        ) as span:
            try:
                payload = {
                    "api_key": self._api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": kwargs.get("search_depth", "basic"),
                    "include_answer": False,
                    "include_raw_content": False,
                }
                if query_domains:
                    payload["include_domains"] = query_domains
                response = await self._client.post(self._api_url, json=payload)
                if response.status_code == 429:
                    reset_at = self._calc_quota_reset(response)
                    raise QuotaExceededError(
                        engine="tavily",
                        reset_at=reset_at,
                        message="Tavily 月度额度已满",
                    )
                response.raise_for_status()
                data = response.json()

                results: list[dict[str, Any]] = []
                for item in data.get("results", [])[:max_results]:
                    results.append(
                        self._normalize_result(
                            title=item.get("title", ""),
                            url=item.get("url", ""),
                            snippet=item.get("content", ""),
                        )
                    )

                span.update(
                    output={"results_count": len(results)},
                    metadata={"tool_name": "tavily", "success": True},
                )
                return results
            except QuotaExceededError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("Tavily 搜索失败: %s", e)
                span.update(metadata={"tool_name": "tavily", "success": False, "error": str(e)})
                return []

    def _calc_quota_reset(self, resp: httpx.Response) -> datetime:
        """Tavily 额度重置时间: 优先 Retry-After 头, 默认次月 1 日."""
        retry_after = resp.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return datetime.now(UTC) + timedelta(seconds=int(retry_after))
        # Tavily 月度配额: 默认次月 1 日 00:00 UTC
        now = datetime.now(UTC)
        if now.month == 12:
            next_month = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0)
        else:
            next_month = now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0)
        return next_month

    async def close(self) -> None:
        await self._client.aclose()
