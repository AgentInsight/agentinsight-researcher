"""会话持久化存储 (以 UserId 为单位的会话管理).

设计约束:
- 业务表含 agent_id + user_id 双列复合索引
- 复用 db_initializer.get_pool() 的 asyncpg 连接池单例 (避免每次请求新建短连接)
- 所有查询显式 WHERE agent_id = ... AND user_id = ... (禁止无过滤全表扫描)
- 消息按 created_at ASC 排序 (旧→新), 分页从最新开始向前加载 (滚动加载)

表结构:
- research_sessions: 会话元数据 (session_id, title, query, status ...)
- chat_messages: 对话消息 (session_id, role, content, message_metadata)
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import asyncpg

from src.config.settings import Settings, get_settings
from src.memory.db_initializer import get_pool

logger = logging.getLogger(__name__)

# research_sessions 查询字段列表 (显式列出, 避免 SELECT * 在表结构变更时的隐患)
_SESSION_COLUMNS = (
    "session_id, agent_id, user_id, query, title, report_type, "
    "report_format, language, agent_role, agent_role_server, status, client_ip, "
    "created_at, updated_at, expires_at"
)

# chat_messages 查询字段列表
_MESSAGE_COLUMNS = "id, session_id, agent_id, user_id, role, content, message_metadata, created_at"


class SessionStore:
    """会话持久化存储 (PostgreSQL).

    复用 db_initializer.get_pool() 的 asyncpg 连接池单例,
    每次 CRUD 操作通过 pool.acquire() 获取连接, 用完自动归还.

    数据隔离: 所有查询带 agent_id + user_id.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    # ========== 会话管理 (research_sessions) ==========

    async def create_session(
        self,
        session_id: str,
        agent_id: str,
        user_id: str,
        title: str = "",
        query: str | None = None,
        client_ip: str = "",
    ) -> None:
        """创建会话记录 (幂等, 已存在则跳过).

        首次对话前调用, 创建空的 research_sessions 记录.
        使用 ON CONFLICT DO NOTHING 保证幂等 (重复创建不报错).

        Args:
            session_id: 会话 ID (thread_id)
            agent_id: Agent 名称 (数据隔离键)
            user_id: 用户 ID
            title: 会话标题 (用于会话列表显示)
            query: 首次查询内容 (可选, 首次对话时传入)
            client_ip: 客户端 IP 地址 (审计追溯用, PII, 可选)
        """
        pool = await get_pool(self._settings)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO research_sessions
                    (session_id, agent_id, user_id, query, title, status, client_ip)
                VALUES ($1, $2, $3, $4, $5, 'active', $6)
                ON CONFLICT (session_id, agent_id, user_id) DO NOTHING
                """,
                session_id,
                agent_id,
                user_id,
                query,
                title or "",
                client_ip or "",
            )
            logger.info(
                "会话已创建: session_id=%s, agent_id=%s, user_id=%s",
                session_id,
                agent_id,
                user_id,
            )

    async def ensure_session(
        self,
        session_id: str,
        agent_id: str,
        user_id: str,
        query: str | None = None,
        client_ip: str = "",
    ) -> None:
        """确保会话记录存在 (不存在则创建, 存在则更新 query 和 updated_at).

        在 chat_completions 端点首次对话时调用:
        - 会话不存在 → 创建 (含 query + client_ip)
        - 会话已存在 → 更新 query (如果提供了) 并触发 updated_at (由触发器自动维护)

        Args:
            session_id: 会话 ID
            agent_id: Agent 名称
            user_id: 用户 ID
            query: 首次查询内容 (可选)
            client_ip: 客户端 IP 地址 (审计追溯用, PII, 可选)
        """
        pool = await get_pool(self._settings)
        async with pool.acquire() as conn:
            # 先尝试插入 (幂等), 不存在时插入
            await conn.execute(
                """
                INSERT INTO research_sessions
                    (session_id, agent_id, user_id, query, title, status, client_ip)
                VALUES ($1, $2, $3, $4, $5, 'active', $6)
                ON CONFLICT (session_id, agent_id, user_id) DO UPDATE SET
                    query = COALESCE(EXCLUDED.query, research_sessions.query),
                    updated_at = NOW()
                """,
                session_id,
                agent_id,
                user_id,
                query,
                (query[:100] if query else "") or "",
                client_ip or "",
            )

    async def update_session_title(
        self,
        session_id: str,
        agent_id: str,
        user_id: str,
        title: str,
    ) -> bool:
        """更新会话标题.

        Args:
            session_id: 会话 ID
            agent_id: Agent 名称
            user_id: 用户 ID
            title: 新标题

        Returns:
            True 更新成功, False 会话不存在
        """
        pool = await get_pool(self._settings)
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE research_sessions
                SET title = $4
                WHERE session_id = $1 AND agent_id = $2 AND user_id = $3
                """,
                session_id,
                agent_id,
                user_id,
                title,
            )
            return result.endswith(" 1")

    async def update_report_config(
        self,
        session_id: str,
        agent_id: str,
        user_id: str,
        report_type: str | None = None,
        report_format: str | None = None,
        language: str | None = None,
    ) -> bool:
        """更新会话的报告配置 (report_type/report_format/language).

        任一参数为 None 时保持原值不变 (COALESCE).

        Args:
            session_id: 会话 ID
            agent_id: Agent 名称
            user_id: 用户 ID
            report_type: 报告类型 (basic_report/detailed_report/deep_research)
            report_format: 输出格式 (markdown/html/pdf/docx/json)
            language: 报告语言 (zh/en)

        Returns:
            True 更新成功, False 会话不存在
        """
        pool = await get_pool(self._settings)
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE research_sessions
                SET report_type = COALESCE($4, report_type),
                    report_format = COALESCE($5, report_format),
                    language = COALESCE($6, language)
                WHERE session_id = $1 AND agent_id = $2 AND user_id = $3
                """,
                session_id,
                agent_id,
                user_id,
                report_type,
                report_format,
                language,
            )
            return result.endswith(" 1")

    async def touch_session(
        self,
        session_id: str,
        agent_id: str,
        user_id: str,
    ) -> None:
        """更新会话的 updated_at 时间戳 (触发器自动维护).

        每次保存消息后调用, 确保会话在列表中按最近活跃排序.

        Args:
            session_id: 会话 ID
            agent_id: Agent 名称
            user_id: 用户 ID
        """
        pool = await get_pool(self._settings)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE research_sessions
                SET updated_at = NOW()
                WHERE session_id = $1 AND agent_id = $2 AND user_id = $3
                """,
                session_id,
                agent_id,
                user_id,
            )

    async def list_sessions(
        self,
        agent_id: str,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """列出用户会话 (按 updated_at DESC 排序).

        Args:
            agent_id: Agent 名称
            user_id: 用户 ID
            limit: 返回上限
            offset: 偏移量

        Returns:
            会话列表, 每项含 session_id/title/created_at/updated_at/message_count
        """
        pool = await get_pool(self._settings)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    rs.session_id, rs.title, rs.query, rs.status,
                    rs.report_type, rs.report_format, rs.language,
                    rs.created_at, rs.updated_at,
                    COALESCE(cm.cnt, 0) AS message_count
                FROM research_sessions rs
                LEFT JOIN (
                    SELECT session_id, agent_id, user_id, COUNT(*) AS cnt
                    FROM chat_messages
                    GROUP BY session_id, agent_id, user_id
                ) cm ON cm.session_id = rs.session_id
                    AND cm.agent_id = rs.agent_id
                    AND cm.user_id = rs.user_id
                WHERE rs.agent_id = $1 AND rs.user_id = $2
                ORDER BY rs.updated_at DESC
                LIMIT $3 OFFSET $4
                """,
                agent_id,
                user_id,
                limit,
                offset,
            )
            return [_session_row_to_dict(r) for r in rows]

    async def get_latest_session(
        self,
        agent_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        """获取最近活跃的会话 (按 updated_at DESC).

        Args:
            agent_id: Agent 名称
            user_id: 用户 ID

        Returns:
            会话字典或 None (无会话)
        """
        pool = await get_pool(self._settings)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    rs.session_id, rs.title, rs.query, rs.status,
                    rs.report_type, rs.report_format, rs.language,
                    rs.created_at, rs.updated_at,
                    COALESCE(cm.cnt, 0) AS message_count
                FROM research_sessions rs
                LEFT JOIN (
                    SELECT session_id, agent_id, user_id, COUNT(*) AS cnt
                    FROM chat_messages
                    GROUP BY session_id, agent_id, user_id
                ) cm ON cm.session_id = rs.session_id
                    AND cm.agent_id = rs.agent_id
                    AND cm.user_id = rs.user_id
                WHERE rs.agent_id = $1 AND rs.user_id = $2
                ORDER BY rs.updated_at DESC
                LIMIT 1
                """,
                agent_id,
                user_id,
            )
            if not row:
                return None
            return _session_row_to_dict(row)

    async def get_session(
        self,
        session_id: str,
        agent_id: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        """获取单个会话详情.

        Args:
            session_id: 会话 ID
            agent_id: Agent 名称
            user_id: 用户 ID

        Returns:
            会话字典或 None
        """
        pool = await get_pool(self._settings)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {_SESSION_COLUMNS} FROM research_sessions
                WHERE session_id = $1 AND agent_id = $2 AND user_id = $3
                """,
                session_id,
                agent_id,
                user_id,
            )
            if not row:
                return None
            return _session_row_to_dict(row)

    async def delete_session(
        self,
        session_id: str,
        agent_id: str,
        user_id: str,
    ) -> bool:
        """删除会话 (级联清理 chat_messages + research_sessions).

        注意: Checkpointer 和 Redis 缓存的清理由 API 层调用方处理
        (SessionStore 不依赖 LangGraph / Redis, 保持单一职责).

        Args:
            session_id: 会话 ID
            agent_id: Agent 名称
            user_id: 用户 ID

        Returns:
            True 删除成功, False 会话不存在
        """
        pool = await get_pool(self._settings)
        async with pool.acquire() as conn:
            # 事务: 先删消息, 再删会话
            async with conn.transaction():
                await conn.execute(
                    """
                    DELETE FROM chat_messages
                    WHERE session_id = $1 AND agent_id = $2 AND user_id = $3
                    """,
                    session_id,
                    agent_id,
                    user_id,
                )
                result = await conn.execute(
                    """
                    DELETE FROM research_sessions
                    WHERE session_id = $1 AND agent_id = $2 AND user_id = $3
                    """,
                    session_id,
                    agent_id,
                    user_id,
                )
                deleted = result.endswith(" 1")
                if deleted:
                    logger.info(
                        "会话已删除 (含消息): session_id=%s, agent_id=%s, user_id=%s",
                        session_id,
                        agent_id,
                        user_id,
                    )
                return deleted

    # ========== 消息管理 (chat_messages) ==========

    async def save_message(
        self,
        session_id: str,
        agent_id: str,
        user_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """保存单条消息到 chat_messages.

        Args:
            session_id: 会话 ID
            agent_id: Agent 名称
            user_id: 用户 ID
            role: 消息角色 (user / assistant)
            content: 消息内容
            metadata: 可选元数据 (如 sources, tool_calls)

        Returns:
            消息 ID (BIGSERIAL 主键)
        """
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        pool = await get_pool(self._settings)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO chat_messages
                    (session_id, agent_id, user_id, role, content, message_metadata)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                RETURNING id
                """,
                session_id,
                agent_id,
                user_id,
                role,
                content,
                metadata_json,
            )
            msg_id = row["id"] if row else 0
            # 更新会话的 updated_at (触发器自动维护, 此处显式 touch 确保排序正确)
            await self.touch_session(session_id, agent_id, user_id)
            return msg_id

    async def list_messages(
        self,
        session_id: str,
        agent_id: str,
        user_id: str,
        limit: int = 10,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """列出会话消息 (分页, 按 created_at ASC 排序: 旧→新).

        滚动加载语义: offset=0 返回最新 limit 条; offset=N 返回更早的 limit 条.
        实现: 先按 created_at DESC 跳过 offset 条取 limit 条, 再反转 ASC 返回.

        Args:
            session_id: 会话 ID
            agent_id: Agent 名称
            user_id: 用户 ID
            limit: 返回条数 (默认 10)
            offset: 偏移量 (0=最新 limit 条, 10=更早 limit 条)

        Returns:
            消息列表 (按 created_at ASC: 旧→新, 便于前端顺序渲染)
        """
        pool = await get_pool(self._settings)
        async with pool.acquire() as conn:
            # 子查询按 DESC 取最新 limit 条 (跳过 offset), 外层反转 ASC
            rows = await conn.fetch(
                f"""
                SELECT {_MESSAGE_COLUMNS} FROM (
                    SELECT {_MESSAGE_COLUMNS} FROM chat_messages
                    WHERE session_id = $1 AND agent_id = $2 AND user_id = $3
                    ORDER BY created_at DESC
                    LIMIT $4 OFFSET $5
                ) sub
                ORDER BY created_at ASC
                """,
                session_id,
                agent_id,
                user_id,
                limit,
                offset,
            )
            return [_message_row_to_dict(r) for r in rows]

    async def get_message_count(
        self,
        session_id: str,
        agent_id: str,
        user_id: str,
    ) -> int:
        """获取会话消息总数.

        Args:
            session_id: 会话 ID
            agent_id: Agent 名称
            user_id: 用户 ID

        Returns:
            消息总数
        """
        pool = await get_pool(self._settings)
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM chat_messages
                WHERE session_id = $1 AND agent_id = $2 AND user_id = $3
                """,
                session_id,
                agent_id,
                user_id,
            )
            return int(count) if count else 0

    async def get_session_title(
        self,
        session_id: str,
        agent_id: str,
        user_id: str,
    ) -> str:
        """获取会话标题 (不存在时返回空字符串).

        Args:
            session_id: 会话 ID
            agent_id: Agent 名称
            user_id: 用户 ID

        Returns:
            会话标题
        """
        pool = await get_pool(self._settings)
        async with pool.acquire() as conn:
            title = await conn.fetchval(
                """
                SELECT title FROM research_sessions
                WHERE session_id = $1 AND agent_id = $2 AND user_id = $3
                """,
                session_id,
                agent_id,
                user_id,
            )
            return title or ""


# ========== 行转字典辅助函数 ==========


def _session_row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """将 research_sessions 行转为字典.

    - created_at/updated_at/expires_at 转 ISO 字符串 (便于 JSON 序列化)
    """
    d: dict[str, Any] = dict(row)
    for k in ("created_at", "updated_at", "expires_at"):
        if k in d and hasattr(d[k], "isoformat"):
            d[k] = d[k].isoformat()
    return d


def _message_row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """将 chat_messages 行转为字典.

    - message_metadata (JSONB) asyncpg 自动解析为 dict, 兜底反序列化
    - created_at 转 ISO 字符串
    """
    d: dict[str, Any] = dict(row)
    if isinstance(d.get("message_metadata"), str):
        try:
            d["message_metadata"] = json.loads(d["message_metadata"])
        except (json.JSONDecodeError, TypeError):
            pass
    if "created_at" in d and hasattr(d["created_at"], "isoformat"):
        d["created_at"] = d["created_at"].isoformat()
    return d


def generate_session_id() -> str:
    """生成新的会话 ID (UUID v4)."""
    return str(uuid.uuid4())


# ========== 全局单例 ==========

_session_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    """获取 SessionStore 全局单例 (延迟初始化)."""
    global _session_store
    if _session_store is None:
        _session_store = SessionStore()
    return _session_store


__all__ = ["SessionStore", "get_session_store", "generate_session_id"]
