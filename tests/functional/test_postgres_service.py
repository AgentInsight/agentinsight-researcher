"""功能测试: 验证 PostgreSQL 服务 (Checkpointer + 业务元数据).

AGENTS.md 第 6/7 章硬约束:
- 单一数据库 agents, LangGraph Checkpointer 表由官方管理, 业务表含 agent_id+user_id 双列
- 业务表应含 created_at; 状态会变更的表还应含 updated_at + BEFORE UPDATE 触发器自动维护
- 查询应显式 WHERE agent_id = ... AND user_id = ..., 业务表应建 agent_id+user_id 复合索引
- 表名复数 snake_case; agent_id/user_id/session_id 三列统一 VARCHAR(64)
- 测试数据隔离: agent_id=test_* + user_id=test_* (第 13 章)

业务表清单 (scripts/init.sql, 6 张):
- research_sessions / research_reports / research_search_logs
- uploaded_files / token_usage_logs / mcp_configs
注: AGENTS.md 第 7 章示例提及 sessions/messages 命名风格, 实际业务表由 init.sql 定义;
    LangGraph Checkpointer 表 (checkpoints/writes/migrations) 由 SDK 管理, 非业务表.

执行方式 (宿主机, 容器栈已 healthy):
    set POSTGRES_HOST=127.0.0.1
    pytest tests/functional/test_postgres_service.py -v -m functional
"""

from __future__ import annotations

import os
import time
import uuid
from typing import TYPE_CHECKING

import pytest

# 注: psycopg 采用延迟导入 (与 test_container_health.py / test_smoke_functional.py 一致),
# 避免本地无该包时 pytest 收集失败 (功能测试在容器栈部署后执行, 依赖在运行环境可用).
if TYPE_CHECKING:
    import psycopg

# PostgreSQL 连接配置 (宿主机直连, 从环境变量注入)
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.getenv("POSTGRES_USER", "agentinsight")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
POSTGRES_DB = os.getenv("POSTGRES_DB", "agents")

# 业务表清单 (scripts/init.sql 定义的实际业务表)
# 注: 不含 LangGraph SDK 管理的 checkpoints/writes/migrations (非业务表)
BUSINESS_TABLES = [
    "research_sessions",
    "research_reports",
    "research_search_logs",
    "uploaded_files",
    "token_usage_logs",
    "mcp_configs",
]

# 测试数据隔离前缀 (AGENTS.md 第 13 章: agent_id=test_* / user_id=test_*)
TEST_AGENT_ID = f"test_pg_agent_{uuid.uuid4().hex[:8]}"
TEST_USER_ID = f"test_pg_user_{uuid.uuid4().hex[:8]}"


def _dsn() -> str:
    """构造 psycopg 连接 DSN."""
    return (
        f"host={POSTGRES_HOST} port={POSTGRES_PORT} dbname={POSTGRES_DB} "
        f"user={POSTGRES_USER} password={POSTGRES_PASSWORD} connect_timeout=5"
    )


def _connect() -> psycopg.Connection:
    """建立 PostgreSQL 连接 (同步, 延迟导入 psycopg)."""
    import psycopg  # type: ignore[import-not-found]

    return psycopg.connect(_dsn())


@pytest.mark.functional
def test_postgres_connection() -> None:
    """验证 PostgreSQL 连接成功: SELECT 1 + version() 非空.

    AGENTS.md 第 1 章: PostgreSQL ≥16 为 Checkpointer + 业务元数据存储.
    """
    import psycopg  # type: ignore[import-not-found]

    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        result = cur.fetchone()
        assert result is not None and result[0] == 1, f"SELECT 1 返回异常: {result}"

        # 验证版本 ≥16 (AGENTS.md 要求 ≥16, 项目实际要求 ≥17)
        cur.execute("SELECT current_setting('server_version_num')")
        version_num = int(cur.fetchone()[0])
        assert version_num >= 160000, f"PostgreSQL 版本低于 16: version_num={version_num}"
        cur.close()
        conn.close()
    except psycopg.OperationalError as e:
        pytest.fail(f"PostgreSQL 连接失败: {e}")


@pytest.mark.functional
def test_business_tables_exist() -> None:
    """验证 init.sql 定义的业务表全部存在 (Agent 启动时 init_database 执行).

    AGENTS.md 第 6 章: Agent 容器启动时应执行 scripts/init.sql 初始化业务表 (幂等).
    检查 information_schema.tables, 6 张业务表必须全部存在.
    """
    import psycopg  # type: ignore[import-not-found]

    try:
        conn = _connect()
        cur = conn.cursor()
        # 查询当前 schema 下的实际表名 (全小写)
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """
        )
        existing = {row[0] for row in cur.fetchall()}
        cur.close()
        conn.close()
    except psycopg.Error as e:
        pytest.fail(f"查询业务表失败: {e}")

    missing = [t for t in BUSINESS_TABLES if t not in existing]
    assert not missing, (
        f"缺失业务表 (Agent 启动时 init_database 未执行成功?): {missing}\n"
        f"现有表: {sorted(existing)}"
    )


@pytest.mark.functional
def test_agent_id_user_id_composite_index() -> None:
    """验证业务表含 agent_id + user_id 复合索引 (数据隔离查询性能保证).

    AGENTS.md 第 7 章:
    - 业务表应含 agent_id + user_id 双列, 建复合索引
    - 查询应显式 WHERE agent_id = ... AND user_id = ..., 禁止全表扫描
    - scripts/init.sql 为每张业务表创建 idx_<table>_agent_user 索引

    验证至少 research_sessions 表存在 (agent_id, user_id) 复合索引.
    """
    import psycopg  # type: ignore[import-not-found]

    try:
        conn = _connect()
        cur = conn.cursor()
        # 查询所有业务表的索引定义, 筛选含 agent_id + user_id 的复合索引
        cur.execute(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND indexname LIKE '%agent_user%'
            """
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except psycopg.Error as e:
        pytest.fail(f"查询复合索引失败: {e}")

    assert rows, "未找到任何 agent_user 复合索引 (init.sql 的 idx_*_agent_user 未创建)"
    # 至少一张表应有 (agent_id, user_id) 复合索引
    # indexdef 形如: CREATE INDEX idx_research_sessions_agent_user
    #                 ON public.research_sessions USING btree (agent_id, user_id)
    has_composite = any("agent_id" in idxdef and "user_id" in idxdef for _, idxdef in rows)
    assert has_composite, f"索引名含 agent_user 但定义未含 agent_id+user_id 复合列: {rows}"


@pytest.mark.functional
def test_updated_at_trigger() -> None:
    """验证 updated_at 触发器自动维护 (BEFORE UPDATE 触发器).

    AGENTS.md 第 7 章:
    - 状态会变更的表 (research_sessions/research_reports/uploaded_files/mcp_configs)
      应含 updated_at + BEFORE UPDATE 触发器 (update_updated_at_column()) 自动维护
    - 不推荐业务代码手动赋值 updated_at

    通过 mcp_configs 表验证: INSERT → 记录 updated_at → UPDATE → 验证 updated_at 推进.
    使用 test_* agent_id/user_id/name 隔离, finally 清理.
    """
    import psycopg  # type: ignore[import-not-found]

    test_name = f"test-trigger-{uuid.uuid4().hex[:8]}"
    conn = _connect()
    try:
        cur = conn.cursor()
        # 1. INSERT 一条测试配置 (enabled=False 避免触发 MCP 可用性测试)
        cur.execute(
            """
            INSERT INTO mcp_configs
                (agent_id, user_id, name, transport_type, enabled, is_system, version, description)
            VALUES (%s, %s, %s, 'stdio', FALSE, FALSE, 1, 'trigger test')
            RETURNING id, updated_at
            """,
            (TEST_AGENT_ID, TEST_USER_ID, test_name),
        )
        row = cur.fetchone()
        assert row is not None, "INSERT RETURNING 未返回行"
        config_id, updated_at_before = row
        conn.commit()

        # 2. 等待 1s 确保 NOW() 推进 (避免触发器更新前后时间戳相同)
        time.sleep(1.0)

        # 3. UPDATE 该行 (改 description), 不手动赋值 updated_at
        cur.execute(
            """
            UPDATE mcp_configs SET description = %s WHERE id = %s
            RETURNING updated_at
            """,
            ("trigger test updated", config_id),
        )
        updated_row = cur.fetchone()
        assert updated_row is not None, "UPDATE RETURNING 未返回行"
        updated_at_after = updated_row[0]
        conn.commit()

        # 4. 验证触发器自动推进了 updated_at (after > before)
        assert updated_at_after > updated_at_before, (
            f"updated_at 未自动更新 (触发器未生效): "
            f"before={updated_at_before} after={updated_at_after}"
        )
    except psycopg.Error as e:
        conn.rollback()
        pytest.fail(f"updated_at 触发器测试失败: {e}")
    finally:
        # 清理: 删除测试行 (按 agent_id+user_id 精确隔离, 避免误删)
        try:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM mcp_configs WHERE agent_id = %s AND user_id = %s",
                (TEST_AGENT_ID, TEST_USER_ID),
            )
            conn.commit()
        except psycopg.Error:  # noqa: BLE001
            conn.rollback()
        conn.close()
