"""API 中间件 (纯 ASGI middleware, 不使用 BaseHTTPMiddleware).

安全约束:
- JWT 验证与 user_id 获取必须在 API 入口中间件完成
- 统一降级策略: Token 不存在/解析失败 → IP-based UserId (无论 self_host 值)
- 本地 JWT 解析 (PyJWT + HS256), 不再调用远程 API
- 禁止将原始 JWT token 写入日志或持久化存储
- 安全响应头中间件不可绕过
- CORS * 限制已移除

BaseHTTPMiddleware 会将请求包裹在内部 task 中, 对 StreamingResponse (SSE) 有性能开销.
改用纯 ASGI middleware (__call__ 方法), 避免 Starlette 内部 task 包装开销.
"""

from __future__ import annotations

import contextvars
import logging
import uuid as _uuid

from starlette.requests import Request
from starlette.types import ASGIApp, Message

from src.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# 请求级上下文变量 (认证上下文用 contextvars, 不用 span 上下文)
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
# 客户端 IP (用于 IP-based UserId 生成 + SearXNG X-Forwarded-For)
_request_client_ip: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_client_ip",
    default="",
)
# 统一请求追踪 ID (用于日志关联 + 分布式追踪)
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id",
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


def get_request_client_ip() -> str:
    """获取当前请求的客户端 IP (用于 IP-based UserId + X-Forwarded-For)."""
    return _request_client_ip.get()


def get_request_id() -> str:
    """获取当前请求的追踪 ID."""
    return _request_id.get()


# 模块级跟踪 JWTAuthMiddleware 实例 (纯 ASGI middleware 无法从 app 获取实例)
_jwt_middleware_instance: JWTAuthMiddleware | None = None


class RequestIDMiddleware:
    """统一请求追踪 ID 中间件.

    - 从 X-Request-ID 请求头提取, 不存在则生成 UUID.
    - 注入 contextvars 供日志关联.
    - 回写到响应头 X-Request-ID.
    - 纯 ASGI 实现 (不使用 BaseHTTPMiddleware).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Message, receive: Message, send: Message) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 从请求头提取 X-Request-ID, 不存在则生成
        request_id = ""
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                request_id = value.decode("latin-1")
                break
        if not request_id:
            request_id = str(_uuid.uuid4())
        _request_id.set(request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # 检查是否已有 X-Request-ID (避免重复)
                existing = any(name == b"x-request-id" for name, _ in headers)
                if not existing:
                    headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_request_id)


class JWTAuthMiddleware:
    """JWT 身份解析中间件 (纯 ASGI middleware).

    安全约束:
    - 统一降级策略: Token 不存在/解析失败 → IP-based UserId (无论 self_host 值)
    - 本地 JWT 解析 (PyJWT + HS256), 不再调用远程 API
    - 禁止将原始 JWT token 写入日志或持久化存储

    改用纯 ASGI __call__, 避免 BaseHTTPMiddleware 对 StreamingResponse 的 task 包装开销.
    """

    def __init__(self, app: ASGIApp, settings: Settings | None = None) -> None:
        self.app = app
        self.settings = settings or get_settings()
        # 注册到模块级变量, 供 close_jwt_middleware() 在 lifespan shutdown 时调用
        global _jwt_middleware_instance
        _jwt_middleware_instance = self

    # 公开路径白名单 (无需 JWT 校验: /health 与测试页面静态资源)
    # /.well-known/agent-discovery.json 为 Agent Discovery Protocol 公开发现端点 (无需鉴权)
    _PUBLIC_PATHS: tuple[str, ...] = (
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/favicon.ico",
        "/.well-known/agent-discovery.json",
    )

    async def __call__(self, scope: Message, receive: Message, send: Message) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 从 scope 构建 Request 仅用于读取 header/path/query (不消费 body)
        request = Request(scope, receive=receive)

        # 注入 agent_id (固定为 agent_name)
        _request_agent_id.set(self.settings.agent_name)

        # 提取并注入客户端 IP (用于 IP-based UserId + SearXNG X-Forwarded-For)
        from src.api.ip_user_resolver import get_client_ip

        client_ip = get_client_ip(request)
        _request_client_ip.set(client_ip)

        # 公开路径白名单: /health, /docs 等 JWT 中间件跳过
        path = request.url.path
        if path in self._PUBLIC_PATHS or path.startswith("/static/"):
            await self.app(scope, receive, send)
            return

        # 从请求头提取 Authorization Bearer Token
        auth_header = request.headers.get("Authorization", "")
        token = self._extract_bearer_token(auth_header)

        # 解析 user_id (统一降级策略: Token 不存在/解析失败 → IP-based)
        user_id, error = await self._resolve_user_id(token, client_ip)
        # 统一降级后 error 始终为 None, user_id 始终非 None
        assert user_id is not None
        _request_user_id.set(user_id)

        # 从查询参数或请求体提取 session_id (thread_id)
        session_id = request.query_params.get("session_id") or request.headers.get(
            "X-Session-Id",
            "",
        )
        if not session_id:
            # 没有显式 session_id, 生成临时 uuid (实际由 LangGraph 注入)
            session_id = str(_uuid.uuid4())
        _request_session_id.set(session_id)

        await self.app(scope, receive, send)

    def _extract_bearer_token(self, auth_header: str) -> str:
        """从 Authorization 头提取 Bearer Token.

        禁止将原始 token 写入日志.
        """
        if not auth_header:
            return ""
        if not auth_header.lower().startswith("bearer "):
            return ""
        return auth_header[7:].strip()

    async def _resolve_user_id(
        self, token: str, client_ip: str = ""
    ) -> tuple[str | None, str | None]:
        """解析 user_id. 统一降级策略: Token 不存在/解析失败 → IP-based UserId.

        无论 self_host 值, 统一走 IP-based 降级, 不再返回错误.
        本地 JWT 解析 (PyJWT), 不再调用远程 API.
        """
        from src.api.ip_user_resolver import generate_user_id_from_ip

        # 无 Token → 直接 IP-based 降级
        if not token:
            ip_user_id = generate_user_id_from_ip(client_ip)
            return ip_user_id, None

        # 优先本地 JWT 解析
        if self.settings.jwt_local_verify and self.settings.jwt_signing_key:
            user_id = self._verify_jwt_local(token)
            if user_id:
                return user_id, None
            logger.warning("JWT 本地解析失败, 降级到 IP-based UserId")

        # 本地解析失败或未启用 → IP-based 降级 (统一行为)
        ip_user_id = generate_user_id_from_ip(client_ip)
        return ip_user_id, None

    def _verify_jwt_local(self, token: str) -> str | None:
        """本地 PyJWT 解析 Token, 返回 user_id.

        使用 jwt_signing_key (HS256) 本地验证, 不调用远程 API.
        失败返回 None, 由调用方降级到 IP-based.
        """
        import jwt  # PyJWT

        try:
            payload = jwt.decode(
                token,
                self.settings.jwt_signing_key,
                algorithms=[self.settings.jwt_algorithm],
                issuer=self.settings.jwt_issuer or None,
                audience=self.settings.jwt_audience or None,
                options={"verify_exp": True, "require": ["exp", "iat"]},
                leeway=self.settings.jwt_clock_skew,
            )
            # AgentInsightService JWT Claims: UserId (字符串)
            user_id = str(payload.get("UserId") or payload.get("user_id") or "")
            return user_id or None
        except Exception as e:  # noqa: BLE001
            logger.warning("JWT 本地解析失败: %s", e)
            return None


class SecurityHeadersMiddleware:
    """安全响应头中间件 (纯 ASGI middleware).

    安全响应头中间件不可绕过.
    - X-Content-Type-Options: nosniff
    - X-Frame-Options: DENY
    - Strict-Transport-Security: HSTS (生产强制 HTTPS)

    改用纯 ASGI __call__, 通过拦截 send 回调注入响应头,
    避免 BaseHTTPMiddleware 对 StreamingResponse 的 task 包装开销.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Message, receive: Message, send: Message) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # 安全响应头 (不可绕过)
                security_headers: list[tuple[bytes, bytes]] = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"x-xss-protection", b"1; mode=block"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                ]
                settings = get_settings()
                if settings.env == "prod":
                    security_headers.append(
                        (
                            b"strict-transport-security",
                            b"max-age=31536000; includeSubDomains; preload",
                        )
                    )
                # 仅添加不存在的头 (避免重复)
                existing = {name.lower() for name, _ in headers}
                for name, value in security_headers:
                    if name not in existing:
                        headers.append((name, value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


async def close_jwt_middleware() -> None:
    """JWT 中间件清理 (lifespan shutdown 调用).

    不再需要关闭 HTTP 客户端, 仅清理模块级引用.
    幂等: 无实例时直接返回.
    """
    global _jwt_middleware_instance
    _jwt_middleware_instance = None
