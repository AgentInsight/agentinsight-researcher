"""agentinsight-researcher FastAPI 入口.

对标 AgentInsightService server.py 模式.
AGENTS.md 第 3/8/14 章: API 入口, JWT 中间件, OpenAI 兼容端点, 前端测试页面.

阶段 2: 集成中间件 + OpenAI 兼容端点骨架 + 图构建器初始化.
阶段 3: 接入完整研究流水线.
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

from src.api.agent_discovery import router as discovery_router
from src.api.mcp_routes import router as mcp_router
from src.api.middleware import JWTAuthMiddleware, SecurityHeadersMiddleware
from src.api.routes import router as api_router
from src.config.settings import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期: 启动时初始化, 关闭时清理."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger.info("agentinsight-researcher 启动中 (env=%s)", settings.env)

    # 启动时初始化业务数据 (AGENTS.md 第 6 章):
    # PostgreSQL 业务表 (原 Docker 构建时执行, 现改为 Agent 启动时触发, 幂等)
    # 失败不阻断启动 (仅告警), depends_on service_healthy 已保证依赖就绪
    # 注: 行业适配采用 GPTR 风格 4 层机制 (Prompt/Config/Retriever/MCP), 不再 bootstrap GICS 行业知识库
    from src.memory.db_initializer import init_database

    await init_database(settings)

    # P0-修复1: 显式同步 ensure Qdrant 集合 (AGENTS.md 第 6/7 章, 用户首要需求)
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

    # 阶段 2: 初始化 LangGraph 图 (延迟到首次请求构建, 避免启动时连 Postgres)
    # 阶段 3: 可预热图

    # P2 清理: 启动时一次性清理 Qdrant 上遗留的短查询/离题种子命名空间数据
    # QUERY_CLASSIFIER_FAST_LLM_OPTIMIZATION_PLAN.md 实施后, 第二层 Embeddings+Qdrant 语义匹配
    # 已移除, 原种子数据不再使用; 启动时清理一次避免残留 (幂等, Qdrant 不可用仅告警)
    async def _cleanup_legacy_chat_seeds() -> None:
        try:
            from src.skills.researcher.query_classifier import cleanup_legacy_chat_seeds

            await cleanup_legacy_chat_seeds()
        except Exception as e:  # noqa: BLE001
            logger.warning("Qdrant 旧种子清理失败 (不阻断启动): %s", e)

    asyncio.create_task(_cleanup_legacy_chat_seeds())

    # P0-03: Embeddings 批量预热 (后台执行, 不阻塞启动)
    # 触发 TEI 模型加载, 避免首次真实调用冷启动; 失败不阻断启动
    async def _warmup_embeddings() -> None:
        try:
            from src.rag.embeddings import warmup_embeddings

            await warmup_embeddings()
        except Exception as e:  # noqa: BLE001
            logger.warning("Embeddings 预热失败 (不阻断): %s", e)

    asyncio.create_task(_warmup_embeddings())

    # P1: FastEmbed 模型预热 (trace 4ad14970 优化, 消除首次调用 10s+ 冷启动延迟)
    # 触发 ONNX 模型加载 + ONNX Runtime 线程初始化, 避免首次请求冷启动; 失败不阻断启动
    async def _warmup_fastembed() -> None:
        try:
            from src.rag.fastembed_client import get_fastembed_client

            client = get_fastembed_client()
            await client.embed_texts(["预热"])
            logger.info("FastEmbed 模型预热完成 (ONNX Runtime 已初始化)")
        except Exception as e:  # noqa: BLE001
            logger.warning("FastEmbed 预热失败 (不阻断): %s", e)

    asyncio.create_task(_warmup_fastembed())

    yield

    # P0-5: 关闭全局 Redis 单例 (由 common.redis_client 统一工厂创建, lifespan 统一关闭)
    from src.common.redis_client import close_redis_client

    await close_redis_client()

    # P0-9: 关闭 WebSocket token 验证模块级 httpx client 单例
    from src.api.websocket import close_verify_client

    await close_verify_client()

    # P0-6: 关闭全局 Playwright 浏览器池单例 (复用 browser, 避免每 URL 启动 chromium)
    from src.skills.researcher.scrapers.playwright_scraper import _PlaywrightPool

    await _PlaywrightPool.shutdown()

    # P0-7: 关闭共享 httpx.AsyncClient 单例 (scraper 复用 TCP 连接池, P1-3)
    # 释放底层 TCP 连接池, 避免依赖进程退出回收; 幂等 (无实例时直接返回)
    from src.skills.researcher.scrapers import close_shared_http_client

    await close_shared_http_client()

    logger.info("agentinsight-researcher 关闭")


def create_app() -> FastAPI:
    """创建 FastAPI 应用."""
    settings = get_settings()

    app = FastAPI(
        title="agentinsight-researcher",
        description="中文优先的研究分析智能体, 对标 GPT Researcher",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.env == "dev" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.env == "dev" else None,
    )

    # CORS (AGENTS.md 第 11 章, * 限制已移除)
    allow_credentials = "*" not in settings.cors_allow_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=allow_credentials,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # JWT 身份解析中间件 (AGENTS.md 第 8 章)
    app.add_middleware(JWTAuthMiddleware, settings=settings)

    # 安全响应头中间件 (AGENTS.md 第 11 章, 不可绕过)
    app.add_middleware(SecurityHeadersMiddleware)

    # 健康检查 (AGENTS.md 第 12 章, 容器健康检查端点)
    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "service": "agentinsight-researcher", "version": "0.1.0"},
        )

    # OpenAI 兼容端点 (AGENTS.md 第 14 章)
    app.include_router(api_router)

    # MCP 配置管理端点 (任务7: 前端 MCP 配置 + Postgres 持久化)
    app.include_router(mcp_router)

    # Agent Discovery Protocol 公开发现端点 (P1-Future-03, 无需鉴权)
    app.include_router(discovery_router)

    # WebSocket 双向实时通信端点 (P2-Future-02, AGENTS.md 第 14 章允许端点)
    # SSE 仍是主通道, WebSocket 是增强通道 (人在回路审核请求 + 实时进度)
    if settings.websocket_enabled:
        from src.api.websocket import router as ws_router

        app.include_router(ws_router)

    # 前端测试页面 (AGENTS.md 第 14 章)
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
