"""SearXNG 搜索 - 自托管元搜索引擎.

P2-Future-04: 对标 GPT Researcher retrievers/searx/searx.py.
通过自托管 SearXNG 实例进行搜索, 适用于全球场景.
无需 API Key, 需配置 SEARX_URL 环境变量 (默认 http://searxng:8099, 容器内访问).

P0-1 优化: 国内主搜索引擎, 替代 DuckDuckGo (平均 22.5s/次) 作为 CN 区域首选.
- name 改为 "searxng" (与注册表 FREE_QUOTA_MAP 一致)
- timeout 从 settings.search_timeout 读取 (默认 10.0)
- 新增 safesearch=0 (关闭安全搜索过滤) + language="zh-CN" 参数
- 新增 time_range/categories 参数支持 (可选, kwargs 传入)
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.settings import Settings
from src.observability.tracing import trace_tool
from src.skills.researcher.searchers import BaseSearcher, SearchRegion

logger = logging.getLogger(__name__)


class SearXNGSearcher(BaseSearcher):
    """SearXNG 自托管元搜索引擎 (CN/GLOBAL 场景, 无需 Key).

    P0-1 优化: 注册到 CN+GLOBAL+AUTO 三区域, 国内查询优先使用.
    """

    name = "searxng"  # 与注册表 FREE_QUOTA_MAP 的 "searxng" 一致
    region = SearchRegion.GLOBAL
    cost_tier = "free"  # v1.1 新增
    quality_score = 65.0  # v1.1 新增

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        # 拼接完整搜索端点: {searx_url}/search (去除尾部斜杠避免双斜杠)
        self._api_url = f"{self.settings.searx_url.rstrip('/')}/search"

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """SearXNG 搜索 (GET, JSON 格式).

        返回 [{"title","url","snippet","source","region"}].

        Args:
            query: 搜索查询.
            max_results: 最大返回结果数.
            query_domains: 域名过滤白名单.
            **kwargs: 可选参数:
                time_range: 时间范围过滤 (None/"day"/"week"/"month"/"year", None 不过滤).
                categories: 搜索分类 (默认 "general", 可选 "images"/"news"/"it"/"science" 等).
        """
        # 可选参数从 kwargs 读取 (默认 categories=general, time_range 不过滤)
        time_range = kwargs.get("time_range")
        categories = kwargs.get("categories", "general,science,it,news")
        async with trace_tool(
            name="searxng-search",
            input={"query": query[:100], "max_results": max_results},
            metadata={"tool_name": "searxng", "region": "global"},
        ) as span:
            try:
                params: dict[str, Any] = {
                    "q": query,
                    "format": "json",
                    "pageno": 1,
                    "safesearch": 0,  # 关闭安全搜索过滤, 避免遗漏相关结果
                    "language": "zh-CN",  # 中文优先, 提升国内查询召回质量
                    "categories": categories,
                }
                # time_range 仅在显式传入时加入 (None 表示不过滤, 不传该参数)
                if time_range:
                    params["time_range"] = time_range
                # P0 修复: 添加 X-Forwarded-For 头, 避免 SearXNG botdetection 警告
                # (SearXNG ProxyFix 中间件检查此头, 缺失时记录 "X-Forwarded-For nor X-Real-IP header is set!")
                headers = {
                    "X-Forwarded-For": "127.0.0.1",
                }
                # timeout 从 settings 读取 (默认 10.0, P0-1 优化替代硬编码 15.0)
                async with httpx.AsyncClient(timeout=self.settings.search_timeout) as client:
                    response = await client.get(self._api_url, params=params, headers=headers)
                    response.raise_for_status()
                    data = response.json()

                results: list[dict[str, Any]] = []
                # SearXNG 返回结构: {"results": [{"title": "", "url": "", "content": ""}]}
                for item in data.get("results", [])[:max_results]:
                    results.append(
                        self._normalize_result(
                            title=item.get("title", ""),
                            url=item.get("url", ""),
                            snippet=item.get("content", ""),
                        )
                    )

                results = self._filter_by_domains(results, query_domains)
                span.update(
                    output={"results_count": len(results)},
                    metadata={"tool_name": "searxng", "success": True},
                )
                return results
            except Exception as e:  # noqa: BLE001
                logger.warning("SearXNG 搜索失败: %s", e)
                span.update(metadata={"tool_name": "searxng", "success": False, "error": str(e)})
                return []
