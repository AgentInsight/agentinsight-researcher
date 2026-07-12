"""会话管理 API 路由 (以 UserId 为单位的会话持久化).

端点列表:
- GET    /v1/sessions                          列出当前用户会话
- GET    /v1/sessions/latest                   获取最近会话
- GET    /v1/sessions/{session_id}/messages    获取会话消息 (分页, 滚动加载)
- POST   /v1/sessions                          创建新会话
- DELETE /v1/sessions/{session_id}             删除会话 (级联清理)
- PATCH  /v1/sessions/{session_id}             更新会话标题

数据隔离:
- 所有查询带 agent_id + user_id
- user_id 由 JWT 中间件注入 (contextvars)
- agent_id = agent_name (全局唯一隔离键)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.api.middleware import get_request_agent_id, get_request_user_id
from src.memory.session_store import generate_session_id, get_session_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/sessions", tags=["session-management"])


# ========== 请求/响应模型 ==========


class CreateSessionRequest(BaseModel):
    """创建会话请求."""

    session_id: str | None = Field(None, description="会话 ID (不传则自动生成 UUID)")
    title: str | None = Field(None, description="会话标题 (不传则用 query 前 100 字符)")


class UpdateSessionRequest(BaseModel):
    """更新会话请求."""

    title: str = Field(..., min_length=1, max_length=256, description="新标题")


# ========== 端点实现 ==========


def _get_user_agent() -> tuple[str, str]:
    """从请求上下文获取 user_id 和 agent_id.

    Returns:
        (user_id, agent_id)

    Raises:
        HTTPException: 401 user_id 未解析 (中间件未注入)
    """
    user_id = get_request_user_id()
    agent_id = get_request_agent_id()
    if not user_id:
        raise HTTPException(status_code=401, detail="无法解析用户身份 (缺少 user_id)")
    if not agent_id:
        raise HTTPException(status_code=500, detail="无法解析 Agent 身份 (缺少 agent_id)")
    return user_id, agent_id


@router.get("")
async def list_sessions(
    limit: int = Query(50, ge=1, le=200, description="返回上限"),
    offset: int = Query(0, ge=0, description="偏移量"),
) -> list[dict[str, Any]]:
    """列出当前用户的会话 (按 updated_at DESC 排序).

    返回: [{"session_id", "title", "query", "status", "created_at", "updated_at", "message_count"}]
    """
    user_id, agent_id = _get_user_agent()
    store = get_session_store()
    sessions = await store.list_sessions(agent_id, user_id, limit=limit, offset=offset)
    return sessions


@router.get("/latest", response_model=None)
async def get_latest_session() -> dict[str, Any] | JSONResponse:
    """获取当前用户最近活跃的会话.

    返回: {"session_id", "title", ...} 或 404 (无会话).
    """
    user_id, agent_id = _get_user_agent()
    store = get_session_store()
    session = await store.get_latest_session(agent_id, user_id)
    if not session:
        return JSONResponse(
            status_code=404,
            content={"detail": "无会话记录"},
        )
    return session


@router.get("/{session_id}/messages")
async def list_session_messages(
    session_id: str,
    limit: int = Query(10, ge=1, le=100, description="返回条数 (默认 10)"),
    offset: int = Query(0, ge=0, description="偏移量 (0=最新 limit 条, 滚动加载更早消息)"),
) -> dict[str, Any]:
    """获取会话消息 (分页, 滚动加载).

    滚动加载语义:
    - offset=0: 返回最新 limit 条消息 (按 created_at ASC: 旧→新)
    - offset=N: 返回更早的 limit 条消息
    - has_more=True 表示还有更早的消息可加载

    返回: {"messages": [...], "total": N, "has_more": bool}
    """
    user_id, agent_id = _get_user_agent()
    store = get_session_store()

    # 校验会话归属当前用户
    session = await store.get_session(session_id, agent_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")

    messages = await store.list_messages(session_id, agent_id, user_id, limit=limit, offset=offset)
    total = await store.get_message_count(session_id, agent_id, user_id)
    has_more = (offset + limit) < total

    return {
        "messages": messages,
        "total": total,
        "has_more": has_more,
        "limit": limit,
        "offset": offset,
    }


@router.post("")
async def create_session(request: CreateSessionRequest) -> dict[str, Any]:
    """创建新会话.

    请求体: {"session_id": "optional", "title": "optional"}
    - session_id 不传则自动生成 UUID
    - title 不传则默认为空字符串

    返回: {"session_id", "title", "created_at"}
    """
    user_id, agent_id = _get_user_agent()
    session_id = request.session_id or generate_session_id()
    title = request.title or ""

    store = get_session_store()
    await store.create_session(session_id, agent_id, user_id, title=title)

    # 返回创建的会话信息
    session = await store.get_session(session_id, agent_id, user_id)
    if not session:
        # 极端情况: 创建后立即查询失败, 返回基本字段
        return {"session_id": session_id, "title": title, "message_count": 0}
    session["message_count"] = 0
    return session


@router.delete("/{session_id}")
async def delete_session(session_id: str) -> dict[str, Any]:
    """删除会话 (级联清理).

    级联清理:
    1. chat_messages 表 (SessionStore.delete_session 事务内清理)
    2. research_sessions 表 (SessionStore.delete_session 事务内清理)
    3. LangGraph Checkpointer (checkpoints 表, 按 thread_id 删除)
    4. Redis 缓存 (按 {agent_id}:{user_id}:{session_id}* 模式清理)

    返回: {"session_id", "deleted": true}
    """
    user_id, agent_id = _get_user_agent()
    store = get_session_store()

    # 校验会话归属
    session = await store.get_session(session_id, agent_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")

    # 1+2: 删除 chat_messages + research_sessions (事务)
    deleted = await store.delete_session(session_id, agent_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 3: 清理 LangGraph Checkpointer (按 thread_id 删除 checkpoints/writes)
    await _cleanup_checkpointer(session_id)

    # 4: 清理 Redis 缓存 (按前缀模式删除)
    await _cleanup_redis_cache(agent_id, user_id, session_id)

    logger.info(
        "会话已级联删除: session_id=%s, agent_id=%s, user_id=%s",
        session_id,
        agent_id,
        user_id,
    )
    return {"session_id": session_id, "deleted": True}


@router.patch("/{session_id}")
async def update_session(session_id: str, request: UpdateSessionRequest) -> dict[str, Any]:
    """更新会话标题.

    请求体: {"title": "新标题"}

    返回: {"session_id", "title", "updated": true}
    """
    user_id, agent_id = _get_user_agent()
    store = get_session_store()

    # 校验会话归属
    session = await store.get_session(session_id, agent_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")

    updated = await store.update_session_title(session_id, agent_id, user_id, request.title)
    if not updated:
        raise HTTPException(status_code=500, detail="更新失败")

    return {"session_id": session_id, "title": request.title, "updated": True}


# ========== 级联清理辅助函数 ==========


async def _cleanup_checkpointer(session_id: str) -> None:
    """清理 LangGraph Checkpointer 中该会话的 checkpoint 数据.

    LangGraph PostgresSaver 的 checkpoints/writes 表按 thread_id 隔离,
    直接通过 asyncpg 删除 (不依赖 LangGraph SDK 的删除方法, 保持解耦).
    失败仅告警, 不阻断删除流程 (业务表已清理).
    """
    try:
        from src.memory.db_initializer import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            # checkpoints 表: 按 thread_id 删除
            await conn.execute(
                "DELETE FROM checkpoints WHERE thread_id = $1",
                session_id,
            )
            # writes 表: 按 thread_id 删除
            await conn.execute(
                "DELETE FROM writes WHERE thread_id = $1",
                session_id,
            )
            # migration tracking 表通常不含 thread_id, 跳过
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "清理 Checkpointer 失败 (不阻断, 业务表已清理, session=%s): %s",
            session_id,
            e,
        )


async def _cleanup_redis_cache(agent_id: str, user_id: str, session_id: str) -> None:
    """清理 Redis 中该会话的缓存数据.

    Redis 键格式: {agent_id}:{user_id}:{module}:{type}:{id}
    按 {agent_id}:{user_id}:{session_id}* 模式扫描并删除.
    失败仅告警, 不阻断删除流程.
    """
    try:
        from src.common.redis_client import get_redis_client

        client = await get_redis_client()
        if client is None:
            return

        # 按前缀模式扫描 (SCAN 非阻塞, 不影响 Redis 性能)
        pattern = f"{agent_id}:{user_id}:*{session_id}*"
        deleted_count = 0
        async for key in client.scan_iter(match=pattern, count=100):
            await client.delete(key)
            deleted_count += 1

        # 同时清理以 session_id 开头的键 (部分模块可能用 session_id 做前缀)
        pattern2 = f"{agent_id}:{user_id}:{session_id}:*"
        async for key in client.scan_iter(match=pattern2, count=100):
            await client.delete(key)
            deleted_count += 1

        if deleted_count > 0:
            logger.info(
                "Redis 缓存已清理: session_id=%s, 删除 %d 个键",
                session_id,
                deleted_count,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "清理 Redis 缓存失败 (不阻断, session=%s): %s",
            session_id,
            e,
        )


__all__ = ["router"]
