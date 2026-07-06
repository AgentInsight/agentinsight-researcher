"""Embeddings 封装.

AGENTS.md 第 7 章硬约束:
- Embeddings: bge-large-zh-v1.5 (中文最强开源嵌入, 本地零成本)
- Embedding 调用统一走 rag/embeddings.py, 禁止业务代码直连 API
- Qdrant 单集合 agents, payload namespace 隔离:
  - 共享知识库: namespace = agent_id
  - 用户私有数据: namespace = {agent_id}:{user_id}

对标 AgentInsightService common/embeddings.py.
所有调用必须包裹在 trace_embedding span 内 (AGENTS.md 第 10 章, head-based 采样).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from collections import OrderedDict
from typing import Any, cast

import httpx

from src.config.settings import Settings, get_settings
from src.observability.tracing import trace_embedding

logger = logging.getLogger(__name__)

# uuid5 命名空间 (AGENTS.md 第 7 章: 点 id 用 uuid5(NAMESPACE_DNS, ...))
NAMESPACE_DNS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

# ========== 进程内 Embedding 缓存 (P1-3, LRU + TTL) ==========
_EMBED_CACHE: OrderedDict[str, dict[str, Any]] = OrderedDict()
_EMBED_CACHE_MAX_SIZE: int = 1000  # 最大缓存条目
_EMBED_CACHE_TTL: int = 3600  # 1 小时 TTL (秒)


def _cache_key(texts: list[str]) -> str:
    """生成缓存键 (基于所有文本的 sha256)."""
    combined = "\n".join(texts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> list[list[float]] | None:
    """从缓存获取 (带 TTL 检查)."""
    if key in _EMBED_CACHE:
        entry = _EMBED_CACHE[key]
        if time.time() - entry["ts"] < _EMBED_CACHE_TTL:
            _EMBED_CACHE.move_to_end(key)
            return cast(list[list[float]], entry["vectors"])
        del _EMBED_CACHE[key]
    return None


def _cache_set(key: str, vectors: list[list[float]]) -> None:
    """写入缓存 (LRU 淘汰)."""
    _EMBED_CACHE[key] = {"vectors": vectors, "ts": time.time()}
    _EMBED_CACHE.move_to_end(key)
    while len(_EMBED_CACHE) > _EMBED_CACHE_MAX_SIZE:
        _EMBED_CACHE.popitem(last=False)


class EmbeddingsClient:
    """Embeddings 客户端, 调用远程 TEI 服务 (bge-large-zh-v1.5).

    AGENTS.md 第 1/7 章: bge-large-zh-v1.5 固定 1024 维, 远程 TEI 服务.
    """

    settings: Settings
    _client: httpx.AsyncClient

    # 预热文本 (P0-03: 触发 TEI 模型加载, 避免首次调用冷启动)
    _WARMUP_TEXTS: list[str] = ["测试", "test", "研究报告", "research report", "短查询"]

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        # TEI API_KEY 鉴权 (AGENTS.md 第 7/12 章): 服务端开启 API_KEY 时,
        # 客户端必须携带 Authorization: Bearer <key> 请求头
        headers: dict[str, str] = {}
        if self.settings.embeddings_api_key:
            headers["Authorization"] = f"Bearer {self.settings.embeddings_api_key}"
        self._client = httpx.AsyncClient(
            base_url=self.settings.embeddings_base_url,
            timeout=httpx.Timeout(
                connect=5.0,
                # P1-04: 读超时 120s, 应对大 batch 慢推理
                # TEI CPU 后端 inference_time 可达 10s+, queue_time 可达 30s+
                # 60s 会踩边界触发 ReadTimeout
                read=120.0,
                write=10.0,
                pool=5.0,
            ),
            headers=headers,
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=30.0,
            ),
        )
        # P1-04: 客户端并发限流 (避免高并发击穿 TEI 限流阈值导致 429)
        self._semaphore = asyncio.Semaphore(self.settings.embeddings_max_concurrent)

    async def embed_texts(
        self,
        texts: list[str],
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[list[float]]:
        """批量嵌入文本.

        返回与 texts 等长的向量列表, 每条 1024 维.
        高频调用, head-based 采样降存储压力.

        P1-1: 客户端按 embeddings_max_client_batch_size 分批, asyncio.gather 并发.
        P1-3: 进程内 LRU+TTL 缓存, 命中直接返回.
        """
        if not texts:
            return []

        # P1-3: 缓存命中检查 (分批之前, 整批命中直接返回)
        key = _cache_key(texts)
        cached = _cache_get(key)
        if cached is not None:
            logger.debug("Embedding 缓存命中: text_count=%d", len(texts))
            return cached

        # P1-1: 客户端分批 (避免单次请求超过 TEI 上限)
        batch_size = getattr(self.settings, "embeddings_max_client_batch_size", 32) or 32
        vectors: list[list[float]]
        if len(texts) <= batch_size:
            vectors = await self._embed_texts_single(texts, user_id=user_id, session_id=session_id)
        else:
            # 分批并发
            batches = [texts[i : i + batch_size] for i in range(0, len(texts), batch_size)]
            tasks = [
                self._embed_texts_single(batch, user_id=user_id, session_id=session_id)
                for batch in batches
            ]
            results = await asyncio.gather(*tasks)
            # 拍平结果
            vectors = []
            for batch_vectors in results:
                vectors.extend(batch_vectors)

        # P1-3: 写入缓存
        _cache_set(key, vectors)
        return vectors

    async def _embed_texts_single(
        self,
        texts: list[str],
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[list[float]]:
        """单次 TEI 调用 (原 embed_texts 逻辑, 不分批).

        供 embed_texts 内部分批并发调用, 每批一次 TEI /embed 请求.
        P1-04: Semaphore 限流 + 429 指数退避重试.
        """
        if not texts:
            return []

        async with trace_embedding(
            name="embed-texts",
            input={"text_count": len(texts), "total_chars": sum(len(t) for t in texts)},
            model=self.settings.embeddings_model,
            user_id=user_id,
            session_id=session_id,
        ) as span:
            # P1-04: Semaphore 限流 (避免高并发击穿 TEI 限流阈值导致 429)
            max_retries = self.settings.embeddings_max_retries
            base_delay = self.settings.embeddings_retry_base_delay
            last_error: Exception | None = None

            for attempt in range(max_retries + 1):
                try:
                    async with self._semaphore:
                        # TEI 服务 /embed 接口
                        response = await self._client.post(
                            "/embed",
                            json={"inputs": texts},
                        )
                        response.raise_for_status()
                        vectors = response.json()

                        # 估算 token 数 (粗略: 字符数 / 3)
                        total_chars = sum(len(t) for t in texts)
                        token_count = total_chars // 3

                        span.update(
                            output={"vector_count": len(vectors)},
                            usage_details={"total_tokens": token_count},
                        )
                        return cast(list[list[float]], vectors)

                except httpx.HTTPStatusError as e:
                    last_error = e
                    # P1-04: 429 Too Many Requests → 指数退避重试
                    if e.response.status_code == 429 and attempt < max_retries:
                        delay = base_delay * (2**attempt)
                        logger.warning(
                            "Embedding 429 限流, 第 %d/%d 次重试 (延迟 %.2fs): text_count=%d",
                            attempt + 1,
                            max_retries,
                            delay,
                            len(texts),
                        )
                        await asyncio.sleep(delay)
                        continue
                    # 非 429 或重试次数用尽, 抛出
                    logger.error(
                        "Embedding 调用失败 (HTTP %d): %s",
                        e.response.status_code,
                        e,
                    )
                    span.update(
                        metadata={
                            "error": f"HTTPStatusError {e.response.status_code}: {e}",
                            "retries": attempt,
                        }
                    )
                    raise

                except Exception as e:  # noqa: BLE001
                    last_error = e
                    logger.error(
                        "Embedding 调用失败: type=%s repr=%r str=%s",
                        type(e).__name__,
                        e,
                        e,
                    )
                    span.update(
                        metadata={
                            "error": f"{type(e).__name__}: {e}",
                            "retries": attempt,
                        }
                    )
                    raise

            # 理论上不会到达 (重试循环要么 return 要么 raise)
            raise last_error  # type: ignore[misc]

    async def embed_query(
        self,
        text: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[float]:
        """嵌入单条查询文本."""
        vectors = await self.embed_texts([text], user_id=user_id, session_id=session_id)
        return vectors[0] if vectors else []

    async def embed_and_index(
        self,
        texts: list[str],
        *,
        namespace: str,
        metadata_list: list[dict[str, Any]] | None = None,
        batch_size: int = 32,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> int:
        """批量嵌入并索引到 Qdrant (embed + upsert 一体化, P0-02).

        AGENTS.md 第 7 章:
        - namespace = agent_id (共享) 或 {agent_id}:{user_id} (私有)
        - 点 id 用 uuid5(NAMESPACE_DNS, f"{namespace}:{content_hash}") 幂等
        - payload 含 content + metadata + namespace (用户私有额外含 user_id)

        Args:
            texts: 待索引文本列表.
            namespace: Qdrant payload namespace.
            metadata_list: 每条文本的 metadata (可选, 长度须与 texts 一致).
            batch_size: 内部分批大小 (减少 TEI HTTP 请求次数, 默认 32).
            user_id: 用户 ID (隔离键, 私有数据需传).
            session_id: 会话 ID (trace 用).

        Returns:
            成功索引的点数.

        Raises:
            ValueError: metadata_list 长度与 texts 不一致.
        """
        if not texts:
            return 0

        if metadata_list is not None and len(metadata_list) != len(texts):
            raise ValueError(
                f"metadata_list 长度 {len(metadata_list)} 与 texts 长度 {len(texts)} 不一致"
            )

        # 延迟导入避免循环依赖
        from src.rag.qdrant_manager import get_qdrant_manager

        qdrant = get_qdrant_manager()
        total_indexed = 0

        # 分批处理 (避免单次 TEI 请求过大)
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i : i + batch_size]
            batch_meta = (
                metadata_list[i : i + batch_size] if metadata_list else [None] * len(batch_texts)
            )

            # 构造 points 字典列表 (复用 QdrantManager.upsert_points 的入参格式)
            points = [
                {
                    "content": text,
                    "metadata": meta or {"source": "batch_index", "batch_index": i + j},
                }
                for j, (text, meta) in enumerate(zip(batch_texts, batch_meta, strict=True))
            ]

            await qdrant.upsert_points(
                namespace=namespace,
                points=points,
                user_id=user_id,
            )
            total_indexed += len(points)
            logger.debug(
                "批量索引批次 %d-%d 完成 (namespace=%s, +%d 点, 累计 %d)",
                i,
                i + len(batch_texts),
                namespace,
                len(points),
                total_indexed,
            )

        logger.info(
            "批量索引完成 (namespace=%s, user_id=%s, 总计 %d 点, 分批 %d)",
            namespace,
            user_id,
            total_indexed,
            (len(texts) + batch_size - 1) // batch_size,
        )
        return total_indexed

    async def warmup(self) -> None:
        """预热 Embeddings 服务 (P0-03).

        用一组标准文本触发 TEI 模型加载, 避免首次真实调用冷启动.
        预热结果丢弃, 失败不阻断启动.
        """
        try:
            await self.embed_texts(self._WARMUP_TEXTS)
            logger.info("Embeddings 服务预热完成 (%d texts)", len(self._WARMUP_TEXTS))
        except Exception as e:  # noqa: BLE001
            logger.warning("Embeddings 预热失败 (不阻断启动): %s", e)

    @staticmethod
    def generate_point_id(namespace: str, content: str) -> str:
        """幂等生成 Qdrant 点 id (AGENTS.md 第 7 章).

        uuid5(NAMESPACE_DNS, f"{namespace}:{content_hash}")
        """
        import hashlib

        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return str(uuid.uuid5(NAMESPACE_DNS, f"{namespace}:{content_hash}"))

    async def close(self) -> None:
        """关闭 HTTP 客户端."""
        await self._client.aclose()


# ========== 全局单例 ==========
_client: EmbeddingsClient | None = None


def get_embeddings_client() -> EmbeddingsClient:
    """获取全局 EmbeddingsClient 单例."""
    global _client
    if _client is None:
        _client = EmbeddingsClient()
    return _client


async def warmup_embeddings() -> None:
    """预热 Embeddings 服务 (P0-03).

    供 server.py lifespan 调用, 触发 TEI 模型加载避免首次调用冷启动.
    """
    client = get_embeddings_client()
    await client.warmup()
