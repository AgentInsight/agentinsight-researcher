"""性能测试: 吞吐量 (Embeddings + Qdrant + 并发短查询).

AGENTS.md 第 7/13 章硬约束:
- Embeddings: bge-base-zh-v1.5 (固定 768 维), TEI 服务 /embed 接口
- Qdrant: 单一集合 agents, distance=Cosine, namespace 过滤隔离
- 性能测试在 docker compose up -d 且全部容器 service_healthy 后执行
- 测试数据隔离: namespace=test_* + session_id=test_*

覆盖场景:
- POST /embed 单条文本延迟 (TEI 直连)
- POST /embed 批量 10 条文本延迟 (TEI 直连)
- Qdrant 搜索延迟 (含 namespace 过滤, 不含 embedding 时间)
- 5 并发短查询 (POST /v1/chat/completions)
- 10 并发短查询 (POST /v1/chat/completions)

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    set EMBEDDINGS_URL=http://127.0.0.1:8088
    set QDRANT_URL=http://127.0.0.1:6333
    pytest tests/performance/test_throughput.py -v -m performance -s
"""

from __future__ import annotations

import asyncio
import time
import uuid

import httpx
import pytest

from tests.performance.conftest import (
    AGENT_URL,
    QDRANT_COLLECTION,
    embeddings_auth_headers,
    make_async_http_client,
    make_http_client,
    qdrant_auth_headers,
)

pytestmark = pytest.mark.performance

# TEI 服务首次加载模型较慢, 给足超时
TEI_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)
# Qdrant 搜索超时
QDRANT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
# 并发短查询超时 (短查询不走 graph, 但并发时 TEI/中间件可能排队)
CONCURRENT_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)

# bge-base-zh-v1.5 固定维度
VECTOR_DIM = 768


def _unique_session_id(prefix: str = "perf_thr") -> str:
    """生成唯一 session_id (AGENTS.md 第 13 章: session_id=test_*)."""
    return f"test_{prefix}_{uuid.uuid4().hex[:12]}"


# ========== Embeddings (TEI 直连) 延迟 ==========


def test_embeddings_single_latency(embeddings_url: str, perf_thresholds: dict[str, float]) -> None:
    """验证单条文本嵌入延迟 < 2s (TEI /embed 单条).

    AGENTS.md 第 7 章: Embeddings bge-base-zh-v1.5, 固定 768 维.
    """
    threshold_s = perf_thresholds["embeddings_single_s"]
    headers = embeddings_auth_headers()

    with make_http_client(timeout=TEI_TIMEOUT) as client:
        start = time.perf_counter()
        r = client.post(
            f"{embeddings_url}/embed",
            json={"inputs": ["人工智能在医疗领域的应用"]},
            headers=headers,
        )
        elapsed = time.perf_counter() - start

    assert r.status_code == 200, f"/embed 单条非 200: {r.status_code} {r.text}"
    vectors = r.json()
    assert isinstance(vectors, list) and len(vectors) == 1
    assert len(vectors[0]) == VECTOR_DIM, f"维度非 {VECTOR_DIM}: {len(vectors[0])}"
    assert elapsed < threshold_s, f"单条嵌入延迟 {elapsed:.3f}s 超过阈值 {threshold_s}s"
    print(f"\n[embeddings_single] {elapsed:.3f}s (阈值 {threshold_s}s)")


def test_embeddings_batch_10_latency(
    embeddings_url: str, perf_thresholds: dict[str, float]
) -> None:
    """验证批量 10 条文本嵌入延迟 < 5s (TEI /embed 批量).

    AGENTS.md 第 7 章: TEI 支持批量嵌入, 客户端按 embeddings_max_client_batch_size 分批.
    TEI MAX_BATCH_REQUESTS=4, 10 条文本分 3 批 (4+4+2) 发送.
    """
    threshold_s = perf_thresholds["embeddings_batch_10_s"]
    headers = embeddings_auth_headers()
    texts = [
        "人工智能在医疗领域的应用前景",
        "Python 异步编程与 asyncio 实践",
        "中文检索增强生成技术 RAG",
        "LangGraph 状态机编排多 Agent 系统",
        "Qdrant 向量数据库的混合检索策略",
        "大语言模型在金融风控中的应用",
        "BM25 与向量检索的 RRF 融合算法",
        "bge-base-zh-v1.5 嵌入模型性能评测",
        "MCP 协议在 AI Agent 工具调用中的实践",
        "PostgreSQL Checkpointer 会话持久化方案",
    ]
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
            assert r.status_code == 200, f"/embed 批量非 200: {r.status_code} {r.text}"
            all_vectors.extend(r.json())
        elapsed = time.perf_counter() - start

    assert len(all_vectors) == len(texts)
    for i, vec in enumerate(all_vectors):
        assert len(vec) == VECTOR_DIM, f"第 {i} 个向量维度非 {VECTOR_DIM}: {len(vec)}"
    assert elapsed < threshold_s, f"批量 10 条嵌入延迟 {elapsed:.3f}s 超过阈值 {threshold_s}s"
    print(f"\n[embeddings_batch_10] {elapsed:.3f}s (阈值 {threshold_s}s)")


# ========== Qdrant 搜索延迟 ==========


def test_qdrant_search_latency(
    embeddings_url: str, qdrant_url: str, perf_thresholds: dict[str, float]
) -> None:
    """验证 Qdrant 搜索延迟 < 1s (不含 embedding 时间, 含 namespace 过滤).

    AGENTS.md 第 7 章:
    - 单一集合 agents, distance=Cosine, vector_size=768
    - 检索时显式传目标 namespace 列表, 禁止无 namespace 过滤的全集合扫描
    - 测试数据隔离: namespace=test_*

    流程: 先 embed 查询 (不计入计时), 再计时 Qdrant search.
    """
    threshold_s = perf_thresholds["qdrant_search_s"]
    embed_headers = embeddings_auth_headers()
    qdrant_headers = qdrant_auth_headers()
    test_namespace = f"test_perf_qdrant_{uuid.uuid4().hex[:8]}"

    # 步骤 1: 预生成查询向量 (不计入 Qdrant 搜索计时)
    with make_http_client(timeout=TEI_TIMEOUT) as client:
        r = client.post(
            f"{embeddings_url}/embed",
            json={"inputs": ["性能测试查询向量"]},
            headers=embed_headers,
        )
        assert r.status_code == 200, f"预生成向量失败: {r.status_code} {r.text}"
        query_vector = r.json()[0]

    # 步骤 2: 计时 Qdrant 搜索 (带 namespace 过滤, 避免全集合扫描)
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

    # Qdrant 搜索接口可能返回 200 (集合存在) 或 404 (集合不存在)
    # 集合不存在时跳过 (Agent 未启动), 由 conftest 的 service-dependent 跳过兜底
    if r.status_code == 404:
        pytest.skip(f"Qdrant 集合 {QDRANT_COLLECTION} 不存在, 跳过搜索延迟测试")
    assert r.status_code == 200, f"Qdrant 搜索非 200: {r.status_code} {r.text}"
    result = r.json()
    assert "result" in result, f"Qdrant 响应缺少 result 字段: {result}"
    assert elapsed < threshold_s, f"Qdrant 搜索延迟 {elapsed:.3f}s 超过阈值 {threshold_s}s"
    print(f"\n[qdrant_search] {elapsed:.3f}s (阈值 {threshold_s}s) hits={len(result['result'])}")


# ========== 并发短查询 (POST /v1/chat/completions) ==========


async def _run_short_query_async(
    client: httpx.AsyncClient, query: str, threshold_s: float
) -> tuple[float, int, str]:
    """执行单个短查询, 返回 (耗时, 状态码, session_id).

    AGENTS.md 第 13 章: 每次用唯一 session_id=test_*.
    """
    sid = _unique_session_id("perf_conc")
    payload = {
        "model": "agentinsight-researcher",
        "messages": [{"role": "user", "content": query}],
        "stream": False,
        "session_id": sid,
    }
    start = time.perf_counter()
    r = await client.post(f"{AGENT_URL}/v1/chat/completions", json=payload)
    elapsed = time.perf_counter() - start
    return elapsed, r.status_code, sid


def test_concurrent_short_queries_5(agent_url: str, perf_thresholds: dict[str, float]) -> None:
    """验证 5 个并发短查询全部在 15s 内完成.

    短查询不走 graph, 应支持并发.
    AGENTS.md 第 6 章: 每个 Agent 应支持并发多会话.
    """
    threshold_s = perf_thresholds["concurrent_5_s"]
    queries = ["你好", "嗨", "在吗", "你是谁", "能帮我什么"]

    async def run_all() -> list[tuple[float, int, str]]:
        async with make_async_http_client(timeout=CONCURRENT_TIMEOUT) as client:
            tasks = [_run_short_query_async(client, q, threshold_s) for q in queries]
            return await asyncio.gather(*tasks)

    start = time.perf_counter()
    results = asyncio.run(run_all())
    total_elapsed = time.perf_counter() - start

    # 验证全部成功
    for i, (_elapsed, status, sid) in enumerate(results):
        assert status == 200, f"并发短查询 #{i} (sid={sid}) 非 200: {status}"

    max_elapsed = max(elapsed for elapsed, _, _ in results)
    assert total_elapsed < threshold_s, (
        f"5 并发短查询总耗时 {total_elapsed:.3f}s 超过阈值 {threshold_s}s "
        f"(单请求最大 {max_elapsed:.3f}s)"
    )
    elapsed_list = [e for e, _, _ in results]
    print(
        f"\n[concurrent_5] total={total_elapsed:.3f}s max={max(elapsed_list):.3f}s "
        f"avg={sum(elapsed_list) / len(elapsed_list):.3f}s (阈值 {threshold_s}s)"
    )


def test_concurrent_short_queries_10(agent_url: str, perf_thresholds: dict[str, float]) -> None:
    """验证 10 个并发短查询全部在 30s 内完成.

    短查询不走 graph, 应支持并发.
    AGENTS.md 第 6 章: 每个 Agent 应支持并发多会话.
    """
    threshold_s = perf_thresholds["concurrent_10_s"]
    queries = [
        "你好",
        "嗨",
        "在吗",
        "你是谁",
        "能帮我什么",
        "请问",
        "hello",
        "hi",
        "你好啊",
        "在不在",
    ]

    async def run_all() -> list[tuple[float, int, str]]:
        async with make_async_http_client(timeout=CONCURRENT_TIMEOUT) as client:
            tasks = [_run_short_query_async(client, q, threshold_s) for q in queries]
            return await asyncio.gather(*tasks)

    start = time.perf_counter()
    results = asyncio.run(run_all())
    total_elapsed = time.perf_counter() - start

    for i, (_elapsed, status, sid) in enumerate(results):
        assert status == 200, f"并发短查询 #{i} (sid={sid}) 非 200: {status}"

    max_elapsed = max(elapsed for elapsed, _, _ in results)
    assert total_elapsed < threshold_s, (
        f"10 并发短查询总耗时 {total_elapsed:.3f}s 超过阈值 {threshold_s}s "
        f"(单请求最大 {max_elapsed:.3f}s)"
    )
    elapsed_list = [e for e, _, _ in results]
    print(
        f"\n[concurrent_10] total={total_elapsed:.3f}s max={max(elapsed_list):.3f}s "
        f"avg={sum(elapsed_list) / len(elapsed_list):.3f}s (阈值 {threshold_s}s)"
    )
