"""功能测试: 验证 Qdrant 向量库服务.

- 单一集合 agents, distance=Cosine, vector_size=768 (bge-base-zh-v1.5 固定)
- payload namespace 隔离: 共享知识库 namespace=agent_id, 用户私有 namespace={agent_id}:{user_id}
- 点 id 用 uuid5(NAMESPACE_DNS, f"{namespace}:{content_hash}") 幂等生成
- 检索时必须显式传目标 namespace 列表, 禁止无 namespace 过滤的全集合扫描
- 测试数据隔离: namespace=test_* + user_id=test_*

执行方式 (宿主机, 容器栈已 healthy):
    set QDRANT_URL=http://127.0.0.1:6333
    pytest tests/functional/test_qdrant_service.py -v -m functional
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

# Qdrant 服务地址
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "") or None
COLLECTION = os.getenv("QDRANT_COLLECTION", "agents")
VECTOR_SIZE = 768

# Embeddings 服务 (用于生成测试向量)
EMBEDDINGS_URL = os.getenv("EMBEDDINGS_URL", "http://127.0.0.1:8088").rstrip("/")
EMBEDDINGS_API_KEY = os.getenv("EMBEDDINGS_API_KEY", "")

# uuid5 命名空间 (与 src/rag/embeddings.py 一致)
NAMESPACE_DNS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

# 测试 namespace 前缀 (测试数据隔离 namespace=test_*)
TEST_NAMESPACE = f"test_{uuid.uuid4().hex[:8]}"


def _auth_headers() -> dict[str, str]:
    """构造 TEI 鉴权请求头."""
    headers: dict[str, str] = {}
    if EMBEDDINGS_API_KEY:
        headers["Authorization"] = f"Bearer {EMBEDDINGS_API_KEY}"
    return headers


def _get_qdrant_client() -> QdrantClient:
    """构造 QdrantClient (同步)."""
    return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=30)


def _generate_point_id(namespace: str, content: str) -> str:
    """与 src/rag/embeddings.py 一致的点 id 生成 (uuid5)."""
    content_hash = str(hash(content))  # 简化: 测试用
    return str(uuid.uuid5(NAMESPACE_DNS, f"{namespace}:{content_hash}"))


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """调用 TEI 服务获取向量 (直连, 不走 src/rag/embeddings.py)."""
    r = httpx.post(
        f"{EMBEDDINGS_URL}/embed",
        json={"inputs": texts},
        headers=_auth_headers(),
        timeout=httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0),
    )
    r.raise_for_status()
    return r.json()


@pytest.mark.functional
def test_collection_exists() -> None:
    """验证 Qdrant 集合 agents 存在: GET /collections/agents → 200.

    单一集合 agents. Agent 启动时应通过 ensure_collection() 创建.
    """
    client = _get_qdrant_client()
    try:
        info = client.get_collection(COLLECTION)
        assert info is not None
        assert info.config.params.vectors.size == VECTOR_SIZE, (
            f"向量维度非 {VECTOR_SIZE}: {info.config.params.vectors.size}"
        )
        assert info.config.params.vectors.distance == Distance.COSINE
    except (UnexpectedResponse, Exception) as e:
        pytest.fail(
            f"集合 {COLLECTION} 不存在或获取失败: {e}\n"
            "Agent 启动时应通过 ensure_collection() 创建集合"
        )
    finally:
        client.close()


@pytest.mark.functional
def test_upsert_and_search() -> None:
    """验证 Qdrant upsert + 搜索 + 清理 (namespace=test_*).

    - 测试数据隔离: namespace=test_*
    - 点 id 用 uuid5 幂等生成
    - payload 含 content + metadata + namespace
    - 检索时显式传 namespace 过滤
    """
    client = _get_qdrant_client()
    try:
        # 确保集合存在 (避免依赖其他测试执行顺序)
        try:
            client.get_collection(COLLECTION)
        except (UnexpectedResponse, Exception):
            client.create_collection(
                collection_name=COLLECTION,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )

        # 准备测试数据
        test_texts = [
            "Python 异步编程核心概念",
            "中文检索增强生成技术",
            "LangGraph 状态机编排",
        ]
        vectors = _embed_texts(test_texts)
        assert len(vectors) == len(test_texts)

        # 构造测试点 (namespace=test_*, payload 含 content + metadata + namespace)
        points = []
        for text, vec in zip(test_texts, vectors, strict=True):
            point_id = _generate_point_id(TEST_NAMESPACE, text)
            points.append(
                PointStruct(
                    id=point_id,
                    vector=vec,
                    payload={
                        "content": text,
                        "metadata": {"source": "test", "test_ns": TEST_NAMESPACE},
                        "namespace": TEST_NAMESPACE,
                    },
                )
            )

        # upsert
        client.upsert(collection_name=COLLECTION, points=points, wait=True)

        # 搜索: 用第一条文本的向量搜索, 过滤 namespace=test_*
        # qdrant_client ≥1.18: 用 query_points 替代已弃用的 search
        # Filter 用 FieldCondition(key=..., match=MatchValue(value=...)) 包装
        query_vec = vectors[0]
        search_result = client.query_points(
            collection_name=COLLECTION,
            query=query_vec,
            query_filter=Filter(
                must=[FieldCondition(key="namespace", match=MatchValue(value=TEST_NAMESPACE))]
            ),
            limit=5,
        )
        results = search_result.points
        assert len(results) >= 1, f"namespace 过滤搜索无结果: {results}"
        top = results[0]
        assert top.payload is not None
        assert top.payload.get("namespace") == TEST_NAMESPACE
        assert top.payload.get("content") in test_texts
        assert top.score > 0.5, f"top-1 相似度过低: {top.score}"

    finally:
        # 清理: 删除测试 namespace 的所有点
        try:
            client.delete(
                collection_name=COLLECTION,
                points_selector=Filter(must=[MatchValue(key="namespace", value=TEST_NAMESPACE)]),
            )
        except Exception:  # noqa: BLE001
            pass
        client.close()


@pytest.mark.functional
def test_namespace_filter_isolation() -> None:
    """验证 namespace 过滤隔离: 不存在的 namespace 搜索应返回空.

    检索时必须显式传目标 namespace 列表.
    """
    client = _get_qdrant_client()
    try:
        try:
            client.get_collection(COLLECTION)
        except (UnexpectedResponse, Exception):
            pytest.skip(f"集合 {COLLECTION} 不存在, 跳过隔离测试")

        # 用随机向量搜索一个不存在的 namespace
        # qdrant_client ≥1.18: 用 query_points 替代已弃用的 search
        # Filter 用 FieldCondition(key=..., match=MatchValue(value=...)) 包装
        fake_vec = [0.01] * VECTOR_SIZE
        search_result = client.query_points(
            collection_name=COLLECTION,
            query=fake_vec,
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key="namespace",
                        match=MatchValue(value=f"test_nonexistent_{uuid.uuid4().hex}"),
                    )
                ]
            ),
            limit=5,
        )
        results = search_result.points
        assert len(results) == 0, f"不存在 namespace 仍返回结果, 隔离失效: {results}"
    finally:
        client.close()
