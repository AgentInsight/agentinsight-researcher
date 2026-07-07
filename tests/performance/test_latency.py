"""性能测试: 端点延迟 (GET 类 + 短查询首块 + 研究查询首块).

AGENTS.md 第 13 章硬约束:
- 性能测试在 docker compose up -d 且全部容器 service_healthy 后执行
- 测试目标地址从环境变量 AGENT_URL 注入, 禁止硬编码
- 测试数据隔离: session_id=test_perf_*

覆盖端点:
- GET /health (健康检查, 无 LLM 调用)
- GET /v1/models (模型列表, 无 LLM 调用)
- GET /v1/mcp/system (系统 MCP 列表, 无 LLM 调用)
- GET /.well-known/agent-discovery.json (Agent 发现, 无 LLM 调用)
- POST /v1/chat/completions stream=true (短查询首块 + 研究查询首块)
- POST /v1/chat/completions stream=false (短查询 P95 总延迟)

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/performance/test_latency.py -v -m performance -s
"""

from __future__ import annotations

import json
import time
import uuid

import httpx
import pytest

from tests.performance.conftest import (
    AGENT_URL,
    make_http_client,
)

pytestmark = pytest.mark.performance

# 短查询保护不走 graph, 60s 足够; 研究首块需要意图分类, 给 60s
LATENCY_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)


def _unique_session_id(prefix: str = "perf_latency") -> str:
    """生成唯一 session_id (AGENTS.md 第 13 章: session_id=test_*)."""
    return f"test_{prefix}_{uuid.uuid4().hex[:12]}"


def _chat_payload(
    query: str = "你好",
    *,
    stream: bool = False,
    session_id: str | None = None,
    report_type: str | None = None,
) -> dict[str, object]:
    """构造 /v1/chat/completions 请求体."""
    payload: dict[str, object] = {
        "model": "agentinsight-researcher",
        "messages": [{"role": "user", "content": query}],
        "stream": stream,
        "session_id": session_id or _unique_session_id(),
    }
    if report_type is not None:
        payload["report_type"] = report_type
    return payload


# ========== GET 端点延迟 (无 LLM 调用, 应极快) ==========


def test_health_endpoint_latency(agent_url: str, perf_thresholds: dict[str, float]) -> None:
    """验证 GET /health 延迟 < 100ms (无 LLM 调用, 仅健康检查).

    AGENTS.md 第 12 章: agent 容器健康检查 GET /health.
    """
    threshold_ms = perf_thresholds["health_ms"]
    with make_http_client(timeout=LATENCY_TIMEOUT) as client:
        start = time.perf_counter()
        r = client.get(f"{agent_url}/health")
        elapsed_ms = (time.perf_counter() - start) * 1000

    assert r.status_code == 200, f"/health 非 200: {r.status_code}"
    assert elapsed_ms < threshold_ms, (
        f"/health 延迟 {elapsed_ms:.1f}ms 超过阈值 {threshold_ms}ms"
    )
    print(f"\n[health] {elapsed_ms:.1f}ms (阈值 {threshold_ms}ms)")


def test_models_endpoint_latency(agent_url: str, perf_thresholds: dict[str, float]) -> None:
    """验证 GET /v1/models 延迟 < 200ms (无 LLM 调用, 仅返回静态模型列表)."""
    threshold_ms = perf_thresholds["models_ms"]
    with make_http_client(timeout=LATENCY_TIMEOUT) as client:
        start = time.perf_counter()
        r = client.get(f"{agent_url}/v1/models")
        elapsed_ms = (time.perf_counter() - start) * 1000

    assert r.status_code == 200, f"/v1/models 非 200: {r.status_code}"
    assert elapsed_ms < threshold_ms, (
        f"/v1/models 延迟 {elapsed_ms:.1f}ms 超过阈值 {threshold_ms}ms"
    )
    print(f"\n[models] {elapsed_ms:.1f}ms (阈值 {threshold_ms}ms)")


def test_mcp_system_list_latency(agent_url: str, perf_thresholds: dict[str, float]) -> None:
    """验证 GET /v1/mcp/system 延迟 < 500ms (Postgres 查询, 无 LLM 调用).

    AGENTS.md 第 7/9 章: MCP 配置按 agent_id 隔离, 系统公用 MCP 可查看.
    """
    threshold_ms = perf_thresholds["mcp_system_ms"]
    with make_http_client(timeout=LATENCY_TIMEOUT) as client:
        start = time.perf_counter()
        r = client.get(f"{agent_url}/v1/mcp/system")
        elapsed_ms = (time.perf_counter() - start) * 1000

    assert r.status_code == 200, f"/v1/mcp/system 非 200: {r.status_code} {r.text}"
    assert elapsed_ms < threshold_ms, (
        f"/v1/mcp/system 延迟 {elapsed_ms:.1f}ms 超过阈值 {threshold_ms}ms"
    )
    print(f"\n[mcp/system] {elapsed_ms:.1f}ms (阈值 {threshold_ms}ms)")


def test_agent_discovery_latency(agent_url: str, perf_thresholds: dict[str, float]) -> None:
    """验证 GET /.well-known/agent-discovery.json 延迟 < 200ms (静态元信息, 无 LLM 调用).

    AGENTS.md 第 11 章: 公开发现端点, 无需鉴权.
    """
    threshold_ms = perf_thresholds["agent_discovery_ms"]
    with make_http_client(timeout=LATENCY_TIMEOUT) as client:
        start = time.perf_counter()
        r = client.get(f"{agent_url}/.well-known/agent-discovery.json")
        elapsed_ms = (time.perf_counter() - start) * 1000

    assert r.status_code == 200, (
        f"/.well-known/agent-discovery.json 非 200: {r.status_code}"
    )
    data = r.json()
    assert "name" in data, f"agent-discovery 缺少 name 字段: {data}"
    assert elapsed_ms < threshold_ms, (
        f"/.well-known/agent-discovery.json 延迟 {elapsed_ms:.1f}ms 超过阈值 {threshold_ms}ms"
    )
    print(f"\n[agent-discovery] {elapsed_ms:.1f}ms (阈值 {threshold_ms}ms)")


# ========== 短查询 (chitchat, 不走 graph) 延迟 ==========


def test_short_query_first_token_latency(
    agent_url: str, perf_thresholds: dict[str, float]
) -> None:
    """验证短查询流式首块延迟 < 3s (短查询保护不走 graph).

    P0-Future-06: 短查询直接返回 reply, 不走任何 graph.
    SSE 首块 = {"role": "assistant"}, 应在阈值内到达.
    """
    threshold_s = perf_thresholds["short_query_first_token_s"]
    sid = _unique_session_id("perf_sq_ft")
    first_chunk_time: float | None = None

    with make_http_client(timeout=LATENCY_TIMEOUT) as client:
        start = time.perf_counter()
        with client.stream(
            "POST",
            f"{agent_url}/v1/chat/completions",
            json=_chat_payload("你好", stream=True, session_id=sid),
        ) as r:
            assert r.status_code == 200, f"短查询流式非 200: {r.status_code}"
            for line in r.iter_lines():
                if line and line.startswith("data: "):
                    first_chunk_time = time.perf_counter() - start
                    break

    assert first_chunk_time is not None, "短查询流式未收到任何 SSE 数据帧"
    assert first_chunk_time < threshold_s, (
        f"短查询首块延迟 {first_chunk_time:.3f}s 超过阈值 {threshold_s}s"
    )
    print(f"\n[short_query_first_token] {first_chunk_time:.3f}s (阈值 {threshold_s}s)")


def test_short_query_total_latency(
    agent_url: str, perf_thresholds: dict[str, float]
) -> None:
    """验证短查询总延迟 P95 < 10s (短查询保护不走 graph).

    P0-Future-06: 短查询直接返回 reply, 不走任何 graph.
    运行 5 次采样计算 P95 (5 次中 P95 ≈ 最大值).
    """
    threshold_s = perf_thresholds["short_query_total_p95_s"]
    sample_count = 5
    elapsed_list: list[float] = []

    with make_http_client(timeout=LATENCY_TIMEOUT) as client:
        for i in range(sample_count):
            sid = _unique_session_id(f"perf_sq_tot_{i}")
            start = time.perf_counter()
            r = client.post(
                f"{agent_url}/v1/chat/completions",
                json=_chat_payload("你好", stream=False, session_id=sid),
            )
            elapsed = time.perf_counter() - start
            assert r.status_code == 200, (
                f"短查询 #{i} 非 200: {r.status_code} {r.text}"
            )
            elapsed_list.append(elapsed)

    # 计算 P95 (对 5 个样本, P95 = 排序后第 95 百分位 = 最大值)
    sorted_times = sorted(elapsed_list)
    p95_index = max(0, int(len(sorted_times) * 0.95) - 1)
    p95 = sorted_times[p95_index]
    # 小样本时直接取最大值作为 P95
    p95 = max(p95, sorted_times[-1])

    assert p95 < threshold_s, (
        f"短查询 P95 延迟 {p95:.3f}s 超过阈值 {threshold_s}s "
        f"(样本: {[f'{t:.3f}' for t in elapsed_list]})"
    )
    print(
        f"\n[short_query_total_p95] {p95:.3f}s (阈值 {threshold_s}s) "
        f"min={min(elapsed_list):.3f}s avg={sum(elapsed_list) / len(elapsed_list):.3f}s "
        f"max={max(elapsed_list):.3f}s (n={sample_count})"
    )


# ========== 研究查询首块延迟 (含意图分类, 不含完整研究) ==========


def test_stream_first_chunk_latency(
    agent_url: str, perf_thresholds: dict[str, float]
) -> None:
    """验证研究查询流式首块延迟 < 5s (含意图分类, 不含完整研究).

    研究查询走 researcher graph, 但首块 {"role":"assistant"} 在 graph 执行前 yield.
    首块延迟 = 请求解析 + has_report 检查 + 意图分类 + 首块 yield.
    仅消费首块后立即关闭流 (不等待完整研究).
    """
    threshold_s = perf_thresholds["stream_first_chunk_s"]
    sid = _unique_session_id("perf_research_ft")
    first_chunk_time: float | None = None

    # 使用明确的研究型查询 + report_type 强制走 research 路径
    research_query = "请研究人工智能在医疗领域的应用前景并提供详细分析报告"
    with make_http_client(timeout=LATENCY_TIMEOUT) as client:
        start = time.perf_counter()
        with client.stream(
            "POST",
            f"{agent_url}/v1/chat/completions",
            json=_chat_payload(
                research_query, stream=True, session_id=sid, report_type="basic_report"
            ),
        ) as r:
            assert r.status_code == 200, f"研究查询流式非 200: {r.status_code}"
            for line in r.iter_lines():
                if line and line.startswith("data: "):
                    first_chunk_time = time.perf_counter() - start
                    # 验证是合法的 SSE 帧后再 break
                    payload = line[6:]
                    if payload == "[DONE]":
                        continue
                    chunk = json.loads(payload)
                    if chunk.get("choices", [{}])[0].get("delta", {}).get("role"):
                        break
                    # 任意首个 data 帧即可计时
                    break

    assert first_chunk_time is not None, "研究查询流式未收到任何 SSE 数据帧"
    assert first_chunk_time < threshold_s, (
        f"研究查询首块延迟 {first_chunk_time:.3f}s 超过阈值 {threshold_s}s"
    )
    print(f"\n[stream_first_chunk] {first_chunk_time:.3f}s (阈值 {threshold_s}s)")
