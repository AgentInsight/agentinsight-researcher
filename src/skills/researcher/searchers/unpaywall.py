"""Unpaywall OA 查找 (unpaywall.org, v1.1 新增).

OA 版本查找神器, 100,000 次/天免费.
- cost_tier: free (完全免费, 真实邮箱必填)
- quality_score: 70.0
- region: ACADEMIC
- API: https://api.unpaywall.org/v2/{doi}?email=...
- 配置: UNPAYWALL_EMAIL (必填, 否则 HTTP 422 拒绝)

注意: Unpaywall 通过 DOI 查询 OA 版本, 不支持关键词搜索.
本搜索器接收 query 参数后, 如果是 DOI 则直接查询;
否则返回空 (建议在调用前由其他搜索器获取 DOI).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.settings import Settings
from src.skills.researcher.searchers import BaseSearcher, SearchRegion, register_searcher

logger = logging.getLogger(__name__)


@register_searcher("unpaywall")
class UnpaywallSearcher(BaseSearcher):
    """Unpaywall OA 查找器.

    通过 DOI 查找 Open Access 版本. 100k/天免费.
    """

    name = "unpaywall"
    region = SearchRegion.ACADEMIC
    cost_tier = "free"
    quality_score = 70.0

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://api.unpaywall.org/v2"
        # P0-2: 字段已在 Settings 中声明, 直接访问 (消除 getattr 防御式编程)
        self.email = settings.unpaywall_email or ""

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """通过 DOI 查找 OA 版本.

        如果 query 不是 DOI 格式, 返回空列表.
        """
        if not self.email:
            logger.warning("UnpaywallSearcher: unpaywall_email 未配置 (必填)")
            return []

        # 简单判断是否 DOI (10.xxxx/yyy)
        query_stripped = query.strip()
        is_doi = query_stripped.startswith("10.") and "/" in query_stripped
        if not is_doi:
            # 非 DOI 查询, Unpaywall 无法处理
            return []

        doi = query_stripped
        url = f"{self.base_url}/{doi}?email={self.email}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url)
        except Exception as e:
            logger.warning(f"unpaywall 调用失败: {e}")
            return []

        if resp.status_code != 200:
            logger.warning(f"unpaywall HTTP {resp.status_code}: {resp.text[:200]}")
            return []

        try:
            data = resp.json()
        except Exception as e:
            logger.warning(f"unpaywall JSON 解析失败: {e}")
            return []

        # Unpaywall 返回单条记录, 转为列表
        title = data.get("title") or ""
        best_oa = data.get("best_oa_location") or {}
        oa_url = best_oa.get("url") or best_oa.get("url_for_pdf") or ""
        snippet = f"OA版本: {data.get('oa_status', 'unknown')}"

        if oa_url:
            return [self._normalize_result(title, oa_url, snippet)]
        return []


@register_searcher("unpaywall_doi")
class UnpaywallDOISearcher(UnpaywallSearcher):
    """Unpaywall DOI 查询别名 (用于显式 DOI 查询)."""

    name = "unpaywall_doi"
