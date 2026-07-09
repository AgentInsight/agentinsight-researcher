"""Qdrant 客户端封装.

AGENTS.md 第 7 章硬约束:
- 单一集合 agents, distance=Cosine, vector_size=768 (bge-base-zh-v1.5 固定)
- payload namespace 隔离:
  - 共享知识库: namespace = agent_id (不含 user_id, 所有用户共享)
  - 用户私有数据: namespace = {agent_id}:{user_id} (payload 含 user_id)
- 点 id 用 uuid5(NAMESPACE_DNS, f"{namespace}:{content_hash}") 幂等生成
- payload 必须含 content + metadata + namespace (用户私有额外含 user_id)
- 检索时必须显式传目标 namespace 列表, 禁止无 namespace 过滤的全集合扫描

用户需求 (2026-07-06): 检验 embeddings+qdrant 是否有私有数据的命名空间,
如果没有, 把结果保存在内存或缓存里, 之后不再调用 embeddings+qdrant 做数据查询,
确保系统采用最少次的 embeddings+qdrant 调用.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


# ========== namespace 可用性缓存 (用户需求: 最少次 embeddings+qdrant 调用) ==========
# 缓存 namespace -> has_data 的布尔结果, TTL 10 分钟
# 避免每次请求都调 Qdrant count (即使 exact=False 也有网络 RTT)
# 参考 AgentInsightService VectorRetriever.available 的 _check_interval 机制
_NAMESPACE_CACHE_TTL: int = 600  # 10 分钟 (秒)
_namespace_cache: dict[str, tuple[bool, float]] = {}  # key=namespace, value=(has_data, timestamp)


class QdrantManager:
    """Qdrant 集合管理 + 客户端封装.

    AGENTS.md 第 7 章: 单一集合 agents, payload namespace 隔离.
    """

    settings: Settings
    _client: Any  # AsyncQdrantClient
    _collection_ready: bool  # P0-修复2: 集合就绪缓存标志, 避免每次操作都网络检查

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._collection_ready = False  # 首次操作前必须 ensure_collection
        # 延迟导入, 避免模块加载时强依赖
        # P1-04: 抑制 qdrant_client 在 http+api_key 场景的 UserWarning
        # (测试环境用 http+api_key, qdrant_client 会警告 "Api key is used with an insecure connection")
        # 同时跳过服务器版本检查 (测试环境可能无法连接服务器, 避免警告)
        import warnings

        from qdrant_client import AsyncQdrantClient

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Api key is used with an insecure connection",
                category=UserWarning,
            )
            warnings.filterwarnings(
                "ignore",
                message="Failed to obtain server version",
                category=UserWarning,
            )
            self._client = AsyncQdrantClient(
                url=self.settings.qdrant_url,
                api_key=self.settings.qdrant_api_key,
                timeout=30,
                check_compatibility=False,
            )

    async def _ensure_collection_once(self) -> None:
        """P0-修复2: 幂等确保集合存在 (首次调用 ensure_collection, 后续短路返回).

        性能考虑: 首次调用有 RTT 开销 (~10ms get_collection),
        后续直接返回, 避免每次操作都检查.
        """
        if not self._collection_ready:
            await self.ensure_collection()
            self._collection_ready = True

    async def ensure_collection(self) -> None:
        """确保集合存在 (不存在则创建, 含 HNSW 参数调优 P0-03).

        AGENTS.md 第 7 章: 单一集合 agents, distance=Cosine, vector_size=768.
        P0-03: 中文密集检索场景, HNSW m=32/ef_construct=200 提升召回率,
        scalar 量化降低内存 50%.

        P0-修复1: 显式刷新 _collection_ready 标志 (允许外部强制重检).
        """
        from qdrant_client.http.exceptions import UnexpectedResponse

        try:
            await self._client.get_collection(self.settings.qdrant_collection)
            logger.debug("Qdrant 集合 %s 已存在", self.settings.qdrant_collection)
            self._collection_ready = True
        except (UnexpectedResponse, Exception):  # noqa: BLE001
            logger.info(
                "创建 Qdrant 集合 %s (HNSW m=%d, ef_construct=%d, quantization=%s)",
                self.settings.qdrant_collection,
                self.settings.qdrant_hnsw_m,
                self.settings.qdrant_hnsw_ef_construct,
                self.settings.qdrant_quantization,
            )
            from qdrant_client.http.models import (
                Distance,
                HnswConfigDiff,
                ScalarQuantization,
                ScalarQuantizationConfig,
                ScalarType,
                VectorParams,
            )

            # HNSW 参数 (P0-03: 中文密集检索调优)
            hnsw_config = HnswConfigDiff(
                m=self.settings.qdrant_hnsw_m,
                ef_construct=self.settings.qdrant_hnsw_ef_construct,
                full_scan_threshold=self.settings.qdrant_hnsw_full_scan_threshold,
            )

            # 标量量化 (P0-03: int8 量化降低内存 50%)
            # qdrant-client ≥1.18 枚举为大写 ScalarType.INT8
            quantization_config = ScalarQuantization(
                scalar=ScalarQuantizationConfig(
                    type=ScalarType.INT8,
                    quantile=0.99,
                    always_ram=True,
                ),
            )

            await self._client.create_collection(
                collection_name=self.settings.qdrant_collection,
                vectors_config=VectorParams(
                    size=self.settings.qdrant_vector_size,
                    distance=Distance.COSINE,
                ),
                hnsw_config=hnsw_config,
                quantization_config=quantization_config,
            )
            self._collection_ready = True

    def build_shared_namespace(self) -> str:
        """共享知识库 namespace = agent_id (旧版兼容, 推荐用 build_data_shared_namespace).

        AGENTS.md 第 7 章: 共享知识库 namespace = agent_id, 不含 user_id.
        """
        return self.settings.agent_name

    def build_user_namespace(self, user_id: str) -> str:
        """用户私有数据 namespace = {agent_id}:{user_id} (旧版兼容, 推荐用 build_data_user_namespace).

        AGENTS.md 第 7 章: 用户私有数据 namespace = {agent_id}:{user_id}, payload 含 user_id.
        """
        return f"{self.settings.agent_name}:{user_id}"

    # ========== 新版 namespace API (CHITCHAT_FAST_LLM_OPTIMIZATION_PLAN, 用户需求: 拆分 data 池) ==========
    # 数据 namespace 池:
    # - agentinsight-researcher-data: 用户私有数据搜索 (按 user_id 隔离)
    # 注: 原 chat namespace 池 (短查询/离题种子) 已在 QUERY_CLASSIFIER_FAST_LLM_OPTIMIZATION_PLAN.md
    #     P2 阶段移除 (改用 FAST_LLM + Redis 缓存), build_chat_namespace 方法已删除.
    def build_data_shared_namespace(self) -> str:
        """共享研究数据 namespace = {agent_id}-data (新命名, 替代旧 build_shared_namespace).

        AGENTS.md 第 7 章: 共享知识库, 所有用户共享, 不含 user_id.
        """
        return f"{self.settings.agent_name}-data"

    def build_data_user_namespace(self, user_id: str) -> str:
        """用户私有数据 namespace = {agent_id}-data:{user_id}.

        AGENTS.md 第 7 章: 用户私有数据按 user_id 隔离, payload 含 user_id.
        """
        return f"{self.settings.agent_name}-data:{user_id}"

    async def count_points_in_namespace(self, namespace: str) -> int:
        """统计指定 namespace 下的点数.

        AGENTS.md 第 7 章: 按 payload namespace 字段过滤统计.
        用于"私有数据搜索前先判断有没有数据"的需求.

        P1-2 修复: exact=True → exact=False
        - exact=True 触发全量扫描, 大集合上延迟可达数百毫秒甚至秒级
        - exact=False 使用 HNSW 索引/元数据统计, 毫秒级响应
        - 仅用于"是否存在数据"的存在性判断, 近似计数足够

        Args:
            namespace: 要统计的 namespace 名称

        Returns:
            该 namespace 下的点数; 集合不存在或异常返回 0
        """
        from qdrant_client.http.models import FieldCondition, Filter, MatchValue

        count_filter = Filter(
            must=[
                FieldCondition(key="namespace", match=MatchValue(value=namespace)),
            ],
        )
        try:
            result = await self._client.count(
                collection_name=self.settings.qdrant_collection,
                count_filter=count_filter,
                exact=False,  # P1-2 修复: 近似计数, 毫秒级响应 (存在性判断足够)
            )
            return int(result.count)
        except Exception as e:  # noqa: BLE001
            logger.warning("Qdrant count namespace=%s 失败: %s", namespace, e)
            return 0

    async def namespace_has_data(self, namespace: str) -> bool:
        """判断指定 namespace 是否有数据 (count > 0).

        用户需求: "私有数据搜索的时候先判断有没有私有数据,
        先判断有没有对应命名空间, 再看命名空间里面有没有数据".

        新需求 (2026-07-06): 结果缓存在内存中 (TTL 10 分钟),
        避免每次请求都调 Qdrant count (减少网络 RTT).
        如果缓存命中且结果为 False (无数据), 直接返回, 不调 Qdrant.
        如果缓存命中且结果为 True (有数据), 直接返回, 不调 Qdrant.
        缓存过期或未命中时才调 Qdrant count.
        """
        # 检查内存缓存
        cached = _namespace_cache.get(namespace)
        if cached is not None:
            has_data, ts = cached
            if time.time() - ts < _NAMESPACE_CACHE_TTL:
                logger.debug(
                    "namespace %s 可用性缓存命中: has_data=%s (缓存年龄 %.0fs)",
                    namespace,
                    has_data,
                    time.time() - ts,
                )
                return has_data

        # 缓存未命中或过期, 调 Qdrant count
        count = await self.count_points_in_namespace(namespace)
        has_data = count > 0
        # 写入缓存
        _namespace_cache[namespace] = (has_data, time.time())
        logger.info(
            "namespace %s 可用性检查完成: has_data=%s (count=%d, 已缓存 %ds)",
            namespace,
            has_data,
            count,
            _NAMESPACE_CACHE_TTL,
        )
        return has_data

    async def has_user_private_data(self, user_id: str) -> bool:
        """判断用户是否有私有数据 (先检查 namespace 有数据).

        用户需求: 私有数据搜索前先判断有没有数据, 有的话才搜索.
        走新命名空间 {agent_id}-data:{user_id}.

        Args:
            user_id: 用户 ID

        Returns:
            True 表示该用户在 data namespace 下有私有数据
        """
        if not user_id:
            return False
        namespace = self.build_data_user_namespace(user_id)
        return await self.namespace_has_data(namespace)

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

        P0-修复2: 入口自保 ensure_collection, 避免新环境首次写入 404.
        """
        from qdrant_client.http.models import PointStruct

        from src.rag.embeddings import EmbeddingsClient, get_embeddings_client

        # P0-修复2: 自保 ensure_collection (首次调用 ensure, 后续短路)
        await self._ensure_collection_once()

        embeddings_client = get_embeddings_client()

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
        # 写入成功后更新 namespace 可用性缓存 (有数据 → True)
        # 避免下一次 namespace_has_data 调用 Qdrant count
        _namespace_cache[namespace] = (True, time.time())
        logger.debug(
            "Qdrant 写入 %d 点 (namespace=%s, user_id=%s, 已更新可用性缓存=True)",
            len(qdrant_points),
            namespace,
            user_id,
        )

    async def delete_by_namespace(self, namespace: str) -> None:
        """删除指定 namespace 下的所有点 (按 payload namespace 字段过滤).

        用于种子模式版本更新时清理旧数据 (AGENTS.md 第 7 章: payload namespace 隔离).

        P0-修复2: 入口自保 ensure_collection, 避免新环境首次删除 404.
        """
        from qdrant_client.http.models import (
            FieldCondition,
            Filter,
            FilterSelector,
            MatchValue,
        )

        # P0-修复2: 自保 ensure_collection (首次调用 ensure, 后续短路)
        await self._ensure_collection_once()

        query_filter = Filter(
            must=[
                FieldCondition(key="namespace", match=MatchValue(value=namespace)),
            ],
        )
        await self._client.delete(
            collection_name=self.settings.qdrant_collection,
            points_selector=FilterSelector(filter=query_filter),
        )
        # 删除成功后失效 namespace 可用性缓存 (下次查询重新 count)
        _namespace_cache.pop(namespace, None)
        logger.debug("Qdrant 删除 namespace=%s 下所有点 (已失效可用性缓存)", namespace)

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
        AGENTS.md 第 7 章: score_threshold 仅 rerank 启用时生效, 向量检索阶段不应套用
            rerank 的 0.3 阈值 (会提前裁剪 RRF 候选集, 降低召回). 调用方未显式传
            score_threshold 时, 此处不再 fallback 到 settings.score_threshold,
            让 RRF + Rerank 阶段做最终筛选.

        P0-修复2: 入口自保 ensure_collection, 避免新环境首次检索 404.
        """
        from qdrant_client.http.models import FieldCondition, Filter, MatchValue

        # P0-修复2: 自保 ensure_collection (首次调用 ensure, 后续短路)
        await self._ensure_collection_once()

        # 构建 namespace 过滤 (OR 关系)
        should_conditions = [
            FieldCondition(key="namespace", match=MatchValue(value=ns)) for ns in namespaces
        ]
        query_filter = Filter(should=should_conditions)

        # P0 阈值误用修复: score_threshold=None 时不应用阈值 (让 RRF + Rerank 筛选);
        # 仅当调用方显式传值 (如 rerank 启用场景) 才传入. 不再 fallback 到
        # settings.score_threshold, 避免向量阶段提前过滤 cosine < 0.3 的文档.
        threshold = score_threshold

        # qdrant-client ≥1.18: AsyncQdrantClient.search 已移除, 改用 query_points
        # - 参数 query_vector → query
        # - 返回结构: 旧 search 直接返回 list[ScoredPoint]; 新 query_points 返回
        #   QueryResponse, 实际命中列表在 .points 字段, 每个 point.payload/point.score
        #   结构与旧 hit 一致
        results = await self._client.query_points(
            collection_name=self.settings.qdrant_collection,
            query=query_vector,
            query_filter=query_filter,
            limit=limit,
            score_threshold=threshold,
        )

        return [
            {
                "content": point.payload.get("content", ""),
                "metadata": point.payload.get("metadata", {}),
                "namespace": point.payload.get("namespace", ""),
                "score": point.score,
            }
            for point in results.points
        ]

    async def scroll_all_by_namespace(
        self,
        namespace: str,
        *,
        batch_size: int = 1000,
        max_points: int = 100_000,
    ) -> list[dict[str, Any]]:
        """scroll 拉取 namespace 内所有点的 content (用于 BM25 语料构建).

        AGENTS.md 第 7 章: 按 payload namespace 字段过滤, 仅返回 content/metadata/
        namespace 三键 (与 upsert_points 写入 payload 一致). 不返回向量 (节省网络带宽).

        P0 BM25 断点修复: HybridRetriever._ensure_bm25_corpus 调用此方法填充 BM25 语料,
        替代旧的"无任何调用方"路径. 语料缓存到 Redis (带版本号), 文档新增/删除时
        通过 invalidate_bm25_cache 失效缓存.

        Args:
            namespace: 要拉取的 namespace.
            batch_size: 单次 scroll 批量大小 (qdrant scroll limit, 默认 1000).
            max_points: 安全上限, 防止异常大集合压垮内存 (默认 10 万).

        Returns:
            文档列表, 每项为 {"content": str, "metadata": dict, "namespace": str};
            集合不存在或异常返回空列表 (降级, 不阻断检索).
        """
        from qdrant_client.http.models import FieldCondition, Filter, MatchValue

        # P0-修复2: 自保 ensure_collection (首次调用 ensure, 后续短路)
        await self._ensure_collection_once()

        count_filter = Filter(
            must=[
                FieldCondition(key="namespace", match=MatchValue(value=namespace)),
            ],
        )

        all_docs: list[dict[str, Any]] = []
        offset: int | None = None
        try:
            while True:
                # qdrant-client scroll: 返回 (points, next_offset), next_offset=None 表示已到底
                points, offset = await self._client.scroll(
                    collection_name=self.settings.qdrant_collection,
                    scroll_filter=count_filter,
                    limit=batch_size,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                for p in points:
                    payload = p.payload or {}
                    all_docs.append(
                        {
                            "content": payload.get("content", ""),
                            "metadata": payload.get("metadata", {}),
                            "namespace": payload.get("namespace", namespace),
                        }
                    )
                # 安全上限: 防止异常大集合压垮内存
                if len(all_docs) >= max_points:
                    logger.warning(
                        "BM25 语料拉取达到安全上限 max_points=%d (namespace=%s), 截断",
                        max_points,
                        namespace,
                    )
                    break
                if offset is None:
                    break
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "scroll_all_by_namespace 失败 (namespace=%s), 返回已拉取的 %d 条: %s",
                namespace,
                len(all_docs),
                e,
            )
        return all_docs

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
