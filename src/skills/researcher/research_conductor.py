"""ResearchConductor 研究总指挥.

对标 GPT Researcher skills/researcher.py.
AGENTS.md 用户需求 3: Planner (拆解问题) → Researcher (并行搜索爬取).

核心流程:
1. plan_research: 按动态角色 persona 拆解子查询 (Planner, 对标 GPTR AGENT_ROLE)
2. asyncio.gather 并行 _process_sub_query (Researcher):
   - 搜索 (中文优先路由)
   - 抓取 (BrowserManager)
   - 压缩去重 (ContextManager)
   - MCP (可选, fast/deep/disabled)
3. 聚合上下文

行业适配采用 GPTR 风格 4 层机制, 不再使用行业分类器:
- agent_role 参数 (对标 GPTR AGENT_ROLE) 注入角色 persona, 由 LLM 动态生成或调用方注入

P1-Future-04: planner prompt 经 PromptFamily 策略注入 (支持中英多语言切换).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, cast

from src.common.json_utils import safe_json_parse
from src.common.redis_client import get_redis_client
from src.config.settings import Settings, get_settings
from src.llm.client import LLMClient, LLMTier, get_llm_client
from src.observability.tracing import trace_chain
from src.skills.researcher.context_manager import ContextManager
from src.skills.researcher.mcp_coordinator import (
    MCPCoordinator,
    conduct_mcp_if_enabled,
)
from src.skills.researcher.prompts import PromptFamily, get_prompt_family
from src.skills.researcher.scrapers import scrape_urls
from src.skills.researcher.searchers import (
    BaseSearcher,
    deduplicate_results,
    detect_region,
)

logger = logging.getLogger(__name__)

# 兜底角色 persona (对标 GPTR 默认 researcher role)
_DEFAULT_AGENT_ROLE = (
    "你是一位资深研究分析专家, 擅长多领域综合研究, 研究重点是全面、客观地分析问题."
)


class ResearchConductor:
    """研究总指挥 (对标 GPT Researcher ResearchConductor).

    含 Planner (拆解子查询) + Researcher (并行搜索爬取) 职责.
    """

    settings: Settings
    _llm: LLMClient
    _context_manager: ContextManager
    _prompt_family: PromptFamily
    _mcp_cache: list[str] | None
    _mcp_query_count: int
    # P0-7: MCPCoordinator 惰性初始化
    _mcp: MCPCoordinator | None
    # 用户需求: 私有数据 RAG 检索器 (惰性初始化, 避免启动期构造开销)
    # HybridRetriever 内部 namespace_has_data 已含 10min TTL 内存缓存,
    # 无私有数据时零 embeddings+qdrant 调用
    _retriever: Any | None  # HybridRetriever | None (惰性导入避免循环依赖)

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        context_manager: ContextManager | None = None,
        prompt_family: PromptFamily | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or get_llm_client()
        self._context_manager = context_manager or ContextManager(self.settings)
        self._prompt_family = prompt_family or get_prompt_family(self.settings.prompt_family)
        self._mcp_cache = None
        self._mcp_query_count = 0
        # P0-7: MCPCoordinator 惰性初始化 (避免启动期构造开销)
        self._mcp = None
        # 用户需求: HybridRetriever 惰性初始化
        self._retriever = None

    def _get_mcp(self) -> MCPCoordinator:
        """惰性初始化 MCPCoordinator (P0-7).

        复用 self._llm 单例, 避免重复构造 LLMClient 导致 step_costs 累计丢失.
        """
        if self._mcp is None:
            self._mcp = MCPCoordinator(self.settings, self._llm)
        return self._mcp

    def _get_retriever(self) -> Any:
        """惰性初始化 HybridRetriever (用户需求: 私有数据 RAG 检索).

        HybridRetriever.retrieve 内部流程:
        1. build_data_namespaces: 检查 namespace 可用性 (10min TTL 内存缓存)
           - 无数据时返回空 namespaces → 零 embeddings+qdrant 调用
           - 有数据时返回 namespaces 列表
        2. namespaces 非空时: embed_query (1次) + qdrant.search (1次) + BM25 + RRF + 可选 Rerank
        3. 结果写入 Redis 缓存 (后续相同 query 命中缓存)

        惰性导入避免循环依赖 (rag.retriever 依赖 rag.qdrant_manager 等).
        """
        if self._retriever is None:
            from src.rag.retriever import get_retriever

            self._retriever = get_retriever()
        return self._retriever

    async def _retrieve_private_data(
        self,
        query: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """私有数据 RAG 检索 (用户需求: 最少次 embeddings+qdrant 调用).

        流程:
        1. 调用 HybridRetriever.retrieve(query, user_id)
        2. retrieve 内部先检查 namespace 可用性 (10min TTL 缓存):
           - 缓存命中且无数据 → 直接返回 [] (零 embeddings+qdrant 调用)
           - 缓存命中且有数据 → 走 BM25+Vector+RRF+Rerank
           - 缓存未命中 → 调 Qdrant count (exact=False, 毫秒级) 更新缓存
        3. 私有数据上下文与 Web 搜索上下文在 conduct_research 中统一聚合

        Args:
            query: 用户查询
            user_id: 用户 ID (None 时只检索共享数据)
            session_id: 会话 ID (用于 trace)

        Returns:
            (contexts, sources): 私有数据上下文列表 + 来源列表
            无数据或异常时返回 ([], [])
        """
        try:
            retriever = self._get_retriever()
            results = await retriever.retrieve(
                query,
                user_id=user_id,
                session_id=session_id,
                top_k=10,
            )
            if not results:
                logger.debug(
                    "私有数据 RAG 检索无结果 (namespace 无数据或未命中): query=%s", query[:50]
                )
                return [], []

            contexts: list[str] = []
            sources: list[dict[str, Any]] = []
            for r in results:
                content = r.get("content", "")
                if content:
                    contexts.append(content)
                # 构造来源信息 (与 Web 搜索来源结构一致)
                sources.append(
                    {
                        "url": r.get("metadata", {}).get("url", ""),
                        "title": r.get("metadata", {}).get("title", "私有数据"),
                        "snippet": content[:200] if content else "",
                        "source": "private_rag",
                        "score": r.get("score", 0.0),
                    }
                )
            logger.info(
                "私有数据 RAG 检索完成: query=%s, contexts=%d, sources=%d",
                query[:50],
                len(contexts),
                len(sources),
            )
            return contexts, sources
        except Exception as e:  # noqa: BLE001
            # 私有数据检索失败不阻断主流程, 降级走 Web 搜索 (与 MCP 调用容错模式一致)
            logger.warning("私有数据 RAG 检索失败 (不阻断, 降级走 Web 搜索): %s", e)
            return [], []

    async def plan_research(
        self,
        query: str,
        *,
        agent_role: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[str]:
        """Planner: 按动态角色 persona 拆解子查询.

        对标 GPT Researcher generate_sub_queries.
        用 strategic_llm (规划专用, 慢但精).
        agent_role (对标 GPTR AGENT_ROLE) 注入角色 persona.
        """
        async with trace_chain(
            name="planner",
            input={
                "query": query[:100],
                "has_agent_role": bool(agent_role),
            },
            user_id=user_id,
            session_id=session_id,
        ) as span:
            max_iterations = self.settings.max_iterations

            # 对标 GPTR: agent_role (来自 LLM 动态生成或调用方注入) 作为角色 persona
            role_persona = agent_role or _DEFAULT_AGENT_ROLE

            # P1-Future-04: prompt 经 PromptFamily 策略注入
            prompt = self._prompt_family.planner_prompt(
                query=query,
                agent_role=role_persona,
                max_iterations=max_iterations,
            )

            messages = [{"role": "user", "content": prompt}]
            # P1-7: planner 生成短 JSON 数组, SMART (v4-flash) 足够, 省 2/3 成本
            response = await self._llm.achat(
                messages,
                tier=LLMTier.SMART,
                temperature=0.2,
                user_id=user_id,
                session_id=session_id,
                span_name="planner-llm",
                step="planner",
            )

            # 解析 JSON (三级容错)
            try:
                sub_queries = safe_json_parse(response.content, fallback=[])
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
        mode: str = "",
        agent_role: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        uploaded_files_context: list[str] | None = None,
        query_domains: list[str] | None = None,
    ) -> dict[str, Any]:
        """完整研究流程: 规划 + 并行检索 + 抓取 + 压缩.

        对标 GPT Researcher conduct_research + _get_context_by_web_search.
        返回 {"contexts","sources","sub_queries","visited_urls"}.

        mode 路由 (P2-01):
        - "summary": 摘要模式 (快速检索 + 简要摘要)
        - "subtopics": 子主题模式 (按子主题分章节)
        - 其他/默认: 现有 basic/detailed 逻辑
        """
        # P0-3: 会话级 reset (替代原 get_similar_content 内的每次 reset)
        # 跨子查询共享 WrittenContentCompressor 去重状态, 减少重复 embed 调用
        self._context_manager._written_compressor.reset()

        async with trace_chain(
            name="research-conductor",
            input={"query": query[:100], "mode": mode},
            user_id=user_id,
            session_id=session_id,
        ) as span:
            # P0-2: 流水线并行化 — _retrieve_private_data 与主流程 (plan_research /
            # _conduct_summary / _conduct_subtopics) 无数据依赖, 并行执行可节省
            # 私有数据 RAG 延迟 (BM25+Vector+RRF+Rerank ~2-5s).
            # 用户需求: namespace 可用性 10min TTL 缓存, 无数据时零 embeddings+qdrant 调用.
            # _retrieve_private_data 内部异常已捕获返回 ([], []), 不会抛出.
            private_data_task = asyncio.create_task(
                self._retrieve_private_data(
                    query,
                    user_id=user_id,
                    session_id=session_id,
                )
            )

            # P2-01: 摘要模式路由 (P0-2: 与私有数据检索并行)
            if mode == "summary":
                result, (private_contexts, private_sources) = await asyncio.gather(
                    self._conduct_summary(
                        query,
                        agent_role=agent_role,
                        user_id=user_id,
                        session_id=session_id,
                        uploaded_files_context=uploaded_files_context,
                        query_domains=query_domains,
                    ),
                    private_data_task,
                )
                result = cast(dict[str, Any], result)
                # 合并私有数据上下文 (优先于 Web 搜索结果)
                if private_contexts:
                    result["contexts"] = private_contexts + result.get("contexts", [])
                    result["sources"] = private_sources + result.get("sources", [])
                span.update(
                    output={
                        "mode": "summary",
                        "sub_queries_count": len(result.get("sub_queries", [])),
                        "contexts_count": len(result.get("contexts", [])),
                        "sources_count": len(result.get("sources", [])),
                        "private_contexts_count": len(private_contexts),
                    },
                )
                return result

            # P2-01: 子主题模式路由 (P0-2: 与私有数据检索并行)
            if mode == "subtopics":
                result, (private_contexts, private_sources) = await asyncio.gather(
                    self._conduct_subtopics(
                        query,
                        agent_role=agent_role,
                        user_id=user_id,
                        session_id=session_id,
                        uploaded_files_context=uploaded_files_context,
                        query_domains=query_domains,
                    ),
                    private_data_task,
                )
                result = cast(dict[str, Any], result)
                # 合并私有数据上下文 (优先于 Web 搜索结果)
                if private_contexts:
                    result["contexts"] = private_contexts + result.get("contexts", [])
                    result["sources"] = private_sources + result.get("sources", [])
                span.update(
                    output={
                        "mode": "subtopics",
                        "sub_queries_count": len(result.get("sub_queries", [])),
                        "contexts_count": len(result.get("contexts", [])),
                        "sources_count": len(result.get("sources", [])),
                        "private_contexts_count": len(private_contexts),
                    },
                )
                return result

            # 1. Planner: 拆解子查询 (P0-2: 与私有数据检索并行, 无数据依赖)
            sub_queries, (private_contexts, private_sources) = await asyncio.gather(
                self.plan_research(
                    query,
                    agent_role=agent_role,
                    user_id=user_id,
                    session_id=session_id,
                ),
                private_data_task,
            )

            # 追加原始 query (对标 GPT Researcher)
            if query not in sub_queries:
                sub_queries.append(query)

            # 2. 并行处理子查询
            tasks = [
                self._process_sub_query(
                    sq,
                    user_id=user_id,
                    session_id=session_id,
                    query_domains=query_domains,
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

            # 5. 合并私有数据 RAG 上下文 (用户需求: 私有数据优先于 Web 搜索结果)
            if private_contexts:
                contexts = private_contexts + contexts
                sources = private_sources + sources

            span.update(
                output={
                    "sub_queries_count": len(sub_queries),
                    "contexts_count": len(contexts),
                    "sources_count": len(sources),
                    "private_contexts_count": len(private_contexts),
                },
            )
            return {
                "contexts": contexts,
                "sources": sources,
                "sub_queries": sub_queries,
                "visited_urls": visited_urls,
            }

    async def _conduct_summary(
        self,
        query: str,
        *,
        agent_role: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        uploaded_files_context: list[str] | None = None,
        query_domains: list[str] | None = None,
    ) -> dict[str, Any]:
        """摘要模式: 快速检索 + 简要摘要 (P2-01).

        - 少量子查询 (2-3 个)
        - max_results=5
        - LLM 摘要 max_tokens=1000
        """
        # 1. 生成 2 个子查询
        planned = await self.plan_research(
            query,
            agent_role=agent_role,
            user_id=user_id,
            session_id=session_id,
        )
        sub_queries = planned[:2] or [query]

        # 2. 检索
        results = await asyncio.gather(
            *[
                self._process_sub_query(
                    sq,
                    user_id=user_id,
                    session_id=session_id,
                    query_domains=query_domains,
                )
                for sq in sub_queries
            ],
            return_exceptions=True,
        )

        contexts: list[str] = []
        sources: list[dict[str, Any]] = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning("摘要模式子查询处理失败: %s", r)
                continue
            r = cast(dict[str, Any], r)
            if r.get("context"):
                contexts.append(r["context"])
            if r.get("sources"):
                sources.extend(r["sources"])

        # 3. 简要摘要
        combined = "\n\n".join(contexts)[:4000]
        prompt = f"""请用 500 字以内总结以下内容:

{combined}

摘要:"""
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        response = await self._llm.achat(
            messages,
            tier=LLMTier.FAST,
            max_tokens=1000,
            temperature=0.3,
            user_id=user_id,
            session_id=session_id,
            span_name="summary-mode",
            step="researcher",
        )
        summary_contexts: list[str] = [response.content]
        # 合并上传文件上下文 (用户需求 8)
        if uploaded_files_context:
            summary_contexts.extend(uploaded_files_context)
        return {
            "sub_queries": sub_queries,
            "contexts": summary_contexts,
            "sources": sources,
            "visited_urls": {s["url"] for s in sources if s.get("url")},
        }

    async def _conduct_subtopics(
        self,
        query: str,
        *,
        agent_role: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        uploaded_files_context: list[str] | None = None,
        query_domains: list[str] | None = None,
    ) -> dict[str, Any]:
        """子主题模式: 按子主题分章节 (P2-01).

        - LLM 生成 3-5 个子主题
        - 每个子主题独立研究
        - 拼接为分章节报告
        """
        # 1. LLM 生成 3-5 个子主题
        subtopics = await self._generate_subtopics(
            query,
            agent_role=agent_role,
            user_id=user_id,
            session_id=session_id,
        )

        # 2. 每个子主题独立研究
        sections = await asyncio.gather(
            *[
                self._research_subtopic(
                    query,
                    topic,
                    user_id=user_id,
                    session_id=session_id,
                    query_domains=query_domains,
                )
                for topic in subtopics
            ],
            return_exceptions=True,
        )

        # 3. 拼接为分章节报告
        # V4-P1-04 优化 6: 失败/空 context 跳过时同步移除 subtopics 条目
        # 避免 sub_queries 含失败子主题但 contexts 不含, 导致 TOC 与正文不一致
        all_contexts: list[str] = []
        all_sources: list[dict[str, Any]] = []
        valid_subtopics: list[str] = []
        for topic, section in zip(subtopics, sections, strict=False):
            if isinstance(section, Exception):
                logger.warning("子主题 '%s' 研究失败, 从列表移除: %s", topic, section)
                continue
            section = cast(dict[str, Any], section)
            ctx = section.get("context", "")
            if not ctx:
                logger.warning("子主题 '%s' context 为空, 从列表移除", topic)
                continue
            valid_subtopics.append(topic)
            all_contexts.append(f"## {topic}\n\n{ctx}")
            if section.get("sources"):
                all_sources.extend(section["sources"])

        # 合并上传文件上下文 (用户需求 8)
        if uploaded_files_context:
            all_contexts.extend(uploaded_files_context)
        return {
            "sub_queries": valid_subtopics,
            "contexts": all_contexts,
            "sources": all_sources,
            "visited_urls": {s["url"] for s in all_sources if s.get("url")},
        }

    async def _generate_subtopics(
        self,
        query: str,
        *,
        agent_role: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[str]:
        """LLM 生成 3-5 个子主题 (P2-01)."""
        role_persona = agent_role or _DEFAULT_AGENT_ROLE
        prompt = f"""{role_persona}

请将以下研究问题拆解为 3-5 个子主题, 用于分章节研究.

研究问题: {query}

返回 JSON 数组, 每项为字符串:"""
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        # P1-7: 子主题列表是短 JSON 数组任务, SMART 足够, 省 2/3 成本 (与 report_generator 一致)
        response = await self._llm.achat(
            messages,
            tier=LLMTier.SMART,
            temperature=0.4,
            max_tokens=800,
            user_id=user_id,
            session_id=session_id,
            span_name="subtopics-gen",
            step="planner",
        )
        topics = safe_json_parse(response.content, fallback=[query])
        if isinstance(topics, list) and topics:
            return [str(t) for t in topics[:5]]
        return [query]

    async def _research_subtopic(
        self,
        parent_query: str,
        subtopic: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
        query_domains: list[str] | None = None,
    ) -> dict[str, Any]:
        """单个子主题研究 (复用 _process_sub_query, P2-01)."""
        sq = f"{parent_query} - {subtopic}"
        return await self._process_sub_query(
            sq,
            user_id=user_id,
            session_id=session_id,
            query_domains=query_domains,
        )

    async def _cached_search(
        self,
        searcher: BaseSearcher,
        query: str,
        *,
        max_results: int,
        query_domains: list[str] | None,
        user_id: str | None,
    ) -> list[dict[str, Any]]:
        """带 Redis 缓存的搜索 (相同 query+engine 5min TTL, trace 4ad14970 优化).

        子主题嵌套研究常重复搜索相同 query+engine, Redis 缓存避免重复调用.
        Redis 不可用时降级为直接搜索 (无缓存).

        Args:
            searcher: 搜索引擎实例
            query: 搜索查询词
            max_results: 最大结果数
            query_domains: 域名过滤列表
            user_id: 用户 ID (用于 Redis key 隔离)

        Returns:
            搜索结果列表
        """
        # 缓存 key: {agent_id}:{user_id}:search:result:{engine}:{query_hash}
        query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
        agent_id = self.settings.agent_name
        uid = user_id or "anonymous"
        cache_key = f"{agent_id}:{uid}:search:result:{searcher.name}:{query_hash}"

        # 1. 尝试读缓存
        redis = await get_redis_client(self.settings)
        if redis is not None:
            try:
                cached = await redis.get(cache_key)
                if cached:
                    logger.debug(
                        "搜索缓存命中: engine=%s, query=%s",
                        searcher.name,
                        query[:50],
                    )
                    return cast(list[dict[str, Any]], json.loads(cached))
            except Exception as e:  # noqa: BLE001
                logger.warning("搜索缓存读取失败 (降级直接搜索): %s", e)

        # 2. 缓存未命中: 直接搜索
        result = await searcher.search(
            query,
            max_results=max_results,
            query_domains=query_domains,
        )

        # 3. 写入缓存 (仅缓存非空结果, TTL=5min)
        if redis is not None and result:
            try:
                await redis.setex(
                    cache_key,
                    self.settings.search_cache_ttl,
                    json.dumps(result, ensure_ascii=False, default=str),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("搜索缓存写入失败 (不阻断): %s", e)

        return result

    async def _process_sub_query(
        self,
        sub_query: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
        query_domains: list[str] | None = None,
    ) -> dict[str, Any]:
        """处理单个子查询: 搜索 → 抓取 → 压缩.

        对标 GPT Researcher _process_sub_query.

        任务2 内存优化: try/finally 确保 searcher httpx 客户端在搜索完成后立即释放,
        避免 httpx.AsyncClient 泄漏 (原主因: 每次调用新建 9 个 searcher 实例, 每个含
        持久化 httpx.AsyncClient, 永不 close → ~90MB/请求泄漏).
        """
        # 1. 检测区域 (中文优先路由, 用户需求 5)
        region = detect_region(sub_query)
        # v2: 改用 get_searchers_async + QuotaCache, 跳过额度已满的引擎
        from src.skills.researcher.searchers import get_searchers_async
        from src.skills.researcher.searchers.exceptions import QuotaExceededError
        from src.skills.researcher.searchers.quota_cache import QuotaCache

        quota_cache = QuotaCache(self.settings)
        searchers = await get_searchers_async(region, self.settings, quota_cache)
        if not searchers:
            logger.warning(f"所有搜索引擎额度已满或不可用, sub_query={sub_query[:80]}")
            return {"context": "", "sources": [], "urls": set()}

        # 记录实际使用的搜索引擎 (便于排查 "METASO 未使用" 类问题)
        active_engines = [s.name for s in searchers]
        logger.info(f"sub_query 搜索引擎列表 (region={region}): {active_engines}")

        try:
            # 2. 并行搜索 (多个搜索引擎) + P1 Redis 缓存 (相同 query+engine 5min TTL)
            search_tasks = [
                self._cached_search(
                    s,
                    sub_query,
                    max_results=self.settings.max_search_results_per_query,
                    query_domains=query_domains,
                    user_id=user_id,
                )
                for s in searchers
            ]
            search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

            # 3. 合并搜索结果 (捕获 QuotaExceededError 写入缓存)
            all_results: list[dict[str, Any]] = []
            for searcher, r in zip(searchers, search_results, strict=True):
                if isinstance(r, QuotaExceededError):
                    await quota_cache.mark_exceeded(
                        engine=r.engine, reset_at=r.reset_at, reason="quota_exceeded"
                    )
                    logger.warning(f"{searcher.name} 额度已满: {r.message}")
                    continue
                if isinstance(r, BaseException):
                    logger.warning(f"{searcher.name} 调用失败: {r}")
                    continue
                all_results.extend(r)

            # P1-01: 跨搜索引擎 URL 去重
            all_results = deduplicate_results(all_results, key="url")
            urls = {r.get("url", "") for r in all_results if r.get("url")}

            # P1-Future-02: 域名过滤兜底 (针对不支持 query_domains 的引擎, 如 arxiv)
            if query_domains:
                all_results = BaseSearcher._filter_by_domains(all_results, query_domains)
                urls = {r.get("url", "") for r in all_results if r.get("url")}

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

            # P0-7 修复: 接入 MCP 工具调用 (仅当 mcp_strategy != "disabled" 时)
            # P1-5: 抽取到 conduct_mcp_if_enabled 公共方法, 消除与 deep_research 的重复 28 行块
            # 位置: scrape_urls 之后, context_manager.get_similar_content 之前
            context_parts: list[str] = []
            mcp_contexts = await conduct_mcp_if_enabled(
                self.settings, sub_query, user_id, session_id
            )
            if mcp_contexts:
                context_parts.append("\n\n".join(mcp_contexts))

            # 5. ContextManager 压缩 + 去重 (Token 优化)
            context = await self._context_manager.get_similar_content(
                sub_query,
                scraped,
                max_results=10,
                user_id=user_id,
                session_id=session_id,
            )
            if context:
                context_parts.append(context)

            return {
                "context": "\n\n".join(context_parts),
                "sources": all_results,
                "urls": urls,
            }
        finally:
            # 任务2 内存优化: 释放 searcher 持有的 httpx.AsyncClient (防泄漏)
            # 每个 httpx.AsyncClient 含 TCP 连接池 + SSL 上下文 + 内部缓冲区 ~5-15MB
            for s in searchers:
                try:
                    await s.close()
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"searcher {s.name} close 失败 (不阻断): {e}")
