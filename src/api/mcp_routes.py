"""MCP 配置管理 API.

前端 MCP 配置功能 + Postgres 持久化.
Agent 通过 postgres 获取对应的 MCP 配置.

AGENTS.md 第 7/9 章:
- 数据隔离键 agent_id = agent_name, 用户私有数据按 user_id 区分
- 工具 (MCP) 配置集中在 tools/registry.py, 此处仅提供 CRUD 持久化
- 所有持久化层 (Postgres) 以 agent_id 区分各 Agent, 用户私有数据按 user_id 区分

MCP 传输模式:
- stdio (本地模式): 通过 stdin/stdout 与本地进程通信, command 必填, server_url 可选
- sse (远程模式): 通过 SSE 连接远程 HTTP 服务器, server_url 必填, command 不需要
- streamable_http (远程模式): 通过 HTTP 流连接远程服务器, server_url 必填, command 不需要

MCP 可用性验证 (用户需求):
1. 新增 MCP 时测试可用性, 不可用则 enabled=FALSE, 不阻止添加
2. 需 Key 的系统 MCP 克隆时 enabled=FALSE, 由前端引导用户填 Key 后测试启用
3. 启用 MCP 时强制测试, 只有可用的服务才能启用
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from src.api.middleware import get_request_agent_id, get_request_user_id
from src.memory.db_initializer import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/mcp", tags=["mcp"])

# MCP 可用性测试超时 (秒)
_MCP_TEST_TIMEOUT = 30


class MCPConfig(BaseModel):
    """MCP 配置模型.

    传输模式说明:
    - stdio (本地模式): command 必填, server_url 可选 (本地进程通信)
    - sse / streamable_http (远程模式): server_url 必填, command 不需要
    """

    name: str = Field(..., description="配置名称")
    server_url: str | None = Field(None, description="MCP Server URL (stdio 模式可空)")
    transport_type: Literal["stdio", "sse", "streamable_http"] = Field(
        "stdio", description="传输类型: stdio=本地模式 / sse / streamable_http=远程模式"
    )
    command: str | None = Field(None, description="启动命令 (stdio 模式必填)")
    args: list[str] | None = Field(None, description="命令参数 (stdio 模式)")
    env_vars: dict[str, str] | None = Field(None, description="环境变量 (stdio 模式)")
    enabled: bool = Field(True, description="是否启用")
    description: str | None = Field(None, description="描述")

    @model_validator(mode="after")
    def validate_transport_fields(self) -> MCPConfig:
        """根据传输类型校验必填字段.

        - stdio (本地模式): command 必填, server_url 可选
        - sse / streamable_http (远程模式): server_url 必填, command 不需要
        """
        if self.transport_type == "stdio":
            if not self.command:
                raise ValueError("stdio 传输模式 (本地模式) 必须提供 command (启动命令)")
        else:
            # sse / streamable_http 远程模式
            if not self.server_url:
                raise ValueError(f"{self.transport_type} 传输模式 (远程模式) 必须提供 server_url")
        return self


class MCPConfigResponse(MCPConfig):
    """MCP 配置响应 (含 id)."""

    id: int
    is_system: bool = False


# SELECT 列 (含 is_system, 所有查询统一使用)
_SELECT_COLUMNS = (
    "id, name, server_url, transport_type, command, args, env_vars, "
    "enabled, is_system, description, created_at, updated_at"
)


# ============================================================================
# MCP 可用性测试 (复用 langchain-mcp-adapters MultiServerMCPClient)
# ============================================================================


async def _test_mcp_config(config: dict[str, Any]) -> dict[str, Any]:
    """测试 MCP 配置可用性.

    复用 mcp_coordinator 的 MultiServerMCPClient 连接逻辑.
    只要 get_tools() 成功返回即判定可用 (即使工具列表为空).

    Args:
        config: MCP 配置 dict, 含 name/transport_type/server_url/command/args/env_vars

    Returns:
        {
            "success": bool,
            "message": str,            # 成功/失败原因 (中文, 可直接展示给用户)
            "error_type": str | None,  # 错误类型: package_not_found/connection_refused/
                                        # timeout/handshake_failed/command_not_found/
                                        # placeholder_env/missing_command/missing_url/
                                        # dependency_missing/unknown (成功时为 None)
            "tools_count": int,        # 发现的工具数
            "tools": list[str],        # 工具名列表 (前 10 个)
            "latency_ms": int,         # 测试耗时 (毫秒)
        }
    """
    name = config.get("name", "default")
    transport_type = config.get("transport_type", "stdio")
    start = time.time()

    # 检测 env_vars 占位符 (系统 MCP 含 <your-token> 等占位符时直接返回)
    env_vars_raw = config.get("env_vars")
    if isinstance(env_vars_raw, str):
        try:
            env_vars_parsed = json.loads(env_vars_raw) if env_vars_raw else {}
        except (json.JSONDecodeError, TypeError):
            env_vars_parsed = {}
    elif isinstance(env_vars_raw, dict):
        env_vars_parsed = env_vars_raw
    else:
        env_vars_parsed = {}

    placeholder_pattern = "<"
    for k, v in env_vars_parsed.items():
        if isinstance(v, str) and placeholder_pattern in v and ">" in v:
            return {
                "success": False,
                "message": f"环境变量 {k} 含占位符 {v}, 请先克隆并填写真实值",
                "error_type": "placeholder_env",
                "tools_count": 0,
                "tools": [],
                "latency_ms": int((time.time() - start) * 1000),
            }

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError:
        return {
            "success": False,
            "message": "langchain-mcp-adapters 未安装, 无法测试",
            "error_type": "dependency_missing",
            "tools_count": 0,
            "tools": [],
            "latency_ms": int((time.time() - start) * 1000),
        }

    # 构建 server_configs (复用 mcp_coordinator 转换逻辑)
    server_configs: dict[str, dict[str, Any]] = {}
    url = config.get("server_url") or config.get("url") or ""

    if transport_type == "stdio":
        command = config.get("command")
        if not command:
            return {
                "success": False,
                "message": "stdio 模式缺少 command",
                "error_type": "missing_command",
                "tools_count": 0,
                "tools": [],
                "latency_ms": int((time.time() - start) * 1000),
            }
        # args/env_vars 可能是 JSONB 字符串或已解析的 list/dict
        args = config.get("args")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                args = None
        env_vars = config.get("env_vars")
        if isinstance(env_vars, str):
            try:
                env_vars = json.loads(env_vars)
            except (json.JSONDecodeError, TypeError):
                env_vars = None
        server_configs[name] = {
            "command": command,
            "args": args or [],
            "env": env_vars or {},
            "transport": "stdio",
        }
    else:
        # sse / streamable_http 远程模式
        if not url:
            return {
                "success": False,
                "message": f"{transport_type} 模式缺少 server_url",
                "error_type": "missing_url",
                "tools_count": 0,
                "tools": [],
                "latency_ms": int((time.time() - start) * 1000),
            }
        server_configs[name] = {
            "url": url,
            "transport": transport_type,
        }

    # 测试连接 + 列工具 (30s 超时)
    try:
        client = MultiServerMCPClient(server_configs)
        try:
            tools = await asyncio.wait_for(client.get_tools(), timeout=_MCP_TEST_TIMEOUT)
        except TimeoutError:
            # 超时后清理子进程
            try:
                # MultiServerMCPClient 可能有 close/aclose 方法
                close_fn = getattr(client, "aclose", None) or getattr(client, "close", None)
                if close_fn:
                    result = close_fn()
                    if hasattr(result, "__await__"):
                        await result
            except Exception as cleanup_err:  # noqa: BLE001
                logger.debug("MCP client cleanup after timeout failed: %s", cleanup_err)
            return {
                "success": False,
                "message": f"测试超时 ({_MCP_TEST_TIMEOUT}s), MCP 服务未响应",
                "error_type": "timeout",
                "tools_count": 0,
                "tools": [],
                "latency_ms": int((time.time() - start) * 1000),
            }
        tool_names = [getattr(t, "name", "") for t in tools[:10]]
        latency_ms = int((time.time() - start) * 1000)
        return {
            "success": True,
            "message": f"连接成功, 发现 {len(tools)} 个工具",
            "error_type": None,
            "tools_count": len(tools),
            "tools": tool_names,
            "latency_ms": latency_ms,
        }
    except FileNotFoundError:
        # npx/uvx 命令不存在 (容器内未安装 Node.js 等)
        cmd = config.get("command", "")
        hint = ""
        if cmd in ("npx", "npx.cmd"):
            hint = " (容器未安装 Node.js, npx 类 MCP 不可用)"
        elif cmd in ("uvx",):
            hint = " (容器未安装 uvx, 请改用其他启动方式)"
        logger.warning("MCP 测试失败 (name=%s): 启动命令不存在 %s", name, cmd)
        return {
            "success": False,
            "message": f"启动命令不存在: {cmd}{hint}",
            "error_type": "command_not_found",
            "tools_count": 0,
            "tools": [],
            "latency_ms": int((time.time() - start) * 1000),
        }
    except Exception as e:  # noqa: BLE001
        err_msg = str(e)
        err_type = type(e).__name__
        # 完整错误写入日志
        logger.warning("MCP 测试失败 (name=%s, type=%s): %s", name, err_type, err_msg)
        # 错误类型识别
        error_type = "unknown"
        err_lower = err_msg.lower()
        if "e404" in err_lower or "not found" in err_lower or "404" in err_lower:
            error_type = "package_not_found"
        elif "econnrefused" in err_lower or "connection refused" in err_lower:
            error_type = "connection_refused"
        elif "etimedout" in err_lower or "timeout" in err_lower or "timed out" in err_lower:
            error_type = "timeout"
        elif "handshake" in err_lower or "protocol" in err_lower:
            error_type = "handshake_failed"
        # 返回给前端的 message 截断为 500 字符
        display_msg = err_msg[:500] + "..." if len(err_msg) > 500 else err_msg
        return {
            "success": False,
            "message": f"连接失败: {display_msg}",
            "error_type": error_type,
            "tools_count": 0,
            "tools": [],
            "latency_ms": int((time.time() - start) * 1000),
        }


# ============================================================================
# 测试端点
# ============================================================================


@router.post("/test")
async def test_mcp_config(config: MCPConfig) -> dict[str, Any]:
    """测试 MCP 配置可用性 (不保存到数据库).

    用于前端在保存前预先测试配置是否可用.

    Args:
        config: MCP 配置 (request body, 不入库)

    Returns:
        测试结果 {success, message, tools_count, tools, latency_ms}
    """
    return await _test_mcp_config(config.model_dump())


@router.post("/{config_id}/test")
async def test_mcp_config_by_id(config_id: int) -> dict[str, Any]:
    """测试已保存的 MCP 配置可用性 (按 ID 查询后测试).

    Args:
        config_id: MCP 配置 ID

    Returns:
        测试结果 {success, message, tools_count, tools, latency_ms}
    """
    # user_id 仅用于请求上下文 (不参与系统 MCP 查询)
    _user_id = get_request_user_id() or "anonymous"
    agent_id = get_request_agent_id() or "agentinsight-researcher"

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_SELECT_COLUMNS} FROM mcp_configs "
            "WHERE id = $1 AND agent_id = $2 AND is_system = FALSE",
            config_id,
            agent_id,
        )
        if not row:
            # 系统 MCP 也可测试 (用户可对系统 MCP 进行可用性验证)
            row = await conn.fetchrow(
                f"SELECT {_SELECT_COLUMNS} FROM mcp_configs "
                "WHERE id = $1 AND agent_id = $2 AND is_system = TRUE",
                config_id,
                agent_id,
            )
            if not row:
                raise HTTPException(status_code=404, detail="MCP 配置不存在")

    config_dict = dict(row)
    # 系统 MCP 可能含 <your-token> 占位符, 测试会失败但应给出明确提示
    return await _test_mcp_config(config_dict)


# ============================================================================
# 系统 MCP 端点
# ============================================================================


@router.get("/system")
async def list_system_mcp_configs() -> list[dict[str, Any]]:
    """列出所有系统公用 MCP 配置 (用户可查看但不可编辑/删除).

    系统 MCP 来源: https://github.com/modelcontextprotocol/servers 官方参考实现.
    用户可通过 POST /v1/mcp/system/{config_id}/clone 克隆到自己的列表.
    """
    agent_id = get_request_agent_id() or "agentinsight-researcher"

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_SELECT_COLUMNS} FROM mcp_configs "
            "WHERE agent_id = $1 AND is_system = TRUE ORDER BY name",
            agent_id,
        )
    return [dict(row) for row in rows]


@router.post("/system/{config_id}/clone")
async def clone_system_mcp_config(config_id: int) -> dict[str, Any]:
    """克隆系统公用 MCP 到当前用户的列表 (用户可编辑克隆后的副本).

    克隆后 enabled=FALSE, 由前端决定后续:
    - 需 Key 的 MCP: 打开编辑表单让用户填 Key, 保存时测试, 通过才启用
    - 无需 Key 的 MCP: 前端调用 /v1/mcp/{id}/test, 通过则 PUT enabled=TRUE

    Args:
        config_id: 系统 MCP 配置 ID (is_system=TRUE)

    Returns:
        克隆后的用户私有配置 (is_system=FALSE, enabled=FALSE)
    """
    user_id = get_request_user_id() or "anonymous"
    agent_id = get_request_agent_id() or "agentinsight-researcher"

    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1. 查询系统 MCP 配置
        src = await conn.fetchrow(
            f"SELECT {_SELECT_COLUMNS} FROM mcp_configs "
            "WHERE id = $1 AND agent_id = $2 AND is_system = TRUE",
            config_id,
            agent_id,
        )
        if not src:
            raise HTTPException(status_code=404, detail="系统 MCP 配置不存在")

        # 2. 检查用户是否已克隆同名配置 (避免重复克隆)
        existing = await conn.fetchval(
            "SELECT id FROM mcp_configs WHERE agent_id = $1 AND user_id = $2 AND name = $3 AND is_system = FALSE",
            agent_id,
            user_id,
            src["name"],
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"已存在同名配置 '{src['name']}', 请先删除或重命名已有配置",
            )

        # 3. 克隆到用户私有列表 (is_system=FALSE, enabled=FALSE)
        #    需 Key 的 MCP 必须由用户填入真实 Key 后测试通过才能启用
        #    无需 Key 的 MCP 由前端测试后决定是否启用
        row = await conn.fetchrow(
            """INSERT INTO mcp_configs
            (agent_id, user_id, name, server_url, transport_type, command, args, env_vars, enabled, is_system, description)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, FALSE, FALSE, $9)
            RETURNING """
            + _SELECT_COLUMNS,
            agent_id,
            user_id,
            src["name"],
            src["server_url"],
            src["transport_type"],
            src["command"],
            src["args"],
            src["env_vars"],
            src["description"],
        )
    return dict(row)


# ============================================================================
# 用户 MCP 端点
# ============================================================================


@router.get("")
async def list_mcp_configs() -> list[dict[str, Any]]:
    """列出当前用户的所有 MCP 配置 (不含系统公用 MCP)."""
    user_id = get_request_user_id() or "anonymous"
    agent_id = get_request_agent_id() or "agentinsight-researcher"

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_SELECT_COLUMNS} FROM mcp_configs "
            "WHERE agent_id = $1 AND user_id = $2 AND is_system = FALSE ORDER BY created_at DESC",
            agent_id,
            user_id,
        )
    return [dict(row) for row in rows]


@router.post("")
async def create_mcp_config(config: MCPConfig) -> dict[str, Any]:
    """创建 MCP 配置 (用户私有, is_system=FALSE).

    用户需求 1: 新增 MCP 服务时测试是否可用, 不可用则将状态设置为不启用, 不阻止用户添加.

    流程:
    1. INSERT 配置 (按 body.enabled 保存)
    2. 异步测试可用性
    3. 若测试失败且 enabled=TRUE → UPDATE 设为 FALSE
    4. 返回 {config, test_result}
    """
    user_id = get_request_user_id() or "anonymous"
    agent_id = get_request_agent_id() or "agentinsight-researcher"

    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1. INSERT 配置
        row = await conn.fetchrow(
            """INSERT INTO mcp_configs
            (agent_id, user_id, name, server_url, transport_type, command, args, env_vars, enabled, is_system, description)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, FALSE, $10)
            RETURNING """
            + _SELECT_COLUMNS,
            agent_id,
            user_id,
            config.name,
            config.server_url,
            config.transport_type,
            config.command,
            json.dumps(config.args) if config.args else None,
            json.dumps(config.env_vars) if config.env_vars else None,
            config.enabled,
            config.description,
        )
        saved = dict(row)

    # 2. 测试可用性 (不在数据库事务内, 避免长连接)
    test_result = await _test_mcp_config(saved)

    # 3. 若测试失败且 enabled=TRUE → UPDATE 设为 FALSE
    if not test_result["success"] and saved["enabled"]:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE mcp_configs SET enabled = FALSE WHERE id = $1",
                saved["id"],
            )
            saved["enabled"] = False

    saved["test_result"] = test_result
    return saved


@router.put("/{config_id}")
async def update_mcp_config(
    config_id: int,
    config: MCPConfig,
    skip_test: bool = Query(False, description="跳过可用性测试 (前端已测试时使用)"),
) -> dict[str, Any]:
    """更新 MCP 配置 (仅用户私有配置, 系统 MCP 不可编辑).

    用户需求 3: 启用 MCP 服务时需验证是否可用, 只有可用的服务才能启用.

    流程:
    - 若 body.enabled=TRUE 且当前 DB 中 enabled=FALSE (从禁用切到启用):
      先测试配置, 失败则拒绝启用 (返回 400 + test_result), 通过则正常 UPDATE
      (除非 skip_test=True, 此时跳过测试, 由前端自行测试后调用)
    - 其他情况 (enabled=FALSE 或 enabled 不变) → 直接 UPDATE
    """
    user_id = get_request_user_id() or "anonymous"
    agent_id = get_request_agent_id() or "agentinsight-researcher"

    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1. 查询当前配置 (校验所有权 + 获取当前 enabled 状态)
        current = await conn.fetchrow(
            f"SELECT {_SELECT_COLUMNS} FROM mcp_configs "
            "WHERE id = $1 AND agent_id = $2 AND user_id = $3 AND is_system = FALSE",
            config_id,
            agent_id,
            user_id,
        )
        if not current:
            raise HTTPException(status_code=404, detail="MCP 配置不存在或为系统配置 (不可编辑)")

        current_enabled = current["enabled"]

        # 2. 若要从禁用切到启用, 先测试可用性 (除非前端已测试 skip_test=True)
        if config.enabled and not current_enabled and not skip_test:
            test_result = await _test_mcp_config(config.model_dump())
            if not test_result["success"]:
                # 拒绝启用, 保持 enabled=FALSE
                # 仍更新其他字段 (name/url/command/args/env_vars/description), 但 enabled 强制为 FALSE
                row = await conn.fetchrow(
                    f"""UPDATE mcp_configs
                    SET name=$4, server_url=$5, transport_type=$6, command=$7, args=$8, env_vars=$9,
                        enabled=FALSE, description=$10
                    WHERE id=$1 AND agent_id=$2 AND user_id=$3 AND is_system=FALSE
                    RETURNING {_SELECT_COLUMNS}""",
                    config_id,
                    agent_id,
                    user_id,
                    config.name,
                    config.server_url,
                    config.transport_type,
                    config.command,
                    json.dumps(config.args) if config.args else None,
                    json.dumps(config.env_vars) if config.env_vars else None,
                    config.description,
                )
                saved = dict(row)
                saved["test_result"] = test_result
                # 返回 200 但 enabled=FALSE + test_result (前端据 test_result 展示失败原因)
                # 不用 400, 因为 PUT 已成功更新其他字段, 只是 enabled 拒绝切换
                return saved

        # 3. 正常 UPDATE (enabled=FALSE 或 enabled 不变 或 enabled=TRUE 且测试通过)
        row = await conn.fetchrow(
            f"""UPDATE mcp_configs
            SET name=$4, server_url=$5, transport_type=$6, command=$7, args=$8, env_vars=$9, enabled=$10, description=$11
            WHERE id=$1 AND agent_id=$2 AND user_id=$3 AND is_system=FALSE
            RETURNING {_SELECT_COLUMNS}""",
            config_id,
            agent_id,
            user_id,
            config.name,
            config.server_url,
            config.transport_type,
            config.command,
            json.dumps(config.args) if config.args else None,
            json.dumps(config.env_vars) if config.env_vars else None,
            config.enabled,
            config.description,
        )
    return dict(row)


@router.delete("/{config_id}")
async def delete_mcp_config(config_id: int) -> dict[str, Any]:
    """删除 MCP 配置 (仅用户私有配置, 系统 MCP 不可删除)."""
    user_id = get_request_user_id() or "anonymous"
    agent_id = get_request_agent_id() or "agentinsight-researcher"

    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM mcp_configs WHERE id=$1 AND agent_id=$2 AND user_id=$3 AND is_system=FALSE",
            config_id,
            agent_id,
            user_id,
        )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="MCP 配置不存在或为系统配置 (不可删除)")
    return {"deleted": True}
