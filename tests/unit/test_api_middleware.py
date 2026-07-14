"""单元测试: API 中间件.

验证 src/api/middleware.py:
- JWTAuthMiddleware: 无 token / 有效 token / 无效 token / 无 signing_key → user_id 解析
- SecurityHeadersMiddleware: 安全响应头注入 + HSTS (prod 环境)
- 请求上下文 getter: get_request_user_id/get_request_session_id/get_request_agent_id

单元测试不依赖外部服务.
本地 JWT 解析 (PyJWT + HS256) + IP-based 降级.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import jwt as pyjwt
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

# ========== JWT 测试辅助 ==========

_JWT_SECRET = "test-jwt-secret-key"  # 测试用密钥, 不入仓


def _make_jwt(payload: dict[str, Any], secret: str = _JWT_SECRET) -> str:
    """生成测试用 JWT token (HS256)."""
    claims = {
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        **payload,
    }
    return pyjwt.encode(claims, secret, algorithm="HS256")


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


def test_jwt_no_auth_header_uses_ip_based_user_id() -> None:
    """无 Authorization 头 → 降级 IP-based UserId.

    无 token 时按客户端 IP 生成确定性 UserId
    (TestClient 默认 client host 为 "testclient",
    generate_user_id_from_ip("testclient") = "ip_846488f1dc5c07b4cebe5c14").
    """
    settings = Settings(_env_file=None, agent_name="test-agent", jwt_signing_key=_JWT_SECRET, jwt_issuer="", jwt_audience="")
    app = _make_test_app(settings)
    client = TestClient(app)

    response = client.get("/test")
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == "ip_846488f1dc5c07b4cebe5c14"
    assert data["agent_id"] == "test-agent"


# ========== JWTAuthMiddleware: 有效 Bearer token (本地解析) ==========


def test_jwt_bearer_token_local_verification_returns_user_id() -> None:
    """有效 Bearer JWT token: 本地 PyJWT 解析返回真实 user_id.

    不再调用远程 user_info API, 直接本地解析 token 的 UserId claim.
    """
    settings = Settings(_env_file=None, agent_name="test-agent", jwt_signing_key=_JWT_SECRET, jwt_issuer="", jwt_audience="")
    token = _make_jwt({"UserId": "real-user-123"})

    app = _make_test_app(settings)
    client = TestClient(app)

    response = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["user_id"] == "real-user-123"


def test_jwt_bearer_token_user_id_field_fallback() -> None:
    """JWT payload 优先 UserId 字段, 其次 user_id 字段 (小写)."""
    settings = Settings(_env_file=None, agent_name="test-agent", jwt_signing_key=_JWT_SECRET, jwt_issuer="", jwt_audience="")
    token = _make_jwt({"user_id": "alt-user-456"})

    app = _make_test_app(settings)
    client = TestClient(app)

    response = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["user_id"] == "alt-user-456"


# ========== JWTAuthMiddleware: token 解析失败 / 无效 ==========


def test_jwt_invalid_token_falls_back_to_ip(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """无效 JWT token → 降级 IP-based UserId + 告警.

    JWT 本地解析失败时, 按客户端 IP 生成确定性 UserId 并记录告警.
    """
    settings = Settings(_env_file=None, agent_name="test-agent", jwt_signing_key=_JWT_SECRET, jwt_issuer="", jwt_audience="")

    app = _make_test_app(settings)
    client = TestClient(app)

    with caplog.at_level(logging.WARNING):
        response = client.get(
            "/test", headers={"Authorization": "Bearer not-a-valid-jwt-token"}
        )

    assert response.status_code == 200
    assert response.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"
    # 应记录解析失败告警
    assert any("JWT 本地解析失败" in rec.message for rec in caplog.records)


def test_jwt_token_with_wrong_secret_falls_back_to_ip(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """JWT 用错误密钥签名 → 本地解析失败 → 降级 IP-based UserId + 告警."""
    settings = Settings(_env_file=None, agent_name="test-agent", jwt_signing_key=_JWT_SECRET, jwt_issuer="", jwt_audience="")
    # 用不同密钥签发 token
    token = _make_jwt({"UserId": "wrong-secret-user"}, secret="another-secret")

    app = _make_test_app(settings)
    client = TestClient(app)

    with caplog.at_level(logging.WARNING):
        response = client.get("/test", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"
    assert any("JWT 本地解析失败" in rec.message for rec in caplog.records)


def test_jwt_token_expired_falls_back_to_ip(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """过期 JWT token → 本地解析失败 → 降级 IP-based UserId + 告警."""
    settings = Settings(_env_file=None, agent_name="test-agent", jwt_signing_key=_JWT_SECRET, jwt_issuer="", jwt_audience="")
    # 签发已过期的 token
    expired_payload = {
        "UserId": "expired-user",
        "exp": int(time.time()) - 3600,  # 1 小时前过期
        "iat": int(time.time()) - 7200,
    }
    token = pyjwt.encode(expired_payload, _JWT_SECRET, algorithm="HS256")

    app = _make_test_app(settings)
    client = TestClient(app)

    with caplog.at_level(logging.WARNING):
        response = client.get("/test", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"
    assert any("JWT 本地解析失败" in rec.message for rec in caplog.records)


def test_jwt_empty_user_id_claim_falls_back_to_ip(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """JWT payload 中 UserId/user_id 均为空 → 降级 IP-based UserId + 告警."""
    settings = Settings(_env_file=None, agent_name="test-agent", jwt_signing_key=_JWT_SECRET, jwt_issuer="", jwt_audience="")
    token = _make_jwt({"UserId": "", "user_id": ""})

    app = _make_test_app(settings)
    client = TestClient(app)

    with caplog.at_level(logging.WARNING):
        response = client.get("/test", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"


def test_jwt_no_signing_key_falls_back_to_ip() -> None:
    """未配置 jwt_signing_key → 跳过本地解析, 直接 IP-based 降级.

    生产环境应配置 jwt_signing_key, 未配置时所有 token 都降级.
    """
    settings = Settings(_env_file=None, agent_name="test-agent", jwt_signing_key="", jwt_issuer="", jwt_audience="")
    token = _make_jwt({"UserId": "would-be-real-user"})

    app = _make_test_app(settings)
    client = TestClient(app)

    response = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    # 未配置密钥 → 跳过本地解析 → IP-based 降级
    assert response.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"


# ========== JWTAuthMiddleware: session_id 注入 ==========


def test_jwt_session_id_from_query_param() -> None:
    """session_id 可从查询参数注入."""
    settings = Settings(_env_file=None, jwt_signing_key=_JWT_SECRET, jwt_issuer="", jwt_audience="")
    app = _make_test_app(settings)
    client = TestClient(app)

    response = client.get("/test?session_id=my-session-123")
    assert response.status_code == 200
    assert response.json()["session_id"] == "my-session-123"


def test_jwt_session_id_from_header() -> None:
    """session_id 可从 X-Session-Id 头注入."""
    settings = Settings(_env_file=None, jwt_signing_key=_JWT_SECRET, jwt_issuer="", jwt_audience="")
    app = _make_test_app(settings)
    client = TestClient(app)

    response = client.get("/test", headers={"X-Session-Id": "header-session"})
    assert response.status_code == 200
    assert response.json()["session_id"] == "header-session"


def test_jwt_session_id_auto_generated_when_missing() -> None:
    """无显式 session_id → 自动生成 UUID."""
    settings = Settings(_env_file=None, jwt_signing_key=_JWT_SECRET, jwt_issuer="", jwt_audience="")
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
