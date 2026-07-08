"""Exa 搜索 - 国外搜索引擎.

P2-Future-04: 对标 GPT Researcher retrievers/exa/exa.py.
通过 Exa API 进行语义搜索, 适用于全球场景.
需 EXA_API_KEY 环境变量 (Bearer token 鉴权).
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


class ExaSearcher(BaseSearcher):
    """Exa 搜索引擎 (全球场景, Bearer token 鉴权)."""

    name = "exa"
    region = SearchRegion.GLOBAL
    cost_tier = "paid"  # v1.1 新增
    quality_score = 76.0  # v1.1 新增

    _api_url: str = "https://api.exa.ai/search"

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._api_key = self.settings.exa_api_key
        self._client = httpx.AsyncClient(timeout=15.0)

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
                response = await self._client.post(self._api_url, headers=headers, json=payload)
                if response.status_code == 429:
                    reset_at = self._calc_quota_reset(response)
                    raise QuotaExceededError(
                        engine="exa",
                        reset_at=reset_at,
                        message="Exa 月度额度已满",
                    )
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
            except QuotaExceededError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("Exa 搜索失败: %s", e)
                span.update(metadata={"tool_name": "exa", "success": False, "error": str(e)})
                return []

    def _calc_quota_reset(self, resp: httpx.Response) -> datetime:
        """Exa 额度重置时间: 优先 Retry-After 头, 默认次月 1 日."""
        retry_after = resp.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return datetime.now(UTC) + timedelta(seconds=int(retry_after))
        now = datetime.now(UTC)
        if now.month == 12:
            return now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0)
        return now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0)

    async def close(self) -> None:
        await self._client.aclose()
