"""agentinsight-researcher FastAPI 入口.

API 入口, JWT 中间件, OpenAI 兼容端点, 前端测试页面.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from src.api.agent_discovery import router as discovery_router
from src.api.mcp_routes import router as mcp_router
from src.api.middleware import (
    JWTAuthMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
    close_jwt_middleware,
)
from src.api.routes import router as api_router
from src.api.session_routes import router as session_router
from src.common.exceptions import AgentError
from src.config.settings import get_settings

logger = logging.getLogger(__name__)

# 后台任务引用保留 (防止 GC 静默取消 asyncio.create_task)
_background_tasks: set[asyncio.Task[None]] = set()


def _create_background_task(coro: object) -> asyncio.Task[None]:
    """创建后台任务并保留引用 (防止 GC 静默取消).

    标准模式: set + done_callback(discard), 任务完成后自动从集合移除.
    """
    task: asyncio.Task[None] = asyncio.create_task(coro)  # type: ignore[arg-type]
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期: 启动时初始化, 关闭时清理."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger.info("agentinsight-researcher 启动中 (env=%s)", settings.env)

    # 启动时初始化业务数据:
    # PostgreSQL 业务表由 Agent 启动时触发 (幂等)
    # 失败不阻断启动 (仅告警), depends_on service_healthy 已保证依赖就绪
    # 注: 行业适配采用 4 层机制 (Prompt/Config/Retriever/MCP), 不再 bootstrap GICS 行业知识库
    from src.memory.db_initializer import init_database

    await init_database(settings)

    # 显式同步 ensure Qdrant 集合
    # 必须同步等待完成, 避免新环境首请求竞态 (后台任务可能在集合未就绪时被查询)
    # 失败不阻断启动 (仅告警), 后续业务方法自保 _ensure_collection_once 兜底
    async def _ensure_qdrant_collection() -> None:
        try:
            from src.rag.qdrant_manager import get_qdrant_manager

            mgr = get_qdrant_manager()
            await mgr.ensure_collection()  # 幂等, 不存在则创建
            logger.info("Qdrant 集合 %s 已就绪", mgr.settings.qdrant_collection)
        except Exception as e:  # noqa: BLE001
            logger.warning("Qdrant ensure_collection 失败 (不阻断启动): %s", e)

    await _ensure_qdrant_collection()  # 同步等待, 确保集合就绪后再启动

    # LangGraph 图预热 (后台任务触发首次构建, 首次请求直接复用单例)
    # 见下方 _warmup_graph() 后台任务, 消除首次请求 20-50ms 编译开销

    # 启动时一次性清理 Qdrant 上遗留的短查询/离题种子命名空间数据
    # QUERY_CLASSIFIER_FAST_LLM_OPTIMIZATION_PLAN.md 实施后, 第二层 Embeddings+Qdrant 语义匹配
    # 已移除, 原种子数据不再使用; 启动时清理一次避免残留 (幂等, Qdrant 不可用仅告警)
    async def _cleanup_legacy_chat_seeds() -> None:
        try:
            from src.skills.researcher.query_classifier import cleanup_legacy_chat_seeds

            await cleanup_legacy_chat_seeds()
        except Exception as e:  # noqa: BLE001
            logger.warning("Qdrant 旧种子清理失败 (不阻断启动): %s", e)

    _create_background_task(_cleanup_legacy_chat_seeds())

    # Embeddings 批量预热 (后台执行, 不阻塞启动)
    # 触发 TEI 模型加载, 避免首次真实调用冷启动; 失败不阻断启动
    async def _warmup_embeddings() -> None:
        try:
            from src.rag.embeddings import warmup_embeddings

            await warmup_embeddings()
        except Exception as e:  # noqa: BLE001
            logger.warning("Embeddings 预热失败 (不阻断): %s", e)

    _create_background_task(_warmup_embeddings())

    # FastEmbed 模型预热 (消除首次调用 10s+ 冷启动延迟)
    # 触发 ONNX 模型加载 + ONNX Runtime 线程初始化, 避免首次请求冷启动; 失败不阻断启动
    async def _warmup_fastembed() -> None:
        try:
            from src.rag.fastembed_client import get_fastembed_client

            client = get_fastembed_client()
            await client.embed_texts(["预热"])
            logger.info("FastEmbed 模型预热完成 (ONNX Runtime 已初始化)")
        except Exception as e:  # noqa: BLE001
            logger.warning("FastEmbed 预热失败 (不阻断): %s", e)

    _create_background_task(_warmup_fastembed())

    # 全局单图编译 (启动时预热, 首次请求直接复用, 消除 20-50ms 编译开销)
    # 复用 routes._get_graph() 全局单例 (懒加载), 后台触发首次构建; 失败不阻断启动
    # 单例机制已在 src/api/routes.py 实现 (_compiled_graph + _get_graph), 这里仅预热
    async def _warmup_graph() -> None:
        try:
            from src.api.routes import _get_graph

            await _get_graph()  # 触发首次构建并存入全局单例
            logger.info("LangGraph 研究图已预热 (全局单例, QPS 预期 +44%%)")
        except Exception as e:  # noqa: BLE001
            logger.warning("图预热失败 (不阻断启动, 首次请求时重试): %s", e)

    _create_background_task(_warmup_graph())

    yield

    # 关闭全局 Redis 单例 (由 common.redis_client 统一工厂创建, lifespan 统一关闭)
    from src.common.redis_client import close_redis_client

    await close_redis_client()

    # 关闭 WebSocket token 验证模块级 httpx client 单例
    from src.api.websocket import close_verify_client

    await close_verify_client()

    # 关闭全局 Playwright 浏览器池单例 (复用 browser, 避免每 URL 启动 chromium)
    from src.skills.researcher.scrapers.playwright_scraper import _PlaywrightPool

    await _PlaywrightPool.shutdown()

    # 关闭共享 httpx.AsyncClient 单例 (scraper 复用 TCP 连接池)
    # 释放底层 TCP 连接池, 避免依赖进程退出回收; 幂等 (无实例时直接返回)
    from src.skills.researcher.scrapers import close_shared_http_client

    await close_shared_http_client()

    # 关闭 JWTAuthMiddleware 的 httpx.AsyncClient (纯 ASGI middleware 无法从 app 获取实例)
    await close_jwt_middleware()

    # 关闭 asyncpg 业务表连接池 (优雅 shutdown, 避免连接泄漏)
    from src.memory.db_initializer import close_pool

    await close_pool()

    # 关闭 Checkpointer 的 psycopg 连接池 (优雅 shutdown)
    from src.memory.checkpointer import close_checkpointer_pool

    await close_checkpointer_pool()

    logger.info("agentinsight-researcher 关闭")


def create_app() -> FastAPI:
    """创建 FastAPI 应用."""
    settings = get_settings()

    app = FastAPI(
        title="agentinsight-researcher",
        description="中文优先的研究分析智能体",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.env == "dev" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.env == "dev" else None,
    )

    # CORS (* 限制已移除)
    allow_credentials = "*" not in settings.cors_allow_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=allow_credentials,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # JWT 身份解析中间件
    app.add_middleware(JWTAuthMiddleware, settings=settings)

    # 安全响应头中间件 (不可绕过)
    app.add_middleware(SecurityHeadersMiddleware)

    # 统一请求追踪 ID 中间件 (纯 ASGI, 注入 X-Request-ID)
    app.add_middleware(RequestIDMiddleware)

    # 健康检查 (容器健康检查端点)
    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "service": "agentinsight-researcher", "version": "0.1.0"},
        )

    # OpenAI 兼容端点
    app.include_router(api_router)

    # MCP 配置管理端点 (前端 MCP 配置 + Postgres 持久化)
    app.include_router(mcp_router)

    # Agent Discovery Protocol 公开发现端点 (无需鉴权)
    app.include_router(discovery_router)

    # 会话管理端点 (以 UserId 为单位的会话持久化: 列表/创建/删除/消息分页)
    app.include_router(session_router)

    # WebSocket 双向实时通信端点
    # SSE 仍是主通道, WebSocket 是增强通道 (人在回路审核请求 + 实时进度)
    if settings.websocket_enabled:
        from src.api.websocket import router as ws_router

        app.include_router(ws_router)

    # 全局异常处理器 (结构化 JSON 错误响应, 符合 OpenAI 兼容 API 规范)
    @app.exception_handler(AgentError)
    async def agent_error_handler(request: Request, exc: AgentError) -> JSONResponse:
        """捕获 Agent 系统自定义异常, 返回结构化错误响应."""
        logger.warning(
            "AgentError: %s (code=%s, http_status=%s)",
            exc.message,
            exc.code,
            exc.http_status,
        )
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": {
                    "message": exc.message,
                    "type": exc.__class__.__name__,
                    "code": exc.code,
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        """捕获所有未处理异常, 返回 500 JSON (避免 Starlette 默认格式泄露堆栈)."""
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "message": "Internal Server Error",
                    "type": "internal_error",
                    "code": "internal_error",
                }
            },
        )

    # 前端测试页面
    if settings.enable_test_page:
        static_dir = Path(__file__).parent / "static"
        if static_dir.exists():
            app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8066,
        reload=False,
        log_level=get_settings().log_level.lower(),
    )
