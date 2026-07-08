"""单元测试: AgentInsightClient (点数校验/扣除 API 客户端).

验证 src/api/agentinsight_client.py:
- validate_agent_usage: 成功(超限/未超限) / HTTP 错误 / 超时 / 连接错误 / 响应缺 Data 字段
- deduct_agent_usage: 成功 / 失败
- Authorization 头透传 / 超时配置 / fail_open 降级策略
- close() 关闭 httpx 客户端

AGENTS.md 第 8 章: JWT 验证与 user_id 获取在 API 入口中间件完成, 本客户端负责点数校验/扣除.
AGENTS.md 第 11 章: 密钥仅环境变量注入, 禁止硬编码/日志.
AGENTS.md 第 13 章: 单元测试不依赖外部服务 (httpx 全部 mock).

注: 本客户端不含 get_user_id() 方法 (身份解析在 src/api/middleware.py 的 JWTAuthMiddleware).
任务描述中的 "token 存在/为空/超时/连接错误降级" 场景在此适配为 validate/deduct 的对应路径.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.api.agentinsight_client import AgentInsightClient
from src.config.settings import Settings

pytestmark = pytest.mark.unit


# ========== Fake httpx 基础设施 ==========


class _FakeResponse:
    """伪造 httpx.Response, 支持 raise_for_status 与 json."""

    def __init__(
        self,
        status_code: int = 200,
        json_data: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "http://test"),
                response=self,  # type: ignore[arg-type]
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
        self.aclose_called = False

    async def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _FakeResponse:
        self.calls.append({"url": url, "params": params, "headers": headers})
        if self._exc is not None:
            raise self._exc
        if self._response is None:
            return _FakeResponse(200, {"Data": [False]})
        return self._response

    async def aclose(self) -> None:
        self.aclose_called = True


def _make_client(
    monkeypatch: pytest.MonkeyPatch,
    fake: _FakeAsyncClient,
    *,
    fail_open: bool = True,
    timeout: int = 5,
) -> AgentInsightClient:
    """构造 AgentInsightClient (httpx.AsyncClient 替换为 fake)."""
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: fake)
    settings = Settings(
        _env_file=None,
        agent_privilege_fail_open=fail_open,
        agent_privilege_api_timeout=timeout,
        agent_privilege_api_base_url="https://api.example.com",
        agent_privilege_validate_path="/validate",
        agent_privilege_deduct_path="/deduct",
    )
    return AgentInsightClient(settings=settings)


# ========== validate_agent_usage: 成功路径 ==========


@pytest.mark.asyncio
async def test_validate_agent_usage_success_not_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API 成功 + Data[0]=False → 返回 (False, None) 未超限."""
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"Data": [False]}))
    client = _make_client(monkeypatch, fake)

    exceeded, error = await client.validate_agent_usage("my-jwt-token")

    assert exceeded is False
    assert error is None
    assert len(fake.calls) == 1
    # 应传 type=2 (Research)
    assert fake.calls[0]["params"] == {"type": 2}


@pytest.mark.asyncio
async def test_validate_agent_usage_success_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API 成功 + Data[0]=True → 返回 (True, None) 已超限."""
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"Data": [True]}))
    client = _make_client(monkeypatch, fake)

    exceeded, error = await client.validate_agent_usage("my-jwt-token")

    assert exceeded is True
    assert error is None


@pytest.mark.asyncio
async def test_validate_agent_usage_response_no_data_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """响应无 Data 字段 → 视为未超限 (exceeded=False)."""
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"Other": "value"}))
    client = _make_client(monkeypatch, fake)

    exceeded, error = await client.validate_agent_usage("tok")

    assert exceeded is False
    assert error is None


@pytest.mark.asyncio
async def test_validate_agent_usage_response_empty_data_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """响应 Data 为空列表 → 视为未超限 (api_data 为空时 exceeded=False)."""
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"Data": []}))
    client = _make_client(monkeypatch, fake)

    exceeded, error = await client.validate_agent_usage("tok")

    assert exceeded is False
    assert error is None


# ========== validate_agent_usage: 失败降级 ==========


@pytest.mark.asyncio
async def test_validate_agent_usage_http_error_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 4xx/5xx + fail_open=True → 放行 (False, None)."""
    fake = _FakeAsyncClient(response=_FakeResponse(500, {"error": "server"}))
    client = _make_client(monkeypatch, fake, fail_open=True)

    exceeded, error = await client.validate_agent_usage("tok")

    assert exceeded is False
    assert error is None


@pytest.mark.asyncio
async def test_validate_agent_usage_http_error_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 4xx/5xx + fail_open=False → 拒绝 (True, error)."""
    fake = _FakeAsyncClient(response=_FakeResponse(401, {"error": "unauthorized"}))
    client = _make_client(monkeypatch, fake, fail_open=False)

    exceeded, error = await client.validate_agent_usage("tok")

    assert exceeded is True
    assert error is not None
    assert "HTTPStatusError" in error or "校验失败" in error


@pytest.mark.asyncio
async def test_validate_agent_usage_timeout_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API 超时 + fail_open=True → 放行 (降级 DEFAULT_USER 路径同类)."""
    fake = _FakeAsyncClient(exc=httpx.TimeoutException("timed out"))
    client = _make_client(monkeypatch, fake, fail_open=True)

    exceeded, error = await client.validate_agent_usage("slow-token")

    assert exceeded is False
    assert error is None


@pytest.mark.asyncio
async def test_validate_agent_usage_connection_error_degrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连接错误 + fail_open=True → 降级放行 (不抛异常)."""
    fake = _FakeAsyncClient(exc=httpx.ConnectError("connection refused"))
    client = _make_client(monkeypatch, fake, fail_open=True)

    exceeded, error = await client.validate_agent_usage("tok")

    assert exceeded is False
    assert error is None


# ========== Authorization 头透传与超时配置 ==========


@pytest.mark.asyncio
async def test_validate_agent_usage_passes_authorization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """validate_agent_usage 应透传 Authorization: Bearer <token> 头."""
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"Data": [False]}))
    client = _make_client(monkeypatch, fake)

    await client.validate_agent_usage("my-secret-jwt")

    assert fake.calls[0]["headers"] == {"Authorization": "Bearer my-secret-jwt"}


@pytest.mark.asyncio
async def test_validate_agent_usage_uses_timeout_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """httpx.AsyncClient 应使用 settings.agent_privilege_api_timeout (默认 5s)."""
    captured: dict[str, Any] = {}

    def _capture(*args: Any, **kwargs: Any) -> _FakeAsyncClient:
        captured["timeout"] = kwargs.get("timeout")
        fake = _FakeAsyncClient(response=_FakeResponse(200, {"Data": [False]}))
        return fake

    monkeypatch.setattr(httpx, "AsyncClient", _capture)
    settings = Settings(
        _env_file=None,
        agent_privilege_api_timeout=5,
    )
    AgentInsightClient(settings=settings)

    assert captured["timeout"] == 5


# ========== deduct_agent_usage ==========


@pytest.mark.asyncio
async def test_deduct_agent_usage_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """API 成功 → 返回 True."""
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"Data": [True]}))
    client = _make_client(monkeypatch, fake)

    success = await client.deduct_agent_usage("my-jwt-token")

    assert success is True
    assert len(fake.calls) == 1
    # deduct 也应传 type=2
    assert fake.calls[0]["params"] == {"type": 2}


@pytest.mark.asyncio
async def test_deduct_agent_usage_failure_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API 失败 (HTTP 错误) → 返回 False (不抛异常)."""
    fake = _FakeAsyncClient(response=_FakeResponse(500, {"error": "server"}))
    client = _make_client(monkeypatch, fake)

    success = await client.deduct_agent_usage("tok")

    assert success is False


@pytest.mark.asyncio
async def test_deduct_agent_usage_connection_error_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连接错误 → 返回 False (降级不阻断主流程)."""
    fake = _FakeAsyncClient(exc=httpx.ConnectError("refused"))
    client = _make_client(monkeypatch, fake)

    success = await client.deduct_agent_usage("tok")

    assert success is False


@pytest.mark.asyncio
async def test_deduct_agent_usage_passes_authorization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """deduct_agent_usage 应透传 Authorization: Bearer <token> 头."""
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"Data": [True]}))
    client = _make_client(monkeypatch, fake)

    await client.deduct_agent_usage("deduct-token")

    assert fake.calls[0]["headers"] == {"Authorization": "Bearer deduct-token"}


# ========== close() ==========


@pytest.mark.asyncio
async def test_close_calls_aclose(monkeypatch: pytest.MonkeyPatch) -> None:
    """close() 应调用 httpx.AsyncClient.aclose()."""
    fake = _FakeAsyncClient(response=_FakeResponse(200, {"Data": [False]}))
    client = _make_client(monkeypatch, fake)

    await client.close()

    assert fake.aclose_called is True
