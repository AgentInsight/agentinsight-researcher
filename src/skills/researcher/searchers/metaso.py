"""秘塔 AI 搜索 (metaso.cn, v1.1 新增).

国内 AI 搜索主力, 支持多模态搜索.
- cost_tier: freemium (有免费额度, 超出后 0.03 元/次)
- quality_score: 75.0
- region: CN
- API: https://metaso.cn/api/v1/search
- 注册: https://metaso.cn/api 获取 API Key

额度已满时抛出 QuotaExceededError 触发缓存机制.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from src.config.settings import Settings
from src.skills.researcher.searchers import BaseSearcher, SearchRegion, register_searcher
from src.skills.researcher.searchers.exceptions import QuotaExceededError

logger = logging.getLogger(__name__)


@register_searcher("metaso")
class MetasoSearcher(BaseSearcher):
    """秘塔 AI 搜索器.

    国内 AI 搜索主力, 0.03 元/次, 新用户赠点.
    """

    name = "metaso"
    region = SearchRegion.CN
    cost_tier = "freemium"
    quality_score = 75.0

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://metaso.cn/api/v1/search"
        self.api_key = settings.metaso_api_key or ""

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """调用秘塔 AI 搜索 API."""
        if not self.api_key:
            logger.warning("MetasoSearcher: api_key 未配置")
            return []

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "q": query,
            "num": max_results,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(self.base_url, headers=headers, json=payload)
        except Exception as e:
            logger.warning(f"metaso 调用失败: {e}")
            return []

        # v1.1: 额度已满检测
        if resp.status_code in (429, 402):
            reset_at = self._calc_quota_reset(resp)
            raise QuotaExceededError(
                engine="metaso",
                reset_at=reset_at,
                message=f"秘塔搜索额度已满 (HTTP {resp.status_code})",
            )

        if resp.status_code != 200:
            logger.warning(f"metaso HTTP {resp.status_code}: {resp.text[:200]}")
            return []

        try:
            data = resp.json()
        except Exception as e:
            logger.warning(f"metaso JSON 解析失败: {e}")
            return []

        results: list[dict[str, Any]] = []
        # 秘塔返回结构: {"webpages": [{"title":"", "link":"", "snippet":""}]}
        # 兼容旧字段 results/data
        items = data.get("webpages") or data.get("results") or data.get("data") or []
        for item in items[:max_results]:
            title = item.get("title") or item.get("name") or ""
            url = item.get("url") or item.get("link") or ""
            snippet = item.get("snippet") or item.get("summary") or item.get("abstract") or ""
            if url:
                results.append(self._normalize_result(title, url, snippet))

        # query_domains 后置过滤
        return self._filter_by_domains(results, query_domains)

    def _calc_quota_reset(self, resp: httpx.Response) -> datetime:
        """额度重置时间: 优先 Retry-After 头, 默认 24 小时 (按日配额)."""
        retry_after = resp.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return datetime.now(UTC) + timedelta(seconds=int(retry_after))
        return datetime.now(UTC) + timedelta(hours=24)
