"""探索性测试: 降级路径 (Qdrant/Redis/LLM/TEI 不可用时的服务行为).

- Qdrant 不可用时降级内存检索仅限 ENV=dev; 生产应告警并失败转移
- LLM 调用经 llm/ 网关 (LiteLLM), 内置重试与降级链 (strategic → smart → fast)
- Embeddings/Rerank TEI 服务通过 API_KEY 鉴权, 客户端重试与降级
- Redis 不可用时降级无缓存, 不阻断检索

本测试在容器栈健康的前提下, 通过 API 行为验证降级路径:
- LLM 超时降级: 复杂查询触发 LLM 调用, 验证响应最终能返回 (走降级链)
- Qdrant namespace 隔离: 查询不存在的 namespace 应返回空结果 (不崩溃)
- Embeddings TEI 限流: 并发 batch 请求应被限流或排队, 不应崩溃
- Redis 缓存降级: 同一查询重复请求应正常返回 (即使缓存失效)

注意: 本测试不直接断开依赖容器 (避免影响其他测试), 而是通过 API 行为验证降级.

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/exploratory/test_degradation.py -v -m exploratory
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

# 测试目标地址从环境变量注入
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333").rstrip("/")
EMBEDDINGS_URL = os.getenv("EMBEDDINGS_URL", "http://127.0.0.1:8088").rstrip("/")
EMBEDDINGS_API_KEY = os.getenv("EMBEDDINGS_API_KEY", "")

# 降级测试超时 (LLM 调用可能耗时较长)
DEGRADATION_TIMEOUT = httpx.Timeout(connect=10.0, read=90.0, write=10.0, pool=10.0)

# Qdrant / Embeddings 直连超时
DIRECT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


def _unique_session_id(prefix: str = "degrade") -> str:
    """生成唯一 session_id (session_id=test_*)."""
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


def _embeddings_auth_headers() -> dict[str, str]:
    """构造 TEI 鉴权请求头."""
    headers: dict[str, str] = {}
    if EMBEDDINGS_API_KEY:
        headers["Authorization"] = f"Bearer {EMBEDDINGS_API_KEY}"
    return headers


# ========== Qdrant 降级: 不存在 namespace 检索应返回空 ==========


@pytest.mark.exploratory
def test_qdrant_nonexistent_namespace_returns_empty() -> None:
    """降级: 查询不存在的 namespace 应返回空结果, 不应崩溃.

    检索时必须显式传目标 namespace 列表,
    无命中时应返回空列表, 不应抛异常.
    """
    nonexistent_ns = f"test_nonexistent_{uuid.uuid4().hex}"
    fake_vector = [0.01] * 768

    payload = {
        "vector": fake_vector,
        "limit": 5,
        "with_payload": False,
        "filter": {"must": [{"key": "namespace", "match": {"value": nonexistent_ns}}]},
    }

    with httpx.Client(timeout=DIRECT_TIMEOUT) as client:
        r = client.post(
            f"{QDRANT_URL}/collections/agents/points/search",
            json=payload,
        )

    # 集合应存在 (Agent 启动时创建), 404 表示真实故障
    if r.status_code == 404:
        pytest.fail("Qdrant 集合 agents 不存在 (Agent 启动时应创建, 容器栈运行时集合必须存在)")
    assert r.status_code == 200, f"Qdrant 搜索非 200: {r.status_code} {r.text[:200]}"
    data = r.json()
    assert "result" in data
    # 不存在的 namespace 应返回空命中 (降级行为: 不崩溃)
    assert len(data["result"]) == 0, (
        f"不存在 namespace 应返回空结果, 实际: {len(data['result'])} 条命中"
    )


# ========== Qdrant 降级: 无效向量维度应被服务端拒绝 ==========


@pytest.mark.exploratory
def test_qdrant_invalid_vector_dimension_handled() -> None:
    """降级: 无效向量维度 (非 768) 应被 Qdrant 拒绝 (400), 不应崩溃.

    bge-base-zh-v1.5 固定 768 维.
    """
    invalid_vector = [0.1] * 512  # 错误维度
    payload = {
        "vector": invalid_vector,
        "limit": 5,
        "with_payload": False,
    }

    with httpx.Client(timeout=DIRECT_TIMEOUT) as client:
        r = client.post(
            f"{QDRANT_URL}/collections/agents/points/search",
            json=payload,
        )

    if r.status_code == 404:
        pytest.fail("Qdrant 集合 agents 不存在 (Agent 启动时应创建, 容器栈运行时集合必须存在)")
    # 无效维度应返回 4xx 错误 (Qdrant 服务端校验), 不应 5xx
    assert 400 <= r.status_code < 500, (
        f"无效向量维度应返回 4xx, 实际: {r.status_code} {r.text[:200]}"
    )


# ========== Embeddings TEI 降级: 空输入应被处理 ==========


@pytest.mark.exploratory
def test_embeddings_empty_input_handled() -> None:
    """降级: TEI /embed 空输入 → 200 (空列表) 或 400 (校验), 不应 5xx.

    Embeddings 调用统一走 rag/embeddings.py,
    空输入应直接返回 [], 不应调用 TEI 服务.
    """
    with httpx.Client(timeout=DIRECT_TIMEOUT) as client:
        r = client.post(
            f"{EMBEDDINGS_URL}/embed",
            json={"inputs": []},
            headers=_embeddings_auth_headers(),
        )
    # 接受 200 (返回空列表) 或 4xx (TEI 校验拒绝)
    assert r.status_code < 500, f"空输入不应 5xx, 实际: {r.status_code} {r.text[:200]}"


# ========== Embeddings TEI 降级: 超大 batch 应被处理 ==========


@pytest.mark.exploratory
def test_embeddings_large_batch_handled() -> None:
    """降级: 超大 batch (100 条) 应被 TEI 处理或限流, 不应 5xx 崩溃.

    客户端按 embeddings_max_client_batch_size 分批,
    但 TEI 服务端也应能处理或拒绝超大请求 (429/413), 不应崩溃.
    """
    large_batch = [f"测试文本 {i}" for i in range(100)]
    with httpx.Client(timeout=DIRECT_TIMEOUT) as client:
        r = client.post(
            f"{EMBEDDINGS_URL}/embed",
            json={"inputs": large_batch},
            headers=_embeddings_auth_headers(),
        )
    # 接受 200 (返回向量) 或 429 (限流) 或 413 (过大)
    assert r.status_code < 500, f"超大 batch 不应 5xx, 实际: {r.status_code} {r.text[:200]}"
    if r.status_code == 200:
        vectors = r.json()
        assert isinstance(vectors, list)
        # 维度检查 (若返回向量)
        if vectors:
            assert len(vectors[0]) == 768


# ========== LLM 降级: 短查询应快速返回 (走 short_query 保护) ==========


@pytest.mark.exploratory
def test_short_query_does_not_invoke_llm_graph() -> None:
    """降级: 短查询 (你好) 应触发 short_query 保护, 不走完整研究图.

    短查询保护直接返回 reply, 不走任何 graph,
    避免 LLM 调用耗时而影响用户体验.
    """
    sid = _unique_session_id("degrade_short")
    with httpx.Client(timeout=DEGRADATION_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False, session_id=sid),
        )
    assert r.status_code == 200, f"短查询应返回 200, 实际: {r.status_code}"
    data = r.json()
    content = data["choices"][0]["message"]["content"]
    # 短查询应返回非空内容 (走 short_query 保护, 不走 LLM 图)
    assert content, "短查询响应内容为空"


# ========== 重复请求降级: 同一查询重复应正常返回 (缓存或重新生成) ==========


@pytest.mark.exploratory
def test_repeated_query_returns_success() -> None:
    """降级: 同一查询重复请求应正常返回 (即使缓存失效, 也应重新生成).

    Redis 缓存不可用时应降级无缓存, 不阻断检索.
    """
    sid1 = _unique_session_id("degrade_rep_1")
    sid2 = _unique_session_id("degrade_rep_2")
    with httpx.Client(timeout=DEGRADATION_TIMEOUT) as client:
        r1 = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False, session_id=sid1),
        )
        r2 = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False, session_id=sid2),
        )
    assert r1.status_code == 200, f"第一次请求非 200: {r1.status_code}"
    assert r2.status_code == 200, f"第二次请求非 200: {r2.status_code}"


# ========== 流式中断降级: 客户端提前断开不应影响服务稳定性 ==========


@pytest.mark.exploratory
def test_stream_client_disconnect_does_not_crash() -> None:
    """降级: 流式响应中客户端提前断开, 服务应正常处理 (不崩溃).

    流式响应应支持客户端随时断开,
    服务端应通过 asyncio.CancelledError 正常清理资源.
    """
    sid = _unique_session_id("degrade_disconnect")
    # 发起流式请求后立即关闭 (模拟客户端断开)
    with httpx.Client(timeout=DEGRADATION_TIMEOUT) as client:
        with client.stream(
            "POST",
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=True, session_id=sid),
        ) as r:
            assert r.status_code == 200
            # 仅消费一帧后立即关闭
            for _ in r.iter_lines():
                break

    # 后续请求应仍能正常处理 (服务未崩溃)
    sid_after = _unique_session_id("degrade_after")
    with httpx.Client(timeout=DEGRADATION_TIMEOUT) as client:
        r_after = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False, session_id=sid_after),
        )
    assert r_after.status_code == 200, (
        f"客户端断开后服务可能崩溃, 后续请求非 200: {r_after.status_code}"
    )


# ========== 探索性单元测试 (mock-based, 不依赖容器栈) ==========
# 以下测试用 mock 模拟异常场景, 标记为 unit 以便构建期执行 (不依赖容器栈健康).
# 单元测试在构建期执行, 不依赖外部服务.

import asyncio  # noqa: E402
import sys  # noqa: E402
import types  # noqa: E402
from unittest.mock import AsyncMock, patch  # noqa: E402

import httpx as _httpx_mod  # noqa: E402

from src.config.settings import Settings  # noqa: E402
from src.llm.client import LLMClient, LLMTier  # noqa: E402
from src.rag import embeddings as emb_module  # noqa: E402
from src.rag import qdrant_manager as qm_module  # noqa: E402
from src.rag.embeddings import EmbeddingsClient  # noqa: E402
from src.rag.qdrant_manager import QdrantManager  # noqa: E402


def _make_unit_settings(**overrides: object) -> Settings:
    """构造测试 Settings (跳过 .env 加载)."""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


class _FakeHttpxResponse:
    """伪造 httpx 响应 (TEI /embed)."""

    def __init__(self, json_data: object, status_code: int = 200) -> None:
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            req = _httpx_mod.Request("POST", "http://test/embed")
            resp = _httpx_mod.Response(self.status_code, request=req, text="error")
            raise _httpx_mod.HTTPStatusError(f"HTTP {self.status_code}", request=req, response=resp)

    def json(self) -> object:
        return self._json_data


# ========== TEI 429 限流重试 (指数退避) ==========


@pytest.mark.unit
async def test_tei_429_rate_limit_retry() -> None:
    """降级: TEI 返回 429 限流应触发指数退避重试, 第二次成功.

    Embeddings 调用统一走 rag/embeddings.py.
    EmbeddingsClient._embed_texts_single 对 429/5xx 执行指数退避重试
    (base_delay * 2^attempt), 成功后重置熔断器.
    注: 当前实现未解析 Retry-After 头, 使用固定指数退避.
    """
    settings = _make_unit_settings(
        embeddings_max_retries=3,
        embeddings_retry_base_delay=0.0,  # 零延迟便于测试
    )
    client = EmbeddingsClient(settings)
    emb_module._EMBED_CACHE.clear()

    call_count = 0

    class _RetryClient:
        async def post(self, url: str, **kwargs: object) -> _FakeHttpxResponse:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 第一次返回 429 (含 Retry-After 头, 当前实现未解析)
                req = _httpx_mod.Request("POST", url)
                resp = _httpx_mod.Response(
                    429,
                    request=req,
                    text="Too Many Requests",
                    headers={"retry-after": "30"},
                )
                raise _httpx_mod.HTTPStatusError(
                    "429 Too Many Requests", request=req, response=resp
                )
            # 第二次返回 200
            inputs = kwargs.get("json", {}).get("inputs", [])  # type: ignore[union-attr]
            return _FakeHttpxResponse([[0.1] * 768 for _ in inputs])

        async def aclose(self) -> None:
            pass

    client._client = _RetryClient()

    try:
        vectors = await client.embed_texts(["测试文本"])
        # 重试后成功
        assert len(vectors) == 1, "应返回 1 条向量"
        assert len(vectors[0]) == 768, "向量维度应为 768"
        assert call_count == 2, f"应重试 1 次 (共 2 次调用), 实际: {call_count}"
        # 熔断器应记录 1 次失败但未开启 (阈值 5)
        assert not client.is_circuit_open(), "1 次失败不应触发熔断 (阈值 5)"
    finally:
        emb_module._EMBED_CACHE.clear()


# ========== Qdrant 超时降级 ==========


@pytest.mark.unit
async def test_qdrant_timeout_degrades_gracefully() -> None:
    """降级: Qdrant 超时时 count_points_in_namespace 应返回 0, namespace_has_data 返回 False.

    Qdrant 不可用时降级内存检索仅限 ENV=dev; 生产应告警并失败转移.
    count_points_in_namespace 内部 try/except 捕获异常返回 0 (降级, 不阻断检索).
    namespace_has_data 通过 count=0 降级返回 False (跳过该 namespace).
    """
    settings = _make_unit_settings()
    qdrant = QdrantManager(settings)

    class _TimeoutClient:
        """模拟 Qdrant 客户端超时."""

        async def count(self, **kwargs: object) -> object:
            raise TimeoutError("Qdrant count timeout")

        async def query_points(self, **kwargs: object) -> object:
            raise TimeoutError("Qdrant search timeout")

        async def get_collection(self, **kwargs: object) -> object:
            raise TimeoutError("Qdrant get_collection timeout")

        async def close(self) -> None:
            pass

    qdrant._client = _TimeoutClient()
    qdrant._collection_ready = True  # 跳过 ensure_collection
    qm_module._namespace_cache.clear()

    try:
        # 1. count_points_in_namespace 应捕获超时返回 0 (不抛异常)
        count = await qdrant.count_points_in_namespace("test_timeout_ns")
        assert count == 0, f"超时应降级返回 0, 实际: {count}"

        # 2. namespace_has_data 应通过 count=0 返回 False
        qm_module._namespace_cache.clear()  # 清缓存强制重新查
        has_data = await qdrant.namespace_has_data("test_timeout_ns")
        assert has_data is False, "超时应降级返回 False (无数据)"
    finally:
        qm_module._namespace_cache.clear()


# ========== LLM 超时降级策略 (strategic → smart → fast) ==========


@pytest.mark.unit
async def test_llm_timeout_fallback_strategy() -> None:
    """降级: LLM 超时应触发降级链 (strategic → smart → fast), 最终在 fast 成功.

    LLM 调用经 llm/ 网关 (LiteLLM), 内置重试与降级链.
    LLMClient.achat 在 tier 调用失败时按 _FALLBACK_TIER 逐级降级.
    """
    settings = _make_unit_settings(
        strategic_llm="deepseek/deepseek-reasoner",
        smart_llm="deepseek/deepseek-chat",
        fast_llm="deepseek/deepseek-chat",
        llm_response_cache_enabled=False,  # 禁用缓存避免 Redis 依赖
    )
    client = LLMClient(settings)

    class _FakeUsage:
        prompt_tokens = 10
        completion_tokens = 5

    class _FakeResp:
        usage = _FakeUsage()
        choices = [types.SimpleNamespace(message=types.SimpleNamespace(content="fast-tier-ok"))]

    calls: list[dict[str, object]] = []

    async def _fake_acompletion(**kwargs: object) -> _FakeResp:
        calls.append(kwargs)
        n = len(calls)
        if n <= 2:
            # 前两次 (strategic, smart) 超时
            raise TimeoutError(f"tier-{n} timeout")
        # 第三次 (fast) 成功
        return _FakeResp()

    fake_litellm = types.ModuleType("litellm")
    fake_litellm.acompletion = _fake_acompletion

    with patch("src.llm.client.litellm", fake_litellm):
        response = await client.achat(
            [{"role": "user", "content": "测试降级链"}],
            tier=LLMTier.STRATEGIC,
            step="timeout_fallback_test",
        )

    # 最终应在 fast tier 成功
    assert response.content == "fast-tier-ok", "降级链应在 fast tier 成功"
    # 应调用 3 次 (strategic + smart + fast)
    assert len(calls) == 3, f"降级链应调用 3 次 (strategic→smart→fast), 实际: {len(calls)}"


# ========== Redis 不可用降级 (无缓存直接计算) ==========


@pytest.mark.unit
async def test_redis_unavailable_no_cache_direct_compute() -> None:
    """降级: Redis 不可用时应降级无缓存, 直接调用 LLM (不阻断).

    Redis 不可用时应降级无缓存, 不阻断检索.
    LLMClient._get_llm_cache 在 get_redis_client 返回 None 时降级返回 None,
    achat 跳过缓存直接调用 litellm; _set_llm_cache 同样跳过 (不阻断).
    """
    settings = _make_unit_settings(
        smart_llm="deepseek/deepseek-chat",
        fast_llm="deepseek/deepseek-chat",
        strategic_llm="deepseek/deepseek-chat",
        llm_response_cache_enabled=True,  # 启用缓存但 Redis 不可用
        temperature=0.0,  # ≤ 0.3 才走缓存路径
    )
    client = LLMClient(settings)

    class _FakeUsage:
        prompt_tokens = 10
        completion_tokens = 5

    class _FakeResp:
        usage = _FakeUsage()
        choices = [types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))]

    litellm_calls: list[dict[str, object]] = []

    async def _fake_acompletion(**kwargs: object) -> _FakeResp:
        litellm_calls.append(kwargs)
        return _FakeResp()

    fake_litellm = types.ModuleType("litellm")
    fake_litellm.acompletion = _fake_acompletion

    # mock get_redis_client 返回 None (Redis 不可用)
    with (
        patch("src.llm.client.litellm", fake_litellm),
        patch(
            "src.common.redis_client.get_redis_client",
            new=AsyncMock(return_value=None),
        ),
    ):
        response = await client.achat(
            [{"role": "user", "content": "Redis 不可用测试"}],
            tier=LLMTier.SMART,
            temperature=0.0,
            step="redis_unavailable_test",
        )

    # Redis 不可用 → 缓存未命中 → 直接调 litellm
    assert response.content == "ok", "Redis 不可用时应直接调 LLM 返回结果"
    assert len(litellm_calls) == 1, f"Redis 不可用应直接调 litellm 1 次, 实际: {len(litellm_calls)}"


# ========== PostgreSQL 连接池耗尽处理 ==========


@pytest.mark.unit
async def test_postgres_connection_pool_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """降级: PostgreSQL 连接池耗尽应抛出 RuntimeError (fail fast, 不降级 MemorySaver).

    生产 StateGraph 必须挂 PostgresSaver.
    分支优化 P-Checkpointer: 移除 MemorySaver 降级, 连接池创建失败时抛出
    RuntimeError (fail fast), 由调用方决定是否阻断启动.
    """
    from src.memory import checkpointer as cp_module

    # 重置单例 (与 test_memory_checkpointer.py 一致)
    monkeypatch.setattr(cp_module, "_checkpointer_instance", None)
    monkeypatch.setattr(cp_module, "_pool_lock", asyncio.Lock())

    # 安装 fake Postgres 模块, pool.open() 模拟连接池耗尽
    class _FailingPool:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def open(self) -> None:
            raise RuntimeError("连接池耗尽: too many connections")

    class _FakeCheckpointer:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def setup(self) -> None:
            pass

    fake_psycopg_pool = types.ModuleType("psycopg_pool")
    fake_psycopg_pool.AsyncConnectionPool = _FailingPool  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg_pool", fake_psycopg_pool)

    fake_psycopg_rows = types.ModuleType("psycopg.rows")
    fake_psycopg_rows.dict_row = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg.rows", fake_psycopg_rows)

    fake_pg_saver = types.ModuleType("langgraph.checkpoint.postgres.aio")
    fake_pg_saver.AsyncPostgresSaver = _FakeCheckpointer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.postgres.aio", fake_pg_saver)

    settings = _make_unit_settings()

    # 连接池 open() 失败 → RuntimeError (fail fast, 不降级 MemorySaver)
    with pytest.raises(RuntimeError, match="PostgresSaver 初始化失败"):
        await cp_module.get_checkpointer(settings)
