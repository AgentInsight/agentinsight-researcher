"""Embeddings 封装.

AGENTS.md 第 7 章硬约束:
- Embeddings: bge-base-zh-v1.5 (中文最强开源嵌入, 本地零成本)
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
# P0-1 修复: 改为单文本级缓存 (key=单条文本 sha256), 提升命中率
# 旧版整批 sha256 在 query 变化时 100% miss, 单文本缓存可跨 query 复用 chunks 向量
_EMBED_CACHE: OrderedDict[str, dict[str, Any]] = OrderedDict()
_EMBED_CACHE_MAX_SIZE: int = 2000  # 最大缓存条目 (单文本级, 提升容量)
_EMBED_CACHE_TTL: int = 3600  # 1 小时 TTL (秒)


def _cache_key_single(text: str) -> str:
    """P0-1 修复: 生成单文本缓存键 (基于单条文本 sha256).

    相比旧版整批 sha256, 单文本 key 在 query 变化但 chunks 相同时可命中,
    WrittenContentCompressor 已写入 chunks 的 embedding 也可被复用.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cache_get_single(key: str) -> list[float] | None:
    """从缓存获取单条向量 (带 TTL 检查)."""
    if key in _EMBED_CACHE:
        entry = _EMBED_CACHE[key]
        if time.time() - entry["ts"] < _EMBED_CACHE_TTL:
            _EMBED_CACHE.move_to_end(key)
            return cast(list[float], entry["vector"])
        del _EMBED_CACHE[key]
    return None


def _cache_set_single(key: str, vector: list[float]) -> None:
    """写入单条向量缓存 (LRU 淘汰)."""
    _EMBED_CACHE[key] = {"vector": vector, "ts": time.time()}
    _EMBED_CACHE.move_to_end(key)
    while len(_EMBED_CACHE) > _EMBED_CACHE_MAX_SIZE:
        _EMBED_CACHE.popitem(last=False)


# 保留旧版批量接口兼容 (标记 deprecated, 内部转发到单文本逻辑)
def _cache_key(texts: list[str]) -> str:
    """[deprecated] 旧版整批缓存键, 仅供向后兼容."""
    combined = "\n".join(texts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


# ========== P0-1: TEI 熔断器 ==========
# 当 TEI 服务连续失败 N 次时短路, 避免雪崩; 半开状态试探恢复.
# 熔断器配置 (常量, 因 settings.py 由其他任务负责, 此处不引入新字段):
# - failure_threshold: 连续失败次数阈值 (5 次)
# - recovery_timeout: 熔断后恢复探测时间 (60s)
# 注: 熔断器为进程级单例, 全局共享 TEI 健康状态.
_CIRCUIT_FAILURE_THRESHOLD: int = 5
_CIRCUIT_RECOVERY_TIMEOUT: float = 60.0


class EmbeddingsCircuitBreaker:
    """TEI Embeddings 熔断器 (P0-1).

    三态:
    - CLOSED (正常): 失败计数 < threshold, 请求正常通过
    - OPEN (熔断): 失败计数 ≥ threshold 且未过恢复时间, 直接抛 EmbeddingsCircuitOpenError
    - HALF_OPEN (半开): 失败计数 ≥ threshold 但已过恢复时间, 允许单次试探请求

    线程安全: 仅在 asyncio 单线程事件循环中使用, 无需加锁.
    对标 GPTR 无 (GPTR 用 SaaS embedding, 不需要熔断器); 主项目因 TEI 自部署必须补.
    """

    def __init__(
        self,
        failure_threshold: int = _CIRCUIT_FAILURE_THRESHOLD,
        recovery_timeout: float = _CIRCUIT_RECOVERY_TIMEOUT,
    ) -> None:
        self._failure_count: int = 0
        self._failure_threshold: int = failure_threshold
        self._recovery_timeout: float = recovery_timeout
        self._last_failure_time: float = 0.0
        self._open: bool = False

    def is_open(self) -> bool:
        """检查熔断器是否开启 (OPEN 状态).

        若已过恢复时间, 自动切换到 HALF_OPEN (返回 False, 允许试探请求).
        """
        if self._open and self._failure_count >= self._failure_threshold:
            if time.time() - self._last_failure_time > self._recovery_timeout:
                # 半开状态: 允许试探, 不立即清零 (试探成功才清零)
                logger.info(
                    "TEI 熔断器进入半开状态, 允许试探请求 (failure_count=%d, recovery=%.1fs)",
                    self._failure_count,
                    self._recovery_timeout,
                )
                self._open = False
                return False
            return True
        return self._open

    def record_success(self) -> None:
        """记录成功调用 (半开试探成功或正常路径成功).

        成功时清零失败计数, 关闭熔断器.
        """
        if self._failure_count > 0 or self._open:
            logger.info(
                "TEI 熔断器关闭 (成功调用恢复, failure_count %d → 0)",
                self._failure_count,
            )
        self._failure_count = 0
        self._open = False

    def record_failure(self) -> None:
        """记录失败调用, 失败计数 +1, 达阈值则开启熔断."""
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= self._failure_threshold and not self._open:
            self._open = True
            logger.warning(
                "TEI 熔断器开启 (连续失败 %d 次 ≥ threshold %d, 熔断 %.1fs)",
                self._failure_count,
                self._failure_threshold,
                self._recovery_timeout,
            )


class EmbeddingsCircuitOpenError(RuntimeError):
    """TEI 熔断器开启异常 (P0-1).

    熔断器 OPEN 状态时, embed_texts 调用直接抛此异常,
    供 context_manager 等调用方识别并降级.
    """

    def __init__(self, message: str = "TEI 熔断器开启, 跳过 embedding 调用") -> None:
        super().__init__(message)


class EmbeddingsClient:
    """Embeddings 客户端, 调用远程 TEI 服务 (bge-base-zh-v1.5).

    AGENTS.md 第 1/7 章: bge-base-zh-v1.5 固定 768 维, 远程 TEI 服务.

    P0-1: 内置 EmbeddingsCircuitBreaker 熔断器, TEI 连续失败 N 次后短路,
    避免雪崩; 调用方可通过 is_circuit_open() 检查状态做降级 (如 context_manager).
    """

    settings: Settings
    _client: httpx.AsyncClient
    _circuit_breaker: EmbeddingsCircuitBreaker

    # 预热文本 (P0-03: 触发 TEI 模型加载, 避免首次调用冷启动)
    _WARMUP_TEXTS: list[str] = ["测试", "test", "研究报告", "research report", "短查询"]

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        # TEI API_KEY 鉴权 (AGENTS.md 第 7/12 章): 服务端开启 API_KEY 时,
        # 客户端必须携带 Authorization: Bearer <key> 请求头
        headers: dict[str, str] = {}
        if self.settings.embeddings_api_key:
            headers["Authorization"] = f"Bearer {self.settings.embeddings_api_key}"
        # P0-1 修复: 客户端配置优化
        # - read timeout 60s→90s: batch_size=4 后请求数增多, TEI 内部排队 30-45s + 推理 11-17s = 总 45-60s, 90s 留余量
        # - max_connections 100→20: 配合 Semaphore(3) 实际并发, 20 已足够 (避免文件描述符膨胀)
        # - max_keepalive_connections 20→10: 与 max_connections 比例合理
        self._client = httpx.AsyncClient(
            base_url=self.settings.embeddings_base_url,
            timeout=httpx.Timeout(
                connect=5.0,
                read=90.0,
                write=10.0,
                pool=10.0,
            ),
            headers=headers,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30.0,
            ),
        )
        # P1-04: 客户端并发限流 (避免高并发击穿 TEI 限流阈值导致 429)
        # P0-1 修复: 走 settings, 默认 3 (匹配 TEI permits=4, 留 1 余量)
        self._semaphore = asyncio.Semaphore(self.settings.embeddings_max_concurrent)
        # P0-1: TEI 熔断器 (进程级单例, 全局共享 TEI 健康状态)
        self._circuit_breaker = EmbeddingsCircuitBreaker()

    def is_circuit_open(self) -> bool:
        """检查 TEI 熔断器是否开启 (P0-1).

        供 context_manager 等调用方识别 TEI 不可用状态,
        在熔断期间走关键词匹配等降级路径, 避免等待 90s timeout.
        """
        return self._circuit_breaker.is_open()

    async def embed_texts(
        self,
        texts: list[str],
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[list[float]]:
        """批量嵌入文本.

        返回与 texts 等长的向量列表, 每条 768 维.
        高频调用, head-based 采样降存储压力.

        P1-1: 客户端按 embeddings_max_client_batch_size 分批, asyncio.gather 并发.
        P1-3: 进程内 LRU+TTL 缓存, 命中直接返回.
        P0-1 修复: 单文本级缓存 (替代旧版整批 sha256), 大幅提升命中率.
        """
        if not texts:
            return []

        # P0-1: 熔断器开启时快速失败, 调用方据此降级 (避免等待 90s timeout)
        if self._circuit_breaker.is_open():
            raise EmbeddingsCircuitOpenError()

        # P0-1 修复: 单文本级缓存查询
        # 1. 逐条查缓存, 收集未命中部分
        keys = [_cache_key_single(t) for t in texts]
        results: list[list[float] | None] = [None] * len(texts)
        miss_indices: list[int] = []
        miss_texts: list[str] = []
        for i, key in enumerate(keys):
            v = _cache_get_single(key)
            if v is not None:
                results[i] = v
            else:
                miss_indices.append(i)
                miss_texts.append(texts[i])

        # 2. 全部命中, 直接返回
        if not miss_texts:
            logger.debug("Embedding 缓存全命中: text_count=%d", len(texts))
            return cast(list[list[float]], results)

        # 3. 未命中部分批量调 TEI (按 batch_size 分批 + gather 并发)
        batch_size = self.settings.embeddings_max_client_batch_size
        miss_vectors: list[list[float]] = []
        if len(miss_texts) <= batch_size:
            miss_vectors = await self._embed_texts_single(
                miss_texts, user_id=user_id, session_id=session_id
            )
        else:
            batches = [
                miss_texts[i : i + batch_size] for i in range(0, len(miss_texts), batch_size)
            ]
            tasks = [
                self._embed_texts_single(batch, user_id=user_id, session_id=session_id)
                for batch in batches
            ]
            batch_results = await asyncio.gather(*tasks)
            for batch_vectors in batch_results:
                miss_vectors.extend(batch_vectors)

        # 4. 回填缓存 + 结果
        for idx, vec in zip(miss_indices, miss_vectors, strict=True):
            results[idx] = vec
            _cache_set_single(keys[idx], vec)

        return cast(list[list[float]], results)

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
        P0-1: 集成熔断器 — 仅在实际 HTTP 请求时持有 semaphore (重试时释放, 避免并发槽耗尽);
              成功记录 record_success, 失败记录 record_failure.
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
            max_retries = self.settings.embeddings_max_retries
            base_delay = self.settings.embeddings_retry_base_delay
            last_error: Exception | None = None

            for attempt in range(max_retries + 1):
                # P0-1: 熔断器检查 (重试前也检查, 避免熔断期间继续重试)
                if self._circuit_breaker.is_open():
                    span.update(
                        metadata={
                            "error": "circuit_open",
                            "retries": attempt,
                        }
                    )
                    raise EmbeddingsCircuitOpenError()

                try:
                    # P0-1: 仅在实际请求时持有 semaphore (重试期间释放, 避免并发槽耗尽)
                    # 429 限流时退避 sleep 不在 semaphore 持有期间, 不阻塞其他批次
                    async with self._semaphore:
                        # TEI 服务 /embed 接口
                        response = await self._client.post(
                            "/embed",
                            json={"inputs": texts},
                        )
                        response.raise_for_status()
                        vectors = response.json()

                    # 成功: 重置熔断器 (含半开试探成功)
                    self._circuit_breaker.record_success()

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
                    status = e.response.status_code
                    # P0-1: 记录失败 (429/5xx 视为 TEI 服务端问题)
                    self._circuit_breaker.record_failure()
                    # P0-1 修复: 429 限流 + 5xx 服务端错误 → 指数退避重试
                    should_retry = (status == 429 or 500 <= status < 600) and attempt < max_retries
                    if should_retry:
                        delay = base_delay * (2**attempt)
                        logger.warning(
                            "Embedding HTTP %d, 第 %d/%d 次重试 (延迟 %.2fs): text_count=%d",
                            status,
                            attempt + 1,
                            max_retries,
                            delay,
                            len(texts),
                        )
                        # P0-1: semaphore 已释放 (async with 已退出), sleep 不占并发槽
                        await asyncio.sleep(delay)
                        continue
                    # 非重试状态码或重试次数用尽, 抛出
                    logger.error(
                        "Embedding 调用失败 (HTTP %d): %s",
                        status,
                        e,
                    )
                    span.update(
                        metadata={
                            "error": f"HTTPStatusError {status}: {e}",
                            "retries": attempt,
                        }
                    )
                    raise

                except (
                    httpx.ConnectError,
                    httpx.ReadTimeout,
                    httpx.PoolTimeout,
                    httpx.RemoteProtocolError,
                ) as e:
                    # P0-1: 网络错误记录失败 (TEI 重启/过载/网络抖动)
                    last_error = e
                    self._circuit_breaker.record_failure()
                    if attempt < max_retries:
                        delay = base_delay * (2**attempt)
                        logger.warning(
                            "Embedding 网络错误 %s, 第 %d/%d 次重试 (延迟 %.2fs): text_count=%d",
                            type(e).__name__,
                            attempt + 1,
                            max_retries,
                            delay,
                            len(texts),
                        )
                        # P0-1: semaphore 已释放, sleep 不占并发槽
                        await asyncio.sleep(delay)
                        continue
                    logger.error(
                        "Embedding 网络错误重试耗尽: type=%s repr=%r",
                        type(e).__name__,
                        e,
                    )
                    span.update(
                        metadata={
                            "error": f"{type(e).__name__}: {e}",
                            "retries": attempt,
                        }
                    )
                    raise

                except Exception as e:  # noqa: BLE001
                    last_error = e
                    # 未知异常也记录失败 (保守策略, 宁可误熔断也不放过系统性故障)
                    self._circuit_breaker.record_failure()
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
        batch_size: int | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> int:
        """批量嵌入并索引到 Qdrant (embed + upsert 一体化, P0-02).

        AGENTS.md 第 7 章:
        - namespace = agent_id (共享) 或 {agent_id}:{user_id} (私有)
        - 点 id 用 uuid5(NAMESPACE_DNS, f"{namespace}:{content_hash}") 幂等
        - payload 含 content + metadata + namespace (用户私有额外含 user_id)

        3.5.1 死代码修复: 本方法实现完整可用, 供 routes.py 的 upload_file 端点
        (或异步后台任务) 调用, 将用户上传的文档内容索引到 Qdrant 私有 namespace,
        使 RAG 检索可召回. 典型调用方式:

            from src.skills.researcher.document_loader import get_document_loader
            from src.rag.embeddings import get_embeddings_client

            loader = get_document_loader(file_path, settings)
            docs = await loader.load(file_path)
            texts = [d.content for d in docs]
            await get_embeddings_client().embed_and_index(
                texts,
                namespace=f"{agent_id}:{user_id}",
                user_id=user_id,
            )

        P0-修复3: 入口显式 ensure_collection, 避免首批 upsert 抛 404;
                 batch_size 统一走 settings.embeddings_max_client_batch_size.
        P0-1: 熔断器开启时 fast-fail (EmbeddingsCircuitOpenError), 调用方应捕获降级.

        Args:
            texts: 待索引文本列表.
            namespace: Qdrant payload namespace.
            metadata_list: 每条文本的 metadata (可选, 长度须与 texts 一致).
            batch_size: 内部分批大小 (None 时走 settings.embeddings_max_client_batch_size).
            user_id: 用户 ID (隔离键, 私有数据需传).
            session_id: 会话 ID (trace 用).

        Returns:
            成功索引的点数.

        Raises:
            ValueError: metadata_list 长度与 texts 不一致.
            EmbeddingsCircuitOpenError: TEI 熔断器开启时 fast-fail.
        """
        if not texts:
            return 0

        if metadata_list is not None and len(metadata_list) != len(texts):
            raise ValueError(
                f"metadata_list 长度 {len(metadata_list)} 与 texts 长度 {len(texts)} 不一致"
            )

        # P0-1: 熔断器开启时快速失败, 调用方据此降级
        if self._circuit_breaker.is_open():
            raise EmbeddingsCircuitOpenError()

        # P0-修复3: batch_size 统一走 settings (与 embed_texts 一致, 默认 4, 匹配 TEI max_batch_requests=4)
        if batch_size is None:
            batch_size = self.settings.embeddings_max_client_batch_size

        # 延迟导入避免循环依赖
        from src.rag.qdrant_manager import get_qdrant_manager

        qdrant = get_qdrant_manager()
        # P0-修复3: 显式 ensure_collection (QdrantManager 内部已 _ensure_collection_once 自保, 此处冗余但语义清晰)
        await qdrant.ensure_collection()
        total_indexed = 0

        # 分批处理 (避免单次 TEI 请求过大)
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i : i + batch_size]
            batch_meta = (
                metadata_list[i : i + batch_size] if metadata_list else [{} for _ in batch_texts]
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
