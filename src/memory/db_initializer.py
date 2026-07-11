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

    # 先连接到维护数据库 (postgres) 确保目标数据库存在, 不存在则创建
    # asyncpg 原生 DSN: postgresql:// (非 sqlalchemy 的 postgresql+asyncpg://)
    raw_dsn = settings.postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")
    target_db = settings.postgres_db
    maint_dsn = _replace_db_in_dsn(raw_dsn, "postgres")
    try:
        admin_conn = await asyncpg.connect(maint_dsn)
        try:
            exists = await admin_conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1", target_db
            )
            if not exists:
                # CREATE DATABASE 不支持参数绑定, 但 target_db 来自配置, 不接受用户输入
                await admin_conn.execute(f'CREATE DATABASE "{target_db}"')
                logger.info("PostgreSQL 数据库 %s 不存在, 已创建", target_db)
        finally:
            await admin_conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "PostgreSQL 数据库存在性检查/创建失败 (不阻断, 可能权限不足): type=%s msg=%s",
            type(e).__name__,
            e,
        )

    sql = await asyncio.to_thread(_read_init_sql)
    dsn = raw_dsn

    try:
        conn = await asyncpg.connect(dsn)
        try:
            # P2-3: 按分号拆分逐条执行, 独立事务隔离错误
            # 原 conn.execute(sql) 在非 autocommit 模式下隐式开启事务,
            # 中间失败会回滚全部已执行的 DDL. 拆分后每条独立执行.
            statements = [
                s.strip() for s in sql.split(";") if s.strip() and not s.strip().startswith("--")
            ]
            failed_count = 0
            for stmt in statements:
                try:
                    await conn.execute(stmt)
                except Exception as stmt_err:  # noqa: BLE001
                    # 单条失败不阻断后续 (DDL 幂等, 可能是列已存在等)
                    logger.debug("SQL 语句执行跳过 (可能已存在): %s", str(stmt_err)[:200])
                    failed_count += 1
            logger.info(
                "PostgreSQL 业务表初始化完成 (init.sql 已执行, 幂等, %d 条跳过)",
                failed_count,
            )
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


def _replace_db_in_dsn(dsn: str, new_db: str) -> str:
    """替换 DSN 中的数据库名.

    支持 postgresql://user:pass@host:port/dbname?params 格式.
    """
    # 用 urllib 解析再重组, 兼容 query 参数
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(dsn)
    path = parts.path
    # path 形如 "/dbname" 或 "" (无 path 时默认到 postgres)
    new_path = f"/{new_db}" if not path or path == "/" else path
    # 若原 path 已含 dbname, 替换; 否则追加
    if path and path != "/":
        new_path = f"/{new_db}"
    return urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, parts.fragment))


__all__ = ["get_pool", "init_database", "close_pool"]


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
            max_inactive_connection_lifetime=300,  # P1-9: 回收闲置连接 (5分钟)
        )
        logger.info(
            "asyncpg 连接池已初始化 (业务表 CRUD, min=%d max=%d)",
            min(2, pool_size),
            pool_size,
        )
        return _pool_instance


async def close_pool() -> None:
    """关闭 asyncpg 连接池 (应用 shutdown 时调用, P1-10).

    幂等: 无实例时直接返回.
    """
    global _pool_instance
    if _pool_instance is not None:
        await _pool_instance.close()
        _pool_instance = None
        logger.info("asyncpg 连接池已关闭")
