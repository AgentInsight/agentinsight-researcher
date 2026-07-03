"""单元测试: API 路由 (OpenAI 兼容端点骨架).

验证 /v1/chat/completions 流式与非流式响应格式.
不实际执行研究, 仅验证端点可访问与响应结构.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from server import app


def test_models_endpoint():
    """测试 /v1/models 端点."""
    client = TestClient(app)
    response = client.get("/v1/models")
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "list"
    assert len(data["data"]) > 0
    assert data["data"][0]["id"] == "agentinsight-researcher"


def test_chat_completions_non_stream():
    """测试 /v1/chat/completions 非流式响应."""
    client = TestClient(app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "agentinsight-researcher",
            "messages": [{"role": "user", "content": "研究中国新能源汽车行业"}],
            "stream": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "chat.completion"
    assert len(data["choices"]) == 1
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert "content" in data["choices"][0]["message"]
    assert "usage" in data
    assert "total_tokens" in data["usage"]


def test_chat_completions_stream():
    """测试 /v1/chat/completions 流式 SSE 响应."""
    client = TestClient(app)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "agentinsight-researcher",
            "messages": [{"role": "user", "content": "研究 AI 行业"}],
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        chunks = []
        for line in response.iter_lines():
            if line.startswith("data: "):
                payload = line[6:]
                if payload == "[DONE]":
                    break
                chunks.append(json.loads(payload))

        # 应有首块(role) + 内容块 + 末块(finish_reason)
        assert len(chunks) >= 3
        assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
        assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_chat_completions_empty_messages():
    """测试空 messages 列表应返回 400."""
    client = TestClient(app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "agentinsight-researcher",
            "messages": [],
            "stream": False,
        },
    )
    assert response.status_code == 400


def test_chat_completions_no_user_message():
    """测试无 user 消息应返回 400."""
    client = TestClient(app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "agentinsight-researcher",
            "messages": [{"role": "system", "content": "你是研究助手"}],
            "stream": False,
        },
    )
    assert response.status_code == 400


def test_chat_completions_empty_query():
    """测试空查询内容应返回 400."""
    client = TestClient(app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "agentinsight-researcher",
            "messages": [{"role": "user", "content": "  "}],
            "stream": False,
        },
    )
    assert response.status_code == 400


def test_security_headers():
    """测试安全响应头 (AGENTS.md 第 11 章)."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("X-XSS-Protection") == "1; mode=block"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
