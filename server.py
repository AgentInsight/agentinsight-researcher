"""agentinsight-researcher FastAPI 入口.

对标 AgentInsightService server.py 模式.
AGENTS.md 第 3/8/14 章: API 入口, JWT 中间件, OpenAI 兼容端点, 前端测试页面.

阶段 2: 集成中间件 + OpenAI 兼容端点骨架 + 图构建器初始化.
阶段 3: 接入完整研究流水线.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.api.middleware import JWTAuthMiddleware, SecurityHeadersMiddleware
from src.api.routes import router as api_router
from src.config.settings import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期: 启动时初始化, 关闭时清理."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger.info("agentinsight-researcher 启动中 (env=%s)", settings.env)

    # 启动时初始化业务数据 (用户需求):
    # 1. PostgreSQL 业务表 (原 Docker 构建时执行, 现改为 Agent 启动时触发, 幂等)
    # 2. GICS 行业知识库 (读取 industry_prompts/*.yaml, embedding 后 upsert 到 Qdrant)
    # 两者均失败不阻断启动 (仅告警), depends_on service_healthy 已保证依赖就绪
    from src.memory.db_initializer import init_database
    from src.rag.knowledge_bootstrap import bootstrap_industry_knowledge

    await init_database(settings)
    await bootstrap_industry_knowledge(settings)

    # 阶段 2: 初始化 LangGraph 图 (延迟到首次请求构建, 避免启动时连 Postgres)
    # 阶段 3: 可预热图

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
