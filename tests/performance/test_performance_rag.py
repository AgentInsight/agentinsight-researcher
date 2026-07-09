"""性能测试: RAG 性能 (检索耗时 + Embedding batch 吞吐).

AGENTS.md 第 7/13 章硬约束:
- 检索必须混合 BM25 + 向量 (bge-large-zh-v1.5), 默认 vector_weight=0.7 / bm25_weight=0.3
- Embeddings: bge-large-zh-v1.5 (固定 1024 维), TEI 服务 /embed 接口
- 性能测试在 docker compose up -d 且全部容器 service_healthy 后执行
- 测试数据隔离: namespace=test_*

本文件专注 RAG 子系统性能 (与 test_throughput.py 区别):
- test_throughput.py: TEI 单条/批量延迟 + Qdrant 搜索延迟 + 并发短查询
- test_performance_rag.py (本文件): 端到端 RAG 检索 + Embedding batch 吞吐量 + 检索质量

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    set EMBEDDINGS_URL=http://127.0.0.1:8088
    set QDRANT_URL=http://127.0.0.1:6333
    pytest tests/performance/test_performance_rag.py -v -m performance -s
"""

from __future__ import annotations

import time
import uuid

import httpx
import pytest

from tests.performance.conftest import (
    QDRANT_COLLECTION,
    embeddings_auth_headers,
    make_http_client,
    qdrant_auth_headers,
)

pytestmark = pytest.mark.performance

# TEI 服务首次加载模型较慢
TEI_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)

# Qdrant 搜索超时
QDRANT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)

# bge-large-zh-v1.5 固定维度
VECTOR_DIM = 1024


def _unique_namespace(prefix: str = "perf_rag") -> str:
    """生成唯一 namespace (AGENTS.md 第 13 章: namespace=test_*)."""
    return f"test_{prefix}_{uuid.uuid4().hex[:8]}"


# ========== Embedding batch 吞吐量 ==========


def test_embeddings_batch_throughput_10_texts(
    embeddings_url: str, perf_thresholds: dict[str, float]
) -> None:
    """验证 Embedding batch 10 条文本的吞吐量 (吞吐 = texts/秒).

    AGENTS.md 第 7 章: TEI 支持批量嵌入, 客户端按 embeddings_max_client_batch_size 分批.
    TEI MAX_BATCH_REQUESTS=4, 10 条文本分 3 批 (4+4+2) 发送.
    阈值: 10 条文本应在 5s 内完成 (吞吐 ≥ 2 texts/s).
    """
    threshold_s = perf_thresholds["embeddings_batch_10_s"]
    texts = [
        "人工智能在医疗领域的应用前景",
        "Python 异步编程与 asyncio 实践",
        "中文检索增强生成技术 RAG",
        "LangGraph 状态机编排多 Agent 系统",
        "Qdrant 向量数据库的混合检索策略",
        "大语言模型在金融风控中的应用",
        "BM25 与向量检索的 RRF 融合算法",
        "bge-large-zh-v1.5 嵌入模型性能评测",
        "MCP 协议在 AI Agent 工具调用中的实践",
        "PostgreSQL Checkpointer 会话持久化方案",
    ]
    headers = embeddings_auth_headers()
    batch_size = 4  # TEI MAX_BATCH_REQUESTS=4

    all_vectors: list[list[float]] = []
    with make_http_client(timeout=TEI_TIMEOUT) as client:
        start = time.perf_counter()
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            r = client.post(
                f"{embeddings_url}/embed",
                json={"inputs": batch},
                headers=headers,
            )
            assert r.status_code == 200, f"/embed 批量非 200: {r.status_code} {r.text[:200]}"
            all_vectors.extend(r.json())
        elapsed = time.perf_counter() - start

    assert len(all_vectors) == len(texts)
    for i, vec in enumerate(all_vectors):
        assert len(vec) == VECTOR_DIM, f"第 {i} 个向量维度非 {VECTOR_DIM}: {len(vec)}"

    # 吞吐量计算
    throughput = len(texts) / elapsed if elapsed > 0 else 0
    assert elapsed < threshold_s, (
        f"批量 {len(texts)} 条嵌入耗时 {elapsed:.3f}s 超过阈值 {threshold_s}s "
        f"(吞吐 {throughput:.2f} texts/s)"
    )
    print(
        f"\n[embeddings_batch_throughput] {len(texts)} texts in {elapsed:.3f}s "
        f"= {throughput:.2f} texts/s (阈值 {threshold_s}s)"
    )


def test_embeddings_batch_throughput_20_texts(
    embeddings_url: str,
) -> None:
    """验证 Embedding batch 20 条文本的吞吐量 (大批量场景).

    TEI MAX_BATCH_REQUESTS=4, 20 条文本分 5 批 (4*5) 发送.
    阈值: 20 条文本应在 10s 内完成 (吞吐 ≥ 2 texts/s).
    """
    texts = [f"测试文本 {i}: 人工智能研究第 {i} 章" for i in range(20)]
    threshold_s = 10.0
    headers = embeddings_auth_headers()
    batch_size = 4  # TEI MAX_BATCH_REQUESTS=4

    all_vectors: list[list[float]] = []
    with make_http_client(timeout=TEI_TIMEOUT) as client:
        start = time.perf_counter()
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            r = client.post(
                f"{embeddings_url}/embed",
                json={"inputs": batch},
                headers=headers,
            )
            assert r.status_code == 200, f"/embed 批量 20 非 200: {r.status_code} {r.text[:200]}"
            all_vectors.extend(r.json())
        elapsed = time.perf_counter() - start

    assert len(all_vectors) == 20
    throughput = 20 / elapsed if elapsed > 0 else 0
    assert elapsed < threshold_s, (
        f"批量 20 条嵌入耗时 {elapsed:.3f}s 超过阈值 {threshold_s}s (吞吐 {throughput:.2f} texts/s)"
    )
    print(
        f"\n[embeddings_batch_20_throughput] 20 texts in {elapsed:.3f}s "
        f"= {throughput:.2f} texts/s (阈值 {threshold_s}s)"
    )


def test_embeddings_concurrent_3_batches(
    embeddings_url: str,
) -> None:
    """验证 3 个并发 batch 请求 (各 4 条) 的吞吐量.

    AGENTS.md 第 7 章: 客户端并发限流 embeddings_max_concurrent=3,
    TEI MAX_BATCH_REQUESTS=4, 每批 4 条.
    3 个并发 batch 应在限流范围内, 不应触发 429.
    """
    import asyncio

    async def _run_batch(client: httpx.AsyncClient, texts: list[str]) -> float:
        headers = embeddings_auth_headers()
        start = time.perf_counter()
        r = await client.post(
            f"{embeddings_url}/embed",
            json={"inputs": texts},
            headers=headers,
        )
        elapsed = time.perf_counter() - start
        assert r.status_code == 200, f"并发 batch 非 200: {r.status_code} {r.text[:200]}"
        return elapsed

    async def _run_all() -> list[float]:
        async with httpx.AsyncClient(timeout=TEI_TIMEOUT) as client:
            tasks = [_run_batch(client, [f"并发测试 {i}-{j}" for j in range(4)]) for i in range(3)]
            return await asyncio.gather(*tasks)

    threshold_s = 15.0
    elapsed_list = asyncio.run(_run_all())
    max_elapsed = max(elapsed_list)
    assert max_elapsed < threshold_s, (
        f"3 并发 batch 最大耗时 {max_elapsed:.3f}s 超过阈值 {threshold_s}s "
        f"(全部: {[f'{t:.3f}' for t in elapsed_list]})"
    )
    print(
        f"\n[embeddings_concurrent_3] max={max_elapsed:.3f}s "
        f"avg={sum(elapsed_list) / len(elapsed_list):.3f}s "
        f"(阈值 {threshold_s}s)"
    )


# ========== Qdrant 检索耗时 ==========


def test_qdrant_search_under_5s(
    embeddings_url: str,
    qdrant_url: str,
    perf_thresholds: dict[str, float],
) -> None:
    """验证 Qdrant 检索耗时 < 5s (含 namespace 过滤, 不含 embedding 时间).

    AGENTS.md 第 7 章:
    - 单一集合 agents, distance=Cosine, vector_size=1024
    - 检索时显式传目标 namespace 列表, 禁止无 namespace 过滤的全集合扫描
    - 测试数据隔离: namespace=test_*

    阈值: Qdrant 搜索应在 5s 内完成 (实际通常 <100ms).
    本测试宽松到 5s 容忍 CI 环境抖动.
    """
    threshold_s = 5.0  # Qdrant 检索耗时阈值 (宽松, 容忍 CI 抖动)
    embed_headers = embeddings_auth_headers()
    qdrant_headers = qdrant_auth_headers()
    test_namespace = _unique_namespace("perf_rag_search")

    # 步骤 1: 预生成查询向量 (不计入检索耗时)
    with make_http_client(timeout=TEI_TIMEOUT) as client:
        r = client.post(
            f"{embeddings_url}/embed",
            json={"inputs": ["人工智能在医疗领域的应用"]},
            headers=embed_headers,
        )
        assert r.status_code == 200, f"预生成向量失败: {r.status_code} {r.text[:200]}"
        query_vector = r.json()[0]

    # 步骤 2: 计时 Qdrant 搜索 (带 namespace 过滤)
    search_payload = {
        "vector": query_vector,
        "limit": 5,
        "with_payload": False,
        "filter": {"must": [{"key": "namespace", "match": {"value": test_namespace}}]},
    }

    with make_http_client(timeout=QDRANT_TIMEOUT) as client:
        start = time.perf_counter()
        r = client.post(
            f"{qdrant_url}/collections/{QDRANT_COLLECTION}/points/search",
            json=search_payload,
            headers=qdrant_headers,
        )
        elapsed = time.perf_counter() - start

    # 集合可能不存在 (Agent 未启动), 跳过
    if r.status_code == 404:
        pytest.skip(f"Qdrant 集合 {QDRANT_COLLECTION} 不存在, 跳过检索耗时测试")
    assert r.status_code == 200, f"Qdrant 搜索非 200: {r.status_code} {r.text[:200]}"
    result = r.json()
    assert "result" in result
    assert elapsed < threshold_s, f"Qdrant 检索耗时 {elapsed:.3f}s 超过阈值 {threshold_s}s"
    print(
        f"\n[qdrant_search_under_5s] {elapsed:.3f}s "
        f"hits={len(result['result'])} (阈值 {threshold_s}s)"
    )


def test_qdrant_search_with_large_namespace_filter(
    embeddings_url: str,
    qdrant_url: str,
) -> None:
    """验证 Qdrant 多 namespace 过滤检索耗时 (should OR 多命名空间).

    AGENTS.md 第 7 章: 检索时显式传目标 namespace 列表,
    多 namespace 用 should OR 过滤 (共享 + 当前用户私有).
    """
    embed_headers = embeddings_auth_headers()
    qdrant_headers = qdrant_auth_headers()

    # 预生成查询向量
    with make_http_client(timeout=TEI_TIMEOUT) as client:
        r = client.post(
            f"{embeddings_url}/embed",
            json={"inputs": ["研究型查询测试向量"]},
            headers=embed_headers,
        )
        assert r.status_code == 200
        query_vector = r.json()[0]

    # 多 namespace should OR 过滤
    namespaces = [
        _unique_namespace("perf_rag_ns_0"),
        _unique_namespace("perf_rag_ns_1"),
        _unique_namespace("perf_rag_ns_2"),
    ]
    search_payload = {
        "vector": query_vector,
        "limit": 5,
        "with_payload": False,
        "filter": {"should": [{"key": "namespace", "match": {"value": ns}} for ns in namespaces]},
    }

    threshold_s = 5.0
    with make_http_client(timeout=QDRANT_TIMEOUT) as client:
        start = time.perf_counter()
        r = client.post(
            f"{qdrant_url}/collections/{QDRANT_COLLECTION}/points/search",
            json=search_payload,
            headers=qdrant_headers,
        )
        elapsed = time.perf_counter() - start

    if r.status_code == 404:
        pytest.skip(f"Qdrant 集合 {QDRANT_COLLECTION} 不存在")
    assert r.status_code == 200, f"Qdrant 多 namespace 搜索非 200: {r.status_code} {r.text[:200]}"
    assert elapsed < threshold_s, (
        f"Qdrant 多 namespace 检索耗时 {elapsed:.3f}s 超过阈值 {threshold_s}s"
    )
    print(
        f"\n[qdrant_search_multi_ns] {elapsed:.3f}s "
        f"namespaces={len(namespaces)} (阈值 {threshold_s}s)"
    )


# ========== 端到端 RAG 检索质量 (相似度) ==========


def test_rag_retrieval_relevance(
    embeddings_url: str,
    qdrant_url: str,
) -> None:
    """验证 RAG 检索质量: 相似查询应召回高相似度结果.

    流程:
    1. 写入测试文档到 Qdrant (namespace=test_*)
    2. 用相似查询向量搜索
    3. 验证 top-1 相似度 > 0.5 (Cosine 相似度)

    AGENTS.md 第 7 章: distance=Cosine, score_threshold=0.3 (默认).
    相似查询应能召回写入的文档, 相似度 > 0.5 (远高于阈值).
    """
    embed_headers = embeddings_auth_headers()
    qdrant_headers = qdrant_auth_headers()
    test_namespace = _unique_namespace("perf_rag_relevance")

    # 步骤 1: 写入测试文档
    docs = [
        "人工智能在医疗影像诊断中的应用",
        "Python 异步编程与 asyncio 实践",
        "中文检索增强生成 RAG 技术",
    ]
    with make_http_client(timeout=TEI_TIMEOUT) as client:
        r = client.post(
            f"{embeddings_url}/embed",
            json={"inputs": docs},
            headers=embed_headers,
        )
        assert r.status_code == 200
        doc_vectors = r.json()
        assert len(doc_vectors) == len(docs)

    # 写入 Qdrant
    points = []
    for _i, (doc, vec) in enumerate(zip(docs, doc_vectors, strict=True)):
        points.append(
            {
                "id": str(
                    uuid.uuid5(
                        uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8"),
                        f"{test_namespace}:{doc}",
                    )
                ),
                "vector": vec,
                "payload": {
                    "content": doc,
                    "metadata": {"source": "perf_test"},
                    "namespace": test_namespace,
                },
            }
        )

    with make_http_client(timeout=QDRANT_TIMEOUT) as client:
        upsert_r = client.put(
            f"{qdrant_url}/collections/{QDRANT_COLLECTION}/points?wait=true",
            json={"points": points},
            headers=qdrant_headers,
        )
        if upsert_r.status_code == 404:
            pytest.skip(f"Qdrant 集合 {QDRANT_COLLECTION} 不存在")
        assert upsert_r.status_code == 200, (
            f"Qdrant upsert 非 200: {upsert_r.status_code} {upsert_r.text[:200]}"
        )

    try:
        # 步骤 2: 用相似查询搜索
        query_text = "AI 在医疗诊断中的技术"  # 与 docs[0] 语义相似
        with make_http_client(timeout=TEI_TIMEOUT) as client:
            r = client.post(
                f"{embeddings_url}/embed",
                json={"inputs": [query_text]},
                headers=embed_headers,
            )
            assert r.status_code == 200
            query_vector = r.json()[0]

        search_payload = {
            "vector": query_vector,
            "limit": 5,
            "with_payload": True,
            "filter": {"must": [{"key": "namespace", "match": {"value": test_namespace}}]},
        }

        with make_http_client(timeout=QDRANT_TIMEOUT) as client:
            start = time.perf_counter()
            r = client.post(
                f"{qdrant_url}/collections/{QDRANT_COLLECTION}/points/search",
                json=search_payload,
                headers=qdrant_headers,
            )
            elapsed = time.perf_counter() - start

        assert r.status_code == 200, f"Qdrant 搜索非 200: {r.status_code} {r.text[:200]}"
        result = r.json()
        hits = result["result"]
        assert len(hits) >= 1, "RAG 检索无命中"

        # 步骤 3: 验证 top-1 相似度
        top1 = hits[0]
        top1_score = top1["score"]
        top1_content = top1["payload"]["content"]

        # 相似查询应召回 docs[0] (语义最相似)
        assert top1_score > 0.5, f"top-1 相似度 {top1_score:.3f} 低于 0.5 (content={top1_content})"
        print(
            f"\n[rag_relevance] top1_score={top1_score:.3f} "
            f"top1='{top1_content[:30]}...' search={elapsed:.3f}s"
        )

    finally:
        # 清理: 删除测试 namespace 数据 (AGENTS.md 第 13 章: 测试数据隔离)
        try:
            with make_http_client(timeout=QDRANT_TIMEOUT) as client:
                client.post(
                    f"{qdrant_url}/collections/{QDRANT_COLLECTION}/points/delete",
                    json={
                        "filter": {
                            "must": [
                                {
                                    "key": "namespace",
                                    "match": {"value": test_namespace},
                                }
                            ]
                        }
                    },
                    headers=qdrant_headers,
                )
        except Exception:  # noqa: BLE001
            pass
