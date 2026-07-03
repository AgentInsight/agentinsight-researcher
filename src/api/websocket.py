"""WebSocket 双向实时通信 (P2-Future-02).

对标 GPTR backend/server/websocket_manager.py.
AGENTS.md 第 14 章: 新增 /v1/ws/{session_id} 为允许调用的端点 (人在回路审核请求通道).

WebSocket 消息类型 (对标 GPTR 8 类):
    1. logs: 日志信息
    2. content: 内容块 (报告正文流式)
    3. node_progress: 节点进度
    4. sources: 检索来源
    5. tool_call: 工具调用
    6. report: 完整报告
    7. human_feedback_request: 人在回路审核请求 (P0-Future-03)
    8. error: 错误信息

接收消息类型:
    - ping → 回 pong
    - human_feedback → 提交到 feedback_queue (P0-Future-03)

注: SSE 仍是主通道 (/v1/chat/completions stream=true), WebSocket 是增强通道,
用于人在回路审核请求推送与实时进度结构化推送.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.api.feedback_queue import get_feedback_queue
from src.config.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["websocket"])

# ========== WebSocket 消息类型常量 (对标 GPTR 8 类) ==========

WS_MSG_LOGS = "logs"
WS_MSG_CONTENT = "content"
WS_MSG_NODE_PROGRESS = "node_progress"
WS_MSG_SOURCES = "sources"
WS_MSG_TOOL_CALL = "tool_call"
WS_MSG_REPORT = "report"
WS_MSG_HUMAN_FEEDBACK_REQUEST = "human_feedback_request"
WS_MSG_ERROR = "error"

ALL_WS_MSG_TYPES: tuple[str, ...] = (
    WS_MSG_LOGS,
    WS_MSG_CONTENT,
    WS_MSG_NODE_PROGRESS,
    WS_MSG_SOURCES,
    WS_MSG_TOOL_CALL,
    WS_MSG_REPORT,
    WS_MSG_HUMAN_FEEDBACK_REQUEST,
    WS_MSG_ERROR,
)


class WebSocketManager:
    """按 session_id 索引的 WebSocket 连接管理器.

    对标 GPTR backend/server/websocket_manager.py WebSocketManager.
    """

    _instance: ClassVar[WebSocketManager | None] = None

    def __init__(self) -> None:
        self._active_connections: dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, session_id: str) -> None:
        """接受连接并存储 (覆盖同 session_id 旧连接)."""
        old = self._active_connections.get(session_id)
        if old is not None:
            try:
                await old.close(code=1000, reason="被新连接替换")
            except Exception:  # noqa: BLE001
                pass
        await websocket.accept()
        self._active_connections[session_id] = websocket
        logger.info("WebSocket 已连接: session_id=%s", session_id)

    def disconnect(self, session_id: str) -> None:
        """移除连接."""
        self._active_connections.pop(session_id, None)
        logger.info("WebSocket 已断开: session_id=%s", session_id)

    def is_connected(self, session_id: str) -> bool:
        """是否已连接."""
        return session_id in self._active_connections

    async def send_message(self, session_id: str, message: dict[str, Any]) -> bool:
        """发送 JSON 消息到指定 session.

        返回是否成功 (False 表示无连接或发送失败).
        """
        ws = self._active_connections.get(session_id)
        if ws is None:
            return False
        try:
            await ws.send_json(message)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("WebSocket 发送失败 session=%s: %s", session_id, e)
            self.disconnect(session_id)
            return False

    async def broadcast(self, session_ids: list[str], message: dict[str, Any]) -> None:
        """批量发送到多个 session."""
        for sid in session_ids:
            await self.send_message(sid, message)


_ws_manager: WebSocketManager | None = None


def get_websocket_manager() -> WebSocketManager:
    """全局单例 (异步协调器, 非业务状态)."""
    global _ws_manager
    if _ws_manager is None:
        _ws_manager = WebSocketManager()
    return _ws_manager


# ========== WebSocket 端点 ==========


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    """WebSocket 双向通信端点.

    AGENTS.md 第 14 章: /v1/ws/{session_id} 为允许调用的端点 (人在回路通道).

    接收消息:
        - {"type": "ping"} → 回 {"type": "pong"}
        - {"type": "human_feedback", "feedback": "..."} → 提交到 feedback_queue

    session_id 即 thread_id, 做会话隔离键 (AGENTS.md 第 6 章).
    """
    settings = get_settings()
    if not settings.websocket_enabled:
        await websocket.accept()
        await websocket.close(code=1008, reason="WebSocket 未启用")
        return

    manager = get_websocket_manager()
    await manager.connect(websocket, session_id)

    # 发送连接成功消息
    await manager.send_message(
        session_id,
        {"type": WS_MSG_LOGS, "message": "WebSocket 已连接", "session_id": session_id},
    )

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "ping":
                await manager.send_message(session_id, {"type": "pong"})

            elif msg_type == "human_feedback":
                feedback = str(data.get("feedback", ""))
                feedback_queue = get_feedback_queue()
                ok = feedback_queue.put_feedback(session_id, feedback)
                if not ok:
                    await manager.send_message(
                        session_id,
                        {
                            "type": WS_MSG_ERROR,
                            "message": "无待处理的反馈请求或反馈已提交",
                        },
                    )

            else:
                await manager.send_message(
                    session_id,
                    {"type": WS_MSG_ERROR, "message": f"未知消息类型: {msg_type}"},
                )

    except WebSocketDisconnect:
        manager.disconnect(session_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("WebSocket 异常 session=%s: %s", session_id, e)
        manager.disconnect(session_id)
