"""单元测试: API 安全 (Bearer JWT 处理 + IP-based UserId 降级 + 数据隔离键).

安全约束:
- Bearer JWT Token 可选, 不存在时按 IP 生成确定性 UserId (self_host=True 自托管)
- self_host=False (云托管): 强制校验 JWT, 不存在/失败时返回 401
- JWT 验证在 API 入口中间件完成, 禁止业务节点重复解析
- user_id 获取 API 调用应设超时 (默认 5s), 超时降级并告警
- 禁止将原始 JWT token 写入日志或持久化存储
- 数据隔离键: agent_id=agent_name, user_id + session_id 三级分键

与 test_api_middleware.py 区别:
- test_api_middleware.py 侧重 SecurityHeadersMiddleware + JWTAuthMiddleware 主流程
- test_api_security.py 侧重安全合规维度: SELF_HOST 模式切换/401 拒绝/数据隔离键注入/公开路径白名单

单元测试不依赖外部服务, 全部用 mock.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.middleware import JWTAuthMiddleware, SecurityHeadersMiddleware
from src.config.settings import Settings

pytestmark = pytest.mark.unit


# ========== Fake httpx client ==========


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
    """伪造 httpx.AsyncClient."""

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


# ========== SELF_HOST 模式切换 ==========


def test_self_host_true_no_token_uses_ip_based_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """self_host=True: 无 token 时降级 IP-based UserId (自托管模式).

    default_user_id 环境变量已移除, 无 token 时按客户端
    IP 生成确定性 UserId (TestClient 默认 client host 为 "testclient",
    generate_user_id_from_ip("testclient") = "ip_846488f1dc5c07b4cebe5c14").
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


def test_self_host_false_no_token_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """self_host=False: 无 token 时返回 401 (云托管强制校验)."""
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
    # 401 响应应含错误信息
    body = r.json()
    assert "error" in body, f"401 响应缺少 error 字段: {body}"
    # 不应调用 user_info API (token 不存在)
    assert len(fake.calls) == 0


def test_self_host_false_token_call_fails_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """self_host=False: token 调用失败时返回 401 (不降级)."""
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
    assert r.status_code == 401, f"self_host=False token 失败应返回 401, 实际: {r.status_code}"


def test_self_host_false_token_returns_empty_user_id_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """self_host=False: token 解析返回空 user_id 时返回 401."""
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


def test_self_host_false_timeout_returns_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """self_host=False: 超时返回 401 (不降级, 严格模式)."""
    settings = Settings(
        _env_file=None,
        self_host=False,
        agent_name="test-agent",
        user_info_api_timeout=1,
    )
    fake = _FakeAsyncClient(exc=httpx.TimeoutException("timed out"))
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": "Bearer slow"})
    assert r.status_code == 401


def test_self_host_true_token_call_fails_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """self_host=True: token 调用失败时降级 IP-based UserId + 告警.

    调用失败按无 token 处理并告警, 按 IP 生成确定性 UserId.
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
    assert any("解析失败" in rec.message for rec in caplog.records)


# ========== JWT Token 不写入日志 ==========


def test_jwt_token_not_in_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """验证原始 JWT token 不写入日志."""
    test_token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.signature-part"
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
        assert "signature-part" not in record.message


def test_jwt_token_not_persisted_in_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证响应中不含原始 JWT token."""
    test_token = "eyJhbGciOiJIUzI1NiJ9.payload.signature"
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
    assert test_token not in r.text
    # 响应头也不应含原始 token
    for header_value in r.headers.values():
        assert test_token not in header_value


# ========== 数据隔离键注入 ==========


def test_agent_id_injected_to_request_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 agent_id 自动注入到请求上下文 (agent_id=agent_name)."""
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


def test_session_id_injected_from_query_param(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 session_id 从查询参数注入到请求上下文 (会话隔离键)."""
    settings = Settings(_env_file=None, self_host=True)
    _patch_httpx_client(monkeypatch, _FakeAsyncClient())
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test?session_id=isolated-session-123")
    assert r.status_code == 200
    assert r.json()["session_id"] == "isolated-session-123"


def test_session_id_injected_from_x_session_id_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 session_id 从 X-Session-Id 头注入到请求上下文."""
    settings = Settings(_env_file=None, self_host=True)
    _patch_httpx_client(monkeypatch, _FakeAsyncClient())
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"X-Session-Id": "header-session-456"})
    assert r.status_code == 200
    assert r.json()["session_id"] == "header-session-456"


def test_session_id_auto_generated_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证无显式 session_id 时自动生成 UUID (会话隔离键不可为空)."""
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


# ========== 公开路径白名单 ==========


def test_public_path_health_skips_jwt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 /health 公开路径跳过 JWT 校验 (健康检查不应强制鉴权)."""
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

    Agent Discovery Protocol 公开发现端点, 无需鉴权.
    """
    settings = Settings(
        _env_file=None,
        self_host=False,  # 即使强制模式, agent-discovery 也应跳过
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
    assert r.json()["name"] == "test-agent"
    assert len(fake.calls) == 0


# ========== Authorization 头格式校验 ==========


def test_invalid_authorization_header_format_treated_as_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 Authorization 头非 'Bearer xxx' 格式时按无 token 处理."""
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient()
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    # 非 Bearer 前缀
    r = client.get("/test", headers={"Authorization": "Basic abc123"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"
    # 不应调用 user_info API (无效格式)
    assert len(fake.calls) == 0


def test_empty_bearer_token_treated_as_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 'Bearer ' (空 token) 按无 token 处理."""
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


# ========== POST 请求同样走 JWT 中间件 ==========


def test_post_request_jwt_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 POST /v1/chat/completions 同样经过 JWT 中间件 (不只 GET)."""
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
    )
    _patch_httpx_client(monkeypatch, _FakeAsyncClient())
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.post("/v1/chat/completions")
    assert r.status_code == 200
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"


# ========== P0: SELF_HOST 双模式成功路径 ==========


def test_self_host_false_with_valid_token_returns_real_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """self_host=False + 有效 token: 返回真实 user_id (成功路径).

    self_host=False (云托管) 强制校验 JWT,
    token 有效时必须返回 user_info API 解析的真实 user_id, 不降级.
    """
    settings = Settings(
        _env_file=None,
        self_host=False,
        agent_name="test-agent",
        user_info_api_url="https://fake.example.com/api/user",
    )
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"id": "real-cloud-user-001"}))
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": "Bearer valid-cloud-token"})
    assert r.status_code == 200, f"有效 token 应返回 200, 实际: {r.status_code}"
    assert r.json()["user_id"] == "real-cloud-user-001"
    # 应调用 user_info API 一次
    assert len(fake.calls) == 1
    assert fake.calls[0]["url"] == "https://fake.example.com/api/user"
    assert fake.calls[0]["headers"] == {"Authorization": "Bearer valid-cloud-token"}


def test_self_host_true_with_valid_token_returns_real_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """self_host=True + 有效 token: 返回真实 user_id (成功路径).

    self_host=True (自托管) token 可选,
    但 token 存在时仍应解析真实 user_id, 不降级到 IP-based UserId.
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
        user_info_api_url="https://fake.example.com/api/user",
    )
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"id": "real-self-host-user-002"}))
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    r = client.get("/test", headers={"Authorization": "Bearer valid-self-host-token"})
    assert r.status_code == 200, f"有效 token 应返回 200, 实际: {r.status_code}"
    # 应返回真实 user_id, 不是 IP-based UserId
    assert r.json()["user_id"] == "real-self-host-user-002"
    assert r.json()["user_id"] != "ip_846488f1dc5c07b4cebe5c14"
    assert len(fake.calls) == 1


def test_user_info_api_returns_500_degrades_to_ip(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """user_info API 返回 500/502/503 时降级 IP-based UserId (self_host=True).

    调用失败按无 token 处理并告警 (self_host=True 降级).
    验证 5xx 服务端错误触发降级路径, 不向调用方抛异常.
    """
    settings = Settings(
        _env_file=None,
        self_host=True,
        agent_name="test-agent",
    )
    # 模拟 500/502/503 服务端错误 (raise_for_status 会抛 HTTPStatusError)
    fake = _FakeAsyncClient(response=_FakeResponse(503, {"error": "service unavailable"}))
    _patch_httpx_client(monkeypatch, fake)
    app = _make_test_app(settings)
    client = TestClient(app)

    with caplog.at_level(logging.WARNING):
        r = client.get("/test", headers={"Authorization": "Bearer tok-when-503"})

    assert r.status_code == 200, f"5xx 应降级返回 200, 实际: {r.status_code}"
    assert r.json()["user_id"] == "ip_846488f1dc5c07b4cebe5c14"
    # 应记录解析失败告警
    assert any("解析失败" in rec.message for rec in caplog.records)


# ========== P1: 公开路径白名单扩展 ==========


def test_public_paths_docs_redoc_openapi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """公开路径 /docs /redoc /openapi.json /favicon.ico 不需鉴权.

    文档与 OpenAPI schema 公开访问, JWT 中间件应跳过.
    验证即使 self_host=False (强制模式), 公开路径也跳过 JWT 校验.
    """
    settings = Settings(
        _env_file=None,
        self_host=False,  # 即使强制模式, 公开路径也应跳过
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient()
    _patch_httpx_client(monkeypatch, fake)

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

    # 逐一验证公开路径不触发 JWT 校验 (不返回 401, 不调用 user_info API)
    for path in ("/docs", "/redoc", "/openapi.json", "/favicon.ico"):
        r = client.get(path)
        assert r.status_code == 200, f"公开路径 {path} 应跳过 JWT 返回 200, 实际: {r.status_code}"
    # 全部公开路径都不应调用 user_info API
    assert len(fake.calls) == 0, f"公开路径不应调用 user_info API, 实际调用: {len(fake.calls)}"


def test_public_path_static_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """公开路径 /static/* 前缀匹配不需鉴权.

    前端测试页面静态资源由 FastAPI StaticFiles 挂载到 /,
    /static/* 前缀路径应跳过 JWT 校验.
    """
    settings = Settings(
        _env_file=None,
        self_host=False,  # 即使强制模式, /static/* 也应跳过
        agent_name="test-agent",
    )
    fake = _FakeAsyncClient()
    _patch_httpx_client(monkeypatch, fake)

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
    assert len(fake.calls) == 0


# ========== P1: session_id 优先级 ==========


def test_session_id_priority_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """session_id 优先级: query param > X-Session-Id header > 自动生成 UUID.

    thread_id 从请求上下文注入做会话隔离键.
    验证三种来源的优先级: 查询参数最高, 其次请求头, 最后自动生成.
    """
    settings = Settings(_env_file=None, self_host=True)
    _patch_httpx_client(monkeypatch, _FakeAsyncClient())
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
