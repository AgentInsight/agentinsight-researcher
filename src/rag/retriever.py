"""混合检索器: BM25 + 向量 + RRF + Rerank.

AGENTS.md 第 7 章硬约束:
- 检索必须混合 BM25 + 向量 (bge-large-zh-v1.5), 默认 vector_weight=0.7 / bm25_weight=0.3
- 重排序默认不启用; 当 rerank_enabled=True 时经 bge-reranker-v2-m3, Top-K 召回后 rerank
- score_threshold 默认 0.3, 低于阈值丢弃 (仅 rerank 启用时生效)
- Embedding 调用统一走 rag/embeddings.py, 禁止业务代码直连 API

对标 AgentInsightService common/retriever.py 的 HybridRetriever 模式.
所有检索必须包裹在 trace_retriever span 内.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any, cast

import httpx
import jieba
import orjson
from rank_bm25 import BM25Okapi

from src.common.redis_client import get_redis_client
from src.config.settings import Settings, get_settings
from src.observability.tracing import trace_retriever
from src.rag.embeddings import EmbeddingsClient, get_embeddings_client
from src.rag.qdrant_manager import QdrantManager

logger = logging.getLogger(__name__)


class HybridRetriever:
    """混合检索器: BM25 + 向量 + RRF 融合 + Rerank (可选).

    AGENTS.md 第 7 章: 检索必须混合 BM25 + 向量; rerank 默认不启用,
    rerank_enabled=True 时经 bge-reranker-v2-m3.
    """

    RETRIEVER_CACHE_TTL: int = 3600  # 检索结果缓存 TTL (秒, 1 小时, 可配置)

    settings: Settings
    _embeddings: EmbeddingsClient
    _qdrant: QdrantManager
    _rerank_client: httpx.AsyncClient
    _redis: Any  # aioredis.Redis | None (Redis 客户端, 不可用时为 None)
    _redis_initialized: bool  # P0-5: 惰性初始化标记, 避免每次检索都调用 get_redis_client
    _bm25_corpus: list[list[str]]  # BM25 语料 (jieba 分词后)
    _bm25_docs: list[dict[str, Any]]  # BM25 原始文档
    _bm25: BM25Okapi | None
    # P1-6: BM25 分词结果缓存 (key=content sha256, value=tokens list)
    # 避免重复语料更新时对同一 content 重复 jieba.cut
    _token_cache: dict[str, list[str]]
    # P1-4: singleflight 互斥锁 (按 query hash 分锁, 防止缓存击穿并发重复计算)
    _inflight_locks: dict[str, asyncio.Lock]

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._embeddings = get_embeddings_client()
        self._qdrant = QdrantManager(self.settings)
        # TEI API_KEY 鉴权 (AGENTS.md 第 7/12 章): rerank 服务端开启 API_KEY 时,
        # 客户端必须携带 Authorization: Bearer <key> 请求头
        headers: dict[str, str] = {}
        if self.settings.rerank_api_key:
            headers["Authorization"] = f"Bearer {self.settings.rerank_api_key}"
        self._rerank_client = httpx.AsyncClient(
            base_url=self.settings.rerank_base_url,
            timeout=30.0,
            headers=headers,
        )
        # P0-5: Redis 缓存客户端改用统一工厂 get_redis_client() (AGENTS.md 第 7 章:
        # 键格式 {agent_id}:{user_id}:{module}:{type}:{id}, 键前缀由本类管理).
        # __init__ 是同步方法, 故惰性到首次 _get_cache/_set_cache 时初始化 (避免阻塞).
        # Redis 不可用时降级为无缓存, 不阻断检索.
        self._redis: Any = None
        self._redis_initialized = False
        self._bm25_corpus = []
        self._bm25_docs = []
        self._bm25 = None
        # P1-6: 分词缓存初始化
        self._token_cache = {}
        # P1-4: singleflight 锁字典初始化
        self._inflight_locks = {}

    def build_namespaces(self, user_id: str | None = None) -> list[str]:
        """构建检索 namespace 列表 (共享 + 用户私有).

        AGENTS.md 第 7 章: 检索时必须显式传目标 namespace 列表 (共享 + 当前用户私有).
        旧版兼容: 仍使用 build_shared_namespace / build_user_namespace.
        新代码应使用 build_data_namespaces (含私有数据存在性检查).
        """
        namespaces = [self._qdrant.build_shared_namespace()]
        if user_id:
            namespaces.append(self._qdrant.build_user_namespace(user_id))
        return namespaces

    async def build_data_namespaces(self, user_id: str | None = None) -> tuple[list[str], bool]:
        """构建数据检索 namespace 列表 (新 API, 含私有数据存在性检查).

        用户需求: "私有数据搜索的时候先判断有没有私有数据,
        先判断有没有对应命名空间, 再看命名空间里面有没有数据, 有的话才搜索".

        新命名空间设计:
        - 共享数据: {agent_id}-data (所有用户共享)
        - 用户私有数据: {agent_id}-data:{user_id} (仅该用户可检索)

        P1-04: 同时检查共享 namespace 是否有数据, 避免无数据时调用 embeddings (减少 429).

        Args:
            user_id: 用户 ID, 为 None 或空字符串时只检索共享数据

        Returns:
            (namespaces, has_private): namespaces 为检索列表 (可能为空),
            has_private 表示是否有私有数据
        """
        namespaces: list[str] = []
        has_private = False

        # P1-04: 检查共享 namespace 是否有数据, 无数据时不加入检索列表
        # 避免无数据时调用 embeddings (减少 429)
        shared_namespace = self._qdrant.build_data_shared_namespace()
        shared_has_data = await self._qdrant.namespace_has_data(shared_namespace)
        if shared_has_data:
            namespaces.append(shared_namespace)
            logger.debug("共享 namespace 有数据, 加入检索列表")
        else:
            logger.debug("共享 namespace 无数据, 不加入检索列表 (避免无意义 embeddings 调用)")

        if user_id:
            # 先判断有没有私有数据, 有的话才加入检索列表 (避免空 namespace 无效检索)
            has_private = await self._qdrant.has_user_private_data(user_id)
            if has_private:
                namespaces.append(self._qdrant.build_data_user_namespace(user_id))
                logger.debug("用户 %s 有私有数据, 加入检索 namespace", user_id)
            else:
                logger.debug("用户 %s 无私有数据", user_id)
        return namespaces, has_private

    async def retrieve(
        self,
        query: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
        top_k: int | None = None,
    ) -> list[dict[str, Any]]:
        """混合检索: BM25 + 向量 + RRF + Rerank.

        AGENTS.md 第 7 章: 默认 vector_weight=0.7 / bm25_weight=0.3, RRF k=60.
        """
        k = top_k or self.settings.rerank_top_k
        # 新 API: 含私有数据存在性检查, 仅当用户有私有数据时才加入私有 namespace
        namespaces, has_private = await self.build_data_namespaces(user_id)

        # P1-04: namespaces 为空时直接返回 (共享和私有 namespace 均无数据)
        # 避免无数据时调用 embeddings (减少 429), 上游走搜索引擎路径
        if not namespaces:
            logger.info(
                "RAG 检索跳过 (无可用 namespace, 共享和私有均无数据): query=%s",
                query[:50],
            )
            return []

        # P2-04: 检查 Redis 缓存 (命中直接返回, 不走 BM25+Vector)
        cache_key = self._cache_key(query, user_id)
        cached = await self._get_cache(cache_key, user_id)
        if cached is not None:
            logger.info("RAG 缓存命中: query=%s", query[:50])
            return cached

        # P1-4: singleflight 互斥锁 (按 query+user_id hash 分锁, 防止缓存击穿)
        # 同一 query 并发请求只允许一个执行 BM25+Vector+Rerank, 其他等待结果
        inflight_key = cache_key  # 复用 cache_key (已含 agent_id+user_id+query_hash)
        lock = self._inflight_locks.get(inflight_key)
        if lock is None:
            lock = asyncio.Lock()
            self._inflight_locks[inflight_key] = lock
        async with lock:
            # 双重检查: 持有锁后再次查缓存, 可能在等待期间已被其他协程填充
            cached = await self._get_cache(cache_key, user_id)
            if cached is not None:
                logger.info("RAG 缓存命中 (singleflight 等待后): query=%s", query[:50])
                return cached

            async with trace_retriever(
                name="hybrid-retrieve",
                input={"query": query[:200], "namespaces": namespaces, "top_k": k},
                metadata={"retriever_type": "hybrid", "has_private_data": has_private},
                user_id=user_id,
                session_id=session_id,
            ) as span:
                # 并行执行 BM25 + 向量检索
                vector_task = self._vector_search(query, namespaces, k * 3)
                bm25_task = self._bm25_search(query, k * 3)

                results = await asyncio.gather(
                    vector_task,
                    bm25_task,
                    return_exceptions=True,
                )

                # 容错: 任一失败用空列表
                vector_results: list[dict[str, Any]] = []
                if isinstance(results[0], Exception):
                    logger.warning("向量检索失败: %s", results[0])
                else:
                    vector_results = cast(list[dict[str, Any]], results[0])
                bm25_results: list[dict[str, Any]] = []
                if isinstance(results[1], Exception):
                    logger.warning("BM25 检索失败: %s", results[1])
                else:
                    bm25_results = cast(list[dict[str, Any]], results[1])

                # RRF 融合
                fused = self._rrf_fuse(
                    vector_results,
                    bm25_results,
                    vector_weight=self.settings.vector_weight,
                    bm25_weight=self.settings.bm25_weight,
                )

                # P1-02: 按内容 hash 去重 (相同内容不同来源的文档, 对标 GPTR context 去重)
                fused = self._deduplicate_by_content_hash(fused)

                # Rerank (AGENTS.md 第 7 章: 默认不启用, rerank_enabled=True 时经 bge-reranker-v2-m3)
                if self.settings.rerank_enabled:
                    reranked = await self._rerank(query, fused, k)
                else:
                    # rerank 未启用, 直接用 RRF 融合分数取 top_k
                    # 注意: score_threshold 仅适用于 rerank 分数 (0~1), RRF 融合分数不应用此阈值
                    reranked = fused[:k]

                span.update(
                    output={"matched": len(reranked)},
                    metadata={
                        "candidate_count": len(fused),
                        "retriever_type": "hybrid",
                        "top_score": reranked[0]["score"] if reranked else 0.0,
                    },
                )
                # P2-04: 写入 Redis 缓存 (TTL=RETRIEVER_CACHE_TTL 秒, Redis 不可用时静默跳过)
                await self._set_cache(cache_key, reranked, user_id)
                return reranked

    async def _vector_search(
        self,
        query: str,
        namespaces: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        """向量检索."""
        query_vector = await self._embeddings.embed_query(query)
        if not query_vector:
            return []
        return await self._qdrant.search(
            query_vector=query_vector,
            namespaces=namespaces,
            limit=limit,
        )

    async def _bm25_search(
        self,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """BM25 检索 (基于内存语料, jieba 中文分词).

        AGENTS.md 第 7 章: rank-bm25 + jieba, 中文分词 + IDF.
        P1-6: query 分词结果缓存 (重复 query 命中缓存, 避免重复 jieba.cut).
        """
        if not self._bm25 or not self._bm25_docs:
            return []

        # P1-6: query 分词缓存 (query 通常重复率高)
        query_tokens = self._get_tokens(query)
        scores = self._bm25.get_scores(query_tokens)

        # 按分数排序取 top limit
        ranked = sorted(
            zip(scores, self._bm25_docs, strict=False),
            key=lambda x: x[0],
            reverse=True,
        )[:limit]

        results = []
        for score, doc in ranked:
            if score <= 0:
                continue
            results.append(
                {
                    "content": doc["content"],
                    "metadata": doc.get("metadata", {}),
                    "namespace": doc.get("namespace", ""),
                    "score": float(score),
                }
            )
        return results

    def _rrf_fuse(
        self,
        vector_results: list[dict[str, Any]],
        bm25_results: list[dict[str, Any]],
        *,
        vector_weight: float,
        bm25_weight: float,
    ) -> list[dict[str, Any]]:
        """倒数排名融合 (RRF).

        AGENTS.md 第 7 章: RRF k=60 (业界标准).
        """
        k = self.settings.rrf_k
        scores: dict[str, float] = {}
        docs: dict[str, dict[str, Any]] = {}

        # 向量结果排名融合
        for rank, doc in enumerate(vector_results):
            content = doc["content"]
            rrf_score = vector_weight / (k + rank + 1)
            scores[content] = scores.get(content, 0.0) + rrf_score
            docs[content] = doc

        # BM25 结果排名融合
        for rank, doc in enumerate(bm25_results):
            content = doc["content"]
            rrf_score = bm25_weight / (k + rank + 1)
            scores[content] = scores.get(content, 0.0) + rrf_score
            if content not in docs:
                docs[content] = doc

        # 按融合分数排序
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [{**docs[content], "score": score} for content, score in ranked]

    def _deduplicate_by_content_hash(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按内容 hash 去重 (对标 GPTR context 去重).

        P1-02: RRF 融合后调用, 去除相同内容不同来源的重复文档.
        """
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for r in results:
            content = r.get("content", r.get("body", ""))
            h = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if h not in seen:
                seen.add(h)
                deduped.append(r)
        return deduped

    def _cache_key(self, query: str, user_id: str | None) -> str:
        """构建 Redis 缓存键 (AGENTS.md 第 7 章: {agent_id}:{user_id}:{module}:{type}:{id})."""
        agent_id = self.settings.agent_name
        uid = user_id or self.settings.default_user_id
        query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
        return f"{agent_id}:{uid}:rag:retriever:{query_hash}"

    def _lru_key(self, user_id: str | None = None) -> str:
        """构建 LRU 访问时间 Sorted Set 键 (AGENTS.md 第 7 章: {agent_id}:{user_id}:cache_access_times)."""
        agent_id = self.settings.agent_name
        uid = user_id or self.settings.default_user_id
        return f"{agent_id}:{uid}:cache_access_times"

    async def _ensure_redis(self) -> Any:
        """P0-5: 惰性初始化 Redis 客户端 (复用 common.redis_client 全局单例).

        __init__ 是同步方法, 无法 await, 故推迟到首次 _get_cache/_set_cache 时初始化.
        Redis 不可用时返回 None (降级无缓存, 不阻断检索).

        Returns:
            aioredis.Redis | None
        """
        if self._redis_initialized:
            return self._redis
        # 复用全局单例 (双重检查锁由 get_redis_client 内部保证)
        self._redis = await get_redis_client(self.settings)
        self._redis_initialized = True
        return self._redis

    async def _get_cache(
        self,
        key: str,
        user_id: str | None = None,
    ) -> list[dict[str, Any]] | None:
        """读取 Redis 缓存 (TTL + LRU 双策略, P1-03).

        命中时更新访问时间 (LRU 排序). Redis 不可用时降级返回 None, 不阻断检索.
        """
        if not self._redis_initialized:
            await self._ensure_redis()
        if self._redis is None:
            return None
        try:
            data = await self._redis.get(key)
            if data is None:
                return None
            # P1-03: 命中时更新 LRU 访问时间 (ZADD score=当前时间戳)
            if self.settings.redis_cache_lru_enabled:
                lru_key = self._lru_key(user_id)
                await self._redis.zadd(lru_key, {key: time.time()})
            return cast(list[dict[str, Any]], orjson.loads(data))
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis 缓存读取失败, 降级无缓存: %s", e)
            return None

    async def _set_cache(
        self,
        key: str,
        results: list[dict[str, Any]],
        user_id: str | None = None,
    ) -> None:
        """写入 Redis 缓存 (TTL + LRU 双策略, P1-03).

        写入后检查总数, 超过 max_size 时淘汰最久未访问.
        Redis 不可用时静默跳过.
        """
        if not self._redis_initialized:
            await self._ensure_redis()
        if self._redis is None:
            return
        try:
            await self._redis.set(
                key,
                orjson.dumps(results, default=str),
                ex=self.RETRIEVER_CACHE_TTL,
            )
            # P1-03: 写入 LRU 访问时间 + 检查总数淘汰最久未访问
            if self.settings.redis_cache_lru_enabled:
                lru_key = self._lru_key(user_id)
                await self._redis.zadd(lru_key, {key: time.time()})
                count = await self._redis.zcard(lru_key)
                if count > self.settings.redis_cache_max_size:
                    # 取最久未访问的 (count - max_size) 条
                    to_evict = await self._redis.zrange(
                        lru_key,
                        0,
                        count - self.settings.redis_cache_max_size - 1,
                    )
                    if to_evict:
                        # 删除缓存数据 + LRU 记录 (pipeline 批量减少 RTT)
                        pipe = self._redis.pipeline()
                        for k in to_evict:
                            k_str = k.decode() if isinstance(k, bytes) else k
                            pipe.delete(k_str)
                            pipe.zrem(lru_key, k_str)
                        await pipe.execute()
                        logger.debug(
                            "LRU 淘汰 %d 条缓存 (当前 %d > 上限 %d)",
                            len(to_evict),
                            count,
                            self.settings.redis_cache_max_size,
                        )
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis 缓存写入失败, 降级无缓存: %s", e)

    async def _rerank(
        self,
        query: str,
        docs: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        """重排序 (bge-reranker-v2-m3).

        AGENTS.md 第 7 章: rerank 默认不启用; rerank_enabled=True 时,
        Top-K 召回后 rerank, 禁止直接用向量分数作最终排序.
        """
        if not docs:
            return []

        # 截取候选集 (避免 rerank 过多)
        candidates = docs[: top_k * 3]
        documents = [c["content"] for c in candidates]

        try:
            response = await self._rerank_client.post(
                "/rerank",
                json={
                    "query": query,
                    "documents": documents,
                    "top_n": top_k,
                },
            )
            response.raise_for_status()
            data = response.json()

            reranked = []
            for item in data:
                idx = item["index"]
                score = item["relevance_score"]
                doc = candidates[idx]
                reranked.append({**doc, "score": float(score)})

            # 低于阈值丢弃 (AGENTS.md 第 7 章: score_threshold 默认 0.3)
            threshold = self.settings.score_threshold
            return [d for d in reranked if d["score"] >= threshold]
        except Exception as e:  # noqa: BLE001
            logger.warning("Rerank 失败, 降级用 RRF 分数: %s", e)
            return docs[:top_k]

    def _get_tokens(self, text: str) -> list[str]:
        """P1-6: 带缓存的 jieba 分词 (key=text sha256).

        重复 query/重复 content 命中缓存, 避免重复 jieba.cut.
        缓存上限 2000 条 (LRU 淘汰最旧).
        """
        import hashlib

        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cached = self._token_cache.get(key)
        if cached is not None:
            return cached
        tokens = list(jieba.cut(text))
        # LRU 淘汰: 超过 2000 条删除最旧
        if len(self._token_cache) >= 2000:
            # dict 在 Python 3.7+ 保持插入顺序, popitem(last=False) 删最旧
            self._token_cache.pop(next(iter(self._token_cache)))
        self._token_cache[key] = tokens
        return tokens

    def update_bm25_corpus(self, docs: list[dict[str, Any]]) -> None:
        """更新 BM25 内存语料.

        P1-6: 复用 _token_cache 避免对同一 content 重复 jieba.cut.
        """
        self._bm25_docs = docs
        self._bm25_corpus = [self._get_tokens(d["content"]) for d in docs]
        self._bm25 = BM25Okapi(self._bm25_corpus) if self._bm25_corpus else None

    async def close(self) -> None:
        """关闭资源.

        P0-5: 不再关闭 Redis 客户端; Redis 改为全局单例 (common.redis_client),
        由 server.py lifespan 统一调用 close_redis_client() 关闭.
        """
        await self._embeddings.close()
        await self._qdrant.close()
        await self._rerank_client.aclose()


# ========== 全局单例 ==========
_retriever: HybridRetriever | None = None


def get_retriever() -> HybridRetriever:
    """获取全局 HybridRetriever 单例."""
    global _retriever
    if _retriever is None:
        _retriever = HybridRetriever()
    return _retriever
