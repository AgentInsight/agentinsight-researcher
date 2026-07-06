"""OpenAlex 学术搜索 - 开放学术数据库.

P2-Future-04: 对标 GPT Researcher retrievers/openalex/openalex.py.
OpenAlex 是开放学术文献数据库, 适用于学术场景.
无需 API Key, 可选配置 OPENALEX_EMAIL 进入 polite pool (更高配额).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.settings import Settings
from src.observability.tracing import trace_tool
from src.skills.researcher.searchers import BaseSearcher, SearchRegion

logger = logging.getLogger(__name__)


class OpenAlexSearcher(BaseSearcher):
    """OpenAlex 学术文献搜索 (开放, 可选邮箱进入 polite pool)."""

    name = "openalex"
    region = SearchRegion.ACADEMIC
    cost_tier = "free"  # v1.1 新增
    quality_score = 78.0  # v1.1 新增

    _api_url: str = "https://api.openalex.org/works"

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._email = self.settings.openalex_email

    @staticmethod
    def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
        """重建 OpenAlex 倒排索引摘要为纯文本.

        OpenAlex 摘要以 {"word": [positions]} 倒排索引形式存储, 需按位置重建.
        """
        if not inverted_index:
            return ""
        positions: list[tuple[int, str]] = []
        for word, idxs in inverted_index.items():
            for idx in idxs:
                positions.append((idx, word))
        positions.sort()
        return " ".join(word for _, word in positions)

    @staticmethod
    def _format_authors(authorships: list[dict[str, Any]] | None) -> str:
        """提取作者显示名列表, 拼接为逗号分隔字符串."""
        authors: list[str] = []
        for authorship in authorships or []:
            name = authorship.get("author", {}).get("display_name", "")
            if name:
                authors.append(name)
        return ", ".join(authors)

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """OpenAlex 学术搜索 (GET, 无需 Key).

        返回 [{"title","url","snippet","source","region"}].
        snippet 含摘要 + 作者 + 发表日期 + DOI (对标 GPTR openalex).
        """
        async with trace_tool(
            name="openalex-search",
            input={"query": query[:100], "max_results": max_results},
            metadata={"tool_name": "openalex", "region": "academic"},
        ) as span:
            try:
                params: dict[str, Any] = {
                    "search": query,
                    "per_page": max_results,
                }
                # polite pool: 提供 mailto 参数 (OpenAlex 官方建议)
                if self._email:
                    params["mailto"] = self._email
                headers: dict[str, str] = {}
                if self._email:
                    # User-Agent 含邮箱, 进入 polite pool 获得更高配额
                    headers["User-Agent"] = f"agentinsight-researcher/1.0 (mailto:{self._email})"
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(self._api_url, headers=headers, params=params)
                    response.raise_for_status()
                    data = response.json()

                results: list[dict[str, Any]] = []
                # OpenAlex 返回结构: {"results": [{"title": "", "doi": "", "authorships": [],
                #   "abstract_inverted_index": {}, "publication_date": ""}]}
                for item in data.get("results", [])[:max_results]:
                    abstract = self._reconstruct_abstract(item.get("abstract_inverted_index"))
                    authors = self._format_authors(item.get("authorships"))
                    pub_date = item.get("publication_date", "")
                    doi = item.get("doi") or ""
                    # 拼接富信息 snippet: 摘要 + 作者 + 发表日期 + DOI
                    snippet_parts: list[str] = []
                    if abstract:
                        snippet_parts.append(abstract)
                    if authors:
                        snippet_parts.append(f"Authors: {authors}")
                    if pub_date:
                        snippet_parts.append(f"Published: {pub_date}")
                    if doi:
                        snippet_parts.append(f"DOI: {doi}")
                    snippet = "\n\n".join(snippet_parts)
                    # url 优先用 DOI (OpenAlex 的 doi 字段已是 https://doi.org/... 形式),
                    # 否则用 OpenAlex ID (id 字段为 https://openalex.org/W... 形式)
                    url = doi or item.get("id", "")
                    results.append(
                        self._normalize_result(
                            title=item.get("title", ""),
                            url=url,
                            snippet=snippet,
                        )
                    )

                results = self._filter_by_domains(results, query_domains)
                span.update(
                    output={"results_count": len(results)},
                    metadata={"tool_name": "openalex", "success": True},
                )
                return results
            except Exception as e:  # noqa: BLE001
                logger.warning("OpenAlex 搜索失败: %s", e)
                span.update(metadata={"tool_name": "openalex", "success": False, "error": str(e)})
                return []
