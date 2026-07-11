"""研究报告持久化存储 (P1-Future-09).

设计参考: backend/server/report_store.py.
AGENTS.md 第 6/7 章硬约束:
- 业务表含 agent_id + user_id 双列复合索引
- 复用 db_initializer.get_pool() 的 asyncpg 连接池单例 (P0-4 修复, 避免每次请求新建短连接)
- save_report 失败不阻断主流程 (调用方 try/except)
"""

from __future__ import annotations

import json
import logging
import warnings
from typing import Any

import asyncpg

from src.config.settings import Settings, get_settings
from src.memory.db_initializer import get_pool

logger = logging.getLogger(__name__)

# 查询字段列表 (SELECT * 的显式版, 避免 SELECT * 在表结构变更时的隐患)
_SELECT_COLUMNS = (
    "report_id, session_id, user_id, agent_id, query, "
    "report_md, report_format, sources, agent_role, created_at, updated_at"
)


class ReportStore:
    """报告持久化存储 (P1-Future-09).

    设计参考: backend/server/report_store.py.
    复用 db_initializer.get_pool() 的 asyncpg 连接池单例 (P0-4 修复),
    每次 CRUD 操作通过 pool.acquire() 获取连接, 用完自动归还, 不再新建短连接.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def _dsn(self) -> str:
        """获取 asyncpg 原生 DSN (postgresql://).

        .. deprecated:: P0-4
            改为复用 ``db_initializer.get_pool()`` 连接池单例, 不再新建短连接.
            此方法仅为兼容外部调用保留, 后续版本可能移除.

        settings.postgres_dsn 返回 postgresql+asyncpg:// 前缀 (sqlalchemy 风格),
        asyncpg 需要 postgresql:// 前缀, 故替换.
        """
        warnings.warn(
            "ReportStore._dsn() is deprecated since P0-4; use db_initializer.get_pool() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._settings.postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")

    async def save_report(
        self,
        session_id: str,
        user_id: str,
        agent_id: str,
        query: str,
        report_md: str,
        report_format: str,
        sources: list[dict[str, Any]],
        agent_role: str | None = None,
    ) -> str:
        """保存报告到数据库, 返回 report_id.

        Args:
            session_id: 会话 ID (thread_id)
            user_id: 用户 ID
            agent_id: Agent 名称 (数据隔离键)
            query: 原始研究请求
            report_md: Markdown 报告原文
            report_format: 输出格式 (markdown/html/pdf/docx/json)
            sources: 引用来源列表
            agent_role: 角色 persona 简称 (可选, 设计参考: server 约定)

        Returns:
            report_id (UUID 字符串)
        """
        sources_json = json.dumps(sources, ensure_ascii=False)
        pool = await get_pool(self._settings)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO research_reports
                    (session_id, user_id, agent_id, query, report_md,
                     report_format, sources, agent_role)
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
                RETURNING report_id
                """,
                session_id,
                user_id,
                agent_id,
                query,
                report_md,
                report_format,
                sources_json,
                agent_role,
            )
            report_id = str(row["report_id"]) if row else ""
            logger.info(
                "报告已保存: report_id=%s, session_id=%s, agent_id=%s, user_id=%s",
                report_id,
                session_id,
                agent_id,
                user_id,
            )
            return report_id

    async def get_report(self, report_id: str) -> dict[str, Any] | None:
        """按 report_id 获取报告.

        Args:
            report_id: 报告 UUID (字符串)

        Returns:
            报告字典, 不存在返回 None
        """
        pool = await get_pool(self._settings)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_SELECT_COLUMNS} FROM research_reports WHERE report_id = $1::uuid",
                report_id,
            )
            if not row:
                return None
            return _row_to_dict(row)

    async def list_reports(
        self,
        session_id: str | None = None,
        user_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """列出报告 (按 session_id + user_id 组合过滤, AND 语义).

        Args:
            session_id: 会话 ID (可选过滤)
            user_id: 用户 ID (可选过滤)
            limit: 返回上限
            offset: 偏移量

        Returns:
            报告列表 (按 created_at DESC 排序)
        """
        pool = await get_pool(self._settings)
        async with pool.acquire() as conn:
            if session_id and user_id:
                rows = await conn.fetch(
                    f"""
                    SELECT {_SELECT_COLUMNS} FROM research_reports
                    WHERE session_id = $1 AND user_id = $2
                    ORDER BY created_at DESC LIMIT $3 OFFSET $4
                    """,
                    session_id,
                    user_id,
                    limit,
                    offset,
                )
            elif session_id:
                rows = await conn.fetch(
                    f"""
                    SELECT {_SELECT_COLUMNS} FROM research_reports
                    WHERE session_id = $1
                    ORDER BY created_at DESC LIMIT $2 OFFSET $3
                    """,
                    session_id,
                    limit,
                    offset,
                )
            elif user_id:
                rows = await conn.fetch(
                    f"""
                    SELECT {_SELECT_COLUMNS} FROM research_reports
                    WHERE user_id = $1
                    ORDER BY created_at DESC LIMIT $2 OFFSET $3
                    """,
                    user_id,
                    limit,
                    offset,
                )
            else:
                rows = await conn.fetch(
                    f"""
                    SELECT {_SELECT_COLUMNS} FROM research_reports
                    ORDER BY created_at DESC LIMIT $1 OFFSET $2
                    """,
                    limit,
                    offset,
                )
            return [_row_to_dict(r) for r in rows]

    async def delete_report(self, report_id: str) -> bool:
        """删除报告.

        Args:
            report_id: 报告 UUID (字符串)

        Returns:
            True 删除成功, False 报告不存在
        """
        pool = await get_pool(self._settings)
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM research_reports WHERE report_id = $1::uuid",
                report_id,
            )
            # asyncpg execute 返回 "DELETE N" 格式
            deleted = bool(result.endswith(" 1"))
            if deleted:
                logger.info("报告已删除: report_id=%s", report_id)
            return deleted


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """将 asyncpg Record 行转为字典.

    - report_id (UUID) 转字符串
    - sources (JSONB) asyncpg 自动解析为 list/dict, 兜底反序列化
    - created_at/updated_at 转 ISO 字符串 (便于 JSON 序列化)
    """
    d: dict[str, Any] = dict(row)
    if "report_id" in d and d["report_id"] is not None:
        d["report_id"] = str(d["report_id"])
    if isinstance(d.get("sources"), str):
        d["sources"] = json.loads(d["sources"])
    for k in ("created_at", "updated_at"):
        if k in d and hasattr(d[k], "isoformat"):
            d[k] = d[k].isoformat()
    return d


# ========== 全局单例 ==========

_report_store: ReportStore | None = None


def get_report_store() -> ReportStore:
    """获取 ReportStore 全局单例 (延迟初始化)."""
    global _report_store
    if _report_store is None:
        _report_store = ReportStore()
    return _report_store


__all__ = ["ReportStore", "get_report_store"]
