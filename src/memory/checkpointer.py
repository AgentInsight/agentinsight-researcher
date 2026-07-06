"""Postgres Checkpointer 配置.

AGENTS.md 第 3/6 章硬约束:
- 生产 StateGraph 必须挂 PostgresSaver (PostgreSQL ≥16)
- 内存 Checkpoint 仅 ENV=dev 允许
- thread_id 从请求上下文注入做会话隔离键, 禁止客户端自造

对标 AgentInsightService common/memory.py.

P0-02: 连接池复用.
- 模块级单例 _checkpointer_instance 避免每次调用创建新连接.
- 生产用 AsyncPostgresSaver + AsyncConnectionPool 复用同一 asyncpg 连接池,
  池 min/max 从 settings.postgres_pool_min_size/postgres_pool_max_size 读取.
- 双重检查锁 (_pool_lock) 保证并发安全.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# P0-02: 模块级单例, 避免重复创建连接/连接池.
_checkpointer_instance: Any = None
_pool_lock = asyncio.Lock()


async def get_checkpointer(settings: Settings | None = None) -> Any:
    """获取 LangGraph Checkpointer 单例 (P0-02 连接池复用).

    AGENTS.md 第 6 章:
    - 生产 (ENV=prod): PostgresSaver (复用 AsyncConnectionPool)
    - 开发 (ENV=dev): MemorySaver (允许)

    双重检查锁保证并发场景下只创建一个实例; 首次调用 settings 生效,
    后续调用忽略 settings 参数直接返回已建单例.

    降级策略: 生产环境连接池创建/setup 失败时, 记录 ERROR 并回退到
    MemorySaver, 保证服务可用 (AGENTS.md 第 6 章 prod 强制 PostgresSaver,
    但服务可用性优先, 降级时显式告警).

    Returns:
        已 setup() 的 Checkpointer 实例.
    """
    global _checkpointer_instance

    # 快路径: 已有单例直接返回 (无锁开销)
    if _checkpointer_instance is not None:
        return _checkpointer_instance

    settings = settings or get_settings()

    async with _pool_lock:
        # 双重检查: 持锁后再次确认 (防止并发重复创建)
        if _checkpointer_instance is not None:
            return _checkpointer_instance

        if settings.env == "prod":
            _checkpointer_instance = await _create_postgres_checkpointer(settings)
        else:
            # 开发环境允许 MemorySaver
            from langgraph.checkpoint.memory import MemorySaver

            _checkpointer_instance = MemorySaver()
            logger.info("MemorySaver 已初始化 (开发环境)")

        return _checkpointer_instance


async def _create_postgres_checkpointer(settings: Settings) -> Any:
    """创建生产 AsyncPostgresSaver (P0-02 连接池复用).

    用 AsyncConnectionPool 复用连接, 池 min/max 从
    settings.postgres_pool_min_size/postgres_pool_max_size 读取
    (P2-6 配置化, 支持按负载调整). 创建/setup 失败时降级到 MemorySaver 并告警.

    Args:
        settings: 全局配置.

    Returns:
        AsyncPostgresSaver 实例 (已 setup); 失败时返回 MemorySaver (降级).
    """
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        # P2-6: 连接池 min/max 从 settings 读取, 支持按负载调整
        # min_size 不超过 max_size, 且均 ≥1, 避免 AsyncConnectionPool ValueError
        max_size = max(int(settings.postgres_pool_max_size), 1)
        min_size = max(min(int(settings.postgres_pool_min_size), max_size), 1)

        # AsyncConnectionPool 配置对齐 from_conn_string:
        # autocommit=True / prepare_threshold=0 / row_factory=dict_row
        pool = AsyncConnectionPool(
            conninfo=settings.postgres_dsn_psycopg,
            min_size=min_size,
            max_size=max_size,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
            open=False,
        )
        await pool.open()

        checkpointer = AsyncPostgresSaver(conn=pool)  # type: ignore[arg-type]  # mypy 无法从 kwargs 推断 row_factory=dict_row 的行类型
        await checkpointer.setup()

        logger.info(
            "PostgresSaver 已初始化 (生产环境, 连接池复用, min=%d max=%d)",
            min_size,
            max_size,
        )
        return checkpointer
    except Exception as exc:  # noqa: BLE001
        # 降级: 连接池创建/setup 失败, 回退 MemorySaver 并告警
        logger.error(
            "PostgresSaver 初始化失败, 降级到 MemorySaver (生产环境降级告警): %s",
            exc,
            exc_info=True,
        )
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
