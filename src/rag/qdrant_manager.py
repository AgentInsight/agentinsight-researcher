"""Qdrant 客户端封装.

AGENTS.md 第 7 章硬约束:
- 单一集合 agents, distance=Cosine, vector_size=1024 (bge-large-zh-v1.5 固定)
- payload namespace 隔离:
  - 共享知识库: namespace = agent_id (不含 user_id, 所有用户共享)
  - 用户私有数据: namespace = {agent_id}:{user_id} (payload 含 user_id)
- 点 id 用 uuid5(NAMESPACE_DNS, f"{namespace}:{content_hash}") 幂等生成
- payload 必须含 content + metadata + namespace (用户私有额外含 user_id)
- 检索时必须显式传目标 namespace 列表, 禁止无 namespace 过滤的全集合扫描
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class QdrantManager:
    """Qdrant 集合管理 + 客户端封装.

    AGENTS.md 第 7 章: 单一集合 agents, payload namespace 隔离.
    """

    settings: Settings
    _client: Any  # AsyncQdrantClient

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        # 延迟导入, 避免模块加载时强依赖
        from qdrant_client import AsyncQdrantClient

        self._client = AsyncQdrantClient(
            url=self.settings.qdrant_url,
            api_key=self.settings.qdrant_api_key,
            timeout=30,
        )

    async def ensure_collection(self) -> None:
        """确保集合存在 (不存在则创建)."""
        from qdrant_client.http.exceptions import UnexpectedResponse

        try:
            await self._client.get_collection(self.settings.qdrant_collection)
            logger.debug("Qdrant 集合 %s 已存在", self.settings.qdrant_collection)
        except (UnexpectedResponse, Exception):  # noqa: BLE001
            logger.info("创建 Qdrant 集合 %s", self.settings.qdrant_collection)
            from qdrant_client.http.models import Distance, VectorParams

            await self._client.create_collection(
                collection_name=self.settings.qdrant_collection,
                vectors_config=VectorParams(
                    size=self.settings.qdrant_vector_size,
                    distance=Distance.COSINE,
                ),
            )

    def build_shared_namespace(self) -> str:
        """共享知识库 namespace = agent_id."""
        return self.settings.agent_name

    def build_user_namespace(self, user_id: str) -> str:
        """用户私有数据 namespace = {agent_id}:{user_id}."""
        return f"{self.settings.agent_name}:{user_id}"

    async def upsert_points(
        self,
        namespace: str,
        points: list[dict[str, Any]],
        *,
        user_id: str | None = None,
    ) -> None:
        """批量写入点.

        AGENTS.md 第 7 章:
        - 点 id 用 uuid5(NAMESPACE_DNS, f"{namespace}:{content_hash}") 幂等生成
        - payload 必须含 content + metadata + namespace
        - 用户私有数据额外含 user_id
        """
        from qdrant_client.http.models import PointStruct

        from src.rag.embeddings import EmbeddingsClient

        embeddings_client = EmbeddingsClient(self.settings)

        # 批量嵌入
        texts = [p["content"] for p in points]
        vectors = await embeddings_client.embed_texts(texts)

        # 构造 PointStruct
        qdrant_points = []
        for i, point in enumerate(points):
            content = point["content"]
            point_id = EmbeddingsClient.generate_point_id(namespace, content)

            payload = {
                "content": content,
                "metadata": point.get("metadata", {}),
                "namespace": namespace,
            }
            # 用户私有数据额外含 user_id
            if user_id:
                payload["user_id"] = user_id

            qdrant_points.append(PointStruct(id=point_id, vector=vectors[i], payload=payload))

        await self._client.upsert(
            collection_name=self.settings.qdrant_collection,
            points=qdrant_points,
        )
        logger.debug(
            "Qdrant 写入 %d 点 (namespace=%s, user_id=%s)",
            len(qdrant_points),
            namespace,
            user_id,
        )

    async def search(
        self,
        query_vector: list[float],
        namespaces: list[str],
        *,
        limit: int = 10,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """向量检索.

        AGENTS.md 第 7 章: 必须显式传 namespace 列表, 禁止全集合扫描.
        """
        from qdrant_client.http.models import FieldCondition, Filter, MatchValue

        # 构建 namespace 过滤 (OR 关系)
        should_conditions = [
            FieldCondition(key="namespace", match=MatchValue(value=ns)) for ns in namespaces
        ]
        query_filter = Filter(should=should_conditions)  # type: ignore[arg-type]  # qdrant Filter.should 期望 list[FieldCondition|...], list 不变性导致 list[FieldCondition] 不兼容

        threshold = score_threshold or self.settings.score_threshold

        results = await self._client.search(
            collection_name=self.settings.qdrant_collection,
            query_vector=query_vector,
            query_filter=query_filter,
            limit=limit,
            score_threshold=threshold,
        )

        return [
            {
                "content": hit.payload.get("content", ""),
                "metadata": hit.payload.get("metadata", {}),
                "namespace": hit.payload.get("namespace", ""),
                "score": hit.score,
            }
            for hit in results
        ]

    async def close(self) -> None:
        """关闭客户端."""
        await self._client.close()


# ========== 全局单例 ==========
_manager: QdrantManager | None = None


def get_qdrant_manager() -> QdrantManager:
    """获取全局 QdrantManager 单例."""
    global _manager
    if _manager is None:
        _manager = QdrantManager()
    return _manager
