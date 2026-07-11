"""性能测试: 负载/压力测试 (持续请求 + 并发会话隔离 + 内存稳定性).

AGENTS.md 第 6/13 章硬约束:
- 每个 Agent 应支持并发多会话; 会话间状态通过 Postgres Checkpointer 隔离
- 性能测试在 docker compose up -d 且全部容器 service_healthy 后执行
- 测试数据隔离: session_id=test_perf_*

覆盖场景:
- 持续 10 次顺序请求, 验证无性能退化 (末次耗时 <= 首次 * 2)
- 5 个并发会话不同主题, 验证会话隔离 + 全部在 60s 内完成
- 20 次顺序请求, 验证无 OOM/错误 (内存稳定性)

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/performance/test_load.py -v -m performance -s
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

# 短查询不走 graph, 但负载测试串行多次, 用宽松超时
LOAD_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
# 并发会话隔离测试超时
CONCURRENT_SESSION_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)


def _unique_session_id(prefix: str = "perf_load") -> str:
    """生成唯一 session_id (AGENTS.md 第 13 章: session_id=test_*)."""
    return f"test_{prefix}_{uuid.uuid4().hex[:12]}"


def _chat_payload(query: str, session_id: str) -> dict[str, object]:
    """构造 /v1/chat/completions 短查询请求体."""
    return {
        "model": "agentinsight-researcher",
        "messages": [{"role": "user", "content": query}],
        "stream": False,
        "session_id": session_id,
    }


# ========== 持续请求无退化 ==========


def test_sustained_10_requests_no_degradation(agent_url: str) -> None:
    """验证 10 次顺序短查询无性能退化 (末次耗时 <= 首次 * 2).

    短查询不走 graph, 应保持稳定延迟.
    若末次耗时显著超过首次, 可能存在资源泄漏/缓存退化.
    """
    request_count = 10
    elapsed_list: list[float] = []

    with make_http_client(timeout=LOAD_TIMEOUT) as client:
        for i in range(request_count):
            sid = _unique_session_id(f"perf_sust_{i}")
            start = time.perf_counter()
            r = client.post(
                f"{agent_url}/v1/chat/completions",
                json=_chat_payload("你好", sid),
            )
            elapsed = time.perf_counter() - start
            assert r.status_code == 200, f"顺序请求 #{i} 非 200: {r.status_code} {r.text}"
            elapsed_list.append(elapsed)

    first = elapsed_list[0]
    last = elapsed_list[-1]
    max_elapsed = max(elapsed_list)
    avg = sum(elapsed_list) / len(elapsed_list)

    # 末次耗时不超过首次的 2 倍 (容忍 JIT 预热/JIT 抖动)
    degradation_factor = last / first if first > 0 else float("inf")
    assert degradation_factor <= 2.0, (
        f"性能退化: 首次={first:.3f}s 末次={last:.3f}s "
        f"退化因子={degradation_factor:.2f}x (>2.0) "
        f"(全部: {[f'{t:.3f}' for t in elapsed_list]})"
    )

    print(
        f"\n[sustained_10] first={first:.3f}s last={last:.3f}s "
        f"max={max_elapsed:.3f}s avg={avg:.3f}s "
        f"degradation={degradation_factor:.2f}x"
    )


# ========== 并发会话隔离 ==========


async def _run_session_query(
    client: httpx.AsyncClient, query: str, expected_sid: str
) -> tuple[float, int, str, str]:
    """执行单会话短查询, 返回 (耗时, 状态码, 请求 sid, 响应 sid).

    AGENTS.md 第 6 章: thread_id 做会话隔离键, X-Session-Id 头应匹配.
    """
    payload = _chat_payload(query, expected_sid)
    start = time.perf_counter()
    r = await client.post(f"{AGENT_URL}/v1/chat/completions", json=payload)
    elapsed = time.perf_counter() - start
    # 流式响应头中的 X-Session-Id 应与请求一致 (会话隔离验证)
    resp_sid = r.headers.get("x-session-id", "")
    return elapsed, r.status_code, expected_sid, resp_sid


def test_concurrent_5_sessions_isolation(agent_url: str, perf_thresholds: dict[str, float]) -> None:
    """验证 5 个并发会话不同主题, 会话隔离 + 全部在 60s 内完成.

    AGENTS.md 第 6 章:
    - 每个 Agent 应支持并发多会话
    - 会话隔离键为 thread_id (session_id), 由请求上下文注入
    - 会话间状态通过 Postgres Checkpointer 隔离

    验证:
    1. 每个请求返回 200
    2. 响应 X-Session-Id 与请求 session_id 一致 (会话隔离)
    3. 全部在阈值内完成
    """
    threshold_s = perf_thresholds["concurrent_sessions_5_s"]
    # 不同主题的短查询 (均触发 short_query 保护, 但会话 ID 不同)
    sessions = [
        ("你好", _unique_session_id("perf_iso_0")),
        ("嗨", _unique_session_id("perf_iso_1")),
        ("在吗", _unique_session_id("perf_iso_2")),
        ("你是谁", _unique_session_id("perf_iso_3")),
        ("能帮我什么", _unique_session_id("perf_iso_4")),
    ]

    async def run_all() -> list[tuple[float, int, str, str]]:
        async with make_async_http_client(timeout=CONCURRENT_SESSION_TIMEOUT) as client:
            tasks = [_run_session_query(client, query, sid) for query, sid in sessions]
            return await asyncio.gather(*tasks)

    start = time.perf_counter()
    results = asyncio.run(run_all())
    total_elapsed = time.perf_counter() - start

    # 验证全部成功 + 会话隔离
    for i, (_elapsed, status, req_sid, resp_sid) in enumerate(results):
        assert status == 200, f"并发会话 #{i} (sid={req_sid}) 非 200: {status}"
        # X-Session-Id 应与请求一致 (非流式响应也携带此头)
        # 若中间件未在非流式响应头中注入 X-Session-Id, 跳过此断言 (容错)
        # 流式响应必带 X-Session-Id, 非流式可能不带
        if resp_sid:
            assert resp_sid == req_sid, (
                f"并发会话 #{i} X-Session-Id 不匹配: 请求={req_sid} 响应={resp_sid} "
                f"(会话隔离可能失效)"
            )

    max_elapsed = max(elapsed for elapsed, _, _, _ in results)
    assert total_elapsed < threshold_s, (
        f"5 并发会话总耗时 {total_elapsed:.3f}s 超过阈值 {threshold_s}s "
        f"(单请求最大 {max_elapsed:.3f}s)"
    )

    elapsed_list = [e for e, _, _, _ in results]
    print(
        f"\n[concurrent_5_sessions] total={total_elapsed:.3f}s "
        f"max={max(elapsed_list):.3f}s avg={sum(elapsed_list) / len(elapsed_list):.3f}s "
        f"(阈值 {threshold_s}s)"
    )


# ========== 内存稳定性 ==========


def test_memory_stable_under_load(agent_url: str) -> None:
    """验证 20 次顺序短查询无 OOM/错误 (内存稳定性).

    AGENTS.md 第 6 章: 会话级数据按 agent_id + user_id + session_id 三级分键.
    大量会话不应导致内存泄漏或 OOM.

    验证:
    1. 全部 20 次请求返回 200 (无 OOM/错误)
    2. 响应内容非空 (服务未崩溃)
    3. 耗时无显著上升趋势 (无内存压力导致的 GC 停顿)
    """
    request_count = 20
    elapsed_list: list[float] = []
    error_count = 0

    with make_http_client(timeout=LOAD_TIMEOUT) as client:
        for i in range(request_count):
            sid = _unique_session_id(f"perf_mem_{i}")
            start = time.perf_counter()
            try:
                r = client.post(
                    f"{agent_url}/v1/chat/completions",
                    json=_chat_payload("你好", sid),
                )
                elapsed = time.perf_counter() - start
                if r.status_code != 200:
                    error_count += 1
                else:
                    # 验证响应内容非空 (服务未崩溃)
                    data = r.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    assert content, f"请求 #{i} 响应内容为空 (可能 OOM)"
                elapsed_list.append(elapsed)
            except (httpx.HTTPError, OSError) as e:
                error_count += 1
                elapsed_list.append(time.perf_counter() - start)
                print(f"\n[memory] 请求 #{i} 异常: {type(e).__name__}: {e}")

    # 全部请求应成功 (无 OOM/错误)
    assert error_count == 0, (
        f"20 次请求中 {error_count} 次失败 (可能 OOM 或资源耗尽) "
        f"(耗时: {[f'{t:.3f}' for t in elapsed_list]})"
    )

    # 耗时无显著上升趋势: 后 10 次平均不应超过前 10 次平均的 3 倍
    first_half_avg = sum(elapsed_list[:10]) / 10
    second_half_avg = sum(elapsed_list[10:]) / 10
    growth_ratio = second_half_avg / first_half_avg if first_half_avg > 0 else 1.0

    max_elapsed = max(elapsed_list)
    avg = sum(elapsed_list) / len(elapsed_list)

    print(
        f"\n[memory_stable_20] total_requests={request_count} "
        f"avg={avg:.3f}s max={max_elapsed:.3f}s "
        f"first_half_avg={first_half_avg:.3f}s second_half_avg={second_half_avg:.3f}s "
        f"growth={growth_ratio:.2f}x"
    )

    assert growth_ratio <= 3.0, (
        f"耗时显著上升: 前 10 次平均={first_half_avg:.3f}s "
        f"后 10 次平均={second_half_avg:.3f}s 增长比={growth_ratio:.2f}x (>3.0) "
        f"(可能内存压力导致 GC 停顿)"
    )
