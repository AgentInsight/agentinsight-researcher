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

import logging
import uuid
from typing import Any, cast

import httpx

from src.config.settings import Settings, get_settings
from src.observability.tracing import trace_embedding

logger = logging.getLogger(__name__)

# uuid5 命名空间 (AGENTS.md 第 7 章: 点 id 用 uuid5(NAMESPACE_DNS, ...))
NAMESPACE_DNS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


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
            timeout=30.0,
            headers=headers,
        )

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
            try:
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
                    usage_details={"token_count": token_count},
                )
                return cast(list[list[float]], vectors)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "Embedding 调用失败: type=%s repr=%r str=%s",
                    type(e).__name__,
                    e,
                    e,
                )
                span.update(metadata={"error": f"{type(e).__name__}: {e}"})
                raise

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
