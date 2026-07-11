"""MCP Server 封装.

MCP 工具配置存储在 PostgreSQL mcp_configs 表 (按 agent_id + user_id 隔离),
运行时由 src/skills/researcher/mcp_coordinator.py 加载; 多 Agent 落地后再引入 tools/registry.py 集中授权.
"""
