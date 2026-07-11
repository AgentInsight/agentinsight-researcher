"""API 测试: MCP 配置 API 端点 (CRUD/clone/test) + 隔离场景.

测试约定:
- API 测试在 docker compose up -d 且全部容器 service_healthy 后执行
- 测试目标地址从环境变量 AGENT_URL 注入
- 每次用唯一 session_id=test_* / config name=test-api-mcp-*
- 测试用例独立可重复运行, 不依赖执行顺序

本文件聚焦 MCP 配置 API 的 HTTP 契约测试 (与 functional 层互补):
- POST /v1/mcp: 创建配置 (含可用性测试 → 不可达时 enabled 自动置 False)
- GET /v1/mcp: 列出当前用户私有配置 (不含系统 MCP)
- GET /v1/mcp/system: 列出系统 MCP 配置
- POST /v1/mcp/system/{id}/clone: 克隆系统 MCP → 用户私有副本 (enabled=False)
- PUT /v1/mcp/{id}: 更新配置 (enabled False→True 触发 test, 失败保持 False)
- DELETE /v1/mcp/{id}: 删除配置 (仅用户私有, 系统 MCP 不可删)
- POST /v1/mcp/test: 测试配置可用性 (不入库)
- POST /v1/mcp/{id}/test: 测试已保存配置
- 错误码: 422 (Pydantic 校验) / 404 (不存在) / 409 (克隆重名)
- 数据隔离: agent_id + user_id (SELF_HOST 模式下用户视角一致性)

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/api/test_mcp_endpoints.py -v -m api
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

# 测试目标地址从环境变量注入, 禁止硬编码
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")

# API 测试超时 60s (MCP 可用性测试 30s + 余量)
API_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)


def _unique_config_name() -> str:
    """生成唯一 MCP 配置名 (避免并发测试冲突)."""
    return f"test-api-mcp-{uuid.uuid4().hex[:8]}"


def _create_config_payload(
    *,
    name: str | None = None,
    transport_type: str = "streamable_http",
    server_url: str = "http://api-test:1/mcp",
    command: str | None = None,
    args: list[str] | None = None,
    env_vars: dict[str, str] | None = None,
    enabled: bool = False,
    description: str = "api test config",
) -> dict[str, object]:
    """构造 POST /v1/mcp 请求体."""
    payload: dict[str, object] = {
        "name": name or _unique_config_name(),
        "transport_type": transport_type,
        "enabled": enabled,
        "description": description,
    }
    if transport_type == "stdio":
        payload["command"] = command or "echo"
        if args:
            payload["args"] = args
        if env_vars:
            payload["env_vars"] = env_vars
    else:
        payload["server_url"] = server_url
    return payload


@pytest.fixture()
def cleanup_configs():
    """fixture: 收集测试中创建的 config_id, 用例后清理 (避免污染)."""
    created_ids: list[int] = []
    yield created_ids
    # 清理
    with httpx.Client(timeout=API_TIMEOUT) as client:
        for cid in created_ids:
            try:
                client.delete(f"{AGENT_URL}/v1/mcp/{cid}")
            except Exception:  # noqa: BLE001
                pass


# ========== POST /v1/mcp: 创建配置 ==========


@pytest.mark.api
def test_create_mcp_config_streamable_http(cleanup_configs: list[int]) -> None:
    """创建 streamable_http 模式配置 → 200 + 含 id + test_result."""
    payload = _create_config_payload(
        transport_type="streamable_http",
        server_url="http://127.0.0.1:1/mcp",  # 不可达
        enabled=False,
    )
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(f"{AGENT_URL}/v1/mcp", json=payload)

    assert r.status_code == 200, f"创建失败: {r.status_code} {r.text}"
    data = r.json()
    assert "id" in data
    assert data["name"] == payload["name"]
    assert data["transport_type"] == "streamable_http"
    assert data["is_system"] is False
    # 不可达: test_result.success 应为 False
    assert "test_result" in data
    assert data["test_result"]["success"] is False
    cleanup_configs.append(data["id"])


@pytest.mark.api
def test_create_mcp_config_stdio_mode(cleanup_configs: list[int]) -> None:
    """创建 stdio 模式配置 → 200."""
    payload = _create_config_payload(
        transport_type="stdio",
        command="echo",
        args=["hello"],
        env_vars={"TEST_VAR": "value"},
        enabled=False,
    )
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(f"{AGENT_URL}/v1/mcp", json=payload)

    assert r.status_code == 200, f"stdio 创建失败: {r.status_code} {r.text}"
    data = r.json()
    assert data["transport_type"] == "stdio"
    cleanup_configs.append(data["id"])


@pytest.mark.api
def test_create_mcp_config_unreachable_disables_enabled(
    cleanup_configs: list[int],
) -> None:
    """enabled=True + 不可达 server_url → test 失败 → enabled 强制为 False (用户需求 1)."""
    payload = _create_config_payload(
        server_url="http://127.0.0.1:1/mcp",
        enabled=True,  # 用户请求启用
    )
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(f"{AGENT_URL}/v1/mcp", json=payload)

    assert r.status_code == 200
    data = r.json()
    # 不可达 → test 失败 → enabled 强制 False
    assert data["enabled"] is False
    assert data["test_result"]["success"] is False
    cleanup_configs.append(data["id"])


@pytest.mark.api
def test_create_mcp_config_missing_command_returns_422() -> None:
    """stdio 模式缺 command → 422 (Pydantic model_validator)."""
    payload = {
        "name": _unique_config_name(),
        "transport_type": "stdio",
        "command": None,  # 缺 command
        "enabled": False,
    }
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(f"{AGENT_URL}/v1/mcp", json=payload)
    assert r.status_code == 422


@pytest.mark.api
def test_create_mcp_config_missing_url_returns_422() -> None:
    """sse 模式缺 server_url → 422."""
    payload = {
        "name": _unique_config_name(),
        "transport_type": "sse",
        "server_url": None,
        "enabled": False,
    }
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(f"{AGENT_URL}/v1/mcp", json=payload)
    assert r.status_code == 422


@pytest.mark.api
def test_create_mcp_config_invalid_transport_returns_422() -> None:
    """非法 transport_type → 422 (Literal 校验)."""
    payload = {
        "name": _unique_config_name(),
        "transport_type": "invalid_transport",
        "server_url": "http://x:1/mcp",
        "enabled": False,
    }
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(f"{AGENT_URL}/v1/mcp", json=payload)
    assert r.status_code == 422


# ========== GET /v1/mcp: 列出配置 ==========


@pytest.mark.api
def test_list_mcp_configs_returns_user_only(
    cleanup_configs: list[int],
) -> None:
    """GET /v1/mcp → 仅返回当前用户私有配置 (is_system=False)."""
    name = _unique_config_name()
    payload = _create_config_payload(name=name, enabled=False)
    with httpx.Client(timeout=API_TIMEOUT) as client:
        created = client.post(f"{AGENT_URL}/v1/mcp", json=payload).json()
        cleanup_configs.append(created["id"])

        # 列出
        r = client.get(f"{AGENT_URL}/v1/mcp")

    assert r.status_code == 200
    configs = r.json()
    # 应含刚创建的
    matching = [c for c in configs if c["name"] == name]
    assert len(matching) == 1
    # 列出不应含系统 MCP
    assert all(c.get("is_system") is False for c in configs)


@pytest.mark.api
def test_list_mcp_configs_empty_returns_list() -> None:
    """GET /v1/mcp (无配置时) → 200 + 空列表 []."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.get(f"{AGENT_URL}/v1/mcp")

    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)


# ========== GET /v1/mcp/system: 列出系统配置 ==========


@pytest.mark.api
def test_list_system_mcp_configs() -> None:
    """GET /v1/mcp/system → 200 + list (可能为空, 但应为 list)."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.get(f"{AGENT_URL}/v1/mcp/system")

    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    # 所有返回的都应是 is_system=True
    for c in data:
        assert c.get("is_system") is True


# ========== PUT /v1/mcp/{id}: 更新配置 ==========


@pytest.mark.api
def test_update_mcp_config_skip_test(
    cleanup_configs: list[int],
) -> None:
    """PUT ?skip_test=true → 跳过可用性测试, 直接更新字段."""
    # 创建 enabled=False
    create_payload = _create_config_payload(enabled=False)
    with httpx.Client(timeout=API_TIMEOUT) as client:
        created = client.post(f"{AGENT_URL}/v1/mcp", json=create_payload).json()
        cleanup_configs.append(created["id"])
        config_id = created["id"]

        # PUT skip_test=true 更新 description
        update_payload = _create_config_payload(
            name=create_payload["name"],
            enabled=False,
            description="updated via skip_test",
        )
        r = client.put(
            f"{AGENT_URL}/v1/mcp/{config_id}?skip_test=true",
            json=update_payload,
        )

    assert r.status_code == 200, f"更新失败: {r.status_code} {r.text}"
    updated = r.json()
    assert updated["description"] == "updated via skip_test"


@pytest.mark.api
def test_update_mcp_config_enable_unreachable_keeps_disabled(
    cleanup_configs: list[int],
) -> None:
    """PUT enabled False→True + 不可达 → test 失败 → enabled 保持 False (用户需求 3)."""
    create_payload = _create_config_payload(
        server_url="http://127.0.0.1:1/mcp",
        enabled=False,
    )
    with httpx.Client(timeout=API_TIMEOUT) as client:
        created = client.post(f"{AGENT_URL}/v1/mcp", json=create_payload).json()
        cleanup_configs.append(created["id"])

        # 尝试切换为 enabled=True (不 skip_test)
        update_payload = _create_config_payload(
            name=create_payload["name"],
            server_url="http://127.0.0.1:1/mcp",
            enabled=True,
        )
        r = client.put(f"{AGENT_URL}/v1/mcp/{created['id']}", json=update_payload)

    assert r.status_code == 200
    updated = r.json()
    # 不可达 → test 失败 → enabled 强制 False
    assert updated["enabled"] is False
    assert updated["test_result"]["success"] is False


@pytest.mark.api
def test_update_nonexistent_config_returns_404() -> None:
    """PUT 不存在的 config_id → 404."""
    payload = _create_config_payload(enabled=False)
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.put(f"{AGENT_URL}/v1/mcp/999999", json=payload)
    assert r.status_code == 404


# ========== DELETE /v1/mcp/{id} ==========


@pytest.mark.api
def test_delete_mcp_config(cleanup_configs: list[int]) -> None:
    """DELETE 已存在配置 → 200 + {deleted: True}."""
    payload = _create_config_payload(enabled=False)
    with httpx.Client(timeout=API_TIMEOUT) as client:
        created = client.post(f"{AGENT_URL}/v1/mcp", json=payload).json()
        config_id = created["id"]

        r = client.delete(f"{AGENT_URL}/v1/mcp/{config_id}")
        assert r.status_code == 200
        assert r.json()["deleted"] is True

        # 二次删除 → 404
        r2 = client.delete(f"{AGENT_URL}/v1/mcp/{config_id}")
        assert r2.status_code == 404


@pytest.mark.api
def test_delete_nonexistent_config_returns_404() -> None:
    """DELETE 不存在的 config_id → 404."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.delete(f"{AGENT_URL}/v1/mcp/999999")
    assert r.status_code == 404


# ========== POST /v1/mcp/system/{id}/clone: 克隆 ==========


@pytest.mark.api
def test_clone_nonexistent_system_config_returns_404() -> None:
    """POST clone 不存在的 system config_id → 404."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(f"{AGENT_URL}/v1/mcp/system/999999/clone")
    assert r.status_code == 404


@pytest.mark.api
def test_clone_system_config_creates_user_copy(
    cleanup_configs: list[int],
) -> None:
    """POST clone 系统 MCP → 200 + is_system=False + enabled=False (用户私有副本)."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        # 列出系统 MCP
        r = client.get(f"{AGENT_URL}/v1/mcp/system")
        system_configs = r.json()
        if not system_configs:
            pytest.skip("无系统 MCP 配置可克隆")

        src = system_configs[0]
        # 先清理可能存在的同名副本 (上次用例残留)
        existing = client.get(f"{AGENT_URL}/v1/mcp").json()
        for c in existing:
            if c.get("name") == src["name"]:
                client.delete(f"{AGENT_URL}/v1/mcp/{c['id']}")

        # 克隆
        r2 = client.post(f"{AGENT_URL}/v1/mcp/system/{src['id']}/clone")
        assert r2.status_code == 200, f"克隆失败: {r2.status_code} {r2.text}"
        cloned = r2.json()
        assert cloned["is_system"] is False
        assert cloned["enabled"] is False
        cleanup_configs.append(cloned["id"])


@pytest.mark.api
def test_clone_system_config_duplicate_returns_409(
    cleanup_configs: list[int],
) -> None:
    """POST clone 同名系统 MCP 二次 → 409 (已存在同名配置)."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        system_configs = client.get(f"{AGENT_URL}/v1/mcp/system").json()
        if not system_configs:
            pytest.skip("无系统 MCP 配置可克隆")

        src = system_configs[0]
        # 先清理可能存在的同名副本
        existing = client.get(f"{AGENT_URL}/v1/mcp").json()
        for c in existing:
            if c.get("name") == src["name"]:
                client.delete(f"{AGENT_URL}/v1/mcp/{c['id']}")

        # 第一次克隆 → 200
        r1 = client.post(f"{AGENT_URL}/v1/mcp/system/{src['id']}/clone")
        assert r1.status_code == 200
        cleanup_configs.append(r1.json()["id"])

        # 第二次克隆同名 → 409
        r2 = client.post(f"{AGENT_URL}/v1/mcp/system/{src['id']}/clone")
        assert r2.status_code == 409


# ========== POST /v1/mcp/test: 测试配置可用性 ==========


@pytest.mark.api
def test_mcp_test_endpoint_unreachable() -> None:
    """POST /v1/mcp/test 不可达 → success=False + error_type 非 None."""
    payload = _create_config_payload(
        server_url="http://127.0.0.1:1/mcp",
        enabled=True,
    )
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(f"{AGENT_URL}/v1/mcp/test", json=payload)

    assert r.status_code == 200
    result = r.json()
    assert result["success"] is False
    assert result["tools_count"] == 0
    assert "error_type" in result
    assert result["error_type"] is not None


@pytest.mark.api
def test_mcp_test_endpoint_latency_recorded() -> None:
    """POST /v1/mcp/test → latency_ms 为正整数."""
    payload = _create_config_payload(
        server_url="http://127.0.0.1:1/mcp",
        enabled=True,
    )
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(f"{AGENT_URL}/v1/mcp/test", json=payload)

    result = r.json()
    assert "latency_ms" in result
    assert isinstance(result["latency_ms"], int)
    assert result["latency_ms"] >= 0


# ========== POST /v1/mcp/{id}/test: 测试已保存配置 ==========


@pytest.mark.api
def test_mcp_test_by_id_nonexistent_returns_404() -> None:
    """POST /v1/mcp/999999/test (不存在) → 404."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(f"{AGENT_URL}/v1/mcp/999999/test")
    assert r.status_code == 404


@pytest.mark.api
def test_mcp_test_by_id_existing_config(
    cleanup_configs: list[int],
) -> None:
    """POST /v1/mcp/{id}/test 已存在配置 → 200 + test_result."""
    payload = _create_config_payload(
        server_url="http://127.0.0.1:1/mcp",
        enabled=False,
    )
    with httpx.Client(timeout=API_TIMEOUT) as client:
        created = client.post(f"{AGENT_URL}/v1/mcp", json=payload).json()
        cleanup_configs.append(created["id"])

        r = client.post(f"{AGENT_URL}/v1/mcp/{created['id']}/test")

    assert r.status_code == 200
    result = r.json()
    assert "success" in result
    # 不可达 → success=False
    assert result["success"] is False


# ========== 数据隔离 (agent_id + user_id) ==========


@pytest.mark.api
def test_mcp_config_isolation_agent_id(
    cleanup_configs: list[int],
) -> None:
    """数据隔离: agent_id=agentinsight-researcher 自动注入, 配置归属于本 Agent.

    SELF_HOST 模式下所有请求使用同一 IP-based UserId, 本用例验证:
    - 创建的配置可被同一用户列出 (一致性)
    - 系统 MCP 与用户私有 MCP 分开列出 (is_system 隔离)
    """
    name = _unique_config_name()
    payload = _create_config_payload(name=name, enabled=False)
    with httpx.Client(timeout=API_TIMEOUT) as client:
        created = client.post(f"{AGENT_URL}/v1/mcp", json=payload).json()
        cleanup_configs.append(created["id"])

        # 用户私有列表
        user_configs = client.get(f"{AGENT_URL}/v1/mcp").json()
        # 系统 MCP 列表
        system_configs = client.get(f"{AGENT_URL}/v1/mcp/system").json()

        # 用户私有列表含刚创建, 系统 MCP 列表不含
        user_names = {c["name"] for c in user_configs}
        system_names = {c["name"] for c in system_configs}
        assert name in user_names
        assert name not in system_names


@pytest.mark.api
def test_mcp_config_isolation_user_id_consistency(
    cleanup_configs: list[int],
) -> None:
    """数据隔离: 同一 user_id 多次请求结果一致.

    SELF_HOST 模式无 JWT → IP-based UserId, 所有请求视为同一用户.
    本用例验证: 创建后, 多次列出都稳定返回该配置 (无随机性).
    """
    name = _unique_config_name()
    payload = _create_config_payload(name=name, enabled=False)
    with httpx.Client(timeout=API_TIMEOUT) as client:
        created = client.post(f"{AGENT_URL}/v1/mcp", json=payload).json()
        cleanup_configs.append(created["id"])

        # 三次列出, 应都含该配置
        for _ in range(3):
            configs = client.get(f"{AGENT_URL}/v1/mcp").json()
            assert any(c["name"] == name for c in configs)
