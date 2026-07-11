"""单元测试: API 中间件.

验证 src/api/middleware.py:
- JWTAuthMiddleware: 无 token / 有 token / 调用失败 / 超时 → user_id 解析
- SecurityHeadersMiddleware: 安全响应头注入 + HSTS (prod 环境)
- 请求上下文 getter: get_request_user_id/get_request_session_id/get_request_agent_id

单元测试不依赖外部服务.
JWT 解析 + 安全响应头硬约束.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware import (
    JWTAuthMiddleware,
    SecurityHeadersMiddleware,
    _request_agent_id,
    _request_session_id,
    _request_user_id,
    get_request_agent_id,
    get_request_session_id,
    get_request_user_id,
)
from src.config.settings import Settings

# ========== Fake httpx client ==========


class _FakeResponse:
    """伪造 httpx.Response, 支持 raise_for_status 与 json."""

    def __init__(self, status_code: int = 200, json_data: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._json = json_data or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=httpx.Request("GET", "http://test"),
                response=self,
            )

    def json(self) -> dict[str, Any]:
        return self._json


class _FakeAsyncClient:
    """伪造 httpx.AsyncClient, 捕获 get 调用."""

    def __init__(
        self,
        response: _FakeResponse | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._response = response
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        self.calls.append({"url": url, "headers": headers})
        if self._exc is not None:
            raise self._exc
        if self._response is None:
            return _FakeResponse(200, {"id": "fake-user"})
        return self._response

    async def aclose(self) -> None:
        """无需操作 (测试用)."""


def _patch_httpx_client(monkeypatch: pytest.MonkeyPatch, fake: _FakeAsyncClient) -> None:
    """替换 httpx.AsyncClient 为 fake 实例 (中间件 __init__ 内调用)."""
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: fake)


def _make_test_app(settings: Settings) -> FastAPI:
    """创建测试用 FastAPI 应用 (含 JWTAuthMiddleware + SecurityHeadersMiddleware).

    端点 /test 返回当前请求上下文中的 user_id/session_id/agent_id.
    """
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint() -> dict[str, str]:
        return {
            "user_id": get_request_user_id(),
            "session_id": get_request_session_id(),
            "agent_id": get_request_agent_id(),
        }

    app.add_middleware(JWTAuthMiddleware, settings=settings)
    app.add_middleware(SecurityHeadersMiddleware)
    return app


# ========== JWTAuthMiddleware: 无 Authorization 头 ==========


def test_jwt_no_auth_header_uses_ip_based_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无 Authorization 头 → 降级 IP-based UserId (不调用 user_info API).

    default_user_id 环境变量已移除, 无 token 时按客户端
    IP 生成确定性 UserId (TestClient 默认 client host 为 "testclient",
    generate_user_id_from_ip("testclient") = "ip_846488f1dc5c07b4cebe5c14").
    """
    settings = Settings(
        _env_file=None,
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient()
    _patch_httpx_client(monkeypatch, fake)

    app = _make_test_app(settings)
    client = TestClient(app)

    response = client.get("/test")
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == "ip_846488f1dc5c07b4cebe5c14"
    assert data["agent_id"] == "test-agent"
    # 无 token 时不应调用 user_info API
    assert len(fake.calls) == 0


# ========== JWTAuthMiddleware: 有 Bearer token ==========


def test_jwt_bearer_token_calls_user_info_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有 Bearer token → 调用 user_info_api_url 获取 user_id."""
    settings = Settings(
        _env_file=None,
        agent_name="test-agent",
        user_info_api_url="https://fake.example.com/api/user",
    )
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"id": "real-user-123"}))
    _patch_httpx_client(monkeypatch, fake)

    app = _make_test_app(settings)
    client = TestClient(app)

    response = client.get("/test", headers={"Authorization": "Bearer my-jwt-token"})
    assert response.status_code == 200
    assert response.json()["user_id"] == "real-user-123"
    # 应调用 user_info API 一次, 携带原 Authorization 头
    assert len(fake.calls) == 1
    assert fake.calls[0]["url"] == "https://fake.example.com/api/user"
    assert fake.calls[0]["headers"] == {"Authorization": "Bearer my-jwt-token"}


def test_jwt_bearer_token_user_id_field_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """token 解析响应优先 id 字段, 其次 user_id 字段."""
    settings = Settings(_env_file=None)
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"user_id": "alt-user-456"}))
    _patch_httpx_client(monkeypatch, fake)

    app = _make_test_app(settings)
    client = TestClient(app)

    response = client.get("/test", headers={"Authorization": "Bearer tok"})
    assert response.status_code == 200
    assert response.json()["user_id"] == "alt-user-456"


# ========== JWTAuthMiddleware: token 调用失败 ==========


def test_jwt_token_call_fails_falls_back_to_ip(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """token 调用失败 (连接错误) → 降级 IP-based UserId + 告警.

    调用失败按无 token 处理并告警, 按 IP 生成确定性 UserId.
    """
    settings = Settings(_env_file=None)
    fake = _FakeAsyncClient(exc=httpx.ConnectError("connection refused"))
    _patch_httpx_client(monkeypatch, fake)

    app = _make_test_app(settings)
    client = TestClient(app)

    with caplog.at_level(logging.WARNING):
        response = client.get("/test", headers={"Authorization": "Bearer bad-token"})

    assert response.status_code == 200
    assert response.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"
    # 应记录解析失败告警 (中间件 _resolve_user_id 捕获异常并降级到 IP-based UserId)
    assert any("解析失败" in rec.message for rec in caplog.records)


def test_jwt_token_call_http_error_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """token 调用返回 HTTP 4xx/5xx → 降级 IP-based UserId."""
    settings = Settings(_env_file=None)
    fake = _FakeAsyncClient(response=_FakeResponse(401, {"error": "unauthorized"}))
    _patch_httpx_client(monkeypatch, fake)

    app = _make_test_app(settings)
    client = TestClient(app)

    response = client.get("/test", headers={"Authorization": "Bearer expired"})
    assert response.status_code == 200
    assert response.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"


def test_jwt_token_returns_empty_user_id_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """token 解析响应中 id/user_id 为空 → 降级 IP-based UserId."""
    settings = Settings(_env_file=None)
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"other": "value"}))
    _patch_httpx_client(monkeypatch, fake)

    app = _make_test_app(settings)
    client = TestClient(app)

    response = client.get("/test", headers={"Authorization": "Bearer tok"})
    assert response.status_code == 200
    assert response.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"


# ========== JWTAuthMiddleware: 超时 ==========


def test_jwt_timeout_falls_back_to_ip(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """超时 → 降级 IP-based UserId + 告警."""
    settings = Settings(
        _env_file=None,
        user_info_api_timeout=1,
    )
    fake = _FakeAsyncClient(exc=httpx.TimeoutException("timed out"))
    _patch_httpx_client(monkeypatch, fake)

    app = _make_test_app(settings)
    client = TestClient(app)

    with caplog.at_level(logging.WARNING):
        response = client.get("/test", headers={"Authorization": "Bearer slow-token"})

    assert response.status_code == 200
    assert response.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"
    assert any("超时" in rec.message for rec in caplog.records)


# ========== JWTAuthMiddleware: session_id 注入 ==========


def test_jwt_session_id_from_query_param(monkeypatch: pytest.MonkeyPatch) -> None:
    """session_id 可从查询参数注入."""
    settings = Settings(_env_file=None)
    _patch_httpx_client(monkeypatch, _FakeAsyncClient())

    app = _make_test_app(settings)
    client = TestClient(app)

    response = client.get("/test?session_id=my-session-123")
    assert response.status_code == 200
    assert response.json()["session_id"] == "my-session-123"


def test_jwt_session_id_from_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """session_id 可从 X-Session-Id 头注入."""
    settings = Settings(_env_file=None)
    _patch_httpx_client(monkeypatch, _FakeAsyncClient())

    app = _make_test_app(settings)
    client = TestClient(app)

    response = client.get("/test", headers={"X-Session-Id": "header-session"})
    assert response.status_code == 200
    assert response.json()["session_id"] == "header-session"


def test_jwt_session_id_auto_generated_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无显式 session_id → 自动生成 UUID."""
    settings = Settings(_env_file=None)
    _patch_httpx_client(monkeypatch, _FakeAsyncClient())

    app = _make_test_app(settings)
    client = TestClient(app)

    response = client.get("/test")
    assert response.status_code == 200
    session_id = response.json()["session_id"]
    assert len(session_id) > 0
    # UUID 格式: 8-4-4-4-12 (含 4 个连字符)
    assert session_id.count("-") == 4


# ========== SecurityHeadersMiddleware ==========


def _make_security_app() -> FastAPI:
    """创建仅含 SecurityHeadersMiddleware 的测试应用."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint() -> dict[str, str]:
        return {"ok": "true"}

    app.add_middleware(SecurityHeadersMiddleware)
    return app


def test_security_headers_injected() -> None:
    """测试安全响应头注入 (dev 环境, 无 HSTS)."""
    client = TestClient(_make_security_app())
    response = client.get("/test")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-XSS-Protection"] == "1; mode=block"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_security_headers_hsts_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    """env=prod 时注入 Strict-Transport-Security (HSTS) 头."""
    prod_settings = Settings(env="prod", _env_file=None)
    monkeypatch.setattr("src.api.middleware.get_settings", lambda: prod_settings)

    client = TestClient(_make_security_app())
    response = client.get("/test")
    assert response.headers["Strict-Transport-Security"] == (
        "max-age=31536000; includeSubDomains; preload"
    )


def test_security_headers_no_hsts_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    """dev 环境不注入 HSTS 头."""
    # 显式 mock dev 环境, 避免受 .env 中 ENV=prod 影响导致测试不稳定
    dev_settings = Settings(env="dev", _env_file=None)
    monkeypatch.setattr("src.api.middleware.get_settings", lambda: dev_settings)

    client = TestClient(_make_security_app())
    response = client.get("/test")
    assert "Strict-Transport-Security" not in response.headers


# ========== 请求上下文 getter ==========


def test_request_context_getters_return_contextvar_values() -> None:
    """测试 get_request_user_id/session_id/agent_id 直接读取 contextvar."""
    tok_u = _request_user_id.set("direct-user")
    tok_s = _request_session_id.set("direct-session")
    tok_a = _request_agent_id.set("direct-agent")
    try:
        assert get_request_user_id() == "direct-user"
        assert get_request_session_id() == "direct-session"
        assert get_request_agent_id() == "direct-agent"
    finally:
        _request_user_id.reset(tok_u)
        _request_session_id.reset(tok_s)
        _request_agent_id.reset(tok_a)


def test_request_context_getters_default_empty_in_fresh_context() -> None:
    """全新 context 中 getter 返回默认空字符串 (contextvar default='')."""
    import contextvars

    ctx = contextvars.Context()
    result: dict[str, str] = {}

    def _check() -> None:
        result["user_id"] = get_request_user_id()
        result["session_id"] = get_request_session_id()
        result["agent_id"] = get_request_agent_id()

    ctx.run(_check)
    assert result["user_id"] == ""
    assert result["session_id"] == ""
    assert result["agent_id"] == ""
