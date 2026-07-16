"""GDELT 新闻搜索 (gdeltproject.org, v1.1 新增).

40 年全球新闻事件数据库, 完全免费.
- cost_tier: free (完全免费)
- quality_score: 65.0
- region: AUTO (CN+GLOBAL 都可用)
- API: https://api.gdeltproject.org/api/v2/doc/doc
- E11: GDELT 免费 API 限流, 每 5 秒 1 次, 并发请求触发 429
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from src.config.settings import Settings
from src.skills.researcher.searchers import BaseSearcher, SearchRegion, register_searcher

logger = logging.getLogger(__name__)

# E11: GDELT 免费 API 全局限流 (每 5 秒 1 次请求)
# 模块级锁 + 上次请求时间戳, 确保所有 GDELTSearcher 实例共享同一个限流器
_GDELT_LOCK = asyncio.Lock()
_GDELT_LAST_REQUEST_TIME: float = 0.0
_GDELT_MIN_INTERVAL: float = 5.0  # 秒, GDELT 要求至少 5 秒间隔


@register_searcher("gdelt")
class GDELTSearcher(BaseSearcher):
    """GDELT 新闻搜索器.

    40 年全球新闻事件数据库, 完全免费.
    E11: 内置 5 秒请求间隔限流, 避免触发 429.
    """

    name = "gdelt"
    region = SearchRegion.AUTO
    cost_tier = "free"
    quality_score = 65.0

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://api.gdeltproject.org/api/v2/doc/doc"

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """调用 GDELT API (内置 5 秒限流)."""
        params = {
            "query": query,
            "mode": "ArtList",
            "maxrecords": str(max_results),
            "format": "json",
            "sort": "DateDesc",
        }

        # E11: 全局限流, 确保至少 5 秒间隔
        global _GDELT_LAST_REQUEST_TIME
        async with _GDELT_LOCK:
            now = time.monotonic()
            elapsed = now - _GDELT_LAST_REQUEST_TIME
            if elapsed < _GDELT_MIN_INTERVAL:
                wait_time = _GDELT_MIN_INTERVAL - elapsed
                logger.debug("gdelt 限流等待 %.1f 秒", wait_time)
                await asyncio.sleep(wait_time)
            _GDELT_LAST_REQUEST_TIME = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(self.base_url, params=params)
        except Exception as e:
            logger.warning(f"gdelt 调用失败: {e}")
            return []

        if resp.status_code == 429:
            logger.warning("gdelt HTTP 429: 请求过于频繁, 已触发限流 (间隔需 ≥5 秒)")
            return []
        if resp.status_code != 200:
            logger.warning(f"gdelt HTTP {resp.status_code}: {resp.text[:200]}")
            return []

        try:
            data = resp.json()
        except Exception as e:
            # GDELT 有时返回非标准 JSON, 降级处理
            logger.warning(f"gdelt JSON 解析失败: {e}")
            return []

        results: list[dict[str, Any]] = []
        for item in (data.get("articles") or [])[:max_results]:
            title = item.get("title") or ""
            url = item.get("url") or ""
            snippet = item.get("socialimage") or item.get("summary") or ""
            if url:
                results.append(self._normalize_result(title, url, snippet))

        return self._filter_by_domains(results, query_domains)
