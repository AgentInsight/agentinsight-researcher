"""单元测试: AgentInsightTracer 6 类 trace span 与工厂.

验证 src/observability/tracing.py:
- AgentInsightTracer.trace_agent/generation/tool/retriever/chain: 验证 as_type 与字段传递
- trace_embedding head-based 采样: sample_rate=0.0 永远降级, 1.0 永远走 SDK
- get_tracer 工厂单例
- _get_client 降级路径

AGENTS.md 第 13 章: 单元测试不依赖外部服务.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from src.config.settings import Settings
from src.observability import tracing as tracing_module
from src.observability.tracing import (
    AgentInsightTracer,
    _get_client,
    _NoopSpan,
    get_tracer,
)


class _FakeSpan:
    """伪造 SDK span, 支持 update/end 链式调用."""

    def update(self, **_kwargs: Any) -> _FakeSpan:
        return self

    def end(self, **_kwargs: Any) -> _FakeSpan:
        return self


class _FakeClient:
    """伪造 agentinsight client, 捕获 start_as_current_observation 调用."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._span = _FakeSpan()

    @contextmanager
    def start_as_current_observation(self, **kwargs: Any):
        self.calls.append(kwargs)
        yield self._span


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    """注入伪造 agentinsight client (覆盖 _get_client)."""
    client = _FakeClient()
    monkeypatch.setattr(tracing_module, "_get_client", lambda: client)
    return client


# ========== trace_agent ==========


@pytest.mark.asyncio
async def test_trace_agent_calls_sdk(fake_client: _FakeClient) -> None:
    """trace_agent 调用 start_as_current_observation(as_type="agent", ...)."""
    tracer = AgentInsightTracer()
    async with tracer.trace_agent(
        name="test-agent",
        input={"query": "测试"},
        metadata={"intent": "research"},
        version="1.0",
        user_id="u1",
        session_id="s1",
    ) as span:
        assert span is fake_client._span

    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["as_type"] == "agent"
    assert call["name"] == "test-agent"
    assert call["input"] == {"query": "测试"}
    assert call["version"] == "1.0"
    # metadata 合并了 user_id/session_id/自定义 metadata
    assert call["metadata"]["user_id"] == "u1"
    assert call["metadata"]["session_id"] == "s1"
    assert call["metadata"]["intent"] == "research"


@pytest.mark.asyncio
async def test_trace_agent_client_none_yields_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """_get_client 返回 None 时降级为 _NoopSpan."""
    monkeypatch.setattr(tracing_module, "_get_client", lambda: None)
    tracer = AgentInsightTracer()
    async with tracer.trace_agent(name="test") as span:
        assert isinstance(span, _NoopSpan)


# ========== trace_generation ==========


@pytest.mark.asyncio
async def test_trace_generation_calls_sdk(fake_client: _FakeClient) -> None:
    """trace_generation 传递 as_type="generation", model, model_parameters.

    PERF-OPT-004: usage_details 和 cost_details 不在创建时传入,
    调用完成后由 llm/client.py span.update() 设置.
    """
    tracer = AgentInsightTracer()
    async with tracer.trace_generation(
        name="test-gen",
        model="deepseek/deepseek-chat",
        model_parameters={"temperature": 0.7},
        usage_details={"prompt_tokens": 10},
        cost_details={"cost_usd": 0.001},
        input="测试",
        version="1.0",
        user_id="u1",
    ) as span:
        assert span is fake_client._span

    call = fake_client.calls[0]
    assert call["as_type"] == "generation"
    assert call["model"] == "deepseek/deepseek-chat"
    assert call["model_parameters"] == {"temperature": 0.7}
    # PERF-OPT-004: usage_details 和 cost_details 不在创建时传入 (由 span.update 后续设置)
    assert "usage_details" not in call
    assert "cost_details" not in call
    assert call["version"] == "1.0"


# ========== trace_tool ==========


@pytest.mark.asyncio
async def test_trace_tool_calls_sdk(fake_client: _FakeClient) -> None:
    """trace_tool 传递 as_type="tool"."""
    tracer = AgentInsightTracer()
    async with tracer.trace_tool(
        name="test-tool",
        input={"args": "参数"},
        metadata={"tool_name": "search"},
        user_id="u1",
    ) as span:
        assert span is fake_client._span

    call = fake_client.calls[0]
    assert call["as_type"] == "tool"
    assert call["name"] == "test-tool"
    assert call["input"] == {"args": "参数"}
    assert call["metadata"]["tool_name"] == "search"


# ========== trace_retriever ==========


@pytest.mark.asyncio
async def test_trace_retriever_calls_sdk(fake_client: _FakeClient) -> None:
    """trace_retriever 传递 as_type="retriever"."""
    tracer = AgentInsightTracer()
    async with tracer.trace_retriever(
        name="test-retriever",
        input={"query": "测试"},
        metadata={"retriever_type": "hybrid"},
    ) as span:
        assert span is fake_client._span

    call = fake_client.calls[0]
    assert call["as_type"] == "retriever"
    assert call["name"] == "test-retriever"


# ========== trace_chain ==========


@pytest.mark.asyncio
async def test_trace_chain_calls_sdk(fake_client: _FakeClient) -> None:
    """trace_chain 传递 as_type="chain", version."""
    tracer = AgentInsightTracer()
    async with tracer.trace_chain(
        name="test-chain",
        input={"step": 1},
        version="2.0",
    ) as span:
        assert span is fake_client._span

    call = fake_client.calls[0]
    assert call["as_type"] == "chain"
    assert call["name"] == "test-chain"
    assert call["version"] == "2.0"


# ========== trace_embedding head-based 采样 ==========


@pytest.mark.asyncio
async def test_trace_embedding_sample_rate_zero_always_noop(
    fake_client: _FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sample_rate=0.0 时永远降级为 _NoopSpan."""
    settings = Settings(tracing_embedding_sample_rate=0.0, _env_file=None)
    monkeypatch.setattr(tracing_module, "get_settings", lambda: settings)

    tracer = AgentInsightTracer()
    for _ in range(20):
        async with tracer.trace_embedding(
            name="test-emb",
            model="BAAI/bge-base-zh-v1.5",
        ) as span:
            assert isinstance(span, _NoopSpan), "sample_rate=0.0 应永远降级"

    # 不应调用 SDK
    assert len(fake_client.calls) == 0


@pytest.mark.asyncio
async def test_trace_embedding_sample_rate_one_always_sdk(
    fake_client: _FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sample_rate=1.0 时永远走 SDK."""
    settings = Settings(tracing_embedding_sample_rate=1.0, _env_file=None)
    monkeypatch.setattr(tracing_module, "get_settings", lambda: settings)

    tracer = AgentInsightTracer()
    for _ in range(20):
        async with tracer.trace_embedding(
            name="test-emb",
            model="BAAI/bge-base-zh-v1.5",
        ) as span:
            assert span is fake_client._span, "sample_rate=1.0 应永远走 SDK"

    # 应调用 SDK 20 次
    assert len(fake_client.calls) == 20


@pytest.mark.asyncio
async def test_trace_embedding_client_none_yields_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_get_client 返回 None 时, 不论采样率如何都降级为 _NoopSpan."""
    monkeypatch.setattr(tracing_module, "_get_client", lambda: None)
    settings = Settings(tracing_embedding_sample_rate=1.0, _env_file=None)
    monkeypatch.setattr(tracing_module, "get_settings", lambda: settings)

    tracer = AgentInsightTracer()
    async with tracer.trace_embedding(name="test", model="m") as span:
        assert isinstance(span, _NoopSpan)


# ========== get_tracer 工厂单例 ==========


def test_get_tracer_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_tracer 两次调用返回同一实例."""
    monkeypatch.setattr(tracing_module, "_tracer", None)
    t1 = get_tracer()
    t2 = get_tracer()
    assert t1 is t2


# ========== _get_client 降级路径 ==========


def test_get_client_sdk_unavailable_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """_sdk_available=False 时 _get_client 返回 None."""
    monkeypatch.setattr(tracing_module, "_sdk_available", False)
    assert _get_client() is None


def test_get_client_get_client_raises_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """agentinsight.get_client() 抛异常时返回 None."""

    class _FailingAgentInsight:
        @staticmethod
        def get_client() -> Any:
            raise RuntimeError("SDK not initialized")

    monkeypatch.setattr(tracing_module, "_sdk_available", True)
    # raising=False: agentinsight 模块在 SDK 初始化失败时未绑定到 tracing 命名空间
    monkeypatch.setattr(tracing_module, "agentinsight", _FailingAgentInsight, raising=False)
    assert _get_client() is None
