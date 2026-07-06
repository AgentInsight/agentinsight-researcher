"""PubMed NCBI E-utilities 学术搜索器.

AGENTS.md 第 9 章: 统一 httpx 异步.
PubMed (NCBI E-utilities) 学术论文搜索, 适用于学术场景.
两步检索: esearch 获取 PMID 列表 -> esummary 获取摘要.
无需 API Key, 建议配置 PUBMED_EMAIL 邮箱.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.settings import Settings
from src.observability.tracing import trace_tool
from src.skills.researcher.searchers import BaseSearcher, SearchRegion

logger = logging.getLogger(__name__)


class PubMedSearcher(BaseSearcher):
    """PubMed 学术论文搜索 (NCBI E-utilities, 无需 Key)."""

    name = "pubmed"
    region = SearchRegion.ACADEMIC
    cost_tier = "free"  # v1.1 新增
    quality_score = 90.0  # v1.1 新增

    _esearch_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    _esummary_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(settings)
        self._email = self.settings.pubmed_email

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """PubMed 搜索 (两步: esearch + esummary).

        返回 [{"title","url","snippet","source","region"}].
        """
        async with trace_tool(
            name="pubmed-search",
            input={"query": query[:100], "max_results": max_results},
            metadata={"tool_name": "pubmed", "region": "academic"},
        ) as span:
            try:
                # 第一步: esearch 获取 PMID 列表
                esearch_params: dict[str, Any] = {
                    "db": "pubmed",
                    "term": query,
                    "retmax": max_results,
                    "retmode": "json",
                }
                if self._email:
                    esearch_params["email"] = self._email

                async with httpx.AsyncClient(timeout=15.0) as client:
                    esearch_resp = await client.get(self._esearch_url, params=esearch_params)
                    esearch_resp.raise_for_status()
                    esearch_data = esearch_resp.json()

                id_list: list[str] = esearch_data.get("esearchresult", {}).get("idlist", [])
                if not id_list:
                    span.update(
                        output={"results_count": 0},
                        metadata={"tool_name": "pubmed", "success": True},
                    )
                    return []

                # 第二步: esummary 获取摘要
                esummary_params: dict[str, Any] = {
                    "db": "pubmed",
                    "id": ",".join(id_list),
                    "retmode": "json",
                }
                if self._email:
                    esummary_params["email"] = self._email

                async with httpx.AsyncClient(timeout=15.0) as client:
                    esummary_resp = await client.get(self._esummary_url, params=esummary_params)
                    esummary_resp.raise_for_status()
                    esummary_data = esummary_resp.json()

                results: list[dict[str, Any]] = []
                result_obj = esummary_data.get("result", {})
                for pmid in id_list:
                    item = result_obj.get(pmid, {})
                    if not item:
                        continue
                    title = item.get("title", "")
                    # PubMed esummary 无直接 abstract, 用摘要构造 snippet
                    abstract_parts = item.get("abstract", [])
                    if isinstance(abstract_parts, list):
                        snippet = " ".join(
                            p.get("text", "") if isinstance(p, dict) else str(p)
                            for p in abstract_parts
                        )
                    else:
                        snippet = str(abstract_parts)
                    url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                    results.append(
                        self._normalize_result(
                            title=title,
                            url=url,
                            snippet=snippet,
                        )
                    )

                results = self._filter_by_domains(results, query_domains)
                span.update(
                    output={"results_count": len(results)},
                    metadata={"tool_name": "pubmed", "success": True},
                )
                return results
            except Exception as e:  # noqa: BLE001
                logger.warning("PubMed 搜索失败: %s", e)
                span.update(metadata={"tool_name": "pubmed", "success": False, "error": str(e)})
                return []
