"""Semantic Scholar Graph API 学术搜索器.

AGENTS.md 第 9 章: 统一 httpx 异步.
Semantic Scholar Graph API 学术论文搜索, 适用于学术场景.
可选配置 SEMANTIC_SCHOLAR_API_KEY 提升配额.

P1-6 修复: 添加 CrossRef 作为备用源.
- Semantic Scholar 故障/限流/超时/空结果 → 自动切换 CrossRef
- CrossRef 免费、无需 API Key (mailto 进入 polite pool)
- 备用源切换通过 logger.warning 记录
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from src.config.settings import Settings
from src.observability.tracing import trace_tool
from src.skills.researcher.searchers import BaseSearcher, SearchRegion
from src.skills.researcher.searchers.exceptions import QuotaExceededError

logger = logging.getLogger(__name__)


class SemanticScholarSearcher(BaseSearcher):
    """Semantic Scholar 学术论文搜索 (Graph API).

    P1-6: 添加 CrossRef 备用源, 主源故障/限流/空结果时自动切换.
    """

    name = "semantic_scholar"
    region = SearchRegion.ACADEMIC
    cost_tier = "free"  # v1.1 新增
    quality_score = 80.0  # v1.1 新增

    _api_url: str = "https://api.semanticscholar.org/graph/v1/paper/search"
    _crossref_url: str = "https://api.crossref.org/works"  # P1-6 备用源
    _crossref_timeout: float = 30.0  # P1-6 CrossRef 总超时 (asyncio.wait_for)

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._api_key = self.settings.semantic_scholar_api_key
        # CrossRef polite pool mailto (可选, 提升配额至 50 req/s)
        self._crossref_mailto: str = getattr(self.settings, "crossref_mailto", "") or ""

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Semantic Scholar 搜索, 失败/限流/空结果时回退 CrossRef.

        返回 [{"title","url","snippet","source","region"}].
        备用源结果 source 字段标记为 "crossref" 以区分来源.
        """
        async with trace_tool(
            name="semantic-scholar-search",
            input={"query": query[:100], "max_results": max_results},
            metadata={"tool_name": "semantic_scholar", "region": "academic"},
        ) as span:
            # ===== 主源: Semantic Scholar =====
            quota_error: QuotaExceededError | None = None
            try:
                results = await self._search_semantic_scholar(query, max_results)
                results = self._filter_by_domains(results, query_domains)
                if results:
                    span.update(
                        output={
                            "results_count": len(results),
                            "source": "semantic_scholar",
                        },
                        metadata={"tool_name": "semantic_scholar", "success": True},
                    )
                    return results
                logger.warning(
                    "Semantic Scholar 返回空结果, 切换 CrossRef 备用源 (query=%s)",
                    query[:50],
                )
            except QuotaExceededError as e:
                quota_error = e
                logger.warning(
                    "Semantic Scholar 配额超限, 切换 CrossRef 备用源: %s",
                    e,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Semantic Scholar 搜索失败, 切换 CrossRef 备用源: %s",
                    e,
                )

            # ===== 备用源: CrossRef (30s 超时保护) =====
            try:
                results = await asyncio.wait_for(
                    self._search_crossref(query, max_results),
                    timeout=self._crossref_timeout,
                )
                results = self._filter_by_domains(results, query_domains)
                span.update(
                    output={
                        "results_count": len(results),
                        "source": "crossref_fallback",
                    },
                    metadata={
                        "tool_name": "semantic_scholar",
                        "success": True,
                        "fallback": "crossref",
                    },
                )
                return results
            except TimeoutError:
                logger.warning(
                    "CrossRef 备用源超时 (>%ss), 放弃",
                    self._crossref_timeout,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("CrossRef 备用源搜索失败: %s", e)

            # 双源均失败: 保留 QuotaExceededError 传播语义 (供 QuotaCache 标记)
            span.update(
                output={"results_count": 0},
                metadata={
                    "tool_name": "semantic_scholar",
                    "success": False,
                    "fallback_failed": True,
                },
            )
            if quota_error is not None:
                raise quota_error
            return []

    async def _search_semantic_scholar(
        self,
        query: str,
        max_results: int,
    ) -> list[dict[str, Any]]:
        """Semantic Scholar Graph API 主搜索 (P1-6: 抽取为独立方法, 逻辑不变)."""
        headers: dict[str, str] = {}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        params: dict[str, Any] = {
            "query": query,
            "limit": max_results,
            "fields": "title,url,abstract,year",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(self._api_url, headers=headers, params=params)
            if response.status_code == 429:
                reset_at = self._calc_quota_reset(response)
                raise QuotaExceededError(
                    engine="semantic_scholar",
                    reset_at=reset_at,
                    message="Semantic Scholar 频率限制",
                )
            response.raise_for_status()
            data = response.json()

        results: list[dict[str, Any]] = []
        # Semantic Scholar 返回结构: {"data": [...]}
        for item in data.get("data", [])[:max_results]:
            results.append(
                self._normalize_result(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("abstract", "") or "",
                )
            )
        return results

    async def _search_crossref(
        self,
        query: str,
        max_results: int,
    ) -> list[dict[str, Any]]:
        """CrossRef 备用搜索 (P1-6 修复).

        CrossRef API: https://api.crossref.org/works?query=...&rows=...
        响应 message.items 数组, 每项含:
          title[0], author[], abstract, published-print/published-online.date-parts,
          DOI, container-title[0]

        转换为统一格式 (与 SemanticScholar 相同结构):
          {"title","url","snippet","source","region"}
        source 字段标记为 "crossref" 以区分备用源.
        """
        params: dict[str, Any] = {
            "query": query,
            "rows": max_results,
        }
        if self._crossref_mailto:
            params["mailto"] = self._crossref_mailto

        headers = {
            "User-Agent": (
                f"AIR/1.0 (mailto:{self._crossref_mailto})" if self._crossref_mailto else "AIR/1.0"
            )
        }

        async with httpx.AsyncClient(timeout=self._crossref_timeout) as client:
            response = await client.get(self._crossref_url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()

        results: list[dict[str, Any]] = []
        # CrossRef 返回结构: {"message": {"items": [...]}}
        items = (data.get("message", {}) or {}).get("items", []) or []
        for item in items[:max_results]:
            title = (item.get("title") or [""])[0]
            doi = item.get("DOI") or ""
            url = f"https://doi.org/{doi}" if doi else (item.get("URL") or "")
            # 摘要 (CrossRef 通常无摘要, 用 subtitle 兜底, 再退而求其次用 abstract)
            subtitle = (item.get("subtitle") or [""])[0]
            abstract = item.get("abstract", "") or ""
            snippet = subtitle or " ".join(abstract.split())[:300]
            if not url:
                continue
            result = self._normalize_result(title=title, url=url, snippet=snippet)
            # 标记来源为 CrossRef 备用源
            result["source"] = "crossref"
            results.append(result)
        return results

    def _calc_quota_reset(self, resp: httpx.Response) -> datetime:
        """Semantic Scholar 额度重置时间: 优先 Retry-After 头, 默认 5 分钟后 (滚动窗口)."""
        retry_after = resp.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return datetime.now(UTC) + timedelta(seconds=int(retry_after))
        # Semantic Scholar 滚动窗口: 默认 5 分钟后
        return datetime.now(UTC) + timedelta(minutes=5)
