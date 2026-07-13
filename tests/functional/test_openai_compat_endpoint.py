"""功能测试: 验证 OpenAI 兼容端点 (/v1/chat/completions + /v1/models).

- 测试页面统一调用 OpenAI 兼容端点 POST /v1/chat/completions, 请求体带 stream: true
- 不应调用后端私有端点, 测试应只走对外 OpenAI 兼容接口
- 流式响应用浏览器原生 fetch + ReadableStream 解析 SSE
- 请求头 Authorization: Bearer <jwt_token> 可选 (空则降级 IP-based UserId)

API 契约 (src/api/routes.py):
- POST /v1/chat/completions (stream=false) -> 200 + chat.completion + choices + usage
- POST /v1/chat/completions (stream=true) -> 200 + text/event-stream + data 帧 + DONE
- 缺 user 消息 -> 400; 空查询 -> 400
- GET /v1/models -> 200 + {object: list, data: [{id: agentinsight-researcher}]}

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/functional/test_openai_compat_endpoint.py -v -m functional
"""

from __future__ import annotations

import json
import os
import uuid

import httpx
import pytest

AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")
CHAT_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)


def _unique_session_id() -> str:
    """生成唯一 session_id (session_id=test_*)."""
    return f"test_oai_{uuid.uuid4().hex[:12]}"


def _chat_payload(query: str = "你好", *, stream: bool = False) -> dict[str, object]:
    """构造 /v1/chat/completions 短查询请求体."""
    return {
        "model": "agentinsight-researcher",
        "messages": [{"role": "user", "content": query}],
        "stream": stream,
        "session_id": _unique_session_id(),
    }


@pytest.mark.functional
def test_chat_completions_non_stream_simple() -> None:
    """非流式简单对话: POST /v1/chat/completions stream=false -> 200 + chat.completion."""
    with httpx.Client(timeout=CHAT_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False),
        )
    assert r.status_code == 200, f"非流式 chat 非 200: {r.status_code} {r.text[:500]}"
    data = r.json()
    assert data["object"] == "chat.completion", f"object 异常: {data.get('object')}"
    assert data.get("id"), "响应缺 id"
    assert "created" in data, "响应缺 created"
    assert data["model"] == "agentinsight-researcher", f"model 异常: {data.get('model')}"
    choices = data.get("choices", [])
    assert len(choices) == 1, f"choices 数量非 1: {len(choices)}"
    choice = choices[0]
    assert choice["message"]["role"] == "assistant", "message.role 非 assistant"
    assert choice["finish_reason"] == "stop", "finish_reason 非 stop"
    assert choice["message"].get("content", ""), "非流式响应 content 为空"
    assert "usage" in data, "响应缺 usage 字段"
    assert "total_tokens" in data["usage"], "usage 缺 total_tokens"


@pytest.mark.functional
def test_chat_completions_stream_sse_format() -> None:
    """流式 SSE 格式: stream=true -> 200 + text/event-stream + data 帧 + [DONE]."""
    with httpx.Client(timeout=CHAT_TIMEOUT) as client:
        with client.stream(
            "POST",
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=True),
        ) as r:
            assert r.status_code == 200, f"流式 chat 非 200: {r.status_code}"
            ct = r.headers.get("content-type", "")
            assert "text/event-stream" in ct, f"content-type 非 text/event-stream: {ct}"
            chunks: list[dict[str, object]] = []
            saw_done = False
            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    saw_done = True
                    break
                chunks.append(json.loads(payload))
    # SSE 帧结构校验
    assert len(chunks) >= 3, f"SSE 帧数不足: {len(chunks)}"
    assert chunks[0]["choices"][0]["delta"].get("role") == "assistant", "首块缺 role"
    assert chunks[-1]["choices"][0].get("finish_reason") == "stop", "末块缺 finish_reason"
    assert saw_done, "未收到 [DONE] 终止标记"
    # 每帧应为 chat.completion.chunk 对象
    for c in chunks:
        assert c["object"] == "chat.completion.chunk", f"帧 object 异常: {c.get('object')}"
    # content 非空
    parts = [
        c["choices"][0]["delta"]["content"]
        for c in chunks
        if c["choices"][0]["delta"].get("content")
    ]
    assert parts, "SSE 流未输出任何 content"


@pytest.mark.functional
def test_chat_completions_missing_user_message_returns_400() -> None:
    """缺 user 消息 -> 400 (OpenAI 兼容错误处理).

    src/api/routes.py: messages 无 user 角色 -> HTTPException(400).
    """
    payload = {
        "model": "agentinsight-researcher",
        "messages": [{"role": "system", "content": "仅系统消息, 无 user 消息"}],
        "stream": False,
        "session_id": _unique_session_id(),
    }
    with httpx.Client(timeout=CHAT_TIMEOUT) as client:
        r = client.post(f"{AGENT_URL}/v1/chat/completions", json=payload)
    assert r.status_code == 400, f"缺 user 消息应返回 400, 实际: {r.status_code} {r.text[:300]}"


@pytest.mark.functional
def test_chat_completions_empty_query_returns_400() -> None:
    """空查询 -> 400 (query.strip() 为空校验).

    src/api/routes.py: user 消息 content 为空白 -> HTTPException(400).
    """
    payload = {
        "model": "agentinsight-researcher",
        "messages": [{"role": "user", "content": "   "}],  # 仅空白字符
        "stream": False,
        "session_id": _unique_session_id(),
    }
    with httpx.Client(timeout=CHAT_TIMEOUT) as client:
        r = client.post(f"{AGENT_URL}/v1/chat/completions", json=payload)
    assert r.status_code == 400, f"空查询应返回 400, 实际: {r.status_code} {r.text[:300]}"


@pytest.mark.functional
def test_models_endpoint_returns_agentinsight_researcher() -> None:
    """GET /v1/models -> 200 + 含 agentinsight-researcher 模型 (OpenAI 兼容).

    测试页面从 /v1/models 获取可用模型列表.
    src/api/routes.py: list_models 返回 {object: list, data: [...]}.
    """
    with httpx.Client(timeout=CHAT_TIMEOUT) as client:
        r = client.get(f"{AGENT_URL}/v1/models")
    assert r.status_code == 200, f"/v1/models 非 200: {r.status_code} {r.text[:300]}"
    data = r.json()
    assert data["object"] == "list", f"object 非 list: {data.get('object')}"
    assert isinstance(data.get("data"), list), "data 非 list"
    assert len(data["data"]) > 0, "data 为空列表"
    model = data["data"][0]
    assert model["id"] == "agentinsight-researcher", f"模型 id 异常: {model.get('id')}"
    assert model["object"] == "model", f"模型 object 异常: {model.get('object')}"
    assert "created" in model, "模型缺 created"
    assert "owned_by" in model, "模型缺 owned_by"
