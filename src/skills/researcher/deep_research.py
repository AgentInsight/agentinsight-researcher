"""DeepResearch 递归深度研究器.

节点为纯函数, 单一职责.
通过 breadth×depth 递归树探索, 每层聚合上下文.
11 项功能 + 自适应深度.
"""

from __future__ import annotations

import asyncio
import gc
import logging
from typing import Any

from src.common.http_client import get_http_client_pool
from src.common.json_utils import safe_json_parse
from src.config.settings import Settings, get_settings
from src.llm.client import LLMClient, LLMTier, get_llm_client
from src.observability.tracing import trace_chain
from src.skills.researcher.context_manager import ContextManager
from src.skills.researcher.mcp_coordinator import (
    MCPCoordinator,
    conduct_mcp_if_enabled,
    get_mcp_coordinator,
)
from src.skills.researcher.scrapers import scrape_urls
from src.skills.researcher.searchers import (
    BaseSearcher,
    detect_region,
)

logger = logging.getLogger(__name__)

# 10 级复杂度评估提示词 (4 维度评估)
_COMPLEXITY_PROMPT = """你是研究复杂度评估专家. 请评估以下查询的研究复杂度 (1-10), 返回严格 JSON.

## 评估维度 (每维度 1-10 分)

1. **scope (研究广度)**: 涉及的领域/实体/主题数量
   - 1-3: 单一实体/概念
   - 4-6: 2-3 个实体对比
   - 7-10: 跨多个领域/行业

2. **depth (研究深度)**: 分析层次与逻辑链长度
   - 1-3: 事实陈述/定义
   - 4-6: 优缺点分析/多维度对比
   - 7-10: 因果链/战略推演/长期预测

3. **multidisciplinary (跨学科)**: 涉及不同学科/行业数量
   - 1-3: 单一学科
   - 4-6: 2-3 个学科交叉
   - 7-10: 4+ 学科综合

4. **temporal (时效性)**: 是否需要最新数据/趋势预测
   - 1-3: 无时效要求 (定义/历史)
   - 4-6: 需要近期数据 (1 年内)
   - 7-10: 需要预测/前瞻 (未来趋势)

## 复杂度分级参考 (Rubric)

| 级别 | 名称 | 特征 | 示例 |
|------|------|------|------|
| L1 | 单一事实 | 事实型/定义型, 单一答案 | "什么是 RAG" |
| L2 | 事实+示例 | 定义+举例/列表 | "RAG 的主要组件有哪些" |
| L3 | 简单对比 | 2-3 个实体横向对比 | "React 和 Vue 哪个更好" |
| L4 | 多维度分析 | 多维度深入对比/优缺点 | "对比 React 和 Vue 的优缺点" |
| L5 | 技术深度 | 单一领域技术深度分析 | "RAG 在企业落地的技术挑战" |
| L6 | 行业调研 | 单一行业现状/趋势 | "2026 年 AI Agent 行业现状" |
| L7 | 综合研究 | 跨维度综合性研究 | "分析 2026 年 AI Agent 行业趋势与竞争格局" |
| L8 | 跨领域 | 跨 2-3 个领域综合 | "AI Agent 对金融/医疗/教育的影响" |
| L9 | 战略级 | 战略规划/决策支持 | "基于 AI Agent 趋势制定 2026-2028 技术战略" |
| L10 | 极复杂 | 跨学科/政策/伦理/长期影响 | "AI Agent 对全球经济长期影响 (技术/市场/政策/伦理)" |

## 输出格式 (严格 JSON, 不要 markdown 代码块)

{{
  "complexity": <1-10 整数>,
  "dimensions": {{
    "scope": <1-10>,
    "depth": <1-10>,
    "multidisciplinary": <1-10>,
    "temporal": <1-10>
  }},
  "reasoning": "<评估推理过程, 50-100 字>",
  "suggested_domains": ["<建议研究维度1>", "<建议研究维度2>"]
}}

## 查询: {query}

仅返回 JSON:"""

# 10 级复杂度 → breadth/depth/concurrency 映射表
# 子查询数 = breadth × (1 + next_breadth + next_breadth² + ...), next_breadth = max(2, breadth//2)
# max_sub_queries=42 守卫: L9-L10 (35 子查询) 不触发降级
_COMPLEXITY_MAP: dict[int, dict[str, int]] = {
    1: {"breadth": 3, "depth": 1, "concurrency": 3},  # 3 子查询
    2: {"breadth": 3, "depth": 1, "concurrency": 3},  # 3 子查询
    3: {"breadth": 4, "depth": 1, "concurrency": 4},  # 4 子查询
    4: {"breadth": 4, "depth": 2, "concurrency": 4},  # 4+8=12 子查询
    5: {"breadth": 4, "depth": 2, "concurrency": 4},  # 4+8=12 子查询
    6: {"breadth": 4, "depth": 2, "concurrency": 5},  # 4+8=12 子查询
    7: {"breadth": 4, "depth": 3, "concurrency": 6},  # 4+8+16=28 子查询
    8: {"breadth": 4, "depth": 3, "concurrency": 6},  # 4+8+16=28 子查询
    9: {"breadth": 5, "depth": 3, "concurrency": 8},  # 5+10+20=35 子查询
    10: {"breadth": 5, "depth": 3, "concurrency": 8},  # 5+10+20=35 子查询
}


class DeepResearcher:
    """递归深度研究器 (breadth×depth 树探索).

    每层: 1) 生成 breadth 个子查询 2) 并行检索 3) 聚合上下文 4) 递归下一层.
    对每个 result 递归, 由 researchGoal + followUpQuestions 驱动.
    """

    settings: Settings
    _llm: LLMClient
    _context_manager: ContextManager
    _visited_urls: set[str]
    # learnings 去重集合 (功能 9, list(set(all_learnings)))
    _learnings: set[str]
    # citations 累积字典 (功能 7, learning -> source_url)
    _citations: dict[str, str]
    # MCPCoordinator 惰性初始化
    _mcp: MCPCoordinator | None

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        context_manager: ContextManager | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or get_llm_client()
        self._context_manager = context_manager or ContextManager(self.settings)
        self._visited_urls = set()
        # 功能 9: learnings 跨子查询去重
        self._learnings = set()
        # 功能 7: citations 累积 (learning -> source_url)
        self._citations = {}
        # MCPCoordinator 惰性初始化 (避免启动期构造开销)
        self._mcp = None

    def _get_mcp(self) -> MCPCoordinator:
        """获取全局 MCPCoordinator 单例 (A1: 避免多实例导致缓存隔离失效).

        A1 修复: 改用 get_mcp_coordinator() 全局单例, 不再自建实例.
        原实现 self._mcp = MCPCoordinator(self.settings, self._llm) 导致:
        - deep_research 和 research_conductor 各自构造实例
        - _client_cache 相互独立, stdio 进程倍增
        - clear_cache() 只清当前实例, 另一实例缓存仍残留
        """
        return get_mcp_coordinator()

    def _cleanup_after_research(self, session_id: str | None) -> None:
        """研究完成后清理会话级资源 (P2-20).

        主动 GC + 清理 WrittenContentCompressor chunk cache + 清理 LLM 会话成本缓存.
        仅在顶层调用 (非递归子查询) 执行, 避免递归中误清缓存.
        失败不阻断返回结果.

        Args:
            session_id: 会话 ID, None 时跳过会话级缓存清理
        """
        # P2-20: 研究完成后主动 GC + 缓存清理
        # 释放 ~200-300MB 未回收内存 (WrittenContentCompressor chunk cache + embeddings)
        try:
            gc.collect()  # 触发循环引用清理
        except Exception as e:  # noqa: BLE001
            logger.warning("gc.collect 失败 (不阻断): %s", e)
        try:
            # 清理 WrittenContentCompressor chunk cache
            if hasattr(self._context_manager, "_written_compressor"):
                self._context_manager._written_compressor.reset()
        except Exception as e:  # noqa: BLE001
            logger.warning("WrittenContentCompressor reset 失败 (不阻断): %s", e)
        try:
            # 清理 LLM 会话成本缓存 (cleanup_session_cost 已在 P0-2 实现)
            # 仅清理当前会话, 不影响其他并发会话
            if session_id is not None:
                self._llm.cleanup_session_cost(session_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("cleanup_session_cost 失败 (不阻断): %s", e)

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
        _learnings: list[str] | None = None,
        _citations: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """递归深度研究.

        Args:
            query: 研究查询
            breadth: 每层子查询数, 默认 settings.deep_research_breadth
            depth: 递归深度, 默认 settings.deep_research_depth
            _current_depth: 当前递归深度 (内部用)
            _parent_context: 父节点上下文 (内部用)
            _learnings: 父节点累积 learnings (内部用, 顶层传 None, 内部共享 self._learnings)
            _citations: 父节点累积 citations (内部用, 顶层传 None, 内部共享 self._citations)

        Returns:
            {"query", "context", "sources", "learnings", "citations", "children": [...]}
        """
        # 自适应深度仅在顶层调用且未显式传参时启用, 避免递归层重复评估
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

        # max_sub_queries 守卫 (自适应深度机制, 防止递归树失控)
        # 递归树规模估算: breadth * (1 + next_breadth + next_breadth^2 + ... + next_breadth^(depth-1))
        # next_breadth = max(2, breadth // 2)
        if depth > 1:
            next_b = max(2, breadth // 2)
            total_sub_queries = breadth * sum(next_b**i for i in range(depth - 1 + 1))
        else:
            total_sub_queries = breadth
        if total_sub_queries > self.settings.deep_research_max_sub_queries:
            logger.warning(
                "递归树规模 %d 超过上限 %d, 降级到 depth=1 (query=%s)",
                total_sub_queries,
                self.settings.deep_research_max_sub_queries,
                query[:50],
            )
            depth = 1

        async with trace_chain(
            name=f"deep-research-d{_current_depth}",
            input={"query": query[:100], "depth": _current_depth, "breadth": breadth},
            user_id=user_id,
            session_id=session_id,
        ) as span:
            # 递归终止
            if _current_depth >= depth:
                span.update(output={"terminated": True, "depth": _current_depth})
                if _current_depth == 0:
                    try:
                        pool = await get_http_client_pool()
                        await pool.close_all()
                    except Exception as e:  # noqa: BLE001
                        logger.warning("HttpClientPool close_all 失败 (不阻断): %s", e)
                    # P2-20: 顶层研究完成 (终止分支) 主动 GC + 缓存清理
                    self._cleanup_after_research(session_id)
                return {
                    "query": query,
                    "context": _parent_context,
                    "sources": [],
                    "learnings": [],
                    "citations": {},
                    "children": [],
                }

            # 1. 生成 breadth 个子查询 (含 researchGoal)
            sub_queries = await self._generate_sub_queries(
                query,
                breadth,
                parent_context=_parent_context,
                user_id=user_id,
                session_id=session_id,
            )

            # 2. 并行检索每个子查询 (传查询字符串, 与 researchGoal 解耦)
            results = await asyncio.gather(
                *[
                    self._research_sub_query(
                        sq["query"],
                        user_id=user_id,
                        session_id=session_id,
                        query_domains=query_domains,
                    )
                    for sq in sub_queries
                ]
            )

            # 为每个 result 关联 researchGoal (功能 3 依赖, 用于 _build_next_query)
            for sq, r in zip(sub_queries, results, strict=True):
                r["researchGoal"] = sq.get("researchGoal", sq["query"])

            # 3. 聚合上下文 (列表累积 + learnings + citation + 裁剪)
            all_context_list: list[str] = []
            all_sources = [s for r in results for s in r["sources"]]

            for r in results:
                if r.get("context"):
                    all_context_list.append(r["context"])
                # learnings 去重 + citation 标注 (功能 7/9)
                for learning in r.get("learnings", []):
                    if learning not in self._learnings:
                        self._learnings.add(learning)
                        citation = r.get("citations", {}).get(learning, "")
                        if citation:
                            all_context_list.append(f"{learning} [Source: {citation}]")
                        else:
                            all_context_list.append(learning)
                # 累积 citations (功能 7)
                for k, v in r.get("citations", {}).items():
                    self._citations.setdefault(k, v)

            # 裁剪到 max_context_words (功能 4/5)
            trimmed = self._trim_context_to_word_limit(
                all_context_list, self.settings.max_context_words
            )
            aggregated_context = "\n\n---\n\n".join(trimmed)
            if _parent_context:
                aggregated_context = f"{_parent_context}\n\n---\n\n{aggregated_context}"

            # 4. 递归下一层 (对每个 result 递归, 由 researchGoal+followUpQuestions 驱动)
            if depth - _current_depth > 1:
                # 功能 2: next_breadth = max(2, breadth // 2)
                next_breadth = max(2, breadth // 2)
                children = await asyncio.gather(
                    *[
                        self.research(
                            # 功能 3: 递归查询由 researchGoal + followUpQuestions 拼接
                            self._build_next_query(r),
                            breadth=next_breadth,
                            depth=depth,
                            user_id=user_id,
                            session_id=session_id,
                            query_domains=query_domains,
                            _current_depth=_current_depth + 1,
                            _parent_context=aggregated_context,
                        )
                        for r in results
                        if r.get("context")  # 跳过空结果
                    ]
                )
            else:
                children = []

            span.update(
                output={
                    "context_len": len(aggregated_context),
                    "sources_count": len(all_sources),
                    "children_count": len(children),
                    "learnings_count": len(self._learnings),
                }
            )
            if _current_depth == 0:
                try:
                    pool = await get_http_client_pool()
                    await pool.close_all()
                except Exception as e:  # noqa: BLE001
                    logger.warning("HttpClientPool close_all 失败 (不阻断): %s", e)
                # P2-20: 顶层研究完成 主动 GC + 缓存清理
                self._cleanup_after_research(session_id)
            return {
                "query": query,
                "context": aggregated_context,
                "sources": all_sources,
                "learnings": list(self._learnings),
                "citations": dict(self._citations),
                "children": list(children),
            }

    async def _assess_complexity(
        self,
        query: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, int]:
        """评估查询复杂度, 返回自适应参数 (10 级 + 4 维度).

        用 LLMTier.SMART 评估查询复杂度 (1-10), 映射到 breadth/depth/concurrency:
            L1-L2  (简单):   breadth=3, depth=1, concurrency=3 (3 子查询, 单层)
            L3     (简单+):  breadth=4, depth=1, concurrency=4 (4 子查询, 单层)
            L4-L5  (中等):   breadth=4, depth=2, concurrency=4 (4+8=12 子查询)
            L6     (中等+):  breadth=4, depth=2, concurrency=5 (4+8=12, 高并发)
            L7-L8  (复杂):   breadth=4, depth=3, concurrency=6 (4+8+16=28 子查询)
            L9-L10 (极复杂): breadth=5, depth=3, concurrency=8 (5+10+20=35 子查询)

        4 维度评估:
            - scope (研究广度): 涉及领域/实体数量
            - depth (研究深度): 分析层次/逻辑链长度
            - multidisciplinary (跨学科): 涉及不同学科/行业数量
            - temporal (时效性): 是否需要最新数据/趋势预测

        LLM 失败时返回 L4-L5 中等参数 (breadth=4/depth=2/concurrency=4, 12 子查询),
        确保失败时仍能给出中等质量研究结果, 而非最简陋的单层研究.
        """
        # 默认参数兜底 (LLM 失败时使用 L4-L5 中等参数)
        # 理由: 大多数查询本身为中等复杂度, 用 L4-L5 (12 子查询) 比 depth=1 (4 子查询)
        # 更贴合实际分布, 失败时仍能给出中等质量研究结果.
        default_params: dict[str, int] = {
            "breadth": 4,
            "depth": 2,  # L4-L5 中等参数: 4+8=12 子查询
            "concurrency": 4,
        }

        prompt = _COMPLEXITY_PROMPT.format(query=query)
        messages = [{"role": "user", "content": prompt}]

        try:
            response = await self._llm.achat(
                messages,
                tier=LLMTier.SMART,
                temperature=0.0,
                max_tokens=500,  # 500 token 容错空间 (4 维度 + reasoning + domains)
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
            if not isinstance(complexity, int) or complexity < 1 or complexity > 10:
                logger.warning(
                    "复杂度评分非法 (%r, 应为 1-10 整数), 降级默认值, query=%s",
                    complexity,
                    query[:50],
                )
                return default_params

            # 从映射表获取参数 (clamped to 1-10)
            complexity = max(1, min(10, complexity))
            params = _COMPLEXITY_MAP.get(complexity, _COMPLEXITY_MAP[5])  # 兜底用 L5

            # 记录维度评分 (供日志/trace 分析, 不影响路由)
            dimensions = parsed.get("dimensions", {})
            reasoning = parsed.get("reasoning", "")
            logger.info(
                "自适应深度评估: complexity=L%d (scope=%s depth=%s multi=%s temporal=%s) "
                "→ breadth=%d depth=%d concurrency=%d (query=%s, reasoning=%s)",
                complexity,
                dimensions.get("scope", "?"),
                dimensions.get("depth", "?"),
                dimensions.get("multidisciplinary", "?"),
                dimensions.get("temporal", "?"),
                params["breadth"],
                params["depth"],
                params["concurrency"],
                query[:50],
                reasoning[:80],
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
    ) -> list[dict[str, str]]:
        """LLM 生成 breadth 个子查询 (含 researchGoal).

        Returns:
            [{"query": "<搜索查询>", "researchGoal": "<研究目标>"}, ...]
        """
        prompt = f"""你是研究分析专家. 请将以下研究问题拆解为 {breadth} 个具体的子查询, 用于搜索引擎检索.

研究问题: {query}

父节点上下文 (如有):
{parent_context[:2000]}

要求:
1. 子查询应覆盖问题的不同维度
2. 每个子查询应具体可检索
3. 返回 JSON 数组, 每项为对象: [{{"query": "<搜索查询>", "researchGoal": "<研究目标>"}}]

仅返回 JSON 数组:"""
        messages = [{"role": "user", "content": prompt}]
        response = await self._llm.achat(
            messages,
            tier=LLMTier.STRATEGIC,
            temperature=0.4,
            max_tokens=1500,
            reasoning_effort=self.settings.deep_research_reasoning_effort,
            user_id=user_id,
            session_id=session_id,
            span_name="deep-research-planner",
            step="deep_research",
        )
        return self._parse_search_queries(response.content, breadth)

    @staticmethod
    def _parse_search_queries(response: str, num_queries: int) -> list[dict[str, str]]:
        """解析子查询响应.

        支持两种格式:
        - [{"query": "...", "researchGoal": "..."}] (标准)
        - ["query1", "query2"] (降级, researchGoal=query)
        """
        parsed = safe_json_parse(response, fallback=None)
        if isinstance(parsed, list):
            queries: list[dict[str, str]] = []
            for item in parsed:
                if isinstance(item, dict):
                    q = str(item.get("query", "")).strip()
                    rg = str(item.get("researchGoal", "")).strip()
                    if q and rg:
                        queries.append({"query": q, "researchGoal": rg})
                elif isinstance(item, str) and item.strip():
                    # 降级: 字符串数组, researchGoal = query
                    queries.append({"query": item.strip(), "researchGoal": item.strip()})
            if queries:
                return queries[:num_queries]
        # 完全降级: 返回单个 query (用响应文本前 100 字符)
        fallback_q = response.strip()[:100] if response.strip() else "query"
        return [{"query": fallback_q, "researchGoal": fallback_q}]

    async def _research_sub_query(
        self,
        sub_query: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
        query_domains: list[str] | None = None,
    ) -> dict[str, Any]:
        """单个子查询: 搜索 + 抓取 + 压缩 + learnings 提取.

        v1.1 改造:
        - 串行 → 并行 (asyncio.gather)
        - 接入 QuotaCache 额度缓存 (捕获 QuotaExceededError 写入缓存)

        功能 6:
        - 返回值新增 learnings/followUpQuestions/citations/researchGoal
        - researchGoal 由 research() 调用处关联 (此处不设置)
        """
        # 初始化 searchers 为空列表, 供 finally 块安全访问
        # (若 get_searchers_async 抛异常, searchers 仍为 [], finally 不报错)
        searchers: list[Any] = []
        try:
            # 搜索
            region = detect_region(sub_query)

            # v1.1: 优先使用异步版本 (带额度缓存检查)
            from src.skills.researcher.searchers import get_searchers_async
            from src.skills.researcher.searchers.exceptions import QuotaExceededError
            from src.skills.researcher.searchers.quota_cache import QuotaCache

            quota_cache = QuotaCache(self.settings)
            searchers = await get_searchers_async(region, self.settings, quota_cache)

            # v1.1: 并行调用多个搜索引擎
            search_tasks = [
                s.search(
                    sub_query,
                    max_results=self.settings.max_search_results_per_query,
                    query_domains=query_domains,
                )
                for s in searchers
            ]
            search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

            urls: list[str] = []
            sources: list[dict[str, Any]] = []
            for searcher, r in zip(searchers, search_results, strict=True):
                # v1.1: 捕获额度已满异常, 写入 QuotaCache
                if isinstance(r, QuotaExceededError):
                    await quota_cache.mark_exceeded(
                        engine=r.engine,
                        reset_at=r.reset_at,
                        reason="quota_exceeded",
                    )
                    logger.warning(f"{searcher.name} 额度已满: {r.message}")
                    continue
                if isinstance(r, BaseException):
                    logger.warning(f"{searcher.name} 调用失败: {r}")
                    continue
                # 域名过滤兜底 (针对不支持 query_domains 的引擎, 如 arxiv)
                if query_domains:
                    r = BaseSearcher._filter_by_domains(r, query_domains)
                for item in r:
                    if item.get("url") and item["url"] not in self._visited_urls:
                        urls.append(item["url"])
                        self._visited_urls.add(item["url"])
                        sources.append(item)

            # 抓取
            docs = await scrape_urls(
                urls[: self.settings.max_scraper_workers],
                max_workers=self.settings.max_scraper_workers,
                rate_limit_delay=self.settings.scraper_rate_limit_delay,
            )

            # 接入 MCP 工具调用 (仅当 mcp_strategy != "disabled" 时)
            # 抽取到 conduct_mcp_if_enabled 公共方法, 消除与 research_conductor 的重复 28 行块
            # 位置: scrape_urls 之后, context_manager.get_similar_content 之前
            context_parts: list[str] = []
            mcp_contexts = await conduct_mcp_if_enabled(
                self.settings, sub_query, user_id, session_id
            )
            if mcp_contexts:
                context_parts.append("\n\n".join(mcp_contexts))

            # 压缩
            context = await self._context_manager.get_similar_content(
                sub_query,
                docs,
                max_results=5,
                user_id=user_id,
                session_id=session_id,
            )
            if context:
                context_parts.append(context)

            context_str = "\n\n".join(context_parts)

            # 功能 6: learnings 提取
            learnings_result = await self._process_research_results(
                sub_query,
                context_str,
                num_learnings=self.settings.deep_research_num_learnings,
                user_id=user_id,
                session_id=session_id,
            )

            return {
                "context": context_str,
                "sources": sources,
                "learnings": learnings_result["learnings"],
                "followUpQuestions": learnings_result["followUpQuestions"],
                "citations": learnings_result["citations"],
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("DeepResearch 子查询 '%s' 失败: %s", sub_query[:50], e)
            return {
                "context": "",
                "sources": [],
                "learnings": [],
                "followUpQuestions": [],
                "citations": {},
            }
        finally:
            # 释放 searcher 持有的 httpx.AsyncClient (防泄漏)
            # 每个 httpx.AsyncClient 含 TCP 连接池 + SSL 上下文 + 内部缓冲区 ~5-15MB
            # 参考 research_conductor.py 的 _process_sub_query try/finally 实现
            for s in searchers:
                try:
                    await s.close()
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"searcher {s.name} close 失败 (不阻断): {e}")

    async def _process_research_results(
        self,
        query: str,
        context: str,
        *,
        num_learnings: int = 3,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """提取 learnings + followUpQuestions + citations.

        Args:
            query: 子查询
            context: 检索得到的上下文
            num_learnings: 提取的 learnings 数量上限

        Returns:
            {"learnings": [str], "followUpQuestions": [str], "citations": {str: str}}
        """
        # 空上下文直接返回空结果 (避免无意义 LLM 调用)
        if not context.strip():
            return {"learnings": [], "followUpQuestions": [], "citations": {}}

        prompt = (
            f"Given the following research results for the query '{query}', extract key learnings and suggest "
            "follow-up questions. For each learning, include a citation to the source URL if available.\n\n"
            "Return ONLY a JSON object using this exact schema:\n"
            '{"learnings": [{"insight": "<insight>", "sourceUrl": "<url or empty string>"}], '
            '"followUpQuestions": ["<question 1>", "<question 2>"]}\n\n'
            f"Research results:\n{context[:8000]}"  # 截断防止 prompt 过长
        )
        messages = [{"role": "user", "content": prompt}]
        try:
            response = await self._llm.achat(
                messages,
                tier=LLMTier.STRATEGIC,
                temperature=0.4,
                max_tokens=1000,
                reasoning_effort=self.settings.deep_research_reasoning_effort,
                user_id=user_id,
                session_id=session_id,
                span_name="deep-research-learnings",
                step="deep_research",
            )
            return self._parse_research_results(response.content, num_learnings)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "learnings 提取失败, 降级空结果: %s (query=%s)",
                e,
                query[:50],
            )
            return {"learnings": [], "followUpQuestions": [], "citations": {}}

    @staticmethod
    def _parse_research_results(response: str, num_learnings: int) -> dict[str, Any]:
        """解析 LLM 返回的 learnings/followUpQuestions/citations.

        支持两种格式:
        - {"learnings": [{"insight": "...", "sourceUrl": "..."}], "followUpQuestions": [...]}
        - {"learnings": ["insight1", "insight2"], "followUpQuestions": [...]} (降级)
        """
        parsed = safe_json_parse(response, fallback=None)
        if isinstance(parsed, dict):
            learnings_payload = parsed.get("learnings", [])
            follow_up_payload = parsed.get("followUpQuestions") or parsed.get("questions") or []
            learnings: list[str] = []
            citations: dict[str, str] = {}
            if isinstance(learnings_payload, list):
                for item in learnings_payload:
                    if isinstance(item, dict):
                        learning = str(item.get("insight") or item.get("learning") or "").strip()
                        citation = str(item.get("sourceUrl") or item.get("citation") or "").strip()
                    else:
                        learning = str(item).strip()
                        citation = ""
                    if learning:
                        learnings.append(learning)
                        if citation:
                            citations[learning] = citation
            questions = [str(q).strip() for q in follow_up_payload if str(q).strip()]
            if learnings or questions:
                return {
                    "learnings": learnings[:num_learnings],
                    "followUpQuestions": questions[:num_learnings],
                    "citations": citations,
                }
        return {"learnings": [], "followUpQuestions": [], "citations": {}}

    def _build_next_query(self, result: dict[str, Any]) -> str:
        """构建下一层递归查询.

        由 researchGoal + followUpQuestions 拼接, 内容驱动深入探索.
        """
        research_goal = result.get("researchGoal", "")
        follow_ups = result.get("followUpQuestions", [])
        parts = []
        if research_goal:
            parts.append(f"Previous research goal: {research_goal}")
        if follow_ups:
            parts.append(f"Follow-up questions: {' '.join(follow_ups)}")
        return "\n".join(parts) if parts else result.get("query", "")

    @staticmethod
    def _trim_context_to_word_limit(context_list: list[str], max_words: int) -> list[str]:
        """裁剪上下文列表到词数上限.

        从后向前保留最近/最相关内容, 超限的早期上下文被丢弃.
        """
        total_words = 0
        trimmed: list[str] = []
        for item in reversed(context_list):
            words = len(item.split())
            if total_words + words <= max_words:
                trimmed.insert(0, item)
                total_words += words
            elif not trimmed:
                # 至少保留第一条 (截断到 max_words)
                trimmed.insert(0, " ".join(item.split()[:max_words]))
                break
            else:
                break
        return trimmed
