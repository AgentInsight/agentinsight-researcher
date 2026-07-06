"""Arxiv 学术搜索 - 国外学术论文.

用户需求 5: 国外资料搜索, 学术论文专用.
对标 GPT Researcher retrievers/arxiv/arxiv.py.
无需 API Key.

v1.1 改造: 移除 arxiv 库依赖, 改用 httpx 直接调用 arxiv API (与其他搜索器一致).
- API: http://export.arxiv.org/api/query
- 返回 Atom XML 格式, 用 xml.etree.ElementTree 解析
- 优势: 减少依赖, 与项目其他搜索器保持一致的异步 httpx 模式
"""

from __future__ import annotations

import logging
from typing import Any
from xml.etree import ElementTree

import httpx

from src.config.settings import Settings
from src.observability.tracing import trace_tool
from src.skills.researcher.searchers import BaseSearcher, SearchRegion

logger = logging.getLogger(__name__)

# arxiv Atom 命名空间
_ATOM_NS = "{http://www.w3.org/2005/Atom}"


class ArxivSearcher(BaseSearcher):
    """Arxiv 学术论文搜索 (国外, 无需 Key).

    v1.1 改造: 移除 arxiv 库依赖, 改用 httpx + XML 解析.
    """

    name = "arxiv"
    region = SearchRegion.GLOBAL
    # v1.1 新增双字段
    cost_tier = "free"
    quality_score = 85.0

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self.base_url = "http://export.arxiv.org/api/query"

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Arxiv 搜索 (httpx + Atom XML 解析)."""
        async with trace_tool(
            name="arxiv-search",
            input={"query": query[:100], "max_results": max_results},
            metadata={"tool_name": "arxiv", "region": "global"},
        ) as span:
            try:
                params = {
                    "search_query": f"all:{query}",
                    "start": "0",
                    "max_results": str(max_results),
                    "sortBy": "relevance",
                    "sortOrder": "descending",
                }

                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(self.base_url, params=params)

                if resp.status_code != 200:
                    logger.warning(f"arxiv HTTP {resp.status_code}: {resp.text[:200]}")
                    span.update(
                        metadata={
                            "tool_name": "arxiv",
                            "success": False,
                            "error": f"HTTP {resp.status_code}",
                        }
                    )
                    return []

                # 解析 Atom XML
                root = ElementTree.fromstring(resp.text)
                results: list[dict[str, Any]] = []
                for entry in root.findall(f"{_ATOM_NS}entry"):
                    title_elem = entry.find(f"{_ATOM_NS}title")
                    summary_elem = entry.find(f"{_ATOM_NS}summary")
                    # entry_id 是 arxiv URL (如 http://arxiv.org/abs/2401.12345v1)
                    id_elem = entry.find(f"{_ATOM_NS}id")
                    # 优先取 <link rel="alternate" type="text/html"> 的 href
                    url = ""
                    for link in entry.findall(f"{_ATOM_NS}link"):
                        if link.get("rel") == "alternate" and link.get("type") == "text/html":
                            url = link.get("href") or ""
                            break
                    if not url and id_elem is not None:
                        url = id_elem.text or ""

                    title = (title_elem.text or "").strip() if title_elem is not None else ""
                    snippet = (summary_elem.text or "").strip() if summary_elem is not None else ""
                    # 摘要常含多余空白, 规范化
                    snippet = " ".join(snippet.split())[:500]

                    if url:
                        results.append(self._normalize_result(title, url, snippet))

                # query_domains 后置过滤
                results = self._filter_by_domains(results, query_domains)

                span.update(
                    output={"results_count": len(results)},
                    metadata={"tool_name": "arxiv", "success": True},
                )
                return results

            except Exception as e:  # noqa: BLE001
                logger.warning("Arxiv 搜索失败: %s", e)
                span.update(metadata={"tool_name": "arxiv", "success": False, "error": str(e)})
                return []
