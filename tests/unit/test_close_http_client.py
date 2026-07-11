"""单元测试: close_shared_http_client 关闭共享 httpx 客户端.

验证 src/skills/researcher/scrapers/__init__.py 的 close_shared_http_client:
- 关闭已存在的 httpx.AsyncClient 单例 (释放 TCP 连接池)
- 幂等: 无实例时直接返回 (不抛异常)
- 二次调用安全 (关闭后再次调用不报错)
- server.py lifespan shutdown 阶段调用清理

单元测试不依赖外部服务.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.skills.researcher.scrapers import (
    close_shared_http_client,
    get_shared_http_client,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_shared_http_client():
    """每个用例前后重置模块级 _shared_http_client 单例."""
    import src.skills.researcher.scrapers as scrapers_mod

    scrapers_mod._shared_http_client = None
    scrapers_mod._shared_http_client_lock = None
    yield
    scrapers_mod._shared_http_client = None
    scrapers_mod._shared_http_client_lock = None


# ========== close_shared_http_client 行为 ==========


@pytest.mark.asyncio
async def test_close_shared_http_client_idempotent_when_no_instance() -> None:
    """无实例时调用 close → 直接返回, 不抛异常 (幂等)."""
    # _shared_http_client 已是 None (fixture 重置)
    await close_shared_http_client()  # 不抛异常


@pytest.mark.asyncio
async def test_close_shared_http_client_closes_existing_instance() -> None:
    """有实例时调用 close → 调用 aclose() 释放连接池 + 置 None."""
    # 先创建一个 mock 实例
    mock_client = MagicMock()
    mock_client.aclose = AsyncMock()

    import src.skills.researcher.scrapers as scrapers_mod

    scrapers_mod._shared_http_client = mock_client

    await close_shared_http_client()

    mock_client.aclose.assert_awaited_once()
    assert scrapers_mod._shared_http_client is None


@pytest.mark.asyncio
async def test_close_shared_http_client_idempotent_on_double_call() -> None:
    """二次调用 close → 第二次无实例, 安全返回 (幂等)."""
    mock_client = MagicMock()
    mock_client.aclose = AsyncMock()

    import src.skills.researcher.scrapers as scrapers_mod

    scrapers_mod._shared_http_client = mock_client

    await close_shared_http_client()  # 第一次: 关闭 + 置 None
    await close_shared_http_client()  # 第二次: 无实例, 直接返回

    mock_client.aclose.assert_awaited_once()  # 只关闭一次


@pytest.mark.asyncio
async def test_close_shared_http_client_resets_for_reuse() -> None:
    """关闭后再次 get_shared_http_client → 创建新实例 (不复用已关闭的)."""
    # 第一次创建
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client1 = MagicMock()
        mock_client1.aclose = AsyncMock()
        mock_client_cls.return_value = mock_client1

        client1 = await get_shared_http_client()
        assert client1 is mock_client1

        # 关闭
        await close_shared_http_client()

        # 第二次创建 (新实例)
        mock_client2 = MagicMock()
        mock_client_cls.return_value = mock_client2
        client2 = await get_shared_http_client()

        assert client2 is mock_client2
        assert client2 is not client1


# ========== get_shared_http_client 单例 ==========


@pytest.mark.asyncio
async def test_get_shared_http_client_returns_singleton() -> None:
    """get_shared_http_client 多次调用返回同一实例 (复用 TCP 连接池)."""
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        client1 = await get_shared_http_client()
        client2 = await get_shared_http_client()

        assert client1 is client2
        mock_client_cls.assert_called_once()  # 只创建一次


@pytest.mark.asyncio
async def test_get_shared_http_client_concurrent_safe() -> None:
    """并发调用 get_shared_http_client → 只创建一个实例 (双重检查锁)."""
    import asyncio

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        # 并发 5 个调用
        clients = await asyncio.gather(*[get_shared_http_client() for _ in range(5)])

        # 全部返回同一实例
        assert all(c is mock_client for c in clients)
        # 只创建一次 (双重检查锁)
        mock_client_cls.assert_called_once()


# ========== server.py lifespan shutdown 集成 ==========


@pytest.mark.asyncio
async def test_server_lifespan_calls_close_shared_http_client_on_shutdown() -> None:
    """server.py lifespan shutdown 阶段调用 close_shared_http_client.

    释放底层 TCP 连接池, 避免依赖进程退出回收.
    验证 lifespan 上下文管理器退出时调用清理函数.
    """
    # 直接 patch close_shared_http_client, 验证 lifespan 调用它
    with (
        patch(
            "src.skills.researcher.scrapers.close_shared_http_client",
            new=AsyncMock(),
        ) as mock_close,
        patch("src.common.redis_client.close_redis_client", new=AsyncMock()),
        patch("src.api.websocket.close_verify_client", new=AsyncMock()),
        patch("src.skills.researcher.scrapers.playwright_scraper._PlaywrightPool") as mock_pool,
        patch("src.memory.db_initializer.init_database", new=AsyncMock()),
        patch("src.rag.qdrant_manager.get_qdrant_manager") as mock_qdrant_mgr,
        patch(
            "src.skills.researcher.query_classifier.cleanup_legacy_chat_seeds",
            new=AsyncMock(),
        ),
        patch("src.rag.embeddings.warmup_embeddings", new=AsyncMock()),
    ):
        mock_pool.shutdown = AsyncMock()
        mock_qdrant_mgr.return_value.ensure_collection = AsyncMock()

        # 导入 lifespan (在 server.py 模块)
        from fastapi import FastAPI

        from server import lifespan

        app = FastAPI()
        # 进入 lifespan (启动阶段)
        async with lifespan(app):
            pass  # 退出时触发 shutdown

        # close_shared_http_client 应在 shutdown 阶段被调用
        mock_close.assert_awaited_once()
