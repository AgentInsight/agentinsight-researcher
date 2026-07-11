"""Postgres Checkpointer 配置.

硬约束:
- StateGraph 必须挂 PostgresSaver (PostgreSQL ≥16)
- thread_id 从请求上下文注入做会话隔离键, 禁止客户端自造

分支优化方案 P-Checkpointer: 工厂统一 PostgresSaver.
- 移除 dev/prod 分支与 MemorySaver 降级, 全部使用 PostgresSaver
  (开发环境也需 postgres, 强制依赖一致性, 避免 dev/prod 行为漂移).
- 连接失败时抛出异常 (fail fast), 由调用方决定是否阻断启动.

连接池复用.
- 模块级单例 _checkpointer_instance 避免每次调用创建新连接.
- AsyncPostgresSaver + AsyncConnectionPool 复用同一 asyncpg 连接池,
  池 min/max 从 settings.postgres_pool_min_size/postgres_pool_max_size 读取.
- 双重检查锁 (_pool_lock) 保证并发安全.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# 模块级单例, 避免重复创建连接/连接池.
_checkpointer_instance: Any = None
_pool_lock = asyncio.Lock()


async def get_checkpointer(settings: Settings | None = None) -> Any:
    """获取 LangGraph Checkpointer 单例 (连接池复用).

    分支优化方案 P-Checkpointer: 统一 PostgresSaver.
    - 移除 dev/prod 分支, 所有环境均使用 PostgresSaver
      (开发环境也需 postgres, 保证 dev/prod 行为一致).
    - 连接池创建/setup 失败时抛出 RuntimeError (fail fast),
      由调用方决定是否阻断启动 (server.py lifespan 捕获后告警但不阻断).

    双重检查锁保证并发场景下只创建一个实例; 首次调用 settings 生效,
    后续调用忽略 settings 参数直接返回已建单例.

    Returns:
        已 setup() 的 AsyncPostgresSaver 实例.

    Raises:
        RuntimeError: PostgresSaver 初始化失败 (连接池创建/setup 异常).
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

        _checkpointer_instance = await _create_postgres_checkpointer(settings)
        return _checkpointer_instance


async def _create_postgres_checkpointer(settings: Settings) -> Any:
    """创建 AsyncPostgresSaver (连接池复用).

    用 AsyncConnectionPool 复用连接, 池 min/max 从
    settings.postgres_pool_min_size/postgres_pool_max_size 读取
    (配置化, 支持按负载调整).

    分支优化方案 P-Checkpointer: 移除 MemorySaver 降级.
    连接池创建/setup 失败时记录 ERROR 并抛出 RuntimeError (fail fast),
    不再回退 MemorySaver (避免 dev/prod 行为漂移, 强制 postgres 可用).

    Args:
        settings: 全局配置.

    Returns:
        AsyncPostgresSaver 实例 (已 setup).

    Raises:
        RuntimeError: 连接池创建/setup 失败 (包装原始异常).
    """
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        # 连接池 min/max 从 settings 读取, 支持按负载调整
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

        checkpointer = AsyncPostgresSaver(conn=pool)
        await checkpointer.setup()

        logger.info(
            "PostgresSaver 已初始化 (连接池复用, min=%d max=%d)",
            min_size,
            max_size,
        )
        return checkpointer
    except Exception as exc:  # noqa: BLE001
        # 分支优化: 不再降级 MemorySaver, 抛出异常由调用方处理
        logger.error(
            "PostgresSaver 初始化失败 (不降级 MemorySaver, fail fast): %s",
            exc,
            exc_info=True,
        )
        raise RuntimeError(f"PostgresSaver 初始化失败: {exc}") from exc


async def close_checkpointer_pool() -> None:
    """关闭 Checkpointer 的 psycopg 连接池 (应用 shutdown 时调用).

    幂等: 无实例时直接返回.
    """
    global _checkpointer_instance
    if _checkpointer_instance is not None:
        try:
            # AsyncPostgresSaver 内部持有 AsyncConnectionPool
            # 通过 conn 属性获取 pool 并关闭
            pool = getattr(_checkpointer_instance, "conn", None)
            if pool is not None and hasattr(pool, "close"):
                await pool.close()
            logger.info("Checkpointer psycopg 连接池已关闭")
        except Exception as e:  # noqa: BLE001
            logger.warning("关闭 Checkpointer 连接池失败: %s", e)
        finally:
            _checkpointer_instance = None
