"""API 中间件.

AGENTS.md 第 8/11 章硬约束:
- JWT 验证与 user_id 获取必须在 API 入口中间件完成
- self_host=True (自托管): token 不存在或调用失败时降级 DEFAULT_USER_ID
- self_host=False (云托管): 强制校验 JWT Token, 不存在或取不到 User 信息时返回 401
- 禁止将原始 JWT token 写入日志或持久化存储
- 安全响应头中间件不可绕过
- CORS * 限制已移除 (AGENTS.md 第 11 章已更新)
"""

from __future__ import annotations

import contextvars
import logging

import httpx
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from src.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# 请求级上下文变量 (AGENTS.md 第 10 章: 认证上下文用 contextvars, 不用 span 上下文)
_request_user_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_user_id",
    default="",
)
_request_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_session_id",
    default="",
)
_request_agent_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_agent_id",
    default="",
)


def get_request_user_id() -> str:
    """获取当前请求的 user_id (从 contextvars 恢复)."""
    return _request_user_id.get()


def get_request_session_id() -> str:
    """获取当前请求的 session_id."""
    return _request_session_id.get()


def get_request_agent_id() -> str:
    """获取当前请求的 agent_id."""
    return _request_agent_id.get()


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """JWT 身份解析中间件.

    AGENTS.md 第 8 章硬约束:
    - Bearer JWT Token 可选, 不存在时走匿名用户路径 (self_host=True 自托管模式)
    - self_host=False (云托管模式): 强制校验 JWT Token, 不存在或取不到 User 信息时返回 401
    - token 存在时: 同步调用 GET /api/user 获取 user_id, 携带原 Authorization 头
    - self_host=True 时: 调用失败/超时降级 DEFAULT_USER_ID 并告警
    - 禁止将原始 JWT token 写入日志或持久化存储
    """

    def __init__(self, app: ASGIApp, settings: Settings | None = None) -> None:
        super().__init__(app)
        self.settings = settings or get_settings()
        self._client = httpx.AsyncClient(timeout=self.settings.user_info_api_timeout)

    # 公开路径白名单 (无需 JWT 校验, AGENTS.md 第 14 章: /health 与测试页面静态资源)
    # /.well-known/agent-discovery.json 为 Agent Discovery Protocol 公开发现端点 (无需鉴权)
    _PUBLIC_PATHS: tuple[str, ...] = (
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/favicon.ico",
        "/.well-known/agent-discovery.json",
    )

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """解析 JWT Token 并注入 user_id 到请求上下文."""
        # 注入 agent_id (固定为 agent_name)
        _request_agent_id.set(self.settings.agent_name)

        # 公开路径白名单: /health, /docs 等 JWT 中间件跳过 (健康检查与文档不应强制 JWT)
        path = request.url.path
        if path in self._PUBLIC_PATHS or path.startswith("/static/"):
            return await call_next(request)

        # 从请求头提取 Authorization Bearer Token
        auth_header = request.headers.get("Authorization", "")
        token = self._extract_bearer_token(auth_header)

        # 解析 user_id
        user_id, error = await self._resolve_user_id(token)
        if error:
            # SELF_HOST=False 时返回 401 (token 不存在或校验失败)
            return JSONResponse(
                status_code=401,
                content={"error": {"message": error, "type": "authentication_error"}},
            )
        assert user_id is not None  # error is None implies user_id is not None
        _request_user_id.set(user_id)

        # 从查询参数或请求体提取 session_id (thread_id)
        session_id = request.query_params.get("session_id") or request.headers.get(
            "X-Session-Id",
            "",
        )
        if not session_id:
            # 没有显式 session_id, 生成临时 uuid (实际由 LangGraph 注入)
            import uuid as _uuid

            session_id = str(_uuid.uuid4())
        _request_session_id.set(session_id)

        response = await call_next(request)
        return response

    def _extract_bearer_token(self, auth_header: str) -> str:
        """从 Authorization 头提取 Bearer Token.

        禁止将原始 token 写入日志.
        """
        if not auth_header:
            return ""
        if not auth_header.lower().startswith("bearer "):
            return ""
        return auth_header[7:].strip()

    async def _resolve_user_id(self, token: str) -> tuple[str | None, str | None]:
        """解析 user_id.

        返回 (user_id, error_message):
        - self_host=True: token 不存在或失败时降级到 default_user_id (AGENTS.md 第 8 章现有逻辑)
        - self_host=False: token 不存在或失败时返回错误 (云托管强制校验)
        """
        if not token:
            if self.settings.self_host:
                return self.settings.default_user_id, None
            return None, "缺少 Authorization Bearer Token"

        try:
            response = await self._client.get(
                self.settings.user_info_api_url,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            data = response.json()
            user_id = str(data.get("id") or data.get("user_id") or "")
            if user_id:
                return user_id, None
            logger.warning("user_id 解析返回空")
            if self.settings.self_host:
                return self.settings.default_user_id, None
            return None, "通过 Token 无法获取 User 信息"
        except httpx.TimeoutException:
            logger.warning("user_id 解析超时 (%ss)", self.settings.user_info_api_timeout)
            if self.settings.self_host:
                return self.settings.default_user_id, None
            return None, "Token 校验失败: TimeoutException"
        except Exception as e:  # noqa: BLE001
            logger.warning("user_id 解析失败: %s", e)
            if self.settings.self_host:
                return self.settings.default_user_id, None
            return None, f"Token 校验失败: {type(e).__name__}"

    async def aclose(self) -> None:
        """关闭 HTTP 客户端."""
        await self._client.aclose()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """安全响应头中间件.

    AGENTS.md 第 11 章硬约束: 安全响应头中间件不可绕过.
    - X-Content-Type-Options: nosniff
    - X-Frame-Options: DENY
    - Strict-Transport-Security: HSTS (生产强制 HTTPS)
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        settings = get_settings()
        if settings.env == "prod":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )
        return response
