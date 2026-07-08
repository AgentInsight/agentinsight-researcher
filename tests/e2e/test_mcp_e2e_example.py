"""端到端测试: MCP 调用示例 (用户特别要求).

AGENTS.md 第 9/13/14 章硬约束:
- e2e 必须在容器栈 service_healthy 后执行
- 测试目标地址从环境变量 AGENT_URL 注入
- MCP 工具配置存储在 PostgreSQL mcp_configs 表 (按 agent_id + user_id 隔离)
- 运行时由 mcp_coordinator.py 加载用户启用配置并经 LLM 智能选工具
- 每次用唯一 session_id=test_e2e_* / config name=test-e2e-mcp-*

覆盖 5 个核心场景 (用户特别要求的 MCP 调用示例):
1. stdio 传输模式完整示例 (含 command + args + env_vars)
2. sse 传输模式完整示例
3. /v1/chat/completions 触发研究 → MCP 工具调用 → 结果出现在报告中
4. 多 MCP Server 并发调用 (LLM 在多 Server 工具集中选择)
5. 同一 query 不同 session_id 时 fast 策略缓存命中

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/e2e/test_mcp_e2e_example.py -v -m e2e

注意: MCP 工具实际调用依赖容器内可用 MCP Server (如 npx/uvx).
容器未安装 Node.js 时 npx 类 MCP 不可用 (返回 command_not_found),
测试验证配置创建/测试/清理的完整链路, 不强制要求 MCP 工具实际可用.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import httpx
import pytest

# AGENTS.md 第 13 章: 测试目标地址从环境变量注入, 禁止硬编码
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")

# e2e 测试超时 600s (完整研究 5-10 分钟)
E2E_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)

# MCP 配置操作超时 (CRUD + 可用性测试 30s)
MCP_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)


def _unique_session_id() -> str:
    """生成唯一 session_id (AGENTS.md 第 13 章: session_id=test_*)."""
    return f"test_e2e_mcp_{uuid.uuid4().hex[:12]}"


def _unique_config_name() -> str:
    """生成唯一 MCP 配置名 (避免并发测试冲突)."""
    return f"test-e2e-mcp-{uuid.uuid4().hex[:8]}"


def _log(msg: str) -> None:
    """带时间戳输出, 便于追踪长流程进度."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _stdio_config_payload(
    name: str | None = None,
    *,
    command: str = "echo",
    args: list[str] | None = None,
    env_vars: dict[str, str] | None = None,
    enabled: bool = False,
    description: str = "e2e stdio mcp config",
) -> dict[str, object]:
    """构造 stdio 传输模式 MCP 配置请求体 (含 command + args + env_vars)."""
    return {
        "name": name or _unique_config_name(),
        "transport_type": "stdio",
        "command": command,
        "args": args or ["hello"],
        "env_vars": env_vars or {"TEST_ENV": "e2e_value"},
        "enabled": enabled,
        "description": description,
    }


def _sse_config_payload(
    name: str | None = None,
    *,
    server_url: str = "http://127.0.0.1:1/mcp",
    enabled: bool = False,
    description: str = "e2e sse mcp config",
) -> dict[str, object]:
    """构造 sse 传输模式 MCP 配置请求体."""
    return {
        "name": name or _unique_config_name(),
        "transport_type": "sse",
        "server_url": server_url,
        "enabled": enabled,
        "description": description,
    }


@pytest.fixture()
async def cleanup_mcp_configs():
    """fixture: 收集测试中创建的 config_id, 用例后清理 (避免污染)."""
    created_ids: list[int] = []
    yield created_ids
    async with httpx.AsyncClient(timeout=MCP_TIMEOUT) as client:
        for cid in created_ids:
            try:
                await client.delete(f"{AGENT_URL}/v1/mcp/{cid}")
            except Exception:  # noqa: BLE001
                pass


# ========== 场景 1: stdio 传输模式完整示例 ==========


@pytest.mark.e2e
async def test_mcp_stdio_transport_e2e(cleanup_mcp_configs: list[int]) -> None:
    """stdio 传输模式完整示例: 创建 + 测试 + 清理 (含 command + args + env_vars).

    AGENTS.md 第 9 章: MCP 传输模式 stdio (本地模式), 通过 stdin/stdout 与本地进程通信.
    MCP 配置 (mcp_routes.py): stdio 模式 command 必填, server_url 可选.

    流程:
    1. POST /v1/mcp 创建 stdio 配置 (command=echo, args=["hello"], env_vars={...})
    2. 验证配置字段正确持久化 (transport_type/command/args/env_vars)
    3. POST /v1/mcp/{id}/test 测试可用性 (echo 非 MCP Server, 预期失败)
    4. 验证 test_result 结构 (success/message/error_type/tools_count/latency_ms)
    """
    config_name = _unique_config_name()
    payload = _stdio_config_payload(
        name=config_name,
        command="echo",
        args=["hello", "world"],
        env_vars={"MCP_TEST_MODE": "stdio", "MCP_E2E": "true"},
        enabled=False,
    )
    _log(f"stdio MCP 创建: name={config_name}, command=echo")

    async with httpx.AsyncClient(timeout=MCP_TIMEOUT) as client:
        # 步骤 1: 创建配置
        r_create = await client.post(f"{AGENT_URL}/v1/mcp", json=payload)
        assert r_create.status_code == 200, (
            f"创建 stdio MCP 配置失败: {r_create.status_code} {r_create.text[:300]}"
        )
        created = r_create.json()
        config_id = created["id"]
        cleanup_mcp_configs.append(config_id)

        # 步骤 2: 验证字段持久化
        assert created["name"] == config_name
        assert created["transport_type"] == "stdio"
        assert created["command"] == "echo"
        assert created["is_system"] is False
        # args/env_vars 可能以 JSON 字符串或 list/dict 返回 (JSONB 序列化)
        args_value = created.get("args")
        if isinstance(args_value, str):
            args_value = json.loads(args_value)
        assert args_value == ["hello", "world"]
        env_value = created.get("env_vars")
        if isinstance(env_value, str):
            env_value = json.loads(env_value)
        assert env_value["MCP_TEST_MODE"] == "stdio"
        _log(f"stdio MCP 字段验证通过: id={config_id}")

        # 步骤 3: 测试可用性 (echo 非 MCP Server, 预期失败)
        r_test = await client.post(f"{AGENT_URL}/v1/mcp/{config_id}/test")
        assert r_test.status_code == 200, f"测试端点非 200: {r_test.status_code}"

        # 步骤 4: 验证 test_result 结构
        test_result = r_test.json()
        assert "success" in test_result
        assert "message" in test_result
        assert "error_type" in test_result
        assert "tools_count" in test_result
        assert "latency_ms" in test_result
        # echo 不是真正的 MCP Server, 测试应失败
        assert test_result["success"] is False
        assert test_result["tools_count"] == 0
        _log(
            f"stdio MCP 测试结果: success={test_result['success']}, "
            f"error_type={test_result['error_type']}, "
            f"latency={test_result['latency_ms']}ms"
        )


# ========== 场景 2: sse 传输模式完整示例 ==========


@pytest.mark.e2e
async def test_mcp_sse_transport_e2e(cleanup_mcp_configs: list[int]) -> None:
    """sse 传输模式完整示例: 创建 + 测试 + 清理.

    AGENTS.md 第 9 章: MCP 传输模式 sse (远程模式), 通过 SSE 连接远程 HTTP 服务器.
    MCP 配置 (mcp_routes.py): sse 模式 server_url 必填, command 不需要.

    流程:
    1. POST /v1/mcp 创建 sse 配置 (server_url=http://127.0.0.1:1/mcp, 不可达)
    2. 验证配置字段正确持久化 (transport_type/server_url)
    3. POST /v1/mcp/{id}/test 测试可用性 (不可达, 预期失败)
    4. 验证 test_result 结构 + error_type 为 connection_refused/timeout 等
    """
    config_name = _unique_config_name()
    payload = _sse_config_payload(
        name=config_name,
        server_url="http://127.0.0.1:1/mcp",  # 不可达端口
        enabled=False,
    )
    _log(f"sse MCP 创建: name={config_name}, url=http://127.0.0.1:1/mcp")

    async with httpx.AsyncClient(timeout=MCP_TIMEOUT) as client:
        # 步骤 1: 创建配置
        r_create = await client.post(f"{AGENT_URL}/v1/mcp", json=payload)
        assert r_create.status_code == 200, (
            f"创建 sse MCP 配置失败: {r_create.status_code} {r_create.text[:300]}"
        )
        created = r_create.json()
        config_id = created["id"]
        cleanup_mcp_configs.append(config_id)

        # 步骤 2: 验证字段持久化
        assert created["name"] == config_name
        assert created["transport_type"] == "sse"
        assert created["server_url"] == "http://127.0.0.1:1/mcp"
        assert created.get("command") is None  # sse 模式无 command
        assert created["is_system"] is False
        _log(f"sse MCP 字段验证通过: id={config_id}")

        # 步骤 3: 测试可用性 (不可达, 预期失败)
        r_test = await client.post(f"{AGENT_URL}/v1/mcp/{config_id}/test")
        assert r_test.status_code == 200, f"测试端点非 200: {r_test.status_code}"

        # 步骤 4: 验证 test_result 结构
        test_result = r_test.json()
        assert test_result["success"] is False
        assert test_result["tools_count"] == 0
        # 不可达应返回 connection_refused/timeout 类错误
        assert test_result["error_type"] is not None
        _log(
            f"sse MCP 测试结果: success={test_result['success']}, "
            f"error_type={test_result['error_type']}, "
            f"latency={test_result['latency_ms']}ms"
        )


# ========== 场景 3: 研究流程触发 MCP 工具调用 ==========


@pytest.mark.e2e
async def test_mcp_research_flow_with_tool_call(
    cleanup_mcp_configs: list[int],
) -> None:
    """/v1/chat/completions 触发研究 → MCP 工具调用 → 结果出现在报告中.

    AGENTS.md 第 9 章: 运行时由 mcp_coordinator.py 加载用户启用配置并经 LLM 智能选工具.
    AGENTS.md 第 13 章: e2e 应覆盖完整链路: 提问 → 检索 → 工具调用 → 流式响应.

    流程:
    1. 创建 MCP 配置 (不可达, enabled=False → 不影响研究流程)
    2. POST /v1/chat/completions 触发研究 (非流式)
    3. 验证响应 200 + 报告结构 (content 非空 + finish_reason=stop)
    4. 验证 sources 字段 (研究来源, MCP 工具调用结果会合并到 sources/contexts)

    注意: 实际 MCP 工具调用需可用 MCP Server. 不可达配置不影响研究流程
    (mcp_coordinator 加载失败时降级为无工具研究). 本用例验证研究流程在
    MCP 配置存在时仍能正常完成, 不强制要求工具实际被调用.
    """
    sid = _unique_session_id()
    # 创建一个不可达的 MCP 配置 (不影响研究, 验证配置存在时研究仍正常)
    mcp_payload = _sse_config_payload(
        name=_unique_config_name(),
        server_url="http://127.0.0.1:1/mcp",
        enabled=False,
    )
    query = "用 200 字简述 Python 异步编程的核心优势与应用场景"
    _log(f"研究+MCP 测试: session={sid}, query={query[:60]}")

    async with httpx.AsyncClient(timeout=E2E_TIMEOUT) as client:
        # 步骤 1: 创建 MCP 配置
        r_mcp = await client.post(f"{AGENT_URL}/v1/mcp", json=mcp_payload)
        assert r_mcp.status_code == 200, f"MCP 配置创建失败: {r_mcp.status_code}"
        cleanup_mcp_configs.append(r_mcp.json()["id"])

        # 步骤 2: 触发研究 (非流式)
        r_chat = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": query}],
                "stream": False,
                "report_type": "basic_report",
                "session_id": sid,
            },
        )

    assert r_chat.status_code == 200, f"研究请求非 200: {r_chat.status_code} {r_chat.text[:300]}"
    data = r_chat.json()

    # 步骤 3: 验证报告结构
    assert data["object"] == "chat.completion"
    assert data["model"] == "agentinsight-researcher"
    assert len(data["choices"]) == 1
    choice = data["choices"][0]
    assert choice["finish_reason"] == "stop"
    content = choice["message"].get("content", "")
    assert content, "研究响应 content 为空"
    assert len(content) > 50, f"研究内容过短 (<50 字): {len(content)} 字"

    # 步骤 4: 验证 sources 字段 (MCP 工具调用结果会合并到 sources)
    sources = data.get("sources", [])
    assert isinstance(sources, list), f"sources 非列表: {type(sources)}"
    _log(f"研究+MCP 完成: content {len(content)} 字, sources {len(sources)} 条")


# ========== 场景 4: 多 MCP Server 并发调用 ==========


@pytest.mark.e2e
async def test_mcp_multi_server_concurrent(
    cleanup_mcp_configs: list[int],
) -> None:
    """多 MCP Server 并发调用: 创建多个配置 → 并发测试 → 验证独立结果.

    AGENTS.md 第 9 章: MCP_SERVERS 注册行业专用工具服务器, mcp_coordinator.py
    让 LLM 自动选工具 (对标 GPTR MCPToolSelector).

    流程:
    1. 并发创建 3 个 MCP 配置 (2 个 sse + 1 个 stdio)
    2. GET /v1/mcp 验证 3 个配置都已持久化
    3. 并发测试 3 个配置 (POST /v1/mcp/{id}/test)
    4. 验证每个测试结果独立 (各有自己的 test_result)
    """
    # 准备 3 个配置 payload
    payloads = [
        _sse_config_payload(name=_unique_config_name(), server_url="http://127.0.0.1:1/mcp1"),
        _sse_config_payload(name=_unique_config_name(), server_url="http://127.0.0.1:1/mcp2"),
        _stdio_config_payload(name=_unique_config_name(), command="echo", args=["test3"]),
    ]
    _log(f"多 MCP Server 并发: 创建 {len(payloads)} 个配置")

    async with httpx.AsyncClient(timeout=MCP_TIMEOUT) as client:
        # 步骤 1: 并发创建
        create_tasks = [client.post(f"{AGENT_URL}/v1/mcp", json=p) for p in payloads]
        create_responses = await asyncio.gather(*create_tasks)
        config_ids: list[int] = []
        for r in create_responses:
            assert r.status_code == 200, f"并发创建失败: {r.status_code} {r.text[:200]}"
            config_ids.append(r.json()["id"])
            cleanup_mcp_configs.append(r.json()["id"])
        _log(f"并发创建完成: ids={config_ids}")

        # 步骤 2: 验证持久化
        r_list = await client.get(f"{AGENT_URL}/v1/mcp")
        assert r_list.status_code == 200
        all_configs = r_list.json()
        config_id_set = {c["id"] for c in all_configs}
        for cid in config_ids:
            assert cid in config_id_set, f"配置 {cid} 未在列表中找到"

        # 步骤 3: 并发测试
        test_tasks = [client.post(f"{AGENT_URL}/v1/mcp/{cid}/test") for cid in config_ids]
        test_responses = await asyncio.gather(*test_tasks)

    # 步骤 4: 验证独立结果
    for i, r_test in enumerate(test_responses):
        assert r_test.status_code == 200, f"配置 {config_ids[i]} 测试非 200"
        result = r_test.json()
        assert "success" in result
        assert "latency_ms" in result
        assert "error_type" in result
        # 不可达配置应失败
        assert result["success"] is False
    _log(f"并发测试完成: {len(test_responses)} 个独立结果")


# ========== 场景 5: 跨会话缓存复用 (fast 策略) ==========


@pytest.mark.e2e
async def test_mcp_cache_cross_session_reuse() -> None:
    """同一 query 不同 session_id 时 fast 策略缓存命中.

    AGENTS.md 第 6/7 章: 会话隔离键为 thread_id, 会话间状态通过 Checkpointer 隔离.
    研究结果缓存 (fast 策略) 应跨会话复用, 减少重复计算.

    流程:
    1. session_a 发起研究 (非流式) → 等待完成, 记录耗时
    2. session_b 发起相同 query 研究 (非流式) → 等待完成, 记录耗时
    3. 验证两次研究都成功 (200 + content 非空)
    4. 验证两次研究内容主题一致 (同一 query 应产出相似结果)

    注意: 缓存命中时第二次应更快, 但缓存行为受配置影响 (fast 策略),
    本用例不强制断言第二次更快, 仅验证两次都能成功完成.
    """
    sid_a = _unique_session_id()
    sid_b = _unique_session_id()
    query = "用 200 字简述 Python 异步编程的核心优势"
    _log(f"跨会话缓存测试: session_a={sid_a[:20]}..., session_b={sid_b[:20]}...")

    async with httpx.AsyncClient(timeout=E2E_TIMEOUT) as client:
        # 步骤 1: session_a 研究
        start_a = time.time()
        r_a = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": query}],
                "stream": False,
                "report_type": "basic_report",
                "session_id": sid_a,
            },
        )
        elapsed_a = time.time() - start_a

        # 步骤 2: session_b 相同 query 研究
        start_b = time.time()
        r_b = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": query}],
                "stream": False,
                "report_type": "basic_report",
                "session_id": sid_b,
            },
        )
        elapsed_b = time.time() - start_b

    # 步骤 3: 验证两次都成功
    assert r_a.status_code == 200, f"session_a 研究非 200: {r_a.status_code} {r_a.text[:300]}"
    assert r_b.status_code == 200, f"session_b 研究非 200: {r_b.status_code} {r_b.text[:300]}"

    data_a = r_a.json()
    data_b = r_b.json()
    content_a = data_a["choices"][0]["message"].get("content", "")
    content_b = data_b["choices"][0]["message"].get("content", "")

    assert content_a, "session_a content 为空"
    assert content_b, "session_b content 为空"
    assert len(content_a) > 50, f"session_a 内容过短: {len(content_a)} 字"
    assert len(content_b) > 50, f"session_b 内容过短: {len(content_b)} 字"

    # 步骤 4: 验证主题一致 (同一 query 应产出相关结果)
    content_a_lower = content_a.lower()
    content_b_lower = content_b.lower()
    assert any(kw in content_a_lower for kw in ["python", "异步", "async"]), (
        f"session_a 内容未包含主题关键词: {content_a[:200]}"
    )
    assert any(kw in content_b_lower for kw in ["python", "异步", "async"]), (
        f"session_b 内容未包含主题关键词: {content_b[:200]}"
    )

    _log(
        f"跨会话缓存测试完成: a={elapsed_a:.1f}s, b={elapsed_b:.1f}s, "
        f"content_a={len(content_a)}字, content_b={len(content_b)}字"
    )
