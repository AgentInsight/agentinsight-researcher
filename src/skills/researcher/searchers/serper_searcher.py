"""Serper.dev Google Search API 搜索器.

AGENTS.md 第 9 章: 统一 httpx 异步.
Serper.dev Google Search API, 适用于全球场景.
需 SERPER_API_KEY 环境变量.
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


class SerperSearcher(BaseSearcher):
    """Serper.dev Google 搜索引擎 (全球场景)."""

    name = "serper"
    region = SearchRegion.GLOBAL
    cost_tier = "paid"  # v1.1 新增
    quality_score = 82.2  # v1.1 新增

    _api_url: str = "https://google.serper.dev/search"

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._api_key = self.settings.serper_api_key
        self._client = httpx.AsyncClient(timeout=15.0)

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
                response = await self._client.post(self._api_url, headers=headers, json=payload)
                if response.status_code == 429:
                    reset_at = self._calc_quota_reset(response)
                    raise QuotaExceededError(
                        engine="serper",
                        reset_at=reset_at,
                        message="Serper 额度已满",
                    )
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
            except QuotaExceededError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("Serper 搜索失败: %s", e)
                span.update(metadata={"tool_name": "serper", "success": False, "error": str(e)})
                return []

    def _calc_quota_reset(self, resp: httpx.Response) -> datetime:
        """Serper 额度重置时间: 优先 Retry-After 头, 默认 24 小时后."""
        retry_after = resp.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return datetime.now(UTC) + timedelta(seconds=int(retry_after))
        return datetime.now(UTC) + timedelta(hours=24)

    async def close(self) -> None:
        await self._client.aclose()
