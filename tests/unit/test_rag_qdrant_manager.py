"""单元测试: Qdrant 客户端封装.

验证 src/rag/qdrant_manager.py:
- ensure_collection: 已存在/不存在路径
- upsert_points: PointStruct 构造, payload 字段, user_id 条件注入
- delete_by_namespace: Filter 构造
- search: should OR 过滤, score_threshold, 返回字段映射
- get_qdrant_manager 单例

AGENTS.md 第 13 章: 单元测试不依赖外部服务.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from src.config.settings import Settings
from src.rag import embeddings as emb_module
from src.rag import qdrant_manager as qm_module
from src.rag.qdrant_manager import QdrantManager, get_qdrant_manager


class _FakeEmbeddingsClient:
    """伪造 EmbeddingsClient, embed_texts 返回固定向量.

    保留 generate_point_id 静态方法 (upsert_points 调用), 直接实现避免递归
    (monkeypatch 已替换 emb_module.EmbeddingsClient, 内部 import 会拿到 fake 自身).
    """

    _NAMESPACE_DNS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

    def __init__(self, settings: Any = None) -> None:
        self.settings = settings

    async def embed_texts(self, texts: list[str], **_kwargs: Any) -> list[list[float]]:
        return [[0.1] * 1024 for _ in texts]

    @staticmethod
    def generate_point_id(namespace: str, content: str) -> str:
        """uuid5 幂等生成 (与 EmbeddingsClient.generate_point_id 一致)."""
        import hashlib

        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return str(uuid.uuid5(_FakeEmbeddingsClient._NAMESPACE_DNS, f"{namespace}:{content_hash}"))


class _FakeQdrantClient:
    """伪造 AsyncQdrantClient, 捕获调用."""

    def __init__(
        self,
        *,
        get_collection_exc: Exception | None = None,
        search_results: list[Any] | None = None,
    ) -> None:
        self.get_collection_exc = get_collection_exc
        self.search_results = search_results or []
        self.calls: dict[str, list[dict[str, Any]]] = {
            "get_collection": [],
            "create_collection": [],
            "upsert": [],
            "delete": [],
            "search": [],
        }

    async def get_collection(self, collection_name: str) -> Any:
        self.calls["get_collection"].append({"collection_name": collection_name})
        if self.get_collection_exc is not None:
            raise self.get_collection_exc
        return None

    async def create_collection(self, **kwargs: Any) -> Any:
        self.calls["create_collection"].append(kwargs)
        return None

    async def upsert(self, **kwargs: Any) -> Any:
        self.calls["upsert"].append(kwargs)
        return None

    async def delete(self, **kwargs: Any) -> Any:
        self.calls["delete"].append(kwargs)
        return None

    async def search(self, **kwargs: Any) -> Any:
        self.calls["search"].append(kwargs)
        return self.search_results

    async def close(self) -> None:
        pass


class _FakeSearchHit:
    """伪造 qdrant search hit."""

    def __init__(self, payload: dict[str, Any], score: float) -> None:
        self.payload = payload
        self.score = score


def _make_manager(
    settings: Settings | None = None,
    fake_client: _FakeQdrantClient | None = None,
) -> tuple[QdrantManager, _FakeQdrantClient]:
    """构造 QdrantManager 并替换内部 client 为 fake."""
    settings = settings or Settings(_env_file=None)
    mgr = QdrantManager(settings)
    fake = fake_client or _FakeQdrantClient()
    mgr._client = fake  # type: ignore[assignment]
    return mgr, fake


# ========== ensure_collection ==========


@pytest.mark.asyncio
async def test_ensure_collection_already_exists() -> None:
    """集合已存在: get_collection 成功, 不调用 create_collection."""
    mgr, fake = _make_manager()
    await mgr.ensure_collection()
    assert len(fake.calls["get_collection"]) == 1
    assert fake.calls["get_collection"][0]["collection_name"] == "agents"
    assert len(fake.calls["create_collection"]) == 0


@pytest.mark.asyncio
async def test_ensure_collection_creates_when_missing() -> None:
    """集合不存在: get_collection 抛异常, 调用 create_collection."""
    fake = _FakeQdrantClient(get_collection_exc=RuntimeError("collection not found"))
    mgr, _ = _make_manager(fake_client=fake)
    await mgr.ensure_collection()
    assert len(fake.calls["create_collection"]) == 1
    create_kwargs = fake.calls["create_collection"][0]
    assert create_kwargs["collection_name"] == "agents"
    # 验证 VectorParams (bge-large-zh-v1.5 固定 1024 维)
    vectors_config = create_kwargs["vectors_config"]
    assert vectors_config.size == 1024


# ========== upsert_points ==========


@pytest.mark.asyncio
async def test_upsert_points_with_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """upsert_points: user_id 非空时 payload 含 user_id 字段."""
    monkeypatch.setattr(emb_module, "EmbeddingsClient", _FakeEmbeddingsClient)

    mgr, fake = _make_manager()
    points = [
        {"content": "文档A", "metadata": {"source": "web"}},
        {"content": "文档B", "metadata": {"source": "arxiv"}},
    ]
    await mgr.upsert_points("ns:user123", points, user_id="user123")

    assert len(fake.calls["upsert"]) == 1
    upsert_kwargs = fake.calls["upsert"][0]
    assert upsert_kwargs["collection_name"] == "agents"
    qdrant_points = upsert_kwargs["points"]
    assert len(qdrant_points) == 2

    p0 = qdrant_points[0]
    assert p0.payload["content"] == "文档A"
    assert p0.payload["metadata"] == {"source": "web"}
    assert p0.payload["namespace"] == "ns:user123"
    assert p0.payload["user_id"] == "user123"  # user_id 条件注入
    assert len(p0.vector) == 1024
    assert p0.id  # 应有 uuid


@pytest.mark.asyncio
async def test_upsert_points_without_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """upsert_points: user_id=None 时 payload 不含 user_id 字段."""
    monkeypatch.setattr(emb_module, "EmbeddingsClient", _FakeEmbeddingsClient)

    mgr, fake = _make_manager()
    points = [{"content": "共享文档", "metadata": {}}]
    await mgr.upsert_points("agent_id", points, user_id=None)

    upsert_kwargs = fake.calls["upsert"][0]
    p0 = upsert_kwargs["points"][0]
    assert "user_id" not in p0.payload
    assert p0.payload["namespace"] == "agent_id"
    assert p0.payload["content"] == "共享文档"


# ========== delete_by_namespace ==========


@pytest.mark.asyncio
async def test_delete_by_namespace() -> None:
    """delete_by_namespace: 验证 Filter 构造 (must namespace = ns)."""
    mgr, fake = _make_manager()
    await mgr.delete_by_namespace("test-ns")

    assert len(fake.calls["delete"]) == 1
    delete_kwargs = fake.calls["delete"][0]
    assert delete_kwargs["collection_name"] == "agents"
    points_selector = delete_kwargs["points_selector"]
    assert points_selector.filter.must[0].key == "namespace"
    assert points_selector.filter.must[0].match.value == "test-ns"


# ========== search ==========


@pytest.mark.asyncio
async def test_search_returns_mapped_fields() -> None:
    """search: 验证 should OR 过滤, score_threshold, 返回字段映射."""
    fake_hits = [
        _FakeSearchHit(
            payload={"content": "文档A", "metadata": {"src": "web"}, "namespace": "ns1"},
            score=0.95,
        ),
        _FakeSearchHit(
            payload={"content": "文档B", "metadata": {"src": "arxiv"}, "namespace": "ns2"},
            score=0.85,
        ),
    ]
    fake = _FakeQdrantClient(search_results=fake_hits)
    mgr, _ = _make_manager(fake_client=fake)

    results = await mgr.search(
        query_vector=[0.1] * 1024,
        namespaces=["ns1", "ns2"],
        limit=10,
        score_threshold=0.5,
    )

    assert len(results) == 2
    assert results[0]["content"] == "文档A"
    assert results[0]["metadata"] == {"src": "web"}
    assert results[0]["namespace"] == "ns1"
    assert results[0]["score"] == 0.95

    search_kwargs = fake.calls["search"][0]
    query_filter = search_kwargs["query_filter"]
    assert len(query_filter.should) == 2
    assert query_filter.should[0].match.value == "ns1"
    assert query_filter.should[1].match.value == "ns2"
    assert search_kwargs["score_threshold"] == 0.5
    assert search_kwargs["limit"] == 10


@pytest.mark.asyncio
async def test_search_uses_default_score_threshold() -> None:
    """search: score_threshold=None 时使用 settings.score_threshold."""
    settings = Settings(score_threshold=0.3, _env_file=None)
    fake = _FakeQdrantClient()
    mgr, _ = _make_manager(settings=settings, fake_client=fake)

    await mgr.search([0.1] * 1024, ["ns1"], limit=5)

    search_kwargs = fake.calls["search"][0]
    assert search_kwargs["score_threshold"] == 0.3


@pytest.mark.asyncio
async def test_search_empty_results() -> None:
    """search: 无命中时返回空列表."""
    fake = _FakeQdrantClient(search_results=[])
    mgr, _ = _make_manager(fake_client=fake)
    results = await mgr.search([0.1] * 1024, ["ns1"], limit=5)
    assert results == []


# ========== get_qdrant_manager 单例 ==========


def test_get_qdrant_manager_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_qdrant_manager 两次调用返回同一实例."""
    test_settings = Settings(_env_file=None)
    monkeypatch.setattr(qm_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(qm_module, "_manager", None)

    m1 = get_qdrant_manager()
    m2 = get_qdrant_manager()
    assert m1 is m2
