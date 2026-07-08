"""功能测试: 容器栈健康后冒烟测试 (端到端冒烟).

AGENTS.md 第 13 章硬约束:
- 功能测试在 docker compose up -d 且全部容器 service_healthy 后执行
- 测试目标地址从环境变量 AGENT_URL 注入, 禁止硬编码
- 测试用例独立可重复运行, 不依赖执行顺序
- 测试数据隔离: session_id=test_smoke_*

本文件作为容器栈冒烟测试入口, 验证端到端最基本可用性:
- /health 端点 (agent 容器健康)
- /v1/models 端点 (静态端点)
- /v1/chat/completions 非流式短查询 (端到端基本请求)
- /v1/chat/completions 流式短查询 (SSE 流式)
- 各依赖容器健康 (postgres/redis/qdrant/embeddings)

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/functional/test_smoke_functional.py -v -m functional
"""

from __future__ import annotations

import json
import os
import uuid

import httpx
import pytest

# AGENTS.md 第 13 章: 测试目标地址从环境变量注入, 禁止硬编码
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")

# 冒烟测试超时 (短查询 + 端点响应)
SMOKE_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)


def _unique_session_id() -> str:
    """生成唯一 session_id (AGENTS.md 第 13 章: session_id=test_*)."""
    return f"test_smoke_{uuid.uuid4().hex[:12]}"


def _chat_payload(query: str = "你好", *, stream: bool = False) -> dict[str, object]:
    """构造 /v1/chat/completions 短查询请求体."""
    return {
        "model": "agentinsight-researcher",
        "messages": [{"role": "user", "content": query}],
        "stream": stream,
        "session_id": _unique_session_id(),
    }


# ========== 端点冒烟 ==========


@pytest.mark.functional
def test_health_endpoint_smoke() -> None:
    """冒烟: GET /health → 200 + status=ok + service=agentinsight-researcher."""
    with httpx.Client(timeout=SMOKE_TIMEOUT) as client:
        r = client.get(f"{AGENT_URL}/health")
    assert r.status_code == 200, f"/health 非 200: {r.status_code} {r.text}"
    body = r.json()
    assert body.get("status") == "ok", f"/health status 异常: {body}"
    assert body.get("service") == "agentinsight-researcher", f"/health service 异常: {body}"


@pytest.mark.functional
def test_models_endpoint_smoke() -> None:
    """冒烟: GET /v1/models → 200 + 至少一个模型 (agentinsight-researcher)."""
    with httpx.Client(timeout=SMOKE_TIMEOUT) as client:
        r = client.get(f"{AGENT_URL}/v1/models")
    assert r.status_code == 200, f"/v1/models 非 200: {r.status_code} {r.text}"
    data = r.json()
    assert data["object"] == "list"
    assert len(data["data"]) > 0
    model_ids = [m["id"] for m in data["data"]]
    assert "agentinsight-researcher" in model_ids


@pytest.mark.functional
def test_agent_discovery_smoke() -> None:
    """冒烟: GET /.well-known/agent-discovery.json → 200 + 元信息完整.

    AGENTS.md 第 14 章: Agent Discovery Protocol 公开发现端点.
    """
    with httpx.Client(timeout=SMOKE_TIMEOUT) as client:
        r = client.get(f"{AGENT_URL}/.well-known/agent-discovery.json")
    assert r.status_code == 200, f"agent-discovery 非 200: {r.status_code} {r.text}"
    data = r.json()
    assert "name" in data
    assert "version" in data
    assert "services" in data
    assert "capabilities" in data


# ========== 端到端短查询冒烟 ==========


@pytest.mark.functional
def test_chat_completions_non_stream_smoke() -> None:
    """冒烟: POST /v1/chat/completions stream=false → 200 + chat.completion 结构.

    使用短查询 "你好" 触发 short_query 保护, 快速返回 (不走研究图).
    """
    with httpx.Client(timeout=SMOKE_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False),
        )
    assert r.status_code == 200, f"非流式 chat 非 200: {r.status_code} {r.text[:500]}"
    data = r.json()
    assert data["object"] == "chat.completion"
    assert len(data["choices"]) == 1
    choice = data["choices"][0]
    assert choice["message"]["role"] == "assistant"
    assert choice["finish_reason"] == "stop"
    # 响应内容非空
    content = choice["message"].get("content", "")
    assert content, "非流式响应 content 为空"
    # usage 字段
    assert "usage" in data
    assert "total_tokens" in data["usage"]


@pytest.mark.functional
def test_chat_completions_stream_smoke() -> None:
    """冒烟: POST /v1/chat/completions stream=true → 200 + SSE 流式正确.

    验证 SSE 帧格式: 首块(role) + 内容块 + 末块(finish_reason) + [DONE].
    """
    with httpx.Client(timeout=SMOKE_TIMEOUT) as client:
        with client.stream(
            "POST",
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=True),
        ) as r:
            assert r.status_code == 200, f"流式 chat 非 200: {r.status_code}"
            content_type = r.headers.get("content-type", "")
            assert "text/event-stream" in content_type, (
                f"content-type 非 text/event-stream: {content_type}"
            )

            chunks: list[dict[str, object]] = []
            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                chunks.append(json.loads(payload))

    # 至少有首块(role) + 内容块 + 末块(finish_reason)
    assert len(chunks) >= 3, f"SSE 帧数不足: {len(chunks)}"
    # 首块应含 role
    assert chunks[0]["choices"][0]["delta"].get("role") == "assistant"
    # 末块应含 finish_reason
    assert chunks[-1]["choices"][0].get("finish_reason") == "stop"
    # 收集 content 验证非空
    content_parts = [
        c["choices"][0]["delta"]["content"]
        for c in chunks
        if c["choices"][0]["delta"].get("content")
    ]
    assert content_parts, "SSE 流未输出任何 content"


# ========== 容器栈各服务冒烟 ==========


@pytest.mark.functional
def test_qdrant_service_smoke() -> None:
    """冒烟: Qdrant 服务可用 GET /healthz → 200."""
    qdrant_url = os.getenv("QDRANT_URL", "http://127.0.0.1:6333").rstrip("/")
    with httpx.Client(timeout=SMOKE_TIMEOUT) as client:
        r = client.get(f"{qdrant_url}/healthz")
    assert r.status_code == 200, f"Qdrant /healthz 非 200: {r.status_code}"


@pytest.mark.functional
def test_embeddings_service_smoke() -> None:
    """冒烟: Embeddings TEI 服务可用 GET /health → 200."""
    embeddings_url = os.getenv("EMBEDDINGS_URL", "http://127.0.0.1:8088").rstrip("/")
    embeddings_api_key = os.getenv("EMBEDDINGS_API_KEY", "")
    headers: dict[str, str] = {}
    if embeddings_api_key:
        headers["Authorization"] = f"Bearer {embeddings_api_key}"
    with httpx.Client(timeout=SMOKE_TIMEOUT) as client:
        r = client.get(f"{embeddings_url}/health", headers=headers)
    assert r.status_code == 200, f"Embeddings /health 非 200: {r.status_code}"


@pytest.mark.functional
def test_postgres_service_smoke() -> None:
    """冒烟: PostgreSQL 服务可用 SELECT 1."""
    import psycopg  # type: ignore[import-not-found]

    dsn = (
        f"host={os.getenv('POSTGRES_HOST', '127.0.0.1')} "
        f"port={int(os.getenv('POSTGRES_PORT', '5432'))} "
        f"dbname={os.getenv('POSTGRES_DB', 'agents')} "
        f"user={os.getenv('POSTGRES_USER', 'agentinsight')} "
        f"password={os.getenv('POSTGRES_PASSWORD', '')} "
        f"connect_timeout=5"
    )
    try:
        conn = psycopg.connect(dsn)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        result = cur.fetchone()
        assert result is not None and result[0] == 1
        cur.close()
        conn.close()
    except psycopg.OperationalError as e:
        pytest.fail(f"Postgres 连接失败: {e}")


@pytest.mark.functional
def test_redis_service_smoke() -> None:
    """冒烟: Redis 服务可用 PING → True."""
    import redis  # type: ignore[import-not-found]

    redis_auth = os.getenv("REDIS_AUTH", "")
    try:
        client = redis.Redis(
            host=os.getenv("REDIS_HOST", "127.0.0.1"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            username="default" if redis_auth else None,
            password=redis_auth if redis_auth else None,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        pong = client.ping()
        assert pong is True, f"Redis PING 返回非 True: {pong}"
        client.close()
    except redis.RedisError as e:
        pytest.fail(f"Redis 连接失败: {e}")


# ========== 安全响应头冒烟 ==========


@pytest.mark.functional
def test_security_headers_smoke() -> None:
    """冒烟: 安全响应头中间件注入正确 (AGENTS.md 第 11 章硬约束).

    验证: nosniff / DENY / XSS-Protection / Referrer-Policy.
    """
    with httpx.Client(timeout=SMOKE_TIMEOUT) as client:
        r = client.get(f"{AGENT_URL}/health")
    assert r.status_code == 200
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    xss = r.headers.get("x-xss-protection", "")
    assert "1" in xss and "mode=block" in xss
    assert r.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
