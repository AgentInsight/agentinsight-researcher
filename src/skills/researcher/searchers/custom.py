"""Custom 搜索引擎 - 企业私有端点 (无 API Key).

对标 GPT Researcher retrievers/custom/custom.py.
GPTR 通过 `custom_retriever_endpoint` + `custom_retriever_arg` 配置接入企业
自建检索服务, 实现一次接入复用 20+ retriever 之外的私有数据源.

实现差异:
- GPTR 用环境变量直接读取 (避免侵入 settings.py, 用户无需改配置类).
- 用 httpx.AsyncClient 异步调用 (AIR 统一约定, 禁 requests 同步).
- 返回 AIR 统一规约: [{"title","url","snippet","source","region"}]
  (GPTR 原生返回 title/href/body, 由本类映射到 AIR 字段).

环境变量:
- CUSTOM_RETRIEVER_ENDPOINT: 私有检索端点 URL (POST JSON).
- CUSTOM_RETRIEVER_ARG: 端点接收的查询参数名 (默认 "query").
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from src.config.settings import Settings
from src.observability.tracing import trace_tool
from src.skills.researcher.searchers import BaseSearcher, SearchRegion

logger = logging.getLogger(__name__)


class CustomSearcher(BaseSearcher):
    """企业私有端点搜索引擎 (无 API Key, 自托管).

    仅当环境变量 CUSTOM_RETRIEVER_ENDPOINT 配置时启用,
    由 get_searchers() 工厂按需实例化.
    """

    name = "custom"
    region = SearchRegion.GLOBAL
    cost_tier = "free"  # v1.1 新增
    quality_score = 50.0  # v1.1 新增

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        # 直接从环境变量读取, 不侵入 settings.py (任务约束)
        self._endpoint: str = os.getenv("CUSTOM_RETRIEVER_ENDPOINT", "")
        self._arg: str = os.getenv("CUSTOM_RETRIEVER_ARG", "query")
        self._client = httpx.AsyncClient(timeout=30.0)

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """调用企业私有检索端点.

        端点契约 (POST application/json):
            请求: {<arg>: query, "max_results": max_results}
            响应: {"results": [{"title","href","body"} | {"title","url","snippet"}]}
        """
        if not self._endpoint:
            logger.warning("CustomSearcher: CUSTOM_RETRIEVER_ENDPOINT 未配置, 跳过")
            return []

        async with trace_tool(
            name="custom-search",
            input={"query": query[:100], "max_results": max_results, "endpoint": self._endpoint},
            metadata={"tool_name": "custom", "region": "global"},
        ) as span:
            try:
                payload: dict[str, Any] = {
                    self._arg: query,
                    "max_results": max_results,
                }
                response = await self._client.post(self._endpoint, json=payload)
                response.raise_for_status()
                data = response.json()

                results: list[dict[str, Any]] = []
                # 兼容 GPTR (title/href/body) 与 AIR (title/url/snippet) 两种字段命名
                for item in data.get("results", [])[:max_results]:
                    title = item.get("title", "")
                    url = item.get("url") or item.get("href") or ""
                    snippet = item.get("snippet") or item.get("body") or ""
                    results.append(self._normalize_result(title=title, url=url, snippet=snippet))

                results = self._filter_by_domains(results, query_domains)
                span.update(
                    output={"results_count": len(results)},
                    metadata={"tool_name": "custom", "success": True},
                )
                return results
            except Exception as e:  # noqa: BLE001
                logger.warning("Custom 搜索失败 (endpoint=%s): %s", self._endpoint, e)
                span.update(metadata={"tool_name": "custom", "success": False, "error": str(e)})
                return []

    async def close(self) -> None:
        await self._client.aclose()
