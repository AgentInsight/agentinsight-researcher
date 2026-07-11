"""单元测试: 研究报告持久化存储.

验证 src/memory/report_store.py:
- _row_to_dict(row): UUID/JSONB/时间戳转换
- ReportStore._dsn(): 返回 postgresql:// 格式 DSN (非 postgresql+asyncpg://)
- CRUD 方法 (save_report/get_report/list_reports/delete_report) 用 mock asyncpg

AGENTS.md 第 6/7 章: 业务表含 agent_id + user_id 双列复合索引, asyncpg 直连.
AGENTS.md 第 13 章: 单元测试不依赖外部服务 (用 mock asyncpg).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from src.config.settings import Settings
from src.memory.report_store import ReportStore, _row_to_dict

pytestmark = pytest.mark.unit


# ========== _row_to_dict 测试 ==========


class TestRowToDict:
    """_row_to_dict: asyncpg Record 行转字典."""

    def test_uuid_to_string(self) -> None:
        """report_id (UUID) 转字符串."""
        rid = uuid.uuid4()
        row: dict[str, Any] = {"report_id": rid, "query": "test"}
        result = _row_to_dict(row)
        assert result["report_id"] == str(rid)
        assert isinstance(result["report_id"], str)

    def test_sources_jsonb_string_parsed(self) -> None:
        """sources (JSONB 字符串) 自动反序列化为 list."""
        sources = [{"title": "src1", "url": "http://example.com"}]
        row: dict[str, Any] = {
            "report_id": uuid.uuid4(),
            "sources": json.dumps(sources),
        }
        result = _row_to_dict(row)
        assert result["sources"] == sources
        assert isinstance(result["sources"], list)

    def test_sources_already_parsed_unchanged(self) -> None:
        """sources 已是 list (asyncpg 自动解析) 时保持不变."""
        sources = [{"title": "src1"}]
        row: dict[str, Any] = {"report_id": uuid.uuid4(), "sources": sources}
        result = _row_to_dict(row)
        assert result["sources"] == sources

    def test_sources_dict_parsed(self) -> None:
        """sources 为 dict 时保持不变 (非字符串不反序列化)."""
        sources = {"key": "value"}
        row: dict[str, Any] = {"report_id": uuid.uuid4(), "sources": sources}
        result = _row_to_dict(row)
        assert result["sources"] == sources

    def test_datetime_to_isoformat(self) -> None:
        """created_at/updated_at (datetime) 转 ISO 字符串."""
        now = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        row: dict[str, Any] = {
            "report_id": uuid.uuid4(),
            "created_at": now,
            "updated_at": now,
        }
        result = _row_to_dict(row)
        assert result["created_at"] == now.isoformat()
        assert result["updated_at"] == now.isoformat()

    def test_none_report_id_preserved(self) -> None:
        """report_id=None 时保持 None (不转字符串)."""
        row: dict[str, Any] = {"report_id": None}
        result = _row_to_dict(row)
        assert result["report_id"] is None

    def test_missing_optional_fields(self) -> None:
        """缺少可选字段时不报错."""
        row: dict[str, Any] = {"report_id": uuid.uuid4()}
        result = _row_to_dict(row)
        assert "created_at" not in result
        assert "sources" not in result
        assert "updated_at" not in result


# ========== ReportStore._dsn 测试 ==========


class TestReportStoreDsn:
    """ReportStore._dsn: 获取 asyncpg 原生 DSN."""

    def test_dsn_replaces_asyncpg_prefix(self) -> None:
        """_dsn() 返回 postgresql:// 格式 (非 postgresql+asyncpg://)."""
        settings = Settings(
            postgres_user="user",
            postgres_password="pass",
            postgres_host="host",
            postgres_port=5432,
            postgres_db="db",
            _env_file=None,
        )
        store = ReportStore(settings)
        # _dsn() 已弃用, 用 pytest.warns 显式捕获 DeprecationWarning 避免 warning 噪声
        with pytest.warns(DeprecationWarning, match="deprecated since P0-4"):
            dsn = store._dsn()
        assert dsn == "postgresql://user:pass@host:5432/db"
        assert "asyncpg" not in dsn

    def test_dsn_starts_with_postgresql(self) -> None:
        """_dsn() 以 postgresql:// 开头."""
        settings = Settings(_env_file=None)
        store = ReportStore(settings)
        with pytest.warns(DeprecationWarning, match="deprecated since P0-4"):
            dsn = store._dsn()
        assert dsn.startswith("postgresql://")

    def test_postgres_dsn_has_asyncpg_prefix(self) -> None:
        """settings.postgres_dsn 返回 postgresql+asyncpg:// 前缀 (验证替换必要)."""
        settings = Settings(
            postgres_user="u",
            postgres_password="p",
            postgres_host="h",
            postgres_port=5432,
            postgres_db="d",
            _env_file=None,
        )
        assert settings.postgres_dsn == "postgresql+asyncpg://u:p@h:5432/d"


# ========== CRUD 方法测试 (mock asyncpg) ==========


class _MockConn:
    """伪造 asyncpg.Connection."""

    def __init__(
        self,
        fetchrow_result: dict[str, Any] | None = None,
        fetch_result: list[dict[str, Any]] | None = None,
        execute_result: str = "DELETE 1",
    ) -> None:
        self._fetchrow_result = fetchrow_result
        self._fetch_result = fetch_result or []
        self._execute_result = execute_result
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.fetchrow_calls.append((query, args))
        return self._fetchrow_result

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_calls.append((query, args))
        return self._fetch_result

    async def execute(self, query: str, *args: Any) -> str:
        self.execute_calls.append((query, args))
        return self._execute_result

    async def close(self) -> None:
        self.closed = True


def _make_settings() -> Settings:
    """构造测试用 Settings."""
    return Settings(
        postgres_user="user",
        postgres_password="pass",
        postgres_host="host",
        postgres_port=5432,
        postgres_db="db",
        _env_file=None,
    )


class _MockPool:
    """伪造 asyncpg.Pool (report_store 改用 get_pool 连接池)."""

    def __init__(self, conn: _MockConn) -> None:
        self._conn = conn

    def acquire(self) -> Any:
        """返回 async context manager, yield 伪造连接."""

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _acquire() -> Any:
            yield self._conn

        return _acquire()


def _install_mock_pool(monkeypatch: pytest.MonkeyPatch, mock_conn: _MockConn) -> None:
    """注入 mock get_pool (report_store 通过 get_pool 获取连接池, 不再直连).

    mock get_pool 返回 _MockPool, 其 acquire() yield _MockConn.
    """

    async def _mock_get_pool(_settings: Any = None) -> _MockPool:
        return _MockPool(mock_conn)

    monkeypatch.setattr("src.memory.report_store.get_pool", _mock_get_pool)


class TestSaveReport:
    """save_report 测试."""

    async def test_success_returns_report_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """save_report 成功返回 report_id 字符串."""
        rid = uuid.uuid4()
        mock_conn = _MockConn(fetchrow_result={"report_id": rid})
        _install_mock_pool(monkeypatch, mock_conn)

        store = ReportStore(_make_settings())
        report_id = await store.save_report(
            session_id="sess-1",
            user_id="user-1",
            agent_id="agentinsight-researcher",
            query="研究 AI",
            report_md="# 报告",
            report_format="markdown",
            sources=[{"title": "src1", "url": "http://example.com"}],
            agent_role="financial_analyst",
        )
        assert report_id == str(rid)
        # 验证 fetchrow 被调用 (INSERT ... RETURNING)
        assert len(mock_conn.fetchrow_calls) == 1
        query, args = mock_conn.fetchrow_calls[0]
        assert "INSERT INTO research_reports" in query
        assert "RETURNING report_id" in query
        assert args[0] == "sess-1"

    async def test_no_row_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """save_report 无 RETURNING 行时返回空字符串."""
        mock_conn = _MockConn(fetchrow_result=None)
        _install_mock_pool(monkeypatch, mock_conn)

        store = ReportStore(_make_settings())
        report_id = await store.save_report(
            session_id="sess-1",
            user_id="user-1",
            agent_id="agentinsight-researcher",
            query="研究 AI",
            report_md="# 报告",
            report_format="markdown",
            sources=[],
        )
        assert report_id == ""


class TestGetReport:
    """get_report 测试."""

    async def test_found_returns_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_report 找到报告时返回字典 (含类型转换)."""
        rid = uuid.uuid4()
        now = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        mock_conn = _MockConn(
            fetchrow_result={
                "report_id": rid,
                "session_id": "sess-1",
                "user_id": "user-1",
                "agent_id": "agentinsight-researcher",
                "query": "研究 AI",
                "report_md": "# 报告",
                "report_format": "markdown",
                "sources": json.dumps([{"title": "src1"}]),
                "agent_role": "analyst",
                "created_at": now,
                "updated_at": now,
            }
        )
        _install_mock_pool(monkeypatch, mock_conn)

        store = ReportStore(_make_settings())
        result = await store.get_report(str(rid))
        assert result is not None
        assert result["report_id"] == str(rid)
        assert result["sources"] == [{"title": "src1"}]
        assert result["created_at"] == now.isoformat()

    async def test_not_found_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_report 未找到时返回 None."""
        mock_conn = _MockConn(fetchrow_result=None)
        _install_mock_pool(monkeypatch, mock_conn)

        store = ReportStore(_make_settings())
        result = await store.get_report(str(uuid.uuid4()))
        assert result is None


class TestListReports:
    """list_reports 测试."""

    async def test_by_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_reports 按 session_id 过滤."""
        rid = uuid.uuid4()
        mock_conn = _MockConn(
            fetch_result=[
                {
                    "report_id": rid,
                    "session_id": "sess-1",
                    "user_id": "user-1",
                    "agent_id": "agentinsight-researcher",
                    "query": "研究 AI",
                    "report_md": "# 报告",
                    "report_format": "markdown",
                    "sources": [],
                    "agent_role": None,
                    "created_at": datetime(2026, 1, 15, tzinfo=UTC),
                    "updated_at": datetime(2026, 1, 15, tzinfo=UTC),
                }
            ]
        )
        _install_mock_pool(monkeypatch, mock_conn)

        store = ReportStore(_make_settings())
        results = await store.list_reports(session_id="sess-1", limit=10, offset=0)
        assert len(results) == 1
        assert results[0]["report_id"] == str(rid)
        # 验证查询含 session_id 过滤
        query, args = mock_conn.fetch_calls[0]
        assert "WHERE session_id = $1" in query
        assert args[0] == "sess-1"

    async def test_by_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_reports 按 user_id 过滤."""
        mock_conn = _MockConn(fetch_result=[])
        _install_mock_pool(monkeypatch, mock_conn)

        store = ReportStore(_make_settings())
        results = await store.list_reports(user_id="user-1", limit=5, offset=0)
        assert results == []
        query, args = mock_conn.fetch_calls[0]
        assert "WHERE user_id = $1" in query
        assert args[0] == "user-1"

    async def test_by_session_and_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_reports 同时按 session_id 和 user_id 过滤 (AND 条件, 数据隔离)."""
        mock_conn = _MockConn(fetch_result=[])
        _install_mock_pool(monkeypatch, mock_conn)

        store = ReportStore(_make_settings())
        results = await store.list_reports(
            session_id="sess-1", user_id="user-1", limit=10, offset=0
        )
        assert results == []
        query, _ = mock_conn.fetch_calls[0]
        assert "WHERE session_id = $1 AND user_id = $2" in query

    async def test_no_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """list_reports 无过滤时返回全部 (无 WHERE 子句)."""
        mock_conn = _MockConn(fetch_result=[])
        _install_mock_pool(monkeypatch, mock_conn)

        store = ReportStore(_make_settings())
        results = await store.list_reports(limit=20, offset=0)
        assert results == []
        query, _ = mock_conn.fetch_calls[0]
        assert "WHERE" not in query


class TestDeleteReport:
    """delete_report 测试."""

    async def test_success_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """delete_report 删除成功 (DELETE 1) 返回 True."""
        mock_conn = _MockConn(execute_result="DELETE 1")
        _install_mock_pool(monkeypatch, mock_conn)

        store = ReportStore(_make_settings())
        result = await store.delete_report(str(uuid.uuid4()))
        assert result is True
        query, _ = mock_conn.execute_calls[0]
        assert "DELETE FROM research_reports" in query

    async def test_not_found_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """delete_report 报告不存在 (DELETE 0) 返回 False."""
        mock_conn = _MockConn(execute_result="DELETE 0")
        _install_mock_pool(monkeypatch, mock_conn)

        store = ReportStore(_make_settings())
        result = await store.delete_report(str(uuid.uuid4()))
        assert result is False
