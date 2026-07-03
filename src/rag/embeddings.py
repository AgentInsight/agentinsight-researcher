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
from typing import cast

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

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = httpx.AsyncClient(
            base_url=self.settings.embeddings_base_url,
            timeout=30.0,
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
