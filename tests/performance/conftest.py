"""性能测试 fixtures: 延迟阈值 + 服务地址.

- 性能测试在 docker compose up -d 且全部容器 service_healthy 后执行
- 测试目标地址从环境变量 AGENT_URL 注入, 禁止硬编码
- 测试数据隔离: namespace=test_* + user_id=test_* + session_id=test_*

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/performance/ -v -m performance

注意: 性能测试阈值可经环境变量覆盖 (PERF_*), 便于不同环境调优.
"""

from __future__ import annotations

import os

import httpx
import pytest

# 测试目标地址从环境变量注入, 禁止硬编码
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")

# TEI Embeddings 服务 (宿主机直连 127.0.0.1:8088)
EMBEDDINGS_URL = os.getenv("EMBEDDINGS_URL", "http://127.0.0.1:8088").rstrip("/")
EMBEDDINGS_API_KEY = os.getenv("EMBEDDINGS_API_KEY", "")

# Qdrant 服务 (宿主机直连 127.0.0.1:6333)
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333").rstrip("/")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "") or None
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "agents")


@pytest.fixture
def agent_url() -> str:
    """Agent 服务地址 (复用主 conftest 的 AGENT_URL 环境变量)."""
    return AGENT_URL


@pytest.fixture
def embeddings_url() -> str:
    """TEI Embeddings 服务地址."""
    return EMBEDDINGS_URL


@pytest.fixture
def qdrant_url() -> str:
    """Qdrant 服务地址."""
    return QDRANT_URL


@pytest.fixture
def perf_thresholds() -> dict[str, float]:
    """可配置的延迟阈值 (秒), 经 PERF_* 环境变量覆盖.

    默认值参考性能要求与现有 regression/test_short_query.py 的 10s 上限.
    阈值宽松 enough 容忍 CI 环境抖动, 严格 enough 捕获性能退化.
    """
    return {
        # 端点延迟 (GET 类, 无 LLM 调用)
        "health_ms": float(os.getenv("PERF_HEALTH_MS", "100")),
        "models_ms": float(os.getenv("PERF_MODELS_MS", "200")),
        "mcp_system_ms": float(os.getenv("PERF_MCP_SYSTEM_MS", "500")),
        "agent_discovery_ms": float(os.getenv("PERF_AGENT_DISCOVERY_MS", "200")),
        # 短查询 (chitchat, 不走 graph)
        "short_query_first_token_s": float(os.getenv("PERF_SHORT_QUERY_FIRST_TOKEN_S", "3")),
        "short_query_total_p95_s": float(os.getenv("PERF_SHORT_QUERY_TOTAL_P95_S", "10")),
        # 研究查询首块 (含意图分类, 不含完整研究)
        "stream_first_chunk_s": float(os.getenv("PERF_STREAM_FIRST_CHUNK_S", "5")),
        # Embeddings (TEI bge-base-zh-v1.5)
        "embeddings_single_s": float(os.getenv("PERF_EMBEDDINGS_SINGLE_S", "2")),
        "embeddings_batch_10_s": float(os.getenv("PERF_EMBEDDINGS_BATCH_10_S", "5")),
        # Qdrant 搜索 (不含 embedding 时间)
        "qdrant_search_s": float(os.getenv("PERF_QDRANT_SEARCH_S", "1")),
        # 并发短查询
        "concurrent_5_s": float(os.getenv("PERF_CONCURRENT_5_S", "15")),
        "concurrent_10_s": float(os.getenv("PERF_CONCURRENT_10_S", "30")),
        # 并发会话隔离
        "concurrent_sessions_5_s": float(os.getenv("PERF_CONCURRENT_SESSIONS_5_S", "60")),
    }


def make_http_client(timeout: httpx.Timeout | None = None) -> httpx.Client:
    """构造 httpx.Client (trust_env=False 绕过系统 HTTP 代理).

    localhost 不应走系统代理 (Clash/V2Ray 等).
    """
    if timeout is None:
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
    return httpx.Client(trust_env=False, timeout=timeout)


def make_async_http_client(timeout: httpx.Timeout | None = None) -> httpx.AsyncClient:
    """构造 httpx.AsyncClient (trust_env=False 绕过系统 HTTP 代理)."""
    if timeout is None:
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
    return httpx.AsyncClient(trust_env=False, timeout=timeout)


def embeddings_auth_headers() -> dict[str, str]:
    """构造 TEI 鉴权请求头 (API_KEY 配置时携带 Bearer)."""
    headers: dict[str, str] = {}
    if EMBEDDINGS_API_KEY:
        headers["Authorization"] = f"Bearer {EMBEDDINGS_API_KEY}"
    return headers


def qdrant_auth_headers() -> dict[str, str]:
    """构造 Qdrant 鉴权请求头."""
    headers: dict[str, str] = {}
    if QDRANT_API_KEY:
        headers["api-key"] = QDRANT_API_KEY
    return headers
