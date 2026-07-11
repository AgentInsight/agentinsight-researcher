"""单元测试: server.py lifespan 预热逻辑.

覆盖 server.py lifespan 函数中的 FastEmbed 预热行为:
- lifespan 启动时调用 _warmup_fastembed() 后台任务
- 预热失败不阻断启动
- 预热成功日志输出

AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
所有 lifespan 依赖 (PostgreSQL/Qdrant/Redis/Embeddings/FastEmbed) 均通过 mock 替换.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture
def track_create_task(monkeypatch: pytest.MonkeyPatch) -> list:
    """追踪 asyncio.create_task 创建的后台任务.

    lifespan 使用 asyncio.create_task 创建 4 个后台任务:
    - _cleanup_legacy_chat_seeds
    - _warmup_embeddings
    - _warmup_fastembed
    - _warmup_graph (P1-OPT-009: 全局单图编译预热)

    本 fixture 拦截 create_task 调用, 记录任务对象,
    测试中可 await 这些任务确保后台逻辑执行完毕后再验证 mock.
    """
    tasks: list[asyncio.Task] = []
    real_create_task = asyncio.create_task

    def _tracked(coro):
        task = real_create_task(coro)
        tasks.append(task)
        return task

    monkeypatch.setattr("asyncio.create_task", _tracked)
    return tasks


@pytest.fixture
def mock_lifespan_deps(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mock 所有 lifespan 外部依赖, 返回 mock FastEmbed 客户端.

    lifespan 启动时调用的外部服务:
    - init_database (PostgreSQL 业务表初始化)
    - get_qdrant_manager().ensure_collection (Qdrant 集合初始化)
    - cleanup_legacy_chat_seeds (Qdrant 旧种子清理, 后台任务)
    - warmup_embeddings (TEI 预热, 后台任务)
    - get_fastembed_client().embed_texts (FastEmbed 预热, 后台任务)

    lifespan 关闭时调用的外部服务:
    - close_redis_client (Redis 单例关闭)
    - close_verify_client (WebSocket httpx client 关闭)
    - _PlaywrightPool.shutdown (浏览器池关闭)
    - close_shared_http_client (共享 httpx 客户端关闭)
    """
    # 清除 settings 缓存, 确保使用测试环境配置
    from src.config.settings import get_settings

    get_settings.cache_clear()

    # 启动依赖
    monkeypatch.setattr("src.memory.db_initializer.init_database", AsyncMock())

    mock_qdrant_mgr = MagicMock()
    mock_qdrant_mgr.ensure_collection = AsyncMock()
    mock_qdrant_mgr.settings.qdrant_collection = "agents"
    monkeypatch.setattr(
        "src.rag.qdrant_manager.get_qdrant_manager",
        MagicMock(return_value=mock_qdrant_mgr),
    )

    monkeypatch.setattr(
        "src.skills.researcher.query_classifier.cleanup_legacy_chat_seeds",
        AsyncMock(),
    )
    monkeypatch.setattr("src.rag.embeddings.warmup_embeddings", AsyncMock())

    # FastEmbed 预热 mock (返回 mock 客户端供测试验证)
    mock_fe_client = MagicMock()
    mock_fe_client.embed_texts = AsyncMock(return_value=[[0.1, 0.2]])
    monkeypatch.setattr(
        "src.rag.fastembed_client.get_fastembed_client",
        MagicMock(return_value=mock_fe_client),
    )

    # P1-OPT-009: 图预热 mock (lifespan _warmup_graph 后台任务调用 build_researcher_graph)
    # 重置 routes 全局单例, 避免跨测试污染
    import src.api.routes as _routes_mod

    _routes_mod._compiled_graph = None
    mock_graph = object()
    _mock_build_graph = AsyncMock(return_value=mock_graph)
    monkeypatch.setattr("src.graph.builder.build_researcher_graph", _mock_build_graph)

    # 关闭依赖
    monkeypatch.setattr("src.common.redis_client.close_redis_client", AsyncMock())
    monkeypatch.setattr("src.api.websocket.close_verify_client", AsyncMock())
    monkeypatch.setattr(
        "src.skills.researcher.scrapers.playwright_scraper._PlaywrightPool",
        MagicMock(shutdown=AsyncMock()),
    )
    monkeypatch.setattr(
        "src.skills.researcher.scrapers.close_shared_http_client",
        AsyncMock(),
    )
    monkeypatch.setattr("server.close_jwt_middleware", AsyncMock())
    monkeypatch.setattr("src.memory.db_initializer.close_pool", AsyncMock())
    monkeypatch.setattr("src.memory.checkpointer.close_checkpointer_pool", AsyncMock())

    return mock_fe_client


# ========== lifespan 预热测试 ==========


async def test_lifespan_starts_successfully(mock_lifespan_deps, track_create_task) -> None:
    """测试 lifespan 启动成功 (所有依赖 mock 后不抛异常)."""
    from server import lifespan

    # lifespan 应正常启动和关闭, 不抛异常
    async with lifespan(MagicMock()):
        # 等待后台任务完成
        if track_create_task:
            await asyncio.gather(*track_create_task, return_exceptions=True)


async def test_warmup_fastembed_called_on_startup(
    mock_lifespan_deps: MagicMock,
    track_create_task: list,
) -> None:
    """测试 lifespan 启动时调用 FastEmbed 预热 (embed_texts(["预热"]))."""
    from server import lifespan

    async with lifespan(MagicMock()):
        # 等待后台任务执行完毕
        if track_create_task:
            await asyncio.gather(*track_create_task, return_exceptions=True)

    # 验证 FastEmbed embed_texts 被调用, 参数为 ["预热"]
    mock_lifespan_deps.embed_texts.assert_called_once_with(["预热"])


async def test_warmup_fastembed_failure_does_not_block_startup(
    mock_lifespan_deps: MagicMock,
    track_create_task: list,
) -> None:
    """测试 FastEmbed 预热失败不阻断启动 (lifespan 正常完成)."""
    # 覆盖 embed_texts 为抛异常的 mock
    mock_lifespan_deps.embed_texts = AsyncMock(side_effect=RuntimeError("ONNX init failed"))

    from server import lifespan

    # lifespan 应正常启动和关闭, 不因预热失败而抛异常
    async with lifespan(MagicMock()):
        if track_create_task:
            await asyncio.gather(*track_create_task, return_exceptions=True)

    # 验证 embed_texts 确实被调用 (即使失败)
    mock_lifespan_deps.embed_texts.assert_called_once_with(["预热"])


async def test_warmup_fastembed_success_logs(
    mock_lifespan_deps: MagicMock,
    track_create_task: list,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """测试预热成功时输出 "FastEmbed 模型预热完成" 日志."""
    from server import lifespan

    with caplog.at_level(logging.INFO):
        async with lifespan(MagicMock()):
            if track_create_task:
                await asyncio.gather(*track_create_task, return_exceptions=True)

    assert "FastEmbed 模型预热完成" in caplog.text


async def test_warmup_fastembed_failure_logs_warning(
    mock_lifespan_deps: MagicMock,
    track_create_task: list,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """测试预热失败时输出 "FastEmbed 预热失败" 警告日志."""
    mock_lifespan_deps.embed_texts = AsyncMock(side_effect=RuntimeError("warmup error"))

    from server import lifespan

    with caplog.at_level(logging.WARNING):
        async with lifespan(MagicMock()):
            if track_create_task:
                await asyncio.gather(*track_create_task, return_exceptions=True)

    assert "FastEmbed 预热失败" in caplog.text
    assert "不阻断" in caplog.text


async def test_warmup_fastembed_is_background_task(
    mock_lifespan_deps: MagicMock,
    track_create_task: list,
) -> None:
    """测试 _warmup_fastembed 作为后台任务执行 (asyncio.create_task)."""
    from server import lifespan

    async with lifespan(MagicMock()):
        # lifespan 创建了 4 个后台任务:
        # _cleanup_legacy_chat_seeds, _warmup_embeddings, _warmup_fastembed, _warmup_graph
        assert len(track_create_task) == 4

        # 等待所有后台任务完成
        await asyncio.gather(*track_create_task, return_exceptions=True)

    # 验证 FastEmbed 预热确实被调用
    mock_lifespan_deps.embed_texts.assert_called_once_with(["预热"])


# ========== P1-OPT-009: 图预热测试 ==========


async def test_warmup_graph_called_on_startup(
    mock_lifespan_deps: MagicMock,
    track_create_task: list,
) -> None:
    """测试 lifespan 启动时触发图预热 (build_researcher_graph 被调用)."""
    from server import lifespan

    async with lifespan(MagicMock()):
        if track_create_task:
            await asyncio.gather(*track_create_task, return_exceptions=True)

    # 验证 build_researcher_graph 被调用 (图预热触发)
    # mock_lifespan_deps 中 monkeypatch 了 build_researcher_graph
    import src.graph.builder as builder_mod

    assert builder_mod.build_researcher_graph.called, "图预热应调用 build_researcher_graph"


async def test_warmup_graph_success_logs(
    mock_lifespan_deps: MagicMock,
    track_create_task: list,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """测试图预热成功时输出 '研究图已预热' 日志."""
    from server import lifespan

    with caplog.at_level(logging.INFO):
        async with lifespan(MagicMock()):
            if track_create_task:
                await asyncio.gather(*track_create_task, return_exceptions=True)

    assert "研究图已预热" in caplog.text


async def test_warmup_graph_failure_does_not_block_startup(
    mock_lifespan_deps: MagicMock,
    track_create_task: list,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """测试图预热失败不阻断启动 (lifespan 正常完成, 输出警告日志)."""
    # 覆盖 build_researcher_graph 为抛异常的 mock
    import src.graph.builder as builder_mod

    builder_mod.build_researcher_graph = AsyncMock(side_effect=RuntimeError("Postgres 不可用"))

    from server import lifespan

    # lifespan 应正常启动和关闭, 不因图预热失败而抛异常
    with caplog.at_level(logging.WARNING):
        async with lifespan(MagicMock()):
            if track_create_task:
                await asyncio.gather(*track_create_task, return_exceptions=True)

    assert "图预热失败" in caplog.text
    assert "不阻断" in caplog.text
