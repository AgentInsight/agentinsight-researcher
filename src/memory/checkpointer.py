"""Postgres Checkpointer 配置.

AGENTS.md 第 3/6 章硬约束:
- 生产 StateGraph 必须挂 PostgresSaver (PostgreSQL ≥16)
- 内存 Checkpoint 仅 ENV=dev 允许
- thread_id 从请求上下文注入做会话隔离键, 禁止客户端自造

对标 AgentInsightService common/memory.py.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


async def get_checkpointer(settings: Settings | None = None) -> Any:
    """获取 LangGraph Checkpointer.

    AGENTS.md 第 6 章:
    - 生产 (ENV=prod): PostgresSaver
    - 开发 (ENV=dev): MemorySaver (允许)

    返回已 setup() 的 Checkpointer 实例.
    """
    settings = settings or get_settings()

    if settings.env == "prod":
        # 生产环境强制 PostgresSaver
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        checkpointer = AsyncPostgresSaver.from_conn_string(settings.postgres_dsn_psycopg)
        await checkpointer.setup()  # type: ignore[attr-defined]  # langgraph-checkpoint-postgres from_conn_string 返回上下文管理器, __enter__ 后才有 setup
        logger.info("PostgresSaver 已初始化 (生产环境)")
        return checkpointer
    else:
        # 开发环境允许 MemorySaver
        from langgraph.checkpoint.memory import MemorySaver

        logger.info("MemorySaver 已初始化 (开发环境)")
        return MemorySaver()
