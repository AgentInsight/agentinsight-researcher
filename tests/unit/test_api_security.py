"""单元测试: API 安全 (本地 JWT 解析 + IP-based 统一降级 + 数据隔离键).

安全约束:
- Bearer JWT Token 可选, 不存在时按 IP 生成确定性 UserId (统一降级策略)
- 本轮改造: self_host=False 不再返回 401, 统一降级到 IP-based
- 本地 JWT 解析 (PyJWT + HS256), 不再调用远程 user_info API
- JWT 验证在 API 入口中间件完成, 禁止业务节点重复解析
- 禁止将原始 JWT token 写入日志或持久化存储
- 数据隔离键: agent_id=agent_name, user_id + session_id 三级分键

与 test_api_middleware.py 区别:
- test_api_middleware.py 侧重 SecurityHeadersMiddleware + JWTAuthMiddleware 主流程
- test_api_security.py 侧重安全合规维度: SELF_HOST 双模式统一降级/数据隔离键注入/公开路径白名单

单元测试不依赖外部服务, 全部用本地 JWT 解析.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import jwt as pyjwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware import JWTAuthMiddleware, SecurityHeadersMiddleware
from src.config.settings import Settings

pytestmark = pytest.mark.unit


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
    """创建测试用 FastAPI 应用 (含 JWTAuthMiddleware + SecurityHeadersMiddleware)."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint() -> dict[str, str]:
        from src.api.middleware import (
            get_request_agent_id,
            get_request_session_id,
            get_request_user_id,
        )

        return {
            "user_id": get_request_user_id(),
            "session_id": get_request_session_id(),
            "agent_id": get_request_agent_id(),
        }

    @app.post("/v1/chat/completions")
    async def chat_endpoint() -> dict[str, str]:
        from src.api.middleware import (
            get_request_agent_id,
            get_request_session_id,
            get_request_user_id,
        )

        return {
            "user_id": get_request_user_id(),
            "session_id": get_request_session_id(),
            "agent_id": get_request_agent_id(),
        }

    app.add_middleware(JWTAuthMiddleware, settings=settings)
    app.add_middleware(SecurityHeadersMiddleware)
    return app


# ========== SELF_HOST 模式切换 (统一降级策略) ==========


def test_self_host_true_no_token_uses_ip_based_user_id() -> None:
    """self_host=True: 无 token 时降级 IP-based UserId (自托管模式).

    无 token 时按客户端 IP 生成确定性 UserId
    (TestClient 默认 client host 为 "testclient",
    generate_user_id_from_ip("testclient") = "ip_846488f1dc5c07b4cebe5c14").
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test")
    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"


def test_self_host_false_no_token_unified_degrades_to_ip() -> None:
    """self_host=False: 无 token → 统一降级 IP-based UserId (不再返回 401).

    本轮改造: 统一降级策略, 无论 self_host 值, Token 不存在/解析失败 → IP-based UserId.
    """
    settings = Settings(
        _env_file=None,
        self_host=False,
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test")
    assert r.status_code == 200, f"统一降级应返回 200, 实际: {r.status_code}"
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"


def test_self_host_false_invalid_token_unified_degrades_to_ip() -> None:
    """self_host=False + 无效 token: 统一降级 IP-based UserId (不再返回 401)."""
    settings = Settings(
        _env_file=None,
        self_host=False,
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get(
        "/test", headers={"Authorization": "Bearer bad-token-self-host-false"}
    )
    assert r.status_code == 200, f"统一降级应返回 200, 实际: {r.status_code}"


def test_self_host_false_empty_user_id_claim_unified_degrades_to_ip() -> None:
    """self_host=False + 空 user_id claim: 统一降级 IP-based UserId (不再返回 401)."""
    settings = Settings(
        _env_file=None,
        self_host=False,
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    token = _make_jwt({"UserId": ""})
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"


def test_self_host_false_expired_token_unified_degrades_to_ip() -> None:
    """self_host=False + 过期 token: 统一降级 IP-based UserId (不再返回 401)."""
    settings = Settings(
        _env_file=None,
        self_host=False,
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    expired_payload = {
        "UserId": "expired-user",
        "exp": int(time.time()) - 3600,
        "iat": int(time.time()) - 7200,
    }
    token = pyjwt.encode(expired_payload, _JWT_SECRET, algorithm="HS256")
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"


def test_self_host_true_invalid_token_falls_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """self_host=True: 无效 token → 降级 IP-based UserId + 告警.

    JWT 本地解析失败时, 按客户端 IP 生成确定性 UserId 并记录告警.
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    app = _make_test_app(settings)
    client = TestClient(app)

    with caplog.at_level(logging.WARNING):
        r = client.get(
            "/test", headers={"Authorization": "Bearer bad-token"}
        )

    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"
    # 应记录解析失败告警
    assert any("JWT 本地解析失败" in rec.message for rec in caplog.records)


# ========== JWT Token 不写入日志 ==========


def test_jwt_token_not_in_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """验证原始 JWT token 不写入日志."""
    test_token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.signature-part"
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    app = _make_test_app(settings)
    client = TestClient(app)

    with caplog.at_level(logging.WARNING):
        r = client.get("/test", headers={"Authorization": f"Bearer {test_token}"})

    assert r.status_code == 200
    # 所有日志记录都不应包含原始 token
    for record in caplog.records:
        assert test_token not in record.message, f"JWT token 泄漏在日志中: {record.message}"
        # 也不应仅含 token 的签名部分 (避免部分泄漏)
        assert "signature-part" not in record.message


def test_jwt_token_not_persisted_in_response() -> None:
    """验证响应中不含原始 JWT token."""
    test_token = _make_jwt({"UserId": "real-user"}) + "-extra-signature"
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": f"Bearer {test_token}"})
    assert r.status_code == 200
    # 响应文本不应含原始 token
    assert test_token not in r.text
    # 响应头也不应含原始 token
    for header_value in r.headers.values():
        assert test_token not in header_value


# ========== 数据隔离键注入 ==========


def test_agent_id_injected_to_request_context() -> None:
    """验证 agent_id 自动注入到请求上下文 (agent_id=agent_name)."""
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="my-custom-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test")
    assert r.status_code == 200
    assert r.json()["agent_id"] == "my-custom-agent"


def test_session_id_injected_from_query_param() -> None:
    """验证 session_id 从查询参数注入到请求上下文 (会话隔离键)."""
    settings = Settings(_env_file=None, self_host=True, jwt_signing_key=_JWT_SECRET, jwt_issuer="", jwt_audience="")
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test?session_id=isolated-session-123")
    assert r.status_code == 200
    assert r.json()["session_id"] == "isolated-session-123"


def test_session_id_injected_from_x_session_id_header() -> None:
    """验证 session_id 从 X-Session-Id 头注入到请求上下文."""
    settings = Settings(_env_file=None, self_host=True, jwt_signing_key=_JWT_SECRET, jwt_issuer="", jwt_audience="")
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"X-Session-Id": "header-session-456"})
    assert r.status_code == 200
    assert r.json()["session_id"] == "header-session-456"


def test_session_id_auto_generated_when_missing() -> None:
    """验证无显式 session_id 时自动生成 UUID (会话隔离键不可为空)."""
    settings = Settings(_env_file=None, self_host=True, jwt_signing_key=_JWT_SECRET, jwt_issuer="", jwt_audience="")
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test")
    assert r.status_code == 200
    session_id = r.json()["session_id"]
    assert len(session_id) > 0
    # UUID 格式: 8-4-4-4-12 (含 4 个连字符)
    assert session_id.count("-") == 4


# ========== 公开路径白名单 ==========


def test_public_path_health_skips_jwt() -> None:
    """验证 /health 公开路径跳过 JWT 校验 (健康检查不应强制鉴权)."""
    settings = Settings(
        _env_file=None,
        self_host=False,  # 即使 self_host=False, /health 也应跳过
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )

    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(JWTAuthMiddleware, settings=settings)
    client = TestClient(app)

    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_public_path_agent_discovery_skips_jwt() -> None:
    """验证 /.well-known/agent-discovery.json 公开路径跳过 JWT 校验.

    Agent Discovery Protocol 公开发现端点, 无需鉴权.
    """
    settings = Settings(
        _env_file=None,
        self_host=False,  # 即使 self_host=False, agent-discovery 也应跳过
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )

    app = FastAPI()

    @app.get("/.well-known/agent-discovery.json")
    async def discovery() -> dict[str, str]:
        return {"name": "test-agent"}

    app.add_middleware(JWTAuthMiddleware, settings=settings)
    client = TestClient(app)

    r = client.get("/.well-known/agent-discovery.json")
    assert r.status_code == 200
    assert r.json()["name"] == "test-agent"


# ========== Authorization 头格式校验 ==========


def test_invalid_authorization_header_format_treated_as_no_token() -> None:
    """验证 Authorization 头非 'Bearer xxx' 格式时按无 token 处理 → IP-based 降级."""
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    app = _make_test_app(settings)
    client = TestClient(app)

    # 非 Bearer 前缀
    r = client.get("/test", headers={"Authorization": "Basic abc123"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"


def test_empty_bearer_token_treated_as_no_token() -> None:
    """验证 'Bearer ' (空 token) 按无 token 处理 → IP-based 降级."""
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": "Bearer "})
    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"


# ========== POST 请求同样走 JWT 中间件 ==========


def test_post_request_jwt_authentication() -> None:
    """验证 POST /v1/chat/completions 同样经过 JWT 中间件 (不只 GET)."""
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.post("/v1/chat/completions")
    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"


# ========== P0: SELF_HOST 双模式成功路径 (本地 JWT 解析) ==========


def test_self_host_false_with_valid_token_returns_real_user_id() -> None:
    """self_host=False + 有效 token: 本地 JWT 解析返回真实 user_id.

    本轮改造: 不再调用远程 API, 直接本地解析 token 的 UserId claim.
    """
    settings = Settings(
        _env_file=None,
        self_host=False,
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    token = _make_jwt({"UserId": "real-cloud-user-001"})
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, f"有效 token 应返回 200, 实际: {r.status_code}"
    assert r.json()["user_id"] == "real-cloud-user-001"


def test_self_host_true_with_valid_token_returns_real_user_id() -> None:
    """self_host=True + 有效 token: 本地 JWT 解析返回真实 user_id (成功路径).

    self_host=True (自托管) token 可选,
    但 token 存在时仍应解析真实 user_id, 不降级到 IP-based UserId.
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    token = _make_jwt({"UserId": "real-self-host-user-002"})
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, f"有效 token 应返回 200, 实际: {r.status_code}"
    # 应返回真实 user_id, 不是 IP-based UserId
    assert r.json()["user_id"] == "real-self-host-user-002"
    assert r.json()["user_id"] != "ip_846488f1dc5c07b4cebe5c14"


def test_invalid_token_degrades_to_ip_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """无效 JWT token → 降级 IP-based UserId + 告警.

    JWT 本地解析失败时, 按客户端 IP 生成确定性 UserId 并记录告警.
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    app = _make_test_app(settings)
    client = TestClient(app)

    with caplog.at_level(logging.WARNING):
        r = client.get(
            "/test", headers={"Authorization": "Bearer tok-when-invalid"}
        )

    assert r.status_code == 200, f"无效 token 应降级返回 200, 实际: {r.status_code}"
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"
    # 应记录解析失败告警
    assert any("JWT 本地解析失败" in rec.message for rec in caplog.records)


# ========== P1: 公开路径白名单扩展 ==========


def test_public_paths_docs_redoc_openapi() -> None:
    """公开路径 /docs /redoc /openapi.json /favicon.ico 不需鉴权.

    文档与 OpenAPI schema 公开访问, JWT 中间件应跳过.
    验证即使 self_host=False, 公开路径也跳过 JWT 校验.
    """
    settings = Settings(
        _env_file=None,
        self_host=False,  # 即使 self_host=False, 公开路径也应跳过
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )

    app = FastAPI()

    @app.get("/docs")
    async def docs() -> dict[str, str]:
        return {"page": "docs"}

    @app.get("/redoc")
    async def redoc() -> dict[str, str]:
        return {"page": "redoc"}

    @app.get("/openapi.json")
    async def openapi_schema() -> dict[str, str]:
        return {"openapi": "3.0.0"}

    @app.get("/favicon.ico")
    async def favicon() -> dict[str, str]:
        return {"icon": "favicon"}

    app.add_middleware(JWTAuthMiddleware, settings=settings)
    client = TestClient(app)

    # 逐一验证公开路径不触发 JWT 校验 (不返回 401, 不调用远程 API)
    for path in ("/docs", "/redoc", "/openapi.json", "/favicon.ico"):
        r = client.get(path)
        assert r.status_code == 200, f"公开路径 {path} 应跳过 JWT 返回 200, 实际: {r.status_code}"


def test_public_path_static_prefix() -> None:
    """公开路径 /static/* 前缀匹配不需鉴权.

    前端测试页面静态资源由 FastAPI StaticFiles 挂载到 /,
    /static/* 前缀路径应跳过 JWT 校验.
    """
    settings = Settings(
        _env_file=None,
        self_host=False,  # 即使 self_host=False, /static/* 也应跳过
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )

    app = FastAPI()

    @app.get("/static/css/main.css")
    async def static_css() -> dict[str, str]:
        return {"file": "main.css"}

    @app.get("/static/js/app.js")
    async def static_js() -> dict[str, str]:
        return {"file": "app.js"}

    @app.get("/static/img/logo.png")
    async def static_img() -> dict[str, str]:
        return {"file": "logo.png"}

    app.add_middleware(JWTAuthMiddleware, settings=settings)
    client = TestClient(app)

    # /static/ 前缀下的路径都应跳过 JWT
    for path in ("/static/css/main.css", "/static/js/app.js", "/static/img/logo.png"):
        r = client.get(path)
        assert r.status_code == 200, (
            f"/static/* 路径 {path} 应跳过 JWT 返回 200, 实际: {r.status_code}"
        )


# ========== P1: session_id 优先级 ==========


def test_session_id_priority_order() -> None:
    """session_id 优先级: query param > X-Session-Id header > 自动生成 UUID.

    thread_id 从请求上下文注入做会话隔离键.
    验证三种来源的优先级: 查询参数最高, 其次请求头, 最后自动生成.
    """
    settings = Settings(_env_file=None, self_host=True, jwt_signing_key=_JWT_SECRET, jwt_issuer="", jwt_audience="")
    app = _make_test_app(settings)
    client = TestClient(app)

    # 1. 仅 query param → 使用 query param
    r = client.get("/test?session_id=from-query")
    assert r.json()["session_id"] == "from-query"

    # 2. 仅 header → 使用 header
    r = client.get("/test", headers={"X-Session-Id": "from-header"})
    assert r.json()["session_id"] == "from-header"

    # 3. query param + header 同时存在 → query param 优先
    r = client.get(
        "/test?session_id=from-query",
        headers={"X-Session-Id": "from-header"},
    )
    assert r.json()["session_id"] == "from-query", (
        "query param session_id 应优先于 X-Session-Id header"
    )

    # 4. 两者都没有 → 自动生成 UUID (8-4-4-4-12 格式)
    r = client.get("/test")
    session_id = r.json()["session_id"]
    assert session_id.count("-") == 4, f"自动生成 session_id 应为 UUID 格式, 实际: {session_id}"


# ========== P1: HSTS 生产强制 HTTPS ==========


def test_security_headers_prod_hsts(monkeypatch: pytest.MonkeyPatch) -> None:
    """env='prod' 时注入 HSTS 头 (生产强制 HTTPS).

    生产强制 HTTPS; 安全响应头中间件不可绕过.
    Strict-Transport-Security 头应含 max-age + includeSubDomains + preload.
    """
    # 显式 mock prod 环境的 settings (避免受 .env 中 ENV 配置影响)
    prod_settings = Settings(env="prod", _env_file=None)
    monkeypatch.setattr("src.api.middleware.get_settings", lambda: prod_settings)

    app = FastAPI()

    @app.get("/test")
    async def test_endpoint() -> dict[str, str]:
        return {"ok": "true"}

    app.add_middleware(SecurityHeadersMiddleware)
    client = TestClient(app)

    r = client.get("/test")
    assert r.status_code == 200
    # HSTS 头应存在且含完整指令
    hsts = r.headers.get("Strict-Transport-Security", "")
    assert "max-age=31536000" in hsts, f"HSTS 缺少 max-age: {hsts}"
    assert "includeSubDomains" in hsts, f"HSTS 缺少 includeSubDomains: {hsts}"
    assert "preload" in hsts, f"HSTS 缺少 preload: {hsts}"
    # 其他安全头也应同时存在 (中间件不可绕过)
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
