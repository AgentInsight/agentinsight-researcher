"""单元测试: MCP 配置管理 API.

验证 src/api/mcp_routes.py:
- GET /v1/mcp/system: 列出系统 MCP 配置
- POST /v1/mcp/system/{id}/clone: 克隆系统 MCP 到用户私有列表
- POST /v1/mcp/{id}/test: 测试已保存的 MCP 配置
- POST /v1/mcp: 创建用户 MCP 配置
- PUT /v1/mcp/{id}: 更新用户 MCP 配置
- DELETE /v1/mcp/{id}: 删除用户 MCP 配置
- 数据隔离: agent_id + user_id

数据隔离键 agent_id = agent_name, 用户私有数据按 user_id 区分.
单元测试不依赖外部服务, mock 数据库池.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import mcp_routes
from src.api.mcp_routes import router

pytestmark = pytest.mark.unit


# ========== 辅助函数 ==========


class _AsyncPoolContext:
    """Mock asyncpg pool.acquire() 返回的异步上下文管理器.

    asyncpg 的 pool.acquire() 返回 _PoolContextManager (非 coroutine),
    支持 async with pool.acquire() as conn: 语法.
    """

    def __init__(self, conn: AsyncMock) -> None:
        self._conn = conn

    async def __aenter__(self) -> AsyncMock:
        return self._conn

    async def __aexit__(self, *args: Any) -> None:
        return None


class _MockPool:
    """Mock asyncpg 连接池 (acquire 返回异步上下文管理器)."""

    def __init__(self, conn: AsyncMock) -> None:
        self._conn = conn

    def acquire(self) -> _AsyncPoolContext:
        return _AsyncPoolContext(self._conn)


def _make_record(**overrides: Any) -> dict[str, Any]:
    """构造 fake asyncpg Record (dict 兼容 dict(row) 调用)."""
    record: dict[str, Any] = {
        "id": 1,
        "name": "test-mcp",
        "server_url": None,
        "transport_type": "stdio",
        "command": "npx",
        "args": None,
        "env_vars": None,
        "enabled": True,
        "is_system": False,
        "description": "test description",
        "created_at": datetime(2024, 1, 1, tzinfo=UTC),
        "updated_at": datetime(2024, 1, 1, tzinfo=UTC),
    }
    record.update(overrides)
    return record


# ========== Fixtures ==========


@pytest.fixture
def app() -> FastAPI:
    """创建测试用 FastAPI 应用 (仅含 MCP router)."""
    application = FastAPI()
    application.include_router(router)
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """创建测试客户端."""
    return TestClient(app)


@pytest.fixture
def mock_db():
    """Mock asyncpg 连接池与连接.

    patch mcp_routes.get_pool 返回 mock 池, yield mock 连接对象.
    asyncpg 的 pool.acquire() 返回异步上下文管理器 (非 coroutine),
    故用 _MockPool 封装而非 AsyncMock.
    """
    conn = AsyncMock()
    pool = _MockPool(conn)

    with patch.object(mcp_routes, "get_pool", new=AsyncMock(return_value=pool)):
        yield conn


@pytest.fixture
def mock_request_context():
    """Mock 请求上下文 (agent_id + user_id).

    请求上下文用 contextvars, 测试中 patch 模块级引用.
    """
    with (
        patch.object(mcp_routes, "get_request_agent_id", return_value="test-agent"),
        patch.object(mcp_routes, "get_request_user_id", return_value="test-user"),
    ):
        yield


# ========== GET /v1/mcp/system: 列出系统 MCP ==========


def test_list_system_mcp_configs_returns_list(
    client: TestClient,
    mock_db: AsyncMock,
    mock_request_context: None,
) -> None:
    """GET /v1/mcp/system → 返回系统 MCP 配置列表."""
    mock_db.fetch.return_value = [
        _make_record(id=1, name="system-mcp-1", is_system=True),
        _make_record(id=2, name="system-mcp-2", is_system=True),
    ]

    response = client.get("/v1/mcp/system")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["name"] == "system-mcp-1"
    assert data[1]["name"] == "system-mcp-2"
    # 查询应使用 agent_id
    call_args = mock_db.fetch.call_args
    assert "test-agent" in call_args.args


def test_list_system_mcp_configs_empty_returns_empty_list(
    client: TestClient,
    mock_db: AsyncMock,
    mock_request_context: None,
) -> None:
    """无系统 MCP 配置 → 返回空列表 []."""
    mock_db.fetch.return_value = []

    response = client.get("/v1/mcp/system")

    assert response.status_code == 200
    assert response.json() == []


# ========== POST /v1/mcp/system/{id}/clone: 克隆系统 MCP ==========


def test_clone_system_mcp_creates_user_copy(
    client: TestClient,
    mock_db: AsyncMock,
    mock_request_context: None,
) -> None:
    """POST /v1/mcp/system/{id}/clone → 创建 is_system=False 副本."""
    src_record = _make_record(
        id=1,
        name="system-mcp",
        is_system=True,
        enabled=True,
        command="npx",
    )
    cloned_record = _make_record(
        id=10,
        name="system-mcp",
        is_system=False,
        enabled=False,
        command="npx",
    )
    # fetchrow 第一次返回源配置, 第二次返回新插入的副本
    mock_db.fetchrow.side_effect = [src_record, cloned_record]
    # fetchval 检查是否已克隆 → None 表示未克隆
    mock_db.fetchval.return_value = None

    response = client.post("/v1/mcp/system/1/clone")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 10
    assert data["is_system"] is False
    assert data["enabled"] is False
    assert data["name"] == "system-mcp"


def test_clone_system_mcp_not_found_returns_404(
    client: TestClient,
    mock_db: AsyncMock,
    mock_request_context: None,
) -> None:
    """不存在的系统 MCP ID → 404."""
    mock_db.fetchrow.return_value = None

    response = client.post("/v1/mcp/system/999/clone")

    assert response.status_code == 404
    assert "系统 MCP 配置不存在" in response.json()["detail"]


# ========== POST /v1/mcp/{id}/test: 测试已保存配置 ==========


def test_test_mcp_config_by_id_not_found_returns_404(
    client: TestClient,
    mock_db: AsyncMock,
    mock_request_context: None,
) -> None:
    """测试不存在的 MCP 配置 ID → 404 (is_system=False 和 is_system=True 查询均返回 None)."""
    mock_db.fetchrow.return_value = None

    response = client.post("/v1/mcp/999/test")

    assert response.status_code == 404
    assert "MCP 配置不存在" in response.json()["detail"]


# ========== POST /v1/mcp: 创建用户 MCP 配置 ==========


def test_create_user_mcp_config_success(
    client: TestClient,
    mock_db: AsyncMock,
    mock_request_context: None,
) -> None:
    """POST /v1/mcp → 创建用户私有配置 (is_system=False)."""
    new_record = _make_record(
        id=1,
        name="my-mcp",
        is_system=False,
        enabled=True,
        transport_type="stdio",
        command="npx",
    )
    mock_db.fetchrow.return_value = new_record

    test_result = {
        "success": True,
        "message": "连接成功, 发现 1 个工具",
        "error_type": None,
        "tools_count": 1,
        "tools": ["tool1"],
        "latency_ms": 100,
    }

    with patch.object(mcp_routes, "_test_mcp_config", new=AsyncMock(return_value=test_result)):
        response = client.post(
            "/v1/mcp",
            json={
                "name": "my-mcp",
                "transport_type": "stdio",
                "command": "npx",
                "enabled": True,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "my-mcp"
    assert data["is_system"] is False
    assert data["test_result"]["success"] is True


def test_create_user_mcp_config_stdio_missing_command_returns_422(
    client: TestClient,
    mock_db: AsyncMock,
    mock_request_context: None,
) -> None:
    """stdio 传输模式缺少 command → Pydantic 校验错误 (422)."""
    response = client.post(
        "/v1/mcp",
        json={
            "name": "my-mcp",
            "transport_type": "stdio",
            # 缺少 command 字段
        },
    )

    assert response.status_code == 422
    # 响应应含校验错误信息
    detail = response.json()["detail"]
    assert any("command" in str(err.get("msg", "")).lower() for err in detail)


# ========== PUT /v1/mcp/{id}: 更新用户 MCP 配置 ==========


def test_update_user_mcp_config_success(
    client: TestClient,
    mock_db: AsyncMock,
    mock_request_context: None,
) -> None:
    """PUT /v1/mcp/{id} → 更新用户私有配置."""
    current_record = _make_record(
        id=1,
        name="old-name",
        enabled=False,
        is_system=False,
    )
    updated_record = _make_record(
        id=1,
        name="new-name",
        enabled=False,
        is_system=False,
    )
    # fetchrow 第一次返回当前配置, 第二次返回更新后的配置
    mock_db.fetchrow.side_effect = [current_record, updated_record]

    response = client.put(
        "/v1/mcp/1",
        json={
            "name": "new-name",
            "transport_type": "stdio",
            "command": "npx",
            "enabled": False,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "new-name"
    assert data["id"] == 1


# ========== DELETE /v1/mcp/{id}: 删除用户 MCP 配置 ==========


def test_delete_user_mcp_config_success(
    client: TestClient,
    mock_db: AsyncMock,
    mock_request_context: None,
) -> None:
    """DELETE /v1/mcp/{id} → 删除用户私有配置."""
    mock_db.execute.return_value = "DELETE 1"

    response = client.delete("/v1/mcp/1")

    assert response.status_code == 200
    assert response.json()["deleted"] is True


# ========== 数据隔离: agent_id + user_id ==========


def test_mcp_config_isolation_by_agent_user(
    client: TestClient,
    mock_db: AsyncMock,
) -> None:
    """不同 agent_id/user_id → 查询参数隔离 (互不可见)."""
    mock_db.fetch.return_value = []

    # 用户 A 查询
    with (
        patch.object(mcp_routes, "get_request_agent_id", return_value="agent-A"),
        patch.object(mcp_routes, "get_request_user_id", return_value="user-A"),
    ):
        client.get("/v1/mcp")

    # 验证查询使用了 agent-A 和 user-A
    call_args_a = mock_db.fetch.call_args
    assert "agent-A" in call_args_a.args
    assert "user-A" in call_args_a.args

    # 用户 B 查询
    mock_db.fetch.reset_mock()
    mock_db.fetch.return_value = []

    with (
        patch.object(mcp_routes, "get_request_agent_id", return_value="agent-B"),
        patch.object(mcp_routes, "get_request_user_id", return_value="user-B"),
    ):
        client.get("/v1/mcp")

    # 验证查询使用了 agent-B 和 user-B (与 A 不同)
    call_args_b = mock_db.fetch.call_args
    assert "agent-B" in call_args_b.args
    assert "user-B" in call_args_b.args
    # A 和 B 的查询参数应不同
    assert call_args_a.args[1] != call_args_b.args[1]  # agent_id 不同
    assert call_args_a.args[2] != call_args_b.args[2]  # user_id 不同
