"""MCP 配置管理 API.

任务7: 前端 MCP 配置功能 + Postgres 持久化.
Agent 通过 postgres 获取对应的 MCP 配置.

AGENTS.md 第 7/9 章:
- 数据隔离键 agent_id = agent_name, 用户私有数据按 user_id 区分
- 工具 (MCP) 配置集中在 tools/registry.py, 此处仅提供 CRUD 持久化
- 所有持久化层 (Postgres) 以 agent_id 区分各 Agent, 用户私有数据按 user_id 区分
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.api.middleware import get_request_agent_id, get_request_user_id
from src.memory.db_initializer import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/mcp", tags=["mcp"])


class MCPConfig(BaseModel):
    """MCP 配置模型."""

    name: str = Field(..., description="配置名称")
    server_url: str = Field(..., description="MCP Server URL")
    transport_type: str = Field("stdio", description="传输类型: stdio/sse/streamable_http")
    command: str | None = Field(None, description="启动命令 (stdio 类型)")
    args: list[str] | None = Field(None, description="命令参数")
    env_vars: dict[str, str] | None = Field(None, description="环境变量")
    enabled: bool = Field(True, description="是否启用")
    description: str | None = Field(None, description="描述")


class MCPConfigResponse(MCPConfig):
    """MCP 配置响应 (含 id)."""

    id: int


@router.get("/configs")
async def list_mcp_configs() -> list[dict[str, Any]]:
    """列出当前用户的所有 MCP 配置."""
    user_id = get_request_user_id() or "anonymous"
    agent_id = get_request_agent_id() or "agentinsight-researcher"

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, server_url, transport_type, command, args, env_vars, enabled, description, created_at, updated_at "
            "FROM mcp_configs WHERE agent_id = $1 AND user_id = $2 ORDER BY created_at DESC",
            agent_id,
            user_id,
        )
    return [dict(row) for row in rows]


@router.post("/configs")
async def create_mcp_config(config: MCPConfig) -> dict[str, Any]:
    """创建 MCP 配置."""
    user_id = get_request_user_id() or "anonymous"
    agent_id = get_request_agent_id() or "agentinsight-researcher"

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO mcp_configs (agent_id, user_id, name, server_url, transport_type, command, args, env_vars, enabled, description)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING id, name, server_url, transport_type, command, args, env_vars, enabled, description, created_at, updated_at""",
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


@router.put("/configs/{config_id}")
async def update_mcp_config(config_id: int, config: MCPConfig) -> dict[str, Any]:
    """更新 MCP 配置."""
    user_id = get_request_user_id() or "anonymous"
    agent_id = get_request_agent_id() or "agentinsight-researcher"

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE mcp_configs
            SET name=$4, server_url=$5, transport_type=$6, command=$7, args=$8, env_vars=$9, enabled=$10, description=$11
            WHERE id=$1 AND agent_id=$2 AND user_id=$3
            RETURNING id, name, server_url, transport_type, command, args, env_vars, enabled, description, created_at, updated_at""",
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
    if not row:
        raise HTTPException(status_code=404, detail="MCP 配置不存在")
    return dict(row)


@router.delete("/configs/{config_id}")
async def delete_mcp_config(config_id: int) -> dict[str, Any]:
    """删除 MCP 配置."""
    user_id = get_request_user_id() or "anonymous"
    agent_id = get_request_agent_id() or "agentinsight-researcher"

    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM mcp_configs WHERE id=$1 AND agent_id=$2 AND user_id=$3",
            config_id,
            agent_id,
            user_id,
        )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="MCP 配置不存在")
    return {"deleted": True}
