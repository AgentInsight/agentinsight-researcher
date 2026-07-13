"""单元测试: 会话管理 API 路由.

验证 src/api/session_routes.py:
- GET    /v1/sessions                          列出当前用户会话
- GET    /v1/sessions/latest                   获取最近会话 (404 无会话)
- GET    /v1/sessions/{session_id}/messages    获取会话消息 (分页, 滚动加载)
- POST   /v1/sessions                          创建新会话
- DELETE /v1/sessions/{session_id}             删除会话 (级联清理)
- PATCH  /v1/sessions/{session_id}             更新会话标题

数据隔离:
- 所有查询带 agent_id + user_id
- user_id 由 JWT 中间件注入 (contextvars)
- agent_id = agent_name (全局唯一隔离键)

使用 FastAPI TestClient + mock SessionStore, 不依赖外部服务.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import session_routes
from src.api.session_routes import router

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture
def app() -> FastAPI:
    """创建测试用 FastAPI 应用 (仅含 session router)."""
    application = FastAPI()
    application.include_router(router)
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """创建测试客户端."""
    return TestClient(app)


@pytest.fixture
def mock_store():
    """Mock SessionStore.

    patch session_routes.get_session_store 返回 AsyncMock,
    所有方法 (list_sessions/get_session/save_message 等) 均为 AsyncMock.
    """
    store = AsyncMock()
    with patch.object(session_routes, "get_session_store", return_value=store):
        yield store


@pytest.fixture
def mock_request_context():
    """Mock 请求上下文 (agent_id + user_id).

    请求上下文用 contextvars, 测试中 patch 模块级引用.
    """
    with (
        patch.object(session_routes, "get_request_agent_id", return_value="test-agent"),
        patch.object(session_routes, "get_request_user_id", return_value="test-user"),
    ):
        yield


def _make_session(**overrides: Any) -> dict[str, Any]:
    """构造测试用会话字典."""
    now = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
    session: dict[str, Any] = {
        "session_id": "sess-1",
        "title": "测试会话",
        "query": "研究 AI",
        "status": "active",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "message_count": 5,
    }
    session.update(overrides)
    return session


def _make_message(**overrides: Any) -> dict[str, Any]:
    """构造测试用消息字典."""
    msg: dict[str, Any] = {
        "id": 1,
        "session_id": "sess-1",
        "agent_id": "test-agent",
        "user_id": "test-user",
        "role": "user",
        "content": "你好",
        "message_metadata": None,
        "created_at": datetime(2026, 1, 15, tzinfo=UTC).isoformat(),
    }
    msg.update(overrides)
    return msg


# ========== GET /v1/sessions: 列出会话 ==========


def test_list_sessions_returns_list(
    client: TestClient,
    mock_store: AsyncMock,
    mock_request_context: None,
) -> None:
    """GET /v1/sessions → 返回当前用户会话列表."""
    mock_store.list_sessions.return_value = [
        _make_session(session_id="sess-1", title="会话1"),
        _make_session(session_id="sess-2", title="会话2"),
    ]

    response = client.get("/v1/sessions")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["session_id"] == "sess-1"
    assert data[1]["session_id"] == "sess-2"
    # 验证调用参数含 agent_id + user_id (数据隔离)
    mock_store.list_sessions.assert_awaited_once_with(
        "test-agent", "test-user", limit=50, offset=0
    )


def test_list_sessions_empty_returns_empty_list(
    client: TestClient,
    mock_store: AsyncMock,
    mock_request_context: None,
) -> None:
    """无会话时返回空列表 []."""
    mock_store.list_sessions.return_value = []

    response = client.get("/v1/sessions")

    assert response.status_code == 200
    assert response.json() == []


def test_list_sessions_with_pagination(
    client: TestClient,
    mock_store: AsyncMock,
    mock_request_context: None,
) -> None:
    """GET /v1/sessions?limit=10&offset=20 → 分页参数传递."""
    mock_store.list_sessions.return_value = []

    response = client.get("/v1/sessions?limit=10&offset=20")

    assert response.status_code == 200
    mock_store.list_sessions.assert_awaited_once_with(
        "test-agent", "test-user", limit=10, offset=20
    )


# ========== GET /v1/sessions/latest: 获取最近会话 ==========


def test_get_latest_session_found(
    client: TestClient,
    mock_store: AsyncMock,
    mock_request_context: None,
) -> None:
    """GET /v1/sessions/latest → 返回最近活跃会话."""
    mock_store.get_latest_session.return_value = _make_session(
        session_id="sess-latest", title="最新会话"
    )

    response = client.get("/v1/sessions/latest")

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "sess-latest"
    mock_store.get_latest_session.assert_awaited_once_with("test-agent", "test-user")


def test_get_latest_session_not_found_returns_404(
    client: TestClient,
    mock_store: AsyncMock,
    mock_request_context: None,
) -> None:
    """无会话时返回 404."""
    mock_store.get_latest_session.return_value = None

    response = client.get("/v1/sessions/latest")

    assert response.status_code == 404
    assert response.json()["detail"] == "无会话记录"


# ========== GET /v1/sessions/{session_id}/messages: 获取消息 ==========


def test_list_session_messages_success(
    client: TestClient,
    mock_store: AsyncMock,
    mock_request_context: None,
) -> None:
    """GET /v1/sessions/{session_id}/messages → 返回消息列表."""
    mock_store.get_session.return_value = _make_session()
    mock_store.list_messages.return_value = [
        _make_message(id=1, role="user", content="你好"),
        _make_message(id=2, role="assistant", content="你好, 有什么可以帮你?"),
    ]
    mock_store.get_message_count.return_value = 2

    response = client.get("/v1/sessions/sess-1/messages")

    assert response.status_code == 200
    data = response.json()
    assert len(data["messages"]) == 2
    assert data["total"] == 2
    assert data["has_more"] is False
    # 验证数据隔离参数
    mock_store.list_messages.assert_awaited_once_with(
        "sess-1", "test-agent", "test-user", limit=10, offset=0
    )


def test_list_session_messages_has_more_true(
    client: TestClient,
    mock_store: AsyncMock,
    mock_request_context: None,
) -> None:
    """消息总数超过 limit+offset 时 has_more=True."""
    mock_store.get_session.return_value = _make_session()
    mock_store.list_messages.return_value = []
    mock_store.get_message_count.return_value = 25  # offset=0, limit=10 → has_more=True

    response = client.get("/v1/sessions/sess-1/messages?limit=10&offset=0")

    assert response.status_code == 200
    assert response.json()["has_more"] is True


def test_list_session_messages_session_not_found_404(
    client: TestClient,
    mock_store: AsyncMock,
    mock_request_context: None,
) -> None:
    """会话不存在或无权访问 → 404."""
    mock_store.get_session.return_value = None

    response = client.get("/v1/sessions/sess-not-exist/messages")

    assert response.status_code == 404
    assert "会话不存在" in response.json()["detail"]
    # 不应调用 list_messages
    mock_store.list_messages.assert_not_awaited()


# ========== POST /v1/sessions: 创建会话 ==========


def test_create_session_with_explicit_id(
    client: TestClient,
    mock_store: AsyncMock,
    mock_request_context: None,
) -> None:
    """POST /v1/sessions 带 session_id → 使用传入 ID."""
    mock_store.get_session.return_value = _make_session(session_id="custom-id", title="标题")

    response = client.post("/v1/sessions", json={"session_id": "custom-id", "title": "标题"})

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "custom-id"
    assert data["message_count"] == 0
    mock_store.create_session.assert_awaited_once_with(
        "custom-id", "test-agent", "test-user", title="标题", client_ip=""
    )


def test_create_session_auto_generate_id(
    client: TestClient,
    mock_store: AsyncMock,
    mock_request_context: None,
) -> None:
    """POST /v1/sessions 不传 session_id → 自动生成 UUID."""
    mock_store.get_session.return_value = None  # 创建后查询返回 None (极端情况)

    response = client.post("/v1/sessions", json={})

    assert response.status_code == 200
    data = response.json()
    # 自动生成的 session_id 应为 UUID 格式
    assert "session_id" in data
    assert len(data["session_id"]) > 0
    # create_session 被调用, session_id 为自动生成的值
    call_args = mock_store.create_session.call_args
    assert len(call_args.args[0]) > 0  # 自动生成的 session_id
    assert call_args.args[1] == "test-agent"
    assert call_args.args[2] == "test-user"


# ========== DELETE /v1/sessions/{session_id}: 删除会话 ==========


def test_delete_session_success(
    client: TestClient,
    mock_store: AsyncMock,
    mock_request_context: None,
) -> None:
    """DELETE /v1/sessions/{session_id} → 级联删除成功."""
    mock_store.get_session.return_value = _make_session()
    mock_store.delete_session.return_value = True

    with (
        patch.object(
            session_routes, "_cleanup_checkpointer", new=AsyncMock(return_value=None)
        ),
        patch.object(
            session_routes, "_cleanup_redis_cache", new=AsyncMock(return_value=None)
        ),
    ):
        response = client.delete("/v1/sessions/sess-1")

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "sess-1"
    assert data["deleted"] is True
    mock_store.delete_session.assert_awaited_once_with("sess-1", "test-agent", "test-user")


def test_delete_session_not_found_404(
    client: TestClient,
    mock_store: AsyncMock,
    mock_request_context: None,
) -> None:
    """删除不存在的会话 → 404 (get_session 返回 None)."""
    mock_store.get_session.return_value = None

    response = client.delete("/v1/sessions/sess-not-exist")

    assert response.status_code == 404
    assert "会话不存在" in response.json()["detail"]
    # 不应调用 delete_session
    mock_store.delete_session.assert_not_awaited()


def test_delete_session_store_returns_false_404(
    client: TestClient,
    mock_store: AsyncMock,
    mock_request_context: None,
) -> None:
    """get_session 找到但 delete_session 返回 False → 404."""
    mock_store.get_session.return_value = _make_session()
    mock_store.delete_session.return_value = False

    response = client.delete("/v1/sessions/sess-1")

    assert response.status_code == 404
    assert response.json()["detail"] == "会话不存在"


# ========== PATCH /v1/sessions/{session_id}: 更新会话标题 ==========


def test_update_session_title_success(
    client: TestClient,
    mock_store: AsyncMock,
    mock_request_context: None,
) -> None:
    """PATCH /v1/sessions/{session_id} → 更新标题成功."""
    mock_store.get_session.return_value = _make_session()
    mock_store.update_session_title.return_value = True

    response = client.patch("/v1/sessions/sess-1", json={"title": "新标题"})

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "sess-1"
    assert data["title"] == "新标题"
    assert data["updated"] is True
    mock_store.update_session_title.assert_awaited_once_with(
        "sess-1", "test-agent", "test-user", "新标题"
    )


def test_update_session_title_session_not_found_404(
    client: TestClient,
    mock_store: AsyncMock,
    mock_request_context: None,
) -> None:
    """更新不存在的会话 → 404."""
    mock_store.get_session.return_value = None

    response = client.patch("/v1/sessions/sess-not-exist", json={"title": "新标题"})

    assert response.status_code == 404
    assert "会话不存在" in response.json()["detail"]
    mock_store.update_session_title.assert_not_awaited()


def test_update_session_title_empty_title_422(
    client: TestClient,
    mock_store: AsyncMock,
    mock_request_context: None,
) -> None:
    """title 为空字符串 → 422 (Pydantic min_length=1 校验)."""
    response = client.patch("/v1/sessions/sess-1", json={"title": ""})

    assert response.status_code == 422
    # 不应调用 store
    mock_store.update_session_title.assert_not_awaited()


def test_update_session_title_update_fails_500(
    client: TestClient,
    mock_store: AsyncMock,
    mock_request_context: None,
) -> None:
    """update_session_title 返回 False → 500 (更新失败)."""
    mock_store.get_session.return_value = _make_session()
    mock_store.update_session_title.return_value = False

    response = client.patch("/v1/sessions/sess-1", json={"title": "新标题"})

    assert response.status_code == 500
    assert response.json()["detail"] == "更新失败"


# ========== 认证与数据隔离 ==========


def test_unauthorized_no_user_id_returns_401(
    client: TestClient,
    mock_store: AsyncMock,
) -> None:
    """无 user_id (中间件未注入) → 401."""
    with (
        patch.object(session_routes, "get_request_agent_id", return_value="test-agent"),
        patch.object(session_routes, "get_request_user_id", return_value=""),
    ):
        response = client.get("/v1/sessions")

    assert response.status_code == 401
    assert "无法解析用户身份" in response.json()["detail"]


def test_no_agent_id_returns_500(
    client: TestClient,
    mock_store: AsyncMock,
) -> None:
    """无 agent_id → 500 (服务器配置错误)."""
    with (
        patch.object(session_routes, "get_request_agent_id", return_value=""),
        patch.object(session_routes, "get_request_user_id", return_value="test-user"),
    ):
        response = client.get("/v1/sessions")

    assert response.status_code == 500
    assert "无法解析 Agent 身份" in response.json()["detail"]


def test_data_isolation_agent_user_passed_to_store(
    client: TestClient,
    mock_store: AsyncMock,
) -> None:
    """不同 agent_id/user_id → 查询参数隔离 (互不可见)."""
    mock_store.list_sessions.return_value = []

    # 用户 A 查询
    with (
        patch.object(session_routes, "get_request_agent_id", return_value="agent-A"),
        patch.object(session_routes, "get_request_user_id", return_value="user-A"),
    ):
        client.get("/v1/sessions")

    # 验证查询使用了 agent-A 和 user-A
    call_args_a = mock_store.list_sessions.call_args
    assert call_args_a.args[0] == "agent-A"
    assert call_args_a.args[1] == "user-A"

    # 用户 B 查询
    mock_store.list_sessions.reset_mock()
    mock_store.list_sessions.return_value = []

    with (
        patch.object(session_routes, "get_request_agent_id", return_value="agent-B"),
        patch.object(session_routes, "get_request_user_id", return_value="user-B"),
    ):
        client.get("/v1/sessions")

    # 验证查询使用了 agent-B 和 user-B (与 A 不同)
    call_args_b = mock_store.list_sessions.call_args
    assert call_args_b.args[0] == "agent-B"
    assert call_args_b.args[1] == "user-B"
