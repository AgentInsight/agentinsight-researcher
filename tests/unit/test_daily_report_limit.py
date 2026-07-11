"""单元测试: 每日报告限额检查 (IP-based 用户限流).

验证 src/api/routes.py 第 559-625 行限额检查逻辑 + src/api/ip_user_resolver.py:
1. IP-based 用户超限 - 非流式 → 429 + 错误信息含 "已达上限" 和 "3/3"
2. IP-based 用户未超限 - 非流式 → 不返回 429 (走正常研究流水线)
3. IP-based 用户超限 - 流式 → SSE 含 "已达上限", finish_reason="stop"
4. JWT Token 用户不受限额 (user_id 不以 "ip_" 开头)
5. self_host=False 不走限额 (无 token → 401)
6. ip_daily_report_limit=0 不限制

单元测试不依赖外部服务 (Redis/Postgres/LLM 全部 mock).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import app
from src.api.middleware import JWTAuthMiddleware, SecurityHeadersMiddleware
from src.api.routes import router
from src.config.settings import Settings
from src.skills.researcher.query_classifier import QueryIntent

pytestmark = pytest.mark.unit


# ========== 辅助函数 ==========


def _make_settings(**kwargs: Any) -> Settings:
    """构造自定义 Settings (默认 self_host=True, ip_daily_report_limit=3)."""
    defaults: dict[str, Any] = {
        "_env_file": None,
        "self_host": True,
        "ip_daily_report_limit": 3,
        "agent_name": "agentinsight-researcher",
    }
    defaults.update(kwargs)
    return Settings(**defaults)


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock 研究流水线依赖 (意图分类 + 图构建 + 会话报告检查).

    避免 IP-based 用户未超限场景下触发真实 LLM/图构建/Postgres.
    """
    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(return_value=QueryIntent.RESEARCH)
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(
        return_value={
            "report_md": "测试报告内容",
            "sources": [],
            "curated_sources": [],
            "total_tokens": 10,
            "token_logs": [{"prompt_tokens": 5, "completion_tokens": 5}],
        }
    )
    monkeypatch.setattr("src.api.routes._has_report", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "src.api.routes.get_query_intent_classifier",
        MagicMock(return_value=mock_classifier),
    )
    monkeypatch.setattr("src.api.routes._get_graph", AsyncMock(return_value=mock_graph))


def _patch_limit_check(
    monkeypatch: pytest.MonkeyPatch,
    allowed: bool,
    count: int,
) -> AsyncMock:
    """Mock check_daily_report_limit 返回指定结果."""
    mock = AsyncMock(return_value=(allowed, count))
    monkeypatch.setattr("src.api.ip_user_resolver.check_daily_report_limit", mock)
    return mock


def _patch_increment(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Mock increment_daily_report_count 避免 Redis 连接."""
    mock = AsyncMock(return_value=1)
    monkeypatch.setattr("src.api.ip_user_resolver.increment_daily_report_count", mock)
    return mock


_REQUEST_BODY: dict[str, Any] = {
    "model": "agentinsight-researcher",
    "messages": [{"role": "user", "content": "研究中国新能源汽车行业"}],
    "stream": False,
}


# ========== 场景 1: IP-based 用户超限 - 非流式 ==========


def test_ip_user_exceeded_non_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """IP-based 用户超限 → 429 + 错误信息含 "已达上限" 和 "3/3"."""
    settings = _make_settings(self_host=True, ip_daily_report_limit=3)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    _patch_limit_check(monkeypatch, allowed=False, count=3)

    client = TestClient(app)
    response = client.post("/v1/chat/completions", json=_REQUEST_BODY)

    assert response.status_code == 429
    data = response.json()
    msg = data["error"]["message"]
    assert "已达上限" in msg
    assert "3/3" in msg
    assert data["error"]["type"] == "rate_limit_exceeded"
    assert data["error"]["code"] == "daily_report_limit"


# ========== 场景 2: IP-based 用户未超限 - 非流式 ==========


def test_ip_user_not_exceeded_non_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """IP-based 用户未超限 → 不返回 429 (走正常研究流水线)."""
    settings = _make_settings(self_host=True, ip_daily_report_limit=3)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    _patch_limit_check(monkeypatch, allowed=True, count=1)
    _patch_increment(monkeypatch)

    client = TestClient(app)
    response = client.post("/v1/chat/completions", json=_REQUEST_BODY)

    assert response.status_code != 429
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "chat.completion"


# ========== 场景 3: IP-based 用户超限 - 流式 ==========


def test_ip_user_exceeded_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """IP-based 用户超限 - 流式 → SSE 含 "已达上限", finish_reason="stop"."""
    settings = _make_settings(self_host=True, ip_daily_report_limit=3)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    _patch_limit_check(monkeypatch, allowed=False, count=3)

    body = {**_REQUEST_BODY, "stream": True}
    client = TestClient(app)
    with client.stream("POST", "/v1/chat/completions", json=body) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        chunks: list[dict[str, Any]] = []
        for line in response.iter_lines():
            if line.startswith("data: "):
                payload = line[6:]
                if payload == "[DONE]":
                    break
                chunks.append(json.loads(payload))

    assert len(chunks) >= 2
    # 首块含限额提示内容
    first_content = chunks[0]["choices"][0]["delta"].get("content", "")
    assert "已达上限" in first_content
    assert "3/3" in first_content
    # 末块 finish_reason="stop"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


# ========== 场景 4: JWT Token 用户不受限额 ==========


def test_jwt_user_not_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """JWT Token 用户 (user_id 不以 "ip_" 开头) 不走限额检查.

    携带有效 Bearer Token 时, user_id 由 user_info API 解析,
    不以 "ip_" 前缀标识, 限额检查条件 user_id.startswith("ip_") 不满足.
    """
    settings = _make_settings(self_host=True, ip_daily_report_limit=3)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    mock_check = _patch_limit_check(monkeypatch, allowed=False, count=3)
    _patch_increment(monkeypatch)

    # Mock 中间件 _resolve_user_id 返回真实 user_id (非 ip_ 前缀)
    async def _fake_resolve(
        self: JWTAuthMiddleware, token: str, client_ip: str = ""
    ) -> tuple[str | None, str | None]:
        return "real-user-123", None

    monkeypatch.setattr(JWTAuthMiddleware, "_resolve_user_id", _fake_resolve)

    client = TestClient(app)
    response = client.post(
        "/v1/chat/completions",
        json=_REQUEST_BODY,
        headers={"Authorization": "Bearer fake-token"},
    )

    # 不应返回 429 (user_id 不以 "ip_" 开头, 限额检查条件不满足)
    assert response.status_code != 429
    # check_daily_report_limit 不应被调用
    mock_check.assert_not_called()


# ========== 场景 5: self_host=False 不走限额 ==========


def test_self_host_false_no_token_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """self_host=False 时无 token → 401 (中间件拦截, 不走限额检查).

    self_host=False (云托管) 强制校验 JWT Token,
    不存在时返回 401, 请求不会到达限额检查逻辑.
    """
    settings = _make_settings(self_host=False, ip_daily_report_limit=3)

    # Mock httpx.AsyncClient 避免创建真实 HTTP 客户端 (中间件 __init__ 内调用)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: MagicMock())

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.add_middleware(JWTAuthMiddleware, settings=settings)
    test_app.add_middleware(SecurityHeadersMiddleware)

    client = TestClient(test_app)
    response = client.post("/v1/chat/completions", json=_REQUEST_BODY)

    assert response.status_code == 401
    data = response.json()
    assert "error" in data


# ========== 场景 6: ip_daily_report_limit=0 不限制 ==========


def test_limit_zero_no_restriction(monkeypatch: pytest.MonkeyPatch) -> None:
    """ip_daily_report_limit=0 → 不检查限额 (条件 ip_daily_report_limit > 0 不满足)."""
    settings = _make_settings(self_host=True, ip_daily_report_limit=0)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    mock_check = _patch_limit_check(monkeypatch, allowed=False, count=0)
    _patch_increment(monkeypatch)

    client = TestClient(app)
    response = client.post("/v1/chat/completions", json=_REQUEST_BODY)

    # 不应返回 429 (limit=0 表示不限制)
    assert response.status_code != 429
    assert response.status_code == 200
    # check_daily_report_limit 不应被调用 (条件 ip_daily_report_limit > 0 不满足)
    mock_check.assert_not_called()
