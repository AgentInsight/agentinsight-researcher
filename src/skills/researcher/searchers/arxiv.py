"""Arxiv 学术搜索 - 国外学术论文.

用户需求 5: 国外资料搜索, 学术论文专用.
对标 GPT Researcher retrievers/arxiv/arxiv.py.
无需 API Key.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.settings import Settings
from src.observability.tracing import trace_tool
from src.skills.researcher.searchers import BaseSearcher, SearchRegion

logger = logging.getLogger(__name__)


class ArxivSearcher(BaseSearcher):
    """Arxiv 学术论文搜索 (国外, 无需 Key)."""

    name = "arxiv"
    region = SearchRegion.GLOBAL

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Arxiv 搜索."""
        async with trace_tool(
            name="arxiv-search",
            input={"query": query[:100], "max_results": max_results},
            metadata={"tool_name": "arxiv", "region": "global"},
        ) as span:
            try:
                import asyncio

                import arxiv

                def _sync_search() -> list[dict[str, Any]]:
                    results: list[dict[str, Any]] = []
                    sort = kwargs.get("sort", "relevance")
                    sort_criterion = (
                        arxiv.SortCriterion.Relevance
                        if sort == "relevance"
                        else arxiv.SortCriterion.SubmittedDate
                    )
                    client = arxiv.Client()
                    search = arxiv.Search(
                        query=query,
                        max_results=max_results,
                        sort_by=sort_criterion,
                    )
                    for result in client.results(search):
                        results.append(
                            self._normalize_result(
                                title=result.title,
                                url=result.entry_id,
                                snippet=result.summary,
                            )
                        )
                    return results

                results = await asyncio.to_thread(_sync_search)
                span.update(
                    output={"results_count": len(results)},
                    metadata={"tool_name": "arxiv", "success": True},
                )
                return results
            except ImportError:
                logger.warning("arxiv 库未安装, 跳过 Arxiv 搜索")
                span.update(
                    metadata={
                        "tool_name": "arxiv",
                        "success": False,
                        "error": "arxiv not installed",
                    }
                )
                return []
            except Exception as e:  # noqa: BLE001
                logger.warning("Arxiv 搜索失败: %s", e)
                span.update(metadata={"tool_name": "arxiv", "success": False, "error": str(e)})
                return []
