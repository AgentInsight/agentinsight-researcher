"""单元测试: 会话持久化存储 (SessionStore).

验证 src/memory/session_store.py:
- ensure_session(): ON CONFLICT DO UPDATE 幂等创建/更新
- create_session(): ON CONFLICT DO NOTHING 幂等创建
- list_messages(): 滚动加载 (子查询 DESC 取最新 limit 条, 外层反转 ASC)
- save_message(): 保存消息 + 触发 touch_session 更新时间戳
- delete_session(): 事务级联清理 chat_messages + research_sessions
- list_sessions(): 列出用户会话 (按 updated_at DESC)
- update_session_title() / touch_session() / get_session() / get_latest_session()
- get_message_count() / get_session_title()
- 数据隔离: 所有查询显式 WHERE agent_id = ... AND user_id = ...

业务表含 agent_id + user_id 双列复合索引, 复用 db_initializer.get_pool() 连接池.
单元测试不依赖外部服务 (用 mock asyncpg).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from src.config.settings import Settings
from src.memory.session_store import (
    SessionStore,
    _message_row_to_dict,
    _session_row_to_dict,
    generate_session_id,
)

pytestmark = pytest.mark.unit


# ========== Mock 辅助类 ==========


class _MockTransaction:
    """伪造 asyncpg 事务上下文管理器 (async with conn.transaction())."""

    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> _MockTransaction:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None:
            self.rolled_back = True
        else:
            self.committed = True


class _MockConn:
    """伪造 asyncpg.Connection (支持 execute/fetchrow/fetch/fetchval/transaction)."""

    def __init__(
        self,
        fetchrow_result: dict[str, Any] | None = None,
        fetch_result: list[dict[str, Any]] | None = None,
        fetchval_result: Any = None,
        execute_result: str = "DELETE 1",
    ) -> None:
        self._fetchrow_result = fetchrow_result
        self._fetch_result = fetch_result or []
        self._fetchval_result = fetchval_result
        self._execute_result = execute_result
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.transactions: list[_MockTransaction] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.execute_calls.append((query, args))
        return self._execute_result

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.fetchrow_calls.append((query, args))
        return self._fetchrow_result

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_calls.append((query, args))
        return self._fetch_result

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.fetchval_calls.append((query, args))
        return self._fetchval_result

    def transaction(self) -> _MockTransaction:
        tx = _MockTransaction()
        self.transactions.append(tx)
        return tx


class _MockPool:
    """伪造 asyncpg.Pool (acquire 返回异步上下文管理器)."""

    def __init__(self, conn: _MockConn) -> None:
        self._conn = conn

    def acquire(self) -> Any:
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _acquire() -> Any:
            yield self._conn

        return _acquire()


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


def _install_mock_pool(monkeypatch: pytest.MonkeyPatch, mock_conn: _MockConn) -> None:
    """注入 mock get_pool (session_store 通过 get_pool 获取连接池).

    mock get_pool 返回 _MockPool, 其 acquire() yield _MockConn.
    save_message 内部调用 touch_session 会再次 get_pool, 返回同一个 mock_conn.
    """

    async def _mock_get_pool(_settings: Any = None) -> _MockPool:
        return _MockPool(mock_conn)

    monkeypatch.setattr("src.memory.session_store.get_pool", _mock_get_pool)


# ========== 行转字典辅助函数测试 ==========


class TestSessionRowToDict:
    """_session_row_to_dict: asyncpg Record 行转字典."""

    def test_datetime_to_isoformat(self) -> None:
        """created_at/updated_at/expires_at (datetime) 转 ISO 字符串."""
        now = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        row: dict[str, Any] = {
            "session_id": "sess-1",
            "created_at": now,
            "updated_at": now,
            "expires_at": now,
        }
        result = _session_row_to_dict(row)
        assert result["created_at"] == now.isoformat()
        assert result["updated_at"] == now.isoformat()
        assert result["expires_at"] == now.isoformat()

    def test_no_datetime_fields_unchanged(self) -> None:
        """无时间字段时保持原样."""
        row: dict[str, Any] = {"session_id": "sess-1", "title": "测试"}
        result = _session_row_to_dict(row)
        assert result["session_id"] == "sess-1"
        assert result["title"] == "测试"


class TestMessageRowToDict:
    """_message_row_to_dict: chat_messages 行转字典."""

    def test_metadata_string_parsed(self) -> None:
        """message_metadata (JSONB 字符串) 自动反序列化为 dict."""
        metadata = {"sources": [{"title": "src1"}]}
        row: dict[str, Any] = {
            "id": 1,
            "message_metadata": json.dumps(metadata, ensure_ascii=False),
            "created_at": datetime(2026, 1, 15, tzinfo=UTC),
        }
        result = _message_row_to_dict(row)
        assert result["message_metadata"] == metadata
        assert isinstance(result["message_metadata"], dict)

    def test_metadata_already_dict_unchanged(self) -> None:
        """message_metadata 已是 dict (asyncpg 自动解析) 时保持不变."""
        metadata = {"key": "value"}
        row: dict[str, Any] = {"id": 1, "message_metadata": metadata}
        result = _message_row_to_dict(row)
        assert result["message_metadata"] == metadata

    def test_metadata_invalid_string_kept_as_is(self) -> None:
        """message_metadata 非法 JSON 字符串时保持原样 (不抛异常)."""
        row: dict[str, Any] = {"id": 1, "message_metadata": "{invalid json"}
        result = _message_row_to_dict(row)
        assert result["message_metadata"] == "{invalid json"


# ========== ensure_session 测试 ==========


class TestEnsureSession:
    """ensure_session: 幂等创建/更新会话 (ON CONFLICT DO UPDATE)."""

    async def test_executes_insert_with_on_conflict_update(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ensure_session 使用 ON CONFLICT DO UPDATE 保证幂等."""
        mock_conn = _MockConn()
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        await store.ensure_session("sess-1", "agent-1", "user-1", query="研究 AI")

        assert len(mock_conn.execute_calls) == 1
        query, args = mock_conn.execute_calls[0]
        assert "INSERT INTO research_sessions" in query
        assert "ON CONFLICT" in query
        assert "DO UPDATE" in query
        assert "COALESCE(EXCLUDED.query" in query
        # 参数顺序: session_id, agent_id, user_id, query, title
        assert args[0] == "sess-1"
        assert args[1] == "agent-1"
        assert args[2] == "user-1"
        assert args[3] == "研究 AI"
        # title 应为 query 前 100 字符
        assert args[4] == "研究 AI"

    async def test_query_truncates_title_to_100_chars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """query 超过 100 字符时, title 截断为前 100 字符."""
        mock_conn = _MockConn()
        _install_mock_pool(monkeypatch, mock_conn)

        long_query = "研" * 150
        store = SessionStore(_make_settings())
        await store.ensure_session("sess-1", "agent-1", "user-1", query=long_query)

        _, args = mock_conn.execute_calls[0]
        # title (args[4]) 应为前 100 字符
        assert len(args[4]) == 100
        assert args[4] == "研" * 100

    async def test_no_query_passes_empty_title(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """query=None 时 title 为空字符串."""
        mock_conn = _MockConn()
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        await store.ensure_session("sess-1", "agent-1", "user-1", query=None)

        _, args = mock_conn.execute_calls[0]
        assert args[3] is None  # query=None
        assert args[4] == ""  # title=""


# ========== create_session 测试 ==========


class TestCreateSession:
    """create_session: 幂等创建 (ON CONFLICT DO NOTHING)."""

    async def test_uses_on_conflict_do_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """create_session 使用 ON CONFLICT DO NOTHING (重复创建不报错)."""
        mock_conn = _MockConn()
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        await store.create_session("sess-1", "agent-1", "user-1", title="标题")

        assert len(mock_conn.execute_calls) == 1
        query, args = mock_conn.execute_calls[0]
        assert "INSERT INTO research_sessions" in query
        assert "ON CONFLICT" in query
        assert "DO NOTHING" in query
        assert args[0] == "sess-1"
        assert args[1] == "agent-1"
        assert args[2] == "user-1"
        assert args[4] == "标题"


# ========== list_messages 测试 ==========


class TestListMessages:
    """list_messages: 滚动加载 (子查询 DESC + 外层 ASC)."""

    async def test_returns_messages_in_asc_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """list_messages 返回消息列表 (按 created_at ASC: 旧→新)."""
        now = datetime(2026, 1, 15, tzinfo=UTC)
        mock_conn = _MockConn(
            fetch_result=[
                {
                    "id": 1,
                    "session_id": "sess-1",
                    "agent_id": "agent-1",
                    "user_id": "user-1",
                    "role": "user",
                    "content": "你好",
                    "message_metadata": None,
                    "created_at": now,
                },
                {
                    "id": 2,
                    "session_id": "sess-1",
                    "agent_id": "agent-1",
                    "user_id": "user-1",
                    "role": "assistant",
                    "content": "你好, 有什么可以帮你?",
                    "message_metadata": {"sources": []},
                    "created_at": now,
                },
            ]
        )
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        messages = await store.list_messages("sess-1", "agent-1", "user-1", limit=10)

        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
        # 验证 SQL 含子查询 DESC + 外层 ASC (滚动加载)
        query, args = mock_conn.fetch_calls[0]
        assert "ORDER BY created_at DESC" in query
        assert "LIMIT $4 OFFSET $5" in query
        assert "ORDER BY created_at ASC" in query
        # 数据隔离: WHERE agent_id AND user_id
        assert "WHERE session_id = $1 AND agent_id = $2 AND user_id = $3" in query
        assert args[0] == "sess-1"
        assert args[1] == "agent-1"
        assert args[2] == "user-1"

    async def test_empty_returns_empty_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无消息时返回空列表."""
        mock_conn = _MockConn(fetch_result=[])
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        messages = await store.list_messages("sess-1", "agent-1", "user-1")
        assert messages == []


# ========== save_message 测试 ==========


class TestSaveMessage:
    """save_message: 保存消息 + 触发 touch_session."""

    async def test_returns_message_id_and_touches_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """save_message 返回消息 ID, 并调用 touch_session 更新时间戳."""
        mock_conn = _MockConn(fetchrow_result={"id": 42})
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        msg_id = await store.save_message(
            "sess-1",
            "agent-1",
            "user-1",
            role="user",
            content="测试消息",
            metadata={"sources": [{"title": "src1"}]},
        )

        assert msg_id == 42
        # fetchrow 调用 (INSERT ... RETURNING id)
        assert len(mock_conn.fetchrow_calls) == 1
        query, args = mock_conn.fetchrow_calls[0]
        assert "INSERT INTO chat_messages" in query
        assert "RETURNING id" in query
        assert args[3] == "user"  # role
        assert args[4] == "测试消息"  # content
        # touch_session 调用 execute (UPDATE updated_at)
        assert len(mock_conn.execute_calls) == 1
        touch_query, touch_args = mock_conn.execute_calls[0]
        assert "UPDATE research_sessions" in touch_query
        assert "SET updated_at = NOW()" in touch_query
        assert touch_args[0] == "sess-1"

    async def test_no_metadata_passes_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """metadata=None 时传入 None (不序列化)."""
        mock_conn = _MockConn(fetchrow_result={"id": 1})
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        msg_id = await store.save_message(
            "sess-1", "agent-1", "user-1", role="assistant", content="回复"
        )

        assert msg_id == 1
        _, args = mock_conn.fetchrow_calls[0]
        # metadata_json 为 None (args[5])
        assert args[5] is None

    async def test_no_row_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """INSERT 无 RETURNING 行时返回 0."""
        mock_conn = _MockConn(fetchrow_result=None)
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        msg_id = await store.save_message(
            "sess-1", "agent-1", "user-1", role="user", content="测试"
        )
        assert msg_id == 0


# ========== delete_session 测试 ==========


class TestDeleteSession:
    """delete_session: 事务级联清理 chat_messages + research_sessions."""

    async def test_success_returns_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """删除成功 (DELETE 1) 返回 True."""
        mock_conn = _MockConn(execute_result="DELETE 1")
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        result = await store.delete_session("sess-1", "agent-1", "user-1")

        assert result is True
        # 验证使用事务
        assert len(mock_conn.transactions) == 1
        assert mock_conn.transactions[0].committed is True
        # 验证两次 execute: 先删消息, 再删会话
        assert len(mock_conn.execute_calls) == 2
        first_query, _ = mock_conn.execute_calls[0]
        second_query, _ = mock_conn.execute_calls[1]
        assert "DELETE FROM chat_messages" in first_query
        assert "DELETE FROM research_sessions" in second_query

    async def test_not_found_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """会话不存在 (DELETE 0) 返回 False."""
        mock_conn = _MockConn(execute_result="DELETE 0")
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        result = await store.delete_session("sess-1", "agent-1", "user-1")

        assert result is False
        # 仍使用事务 (即使最终返回 False)
        assert len(mock_conn.transactions) == 1

    async def test_data_isolation_in_delete_query(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """删除查询含 WHERE agent_id AND user_id (数据隔离)."""
        mock_conn = _MockConn(execute_result="DELETE 1")
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        await store.delete_session("sess-1", "agent-1", "user-1")

        for query, args in mock_conn.execute_calls:
            assert "WHERE session_id = $1 AND agent_id = $2 AND user_id = $3" in query
            assert args[0] == "sess-1"
            assert args[1] == "agent-1"
            assert args[2] == "user-1"


# ========== list_sessions 测试 ==========


class TestListSessions:
    """list_sessions: 列出用户会话 (按 updated_at DESC)."""

    async def test_returns_sessions_with_message_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """list_sessions 返回会话列表 (含 message_count)."""
        now = datetime(2026, 1, 15, tzinfo=UTC)
        mock_conn = _MockConn(
            fetch_result=[
                {
                    "session_id": "sess-1",
                    "title": "会话1",
                    "query": "研究 AI",
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                    "message_count": 5,
                }
            ]
        )
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        sessions = await store.list_sessions("agent-1", "user-1", limit=50, offset=0)

        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "sess-1"
        assert sessions[0]["message_count"] == 5
        # 验证 SQL 含 agent_id + user_id 过滤
        query, args = mock_conn.fetch_calls[0]
        assert "WHERE rs.agent_id = $1 AND rs.user_id = $2" in query
        assert "ORDER BY rs.updated_at DESC" in query
        assert args[0] == "agent-1"
        assert args[1] == "user-1"

    async def test_empty_returns_empty_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无会话时返回空列表."""
        mock_conn = _MockConn(fetch_result=[])
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        sessions = await store.list_sessions("agent-1", "user-1")
        assert sessions == []


# ========== update_session_title 测试 ==========


class TestUpdateSessionTitle:
    """update_session_title: 更新会话标题."""

    async def test_success_returns_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """更新成功 (UPDATE 1) 返回 True."""
        mock_conn = _MockConn(execute_result="UPDATE 1")
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        result = await store.update_session_title(
            "sess-1", "agent-1", "user-1", "新标题"
        )

        assert result is True
        query, args = mock_conn.execute_calls[0]
        assert "UPDATE research_sessions" in query
        assert "SET title = $4" in query
        assert args[3] == "新标题"

    async def test_not_found_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """会话不存在 (UPDATE 0) 返回 False."""
        mock_conn = _MockConn(execute_result="UPDATE 0")
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        result = await store.update_session_title(
            "sess-1", "agent-1", "user-1", "新标题"
        )
        assert result is False


# ========== get_session / get_latest_session 测试 ==========


class TestGetSession:
    """get_session: 获取单个会话详情."""

    async def test_found_returns_dict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """找到会话时返回字典."""
        now = datetime(2026, 1, 15, tzinfo=UTC)
        mock_conn = _MockConn(
            fetchrow_result={
                "session_id": "sess-1",
                "agent_id": "agent-1",
                "user_id": "user-1",
                "query": "研究 AI",
                "title": "标题",
                "report_type": None,
                "report_format": None,
                "agent_role": None,
                "agent_role_server": None,
                "status": "active",
                "created_at": now,
                "updated_at": now,
                "expires_at": None,
            }
        )
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        result = await store.get_session("sess-1", "agent-1", "user-1")

        assert result is not None
        assert result["session_id"] == "sess-1"
        assert result["created_at"] == now.isoformat()
        # 验证数据隔离
        query, args = mock_conn.fetchrow_calls[0]
        assert "WHERE session_id = $1 AND agent_id = $2 AND user_id = $3" in query

    async def test_not_found_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未找到时返回 None."""
        mock_conn = _MockConn(fetchrow_result=None)
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        result = await store.get_session("sess-1", "agent-1", "user-1")
        assert result is None


class TestGetLatestSession:
    """get_latest_session: 获取最近活跃会话."""

    async def test_found_returns_dict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """找到最近会话时返回字典."""
        now = datetime(2026, 1, 15, tzinfo=UTC)
        mock_conn = _MockConn(
            fetchrow_result={
                "session_id": "sess-latest",
                "title": "最新会话",
                "query": "研究",
                "status": "active",
                "created_at": now,
                "updated_at": now,
                "message_count": 3,
            }
        )
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        result = await store.get_latest_session("agent-1", "user-1")

        assert result is not None
        assert result["session_id"] == "sess-latest"
        query, _ = mock_conn.fetchrow_calls[0]
        assert "ORDER BY rs.updated_at DESC" in query
        assert "LIMIT 1" in query

    async def test_not_found_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无会话时返回 None."""
        mock_conn = _MockConn(fetchrow_result=None)
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        result = await store.get_latest_session("agent-1", "user-1")
        assert result is None


# ========== touch_session 测试 ==========


class TestTouchSession:
    """touch_session: 更新会话 updated_at."""

    async def test_executes_update_query(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """touch_session 执行 UPDATE updated_at = NOW()."""
        mock_conn = _MockConn()
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        await store.touch_session("sess-1", "agent-1", "user-1")

        assert len(mock_conn.execute_calls) == 1
        query, args = mock_conn.execute_calls[0]
        assert "UPDATE research_sessions" in query
        assert "SET updated_at = NOW()" in query
        assert "WHERE session_id = $1 AND agent_id = $2 AND user_id = $3" in query
        assert args[0] == "sess-1"
        assert args[1] == "agent-1"
        assert args[2] == "user-1"


# ========== get_message_count 测试 ==========


class TestGetMessageCount:
    """get_message_count: 获取会话消息总数."""

    async def test_returns_count(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """返回消息总数."""
        mock_conn = _MockConn(fetchval_result=42)
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        count = await store.get_message_count("sess-1", "agent-1", "user-1")

        assert count == 42
        query, args = mock_conn.fetchval_calls[0]
        assert "SELECT COUNT(*) FROM chat_messages" in query
        assert "WHERE session_id = $1 AND agent_id = $2 AND user_id = $3" in query

    async def test_zero_when_no_messages(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无消息时返回 0."""
        mock_conn = _MockConn(fetchval_result=0)
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        count = await store.get_message_count("sess-1", "agent-1", "user-1")
        assert count == 0

    async def test_none_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """fetchval 返回 None 时返回 0."""
        mock_conn = _MockConn(fetchval_result=None)
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        count = await store.get_message_count("sess-1", "agent-1", "user-1")
        assert count == 0


# ========== get_session_title 测试 ==========


class TestGetSessionTitle:
    """get_session_title: 获取会话标题."""

    async def test_returns_title(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """返回会话标题."""
        mock_conn = _MockConn(fetchval_result="测试标题")
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        title = await store.get_session_title("sess-1", "agent-1", "user-1")

        assert title == "测试标题"
        query, _ = mock_conn.fetchval_calls[0]
        assert "SELECT title FROM research_sessions" in query

    async def test_not_found_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """会话不存在时返回空字符串."""
        mock_conn = _MockConn(fetchval_result=None)
        _install_mock_pool(monkeypatch, mock_conn)

        store = SessionStore(_make_settings())
        title = await store.get_session_title("sess-1", "agent-1", "user-1")
        assert title == ""


# ========== generate_session_id 测试 ==========


class TestGenerateSessionId:
    """generate_session_id: 生成 UUID v4 会话 ID."""

    def test_returns_unique_uuid_string(self) -> None:
        """生成唯一 UUID 字符串."""
        sid1 = generate_session_id()
        sid2 = generate_session_id()
        assert sid1 != sid2
        assert isinstance(sid1, str)
        # UUID v4 格式: 8-4-4-4-12
        parts = sid1.split("-")
        assert len(parts) == 5
        assert len(parts[0]) == 8
