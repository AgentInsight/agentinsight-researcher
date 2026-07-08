"""单元测试: PostgreSQL 数据库初始化器.

验证 src/memory/db_initializer.py:
- _read_init_sql() 读取 scripts/init.sql 返回非空字符串
- init_database() 失败时不抛异常, 返回 False (不阻断启动)
- init_database() 成功时返回 True
- DSN 替换: postgresql+asyncpg:// → postgresql://

AGENTS.md 第 6/7 章: 业务表由 Agent 启动时执行 scripts/init.sql 创建 (幂等).
AGENTS.md 第 13 章: 单元测试不依赖外部服务 (用 mock asyncpg).
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest

from src.config.settings import Settings
from src.memory.db_initializer import INIT_SQL_PATH, _read_init_sql, init_database

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


class _MockConn:
    """伪造 asyncpg.Connection (仅 init_database 所需方法)."""

    def __init__(self) -> None:
        self.executed_sql: list[str] = []
        self.closed = False

    async def execute(self, sql: str) -> str:
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
        # 验证 SQL 被执行
        assert len(mock_conn.executed_sql) == 1
        assert "CREATE TABLE" in mock_conn.executed_sql[0].upper()
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

    async def test_execute_exception_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """conn.execute 抛异常时返回 False (不阻断启动)."""

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
        assert result is False
