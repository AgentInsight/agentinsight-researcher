"""PostgreSQL 数据库初始化器.

设计约束:
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

# 模块级 asyncpg 连接池单例 (业务表 CRUD 共用, 与 Checkpointer 的 psycopg 池独立)
# 业务表读写复用同一 asyncpg 池, 避免每次请求创建新连接.
_pool_instance: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


def _read_init_sql() -> str:
    """同步读取 init.sql (在 asyncio.to_thread 中执行, 避免阻塞事件循环).

    ruff ASYNC230: async 函数禁止用阻塞 read_text, 故抽取为同步函数.
    """
    return INIT_SQL_PATH.read_text(encoding="utf-8")


def _split_sql_statements(sql: str) -> list[str]:
    """智能拆分 SQL 脚本为独立语句.

    正确处理:
    - dollar-quoted 字符串 ($$ ... $$ 或 $tag$ ... $tag$): 内部分号不拆分
    - 单引号字符串 ('...;...'): 内部分号不拆分
    - 行注释 (-- ...): 不影响分号识别
    - 块注释 (/* ... */): 不影响分号识别

    Args:
        sql: 完整 SQL 脚本文本.

    Returns:
        拆分后的 SQL 语句列表 (已 strip, 过滤空语句和纯注释语句).
    """
    statements: list[str] = []
    current: list[str] = []
    i = 0
    n = len(sql)
    in_single_quote = False
    in_line_comment = False
    in_block_comment = False
    dollar_tag: str | None = None  # 当前 dollar-quote 标签 (如 $$ 或 $func$)

    while i < n:
        ch = sql[i]
        # 预读后续字符用于模式匹配
        next_ch = sql[i + 1] if i + 1 < n else ""

        # 1. dollar-quoted 字符串处理 (PostgreSQL 特有, 如 $$ ... $$ 或 $tag$ ... $tag$)
        if ch == "$" and not in_single_quote and not in_line_comment and not in_block_comment:
            # 尝试匹配 dollar tag: $tag$
            j = sql.find("$", i + 1)
            if j > i:
                tag = sql[i : j + 1]  # 如 $$ 或 $func$
                # 验证 tag 格式: $ 后跟字母/下划线/空 (空= $$), 再跟 $
                inner = tag[1:-1]
                if inner == "" or (inner[0].isalpha() or inner[0] == "_"):
                    if dollar_tag is None:
                        # 进入 dollar-quoted 字符串
                        dollar_tag = tag
                        current.append(tag)
                        i = j + 1
                        continue
                    elif tag == dollar_tag:
                        # 退出 dollar-quoted 字符串
                        dollar_tag = None
                        current.append(tag)
                        i = j + 1
                        continue
            # 普通 $ 字符 (不在 dollar-quoted 上下文中)
            current.append(ch)
            i += 1
            continue

        # 2. 在 dollar-quoted 字符串内: 所有字符原样保留 (包括分号)
        if dollar_tag is not None:
            current.append(ch)
            i += 1
            continue

        # 3. 单引号字符串处理
        if ch == "'" and not in_line_comment and not in_block_comment:
            if in_single_quote:
                # 检查是否为转义单引号 ''
                if next_ch == "'":
                    current.append("''")
                    i += 2
                    continue
                # 退出单引号字符串
                in_single_quote = False
            else:
                in_single_quote = True
            current.append(ch)
            i += 1
            continue

        # 4. 行注释处理 (内容不追加到 current, 避免 stmt.startswith("--") 误过滤)
        if ch == "-" and next_ch == "-" and not in_single_quote and not in_block_comment:
            in_line_comment = True
            i += 2
            continue

        # 5. 块注释处理 (保留在 current, PostgreSQL 解析器会忽略)
        if ch == "/" and next_ch == "*" and not in_single_quote and not in_line_comment:
            in_block_comment = True
            current.append("/*")
            i += 2
            continue
        if in_block_comment and ch == "*" and next_ch == "/":
            in_block_comment = False
            current.append("*/")
            i += 2
            continue

        # 6. 行注释结束 (换行): 保留换行符以维持 SQL 格式
        if in_line_comment and ch == "\n":
            in_line_comment = False
            current.append(ch)
            i += 1
            continue

        # 6.1 行注释内字符: 跳过 (不追加)
        if in_line_comment:
            i += 1
            continue

        # 7. 分号: 语句分隔符 (仅在不在字符串/注释内时)
        if ch == ";" and not in_single_quote and not in_line_comment and not in_block_comment:
            stmt = "".join(current).strip()
            if stmt and not stmt.startswith("--"):
                statements.append(stmt)
            current = []
            i += 1
            continue

        # 默认: 追加字符
        current.append(ch)
        i += 1

    # 处理最后一个语句 (无分号结尾)
    last_stmt = "".join(current).strip()
    if last_stmt and not last_stmt.startswith("--"):
        statements.append(last_stmt)

    return statements


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
            # 每条语句独立事务: BEGIN/COMMIT/ROLLBACK 隔离错误, 单条失败不影响后续
            # 修复: 原 sql.split(";") 会错误拆分 dollar-quoted 字符串 $$ ... $$ 内的分号
            #   (如 CREATE OR REPLACE FUNCTION 体内的 NEW.updated_at = NOW(); )
            #   导致函数体被截断, 后续语句在 aborted 事务中全部失败 (chat_messages 表未创建)
            statements = _split_sql_statements(sql)
            failed_count = 0
            for stmt in statements:
                try:
                    await conn.execute("BEGIN")
                    await conn.execute(stmt)
                    await conn.execute("COMMIT")
                except Exception as stmt_err:  # noqa: BLE001
                    # 单条失败: ROLLBACK 清理事务状态, 不阻断后续 (DDL 幂等)
                    try:
                        await conn.execute("ROLLBACK")
                    except Exception:  # noqa: BLE001
                        pass  # ROLLBACK 失败忽略 (连接可能已断)
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

    业务表读写复用同一 asyncpg 池, 与 Checkpointer 的 psycopg 池独立.
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
            max_inactive_connection_lifetime=300,  # 回收闲置连接 (5分钟)
        )
        logger.info(
            "asyncpg 连接池已初始化 (业务表 CRUD, min=%d max=%d)",
            min(2, pool_size),
            pool_size,
        )
        return _pool_instance


async def close_pool() -> None:
    """关闭 asyncpg 连接池 (应用 shutdown 时调用).

    幂等: 无实例时直接返回.
    """
    global _pool_instance
    if _pool_instance is not None:
        await _pool_instance.close()
        _pool_instance = None
        logger.info("asyncpg 连接池已关闭")
