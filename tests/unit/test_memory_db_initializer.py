"""单元测试: PostgreSQL 数据库初始化器.

验证 src/memory/db_initializer.py:
- _read_init_sql() 读取 scripts/init.sql 返回非空字符串
- _split_sql_statements() 智能拆分 SQL (dollar-quoted / 单引号 / 注释)
- init_database() 失败时不抛异常, 返回 False (不阻断启动)
- init_database() 成功时返回 True
- DSN 替换: postgresql+asyncpg:// → postgresql://

业务表由 Agent 启动时执行 scripts/init.sql 创建 (幂等).
单元测试不依赖外部服务 (用 mock asyncpg).
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest

from src.config.settings import Settings
from src.memory.db_initializer import (
    INIT_SQL_PATH,
    _read_init_sql,
    _split_sql_statements,
    init_database,
)

pytestmark = pytest.mark.unit


class TestReadInitSql:
    """_read_init_sql 测试."""

    def test_returns_non_empty_string(self) -> None:
        """读取 init.sql 返回非空字符串."""
        sql = _read_init_sql()
        assert isinstance(sql, str)
        assert len(sql) > 0

    def test_contains_create_table(self) -> None:
        """init.sql 含 CREATE TABLE 语句."""
        sql = _read_init_sql()
        assert "CREATE TABLE" in sql.upper()

    def test_contains_idempotent_ddl(self) -> None:
        """init.sql DDL 幂等 (含 IF NOT EXISTS)."""
        sql = _read_init_sql()
        assert "IF NOT EXISTS" in sql.upper()

    def test_init_sql_path_exists(self) -> None:
        """INIT_SQL_PATH 路径存在."""
        assert INIT_SQL_PATH.exists()


class TestSplitSqlStatements:
    """_split_sql_statements 测试 (智能 SQL 拆分器)."""

    def test_simple_statements(self) -> None:
        """简单语句按分号拆分."""
        sql = "CREATE TABLE t1 (id int); CREATE TABLE t2 (id int);"
        stmts = _split_sql_statements(sql)
        assert len(stmts) == 2
        assert "t1" in stmts[0]
        assert "t2" in stmts[1]

    def test_dollar_quoted_function_body(self) -> None:
        """dollar-quoted 函数体内的分号不拆分."""
        sql = """
CREATE OR REPLACE FUNCTION foo() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE bar (id int);
"""
        stmts = _split_sql_statements(sql)
        # 应拆分为 2 条: 函数定义 + CREATE TABLE
        assert len(stmts) == 2
        # 函数定义应包含完整函数体 (含内部分号)
        assert "FUNCTION" in stmts[0]
        assert "NOW()" in stmts[0]
        assert "RETURN NEW" in stmts[0]
        # 第二条是 CREATE TABLE
        assert "CREATE TABLE bar" in stmts[1]

    def test_tagged_dollar_quoted(self) -> None:
        """带标签的 dollar-quoted ($func$ ... $func$) 不拆分."""
        sql = """
CREATE FUNCTION baz() RETURNS int AS $func$
BEGIN
    RETURN 1;
END;
$func$ LANGUAGE plpgsql;

SELECT 1;
"""
        stmts = _split_sql_statements(sql)
        assert len(stmts) == 2
        assert "$func$" in stmts[0]
        assert "RETURN 1" in stmts[0]

    def test_single_quote_string_with_semicolon(self) -> None:
        """单引号字符串内的分号不拆分."""
        sql = "INSERT INTO t VALUES ('a;b'); SELECT 1;"
        stmts = _split_sql_statements(sql)
        assert len(stmts) == 2
        assert "'a;b'" in stmts[0]

    def test_line_comments_ignored(self) -> None:
        """行注释内的分号不影响拆分."""
        sql = """
-- this is a comment; with semicolon
CREATE TABLE t (id int);
"""
        stmts = _split_sql_statements(sql)
        assert len(stmts) == 1
        assert "CREATE TABLE" in stmts[0]

    def test_block_comments_ignored(self) -> None:
        """块注释内的分号不影响拆分."""
        sql = """
/* this is a; block comment */
CREATE TABLE t (id int);
"""
        stmts = _split_sql_statements(sql)
        assert len(stmts) == 1
        assert "CREATE TABLE" in stmts[0]

    def test_empty_and_comment_only_statements_filtered(self) -> None:
        """空语句和纯注释语句被过滤."""
        sql = """
-- comment only
;

CREATE TABLE t (id int);
"""
        stmts = _split_sql_statements(sql)
        assert len(stmts) == 1
        assert "CREATE TABLE" in stmts[0]

    def test_real_init_sql_chat_messages_table_present(self) -> None:
        """真实 init.sql 拆分后包含 chat_messages 表创建语句."""
        sql = _read_init_sql()
        stmts = _split_sql_statements(sql)
        # 应有多条语句
        assert len(stmts) > 10
        # 必须包含 chat_messages 表创建语句
        chat_messages_stmt = [s for s in stmts if "chat_messages" in s and "CREATE TABLE" in s]
        assert len(chat_messages_stmt) == 1, "chat_messages 表 CREATE TABLE 语句应存在"
        # 验证 chat_messages 表定义完整
        assert "session_id" in chat_messages_stmt[0]
        assert "agent_id" in chat_messages_stmt[0]
        assert "user_id" in chat_messages_stmt[0]

    def test_real_init_sql_function_intact(self) -> None:
        """真实 init.sql 拆分后函数定义完整 (含函数体内分号)."""
        sql = _read_init_sql()
        stmts = _split_sql_statements(sql)
        # 找到 update_updated_at_column 函数定义 (CREATE OR REPLACE FUNCTION, 排除 EXECUTE FUNCTION)
        func_stmt = [
            s
            for s in stmts
            if "update_updated_at_column" in s and "CREATE OR REPLACE FUNCTION" in s.upper()
        ]
        assert len(func_stmt) == 1, "update_updated_at_column 函数定义应存在"
        # 验证函数体完整 (含内部分号)
        assert "NEW.updated_at = NOW()" in func_stmt[0]
        assert "RETURN NEW" in func_stmt[0]
        assert "$$" in func_stmt[0]


class _MockConn:
    """伪造 asyncpg.Connection (仅 init_database 所需方法)."""

    def __init__(self) -> None:
        self.executed_sql: list[str] = []
        self.closed = False

    async def execute(self, sql: str) -> str:
        # 记录所有执行的 SQL (含 BEGIN/COMMIT/ROLLBACK 事务控制语句)
        self.executed_sql.append(sql)
        return "CREATE TABLE"

    async def close(self) -> None:
        self.closed = True


class TestInitDatabase:
    """init_database 测试."""

    async def test_failure_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """asyncpg.connect 抛异常时返回 False (不阻断启动)."""

        async def _fail_connect(_dsn: str) -> Any:
            raise ConnectionError("connection refused")

        monkeypatch.setattr(asyncpg, "connect", _fail_connect)
        settings = Settings(_env_file=None)
        result = await init_database(settings)
        assert result is False

    async def test_success_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """asyncpg 正常连接时返回 True."""
        mock_conn = _MockConn()

        async def _mock_connect(_dsn: str) -> _MockConn:
            return mock_conn

        monkeypatch.setattr(asyncpg, "connect", _mock_connect)
        settings = Settings(_env_file=None)
        result = await init_database(settings)
        assert result is True
        # 验证 SQL 被执行 (含 BEGIN/COMMIT 事务控制 + DDL 语句)
        assert len(mock_conn.executed_sql) > 1
        assert any("CREATE" in s.upper() for s in mock_conn.executed_sql)
        # 验证事务控制语句存在 (BEGIN/COMMIT)
        assert any(s == "BEGIN" for s in mock_conn.executed_sql)
        assert any(s == "COMMIT" for s in mock_conn.executed_sql)
        # 验证连接已关闭
        assert mock_conn.closed is True

    async def test_dsn_replaces_asyncpg_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DSN 替换: postgresql+asyncpg:// → postgresql://.

        init_database 调用 asyncpg.connect 两次:
        1. 连维护库 (postgres) 检查/创建目标数据库
        2. 连目标库 (db) 执行 init.sql
        两次 DSN 均应为 postgresql:// 前缀 (非 postgresql+asyncpg://).
        """
        captured_dsn: list[str] = []
        mock_conn = _MockConn()

        async def _mock_connect(dsn: str) -> _MockConn:
            captured_dsn.append(dsn)
            return mock_conn

        monkeypatch.setattr(asyncpg, "connect", _mock_connect)
        settings = Settings(
            postgres_user="user",
            postgres_password="pass",
            postgres_host="host",
            postgres_port=5432,
            postgres_db="db",
            _env_file=None,
        )
        await init_database(settings)
        # 两次连接: 维护库 + 目标库
        assert len(captured_dsn) == 2
        # 所有 DSN 均为 postgresql:// 前缀
        for dsn in captured_dsn:
            assert dsn.startswith("postgresql://")
            assert "asyncpg" not in dsn
        # 第二次连接到目标库 (db) 执行 init.sql
        assert captured_dsn[1] == "postgresql://user:pass@host:5432/db"

    async def test_execute_exception_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """conn.execute 抛异常时仍返回 True (单条失败不阻断, 连接成功即 True).

        注: BEGIN 失败时 ROLLBACK 也会失败, 但不影响最终结果 (返回 True).
        """

        class _FailConn:
            async def execute(self, _sql: str) -> str:
                raise RuntimeError("syntax error")

            async def close(self) -> None:
                pass

        async def _mock_connect(_dsn: str) -> _FailConn:
            return _FailConn()

        monkeypatch.setattr(asyncpg, "connect", _mock_connect)
        settings = Settings(_env_file=None)
        result = await init_database(settings)
        assert result is True

    async def test_chat_messages_table_in_executed_sql(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """验证 init_database 执行的 SQL 包含 chat_messages 表创建语句.

        回归测试: 修复前 sql.split(";") 会错误拆分 dollar-quoted 函数体,
        导致事务 abort, chat_messages 表未创建.
        """
        mock_conn = _MockConn()

        async def _mock_connect(_dsn: str) -> _MockConn:
            return mock_conn

        monkeypatch.setattr(asyncpg, "connect", _mock_connect)
        settings = Settings(_env_file=None)
        await init_database(settings)
        # 验证 chat_messages 表 CREATE TABLE 语句被执行
        chat_messages_exec = [
            s for s in mock_conn.executed_sql if "chat_messages" in s and "CREATE TABLE" in s
        ]
        assert len(chat_messages_exec) == 1, "chat_messages CREATE TABLE 应被执行一次"
        # 验证函数定义语句被执行 (含完整函数体, 排除 EXECUTE FUNCTION)
        func_exec = [
            s
            for s in mock_conn.executed_sql
            if "update_updated_at_column" in s and "CREATE OR REPLACE FUNCTION" in s.upper()
        ]
        assert len(func_exec) == 1, "update_updated_at_column 函数定义应被执行一次"
        assert "NOW()" in func_exec[0], "函数体应完整 (含内部分号)"
