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

    # 阶段 2: 初始化 LangGraph 图 (延迟到首次请求构建, 避免启动时连 Postgres)
    # 阶段 3: 可预热图

    # 启动时预热短查询种子向量到 Qdrant (P0-Future-05/06)
    # 后台异步执行, 不阻塞启动; 失败降级为仅规则层 (AGENTS.md 第 7 章)
    # P1-Future-07: 同时预热离题/闲聊种子 (off_topic_patterns namespace)
    async def _preheat_short_query_seeds() -> None:
        try:
            from src.skills.researcher.query_classifier import (
                get_query_intent_classifier,
            )

            classifier = get_query_intent_classifier()
            # 并行预热短查询 + 离题种子 (两者独立, 互不阻断)
            await asyncio.gather(
                classifier._ensure_seed_patterns(),
                classifier._ensure_off_topic_seed_patterns(),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("种子预热失败 (降级为仅规则层): %s", e)

    asyncio.create_task(_preheat_short_query_seeds())

    # P0-03: Embeddings 批量预热 (后台执行, 不阻塞启动)
    # 触发 TEI 模型加载, 避免首次真实调用冷启动; 失败不阻断启动
    async def _warmup_embeddings() -> None:
        try:
            from src.rag.embeddings import warmup_embeddings

            await warmup_embeddings()
        except Exception as e:  # noqa: BLE001
            logger.warning("Embeddings 预热失败 (不阻断): %s", e)

    asyncio.create_task(_warmup_embeddings())

    yield

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

    # CORS (AGENTS.md 第 11 章, 禁 *)
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
