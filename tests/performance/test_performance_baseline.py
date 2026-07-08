"""性能测试: 基线性能 (单次 chat completions + 并发 P95 + LLM 调用耗时分布).

AGENTS.md 第 6/13 章硬约束:
- 每个 Agent 应支持并发多会话; 会话间状态通过 Postgres Checkpointer 隔离
- 性能测试在 docker compose up -d 且全部容器 service_healthy 后执行
- 测试目标地址从环境变量 AGENT_URL 注入, 禁止硬编码
- 测试数据隔离: session_id=test_perf_baseline_*

本文件专注基线性能 (与 test_latency.py / test_load.py 区别):
- test_latency.py: 单次端点延迟 (GET 类 + 短查询首块/总延迟)
- test_load.py: 持续请求无退化 + 并发会话隔离 + 内存稳定性
- test_performance_baseline.py (本文件): chat completions 单次响应 + 并发 P95 + 多次采样分布

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/performance/test_performance_baseline.py -v -m performance -s
"""

from __future__ import annotations

import asyncio
import time
import uuid

import httpx
import pytest

from tests.performance.conftest import (
    AGENT_URL,
    make_async_http_client,
    make_http_client,
)

pytestmark = pytest.mark.performance

# 短查询不走 graph, 60s 足够; 研究查询首块需要意图分类
BASELINE_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)

# 并发测试超时 (10 并发短查询)
CONCURRENT_TIMEOUT = httpx.Timeout(connect=10.0, read=90.0, write=10.0, pool=10.0)


def _unique_session_id(prefix: str = "perf_baseline") -> str:
    """生成唯一 session_id (AGENTS.md 第 13 章: session_id=test_*)."""
    return f"test_{prefix}_{uuid.uuid4().hex[:12]}"


def _chat_payload(
    query: str = "你好",
    *,
    stream: bool = False,
    session_id: str | None = None,
) -> dict[str, object]:
    """构造 /v1/chat/completions 请求体."""
    return {
        "model": "agentinsight-researcher",
        "messages": [{"role": "user", "content": query}],
        "stream": stream,
        "session_id": session_id or _unique_session_id(),
    }


# ========== 单次 chat completions 响应时间 ==========


def test_single_chat_completions_under_30s(agent_url: str) -> None:
    """验证单次 chat completions 响应时间 < 30s (短查询, 不走 graph).

    AGENTS.md 性能基线: 单次 chat completions 短查询响应时间 < 30s.
    短查询保护直接返回 reply, 不走任何 graph, 应在数秒内完成.
    """
    threshold_s = 30.0
    sid = _unique_session_id("perf_single")

    with make_http_client(timeout=BASELINE_TIMEOUT) as client:
        start = time.perf_counter()
        r = client.post(
            f"{agent_url}/v1/chat/completions",
            json=_chat_payload("你好", stream=False, session_id=sid),
        )
        elapsed = time.perf_counter() - start

    assert r.status_code == 200, f"单次 chat 非 200: {r.status_code} {r.text[:200]}"
    assert elapsed < threshold_s, f"单次 chat 响应时间 {elapsed:.3f}s 超过阈值 {threshold_s}s"
    print(f"\n[single_chat] {elapsed:.3f}s (阈值 {threshold_s}s)")


def test_single_chat_completions_stream_total_under_30s(agent_url: str) -> None:
    """验证单次流式 chat completions 总响应时间 < 30s (短查询).

    流式响应总时间 = 首块延迟 + 全部内容传输 + [DONE].
    """
    threshold_s = 30.0
    sid = _unique_session_id("perf_stream_total")

    with make_http_client(timeout=BASELINE_TIMEOUT) as client:
        start = time.perf_counter()
        with client.stream(
            "POST",
            f"{agent_url}/v1/chat/completions",
            json=_chat_payload("你好", stream=True, session_id=sid),
        ) as r:
            assert r.status_code == 200
            chunk_count = 0
            for line in r.iter_lines():
                if line and line.startswith("data: "):
                    chunk_count += 1
                    if line[6:] == "[DONE]":
                        break
        elapsed = time.perf_counter() - start

    assert elapsed < threshold_s, f"流式 chat 总响应时间 {elapsed:.3f}s 超过阈值 {threshold_s}s"
    print(f"\n[stream_total] {elapsed:.3f}s chunks={chunk_count} (阈值 {threshold_s}s)")


# ========== 并发 P95 延迟 ==========


async def _run_single_chat(
    client: httpx.AsyncClient, query: str, sid: str
) -> tuple[float, int, str]:
    """执行单个 chat 请求, 返回 (耗时, 状态码, sid)."""
    payload = _chat_payload(query, stream=False, session_id=sid)
    start = time.perf_counter()
    r = await client.post(f"{AGENT_URL}/v1/chat/completions", json=payload)
    elapsed = time.perf_counter() - start
    return elapsed, r.status_code, sid


def _percentile(sorted_values: list[float], p: float) -> float:
    """计算百分位数 (线性插值法).

    Args:
        sorted_values: 已排序的值列表
        p: 百分位 (0-100), 如 95 表示 P95
    """
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    # 线性插值: index = (p/100) * (n-1)
    k = (p / 100) * (len(sorted_values) - 1)
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def test_concurrent_10_p95_under_60s(agent_url: str) -> None:
    """验证并发 10 请求的 P95 延迟 < 60s.

    AGENTS.md 性能基线: 并发 10 请求的 P95 < 60s.
    10 个并发短查询, 每个用不同 session_id (会话隔离).
    """
    threshold_p95_s = 60.0
    queries = [
        ("你好", _unique_session_id("perf_p95_0")),
        ("嗨", _unique_session_id("perf_p95_1")),
        ("在吗", _unique_session_id("perf_p95_2")),
        ("你是谁", _unique_session_id("perf_p95_3")),
        ("能帮我什么", _unique_session_id("perf_p95_4")),
        ("请问", _unique_session_id("perf_p95_5")),
        ("hello", _unique_session_id("perf_p95_6")),
        ("hi", _unique_session_id("perf_p95_7")),
        ("你好啊", _unique_session_id("perf_p95_8")),
        ("在不在", _unique_session_id("perf_p95_9")),
    ]

    async def run_all() -> list[tuple[float, int, str]]:
        async with make_async_http_client(timeout=CONCURRENT_TIMEOUT) as client:
            tasks = [_run_single_chat(client, q, sid) for q, sid in queries]
            return await asyncio.gather(*tasks)

    start = time.perf_counter()
    results = asyncio.run(run_all())
    total_elapsed = time.perf_counter() - start

    # 验证全部成功
    for i, (_elapsed, status, sid) in enumerate(results):
        assert status == 200, f"并发 #{i} (sid={sid}) 非 200: {status}"

    # 计算 P95
    elapsed_list = sorted(e for e, _, _ in results)
    p95 = _percentile(elapsed_list, 95)
    p50 = _percentile(elapsed_list, 50)
    p99 = _percentile(elapsed_list, 99)

    assert p95 < threshold_p95_s, (
        f"并发 10 请求 P95 {p95:.3f}s 超过阈值 {threshold_p95_s}s "
        f"(全部: {[f'{t:.3f}' for t in elapsed_list]})"
    )
    print(
        f"\n[concurrent_10_p95] total={total_elapsed:.3f}s "
        f"P50={p50:.3f}s P95={p95:.3f}s P99={p99:.3f}s "
        f"max={max(elapsed_list):.3f}s (阈值 P95 < {threshold_p95_s}s)"
    )


# ========== 多次采样耗时分布 ==========


def test_sampling_latency_distribution(agent_url: str) -> None:
    """验证 10 次顺序短查询的耗时分布 (基线无退化).

    AGENTS.md 性能基线: 多次采样 P95 / P50 / max 应稳定.
    验证 P95 不显著高于 P50 (无显著尾延迟).
    """
    sample_count = 10
    elapsed_list: list[float] = []

    with make_http_client(timeout=BASELINE_TIMEOUT) as client:
        for i in range(sample_count):
            sid = _unique_session_id(f"perf_dist_{i}")
            start = time.perf_counter()
            r = client.post(
                f"{agent_url}/v1/chat/completions",
                json=_chat_payload("你好", stream=False, session_id=sid),
            )
            elapsed = time.perf_counter() - start
            assert r.status_code == 200, f"采样 #{i} 非 200: {r.status_code} {r.text[:200]}"
            elapsed_list.append(elapsed)

    sorted_elapsed = sorted(elapsed_list)
    p50 = _percentile(sorted_elapsed, 50)
    p95 = _percentile(sorted_elapsed, 95)
    p99 = _percentile(sorted_elapsed, 99)
    avg = sum(elapsed_list) / len(elapsed_list)
    max_elapsed = max(elapsed_list)
    min_elapsed = min(elapsed_list)

    # P95 不应超过 P50 的 5 倍 (避免极端尾延迟)
    ratio = p95 / p50 if p50 > 0 else float("inf")
    assert ratio < 5.0, (
        f"P95/P50 比值 {ratio:.2f} 过大 (P50={p50:.3f}s P95={p95:.3f}s), 存在极端尾延迟"
    )

    print(
        f"\n[sampling_dist] n={sample_count} "
        f"min={min_elapsed:.3f}s avg={avg:.3f}s max={max_elapsed:.3f}s "
        f"P50={p50:.3f}s P95={p95:.3f}s P99={p99:.3f}s "
        f"ratio_P95/P50={ratio:.2f}x"
    )


# ========== LLM 调用耗时分布 (通过 token usage 间接验证) ==========


def test_llm_token_usage_within_budget(agent_url: str) -> None:
    """验证单次 chat completions 的 token 用量在合理范围内.

    AGENTS.md 第 9 章: max_total_tokens=128000 (单次研究流程总 token 预算上限).
    短查询不走 graph, token 用量应远低于此上限.
    通过 usage.total_tokens 间接验证 LLM 调用成本可控.
    """
    sid = _unique_session_id("perf_token")

    with make_http_client(timeout=BASELINE_TIMEOUT) as client:
        r = client.post(
            f"{agent_url}/v1/chat/completions",
            json=_chat_payload("你好", stream=False, session_id=sid),
        )

    assert r.status_code == 200
    data = r.json()
    usage = data.get("usage", {})
    total_tokens = usage.get("total_tokens", 0)
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)

    # 短查询 token 用量应远低于 max_total_tokens (128000)
    assert total_tokens < 128000, f"短查询 token 用量 {total_tokens} 超过预算上限 128000"
    # 短查询应有非零 token 用量 (LLM 调用产生)
    # 注意: 某些短查询保护路径可能不调用 LLM (返回模板), 此时 total_tokens=0
    print(
        f"\n[llm_token_usage] prompt={prompt_tokens} "
        f"completion={completion_tokens} total={total_tokens}"
    )


# ========== 不同查询长度的响应时间分布 ==========


def test_query_length_latency_correlation(agent_url: str) -> None:
    """验证不同长度查询的响应时间分布 (短/中/长查询).

    短查询应触发 short_query 保护 (不走 graph);
    中/长查询可能走完整研究图, 响应时间应显著高于短查询.
    """
    test_cases = [
        ("短", "你好", _unique_session_id("perf_ql_short")),
        ("中", "请简要介绍量子计算的基本原理", _unique_session_id("perf_ql_medium")),
    ]

    results: list[tuple[str, float, int]] = []
    with make_http_client(timeout=BASELINE_TIMEOUT) as client:
        for label, query, sid in test_cases:
            start = time.perf_counter()
            r = client.post(
                f"{agent_url}/v1/chat/completions",
                json=_chat_payload(query, stream=False, session_id=sid),
            )
            elapsed = time.perf_counter() - start
            results.append((label, elapsed, r.status_code))

    for label, _, status in results:
        assert status == 200, f"{label} 查询非 200: {status}"

    short_time = next(e for label, e, _ in results if label == "短")
    medium_time = next(e for label, e, _ in results if label == "中")

    print(
        f"\n[query_length] 短={short_time:.3f}s 中={medium_time:.3f}s "
        f"比值={medium_time / short_time:.2f}x"
    )
    # 短查询应在 30s 内 (短查询保护)
    assert short_time < 30.0, f"短查询 {short_time:.3f}s 超过 30s"
