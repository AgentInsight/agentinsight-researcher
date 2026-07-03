"""ResearchConductor 研究总指挥.

对标 GPT Researcher skills/researcher.py.
AGENTS.md 用户需求 3: Planner (拆解问题) → Researcher (并行搜索爬取).

核心流程:
1. plan_research: 按行业提示词拆解子查询 (Planner)
2. asyncio.gather 并行 _process_sub_query (Researcher):
   - 搜索 (中文优先路由)
   - 抓取 (BrowserManager)
   - 压缩去重 (ContextManager)
   - MCP (可选, fast/deep/disabled)
3. 聚合上下文
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from src.config.settings import Settings, get_settings
from src.llm.client import LLMClient, LLMTier
from src.observability.tracing import trace_chain
from src.skills.researcher.context_manager import ContextManager
from src.skills.researcher.scrapers import scrape_urls
from src.skills.researcher.searchers import (
    detect_region,
    get_searchers,
)

logger = logging.getLogger(__name__)


class ResearchConductor:
    """研究总指挥 (对标 GPT Researcher ResearchConductor).

    含 Planner (拆解子查询) + Researcher (并行搜索爬取) 职责.
    """

    settings: Settings
    _llm: LLMClient
    _context_manager: ContextManager
    _mcp_cache: list[str] | None
    _mcp_query_count: int

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        context_manager: ContextManager | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or LLMClient(self.settings)
        self._context_manager = context_manager or ContextManager(self.settings)
        self._mcp_cache = None
        self._mcp_query_count = 0

    async def plan_research(
        self,
        query: str,
        *,
        industry_prompt_family: dict[str, Any] | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[str]:
        """Planner: 按行业提示词拆解子查询.

        对标 GPT Researcher generate_sub_queries.
        用 strategic_llm (规划专用, 慢但精).
        """
        async with trace_chain(
            name="planner",
            input={
                "query": query[:100],
                "industry": industry_prompt_family.get("industry_name", "")
                if industry_prompt_family
                else "",
            },
            user_id=user_id,
            session_id=session_id,
        ) as span:
            # 构建拆解提示词 (按行业专家角色)
            industry_name = (
                industry_prompt_family.get("industry_name", "通用研究")
                if industry_prompt_family
                else "通用研究"
            )
            industry_role = (
                industry_prompt_family.get("planner_prompt", "") if industry_prompt_family else ""
            )

            max_iterations = self.settings.max_iterations

            prompt = f"""你是一位{industry_name}行业的研究分析专家. {industry_role}

你的任务是: 将用户的研究问题拆解为 {max_iterations} 个具体的子查询, 用于搜索引擎检索.

要求:
1. 子查询应覆盖问题的不同维度 (市场/技术/竞争/政策/趋势等)
2. 子查询应为搜索引擎友好的关键词组合
3. 子查询应中文优先 (中文问题用中文子查询, 英文问题用英文子查询)
4. 返回 JSON 数组格式: ["子查询1", "子查询2", ...]

用户问题: {query}

请返回 {max_iterations} 个子查询的 JSON 数组:"""

            messages = [{"role": "user", "content": prompt}]
            response = await self._llm.achat(
                messages,
                tier=LLMTier.STRATEGIC,
                temperature=0.2,
                user_id=user_id,
                session_id=session_id,
                span_name="planner-llm",
            )

            # 解析 JSON (用 json_repair 容错)
            try:
                import json_repair

                sub_queries = json_repair.loads(response.content)
                if isinstance(sub_queries, list) and sub_queries:
                    # 确保是字符串列表
                    sub_queries = [str(q) for q in sub_queries if q][:max_iterations]
                    span.update(output={"sub_queries_count": len(sub_queries)})
                    return sub_queries
            except Exception:  # noqa: BLE001
                pass

            # 解析失败, 返回原始 query
            logger.warning("子查询拆解失败, 返回原始查询")
            span.update(output={"sub_queries_count": 1, "fallback": True})
            return [query]

    async def conduct_research(
        self,
        query: str,
        *,
        industry_prompt_family: dict[str, Any] | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        uploaded_files_context: list[str] | None = None,
    ) -> dict[str, Any]:
        """完整研究流程: 规划 + 并行检索 + 抓取 + 压缩.

        对标 GPT Researcher conduct_research + _get_context_by_web_search.
        返回 {"contexts","sources","sub_queries"}.
        """
        async with trace_chain(
            name="research-conductor",
            input={"query": query[:100]},
            user_id=user_id,
            session_id=session_id,
        ) as span:
            # 1. Planner: 拆解子查询
            sub_queries = await self.plan_research(
                query,
                industry_prompt_family=industry_prompt_family,
                user_id=user_id,
                session_id=session_id,
            )

            # 追加原始 query (对标 GPT Researcher)
            if query not in sub_queries:
                sub_queries.append(query)

            # 2. 并行处理子查询
            tasks = [
                self._process_sub_query(
                    sq,
                    industry_prompt_family=industry_prompt_family,
                    user_id=user_id,
                    session_id=session_id,
                )
                for sq in sub_queries
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 3. 聚合上下文与来源
            contexts: list[str] = []
            sources: list[dict[str, Any]] = []
            visited_urls: set[str] = set()

            for r in results:
                if isinstance(r, Exception):
                    logger.warning("子查询处理失败: %s", r)
                    continue
                r = cast(dict[str, Any], r)
                if r.get("context"):
                    contexts.append(r["context"])
                if r.get("sources"):
                    sources.extend(r["sources"])
                if r.get("urls"):
                    visited_urls.update(r["urls"])

            # 4. 合并上传文件上下文 (用户需求 8)
            if uploaded_files_context:
                contexts.extend(uploaded_files_context)

            span.update(
                output={
                    "sub_queries_count": len(sub_queries),
                    "contexts_count": len(contexts),
                    "sources_count": len(sources),
                },
            )
            return {
                "contexts": contexts,
                "sources": sources,
                "sub_queries": sub_queries,
                "visited_urls": visited_urls,
            }

    async def _process_sub_query(
        self,
        sub_query: str,
        *,
        industry_prompt_family: dict[str, Any] | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """处理单个子查询: 搜索 → 抓取 → 压缩.

        对标 GPT Researcher _process_sub_query.
        """
        # 1. 检测区域 (中文优先路由, 用户需求 5)
        region = detect_region(sub_query)
        searchers = get_searchers(region, self.settings)

        # 2. 并行搜索 (多个搜索引擎)
        search_tasks = [
            s.search(
                sub_query,
                max_results=self.settings.max_search_results_per_query,
            )
            for s in searchers
        ]
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        # 3. 合并搜索结果 + 去重
        all_results: list[dict[str, Any]] = []
        urls: set[str] = set()
        for r in search_results:
            if isinstance(r, Exception):
                continue
            r = cast(list[dict[str, Any]], r)
            for item in r:
                url = item.get("url", "")
                if url and url not in urls:
                    urls.add(url)
                    all_results.append(item)

        if not all_results:
            return {"context": "", "sources": [], "urls": set()}

        # 4. 抓取 URL 内容
        max_workers = self.settings.max_scraper_workers
        rate_limit = self.settings.scraper_rate_limit_delay
        scraped = await scrape_urls(
            list(urls),
            scraper_type=self.settings.scraper,
            max_workers=max_workers,
            rate_limit_delay=rate_limit,
        )

        # 5. ContextManager 压缩 + 去重 (Token 优化)
        context = await self._context_manager.get_similar_content(
            sub_query,
            scraped,
            max_results=10,
            user_id=user_id,
            session_id=session_id,
        )

        return {
            "context": context,
            "sources": all_results,
            "urls": urls,
        }
