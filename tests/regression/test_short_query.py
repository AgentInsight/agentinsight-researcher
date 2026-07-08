"""回归测试: 短查询保护.

AGENTS.md 第 13 章硬约束:
- 回归测试在 docker compose up -d 且全部容器 service_healthy 后执行
- 回归测试为合并 main 前门禁, 不推荐跳过

P0-Future-05/06 短查询保护:
- 短查询 (如"你好") 不走任何 graph, 直接返回 settings.short_query_reply
- 响应快 (不调用 LLM/检索), 验证保护机制生效

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/regression/test_short_query.py -v -m regression
"""

from __future__ import annotations

import json
import os
import uuid

import httpx
import pytest

# AGENTS.md 第 13 章: 测试目标地址从环境变量注入
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")

# 短查询保护不走 graph, 响应快, 60s 足够
SHORT_QUERY_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)

# settings.short_query_reply 默认值 (src/config/settings.py:190)
DEFAULT_SHORT_QUERY_REPLY = "您好！我是研究助手，请提供您想研究的主题，我将为您生成详细的研究报告。"


def _unique_session_id() -> str:
    """生成唯一 session_id (AGENTS.md 第 13 章: session_id=test_*)."""
    return f"test_short_query_{uuid.uuid4().hex[:12]}"


@pytest.mark.regression
def test_short_query_returns_configured_reply() -> None:
    """验证短查询保护: 发送短查询 (如"你好") → 返回 settings.short_query_reply.

    P0-Future-06: 短查询直接返回回复语, 不走任何 graph.
    响应应快速返回 (不调用 LLM/检索).
    """
    sid = _unique_session_id()
    with httpx.Client(timeout=SHORT_QUERY_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
                "session_id": sid,
            },
        )
    assert r.status_code == 200, f"短查询响应非 200: {r.status_code} {r.text}"
    data = r.json()
    assert data["object"] == "chat.completion"
    content = data["choices"][0]["message"]["content"]
    assert content, "短查询回复内容为空"

    # 短查询应返回 short_query_reply (默认或自定义)
    # 由于部署环境可能自定义了 short_query_reply, 这里用包含关键词校验
    reply_lower = content.lower()
    assert any(kw in reply_lower for kw in ["研究助手", "研究", "报告", "主题", "research"]), (
        f"短查询回复未包含研究助手相关关键词, 可能未走短查询保护: content={content[:200]}"
    )


@pytest.mark.regression
def test_short_query_stream() -> None:
    """验证短查询流式响应: stream=true → SSE 流 + 返回 short_query_reply.

    P0-Future-06: 短查询流式也应返回 short_query_reply.
    """
    sid = _unique_session_id()
    content_parts: list[str] = []

    with httpx.Client(timeout=SHORT_QUERY_TIMEOUT) as client:
        with client.stream(
            "POST",
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "嗨"}],
                "stream": True,
                "session_id": sid,
            },
        ) as r:
            assert r.status_code == 200, f"短查询流式响应非 200: {r.status_code}"
            assert "text/event-stream" in r.headers.get("content-type", "")

            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                delta = chunk["choices"][0]["delta"]
                if "content" in delta and delta["content"]:
                    content_parts.append(delta["content"])

    full_content = "".join(content_parts)
    assert full_content, "短查询流式 content 为空"
    # 流式短查询也应返回研究助手相关回复
    reply_lower = full_content.lower()
    assert any(kw in reply_lower for kw in ["研究助手", "研究", "报告", "主题", "research"]), (
        f"短查询流式回复未包含研究助手相关关键词: content={full_content[:200]}"
    )


@pytest.mark.regression
def test_short_query_fast_response() -> None:
    """验证短查询响应快速: 不走 graph, 应在 10s 内返回.

    P0-Future-06: 短查询保护不走任何 graph, 直接返回 reply.
    """
    import time

    sid = _unique_session_id()
    start = time.time()
    with httpx.Client(timeout=SHORT_QUERY_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
                "session_id": sid,
            },
        )
    elapsed = time.time() - start
    assert r.status_code == 200
    # 短查询不走 graph, 应在 10s 内返回 (含网络/中间件开销)
    assert elapsed < 10.0, f"短查询响应过慢 ({elapsed:.1f}s), 可能未走短查询保护而走了 graph"


# ========== 异步回归测试 (httpx.AsyncClient, 仅验证 HTTP 状态码/响应时间, 不依赖完整 LLM) ==========
# AGENTS.md 第 13 章: 新增测试不依赖外部 LLM 调用, 仅验证 HTTP 状态码而非内容.
# 覆盖: 短查询路由/闲聊离题/区域路由/工具选择/缓存机制.

# 异步测试超时 (短查询/离题响应快速; 含研究流式头验证)
ASYNC_TEST_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)


@pytest.mark.regression
async def test_short_query_pure_digits_routed_async() -> None:
    """纯数字短查询 "123" → 200 (规则层 pure_digits 命中, 快速返回).

    P0-Future-06: 纯数字查询命中 SHORT_QUERY 规则, 不走任何 graph.
    覆盖短查询路由 (pure_digits 规则).
    """
    sid = _unique_session_id()
    async with httpx.AsyncClient(timeout=ASYNC_TEST_TIMEOUT) as client:
        r = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "123"}],
                "stream": False,
                "session_id": sid,
            },
        )
    assert r.status_code == 200, f"纯数字短查询响应非 200: {r.status_code}"
    assert r.json()["object"] == "chat.completion"


@pytest.mark.regression
async def test_off_topic_chitchat_routed_async() -> None:
    """闲聊/离题查询 "讲个笑话" → 200 (OFF_TOPIC 路由, 快速返回).

    P1-Future-07: 闲聊正则匹配 OFF_TOPIC, 不走任何 graph, 直接返回回复语.
    覆盖闲聊/离题保护.
    """
    sid = _unique_session_id()
    async with httpx.AsyncClient(timeout=ASYNC_TEST_TIMEOUT) as client:
        r = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "讲个笑话"}],
                "stream": False,
                "session_id": sid,
            },
        )
    assert r.status_code == 200, f"闲聊查询响应非 200: {r.status_code}"
    assert r.json()["object"] == "chat.completion"


@pytest.mark.regression
async def test_short_query_repeated_cache_hit_async() -> None:
    """同一短查询重复请求 → 两次均 200 (分类缓存命中).

    P1: query_classify_cache_enabled=True, 高频重复 query 命中 Redis 缓存.
    验证缓存机制不破坏响应 (Redis 不可用时降级为不缓存, 仍 200).
    覆盖缓存机制.
    """
    sid1 = _unique_session_id()
    sid2 = _unique_session_id()
    query = "你好"

    async with httpx.AsyncClient(timeout=ASYNC_TEST_TIMEOUT) as client:
        # 第一次请求 (可能未命中缓存)
        r1 = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": query}],
                "stream": False,
                "session_id": sid1,
            },
        )
        assert r1.status_code == 200, f"第一次短查询非 200: {r1.status_code}"

        # 第二次相同查询 (不同 session, 但 query 相同 → 分类缓存命中)
        r2 = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": query}],
                "stream": False,
                "session_id": sid2,
            },
        )
        assert r2.status_code == 200, f"第二次短查询 (缓存) 非 200: {r2.status_code}"

    # 两次响应均为有效 chat.completion
    assert r1.json()["object"] == "chat.completion"
    assert r2.json()["object"] == "chat.completion"


@pytest.mark.regression
async def test_academic_keyword_query_stream_accepted() -> None:
    """学术关键词查询流式: stream=true → 200 + text/event-stream (区域路由不崩溃).

    AGENTS.md 第 7 章: academic_keywords 命中时路由到 arxiv/pubmed 等专业数据源.
    验证学术关键词查询不导致 5xx 崩溃, 流式响应头正确返回.
    覆盖区域路由 (学术检索路由).
    """
    sid = _unique_session_id()
    async with httpx.AsyncClient(timeout=ASYNC_TEST_TIMEOUT) as client:
        async with client.stream(
            "POST",
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [
                    {
                        "role": "user",
                        "content": "find recent research papers about machine learning",
                    }
                ],
                "stream": True,
                "report_type": "basic_report",
                "session_id": sid,
            },
        ) as r:
            assert r.status_code == 200, f"学术关键词查询流式非 200: {r.status_code}"
            assert "text/event-stream" in r.headers.get("content-type", ""), (
                f"content-type 非 text/event-stream: {r.headers.get('content-type')}"
            )


@pytest.mark.regression
async def test_research_query_tool_selection_stream_accepted() -> None:
    """研究查询 + 显式 report_type 流式: stream=true → 200 (工具选择路径不崩溃).

    AGENTS.md 第 7/9 章: 显式 report_type 强制走 researcher graph,
    MCP 工具选择 (mcp_auto_tool_selection) 在图内执行.
    验证研究查询 + 工具选择路径不导致 5xx 崩溃, 流式响应头正确返回.
    覆盖工具选择路径.
    """
    sid = _unique_session_id()
    async with httpx.AsyncClient(timeout=ASYNC_TEST_TIMEOUT) as client:
        async with client.stream(
            "POST",
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "分析 2024 年新能源汽车市场发展趋势"}],
                "stream": True,
                "report_type": "basic_report",
                "session_id": sid,
            },
        ) as r:
            assert r.status_code == 200, f"研究查询流式非 200: {r.status_code}"
            assert "text/event-stream" in r.headers.get("content-type", ""), (
                f"content-type 非 text/event-stream: {r.headers.get('content-type')}"
            )
