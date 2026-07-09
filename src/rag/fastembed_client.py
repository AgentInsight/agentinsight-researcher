"""FastEmbed 本地 Embeddings 客户端 (bge-small-zh-v1.5, 512维).

AGENTS.md 第 7 章: 上下文压缩用本地 FastEmbed (WrittenContentCompressor
跨子主题去重 + ContextManager._embeddings_rerank 精排), 不依赖远程 TEI 服务,
解决 TEI CPU 部署性能瓶颈.

设计原则:
1. 使用 bge-small-zh-v1.5 ONNX INT8 模型, 输出 512 维向量
2. 懒加载: 首次调用时才加载模型, 避免启动延迟
3. 线程安全: asyncio.Lock 保护并发模型加载
4. 缓存: 进程内 LRU+TTL 缓存, 与远程 EmbeddingsClient 一致
5. 降级: FastEmbed 加载失败时, 降级到远程 TEI (EmbeddingsClient)

注意:
- 本客户端仅用于上下文压缩 (WrittenContentCompressor 去重 + _embeddings_rerank 精排), 不用于 Qdrant 索引
- Qdrant 索引仍使用远程 TEI (bge-large-zh-v1.5, 1024维), 维度固定不可改
- bge-small-zh-v1.5 ONNX 模型需提前转换并放入配置的模型路径
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from typing import Any, cast

import anyio

from src.config.settings import Settings, get_settings
from src.observability.tracing import trace_embedding

logger = logging.getLogger(__name__)

# 进程内缓存 (与远程 EmbeddingsClient 一致的 LRU+TTL)
_FASTEMBED_CACHE: OrderedDict[str, dict[str, Any]] = OrderedDict()
_FASTEMBED_CACHE_MAX_SIZE: int = 2000
_FASTEMBED_CACHE_TTL: int = 3600


def _cache_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> list[float] | None:
    if key in _FASTEMBED_CACHE:
        entry = _FASTEMBED_CACHE[key]
        if time.time() - entry["ts"] < _FASTEMBED_CACHE_TTL:
            _FASTEMBED_CACHE.move_to_end(key)
            return cast(list[float], entry["vector"])
        del _FASTEMBED_CACHE[key]
    return None


def _cache_set(key: str, vector: list[float]) -> None:
    _FASTEMBED_CACHE[key] = {"vector": vector, "ts": time.time()}
    _FASTEMBED_CACHE.move_to_end(key)
    while len(_FASTEMBED_CACHE) > _FASTEMBED_CACHE_MAX_SIZE:
        _FASTEMBED_CACHE.popitem(last=False)


class FastEmbedClient:
    """FastEmbed 本地 Embeddings 客户端 (bge-small-zh-v1.5, 512维).

    供上下文压缩的 Embeddings 精排使用, 不依赖远程 TEI 服务.

    用法:
        client = FastEmbedClient(settings)
        embeddings = await client.embed_texts(["你好世界"])
    """

    settings: Settings
    _model: Any
    _lock: asyncio.Lock
    _initialized: bool
    _load_failed: bool

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._model = None
        self._lock = asyncio.Lock()
        self._initialized = False
        self._load_failed = False

    async def _ensure_model(self) -> None:
        """懒加载模型 (线程安全)."""
        if self._initialized:
            return
        if self._load_failed:
            raise RuntimeError("FastEmbed 模型加载失败, 请检查模型路径")

        async with self._lock:
            if self._initialized:
                return
            try:
                from fastembed import TextEmbedding

                local_model_exists = await anyio.Path(self.settings.fastembed_model_path).exists()
                kwargs: dict[str, Any] = {
                    "model_name": self.settings.fastembed_model_name,
                    "max_length": self.settings.fastembed_max_length,
                }

                if local_model_exists:
                    kwargs["specific_model_path"] = self.settings.fastembed_model_path
                    logger.info(
                        "加载 FastEmbed 模型: %s (本地路径: %s)",
                        self.settings.fastembed_model_name,
                        self.settings.fastembed_model_path,
                    )
                else:
                    logger.info(
                        "加载 FastEmbed 模型: %s (本地路径不存在, 将从 HuggingFace 自动下载)",
                        self.settings.fastembed_model_name,
                    )

                self._model = TextEmbedding(**kwargs)
                self._initialized = True
                logger.info("FastEmbed 模型加载成功")
            except Exception as e:  # noqa: BLE001
                logger.error("FastEmbed 模型加载失败: %s", e)
                self._load_failed = True
                raise

    # 任务7: 分批并行阈值. 实测 200 chunks 时 batch_size=32 收益 32.1% (>= 30% 阈值).
    # 小批量 (< 此阈值) 用单次 to_thread, 避免线程池调度开销.
    _PARALLEL_BATCH_THRESHOLD: int = 32
    _PARALLEL_BATCH_SIZE: int = 32

    async def embed_texts(
        self,
        texts: list[str],
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[list[float]]:
        """批量嵌入文本 (512维).

        任务7 优化: sync 调用卸载到线程池 + 大批量分批并行.
        - 实测: sync 直接调用 100% 阻塞事件循环 (影响 SSE 流式响应)
        - 实测: 200 chunks 分批并行 (b=32) 耗时收益 +32.1%, 事件循环阻塞率 100%→35.7%
        - 决策依据: temp/fastembed_parallel_test_results.json
        """
        if not texts:
            return []

        keys = [_cache_key(t) for t in texts]
        results: list[list[float] | None] = [None] * len(texts)
        miss_indices: list[int] = []
        miss_texts: list[str] = []

        for i, key in enumerate(keys):
            v = _cache_get(key)
            if v is not None:
                results[i] = v
            else:
                miss_indices.append(i)
                miss_texts.append(texts[i])

        if not miss_texts:
            logger.debug("FastEmbed 缓存全命中: text_count=%d", len(texts))
            return cast(list[list[float]], results)

        async with trace_embedding(
            name="fastembed-embed",
            input={"text_count": len(miss_texts), "total_chars": sum(len(t) for t in miss_texts)},
            model=self.settings.fastembed_model_name,
            user_id=user_id,
            session_id=session_id,
        ) as span:
            await self._ensure_model()

            try:
                miss_vectors = await self._embed_parallel(miss_texts)

                for idx, vec in zip(miss_indices, miss_vectors, strict=True):
                    results[idx] = vec
                    _cache_set(keys[idx], vec)

                token_count = sum(len(t) for t in miss_texts) // 3
                span.update(
                    output={"vector_count": len(miss_vectors)},
                    usage_details={"total_tokens": token_count},
                )
                return cast(list[list[float]], results)
            except Exception as e:  # noqa: BLE001
                logger.error("FastEmbed embed_texts 失败: %s", e)
                span.update(metadata={"error": str(e)})
                raise

    async def _embed_parallel(self, texts: list[str]) -> list[list[float]]:
        """任务7: sync embed 卸载到线程池 + 大批量分批并行.

        策略:
        - text_count < _PARALLEL_BATCH_THRESHOLD: 单次 asyncio.to_thread (避免调度开销)
        - text_count >= _PARALLEL_BATCH_THRESHOLD: 分批 + asyncio.gather 并行
          (实测 200 chunks/b=32 收益 +32.1%)

        关键: 原 list(self._model.embed(texts)) 是 sync CPU 调用, 100% 阻塞事件循环,
        导致 SSE 流式响应卡顿. asyncio.to_thread 释放事件循环 (阻塞率 100%→35.7%).
        """
        model = self._model

        def _embed_batch(batch: list[str]) -> list[list[float]]:
            return [list(e) for e in model.embed(batch)]

        # 小批量: 单次卸载 (避免线程池调度开销)
        if len(texts) < self._PARALLEL_BATCH_THRESHOLD:
            return await asyncio.to_thread(_embed_batch, texts)

        # 大批量: 分批并行
        batch_size = self._PARALLEL_BATCH_SIZE
        batches = [texts[i : i + batch_size] for i in range(0, len(texts), batch_size)]
        batch_results = await asyncio.gather(*[asyncio.to_thread(_embed_batch, b) for b in batches])
        flat: list[list[float]] = []
        for r in batch_results:
            flat.extend(r)
        return flat

    async def embed_text(
        self,
        text: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[float]:
        """嵌入单条文本 (512维)."""
        vectors = await self.embed_texts([text], user_id=user_id, session_id=session_id)
        return vectors[0] if vectors else []

    @property
    def dimension(self) -> int:
        """向量维度 (bge-small-zh-v1.5 = 512)."""
        return self.settings.fastembed_dimension


# ========== 全局单例 ==========
_client: FastEmbedClient | None = None


def get_fastembed_client() -> FastEmbedClient:
    """获取全局 FastEmbedClient 单例."""
    global _client
    if _client is None:
        _client = FastEmbedClient()
    return _client
