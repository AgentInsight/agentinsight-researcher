"""DeepResearch 递归深度研究器 (P0-01).

对标 GPT Researcher deep_research.py.
AGENTS.md 第 5 章: 节点为纯函数, 单一职责.
通过 breadth×depth 递归树探索, 每层聚合上下文.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.common.json_utils import safe_json_parse
from src.config.settings import Settings, get_settings
from src.llm.client import LLMClient, LLMTier
from src.observability.tracing import trace_chain
from src.skills.researcher.context_manager import ContextManager
from src.skills.researcher.scrapers import scrape_urls
from src.skills.researcher.searchers import (
    BaseSearcher,
    detect_region,
    get_searchers,
)

logger = logging.getLogger(__name__)


class DeepResearcher:
    """递归深度研究器 (breadth×depth 树探索).

    对标 GPT Researcher deep_research.
    每层: 1) 生成 breadth 个子查询 2) 并行检索 3) 聚合上下文 4) 递归下一层.
    """

    settings: Settings
    _llm: LLMClient
    _context_manager: ContextManager
    _visited_urls: set[str]

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        context_manager: ContextManager | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or LLMClient(self.settings)
        self._context_manager = context_manager or ContextManager(self.settings)
        self._visited_urls = set()

    async def research(
        self,
        query: str,
        *,
        breadth: int | None = None,
        depth: int | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        query_domains: list[str] | None = None,
        _current_depth: int = 0,
        _parent_context: str = "",
    ) -> dict[str, Any]:
        """递归深度研究.

        Args:
            query: 研究查询
            breadth: 每层子查询数, 默认 settings.deep_research_breadth
            depth: 递归深度, 默认 settings.deep_research_depth
            _current_depth: 当前递归深度 (内部用)
            _parent_context: 父节点上下文 (内部用)

        Returns:
            {"query", "context", "sources", "children": [...]}
        """
        # V4-P2-02: 自适应深度仅在顶层调用且未显式传参时启用, 避免递归层重复评估
        if (
            _current_depth == 0
            and self.settings.deep_research_adaptive
            and breadth is None
            and depth is None
        ):
            params = await self._assess_complexity(query, user_id=user_id, session_id=session_id)
            breadth = params["breadth"]
            depth = params["depth"]

        breadth = breadth or self.settings.deep_research_breadth
        depth = depth or self.settings.deep_research_depth

        async with trace_chain(
            name=f"deep-research-d{_current_depth}",
            input={"query": query[:100], "depth": _current_depth, "breadth": breadth},
            user_id=user_id,
            session_id=session_id,
        ) as span:
            # 递归终止
            if _current_depth >= depth:
                span.update(output={"terminated": True, "depth": _current_depth})
                return {"query": query, "context": _parent_context, "sources": [], "children": []}

            # 1. 生成 breadth 个子查询
            sub_queries = await self._generate_sub_queries(
                query,
                breadth,
                parent_context=_parent_context,
                user_id=user_id,
                session_id=session_id,
            )

            # 2. 并行检索每个子查询
            results = await asyncio.gather(
                *[
                    self._research_sub_query(
                        sq,
                        user_id=user_id,
                        session_id=session_id,
                        query_domains=query_domains,
                    )
                    for sq in sub_queries
                ]
            )

            # 3. 聚合上下文
            all_contexts = [r["context"] for r in results if r["context"]]
            all_sources = [s for r in results for s in r["sources"]]
            aggregated_context = "\n\n---\n\n".join(all_contexts)
            if _parent_context:
                aggregated_context = f"{_parent_context}\n\n---\n\n{aggregated_context}"

            # 4. 递归下一层 (限制广度避免指数爆炸)
            next_breadth = max(1, breadth // 2)  # 下一层广度减半
            children = await asyncio.gather(
                *[
                    self.research(
                        sq,
                        breadth=next_breadth,
                        depth=depth,
                        user_id=user_id,
                        session_id=session_id,
                        query_domains=query_domains,
                        _current_depth=_current_depth + 1,
                        _parent_context=aggregated_context,
                    )
                    for sq in sub_queries[:next_breadth]
                ]
            )

            span.update(
                output={
                    "context_len": len(aggregated_context),
                    "sources_count": len(all_sources),
                    "children_count": len(children),
                }
            )
            return {
                "query": query,
                "context": aggregated_context,
                "sources": all_sources,
                "children": list(children),
            }

    async def _assess_complexity(
        self,
        query: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, int]:
        """评估查询复杂度, 返回自适应参数 (V4-P2-02).

        用 LLMTier.FAST 评估查询复杂度 (1-5), 映射到 breadth/depth/concurrency:
            1-2 (简单): breadth=2, depth=1, concurrency=2
            3   (中等): breadth=3, depth=2, concurrency=4 (默认值)
            4-5 (复杂): breadth=4, depth=3, concurrency=6

        LLM 失败时返回 settings 中的默认配置 (不阻断主流程).
        """
        # 默认参数兜底 (LLM 失败时使用)
        default_params: dict[str, int] = {
            "breadth": self.settings.deep_research_breadth,
            "depth": self.settings.deep_research_depth,
            "concurrency": self.settings.deep_research_concurrency,
        }

        prompt = (
            "评估以下查询的研究复杂度 (1-5), 返回 JSON: "
            '{"complexity": 1-5, "reason": "..."}\n'
            "复杂度参考:\n"
            "  1-2: 单一事实/简单定义 (如 '什么是 RAG')\n"
            "  3: 多维度分析 (如 '对比 React 和 Vue 的优缺点')\n"
            "  4-5: 综合性深度研究 (如 '分析 2026 年 AI Agent 行业趋势与竞争格局')\n\n"
            f"查询: {query}\n\n"
            "仅返回 JSON:"
        )
        messages = [{"role": "user", "content": prompt}]

        try:
            response = await self._llm.achat(
                messages,
                tier=LLMTier.FAST,
                temperature=0.0,
                max_tokens=200,
                user_id=user_id,
                session_id=session_id,
                span_name="deep-research-complexity",
                step="deep_research",
            )
            parsed = safe_json_parse(response.content, fallback=None)
            if not isinstance(parsed, dict):
                logger.warning(
                    "复杂度评估返回非 dict, 降级默认值, query=%s",
                    query[:50],
                )
                return default_params

            complexity = parsed.get("complexity")
            if not isinstance(complexity, int) or complexity < 1 or complexity > 5:
                logger.warning(
                    "复杂度评分非法 (%r), 降级默认值, query=%s",
                    complexity,
                    query[:50],
                )
                return default_params

            # 复杂度映射表 (硬约束)
            if complexity <= 2:
                params = {"breadth": 2, "depth": 1, "concurrency": 2}
            elif complexity == 3:
                params = {"breadth": 3, "depth": 2, "concurrency": 4}
            else:  # complexity 4-5
                params = {"breadth": 4, "depth": 3, "concurrency": 6}

            logger.info(
                "自适应深度评估: complexity=%d → breadth=%d depth=%d concurrency=%d (query=%s)",
                complexity,
                params["breadth"],
                params["depth"],
                params["concurrency"],
                query[:50],
            )
            return params
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "复杂度评估失败, 降级默认值: %s (query=%s)",
                e,
                query[:50],
            )
            return default_params

    async def _generate_sub_queries(
        self,
        query: str,
        breadth: int,
        *,
        parent_context: str = "",
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[str]:
        """LLM 生成 breadth 个子查询."""
        prompt = f"""你是研究分析专家. 请将以下研究问题拆解为 {breadth} 个具体的子查询, 用于搜索引擎检索.

研究问题: {query}

父节点上下文 (如有):
{parent_context[:2000]}

要求:
1. 子查询应覆盖问题的不同维度
2. 每个子查询应具体可检索
3. 返回 JSON 数组, 每项为字符串

仅返回 JSON 数组:"""
        messages = [{"role": "user", "content": prompt}]
        response = await self._llm.achat(
            messages,
            tier=LLMTier.STRATEGIC,
            temperature=0.4,
            max_tokens=1500,
            user_id=user_id,
            session_id=session_id,
            span_name="deep-research-planner",
            step="deep_research",
        )
        sq_list = safe_json_parse(response.content, fallback=[query])
        if isinstance(sq_list, list) and sq_list:
            return [str(q) for q in sq_list[:breadth]]
        return [query]

    async def _research_sub_query(
        self,
        sub_query: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
        query_domains: list[str] | None = None,
    ) -> dict[str, Any]:
        """单个子查询: 搜索 + 抓取 + 压缩."""
        try:
            # 搜索
            region = detect_region(sub_query)
            searchers = get_searchers(region, self.settings)
            urls: list[str] = []
            sources: list[dict[str, Any]] = []
            for searcher in searchers:
                results = await searcher.search(
                    sub_query,
                    max_results=self.settings.max_search_results_per_query,
                    query_domains=query_domains,
                )
                # P1-Future-02: 域名过滤兜底 (针对不支持 query_domains 的引擎, 如 arxiv)
                if query_domains:
                    results = BaseSearcher._filter_by_domains(results, query_domains)
                for r in results:
                    if r.get("url") and r["url"] not in self._visited_urls:
                        urls.append(r["url"])
                        self._visited_urls.add(r["url"])
                        sources.append(r)

            # 抓取
            docs = await scrape_urls(
                urls[: self.settings.max_scraper_workers],
                max_workers=self.settings.max_scraper_workers,
                rate_limit_delay=self.settings.scraper_rate_limit_delay,
            )

            # 压缩
            context = await self._context_manager.get_similar_content(
                sub_query,
                docs,
                max_results=5,
                user_id=user_id,
                session_id=session_id,
            )
            return {"context": context, "sources": sources}
        except Exception as e:  # noqa: BLE001
            logger.warning("DeepResearch 子查询 '%s' 失败: %s", sub_query[:50], e)
            return {"context": "", "sources": []}
