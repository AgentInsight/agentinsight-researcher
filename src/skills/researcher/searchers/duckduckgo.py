"""DuckDuckGo 搜索 - 国内兜底 (无需 API Key).

用户需求 5: 国内资料搜索兜底方案, 无需 Key.
对标 GPT Researcher retrievers/duckduckgo/duckduckgo.py.
"""

from __future__ import annotations

import logging

# 优先 ddgs 新包名 (v8+), 回退 duckduckgo_search 旧包名 (v6/v7)
# P1-04: 抑制 duckduckgo_search 弃用警告 (旧包仍可用, 仅提示升级到 ddgs)
import warnings as _warnings
from typing import Any

from src.config.settings import Settings
from src.observability.tracing import trace_tool
from src.skills.researcher.searchers import BaseSearcher, SearchRegion

# DDGS 可能为 None (两个包都未安装时), 需显式声明联合类型以满足 mypy strict
DDGS: type[Any] | None
with _warnings.catch_warnings():
    _warnings.filterwarnings(
        "ignore",
        message=r".*duckduckgo_search.*has been renamed.*",
        category=RuntimeWarning,
    )
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            DDGS = None

logger = logging.getLogger(__name__)


class DuckDuckGoSearcher(BaseSearcher):
    """DuckDuckGo 搜索引擎 (无需 Key, 兜底)."""

    name = "duckduckgo"
    region = SearchRegion.CN  # 作为国内兜底, 也可用于全球
    cost_tier = "free"  # v1.1 新增
    quality_score = 60.0  # v1.1 新增

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """DuckDuckGo 搜索 (用 ddgs 库)."""
        async with trace_tool(
            name="duckduckgo-search",
            input={"query": query[:100], "max_results": max_results},
            metadata={"tool_name": "duckduckgo"},
        ) as span:
            if DDGS is None:
                logger.warning("ddgs/duckduckgo_search 库未安装, 跳过 DuckDuckGo 搜索")
                span.update(
                    metadata={
                        "tool_name": "duckduckgo",
                        "success": False,
                        "error": "ddgs not installed",
                    }
                )
                return []
            try:
                # ddgs 是同步库, 用 asyncio.to_thread 包装
                import asyncio

                def _sync_search() -> list[dict[str, Any]]:
                    results: list[dict[str, Any]] = []
                    # P1-04: 抑制 duckduckgo_search 弃用警告 (构造 + __enter__ + text 调用均可能触发)
                    with _warnings.catch_warnings():
                        _warnings.filterwarnings(
                            "ignore",
                            message=r".*duckduckgo_search.*has been renamed.*",
                            category=RuntimeWarning,
                        )
                        with DDGS() as ddgs:
                            # region='wt-wt' 全球; 'cn-cn' 中国
                            region = kwargs.get("region", "wt-wt")
                            for r in ddgs.text(query, region=region, max_results=max_results):
                                results.append(
                                    self._normalize_result(
                                        title=r.get("title", ""),
                                        url=r.get("href") or r.get("url", ""),
                                        snippet=r.get("body") or r.get("snippet", ""),
                                    )
                                )
                    return results

                results = await asyncio.to_thread(_sync_search)
                results = self._filter_by_domains(results, query_domains)
                span.update(
                    output={"results_count": len(results)},
                    metadata={"tool_name": "duckduckgo", "success": True},
                )
                return results
            except Exception as e:  # noqa: BLE001
                logger.warning("DuckDuckGo 搜索失败: %s", e)
                span.update(metadata={"tool_name": "duckduckgo", "success": False, "error": str(e)})
                return []
