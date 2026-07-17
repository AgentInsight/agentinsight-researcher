"""WebSocket 双向实时通信.

新增 /v1/ws/{session_id} 为允许调用的端点 (人在回路审核请求通道).

WebSocket 消息类型 (8 类):
    1. logs: 日志信息
    2. content: 内容块 (报告正文流式)
    3. node_progress: 节点进度
    4. sources: 检索来源
    5. tool_call: 工具调用
    6. report: 完整报告
    7. human_feedback_request: 人在回路审核请求
    8. error: 错误信息

接收消息类型:
    - ping → 回 pong
    - human_feedback → 提交到 feedback_queue

注: SSE 仍是主通道 (/v1/chat/completions stream=true), WebSocket 是增强通道,
用于人在回路审核请求推送与实时进度结构化推送.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.api.feedback_queue import get_feedback_queue
from src.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["websocket"])

# ========== WebSocket 消息类型常量 (8 类) ==========

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


# 模块级 httpx client 已移除: _verify_token 改用本地 PyJWT 解析 (复用 JWTAuthMiddleware 逻辑),
# 不再调用远程 /api/user, 不再依赖 settings.user_info_api_url/user_info_api_timeout (字段已移除).


async def close_verify_client() -> None:
    """关闭 WebSocket token 验证模块级 httpx client (已废弃, 保留为 no-op 兼容).

    历史用途: lifespan shutdown 时关闭模块级 httpx.AsyncClient 单例.
    现状: ``_verify_token`` 已改用本地 PyJWT 解析, 不再使用 httpx client.
    保留此函数仅为向后兼容 (``server.py`` lifespan 与单元测试仍引用).
    幂等: 无副作用.
    """
    # no-op: 本地 JWT 解析不使用 httpx client
    return


def _decode_jwt_local(token: str, settings: Settings) -> str | None:
    """本地 PyJWT 解析 Token, 返回 user_id (复用 JWTAuthMiddleware._verify_jwt_local 逻辑).

    使用 ``jwt_signing_key`` (HS256) 本地验证, 不调用远程 API.
    失败返回 None, 由调用方决定降级或拒绝.

    禁止将原始 token 写入日志.
    """
    if not settings.jwt_signing_key:
        logger.error("WebSocket JWT 本地解析失败: jwt_signing_key 未配置")
        return None
    try:
        import jwt  # PyJWT

        payload = jwt.decode(
            token,
            settings.jwt_signing_key,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer or None,
            audience=settings.jwt_audience or None,
            options={"verify_exp": True, "require": ["exp", "iat"]},
            leeway=settings.jwt_clock_skew,
        )
        # AgentInsightService JWT Claims: UserId (字符串)
        user_id = str(payload.get("UserId") or payload.get("user_id") or "")
        if user_id:
            return user_id
        logger.warning("WebSocket JWT 本地解析失败: payload 中未包含 UserId/user_id")
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("WebSocket JWT 本地解析失败: %s", e)
        return None


async def _verify_token(token: str, settings: Settings) -> tuple[bool, str | None]:
    """验证 JWT token 有效性 (本地 PyJWT 解析, 复用 JWTAuthMiddleware 逻辑).

    使用 ``settings.jwt_signing_key`` (HS256) 本地验证, 不调用远程 API.
    禁止将原始 token 写入日志.

    向后兼容:
        - ``jwt_local_verify=True`` (默认): 走本地 JWT 解析.
        - ``jwt_local_verify=False``: 远程验证已废弃 (``user_info_api_url`` 字段已从 Settings
          移除), 为保持安全性降级为本地解析 (若 ``jwt_signing_key`` 已配置), 否则拒绝.

    Returns:
        ``(True, user_id)`` 表示 token 有效; ``(False, None)`` 表示无效或验证失败.
    """
    if not token:
        logger.warning("WebSocket token 验证失败: token 为空")
        return False, None

    # 本地 JWT 解析 (默认启用)
    if settings.jwt_local_verify:
        user_id = _decode_jwt_local(token, settings)
        if user_id:
            logger.info("WebSocket JWT 本地解析成功: user_id=%s", user_id)
            return True, user_id
        return False, None

    # jwt_local_verify=False: 远程验证已废弃 (settings.user_info_api_url 已移除),
    # 降级为本地解析 (若 jwt_signing_key 已配置), 否则拒绝以保持安全性
    logger.warning(
        "WebSocket: jwt_local_verify=False, 远程验证已废弃 (user_info_api_url 字段已移除); "
        "降级为本地 JWT 解析"
    )
    user_id = _decode_jwt_local(token, settings)
    if user_id:
        logger.info("WebSocket JWT 降级本地解析成功: user_id=%s", user_id)
        return True, user_id
    return False, None


class WebSocketManager:
    """按 session_id 索引的 WebSocket 连接管理器."""

    _instance: ClassVar[WebSocketManager | None] = None
    _MAX_CONNECTIONS: ClassVar[int] = 100  # 并发连接上限, 防止内存无界增长

    def __init__(self) -> None:
        self._active_connections: dict[str, WebSocket] = {}
        # 连接级 user_id 上下文 (按 session_id 索引, 用于日志关联与数据隔离)
        self._connection_user_ids: dict[str, str] = {}
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def connect(self, websocket: WebSocket, session_id: str) -> bool:
        """接受连接并存储 (覆盖同 session_id 旧连接).

        新增 Origin 校验 + JWT token 校验, 防止 CSWSH 攻击.
        返回 True 表示连接成功, False 表示被拒绝 (已发送 close 帧).
        """
        settings = get_settings()

        # ========== Origin 校验 (防 CSWSH 跨站 WebSocket 劫持) ==========
        # 生产环境强制开启; dev 环境可配置放宽但仍记录警告
        origin_check_enabled = settings.ws_origin_check or settings.env == "prod"
        if origin_check_enabled:
            origin = websocket.headers.get("origin", "")
            allowed = settings.cors_origins_list  # 复用 CORS 白名单
            # 支持 CORS_ALLOW_ORIGINS=* 通配符 (与 Starlette CORSMiddleware 行为一致)
            # "*" not in allowed 时才逐条匹配; 含 "*" 则放行所有 Origin
            if origin and "*" not in allowed and origin not in allowed:
                logger.warning(
                    "WebSocket Origin 拒绝: origin=%s session_id=%s",
                    origin,
                    session_id,
                )
                await websocket.close(code=4003, reason="Origin not allowed")
                return False
        elif settings.env == "dev":
            logger.warning("DEV: WebSocket Origin 校验已关闭 (不安全), session_id=%s", session_id)

        # ========== JWT Token 校验 ==========
        # 生产环境强制开启; dev 环境可配置放宽但仍记录警告
        auth_required = settings.ws_auth_required or settings.env == "prod"
        if auth_required:
            # 从 query params 或 Authorization 头提取 token
            token = (
                websocket.query_params.get("token")
                or websocket.headers.get("authorization", "").replace("Bearer ", "").strip()
            )
            if not token:
                logger.warning("WebSocket 拒绝连接 (缺少 token): session_id=%s", session_id)
                await websocket.close(code=4001, reason="Missing authentication token")
                return False
            # 本地 JWT 解析 (复用 JWTAuthMiddleware 逻辑, 不再调用远程 /api/user)
            ok, user_id = await _verify_token(token, settings)
            if not ok or not user_id:
                logger.warning(
                    "WebSocket 拒绝连接 (token 无效或解析失败): session_id=%s",
                    session_id,
                )
                await websocket.close(code=4001, reason="Invalid token")
                return False
            # 存入连接上下文 (用于后续日志关联与数据隔离)
            self._connection_user_ids[session_id] = user_id
            logger.info("WebSocket JWT 鉴权通过: session_id=%s user_id=%s", session_id, user_id)
        elif settings.env == "dev":
            logger.warning("DEV: WebSocket JWT 鉴权已关闭 (不安全), session_id=%s", session_id)

        # 并发连接上限, 防止内存无界增长
        if len(self._active_connections) >= self._MAX_CONNECTIONS:
            logger.warning("WebSocket 连接数已达上限 %d, 拒绝新连接", self._MAX_CONNECTIONS)
            await websocket.close(code=1013, reason="Try again later")
            return False

        old = self._active_connections.get(session_id)
        if old is not None:
            try:
                await old.close(code=1000, reason="被新连接替换")
            except Exception:  # noqa: BLE001
                pass
        await websocket.accept()
        self._active_connections[session_id] = websocket
        logger.info("WebSocket 已连接: session_id=%s", session_id)
        return True

    def disconnect(self, session_id: str) -> None:
        """移除连接."""
        self._active_connections.pop(session_id, None)
        self._connection_user_ids.pop(session_id, None)
        logger.info("WebSocket 已断开: session_id=%s", session_id)

    def is_connected(self, session_id: str) -> bool:
        """是否已连接."""
        return session_id in self._active_connections

    def get_user_id(self, session_id: str) -> str:
        """获取连接级 user_id (用于日志关联与数据隔离).

        无连接或未鉴权时返回空字符串.
        """
        return self._connection_user_ids.get(session_id, "")

    async def send_message(self, session_id: str, message: dict[str, Any]) -> bool:
        """发送 JSON 消息到指定 session.

        增加 5s 超时, 防止 TCP 窗口满时无限阻塞.

        Returns:
            是否成功 (False 表示无连接或发送失败).
        """
        ws = self._active_connections.get(session_id)
        if ws is None:
            return False
        try:
            # 5s 超时防止慢客户端阻塞
            await asyncio.wait_for(ws.send_json(message), timeout=5.0)
            return True
        except asyncio.TimeoutError:  # noqa: UP041
            logger.warning("WebSocket 发送超时 session=%s, 断开慢客户端", session_id)
            self.disconnect(session_id)
            return False
        except Exception as e:  # noqa: BLE001
            logger.warning("WebSocket 发送失败 session=%s: %s", session_id, e)
            self.disconnect(session_id)
            return False

    async def broadcast(self, session_ids: list[str], message: dict[str, Any]) -> None:
        """批量发送到多个 session (并行 + 超时).

        改用 asyncio.gather 并行发送, return_exceptions 避免单失败中断.
        """
        if not session_ids:
            return
        await asyncio.gather(
            *[self.send_message(sid, message) for sid in session_ids],
            return_exceptions=True,
        )

    def start_heartbeat(self) -> None:
        """启动服务端心跳任务 (检测并清理死连接)."""
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop_heartbeat(self) -> None:
        """停止心跳任务."""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

    async def _heartbeat_loop(self) -> None:
        """心跳循环: 每 30s ping 所有连接, 超时/失败则断开."""
        while True:
            await asyncio.sleep(30)
            dead_sessions: list[str] = []
            for sid, ws in list(self._active_connections.items()):
                try:
                    await asyncio.wait_for(ws.send_json({"type": "ping"}), timeout=5.0)
                except Exception:  # noqa: BLE001
                    dead_sessions.append(sid)
            for sid in dead_sessions:
                logger.info("WebSocket 心跳超时, 断开死连接: session_id=%s", sid)
                self.disconnect(sid)


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

    /v1/ws/{session_id} 为允许调用的端点 (人在回路通道).

    接收消息:
        - {"type": "ping"} → 回 {"type": "pong"}
        - {"type": "human_feedback", "feedback": "..."} → 提交到 feedback_queue

    session_id 即 thread_id, 做会话隔离键.
    """
    settings = get_settings()
    if not settings.websocket_enabled:
        await websocket.accept()
        await websocket.close(code=1008, reason="WebSocket 未启用")
        return

    manager = get_websocket_manager()
    connected = await manager.connect(websocket, session_id)
    if not connected:
        # Origin/JWT 校验失败, connect() 已发送 close 帧, 直接返回
        return

    # 启动心跳检测
    manager.start_heartbeat()

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
