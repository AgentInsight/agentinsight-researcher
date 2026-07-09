"""单元测试: 可观测性 tracing 封装.

验证 6 类 trace_xxx 在 SDK 不可用时降级为 _NoopSpan.
AGENTS.md 第 10 章: SDK 初始化失败时, 所有 trace_xxx yield _NoopSpan.
"""

from __future__ import annotations

import pytest

from src.observability.tracing import (
    _build_propagate_metadata,
    _NoopSpan,
    trace_agent,
    trace_chain,
    trace_embedding,
    trace_generation,
    trace_retriever,
    trace_tool,
)


@pytest.mark.asyncio
async def test_noop_span_chaining():
    """测试 _NoopSpan 支持链式调用."""
    span = _NoopSpan()
    result = span.update(output="test").end().score(score=0.9)
    assert result is span
    assert span.trace_id is None
    assert span.id is None


@pytest.mark.asyncio
async def test_trace_agent_yields_span():
    """测试 trace_agent yield 一个 span 对象 (SDK 不可用时为 _NoopSpan)."""
    async with trace_agent(
        name="test-agent",
        input={"query": "测试"},
        user_id="u1",
        session_id="s1",
    ) as span:
        # SDK 不可用时 span 为 _NoopSpan, update 永远安全
        span.update(output="result")
        span.update(metadata={"key": "value"})
        assert span is not None


@pytest.mark.asyncio
async def test_trace_generation_yields_span():
    """测试 trace_generation yield span."""
    async with trace_generation(
        name="test-gen",
        model="deepseek/deepseek-chat",
        model_parameters={"temperature": 0.7},
        input="测试输入",
    ) as span:
        span.update(
            output="输出",
            usage_details={"prompt_tokens": 10, "completion_tokens": 20},
            cost_details={"cost_usd": 0.001},
        )


@pytest.mark.asyncio
async def test_trace_tool_yields_span():
    """测试 trace_tool yield span."""
    async with trace_tool(
        name="test-tool",
        input={"args": "参数"},
        metadata={"tool_name": "search", "success": True},
    ) as span:
        span.update(output={"result": "ok"})


@pytest.mark.asyncio
async def test_trace_retriever_yields_span():
    """测试 trace_retriever yield span."""
    async with trace_retriever(
        name="test-retriever",
        input={"query": "测试"},
        metadata={
            "matched": 5,
            "candidate_count": 20,
            "retriever_type": "hybrid",
            "top_score": 0.95,
        },
    ) as span:
        span.update(output=[{"content": "doc1", "score": 0.9}])


@pytest.mark.asyncio
async def test_trace_chain_yields_span():
    """测试 trace_chain yield span."""
    async with trace_chain(
        name="test-chain",
        input={"step": 1},
        version="1.0",
    ) as span:
        span.update(output={"step": 2})


@pytest.mark.asyncio
async def test_trace_embedding_yields_span():
    """测试 trace_embedding yield span (head-based 采样)."""
    # 多次调用验证采样不会抛异常 (命中或降级都应正常)
    for _ in range(10):
        async with trace_embedding(
            name="test-embedding",
            model="BAAI/bge-base-zh-v1.5",
            input={"text_count": 1},
        ) as span:
            span.update(
                output={"vector_count": 1},
                usage_details={"token_count": 10},
            )


def test_build_propagate_metadata():
    """测试 metadata 合并."""
    # 全空
    assert _build_propagate_metadata() == {}

    # 仅 user_id
    result = _build_propagate_metadata(user_id="u1")
    assert result == {"user_id": "u1"}

    # user_id + session_id
    result = _build_propagate_metadata(user_id="u1", session_id="s1")
    assert result == {"user_id": "u1", "session_id": "s1"}

    # 全部 + 自定义 metadata (metadata 可覆盖前两者)
    result = _build_propagate_metadata(
        user_id="u1",
        session_id="s1",
        metadata={"custom": "value", "user_id": "override"},
    )
    assert result["user_id"] == "override"  # metadata 覆盖
    assert result["session_id"] == "s1"
    assert result["custom"] == "value"
