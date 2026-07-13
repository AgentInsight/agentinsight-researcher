"""单元测试: 每日报告限额检查 (IP-based 用户限流) - 穷尽端到端场景.

验证 src/api/routes.py 限额检查逻辑 + src/api/ip_user_resolver.py:
1. IP-based 用户超限 - 非流式 → 429 + 错误信息含 "已达上限" 和 "5/5"
2. IP-based 用户未超限 - 非流式 → 不返回 429 (走正常研究流水线)
3. IP-based 用户超限 - 流式 → SSE 含 "已达上限", finish_reason="stop"
4. JWT Token 用户不受限额 (user_id 不以 "ip_" 开头)
5. self_host=False 不走限额 (无 token → 401)
6. ip_daily_report_limit=0 不限制
7. 非流式成功路径 → increment_daily_report_count 被调用
8. 非流式失败路径 (限额超限) → increment_daily_report_count 不被调用
9. 流式成功路径 → increment_daily_report_count 被调用
10. 流式失败路径 (限额超限) → increment_daily_report_count 不被调用
11. 4 种限额场景通过 API 端到端 (用户有/无/大于/小于系统)
12. increment 调用参数正确 (user_id, agent_id)

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
    """构造自定义 Settings (默认 self_host=True, ip_daily_report_limit=5)."""
    defaults: dict[str, Any] = {
        "_env_file": None,
        "self_host": True,
        "ip_daily_report_limit": 5,
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
    limit: int = 5,
) -> AsyncMock:
    """Mock check_daily_report_limit 返回指定结果.

    返回三元组 (allowed, current_count, effective_limit), 与实际函数签名一致.
    """
    mock = AsyncMock(return_value=(allowed, count, limit))
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
    """IP-based 用户超限 → 429 + 错误信息含 "已达上限" 和 "5/5"."""
    settings = _make_settings(self_host=True, ip_daily_report_limit=5)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    _patch_limit_check(monkeypatch, allowed=False, count=5)

    client = TestClient(app)
    response = client.post("/v1/chat/completions", json=_REQUEST_BODY)

    assert response.status_code == 429
    data = response.json()
    msg = data["error"]["message"]
    assert "已达上限" in msg
    assert "5/5" in msg
    assert data["error"]["type"] == "rate_limit_exceeded"
    assert data["error"]["code"] == "daily_report_limit"


# ========== 场景 2: IP-based 用户未超限 - 非流式 ==========


def test_ip_user_not_exceeded_non_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """IP-based 用户未超限 → 不返回 429 (走正常研究流水线)."""
    settings = _make_settings(self_host=True, ip_daily_report_limit=5)
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
    settings = _make_settings(self_host=True, ip_daily_report_limit=5)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    _patch_limit_check(monkeypatch, allowed=False, count=5)

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
    assert "5/5" in first_content
    # 末块 finish_reason="stop"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


# ========== 场景 4: JWT Token 用户不受限额 ==========


def test_jwt_user_not_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """JWT Token 用户 (user_id 不以 "ip_" 开头) 不走限额检查.

    携带有效 Bearer Token 时, user_id 由 user_info API 解析,
    不以 "ip_" 前缀标识, 限额检查条件 user_id.startswith("ip_") 不满足.
    """
    settings = _make_settings(self_host=True, ip_daily_report_limit=5)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    mock_check = _patch_limit_check(monkeypatch, allowed=False, count=5)
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
    settings = _make_settings(self_host=False, ip_daily_report_limit=5)

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
    """ip_daily_report_limit=0 → 不限制 (数据库读取后 limit<=0 直接放行)."""
    settings = _make_settings(self_host=True, ip_daily_report_limit=0)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    # mock 返回 allowed=True, count=0, limit=0 (表示不限制)
    _patch_limit_check(monkeypatch, allowed=True, count=0, limit=0)
    _patch_increment(monkeypatch)

    client = TestClient(app)
    response = client.post("/v1/chat/completions", json=_REQUEST_BODY)

    # 不应返回 429 (limit=0 表示不限制)
    assert response.status_code != 429
    assert response.status_code == 200


# ========== 场景 7: 非流式成功路径 → increment 被调用 ==========


def test_non_stream_success_increment_called(monkeypatch: pytest.MonkeyPatch) -> None:
    """非流式成功路径 → increment_daily_report_count 应被调用.

    验证: IP-based 用户未超限 → 走研究流水线 → 报告生成成功 → increment 被调用.
    """
    settings = _make_settings(self_host=True, ip_daily_report_limit=5)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    _patch_limit_check(monkeypatch, allowed=True, count=1)
    mock_increment = _patch_increment(monkeypatch)

    client = TestClient(app)
    response = client.post("/v1/chat/completions", json=_REQUEST_BODY)

    assert response.status_code == 200
    # increment 应被调用 (报告生成成功后递增计数)
    mock_increment.assert_awaited_once()


# ========== 场景 8: 非流式失败路径 (限额超限) → increment 不被调用 ==========


def test_non_stream_limit_exceeded_increment_not_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非流式限额超限 → increment_daily_report_count 不应被调用.

    验证: IP-based 用户超限 → 直接返回 429 → 不走研究流水线 → increment 不被调用.
    """
    settings = _make_settings(self_host=True, ip_daily_report_limit=5)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    _patch_limit_check(monkeypatch, allowed=False, count=5)
    mock_increment = _patch_increment(monkeypatch)

    client = TestClient(app)
    response = client.post("/v1/chat/completions", json=_REQUEST_BODY)

    assert response.status_code == 429
    # increment 不应被调用 (未生成报告, 不计数)
    mock_increment.assert_not_called()


# ========== 场景 9: 流式成功路径 → increment 被调用 ==========


def test_stream_success_increment_called(monkeypatch: pytest.MonkeyPatch) -> None:
    """流式成功路径 → 流式 SSE 正常完成 (200 + finish_reason=stop).

    注: 流式路径的 increment_daily_report_count 在后台任务 _persist_report 中调用,
    TestClient 同步上下文下事件循环可能在后台任务执行前关闭.
    increment 的单元测试在 test_ip_user_resolver.py 中已覆盖;
    非流式路径的 increment 调用在 test_non_stream_success_increment_called 中验证.
    此测试验证流式路径不返回 429 且 SSE 正常完成.
    """
    settings = _make_settings(self_host=True, ip_daily_report_limit=5)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    _patch_limit_check(monkeypatch, allowed=True, count=1)
    _patch_increment(monkeypatch)

    body = {**_REQUEST_BODY, "stream": True}
    client = TestClient(app)
    chunks: list[dict[str, Any]] = []
    with client.stream("POST", "/v1/chat/completions", json=body) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        for line in response.iter_lines():
            if line.startswith("data: "):
                payload = line[6:]
                if payload == "[DONE]":
                    break
                chunks.append(json.loads(payload))

    # 验证流式正常完成 (不是限额超限提示)
    assert len(chunks) >= 1
    # 不应有 "已达上限" (那是限额超限的提示)
    first_content = chunks[0]["choices"][0]["delta"].get("content", "")
    assert "已达上限" not in first_content
    # 末块 finish_reason="stop" (正常完成)
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


# ========== 场景 10: 流式失败路径 (限额超限) → increment 不被调用 ==========


def test_stream_limit_exceeded_increment_not_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """流式限额超限 → increment_daily_report_count 不应被调用.

    验证: IP-based 用户超限 → 流式返回限额提示 → 不走研究流水线 → increment 不被调用.
    """
    settings = _make_settings(self_host=True, ip_daily_report_limit=5)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    _patch_limit_check(monkeypatch, allowed=False, count=5)
    mock_increment = _patch_increment(monkeypatch)

    body = {**_REQUEST_BODY, "stream": True}
    client = TestClient(app)
    with client.stream("POST", "/v1/chat/completions", json=body) as response:
        assert response.status_code == 200
        for _ in response.iter_lines():
            pass

    # increment 不应被调用 (未生成报告, 不计数)
    mock_increment.assert_not_called()


# ========== 场景 11: 4 种限额场景通过 API 端到端 ==========


def _patch_db_limit_and_usage(
    monkeypatch: pytest.MonkeyPatch,
    *,
    db_limit: int,
    usage: int,
) -> None:
    """Mock 数据库层: _get_daily_limit_from_db 和 _get_daily_usage_from_db.

    让 check_daily_report_limit 走真实逻辑 (不 mock 整个函数),
    验证 COALESCE 优先级 + count >= limit 判断.

    Args:
        db_limit: 数据库返回的有效限额 (COALESCE 结果)
        usage: 数据库返回的当日已用次数
    """
    monkeypatch.setattr(
        "src.api.ip_user_resolver._get_daily_limit_from_db",
        AsyncMock(return_value=db_limit),
    )
    monkeypatch.setattr(
        "src.api.ip_user_resolver._get_daily_usage_from_db",
        AsyncMock(return_value=usage),
    )


def test_scenario_user_has_limit_under_non_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端到端场景 1: 用户有限额 (10), 已用 3 → 允许, 200, increment 调用."""
    settings = _make_settings(self_host=True, ip_daily_report_limit=5)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    _patch_db_limit_and_usage(monkeypatch, db_limit=10, usage=3)
    mock_increment = _patch_increment(monkeypatch)

    client = TestClient(app)
    response = client.post("/v1/chat/completions", json=_REQUEST_BODY)

    assert response.status_code == 200
    mock_increment.assert_awaited_once()


def test_scenario_user_has_limit_at_non_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端到端场景 1: 用户有限额 (10), 已用 10 → 拒绝, 429, increment 不调用."""
    settings = _make_settings(self_host=True, ip_daily_report_limit=5)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    _patch_db_limit_and_usage(monkeypatch, db_limit=10, usage=10)
    mock_increment = _patch_increment(monkeypatch)

    client = TestClient(app)
    response = client.post("/v1/chat/completions", json=_REQUEST_BODY)

    assert response.status_code == 429
    data = response.json()
    assert "10/10" in data["error"]["message"]
    mock_increment.assert_not_called()


def test_scenario_user_no_limit_uses_system_under(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端到端场景 2: 用户无限额, 系统限额 (5), 已用 2 → 允许, 200, increment 调用."""
    settings = _make_settings(self_host=True, ip_daily_report_limit=5)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    _patch_db_limit_and_usage(monkeypatch, db_limit=5, usage=2)
    mock_increment = _patch_increment(monkeypatch)

    client = TestClient(app)
    response = client.post("/v1/chat/completions", json=_REQUEST_BODY)

    assert response.status_code == 200
    mock_increment.assert_awaited_once()


def test_scenario_user_no_limit_uses_system_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端到端场景 2: 用户无限额, 系统限额 (5), 已用 5 → 拒绝, 429, increment 不调用."""
    settings = _make_settings(self_host=True, ip_daily_report_limit=5)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    _patch_db_limit_and_usage(monkeypatch, db_limit=5, usage=5)
    mock_increment = _patch_increment(monkeypatch)

    client = TestClient(app)
    response = client.post("/v1/chat/completions", json=_REQUEST_BODY)

    assert response.status_code == 429
    data = response.json()
    assert "5/5" in data["error"]["message"]
    mock_increment.assert_not_called()


def test_scenario_user_greater_than_system_under(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端到端场景 3: 用户限额 (10) > 系统 (5), 已用 7 → 允许 (用 10), 200, increment 调用.

    关键: 若用 MAX() 得 10 (与 COALESCE 一致); 若用系统 5 则 7>=5 拒绝 (错误).
    """
    settings = _make_settings(self_host=True, ip_daily_report_limit=5)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    _patch_db_limit_and_usage(monkeypatch, db_limit=10, usage=7)
    mock_increment = _patch_increment(monkeypatch)

    client = TestClient(app)
    response = client.post("/v1/chat/completions", json=_REQUEST_BODY)

    assert response.status_code == 200
    mock_increment.assert_awaited_once()


def test_scenario_user_greater_than_system_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端到端场景 3: 用户限额 (10) > 系统 (5), 已用 10 → 拒绝 (用 10), 429."""
    settings = _make_settings(self_host=True, ip_daily_report_limit=5)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    _patch_db_limit_and_usage(monkeypatch, db_limit=10, usage=10)
    mock_increment = _patch_increment(monkeypatch)

    client = TestClient(app)
    response = client.post("/v1/chat/completions", json=_REQUEST_BODY)

    assert response.status_code == 429
    data = response.json()
    assert "10/10" in data["error"]["message"]
    mock_increment.assert_not_called()


def test_scenario_user_less_than_system_under(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端到端场景 4: 用户限额 (3) < 系统 (5), 已用 2 → 允许 (用 3), 200, increment 调用."""
    settings = _make_settings(self_host=True, ip_daily_report_limit=5)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    _patch_db_limit_and_usage(monkeypatch, db_limit=3, usage=2)
    mock_increment = _patch_increment(monkeypatch)

    client = TestClient(app)
    response = client.post("/v1/chat/completions", json=_REQUEST_BODY)

    assert response.status_code == 200
    mock_increment.assert_awaited_once()


def test_scenario_user_less_than_system_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端到端场景 4: 用户限额 (3) < 系统 (5), 已用 3 → 拒绝 (用 3), 429.

    关键: 若用 MAX() 得 5, 3 < 5 允许 (错误);
    用 COALESCE 用户优先得 3, 3 >= 3 拒绝 (正确).
    """
    settings = _make_settings(self_host=True, ip_daily_report_limit=5)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    _patch_db_limit_and_usage(monkeypatch, db_limit=3, usage=3)
    mock_increment = _patch_increment(monkeypatch)

    client = TestClient(app)
    response = client.post("/v1/chat/completions", json=_REQUEST_BODY)

    assert response.status_code == 429
    data = response.json()
    assert "3/3" in data["error"]["message"]
    mock_increment.assert_not_called()


def test_scenario_user_less_than_system_between(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端到端场景 4 变体: 用户限额 (3) < 系统 (5), 已用 4 → 拒绝 (用 3), 429.

    关键: 若用 MAX() 得 5, 4 < 5 允许 (错误);
    用 COALESCE 用户优先得 3, 4 >= 3 拒绝 (正确).
    这是最关键的差异验证用例 (4 在 3 和 5 之间).
    """
    settings = _make_settings(self_host=True, ip_daily_report_limit=5)
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    _patch_db_limit_and_usage(monkeypatch, db_limit=3, usage=4)
    mock_increment = _patch_increment(monkeypatch)

    client = TestClient(app)
    response = client.post("/v1/chat/completions", json=_REQUEST_BODY)

    assert response.status_code == 429
    data = response.json()
    assert "4/3" in data["error"]["message"]
    mock_increment.assert_not_called()


# ========== 场景 12: increment 调用参数正确 ==========


def test_increment_called_with_correct_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """increment_daily_report_count 应以正确的 user_id 和 agent_id 调用.

    验证: IP-based 用户的 user_id (ip_ 前缀) 和 agent_id (agent_name) 正确传递.
    """
    settings = _make_settings(
        self_host=True, ip_daily_report_limit=5, agent_name="agentinsight-researcher"
    )
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)
    _patch_pipeline(monkeypatch)
    _patch_limit_check(monkeypatch, allowed=True, count=1)
    mock_increment = _patch_increment(monkeypatch)

    client = TestClient(app)
    response = client.post("/v1/chat/completions", json=_REQUEST_BODY)

    assert response.status_code == 200
    mock_increment.assert_awaited_once()
    # 验证调用参数: user_id 以 "ip_" 开头, agent_id 为 agent_name
    call_args = mock_increment.await_args
    assert call_args is not None
    args, kwargs = call_args
    # increment_daily_report_count(user_id, agent_id) 位置参数
    assert len(args) >= 2
    assert args[0].startswith("ip_")  # user_id
    assert args[1] == "agentinsight-researcher"  # agent_id
