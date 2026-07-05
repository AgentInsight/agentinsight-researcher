"""PostgreSQL 数据库初始化器.

AGENTS.md 第 6/7 章硬约束:
- 单一数据库 agents, 业务表含 agent_id+user_id 双列复合索引
- LangGraph Checkpointer 表由官方 SDK 管理

scripts/init.sql 由 Agent 容器启动时读取并执行 (用户需求):
- 所有 DDL 使用 CREATE TABLE/INDEX IF NOT EXISTS, 天然幂等, 支持重复启动
- 表结构变更需追加 ALTER TABLE IF EXISTS ... ADD COLUMN IF NOT EXISTS ... (PostgreSQL 9.6+)
- 失败不阻断启动, 仅告警 (depends_on service_healthy 已保证 Postgres 就绪)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import asyncpg

from src.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# init.sql 路径: 项目根/scripts/init.sql
# Agent 容器内: /app/scripts/init.sql (Dockerfile COPY . . 已包含)
INIT_SQL_PATH = Path(__file__).parent.parent.parent / "scripts" / "init.sql"

# P0-02: 模块级 asyncpg 连接池单例 (业务表 CRUD 共用, 与 Checkpointer 的 psycopg 池独立)
# AGENTS.md 第 6 章: 业务表读写复用同一 asyncpg 池, 避免每次请求创建新连接.
_pool_instance: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


def _read_init_sql() -> str:
    """同步读取 init.sql (在 asyncio.to_thread 中执行, 避免阻塞事件循环).

    ruff ASYNC230: async 函数禁止用阻塞 read_text, 故抽取为同步函数.
    """
    return INIT_SQL_PATH.read_text(encoding="utf-8")


async def init_database(settings: Settings | None = None) -> bool:
    """初始化 PostgreSQL 业务表 (Agent 启动时触发).

    读取 scripts/init.sql 并执行, 所有语句幂等 (IF NOT EXISTS).
    已存在的表不会被重建, 已存在的索引不会被重建.
    如需表结构变更, 在 init.sql 中追加 ALTER TABLE IF EXISTS ... ADD COLUMN IF NOT EXISTS ...

    Returns:
        True 成功, False 失败 (不阻断启动).
    """
    settings = settings or get_settings()

    if not INIT_SQL_PATH.exists():
        logger.warning("init.sql 不存在: %s, 跳过 DB 初始化", INIT_SQL_PATH)
        return False

    sql = await asyncio.to_thread(_read_init_sql)

    # asyncpg 原生 DSN: postgresql:// (非 sqlalchemy 的 postgresql+asyncpg://)
    dsn = settings.postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")

    try:
        conn = await asyncpg.connect(dsn)
        try:
            # asyncpg.execute 可执行多语句 SQL (含 CREATE TABLE/INDEX/EXTENSION)
            await conn.execute(sql)
            logger.info("PostgreSQL 业务表初始化完成 (init.sql 已执行, 幂等)")
            return True
        finally:
            await conn.close()
    except Exception as e:  # noqa: BLE001
        logger.error(
            "PostgreSQL 初始化失败 (不阻断启动, 仅告警): type=%s msg=%s",
            type(e).__name__,
            e,
        )
        return False


__all__ = ["get_pool", "init_database"]


async def get_pool(settings: Settings | None = None) -> asyncpg.Pool:
    """获取 asyncpg 连接池单例 (业务表 CRUD 共用).

    AGENTS.md 第 6 章: 业务表读写复用同一 asyncpg 池, 与 Checkpointer 的 psycopg 池独立.
    双重检查锁保证并发场景下只创建一个实例; 池大小从 settings.postgres_connection_pool_size 读取.

    Args:
        settings: 全局配置 (仅首次调用生效, 后续调用忽略).

    Returns:
        已创建的 asyncpg.Pool 实例.

    Raises:
        asyncpg.PostgresError: 连接池创建失败时抛出 (调用方应捕获并降级).
    """
    global _pool_instance

    # 快路径: 已有单例直接返回 (无锁开销)
    if _pool_instance is not None:
        return _pool_instance

    settings = settings or get_settings()

    async with _pool_lock:
        # 双重检查: 持锁后再次确认 (防止并发重复创建)
        if _pool_instance is not None:
            return _pool_instance

        # asyncpg 原生 DSN: postgresql:// (非 sqlalchemy 的 postgresql+asyncpg://)
        dsn = settings.postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
        pool_size = max(int(settings.postgres_connection_pool_size), 1)

        _pool_instance = await asyncpg.create_pool(
            dsn=dsn,
            min_size=min(2, pool_size),
            max_size=pool_size,
            command_timeout=30,
        )
        logger.info(
            "asyncpg 连接池已初始化 (业务表 CRUD, min=%d max=%d)",
            min(2, pool_size),
            pool_size,
        )
        return _pool_instance
