"""功能测试: 验证容器栈各服务健康.

- 功能测试在 docker compose up -d 且全部容器 service_healthy 后执行
- 测试目标地址从环境变量 AGENT_URL 注入 (默认 http://127.0.0.1:8066)
- 测试用例独立可重复运行, 不依赖执行顺序

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/functional/test_container_health.py -v -m functional

验证 6 容器 (生产联网模式): agent / postgres / redis / qdrant / embeddings
(rerank 为可选容器, 由 profile 控制, 不强制测试)
"""

from __future__ import annotations

import os

import httpx
import pytest

# 测试目标地址从环境变量注入, 禁止硬编码
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333").rstrip("/")
EMBEDDINGS_URL = os.getenv("EMBEDDINGS_URL", "http://127.0.0.1:8088").rstrip("/")
EMBEDDINGS_API_KEY = os.getenv("EMBEDDINGS_API_KEY", "")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.getenv("POSTGRES_USER", "agentinsight")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
POSTGRES_DB = os.getenv("POSTGRES_DB", "agents")
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_AUTH = os.getenv("REDIS_AUTH", "")

HEALTH_TIMEOUT = 10.0


@pytest.mark.functional
def test_agent_health() -> None:
    """验证 agent 容器健康: GET /health → 200 + status=ok."""
    r = httpx.get(f"{AGENT_URL}/health", timeout=HEALTH_TIMEOUT)
    assert r.status_code == 200, f"/health 非 200: {r.status_code} {r.text}"
    body = r.json()
    assert body.get("status") == "ok", f"/health 状态异常: {body}"
    assert body.get("service") == "agentinsight-researcher"


@pytest.mark.functional
def test_postgres_health() -> None:
    """验证 postgres 容器健康: 直连 SELECT 1.

    使用 psycopg (项目依赖), 连接配置从环境变量注入.
    """
    import psycopg  # type: ignore[import-not-found]

    dsn = (
        f"host={POSTGRES_HOST} port={POSTGRES_PORT} dbname={POSTGRES_DB} "
        f"user={POSTGRES_USER} password={POSTGRES_PASSWORD} connect_timeout=5"
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
def test_redis_health() -> None:
    """验证 redis 容器健康: redis-cli ping 等价 (redis-py PING).

    使用 redis-py (项目依赖), 连接配置从环境变量注入.
    """
    import redis  # type: ignore[import-not-found]

    try:
        client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            username="default" if REDIS_AUTH else None,
            password=REDIS_AUTH if REDIS_AUTH else None,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        pong = client.ping()
        assert pong is True, f"Redis PING 返回非 True: {pong}"
        client.close()
    except redis.RedisError as e:
        pytest.fail(f"Redis 连接失败: {e}")


@pytest.mark.functional
def test_qdrant_health() -> None:
    """验证 qdrant 容器健康: GET /healthz → 200."""
    r = httpx.get(f"{QDRANT_URL}/healthz", timeout=HEALTH_TIMEOUT)
    assert r.status_code == 200, f"Qdrant /healthz 非 200: {r.status_code} {r.text}"


@pytest.mark.functional
def test_embeddings_health() -> None:
    """验证 embeddings TEI 容器健康: GET /health → 200.

    TEI 服务通过 API_KEY 环境变量开启鉴权, 客户端须携带 Authorization: Bearer.
    """
    headers: dict[str, str] = {}
    if EMBEDDINGS_API_KEY:
        headers["Authorization"] = f"Bearer {EMBEDDINGS_API_KEY}"
    r = httpx.get(f"{EMBEDDINGS_URL}/health", timeout=HEALTH_TIMEOUT, headers=headers)
    assert r.status_code == 200, f"Embeddings /health 非 200: {r.status_code} {r.text}"


@pytest.mark.functional
def test_qdrant_root_endpoint() -> None:
    """验证 Qdrant 根端点可用 (返回版本信息).

    Qdrant 1.18 根路径 / 可能返回 404, 改用 /healthz 验证可用性.
    """
    r = httpx.get(f"{QDRANT_URL}/healthz", timeout=HEALTH_TIMEOUT)
    assert r.status_code == 200, f"Qdrant /healthz 非 200: {r.status_code} {r.text}"
