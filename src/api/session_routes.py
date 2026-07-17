"""会话管理 API 路由 (以 UserId 为单位的会话持久化).

端点列表:
- GET    /v1/sessions                          列出当前用户会话
- GET    /v1/sessions/latest                   获取最近会话
- GET    /v1/sessions/{session_id}/messages    获取会话消息 (分页, 滚动加载)
- GET    /v1/sessions/{session_id}/config      获取会话报告配置
- POST   /v1/sessions                          创建新会话
- DELETE /v1/sessions/{session_id}             删除会话 (级联清理)
- PATCH  /v1/sessions/{session_id}             更新会话标题
- PUT    /v1/sessions/{session_id}/config      更新会话报告配置

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


class UpdateReportConfigRequest(BaseModel):
    """更新报告配置请求."""

    report_type: str | None = Field(
        None, description="报告类型: basic_report | detailed_report | deep_research"
    )
    report_format: str | None = Field(
        None, description="输出格式: markdown | html | pdf | docx | json"
    )
    language: str | None = Field(None, description="报告语言: zh | en")


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

    限制: 每用户每智能体最多 max_sessions_per_user 个会话 (默认 10)
    """
    user_id, agent_id = _get_user_agent()
    session_id = request.session_id or generate_session_id()
    title = request.title or ""

    # 会话数上限检查 (每用户每智能体)
    store = get_session_store()
    existing_sessions = await store.list_sessions(agent_id, user_id, limit=200)
    from src.config.settings import get_settings
    max_sessions = get_settings().max_sessions_per_user
    if len(existing_sessions) >= max_sessions:
        raise HTTPException(
            status_code=429,
            detail=f"每用户每智能体最多创建 {max_sessions} 个会话，请先删除不用的会话",
        )

    # 获取客户端 IP (审计追溯用, 从 contextvars 恢复)
    from src.api.middleware import get_request_client_ip

    client_ip = get_request_client_ip()

    await store.create_session(session_id, agent_id, user_id, title=title, client_ip=client_ip)

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
    4. Redis 缓存 (no-op, 依赖 TTL 自然过期)
    5. 会话级内存数据结构 (LLMClient._session_costs / token_budget._allocators /
       QueryIntentClassifier._inflight_locks, 防止内存泄漏 P0-2/P0-3/P0-4)

    注: Redis 键不含 session_id 维度, 依赖 TTL 自然过期, 无需按 session 清理.

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

    # 3: 清理 LangGraph Checkpointer (按 thread_id 删除 checkpoints/checkpoint_writes)
    await _cleanup_checkpointer(session_id, agent_id)

    # 4: Redis 缓存 — 当前 Redis 键不含 session_id 维度, 依赖 TTL 自然过期, 无需按 session 清理.
    #    保留 _cleanup_redis_cache 调用为 no-op, 仅为兼容测试 mock; 实际不执行任何删除.
    await _cleanup_redis_cache(agent_id, user_id, session_id)

    # 5: 清理会话级内存数据结构 (per-session 字典/锁, 防止内存泄漏 P0-2/P0-3/P0-4)
    await _cleanup_session_memory(session_id)

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


@router.get("/{session_id}/config")
async def get_report_config(session_id: str) -> dict[str, Any]:
    """获取会话的报告配置 (report_type/report_format/language).

    返回: {"session_id", "report_type", "report_format", "language"}
    """
    user_id, agent_id = _get_user_agent()
    store = get_session_store()
    session = await store.get_session(session_id, agent_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")
    return {
        "session_id": session_id,
        "report_type": session.get("report_type") or "detailed_report",
        "report_format": session.get("report_format") or "markdown",
        "language": session.get("language") or "zh",
    }


@router.put("/{session_id}/config")
async def update_report_config(
    session_id: str, request: UpdateReportConfigRequest
) -> dict[str, Any]:
    """更新会话的报告配置 (report_type/report_format/language).

    请求体: {"report_type": "...", "report_format": "...", "language": "..."}
    所有字段可选, 传 null/省略则保持原值.

    返回: {"session_id", "updated": true}
    """
    user_id, agent_id = _get_user_agent()
    store = get_session_store()

    # 校验会话归属当前用户
    session = await store.get_session(session_id, agent_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")

    updated = await store.update_report_config(
        session_id,
        agent_id,
        user_id,
        report_type=request.report_type,
        report_format=request.report_format,
        language=request.language,
    )
    if not updated:
        raise HTTPException(status_code=500, detail="更新失败")

    return {"session_id": session_id, "updated": True}


# ========== 级联清理辅助函数 ==========


async def _cleanup_checkpointer(session_id: str, agent_id: str | None = None) -> None:
    """清理 LangGraph Checkpointer 中该会话的 checkpoint 数据.

    LangGraph PostgresSaver 的 checkpoints/checkpoint_writes 表按 thread_id 隔离,
    直接通过 asyncpg 删除 (不依赖 LangGraph SDK 的删除方法, 保持解耦).
    失败仅告警, 不阻断删除流程 (业务表已清理).

    thread_id 格式: f"{agent_id}:{session_id}" (多 Agent 命名空间隔离, 见 routes.py).
    """
    try:
        from src.config.settings import get_settings
        from src.memory.db_initializer import get_pool

        # thread_id 加 agent_id 前缀 (与 routes.py 的 thread_id 构造保持一致)
        effective_agent_id = agent_id or get_settings().agent_name
        thread_id = f"{effective_agent_id}:{session_id}"

        pool = await get_pool()
        async with pool.acquire() as conn:
            # checkpoints 表: 按 thread_id 删除
            await conn.execute(
                "DELETE FROM checkpoints WHERE thread_id = $1",
                thread_id,
            )
            # checkpoint_writes 表: 按 thread_id 删除 (注意表名带 checkpoint_ 前缀)
            await conn.execute(
                "DELETE FROM checkpoint_writes WHERE thread_id = $1",
                thread_id,
            )
            # migration tracking 表通常不含 thread_id, 跳过
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "清理 Checkpointer 失败 (不阻断, 业务表已清理, session=%s): %s",
            session_id,
            e,
        )


async def _cleanup_redis_cache(agent_id: str, user_id: str, session_id: str) -> None:
    """清理 Redis 中该会话的缓存数据 (已废弃, no-op).

    当前 Redis 键格式为 {agent_id}:{user_id}:{module}:{type}:{id}, 不含 session_id 维度,
    无法按 session 精确清理. Redis 缓存依赖 TTL 自然过期, 无需按 session 主动清理.

    保留函数签名仅为兼容现有测试 mock (test_api_session_routes.py), 实际不执行任何删除.
    """
    # no-op: Redis 键不含 session_id 维度, 依赖 TTL 自然过期, 无需按 session 清理.
    logger.debug(
        "Redis 清理跳过 (键不含 session_id 维度, 依赖 TTL 自然过期): session_id=%s",
        session_id,
    )


async def _cleanup_session_memory(session_id: str) -> None:
    """清理会话级内存数据结构 (per-session 字典/锁, 防止内存泄漏).

    清理三处会话级内存 (修复 P0-2/P0-3/P0-4 内存泄漏):
    1. LLMClient._session_costs — per-session 成本追踪字典 (cleanup_session_cost)
    2. token_budget._allocators — per-session TokenBudgetAllocator 实例 (cleanup_token_budget_allocator)
    3. QueryIntentClassifier._inflight_locks — singleflight 互斥锁字典 (cleanup_inflight_locks)

    失败仅告警, 不阻断删除流程 (业务表+Checkpointer 已清理).
    """
    try:
        from src.llm.client import get_llm_client
        from src.llm.token_budget import cleanup_token_budget_allocator
        from src.skills.researcher.query_classifier import get_query_intent_classifier

        # P0-2: 清理 per-session 成本追踪字典
        get_llm_client().cleanup_session_cost(session_id)
        # P0-3: 清理 per-session TokenBudgetAllocator 字典
        await cleanup_token_budget_allocator(session_id)
        # P0-4: 清理 singleflight 互斥锁字典
        get_query_intent_classifier().cleanup_inflight_locks(session_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "清理会话级内存失败 (不阻断, session=%s): %s",
            session_id,
            e,
        )


__all__ = ["router"]
