"""单元测试: JWT Token 身份解析安全 (本地解析 + IP 降级).

覆盖场景:
- Bearer JWT Token 有效时本地解析返回 user_id (PyJWT + HS256, 不调用远程 API)
- Token 不存在时降级到 IP-based UserId (无论 self_host 值, 统一降级策略)
- Token 无效/过期/错误密钥时降级到 IP-based UserId + 告警
- 未配置 jwt_signing_key 时跳过本地解析, 直接 IP-based 降级
- 禁止将原始 JWT token 写入日志或持久化存储 (PII 安全约束)
- 中间件在 API 入口完成 JWT 验证, 业务节点直接读取 user_id
- 公开路径白名单 (/health, /docs, /.well-known/agent-discovery.json 等)

安全约束:
- JWT 验证与 user_id 获取必须在 API 入口中间件完成
- 禁止将原始 JWT token 写入日志或持久化存储

安全合规红线:
- 密钥仅环境变量注入, 禁止入仓/硬编码/日志
- PII: 禁止将原始 JWT token 写入日志或持久化存储

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

from src.api.middleware import JWTAuthMiddleware
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
    """创建测试用 FastAPI 应用 (仅含 JWTAuthMiddleware)."""
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

    app.add_middleware(JWTAuthMiddleware, settings=settings)
    return app


# ============================================================================
# 场景 1: Bearer JWT Token 有效时本地解析返回 user_id
# ============================================================================


def test_valid_bearer_token_local_verify_returns_user_id() -> None:
    """有效 Bearer JWT Token: 本地 PyJWT 解析返回真实 user_id.

    不再调用远程 user_info API, 直接本地解析 token 的 UserId claim.
    """
    settings = Settings(
        _env_file=None,
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    token = _make_jwt({"UserId": "user-from-jwt-123"})
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, f"有效 token 应返回 200, 实际: {r.status_code}"
    assert r.json()["user_id"] == "user-from-jwt-123"


def test_valid_bearer_token_user_id_field_fallback() -> None:
    """JWT payload 优先 UserId 字段, 其次 user_id 字段 (小写).

    payload.get("UserId") or payload.get("user_id").
    """
    settings = Settings(
        _env_file=None,
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    token = _make_jwt({"user_id": "fallback-user-456"})
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "fallback-user-456"


# ============================================================================
# 场景 2: Token 不存在时降级 (统一降级策略, 不再因 self_host=False 返回 401)
# ============================================================================


def test_no_token_self_host_true_degrades_to_ip_based_user_id() -> None:
    """self_host=True 无 token: 降级 IP-based UserId.

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


def test_no_token_self_host_false_also_degrades_to_ip() -> None:
    """self_host=False 无 token: 统一降级 IP-based UserId (不再返回 401).

    本轮改造统一降级策略: Token 不存在/解析失败 → IP-based UserId (无论 self_host 值).
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


def test_empty_bearer_token_treated_as_no_token() -> None:
    """'Bearer ' (空 token) 按无 token 处理 → IP-based 降级."""
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


def test_non_bearer_auth_header_treated_as_no_token() -> None:
    """Authorization 非 'Bearer xxx' 格式时按无 token 处理 → IP-based 降级."""
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

    r = client.get("/test", headers={"Authorization": "Basic abc123"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"


# ============================================================================
# 场景 3: Token 无效/过期时降级并告警
# ============================================================================


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
            "/test", headers={"Authorization": "Bearer not-a-valid-jwt-token"}
        )

    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"
    # 应记录解析失败告警
    assert any("JWT 本地解析失败" in rec.message for rec in caplog.records), (
        "缺少解析失败告警日志"
    )


def test_token_with_wrong_secret_degrades_to_ip(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """JWT 用错误密钥签名 → 本地解析失败 → 降级 IP-based UserId + 告警."""
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    token = _make_jwt({"UserId": "wrong-secret-user"}, secret="another-secret")
    app = _make_test_app(settings)
    client = TestClient(app)

    with caplog.at_level(logging.WARNING):
        r = client.get("/test", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"
    assert any("JWT 本地解析失败" in rec.message for rec in caplog.records)


def test_expired_token_degrades_to_ip(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """过期 JWT token → 本地解析失败 → 降级 IP-based UserId + 告警."""
    settings = Settings(
        _env_file=None,
        self_host=True,
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

    with caplog.at_level(logging.WARNING):
        r = client.get("/test", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"
    assert any("JWT 本地解析失败" in rec.message for rec in caplog.records)


def test_token_returns_empty_user_id_degrades_to_ip() -> None:
    """JWT payload 中 UserId/user_id 均为空 → 降级 IP-based UserId."""
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    token = _make_jwt({"UserId": "", "user_id": ""})
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"


def test_token_call_fails_self_host_false_also_degrades_to_ip() -> None:
    """self_host=False + 无效 token: 统一降级 IP-based UserId (不再返回 401).

    本轮改造统一降级策略.
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

    r = client.get(
        "/test", headers={"Authorization": "Bearer invalid-token-self-host-false"}
    )
    assert r.status_code == 200, f"统一降级应返回 200, 实际: {r.status_code}"
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"


def test_token_returns_empty_user_id_self_host_false_degrades_to_ip() -> None:
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


# ============================================================================
# 场景 4: 未配置 jwt_signing_key 时跳过本地解析
# ============================================================================


def test_no_signing_key_self_host_true_degrades_to_ip() -> None:
    """self_host=True + 未配置 jwt_signing_key: 跳过本地解析 → IP-based 降级."""
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
        jwt_signing_key="",
        jwt_issuer="",
        jwt_audience="",
    )
    token = _make_jwt({"UserId": "would-be-user"})
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"


def test_no_signing_key_self_host_false_degrades_to_ip() -> None:
    """self_host=False + 未配置 jwt_signing_key: 统一降级 IP-based (不再返回 401).

    本轮改造统一降级策略.
    """
    settings = Settings(
        _env_file=None,
        self_host=False,
        agent_name="test-agent",
        jwt_signing_key="",
        jwt_issuer="",
        jwt_audience="",
    )
    token = _make_jwt({"UserId": "would-be-user"})
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, f"统一降级应返回 200, 实际: {r.status_code}"
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"


# ============================================================================
# 场景 5: 禁止将原始 JWT token 写入日志或持久化存储 (PII 安全约束)
# 安全合规红线
# ============================================================================


def test_jwt_token_not_in_logs_on_decode_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """验证 JWT 解析失败时原始 token 不写入日志 (PII 安全约束).

    禁止将原始 JWT token 写入日志或持久化存储;
    仅保留解析后的 user_id.
    """
    test_token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.signature-part-unique"
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
        assert test_token not in record.message, (
            f"JWT token 泄漏在日志中: {record.message}"
        )
        # 也不应仅含 token 的签名部分 (避免部分泄漏)
        assert "signature-part-unique" not in record.message


def test_jwt_token_not_in_response_body() -> None:
    """验证响应 body 不含原始 JWT token (PII 安全约束).

    API 响应禁止返回密码/密钥原文.
    """
    test_token = _make_jwt({"UserId": "real-user"}) + "-extra-signature-unique"
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
    assert test_token not in r.text, "响应 body 泄漏 JWT token"
    # 响应头也不应含原始 token
    for header_value in r.headers.values():
        assert test_token not in header_value, "响应头泄漏 JWT token"


def test_jwt_token_not_in_degraded_response_body() -> None:
    """验证降级响应 body 不含原始 JWT token (PII 安全约束).

    JWT 解析失败降级到 IP-based UserId, 响应不应回显 token.
    """
    test_token = "eyJhbGciOiJIUzI1NiJ9.bad.payload.signature-unique"
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

    r = client.get("/test", headers={"Authorization": f"Bearer {test_token}"})
    assert r.status_code == 200
    # 降级响应不应含原始 token
    assert test_token not in r.text, f"降级响应泄漏 JWT token: {r.text}"


def test_jwt_token_not_logged_at_any_log_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """验证所有日志级别都不泄漏原始 JWT token (PII 安全约束).

    禁止将原始 JWT token 写入日志或持久化存储.
    """
    test_token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.unique-signature-xyz"
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

    # 捕获所有级别日志
    with caplog.at_level(logging.DEBUG):
        r = client.get("/test", headers={"Authorization": f"Bearer {test_token}"})

    assert r.status_code == 200
    # 所有日志都不应含 token (含 DEBUG 级别)
    for record in caplog.records:
        assert test_token not in record.message, (
            f"日志级别 {record.levelname} 泄漏 JWT token: {record.message}"
        )


# ============================================================================
# 场景 6: JWT 配置属性默认值
# ============================================================================


def test_jwt_signing_key_default_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 jwt_signing_key 默认值为空字符串 (生产环境应通过环境变量注入).

    使用 monkeypatch 清除环境变量, 确保测试不受环境干扰.
    """
    monkeypatch.delenv("JWT_SIGNING_KEY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.jwt_signing_key == "", (
        f"jwt_signing_key 默认应为空, 实际: {settings.jwt_signing_key}"
    )


def test_jwt_algorithm_default_hs256(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 jwt_algorithm 默认值为 HS256.

    使用 monkeypatch 清除环境变量, 确保测试不受环境干扰.
    """
    monkeypatch.delenv("JWT_ALGORITHM", raising=False)
    settings = Settings(_env_file=None)
    assert settings.jwt_algorithm == "HS256", (
        f"jwt_algorithm 默认应为 HS256, 实际: {settings.jwt_algorithm}"
    )


def test_jwt_local_verify_default_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 jwt_local_verify 默认值为 True (启用本地解析).

    使用 monkeypatch 清除环境变量, 确保测试不受环境干扰.
    """
    monkeypatch.delenv("JWT_LOCAL_VERIFY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.jwt_local_verify is True, (
        f"jwt_local_verify 默认应为 True, 实际: {settings.jwt_local_verify}"
    )


def test_jwt_clock_skew_default_5_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 jwt_clock_skew 默认值为 5 秒.

    使用 monkeypatch 清除环境变量, 确保测试不受环境干扰.
    """
    monkeypatch.delenv("JWT_CLOCK_SKEW", raising=False)
    settings = Settings(_env_file=None)
    assert settings.jwt_clock_skew == 5, (
        f"jwt_clock_skew 默认应为 5s, 实际: {settings.jwt_clock_skew}"
    )


# ============================================================================
# 场景 7: 中间件在 API 入口完成 JWT 验证 (不在业务节点重复解析)
# JWT 验证在 API 入口中间件完成
# ============================================================================


def test_jwt_verification_in_middleware_not_in_endpoint() -> None:
    """验证 JWT 验证在中间件完成, 业务节点直接读取 user_id.

    JWT 验证与 user_id 获取应在 API 入口中间件完成,
    不应在业务节点内重复解析.
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    token = _make_jwt({"UserId": "middleware-resolved-user"})
    app = _make_test_app(settings)
    client = TestClient(app)

    # 业务端点直接读取 contextvar 中的 user_id (中间件已注入)
    r = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "middleware-resolved-user"


def test_post_request_also_goes_through_jwt_middleware() -> None:
    """验证 POST 请求同样经过 JWT 中间件 (不只 GET).

    JWT 验证在 API 入口中间件完成, 对所有方法生效.
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
        jwt_signing_key=_JWT_SECRET,
        jwt_issuer="",
        jwt_audience="",
    )
    token = _make_jwt({"UserId": "post-user"})

    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat_endpoint() -> dict[str, str]:
        from src.api.middleware import get_request_user_id

        return {"user_id": get_request_user_id()}

    app.add_middleware(JWTAuthMiddleware, settings=settings)
    client = TestClient(app)

    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["user_id"] == "post-user"


# ============================================================================
# 场景 8: 公开路径白名单
# ============================================================================


def test_public_path_health_skips_jwt_verification() -> None:
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
        self_host=False,
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


# ============================================================================
# 场景 9: 数据隔离键注入
# ============================================================================


def test_agent_id_injected_equals_agent_name() -> None:
    """验证 agent_id 自动注入到请求上下文 (agent_id=agent_name).

    每个 Agent 的数据隔离键为 agent_id = agent_name.
    """
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


def test_session_id_priority_query_over_header() -> None:
    """验证 session_id 优先级: query param > X-Session-Id header > 自动生成.

    thread_id 从请求上下文注入做会话隔离键.
    """
    settings = Settings(_env_file=None, self_host=True, jwt_signing_key=_JWT_SECRET, jwt_issuer="", jwt_audience="")
    app = _make_test_app(settings)
    client = TestClient(app)

    # query param + header 同时存在 → query param 优先
    r = client.get(
        "/test?session_id=from-query",
        headers={"X-Session-Id": "from-header"},
    )
    assert r.json()["session_id"] == "from-query"


def test_session_id_auto_generated_uuid_when_missing() -> None:
    """验证无显式 session_id 时自动生成 UUID (会话隔离键不可为空).

    thread_id 从请求上下文注入, 不应由客户端自造.
    """
    settings = Settings(_env_file=None, self_host=True, jwt_signing_key=_JWT_SECRET, jwt_issuer="", jwt_audience="")
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test")
    assert r.status_code == 200
    session_id = r.json()["session_id"]
    assert len(session_id) > 0
    # UUID 格式: 8-4-4-4-12 (含 4 个连字符)
    assert session_id.count("-") == 4
