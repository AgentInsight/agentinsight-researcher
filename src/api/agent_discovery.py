"""Agent Discovery Protocol 端点.

提供 GET /.well-known/agent-discovery.json 公开发现端点,
声明 Agent 元信息、服务清单、能力列表与鉴权方式.

安全合规 (公开发现端点, 无需鉴权).
auth 含 bearer_jwt (可选) 与 none (匿名降级 IP-based UserId).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.config.settings import get_settings

router = APIRouter(tags=["agent-discovery"])

_AGENT_VERSION = "1.0.0"


@router.get("/.well-known/agent-discovery.json")
async def agent_discovery() -> JSONResponse:
    """Agent Discovery Protocol 公开发现端点.

    无需鉴权, 返回 Agent 元信息供客户端自动发现与对接.
    """
    settings = get_settings()
    discovery: dict[str, Any] = {
        "name": settings.agent_name,
        "version": _AGENT_VERSION,
        "description": "中文优先的研究分析智能体",
        "services": [
            {
                "name": "research",
                "path": "/v1/chat/completions",
                "method": "POST",
                "description": "OpenAI 兼容研究端点 (流式 SSE + 非流式)",
            },
            {
                "name": "files",
                "path": "/v1/files",
                "method": "POST",
                "description": "文件上传端点 (作为研究数据源)",
            },
            {
                "name": "health",
                "path": "/health",
                "method": "GET",
                "description": "健康检查端点",
            },
            {
                "name": "feedback",
                "path": "/v1/feedback",
                "method": "POST",
                "description": "用户反馈端点",
            },
            {
                "name": "websocket",
                "path": "/v1/ws/{session_id}",
                "method": "WS",
                "description": "WebSocket 流式会话端点",
            },
        ],
        "capabilities": [
            "deep_research",
            "multi_agent",
            "hybrid_retrieval",
            "mcp_tools",
            "human_in_loop",
            "fact_check",
            "image_generation",
        ],
        "auth": ["bearer_jwt", "none"],
    }
    return JSONResponse(status_code=200, content=discovery)
