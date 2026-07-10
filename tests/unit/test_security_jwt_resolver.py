"""单元测试: JWT Token 身份解析安全 (AGENTS.md 第 8/11 章硬约束).

覆盖场景:
- Bearer JWT Token 有效时调用 GET https://agentinsight.goldebridge.com/api/user 获取 user_id
- Token 不存在时降级 (self_host=True → IP-based UserId; self_host=False → 401)
- Token 调用失败时降级并告警 (self_host=True → IP-based UserId; self_host=False → 401)
- 调用超时 (5s) 降级
- 禁止将原始 JWT token 写入日志或持久化存储 (PII 安全硬约束)
- user_info API 返回 5xx 时降级
- user_info API 返回空 user_id 时降级

AGENTS.md 第 8 章:
- JWT 验证与 user_id 获取必须在 API 入口中间件完成
- user_id 获取 API 调用应设超时 (默认 5s), 超时降级并告警
- 禁止将原始 JWT token 写入日志或持久化存储

AGENTS.md 第 11 章安全合规红线:
- 密钥仅环境变量注入, 禁止入仓/硬编码/日志
- PII: 禁止将原始 JWT token 写入日志或持久化存储

AGENTS.md 第 13 章: 单元测试不依赖外部服务, 全部用 mock.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware import JWTAuthMiddleware
from src.config.settings import Settings

pytestmark = pytest.mark.unit


# ========== Fake httpx client (复用 test_api_security.py 模式) ==========


class _FakeResponse:
    """伪造 httpx.Response."""

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
    """伪造 httpx.AsyncClient, 捕获调用细节用于断言."""

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
        """无需操作."""


def _patch_httpx_client(monkeypatch: pytest.MonkeyPatch, fake: _FakeAsyncClient) -> None:
    """替换 httpx.AsyncClient 为 fake 实例."""
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: fake)


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
# 场景 1: Bearer JWT Token 有效时调用 user_info API 获取 user_id
# ============================================================================


def test_valid_bearer_token_calls_user_info_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """有效 Bearer JWT Token: 调用 user_info API 并返回真实 user_id.

    AGENTS.md 第 8 章: token 存在时调用 GET /api/user 获取 user_id.
    验证: 调用 URL 正确, 携带原 Authorization 头, 返回真实 user_id.
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
        user_info_api_url="https://agentinsight.goldebridge.com/api/user",
    )
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"id": "user-from-jwt-123"}))
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": "Bearer eyJ.valid.jwt.token"})
    assert r.status_code == 200, f"有效 token 应返回 200, 实际: {r.status_code}"
    assert r.json()["user_id"] == "user-from-jwt-123"
    # 必须调用 user_info API
    assert len(fake.calls) == 1, f"应调用 user_info API 1 次, 实际: {len(fake.calls)}"
    assert fake.calls[0]["url"] == "https://agentinsight.goldebridge.com/api/user"
    # 必须携带原 Authorization 头 (AGENTS.md 第 8 章)
    assert fake.calls[0]["headers"] == {"Authorization": "Bearer eyJ.valid.jwt.token"}


def test_valid_bearer_token_user_id_field_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """user_info API 响应优先 id 字段, 其次 user_id 字段.

    AGENTS.md 第 8 章: data.get("id") or data.get("user_id").
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"user_id": "fallback-user-456"}))
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "fallback-user-456"


# ============================================================================
# 场景 2: Token 不存在时降级 (AGENTS.md 第 8 章核心)
# ============================================================================


def test_no_token_self_host_true_degrades_to_ip_based_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """self_host=True 无 token: 降级 IP-based UserId.

    AGENTS.md 第 8 章: self_host=True (自托管) token 可选, 不存在时按 IP 生成确定性 UserId.
    TestClient 默认 client host 为 "testclient",
    generate_user_id_from_ip("testclient") = "ip_846488f1dc5c07b4cebe5c14".
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient()
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test")
    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"
    # 无 token 时不应调用 user_info API
    assert len(fake.calls) == 0


def test_no_token_self_host_false_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """self_host=False 无 token: 返回 401 (云托管强制校验).

    AGENTS.md 第 8 章: self_host=False (云托管) 强制校验 JWT Token.
    """
    settings = Settings(
        _env_file=None,
        self_host=False,
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient()
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test")
    assert r.status_code == 401, f"self_host=False 无 token 应返回 401, 实际: {r.status_code}"
    body = r.json()
    assert "error" in body, f"401 响应应含 error 字段: {body}"
    # 不应调用 user_info API (token 不存在直接拒绝)
    assert len(fake.calls) == 0


def test_empty_bearer_token_treated_as_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'Bearer ' (空 token) 按无 token 处理.

    AGENTS.md 第 8 章: token 提取后为空字符串时视为无 token.
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient()
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": "Bearer "})
    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"
    assert len(fake.calls) == 0


def test_non_bearer_auth_header_treated_as_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authorization 非 'Bearer xxx' 格式时按无 token 处理.

    AGENTS.md 第 8 章: 仅 Bearer 前缀的 token 被识别.
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient()
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": "Basic abc123"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"
    assert len(fake.calls) == 0


# ============================================================================
# 场景 3: Token 调用失败时降级并告警
# ============================================================================


def test_token_call_connect_error_degrades_with_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """self_host=True: token 调用连接失败 → 降级 IP-based UserId + 告警.

    AGENTS.md 第 8 章: 调用失败按无 token 处理并告警, 按 IP 生成确定性 UserId.
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient(exc=httpx.ConnectError("connection refused"))
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    with caplog.at_level(logging.WARNING):
        r = client.get("/test", headers={"Authorization": "Bearer bad-token"})

    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"
    # 应记录解析失败告警
    assert any("解析失败" in rec.message for rec in caplog.records), "缺少解析失败告警日志"


def test_token_call_http_401_degrades_to_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """self_host=True: token 调用返回 HTTP 401 → 降级 IP-based UserId.

    AGENTS.md 第 8 章: 调用失败 (含 HTTP 4xx) 按无 token 处理.
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient(response=_FakeResponse(401, {"error": "unauthorized"}))
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": "Bearer expired"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"


def test_token_call_http_500_degrades_to_ip(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """self_host=True: token 调用返回 HTTP 500/502/503 → 降级 IP-based UserId + 告警.

    AGENTS.md 第 8 章: 调用失败按无 token 处理并告警 (5xx 服务端错误).
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient(response=_FakeResponse(503, {"error": "service unavailable"}))
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    with caplog.at_level(logging.WARNING):
        r = client.get("/test", headers={"Authorization": "Bearer tok-when-503"})

    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"
    assert any("解析失败" in rec.message for rec in caplog.records)


def test_token_call_returns_empty_user_id_degrades_to_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """self_host=True: token 解析响应中 id/user_id 为空 → 降级 IP-based UserId.

    AGENTS.md 第 8 章: user_id 为空时按无 token 处理.
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"other": "value"}))
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"


def test_token_call_fails_self_host_false_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """self_host=False: token 调用失败 → 返回 401 (不降级, 严格模式).

    AGENTS.md 第 8 章: self_host=False (云托管) 强制校验, 失败不降级.
    """
    settings = Settings(
        _env_file=None,
        self_host=False,
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient(exc=httpx.ConnectError("auth service down"))
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": "Bearer bad-token"})
    assert r.status_code == 401


def test_token_returns_empty_user_id_self_host_false_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """self_host=False: token 解析返回空 user_id → 返回 401."""
    settings = Settings(
        _env_file=None,
        self_host=False,
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"other": "value"}))
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 401


# ============================================================================
# 场景 4: 调用超时 (5s) 降级 (AGENTS.md 第 8 章: 超时降级)
# ============================================================================


def test_token_call_timeout_self_host_true_degrades_with_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """self_host=True: 超时 → 降级 IP-based UserId + 告警.

    AGENTS.md 第 8 章: user_id 获取 API 调用应设超时 (默认 5s), 超时降级并告警.
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
        user_info_api_timeout=5,
    )
    fake = _FakeAsyncClient(exc=httpx.TimeoutException("read timeout"))
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    with caplog.at_level(logging.WARNING):
        r = client.get("/test", headers={"Authorization": "Bearer slow-token"})

    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"
    # 应记录超时告警
    assert any("超时" in rec.message for rec in caplog.records), "缺少超时告警日志"


def test_token_call_timeout_self_host_false_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """self_host=False: 超时 → 返回 401 (不降级, 严格模式).

    AGENTS.md 第 8 章: self_host=False 时超时同样返回 401.
    """
    settings = Settings(
        _env_file=None,
        self_host=False,
        agent_name="test-agent",
        user_info_api_timeout=5,
    )
    fake = _FakeAsyncClient(exc=httpx.TimeoutException("timed out"))
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": "Bearer slow"})
    assert r.status_code == 401


def test_timeout_config_value_is_5_seconds_by_default() -> None:
    """验证 user_info_api_timeout 默认值为 5 秒 (AGENTS.md 第 8 章).

    AGENTS.md 第 8 章: user_id 获取 API 调用应设超时 (默认 5s).
    """
    settings = Settings(_env_file=None)
    assert settings.user_info_api_timeout == 5, (
        f"user_info_api_timeout 默认应为 5s, 实际: {settings.user_info_api_timeout}"
    )


# ============================================================================
# 场景 5: 禁止将原始 JWT token 写入日志或持久化存储 (PII 安全硬约束)
# AGENTS.md 第 11 章安全合规红线
# ============================================================================


def test_jwt_token_not_in_logs_on_call_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """验证 token 调用失败时原始 JWT token 不写入日志 (PII 安全硬约束).

    AGENTS.md 第 11 章: 禁止将原始 JWT token 写入日志或持久化存储;
    仅保留解析后的 user_id.
    """
    test_token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.signature-part-unique"
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
    )
    # 让 user_info API 调用失败以触发告警 (验证告警日志不含原始 token)
    fake = _FakeAsyncClient(exc=httpx.ConnectError("auth down"))
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    with caplog.at_level(logging.WARNING):
        r = client.get("/test", headers={"Authorization": f"Bearer {test_token}"})

    assert r.status_code == 200
    # 所有日志记录都不应包含原始 token
    for record in caplog.records:
        assert test_token not in record.message, f"JWT token 泄漏在日志中: {record.message}"
        # 也不应仅含 token 的签名部分 (避免部分泄漏)
        assert "signature-part-unique" not in record.message


def test_jwt_token_not_in_logs_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """验证超时时原始 JWT token 不写入日志 (PII 安全硬约束).

    AGENTS.md 第 11 章: 禁止将原始 JWT token 写入日志.
    """
    test_token = "eyJ.unique.timeout.token.signature"
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient(exc=httpx.TimeoutException("timed out"))
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    with caplog.at_level(logging.WARNING):
        r = client.get("/test", headers={"Authorization": f"Bearer {test_token}"})

    assert r.status_code == 200
    for record in caplog.records:
        assert test_token not in record.message, f"超时日志泄漏 JWT token: {record.message}"


def test_jwt_token_not_in_response_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证响应 body 不含原始 JWT token (PII 安全硬约束).

    AGENTS.md 第 11 章: API 响应禁止返回密码/密钥原文.
    """
    test_token = "eyJhbGciOiJIUzI1NiJ9.payload.signature-unique-123"
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"id": "real-user"}))
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": f"Bearer {test_token}"})
    assert r.status_code == 200
    # 响应文本不应含原始 token
    assert test_token not in r.text, "响应 body 泄漏 JWT token"
    # 响应头也不应含原始 token
    for header_value in r.headers.values():
        assert test_token not in header_value, "响应头泄漏 JWT token"


def test_jwt_token_not_in_401_error_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 401 错误响应不含原始 JWT token (PII 安全硬约束).

    AGENTS.md 第 11 章: API 响应禁止返回密码/密钥原文.
    self_host=False 时 token 失败返回 401, 错误信息不应回显 token.
    """
    test_token = "eyJhbGciOiJIUzI1NiJ9.bad.payload.signature"
    settings = Settings(
        _env_file=None,
        self_host=False,
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient(exc=httpx.ConnectError("auth down"))
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": f"Bearer {test_token}"})
    assert r.status_code == 401
    # 错误响应不应含原始 token
    assert test_token not in r.text, f"401 错误响应泄漏 JWT token: {r.text}"


def test_jwt_token_not_logged_at_any_log_level(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """验证所有日志级别都不泄漏原始 JWT token (PII 安全硬约束).

    AGENTS.md 第 11 章: 禁止将原始 JWT token 写入日志或持久化存储.
    """
    test_token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.unique-signature-xyz"
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient(response=_FakeResponse(500, {"error": "internal"}))
    _patch_httpx_client(monkeypatch, fake)
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
# 场景 6: user_info API URL 配置正确 (AGENTS.md 第 8 章默认值)
# ============================================================================


def test_user_info_api_url_default_value() -> None:
    """验证 user_info_api_url 默认值为 AgentInsight 用户接口.

    AGENTS.md 第 8 章: GET https://agentinsight.goldebridge.com/api/user.
    """
    settings = Settings(_env_file=None)
    assert settings.user_info_api_url == "https://agentinsight.goldebridge.com/api/user", (
        f"user_info_api_url 默认值不正确: {settings.user_info_api_url}"
    )


def test_user_info_api_called_with_correct_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 token 存在时调用正确的 user_info API URL.

    AGENTS.md 第 8 章: 同步调用 GET /api/user 获取 user_id.
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
        # 使用默认 URL, 不覆盖
    )
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"id": "real-user"}))
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": "Bearer valid-token"})
    assert r.status_code == 200
    # 必须调用默认 URL
    assert len(fake.calls) == 1
    assert fake.calls[0]["url"] == "https://agentinsight.goldebridge.com/api/user"


# ============================================================================
# 场景 7: 中间件在 API 入口完成 JWT 验证 (不在业务节点重复解析)
# AGENTS.md 第 8 章: JWT 验证在 API 入口中间件完成
# ============================================================================


def test_jwt_verification_in_middleware_not_in_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 JWT 验证在中间件完成, 业务节点直接读取 user_id.

    AGENTS.md 第 8 章: JWT 验证与 user_id 获取应在 API 入口中间件完成,
    不推荐在业务节点内重复解析.
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"id": "middleware-resolved-user"}))
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    # 业务端点直接读取 contextvar 中的 user_id (中间件已注入)
    r = client.get("/test", headers={"Authorization": "Bearer any-token"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "middleware-resolved-user"
    # user_info API 只应被调用 1 次 (中间件), 不应在端点内重复调用
    assert len(fake.calls) == 1


def test_post_request_also_goes_through_jwt_middleware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 POST 请求同样经过 JWT 中间件 (不只 GET).

    AGENTS.md 第 8 章: JWT 验证在 API 入口中间件完成, 对所有方法生效.
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"id": "post-user"}))
    _patch_httpx_client(monkeypatch, fake)

    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat_endpoint() -> dict[str, str]:
        from src.api.middleware import get_request_user_id

        return {"user_id": get_request_user_id()}

    app.add_middleware(JWTAuthMiddleware, settings=settings)
    client = TestClient(app)

    r = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer post-token"},
    )
    assert r.status_code == 200
    assert r.json()["user_id"] == "post-user"
    assert len(fake.calls) == 1


# ============================================================================
# 场景 8: 公开路径白名单 (AGENTS.md 第 8/14 章)
# ============================================================================


def test_public_path_health_skips_jwt_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 /health 公开路径跳过 JWT 校验 (健康检查不应强制鉴权).

    AGENTS.md 第 8/14 章: /health 为公开路径, JWT 中间件应跳过.
    """
    settings = Settings(
        _env_file=None,
        self_host=False,  # 即使强制模式, /health 也应跳过
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient()
    _patch_httpx_client(monkeypatch, fake)

    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(JWTAuthMiddleware, settings=settings)
    client = TestClient(app)

    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    # /health 不应触发 user_info API 调用
    assert len(fake.calls) == 0


def test_public_path_agent_discovery_skips_jwt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 /.well-known/agent-discovery.json 公开路径跳过 JWT 校验.

    AGENTS.md 第 14 章: Agent Discovery Protocol 公开发现端点, 无需鉴权.
    """
    settings = Settings(
        _env_file=None,
        self_host=False,
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient()
    _patch_httpx_client(monkeypatch, fake)

    app = FastAPI()

    @app.get("/.well-known/agent-discovery.json")
    async def discovery() -> dict[str, str]:
        return {"name": "test-agent"}

    app.add_middleware(JWTAuthMiddleware, settings=settings)
    client = TestClient(app)

    r = client.get("/.well-known/agent-discovery.json")
    assert r.status_code == 200
    assert len(fake.calls) == 0


# ============================================================================
# 场景 9: 数据隔离键注入 (AGENTS.md 第 7/8 章)
# ============================================================================


def test_agent_id_injected_equals_agent_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 agent_id 自动注入到请求上下文 (agent_id=agent_name).

    AGENTS.md 第 7 章: 每个 Agent 的数据隔离键为 agent_id = agent_name.
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="my-custom-agent",
    )
    _patch_httpx_client(monkeypatch, _FakeAsyncClient())
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test")
    assert r.status_code == 200
    assert r.json()["agent_id"] == "my-custom-agent"


def test_session_id_priority_query_over_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 session_id 优先级: query param > X-Session-Id header > 自动生成.

    AGENTS.md 第 6/8 章: thread_id 从请求上下文注入做会话隔离键.
    """
    settings = Settings(_env_file=None, self_host=True)
    _patch_httpx_client(monkeypatch, _FakeAsyncClient())
    app = _make_test_app(settings)
    client = TestClient(app)

    # query param + header 同时存在 → query param 优先
    r = client.get(
        "/test?session_id=from-query",
        headers={"X-Session-Id": "from-header"},
    )
    assert r.json()["session_id"] == "from-query"


def test_session_id_auto_generated_uuid_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证无显式 session_id 时自动生成 UUID (会话隔离键不可为空).

    AGENTS.md 第 6 章: thread_id 从请求上下文注入, 不推荐客户端自造.
    """
    settings = Settings(_env_file=None, self_host=True)
    _patch_httpx_client(monkeypatch, _FakeAsyncClient())
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test")
    assert r.status_code == 200
    session_id = r.json()["session_id"]
    assert len(session_id) > 0
    # UUID 格式: 8-4-4-4-12 (含 4 个连字符)
    assert session_id.count("-") == 4
