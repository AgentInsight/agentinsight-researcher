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

import hashlib
import json
import logging
import time
from typing import Any, cast

import httpx
import jieba
from rank_bm25 import BM25Okapi

try:  # P2-04: redis 在 requirements.txt, 但运行环境缺失时降级无缓存 (AGENTS.md 降级策略)
    import redis.asyncio as aioredis
except ImportError:  # pragma: no cover
    aioredis = None  # type: ignore[assignment,unused-ignore]

from src.config.settings import Settings, get_settings
from src.observability.tracing import trace_retriever
from src.rag.embeddings import EmbeddingsClient
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
    _bm25_corpus: list[list[str]]  # BM25 语料 (jieba 分词后)
    _bm25_docs: list[dict[str, Any]]  # BM25 原始文档
    _bm25: BM25Okapi | None

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._embeddings = EmbeddingsClient(self.settings)
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
        # P2-04: Redis 缓存客户端 (AGENTS.md 第 7 章: 键格式 {agent_id}:{user_id}:{module}:{type}:{id})
        # Redis 不可用时降级为无缓存, 不阻断检索 (含 redis 库未安装场景)
        self._redis: Any = None
        if aioredis is not None:
            try:
                self._redis = aioredis.from_url(
                    self.settings.redis_url,
                    password=self.settings.redis_auth or None,
                    decode_responses=True,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Redis 客户端初始化失败, 降级无缓存: %s", e)
                self._redis = None
        self._bm25_corpus = []
        self._bm25_docs = []
        self._bm25 = None

    def build_namespaces(self, user_id: str | None = None) -> list[str]:
        """构建检索 namespace 列表 (共享 + 用户私有).

        AGENTS.md 第 7 章: 检索时必须显式传目标 namespace 列表 (共享 + 当前用户私有).
        """
        namespaces = [self._qdrant.build_shared_namespace()]
        if user_id:
            namespaces.append(self._qdrant.build_user_namespace(user_id))
        return namespaces

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
        namespaces = self.build_namespaces(user_id)

        # P2-04: 检查 Redis 缓存 (命中直接返回, 不走 BM25+Vector)
        cache_key = self._cache_key(query, user_id)
        cached = await self._get_cache(cache_key, user_id)
        if cached is not None:
            logger.info("RAG 缓存命中: query=%s", query[:50])
            return cached

        async with trace_retriever(
            name="hybrid-retrieve",
            input={"query": query[:200], "namespaces": namespaces, "top_k": k},
            metadata={"retriever_type": "hybrid"},
            user_id=user_id,
            session_id=session_id,
        ) as span:
            # 并行执行 BM25 + 向量检索
            import asyncio

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
        """
        if not self._bm25 or not self._bm25_docs:
            return []

        query_tokens = list(jieba.cut(query))
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
            h = hashlib.md5(content.encode("utf-8")).hexdigest()
            if h not in seen:
                seen.add(h)
                deduped.append(r)
        return deduped

    def _cache_key(self, query: str, user_id: str | None) -> str:
        """构建 Redis 缓存键 (AGENTS.md 第 7 章: {agent_id}:{user_id}:{module}:{type}:{id})."""
        agent_id = self.settings.agent_name
        uid = user_id or self.settings.default_user_id
        query_hash = hashlib.md5(query.encode("utf-8")).hexdigest()
        return f"{agent_id}:{uid}:rag:retriever:{query_hash}"

    def _lru_key(self, user_id: str | None = None) -> str:
        """构建 LRU 访问时间 Sorted Set 键 (AGENTS.md 第 7 章: {agent_id}:{user_id}:cache_access_times)."""
        agent_id = self.settings.agent_name
        uid = user_id or self.settings.default_user_id
        return f"{agent_id}:{uid}:cache_access_times"

    async def _get_cache(
        self,
        key: str,
        user_id: str | None = None,
    ) -> list[dict[str, Any]] | None:
        """读取 Redis 缓存 (TTL + LRU 双策略, P1-03).

        命中时更新访问时间 (LRU 排序). Redis 不可用时降级返回 None, 不阻断检索.
        """
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
            return cast(list[dict[str, Any]], json.loads(data))
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
        if self._redis is None:
            return
        try:
            await self._redis.set(
                key,
                json.dumps(results, ensure_ascii=False, default=str),
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

    def update_bm25_corpus(self, docs: list[dict[str, Any]]) -> None:
        """更新 BM25 内存语料."""
        self._bm25_docs = docs
        self._bm25_corpus = [list(jieba.cut(d["content"])) for d in docs]
        self._bm25 = BM25Okapi(self._bm25_corpus) if self._bm25_corpus else None

    async def close(self) -> None:
        """关闭资源."""
        await self._embeddings.close()
        await self._qdrant.close()
        await self._rerank_client.aclose()
        if self._redis is not None:
            await self._redis.aclose()


# ========== 全局单例 ==========
_retriever: HybridRetriever | None = None


def get_retriever() -> HybridRetriever:
    """获取全局 HybridRetriever 单例."""
    global _retriever
    if _retriever is None:
        _retriever = HybridRetriever()
    return _retriever
