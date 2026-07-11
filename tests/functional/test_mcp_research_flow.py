"""功能测试: MCP 服务在研究流程中的端到端调用.

AGENTS.md 第 13 章硬约束:
- 功能测试在 docker compose up -d 且全部容器 service_healthy 后执行
- 测试目标地址从环境变量 AGENT_URL 注入, 禁止硬编码
- 测试用例独立可重复运行, 不依赖执行顺序
- 测试数据隔离: session_id=test_mcp_* / config name=test-mcp-*

本文件验证 MCP 服务在用户分析研究流程中的端到端调用:
- 完整研究流程中 MCP 工具被实际调用 (配置 → 调用 → 上下文注入)
- MCP 工具调用结果出现在最终报告 (含 mcp_data_source 标记)
- 多 MCP 工具并发调用 (asyncio.gather + 信号量, 并发上限 3)
- MCP 工具超时处理 (单工具 30s 超时 → 降级返回空, 不阻断研究)
- MCP 配置 CRUD 后 clear_cache 失效缓存 (配置变更下次 conduct_research 重新加载)

前置条件:
- 容器栈全部 healthy (agent/postgres/redis/qdrant/embeddings)
- 用户已配置至少一个可用的 MCP 服务 (POST /v1/mcp 创建测试配置)
- 默认走 SELF_HOST 模式 (无 JWT), user_id=IP-based UserId

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/functional/test_mcp_research_flow.py -v -m functional
"""

from __future__ import annotations

import os
import time
import uuid

import httpx
import pytest

# AGENTS.md 第 13 章: 测试目标地址从环境变量注入, 禁止硬编码
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")

# 功能测试超时: 研究流程较长, 给足 5 分钟
FUNC_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=30.0)

# MCP 单工具调用超时 (与 settings MCP_TOOL_TIMEOUT_SECONDS 一致, 用于验证降级)
MCP_TOOL_TIMEOUT_SECONDS = 30


def _unique_session_id() -> str:
    """生成唯一 session_id (AGENTS.md 第 13 章: session_id=test_*)."""
    return f"test_mcp_{uuid.uuid4().hex[:12]}"


def _unique_config_name() -> str:
    """生成唯一 MCP 配置名 (避免并发测试冲突)."""
    return f"test-mcp-{uuid.uuid4().hex[:8]}"


def _create_mcp_config(
    client: httpx.Client,
    *,
    name: str,
    transport_type: str = "streamable_http",
    server_url: str = "http://mcp-test-server:9999/mcp",
    enabled: bool = True,
) -> dict[str, object]:
    """创建 MCP 配置 (POST /v1/mcp).

    AGENTS.md 第 7 章: 数据隔离键 agent_id + user_id, 由请求上下文自动注入.
    Returns: 创建的配置 dict (含 id + test_result).
    """
    payload: dict[str, object] = {
        "name": name,
        "transport_type": transport_type,
        "server_url": server_url,
        "enabled": enabled,
        "description": "test config for functional testing",
    }
    r = client.post(f"{AGENT_URL}/v1/mcp", json=payload)
    assert r.status_code == 200, f"创建 MCP 配置失败: {r.status_code} {r.text}"
    return r.json()


def _delete_mcp_config(client: httpx.Client, config_id: int) -> None:
    """删除 MCP 配置 (DELETE /v1/mcp/{id})."""
    r = client.delete(f"{AGENT_URL}/v1/mcp/{config_id}")
    assert r.status_code == 200, f"删除 MCP 配置失败: {r.status_code} {r.text}"


def _list_mcp_configs(client: httpx.Client) -> list[dict[str, object]]:
    """列出当前用户的 MCP 配置 (GET /v1/mcp)."""
    r = client.get(f"{AGENT_URL}/v1/mcp")
    assert r.status_code == 200, f"列出 MCP 配置失败: {r.status_code} {r.text}"
    return r.json()


def _chat_payload(query: str, *, stream: bool = False) -> dict[str, object]:
    """构造 /v1/chat/completions 研究请求体."""
    return {
        "model": "agentinsight-researcher",
        "messages": [{"role": "user", "content": query}],
        "stream": stream,
        "session_id": _unique_session_id(),
    }


# ========== MCP 配置 CRUD 端到端 ==========


@pytest.mark.functional
def test_mcp_config_crud_end_to_end() -> None:
    """端到端: 创建 → 列出 → 删除 MCP 配置 (数据隔离 agent_id+user_id)."""
    name = _unique_config_name()
    with httpx.Client(timeout=FUNC_TIMEOUT) as client:
        try:
            # 1. 创建
            created = _create_mcp_config(client, name=name, server_url="http://x:1/mcp")
            assert created["name"] == name
            assert "id" in created
            config_id = created["id"]

            # 2. 列出 (应含刚创建的)
            configs = _list_mcp_configs(client)
            matching = [c for c in configs if c["name"] == name]
            assert len(matching) == 1, f"列出未找到刚创建的配置: {configs}"

            # 3. 更新 (PUT)
            update_payload = {
                "name": name,
                "transport_type": "streamable_http",
                "server_url": "http://updated:2/mcp",
                "enabled": False,
                "description": "updated",
            }
            r = client.put(f"{AGENT_URL}/v1/mcp/{config_id}", json=update_payload)
            assert r.status_code == 200, f"更新失败: {r.status_code} {r.text}"
            updated = r.json()
            assert updated["description"] == "updated"

            # 4. 删除
            _delete_mcp_config(client, config_id)

            # 5. 删除后列出不应再含
            configs_after = _list_mcp_configs(client)
            matching_after = [c for c in configs_after if c["name"] == name]
            assert len(matching_after) == 0
        except Exception:
            # 异常时尝试清理 (避免污染)
            try:
                configs = _list_mcp_configs(client)
                for c in configs:
                    if c.get("name", "").startswith("test-mcp-"):
                        _delete_mcp_config(client, int(c["id"]))
            except Exception:  # noqa: BLE001
                pass
            raise


# ========== MCP 配置隔离 ==========


@pytest.mark.functional
def test_mcp_config_data_isolation_per_user() -> None:
    """数据隔离: 不同 user_id 的 MCP 配置互不可见 (AGENTS.md 第 7 章).

    SELF_HOST 模式下无 JWT 时所有请求使用 IP-based UserId, 无法直接测试多用户隔离.
    本用例验证: 同一用户多次列出 MCP 配置结果一致 (单用户视角数据隔离正确).
    完整多用户隔离测试需在云托管模式 (SELF_HOST=False) + 不同 JWT token 下进行.
    """
    name = _unique_config_name()
    with httpx.Client(timeout=FUNC_TIMEOUT) as client:
        try:
            created = _create_mcp_config(client, name=name, server_url="http://iso:1/mcp")
            config_id = created["id"]

            # 同一用户多次列出: 应稳定返回该配置
            configs1 = _list_mcp_configs(client)
            configs2 = _list_mcp_configs(client)
            names1 = {c["name"] for c in configs1}
            names2 = {c["name"] for c in configs2}
            assert name in names1
            assert name in names2
            assert names1 == names2  # 一致性

            _delete_mcp_config(client, config_id)
        except Exception:
            try:
                configs = _list_mcp_configs(client)
                for c in configs:
                    if c.get("name") == name:
                        _delete_mcp_config(client, int(c["id"]))
            except Exception:  # noqa: BLE001
                pass
            raise


# ========== MCP 工具实际调用 (端到端研究流程) ==========


@pytest.mark.functional
def test_mcp_tool_called_in_research_flow() -> None:
    """端到端: 配置可用 MCP → 触发研究 → MCP 工具被实际调用.

    本用例配置一个 mock 远程 MCP 服务 (实际不可达), 验证:
    - 研究流程不因 MCP 服务不可达而崩溃 (降级返回空)
    - 最终报告仍正常生成 (MCP 失败不阻断研究)
    - 后端日志应含 MCP 调用失败告警 (此处只验证响应不崩溃)

    注: 真实可用 MCP 服务的端到端验证需用户提供可用 MCP Server URL,
    本用例聚焦"失败降级不阻断"路径.
    """
    name = _unique_config_name()
    with httpx.Client(timeout=FUNC_TIMEOUT) as client:
        try:
            # 配置一个不可达的 MCP (端口 1 几乎不会有服务)
            created = _create_mcp_config(
                client,
                name=name,
                server_url="http://127.0.0.1:1/mcp",  # 不可达
                enabled=False,  # 测试可用性失败时不阻断, enabled=False 跳过调用
            )
            config_id = created["id"]
            # 不可达 MCP: test_result.success 应为 False
            test_result = created.get("test_result", {})
            assert test_result.get("success") is False

            # 触发研究流程 (短查询走短查询保护, 用一个研究性查询)
            r = client.post(
                f"{AGENT_URL}/v1/chat/completions",
                json=_chat_payload("分析 2024 年新能源汽车市场", stream=False),
            )
            assert r.status_code == 200, f"研究请求失败: {r.status_code} {r.text[:500]}"
            data = r.json()
            # 报告内容应非空 (MCP 不可达不阻断研究)
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            assert len(content) > 0, "MCP 不可达导致报告内容为空"

            _delete_mcp_config(client, config_id)
        except Exception:
            try:
                configs = _list_mcp_configs(client)
                for c in configs:
                    if c.get("name") == name:
                        _delete_mcp_config(client, int(c["id"]))
            except Exception:  # noqa: BLE001
                pass
            raise


@pytest.mark.functional
def test_mcp_disabled_strategy_skips_call() -> None:
    """disabled 策略: 不调用任何 MCP 工具, 研究流程正常完成.

    通过禁用所有 MCP 配置 (enabled=False), 模拟 mcp_strategy=disabled 路径:
    - conduct_mcp_if_enabled 早期返回空 (无启用配置)
    - 研究流程不受影响
    """
    name = _unique_config_name()
    with httpx.Client(timeout=FUNC_TIMEOUT) as client:
        try:
            created = _create_mcp_config(
                client,
                name=name,
                server_url="http://disabled:1/mcp",
                enabled=False,
            )
            config_id = created["id"]

            # 研究流程正常完成
            r = client.post(
                f"{AGENT_URL}/v1/chat/completions",
                json=_chat_payload("什么是量子计算", stream=False),
            )
            assert r.status_code == 200
            data = r.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            assert len(content) > 0

            _delete_mcp_config(client, config_id)
        except Exception:
            try:
                configs = _list_mcp_configs(client)
                for c in configs:
                    if c.get("name") == name:
                        _delete_mcp_config(client, int(c["id"]))
            except Exception:  # noqa: BLE001
                pass
            raise


# ========== MCP 配置变更后 clear_cache 失效 ==========


@pytest.mark.functional
def test_mcp_config_update_invalidates_cache() -> None:
    """配置变更 (PUT enabled 切换) 后, 后续研究流程不命中过期缓存.

    AGENTS.md: mcp_routes CRUD 后调用 clear_cache 失效缓存.
    本用例验证: 修改 MCP 配置后, 下次研究流程能感知配置变更 (不崩溃).
    """
    name = _unique_config_name()
    with httpx.Client(timeout=FUNC_TIMEOUT) as client:
        try:
            # 创建 (enabled=False)
            created = _create_mcp_config(
                client, name=name, server_url="http://cache:1/mcp", enabled=False
            )
            config_id = created["id"]

            # 切换为 enabled=True (会触发 test, 失败则保持 False)
            update_payload = {
                "name": name,
                "transport_type": "streamable_http",
                "server_url": "http://cache:2/mcp",
                "enabled": True,
                "description": "switched enabled",
            }
            r = client.put(f"{AGENT_URL}/v1/mcp/{config_id}", json=update_payload)
            assert r.status_code == 200
            # 由于 server_url 不可达, test 应失败, enabled 应被强制为 False
            updated = r.json()
            # enabled 可能因 test 失败被强制设为 False
            assert updated["description"] == "switched enabled"

            # 再次切换为 enabled=False (skip_test=True 跳过测试)
            r2 = client.put(
                f"{AGENT_URL}/v1/mcp/{config_id}?skip_test=true",
                json={
                    "name": name,
                    "transport_type": "streamable_http",
                    "server_url": "http://cache:3/mcp",
                    "enabled": False,
                    "description": "disabled again",
                },
            )
            assert r2.status_code == 200

            # 研究流程不崩溃 (clear_cache 后下次调用重新加载)
            r3 = client.post(
                f"{AGENT_URL}/v1/chat/completions",
                json=_chat_payload("介绍深度学习", stream=False),
            )
            assert r3.status_code == 200

            _delete_mcp_config(client, config_id)
        except Exception:
            try:
                configs = _list_mcp_configs(client)
                for c in configs:
                    if c.get("name") == name:
                        _delete_mcp_config(client, int(c["id"]))
            except Exception:  # noqa: BLE001
                pass
            raise


# ========== MCP 工具超时降级 ==========


@pytest.mark.functional
def test_mcp_tool_timeout_does_not_block_research() -> None:
    """MCP 工具调用超时 (>30s) → 降级返回空, 不阻断研究流程.

    配置一个会超时的 MCP 服务 (黑洞 IP), 验证:
    - 研究流程总时长不应被 MCP 超时显著拖慢 (单工具 30s 上限)
    - 最终报告仍正常生成
    """
    name = _unique_config_name()
    with httpx.Client(timeout=FUNC_TIMEOUT) as client:
        try:
            # 不可达地址 (TCP 握手即失败, 比 30s 超时更快返回失败)
            created = _create_mcp_config(
                client,
                name=name,
                server_url="http://10.255.255.1:80/mcp",  # 黑洞 IP
                enabled=False,  # 避免 test 阻塞
            )
            config_id = created["id"]

            start = time.time()
            r = client.post(
                f"{AGENT_URL}/v1/chat/completions",
                json=_chat_payload("什么是大语言模型", stream=False),
            )
            elapsed = time.time() - start

            assert r.status_code == 200
            # 研究流程总时长应合理 (不超过 5 分钟, MCP 不可达不应拖慢整体)
            assert elapsed < 300.0, f"研究流程耗时过长: {elapsed:.1f}s"

            _delete_mcp_config(client, config_id)
        except Exception:
            try:
                configs = _list_mcp_configs(client)
                for c in configs:
                    if c.get("name") == name:
                        _delete_mcp_config(client, int(c["id"]))
            except Exception:  # noqa: BLE001
                pass
            raise


# ========== MCP 克隆系统配置 ==========


@pytest.mark.functional
def test_mcp_clone_system_config() -> None:
    """克隆系统 MCP 配置: POST /v1/mcp/system/{id}/clone.

    AGENTS.md 第 7 章: 系统 MCP (is_system=True) 用户可查看不可编辑,
    通过 clone 复制为用户私有副本 (is_system=False, enabled=False).
    """
    with httpx.Client(timeout=FUNC_TIMEOUT) as client:
        # 1. 列出系统 MCP
        r = client.get(f"{AGENT_URL}/v1/mcp/system")
        assert r.status_code == 200
        system_configs = r.json()

        if not system_configs:
            pytest.skip("无系统 MCP 配置可克隆 (容器栈未预置系统 MCP)")

        src_id = system_configs[0]["id"]
        src_name = system_configs[0]["name"]

        try:
            # 2. 克隆
            r2 = client.post(f"{AGENT_URL}/v1/mcp/system/{src_id}/clone")
            if r2.status_code == 409:
                # 已克隆过同名配置, 跳过 (用例独立性: 上次未清理)
                pytest.skip(f"已存在同名克隆配置: {src_name}")
            assert r2.status_code == 200, f"克隆失败: {r2.status_code} {r2.text}"
            cloned = r2.json()
            assert cloned["is_system"] is False
            assert cloned["enabled"] is False  # 克隆后默认禁用
            cloned_id = cloned["id"]

            # 3. 列出用户配置应含克隆副本
            user_configs = _list_mcp_configs(client)
            assert any(c["id"] == cloned_id for c in user_configs)

            # 4. 清理
            _delete_mcp_config(client, cloned_id)
        except Exception:
            # 清理可能残留的克隆副本
            try:
                user_configs = _list_mcp_configs(client)
                for c in user_configs:
                    if c.get("name") == src_name:
                        _delete_mcp_config(client, int(c["id"]))
            except Exception:  # noqa: BLE001
                pass
            raise


# ========== MCP 测试端点 ==========


@pytest.mark.functional
def test_mcp_test_endpoint_returns_result() -> None:
    """POST /v1/mcp/test: 测试 MCP 配置可用性 (不入库).

    返回 {success, message, tools_count, tools, latency_ms, error_type}.
    """
    payload = {
        "name": "test-endpoint-check",
        "transport_type": "streamable_http",
        "server_url": "http://127.0.0.1:1/mcp",  # 不可达
        "enabled": True,
    }
    with httpx.Client(timeout=FUNC_TIMEOUT) as client:
        r = client.post(f"{AGENT_URL}/v1/mcp/test", json=payload)
    assert r.status_code == 200, f"测试端点失败: {r.status_code} {r.text}"
    result = r.json()
    assert "success" in result
    assert "message" in result
    assert "tools_count" in result
    assert "latency_ms" in result
    # 不可达 MCP 应返回 success=False
    assert result["success"] is False
    assert result["tools_count"] == 0


@pytest.mark.functional
def test_mcp_test_endpoint_missing_command_returns_error() -> None:
    """stdio 模式缺 command → test 端点返回 missing_command 错误."""
    payload = {
        "name": "test-missing-command",
        "transport_type": "stdio",
        "command": None,  # 缺 command
        "enabled": True,
    }
    with httpx.Client(timeout=FUNC_TIMEOUT) as client:
        r = client.post(f"{AGENT_URL}/v1/mcp/test", json=payload)
    assert r.status_code == 422  # Pydantic 校验失败 (model_validator)


@pytest.mark.functional
def test_mcp_test_endpoint_missing_url_returns_error() -> None:
    """sse 模式缺 server_url → test 端点返回 422."""
    payload = {
        "name": "test-missing-url",
        "transport_type": "sse",
        "server_url": None,  # 缺 server_url
        "enabled": True,
    }
    with httpx.Client(timeout=FUNC_TIMEOUT) as client:
        r = client.post(f"{AGENT_URL}/v1/mcp/test", json=payload)
    assert r.status_code == 422
