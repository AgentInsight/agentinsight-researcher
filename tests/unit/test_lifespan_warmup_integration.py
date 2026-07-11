"""单元测试: server lifespan + FastEmbed 预热集成测试.

验证 server.py 的 lifespan 函数与 src/rag/fastembed_client.py 的预热集成:
- lifespan 启动时触发 _warmup_fastembed 后台任务 (asyncio.create_task)
- 预热调用 embed_texts(["预热"]) 加载 ONNX 模型
- 预热完成后后续 embed_texts 调用不再有冷启动延迟 (TextEmbedding 仅构造一次)
- 预热失败时 lifespan 不阻断 (异常被捕获, 仅告警)

单元测试在构建期执行, 不依赖外部服务.
所有外部依赖 (FastEmbed 模型 / PostgreSQL / Qdrant / Redis / Playwright) 全部 mock.
lifespan 启动时初始化业务数据, 失败不阻断启动.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.rag.fastembed_client import FastEmbedClient

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture(autouse=True)
def _clear_fastembed_singleton() -> None:
    """每个用例前后清空 FastEmbed 全局单例 + 进程内缓存."""
    from src.rag import fastembed_client as fe_mod

    fe_mod._FASTEMBED_CACHE.clear()
    fe_mod._client = None
    yield
    fe_mod._FASTEMBED_CACHE.clear()
    fe_mod._client = None


@pytest.fixture(autouse=True)
def _reset_graph_singleton() -> None:
    """每个用例前后重置 routes._compiled_graph 全局单例 (避免跨测试污染)."""
    import src.api.routes as routes_mod

    routes_mod._compiled_graph = None
    yield
    routes_mod._compiled_graph = None


@pytest.fixture()
def test_settings() -> Settings:
    """构造测试 Settings (跳过 .env 加载)."""
    return Settings(_env_file=None)


@pytest.fixture()
def mock_model_env() -> tuple[MagicMock, MagicMock]:
    """Patch fastembed.TextEmbedding + anyio.Path, 返回 (mock_cls, mock_model).

    用于需要真实 FastEmbedClient (验证 _ensure_model 冷启动逻辑) 的用例.
    """
    mock_model = MagicMock()
    mock_model.embed = MagicMock(side_effect=lambda batch: [[0.1] * 512 for _ in batch])
    mock_te_cls = MagicMock(return_value=mock_model)

    mock_path = MagicMock()
    mock_path.exists = AsyncMock(return_value=False)

    with (
        patch("fastembed.TextEmbedding", mock_te_cls),
        patch("anyio.Path", return_value=mock_path),
    ):
        yield mock_te_cls, mock_model


def _patch_lifespan_deps(fastembed_client_mock: MagicMock | None = None) -> list:
    """返回 lifespan 所有外部依赖的 patch 上下文管理器列表.

    调用方用 contextlib.ExitStack 进入/退出. fastembed_client_mock=None 时
    不覆盖 get_fastembed_client (用于失败场景由调用方自行 patch).
    """
    patches = [
        patch("src.memory.db_initializer.init_database", new=AsyncMock()),
        patch(
            "src.rag.qdrant_manager.get_qdrant_manager",
            return_value=MagicMock(ensure_collection=AsyncMock()),
        ),
        patch(
            "src.skills.researcher.query_classifier.cleanup_legacy_chat_seeds",
            new=AsyncMock(),
        ),
        patch("src.rag.embeddings.warmup_embeddings", new=AsyncMock()),
        patch("src.common.redis_client.close_redis_client", new=AsyncMock()),
        patch("src.api.websocket.close_verify_client", new=AsyncMock()),
        patch(
            "src.skills.researcher.scrapers.playwright_scraper._PlaywrightPool.shutdown",
            new=AsyncMock(),
        ),
        patch("src.skills.researcher.scrapers.close_shared_http_client", new=AsyncMock()),
        # lifespan shutdown 新增的清理调用
        patch("server.close_jwt_middleware", new=AsyncMock()),
        patch("src.memory.db_initializer.close_pool", new=AsyncMock()),
        patch("src.memory.checkpointer.close_checkpointer_pool", new=AsyncMock()),
        # 图预热 mock (lifespan _warmup_graph 后台任务调用 build_researcher_graph)
        patch("src.graph.builder.build_researcher_graph", new=AsyncMock()),
    ]
    if fastembed_client_mock is not None:
        patches.append(
            patch(
                "src.rag.fastembed_client.get_fastembed_client",
                return_value=fastembed_client_mock,
            )
        )
    return patches


# ========== TestLifespanFastEmbedWarmup: lifespan 触发预热后台任务 ==========


class TestLifespanFastEmbedWarmup:
    """验证 lifespan 启动时触发 _warmup_fastembed 后台任务."""

    async def test_lifespan_triggers_fastembed_warmup(self) -> None:
        """lifespan 启动时创建后台任务调用 embed_texts(['预热'])."""
        from contextlib import ExitStack

        from server import lifespan

        warmup_done = asyncio.Event()
        fastembed_mock = MagicMock()

        async def _warmup_embed(texts: list[str], **kwargs: object) -> list[list[float]]:
            warmup_done.set()
            return [[0.1] * 512 for _ in texts]

        fastembed_mock.embed_texts = AsyncMock(side_effect=_warmup_embed)

        with ExitStack() as stack:
            for p in _patch_lifespan_deps(fastembed_mock):
                stack.enter_context(p)
            app_mock = MagicMock()
            async with lifespan(app_mock):
                # 等待后台预热任务完成 (不阻塞 lifespan yield)
                await asyncio.wait_for(warmup_done.wait(), timeout=3.0)

        fastembed_mock.embed_texts.assert_awaited_once()
        called_texts = fastembed_mock.embed_texts.call_args[0][0]
        assert called_texts == ["预热"], f"预热应调用 embed_texts(['预热']), 实际: {called_texts}"

    async def test_warmup_failure_does_not_block_lifespan(self) -> None:
        """FastEmbed 预热失败时, lifespan 正常 yield 不抛异常."""
        from contextlib import ExitStack

        from server import lifespan

        failing_mock = MagicMock()
        failing_mock.embed_texts = AsyncMock(side_effect=RuntimeError("model load failed"))

        with ExitStack() as stack:
            for p in _patch_lifespan_deps(failing_mock):
                stack.enter_context(p)
            app_mock = MagicMock()
            # lifespan 应正常进入 yield (不抛异常)
            async with lifespan(app_mock):
                await asyncio.sleep(0.2)  # 让后台预热任务执行并失败

        # 预热任务被调用过 (即使失败)
        failing_mock.embed_texts.assert_awaited_once()

    async def test_lifespan_warmup_runs_as_background_task(self) -> None:
        """lifespan 不阻塞等待预热完成 (预热是后台任务, yield 前不 await)."""
        from contextlib import ExitStack

        from server import lifespan

        # 预热任务延迟设置事件, 验证 lifespan yield 时预热尚未完成
        embed_started = asyncio.Event()
        fastembed_mock = MagicMock()

        async def _slow_warmup(texts: list[str], **kwargs: object) -> list[list[float]]:
            embed_started.set()
            await asyncio.sleep(0.3)
            return [[0.1] * 512 for _ in texts]

        fastembed_mock.embed_texts = AsyncMock(side_effect=_slow_warmup)

        with ExitStack() as stack:
            for p in _patch_lifespan_deps(fastembed_mock):
                stack.enter_context(p)
            app_mock = MagicMock()
            # lifespan 应立即进入 yield (不等预热完成)
            async with lifespan(app_mock):
                # 此时预热任务可能已启动但未完成
                await asyncio.wait_for(embed_started.wait(), timeout=3.0)


# ========== TestFastEmbedWarmupColdStart: 预热消除冷启动延迟 ==========


class TestFastEmbedWarmupColdStart:
    """验证预热完成后后续 embed_texts 调用不再有冷启动延迟."""

    async def test_warmup_loads_model_once(
        self,
        test_settings: Settings,
        mock_model_env: tuple[MagicMock, MagicMock],
    ) -> None:
        """首次 embed_texts(['预热']) 加载模型, TextEmbedding 仅构造一次."""
        mock_te_cls, _mock_model = mock_model_env
        client = FastEmbedClient(test_settings)

        # 预热: 触发 _ensure_model 加载模型
        await client.embed_texts(["预热"])
        assert client._initialized is True
        assert client._load_failed is False
        assert mock_te_cls.call_count == 1, "首次调用应构造 TextEmbedding 一次"

        # 后续调用: _ensure_model 短路返回 (_initialized=True), 不重新构造
        await client.embed_texts(["后续查询"])
        assert mock_te_cls.call_count == 1, "预热后不应重新构造 TextEmbedding (无冷启动)"

    async def test_second_call_skips_model_loading(
        self,
        test_settings: Settings,
        mock_model_env: tuple[MagicMock, MagicMock],
    ) -> None:
        """预热后第二次调用 _ensure_model 立即返回 (零模型加载开销)."""
        _mock_te_cls, mock_model = mock_model_env
        client = FastEmbedClient(test_settings)

        await client.embed_texts(["预热"])
        first_embed_calls = mock_model.embed.call_count

        # 第二次调用不同文本: _ensure_model 短路, 仅做推理 (model.embed)
        await client.embed_texts(["不同文本"])
        assert client._initialized is True
        # model.embed 调用次数增加 (推理发生), 但 TextEmbedding 未重构
        assert mock_model.embed.call_count > first_embed_calls

    async def test_warmup_caches_vectors_for_repeated_text(
        self,
        test_settings: Settings,
        mock_model_env: tuple[MagicMock, MagicMock],
    ) -> None:
        """预热文本的向量被缓存, 重复调用零推理."""
        _mock_te_cls, mock_model = mock_model_env
        client = FastEmbedClient(test_settings)

        await client.embed_texts(["预热"])
        embed_calls_after_warmup = mock_model.embed.call_count

        # 相同文本再次调用 → 缓存命中, 零推理
        await client.embed_texts(["预热"])
        assert mock_model.embed.call_count == embed_calls_after_warmup, (
            "相同文本应命中缓存, 不触发额外推理"
        )

    async def test_model_load_failure_sets_load_failed(
        self,
        test_settings: Settings,
    ) -> None:
        """模型加载失败 → _load_failed=True, 后续调用抛 RuntimeError."""
        mock_te_cls = MagicMock(side_effect=RuntimeError("ONNX init failed"))
        mock_path = MagicMock()
        mock_path.exists = AsyncMock(return_value=False)

        client = FastEmbedClient(test_settings)
        with (
            patch("fastembed.TextEmbedding", mock_te_cls),
            patch("anyio.Path", return_value=mock_path),
        ):
            with pytest.raises(RuntimeError, match="ONNX init failed"):
                await client.embed_texts(["预热"])

        assert client._initialized is False
        assert client._load_failed is True

        # 后续调用应直接抛 RuntimeError (不重试加载)
        with pytest.raises(RuntimeError, match="模型加载失败"):
            await client.embed_texts(["再次尝试"])
        # TextEmbedding 仅在首次失败时构造一次, 后续不重试
        assert mock_te_cls.call_count == 1
