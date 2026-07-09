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
from src.observability.tracing import trace_tool
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
    quality_score = 78.0

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://metaso.cn/api/v1/search"
        self.api_key = settings.metaso_api_key or ""
        self._client = httpx.AsyncClient(timeout=30.0)

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

        async with trace_tool(
            name="metaso-search",
            input={"query": query[:100], "max_results": max_results},
            metadata={"tool_name": "metaso", "region": "cn"},
        ) as span:
            # 任务3 修复: 对齐秘塔 API 官方文档
            # 之前错误: 用 "num": int, 缺 scope, 缺 Accept 头 → API 拒绝/返回非网页数据
            # 官方文档 (CSDN 实测): {"q","scope":"webpage","size":str,"includeSummary","includeRawContent","conciseSnippet"}
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
            payload = {
                "q": query,
                "scope": "webpage",
                "size": str(max_results),  # 秘塔 API 要求 size 为字符串类型
                "includeSummary": True,  # 返回 summary 字段, 提升结果质量
                "includeRawContent": False,  # 不返回原文 (省流量, 抓取阶段单独处理)
                "conciseSnippet": True,  # 简洁摘要, 避免 snippet 过长
            }

            try:
                resp = await self._client.post(self.base_url, headers=headers, json=payload)
            except Exception as e:
                logger.warning(f"metaso 调用失败: {e}")
                span.update(metadata={"tool_name": "metaso", "success": False, "error": str(e)})
                return []

            # v1.1: 额度已满检测
            if resp.status_code in (429, 402):
                reset_at = self._calc_quota_reset(resp)
                span.update(
                    metadata={"tool_name": "metaso", "success": False, "error": "quota_exceeded"}
                )
                raise QuotaExceededError(
                    engine="metaso",
                    reset_at=reset_at,
                    message=f"秘塔搜索额度已满 (HTTP {resp.status_code})",
                )

            if resp.status_code != 200:
                logger.warning(f"metaso HTTP {resp.status_code}: {resp.text[:200]}")
                span.update(
                    metadata={
                        "tool_name": "metaso",
                        "success": False,
                        "error": f"http_{resp.status_code}",
                    }
                )
                return []

            try:
                data = resp.json()
            except Exception as e:
                logger.warning(f"metaso JSON 解析失败: {e}; body[:200]={resp.text[:200]}")
                span.update(
                    metadata={"tool_name": "metaso", "success": False, "error": "json_parse"}
                )
                return []

            results: list[dict[str, Any]] = []
            # 秘塔返回结构 (scope=webpage): {"result": {"webpages": [...]}} 或 {"webpages": [...]}
            # 兼容多种结构, 优先 result.webpages, 次选裸 webpages/results/data
            result_obj = data.get("result") if isinstance(data.get("result"), dict) else data
            items = (
                result_obj.get("webpages")
                or result_obj.get("results")
                or result_obj.get("data")
                or []
            )
            # 首次调用记录实际响应结构 (方便排查), 后续不重复日志
            if not getattr(self, "_resp_struct_logged", False):
                top_keys = list(data.keys())[:10] if isinstance(data, dict) else type(data).__name__
                inner_keys = (
                    list(result_obj.keys())[:10]
                    if isinstance(result_obj, dict)
                    else type(result_obj).__name__
                )
                logger.info(
                    f"metaso 响应结构: top_keys={top_keys}, inner_keys={inner_keys}, "
                    f"items_count={len(items) if isinstance(items, list) else 'N/A'}"
                )
                self._resp_struct_logged = True

            if not isinstance(items, list):
                logger.warning(f"metaso 返回 items 非列表: {type(items).__name__}")
                items = []

            for item in items[:max_results]:
                if not isinstance(item, dict):
                    continue
                title = item.get("title") or item.get("name") or ""
                url = item.get("url") or item.get("link") or ""
                snippet = item.get("snippet") or item.get("summary") or item.get("abstract") or ""
                if url:
                    results.append(self._normalize_result(title, url, snippet))

            # query_domains 后置过滤
            results = self._filter_by_domains(results, query_domains)
            span.update(
                output={"results_count": len(results)},
                metadata={"tool_name": "metaso", "success": True},
            )
            return results

    async def close(self) -> None:
        await self._client.aclose()

    def _calc_quota_reset(self, resp: httpx.Response) -> datetime:
        """额度重置时间: 优先 Retry-After 头, 默认 24 小时 (按日配额)."""
        retry_after = resp.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return datetime.now(UTC) + timedelta(seconds=int(retry_after))
        return datetime.now(UTC) + timedelta(hours=24)
