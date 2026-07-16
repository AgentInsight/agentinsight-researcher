"""博查搜索 (Bocha) - 国内中文搜索主力.

用户需求 5: 中文优先, 国内资料用国内搜索工具.
博查 API: https://api.bochaai.com/v1/web-search
需 BOCHA_API_KEY 环境变量.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from src.common.http_client import get_http_client_pool
from src.config.settings import Settings
from src.observability.tracing import trace_tool
from src.skills.researcher.searchers import BaseSearcher, SearchRegion
from src.skills.researcher.searchers.exceptions import QuotaExceededError

logger = logging.getLogger(__name__)


class BochaSearcher(BaseSearcher):
    """博查搜索引擎 (国内中文优先)."""

    name = "bocha"
    region = SearchRegion.CN
    cost_tier = "paid"  # v1.1 新增
    quality_score = 62.0  # v1.1 新增; v2: 降低权重 (口令配额不稳定, 质量低于 EXA)

    _api_url: str = "https://api.bochaai.com/v1/web-search"

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._api_key = self.settings.bocha_api_key

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
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
                pool = await get_http_client_pool()
                client = await pool.get_client(self.name)
                response = await client.post(
                    self._api_url,
                    headers=headers,
                    json=payload,
                )
                if response.status_code == 429:
                    reset_at = self._calc_quota_reset(response)
                    raise QuotaExceededError(
                        engine="bocha",
                        reset_at=reset_at,
                        message="博查搜索额度已满",
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

                results = self._filter_by_domains(results, query_domains)
                span.update(
                    output={"results_count": len(results)},
                    metadata={"tool_name": "bocha", "success": True},
                )
                return results
            except QuotaExceededError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.error("博查搜索失败 (query=%s): %s", query[:100], e, exc_info=True)
                span.update(metadata={"tool_name": "bocha", "success": False, "error": str(e)})
                return []

    def _calc_quota_reset(self, resp: httpx.Response) -> datetime:
        """博查额度重置时间: 优先 Retry-After 头, 默认次日 00:00 UTC (按日配额)."""
        retry_after = resp.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return datetime.now(UTC) + timedelta(seconds=int(retry_after))
        # Bocha 按日配额: 默认次日 00:00 UTC
        now = datetime.now(UTC)
        return now.replace(hour=0, minute=0, second=0) + timedelta(days=1)

    async def close(self) -> None:
        """无操作 (httpx 客户端由 HttpClientPool 统一管理生命周期)."""
