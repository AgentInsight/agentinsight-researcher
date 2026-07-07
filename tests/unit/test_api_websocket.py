"""单元测试: WebSocket 双向实时通信 (P2-Future-02).

验证 src/api/websocket.py:
- ping → pong 响应
- human_feedback → 提交到 FeedbackQueue
- 无效 JSON → 异常处理与清理
- 未知消息类型 → 返回 error
- 缺少 feedback 字段 → 返回 error (无待处理请求)
- WebSocket 断开 → 资源清理

AGENTS.md 第 13 章: 单元测试不依赖外部服务.
AGENTS.md 第 14 章: /v1/ws/{session_id} 为允许调用的端点 (人在回路通道).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi import WebSocketDisconnect

from src.api import feedback_queue as fq_module
from src.api import websocket as ws_module
from src.api.feedback_queue import get_feedback_queue
from src.api.websocket import (
    WS_MSG_ERROR,
    WS_MSG_LOGS,
    get_websocket_manager,
    websocket_endpoint,
)
from src.config.settings import Settings

pytestmark = pytest.mark.unit


# ========== Fake WebSocket ==========


class FakeWebSocket:
    """伪造 FastAPI WebSocket, 用于测试消息收发."""

    def __init__(
        self,
        messages: list[dict[str, Any]] | None = None,
        raise_on_receive: Exception | None = None,
    ) -> None:
        self.headers: dict[str, str] = {}
        self.query_params: dict[str, str] = {}
        self.sent_messages: list[dict[str, Any]] = []
        self._messages = list(messages or [])
        self._raise_on_receive = raise_on_receive
        self.accepted = False
        self.closed = False
        self.close_code: int | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_code = code

    async def send_json(self, message: dict[str, Any]) -> None:
        self.sent_messages.append(message)

    async def receive_json(self) -> dict[str, Any]:
        if self._raise_on_receive is not None:
            raise self._raise_on_receive
        if not self._messages:
            raise WebSocketDisconnect()
        return self._messages.pop(0)


# ========== Fixtures ==========


@pytest.fixture
def dev_settings() -> Settings:
    """Dev 环境配置: WebSocket 启用, 关闭 Origin/JWT 校验 (便于单元测试)."""
    return Settings(
        _env_file=None,
        env="dev",
        websocket_enabled=True,
        ws_auth_required=False,
        ws_origin_check=False,
    )


@pytest.fixture(autouse=True)
def reset_singletons() -> None:
    """每个测试前重置 WebSocket manager 和 Feedback queue 单例."""
    ws_module._ws_manager = None
    fq_module._feedback_queue = None
    yield
    ws_module._ws_manager = None
    fq_module._feedback_queue = None


@pytest.fixture
def patched_settings(
    dev_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> Settings:
    """Patch get_settings 在 websocket 模块返回 dev 配置."""
    monkeypatch.setattr(ws_module, "get_settings", lambda: dev_settings)
    return dev_settings


def _has_message(messages: list[dict[str, Any]], msg_type: str) -> bool:
    """检查消息列表中是否包含指定 type 的消息."""
    return any(m.get("type") == msg_type for m in messages)


def _get_messages_by_type(messages: list[dict[str, Any]], msg_type: str) -> list[dict[str, Any]]:
    """获取消息列表中指定 type 的所有消息."""
    return [m for m in messages if m.get("type") == msg_type]


# ========== ping → pong ==========


@pytest.mark.asyncio
async def test_websocket_ping_returns_pong(patched_settings: Settings) -> None:
    """Receive {"type":"ping"} → send {"type":"pong"}."""
    ws = FakeWebSocket(messages=[{"type": "ping"}])
    session_id = "test-ping-session"

    await websocket_endpoint(ws, session_id)

    # 连接应已被接受
    assert ws.accepted is True
    # 应收到连接日志消息 + pong
    assert _has_message(ws.sent_messages, WS_MSG_LOGS)
    pong_messages = _get_messages_by_type(ws.sent_messages, "pong")
    assert len(pong_messages) == 1
    # pong 消息格式应为 {"type": "pong"}
    assert pong_messages[0] == {"type": "pong"}


# ========== human_feedback → FeedbackQueue ==========


@pytest.mark.asyncio
async def test_websocket_human_feedback_submission(
    patched_settings: Settings,
) -> None:
    """Receive {"type":"human_feedback","feedback":"approve"} → 提交到 FeedbackQueue."""
    session_id = "test-feedback-session"
    queue = get_feedback_queue()

    # 启动 waiter 任务创建待处理 Future
    async def waiter() -> str | None:
        return await queue.wait_feedback(session_id, timeout_seconds=2.0)

    task = asyncio.create_task(waiter())
    # 等待 waiter 创建 Future
    await asyncio.sleep(0.05)
    assert queue.has_pending(session_id)

    ws = FakeWebSocket(messages=[{"type": "human_feedback", "feedback": "approve"}])
    await websocket_endpoint(ws, session_id)

    # 反馈应已被提交, waiter 任务应返回 "approve"
    result = await task
    assert result == "approve"
    # put_feedback 返回 True → 不应发送 error 消息
    error_messages = _get_messages_by_type(ws.sent_messages, WS_MSG_ERROR)
    assert len(error_messages) == 0


# ========== 无效 JSON ==========


@pytest.mark.asyncio
async def test_websocket_invalid_json_returns_error(
    patched_settings: Settings,
) -> None:
    """Receive 无效 JSON → 异常处理并清理连接."""
    # 模拟 receive_json 抛出 JSONDecodeError
    ws = FakeWebSocket(raise_on_receive=json.JSONDecodeError("Invalid JSON", "", 0))
    session_id = "test-invalid-json-session"

    await websocket_endpoint(ws, session_id)

    # 连接应已被接受
    assert ws.accepted is True
    # 异常被捕获, 连接应被清理 (disconnect 调用)
    manager = get_websocket_manager()
    assert not manager.is_connected(session_id)


# ========== 未知消息类型 ==========


@pytest.mark.asyncio
async def test_websocket_unknown_message_type_returns_error(
    patched_settings: Settings,
) -> None:
    """Receive {"type":"unknown"} → send error."""
    ws = FakeWebSocket(messages=[{"type": "unknown"}])
    session_id = "test-unknown-type-session"

    await websocket_endpoint(ws, session_id)

    error_messages = _get_messages_by_type(ws.sent_messages, WS_MSG_ERROR)
    assert len(error_messages) == 1
    assert "未知消息类型" in error_messages[0]["message"]
    assert "unknown" in error_messages[0]["message"]


# ========== 缺少 feedback 字段 ==========


@pytest.mark.asyncio
async def test_websocket_missing_feedback_field_returns_error(
    patched_settings: Settings,
) -> None:
    """Receive {"type":"human_feedback"} 无 feedback 字段 → send error (无待处理请求)."""
    ws = FakeWebSocket(messages=[{"type": "human_feedback"}])
    session_id = "test-missing-feedback-session"

    await websocket_endpoint(ws, session_id)

    # 无待处理 Future → put_feedback 返回 False → 发送 error
    error_messages = _get_messages_by_type(ws.sent_messages, WS_MSG_ERROR)
    assert len(error_messages) == 1
    assert "无待处理" in error_messages[0]["message"]


# ========== 断开连接 → 清理 ==========


@pytest.mark.asyncio
async def test_websocket_disconnect_cleans_up(
    patched_settings: Settings,
) -> None:
    """WebSocket 断开 → 清理资源 (manager.disconnect)."""
    # 发送一条消息后, receive_json 抛出 WebSocketDisconnect
    ws = FakeWebSocket(messages=[{"type": "ping"}])
    session_id = "test-disconnect-session"

    await websocket_endpoint(ws, session_id)

    # 处理完消息后断开, 应清理连接
    manager = get_websocket_manager()
    assert not manager.is_connected(session_id)
    # ping 应已被处理 (pong 已发送)
    assert _has_message(ws.sent_messages, "pong")
