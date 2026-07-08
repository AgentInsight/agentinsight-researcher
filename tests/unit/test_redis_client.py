"""单元测试: Redis 客户端工厂 (统一 get_redis_client / close_redis_client).

验证 src/common/redis_client.py:
- get_redis_client: URL 为空返回 None / from_url 参数 (encoding/decode_responses/password/
  max_connections/socket_timeout/health_check_interval) / ping 健康检查 / 失败降级 None /
  单例 (双重检查锁) / 并发安全
- close_redis_client: aclose + 置 None / 幂等 / aclose 异常仍重置 / 关闭后重建

AGENTS.md 第 7 章: Redis 键应加前缀 {agent_id}:{user_id}:, 应设 TTL.
  本模块仅负责客户端创建, 键前缀由调用方管理 (retriever.py/query_classifier.py/quota_cache.py).
AGENTS.md 第 13 章: 单元测试不依赖外部服务 (aioredis 全部 mock).

注: 任务描述中 "键前缀格式/TTL/get-set-delete/JSON 序列化" 由调用方实现,
本测试覆盖客户端工厂的创建参数 (encoding/decode_responses 支持字符串键操作)、
降级策略 (连接失败返回 None) 与单例/关闭生命周期.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.common import redis_client as redis_client_mod
from src.common.redis_client import close_redis_client, get_redis_client
from src.config.settings import Settings

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture(autouse=True)
def reset_redis_singleton():
    """每个用例前后重置模块级 _client 单例 (避免用例间污染)."""
    redis_client_mod._client = None
    yield
    redis_client_mod._client = None


def _make_settings(
    *,
    redis_url: str | None = "redis://redis:6379/0",
    redis_auth: str | None = None,
    redis_max_connections: int = 50,
    redis_socket_timeout: float = 5.0,
) -> Settings:
    """构造带 Redis 配置的 Settings."""
    return Settings(
        _env_file=None,
        redis_url=redis_url or "",
        redis_auth=redis_auth,
        redis_max_connections=redis_max_connections,
        redis_socket_timeout=redis_socket_timeout,
    )


def _make_mock_redis() -> MagicMock:
    """构造 mock aioredis.Redis (ping/aclose)."""
    r = MagicMock()
    r.ping = AsyncMock()
    r.aclose = AsyncMock()
    return r


# ========== get_redis_client: URL 为空返回 None ==========


@pytest.mark.asyncio
async def test_get_redis_client_returns_none_when_url_empty() -> None:
    """redis_url 为空 → 返回 None (降级无缓存)."""
    settings = _make_settings(redis_url=None)

    client = await get_redis_client(settings)

    assert client is None


@pytest.mark.asyncio
async def test_get_redis_client_returns_none_when_url_is_empty_string() -> None:
    """redis_url 为空字符串 → 返回 None."""
    settings = _make_settings(redis_url="")

    client = await get_redis_client(settings)

    assert client is None


# ========== get_redis_client: from_url 参数 ==========


@pytest.mark.asyncio
async def test_get_redis_client_creates_client_from_url() -> None:
    """redis_url 非空 → 调用 aioredis.from_url 创建客户端 + ping 健康检查."""
    mock_redis = _make_mock_redis()
    settings = _make_settings(redis_url="redis://redis:6379/0")

    with patch.object(redis_client_mod.aioredis, "from_url", return_value=mock_redis) as mock_from:
        client = await get_redis_client(settings)

    assert client is mock_redis
    mock_from.assert_called_once()
    call_kwargs = mock_from.call_args.kwargs
    assert mock_from.call_args.args[0] == "redis://redis:6379/0"
    # ping 健康检查
    mock_redis.ping.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_redis_client_sets_encoding_utf8_and_decode_responses() -> None:
    """from_url 应传 encoding=utf-8 + decode_responses=True (支持字符串键操作)."""
    mock_redis = _make_mock_redis()
    settings = _make_settings()

    with patch.object(redis_client_mod.aioredis, "from_url", return_value=mock_redis) as mock_from:
        await get_redis_client(settings)

    call_kwargs = mock_from.call_args.kwargs
    assert call_kwargs["encoding"] == "utf-8"
    assert call_kwargs["decode_responses"] is True


@pytest.mark.asyncio
async def test_get_redis_client_passes_password_auth() -> None:
    """redis_auth 非空 → from_url 应传 password 参数."""
    mock_redis = _make_mock_redis()
    settings = _make_settings(redis_auth="my-redis-password")

    with patch.object(redis_client_mod.aioredis, "from_url", return_value=mock_redis) as mock_from:
        await get_redis_client(settings)

    assert mock_from.call_args.kwargs["password"] == "my-redis-password"


@pytest.mark.asyncio
async def test_get_redis_client_passes_max_connections() -> None:
    """redis_max_connections → from_url max_connections 参数."""
    mock_redis = _make_mock_redis()
    settings = _make_settings(redis_max_connections=20)

    with patch.object(redis_client_mod.aioredis, "from_url", return_value=mock_redis) as mock_from:
        await get_redis_client(settings)

    assert mock_from.call_args.kwargs["max_connections"] == 20


@pytest.mark.asyncio
async def test_get_redis_client_passes_socket_timeout() -> None:
    """redis_socket_timeout → from_url socket_timeout 参数."""
    mock_redis = _make_mock_redis()
    settings = _make_settings(redis_socket_timeout=3.0)

    with patch.object(redis_client_mod.aioredis, "from_url", return_value=mock_redis) as mock_from:
        await get_redis_client(settings)

    assert mock_from.call_args.kwargs["socket_timeout"] == 3.0


@pytest.mark.asyncio
async def test_get_redis_client_sets_health_check_interval_30() -> None:
    """from_url 应传 health_check_interval=30 (长连接保活)."""
    mock_redis = _make_mock_redis()
    settings = _make_settings()

    with patch.object(redis_client_mod.aioredis, "from_url", return_value=mock_redis) as mock_from:
        await get_redis_client(settings)

    assert mock_from.call_args.kwargs["health_check_interval"] == 30


# ========== get_redis_client: 失败降级 ==========


@pytest.mark.asyncio
async def test_get_redis_client_returns_none_on_ping_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ping 失败 → 返回 None + 告警 (降级无缓存)."""
    mock_redis = _make_mock_redis()
    mock_redis.ping.side_effect = ConnectionError("redis unreachable")
    settings = _make_settings()

    with (
        patch.object(redis_client_mod.aioredis, "from_url", return_value=mock_redis),
        caplog.at_level("WARNING"),
    ):
        client = await get_redis_client(settings)

    assert client is None
    assert redis_client_mod._client is None
    assert any("Redis" in rec.message or "降级" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_get_redis_client_returns_none_on_from_url_exception() -> None:
    """from_url 抛异常 → 返回 None (降级无缓存)."""
    settings = _make_settings()

    with patch.object(
        redis_client_mod.aioredis, "from_url", side_effect=OSError("bad url")
    ):
        client = await get_redis_client(settings)

    assert client is None
    assert redis_client_mod._client is None


# ========== get_redis_client: 单例 ==========


@pytest.mark.asyncio
async def test_get_redis_client_returns_singleton() -> None:
    """多次调用 get_redis_client → 返回同一实例 (双重检查锁)."""
    mock_redis = _make_mock_redis()
    settings = _make_settings()

    with patch.object(redis_client_mod.aioredis, "from_url", return_value=mock_redis) as mock_from:
        client1 = await get_redis_client(settings)
        client2 = await get_redis_client(settings)

    assert client1 is client2
    assert client1 is mock_redis
    # from_url 只调用一次 (单例)
    mock_from.assert_called_once()


@pytest.mark.asyncio
async def test_get_redis_client_concurrent_safe() -> None:
    """并发调用 get_redis_client → 只创建一个实例 (双重检查锁)."""
    mock_redis = _make_mock_redis()
    settings = _make_settings()

    with patch.object(redis_client_mod.aioredis, "from_url", return_value=mock_redis) as mock_from:
        clients = await asyncio.gather(*[get_redis_client(settings) for _ in range(5)])

    # 全部返回同一实例
    assert all(c is mock_redis for c in clients)
    # from_url 只调用一次
    mock_from.assert_called_once()


# ========== close_redis_client ==========


@pytest.mark.asyncio
async def test_close_redis_client_closes_and_resets() -> None:
    """close → 调用 aclose + 置 None."""
    mock_redis = _make_mock_redis()
    redis_client_mod._client = mock_redis

    await close_redis_client()

    mock_redis.aclose.assert_awaited_once()
    assert redis_client_mod._client is None


@pytest.mark.asyncio
async def test_close_redis_client_idempotent_when_no_instance() -> None:
    """无实例时调用 close → 直接返回, 不抛异常 (幂等)."""
    # _client 已是 None (fixture 重置)
    await close_redis_client()  # 不抛异常


@pytest.mark.asyncio
async def test_close_redis_client_safe_on_aclose_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """aclose 抛异常 → 仍置 None + 告警 (不阻断 lifespan shutdown)."""
    mock_redis = _make_mock_redis()
    mock_redis.aclose.side_effect = RuntimeError("close failed")
    redis_client_mod._client = mock_redis

    with caplog.at_level("WARNING"):
        await close_redis_client()

    assert redis_client_mod._client is None
    assert any("关闭失败" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_get_redis_client_after_close_creates_new_instance() -> None:
    """close 后再 get → 创建新实例 (不复用已关闭的)."""
    mock_redis1 = _make_mock_redis()
    mock_redis2 = _make_mock_redis()
    settings = _make_settings()

    with patch.object(redis_client_mod.aioredis, "from_url", side_effect=[mock_redis1, mock_redis2]):
        client1 = await get_redis_client(settings)
        await close_redis_client()
        client2 = await get_redis_client(settings)

    assert client1 is mock_redis1
    assert client2 is mock_redis2
    assert client2 is not client1
