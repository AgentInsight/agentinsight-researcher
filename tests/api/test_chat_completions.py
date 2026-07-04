"""API 测试: OpenAI 兼容端点 /v1/chat/completions.

AGENTS.md 第 13/14 章硬约束:
- API 测试在 docker compose up -d 且全部容器 service_healthy 后执行
- 必须覆盖 OpenAI 兼容端点 (流式 SSE + 非流式 + 错误码)
- 必须包含携带 Bearer JWT Token 与不携带两种场景
- 测试目标地址从环境变量 AGENT_URL 注入
- 每次用唯一 session_id=test_* (AGENTS.md 第 13 章: 测试数据隔离)

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/api/test_chat_completions.py -v -m api

注意: API 测试超时 60s, 用短查询 (如"你好") 验证端点契约, 不走完整研究流程.
研究完整流程由 regression / e2e 测试覆盖.
"""

from __future__ import annotations

import json
import os
import uuid

import httpx
import pytest

# AGENTS.md 第 13 章: 测试目标地址从环境变量注入, 禁止硬编码
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")

# API 测试超时 60s (短查询响应快; 带 token 时 user_info API 超时 5s)
API_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)


def _unique_session_id() -> str:
    """生成唯一 session_id (AGENTS.md 第 13 章: session_id=test_*)."""
    return f"test_api_{uuid.uuid4().hex[:12]}"


def _chat_payload(
    query: str = "你好",
    *,
    stream: bool = False,
    session_id: str | None = None,
    report_type: str | None = None,
) -> dict[str, object]:
    """构造 /v1/chat/completions 请求体."""
    payload: dict[str, object] = {
        "model": "agentinsight-researcher",
        "messages": [{"role": "user", "content": query}],
        "stream": stream,
        "session_id": session_id or _unique_session_id(),
    }
    if report_type is not None:
        payload["report_type"] = report_type
    return payload


@pytest.mark.api
def test_non_stream_research() -> None:
    """验证非流式响应: POST /v1/chat/completions stream=false → 200 + chat.completion 结构.

    使用短查询 "你好" 触发 short_query_reply, 快速返回 (不走研究图).
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False),
        )
    assert r.status_code == 200, f"非流式响应非 200: {r.status_code} {r.text}"
    data = r.json()
    assert data["object"] == "chat.completion", f"object 非 chat.completion: {data['object']}"
    assert "id" in data
    assert "created" in data
    assert "model" in data
    assert data["model"] == "agentinsight-researcher"
    assert len(data["choices"]) == 1
    choice = data["choices"][0]
    assert choice["message"]["role"] == "assistant"
    assert "content" in choice["message"]
    assert choice["finish_reason"] == "stop"
    assert "usage" in data
    assert "total_tokens" in data["usage"]


@pytest.mark.api
def test_stream_research() -> None:
    """验证流式 SSE 响应: POST /v1/chat/completions stream=true → 200 + text/event-stream.

    使用短查询触发 short_query_reply, 快速返回 SSE 流.
    验证 SSE 帧格式: 首块(role) + 内容块 + 末块(finish_reason) + [DONE].
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        with client.stream(
            "POST",
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=True),
        ) as r:
            assert r.status_code == 200, f"流式响应非 200: {r.status_code}"
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
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"

    # 收集 content 验证非空
    content_parts = [
        c["choices"][0]["delta"]["content"]
        for c in chunks
        if c["choices"][0]["delta"].get("content")
    ]
    assert content_parts, "SSE 流未输出任何 content"
    full_content = "".join(content_parts)
    assert len(full_content) > 0, "SSE 流 content 为空"


@pytest.mark.api
def test_empty_messages() -> None:
    """验证空 messages 列表: messages=[] → 400."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [],
                "stream": False,
            },
        )
    assert r.status_code == 400, f"空 messages 应返回 400, 实际: {r.status_code}"


@pytest.mark.api
def test_no_user_message() -> None:
    """验证仅 system 消息: 无 user 消息 → 400."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "system", "content": "你是研究助手"}],
                "stream": False,
            },
        )
    assert r.status_code == 400, f"无 user 消息应返回 400, 实际: {r.status_code}"


@pytest.mark.api
def test_empty_query() -> None:
    """验证空查询内容: user content="   " → 400."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "   "}],
                "stream": False,
            },
        )
    assert r.status_code == 400, f"空查询内容应返回 400, 实际: {r.status_code}"


@pytest.mark.api
def test_with_bearer_token() -> None:
    """验证带 Bearer JWT Token: Authorization: Bearer test-token → 200 (不报错).

    AGENTS.md 第 8 章: token 存在时调用 /api/user 获取 user_id,
    调用失败 (test-token 非合法 JWT) 按无 token 处理并降级 DEFAULT_USER_ID.
    中间件超时 5s, 总超时 60s 应足够.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False),
            headers={"Authorization": "Bearer test-token-invalid"},
        )
    assert r.status_code == 200, (
        f"带 token 请求应返回 200 (降级 DEFAULT_USER_ID), 实际: {r.status_code} {r.text}"
    )
    data = r.json()
    assert data["object"] == "chat.completion"


@pytest.mark.api
def test_without_token() -> None:
    """验证不带 Bearer Token: 无 Authorization 头 → 200 (降级 DEFAULT_USER_ID).

    AGENTS.md 第 8 章: token 不存在时使用 DEFAULT_USER_ID.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False),
        )
    assert r.status_code == 200, (
        f"无 token 请求应返回 200 (降级 DEFAULT_USER_ID), 实际: {r.status_code} {r.text}"
    )
    data = r.json()
    assert data["object"] == "chat.completion"


@pytest.mark.api
def test_models_endpoint() -> None:
    """验证 /v1/models 端点: GET → 200 + list 结构."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.get(f"{AGENT_URL}/v1/models")
    assert r.status_code == 200, f"/v1/models 非 200: {r.status_code}"
    data = r.json()
    assert data["object"] == "list"
    assert len(data["data"]) > 0
    assert data["data"][0]["id"] == "agentinsight-researcher"
    assert data["data"][0]["object"] == "model"


@pytest.mark.api
def test_session_id_header_in_stream() -> None:
    """验证流式响应携带 X-Session-Id 头 (会话隔离键).

    AGENTS.md 第 6 章: thread_id 做会话隔离键.
    """
    sid = _unique_session_id()
    with httpx.Client(timeout=API_TIMEOUT) as client:
        with client.stream(
            "POST",
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=True, session_id=sid),
        ) as r:
            assert r.status_code == 200
            x_sid = r.headers.get("x-session-id", "")
            assert x_sid, "流式响应未携带 X-Session-Id 头"
            assert x_sid == sid, f"X-Session-Id 不匹配: 期望={sid}, 实际={x_sid}"
            # 消费流以避免连接泄漏
            for _ in r.iter_lines():
                break
