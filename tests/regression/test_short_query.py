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
