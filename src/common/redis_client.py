"""统一 Redis 客户端工厂.

AGENTS.md 第 7 章: Redis 键应加前缀 {agent_id}:{user_id}:
本模块仅负责客户端创建, 键前缀由调用方管理.

3 处调用点 (retriever.py / query_classifier.py / quota_cache.py) 共享同一单例,
参数统一: encoding=utf-8, decode_responses=True, password=redis_auth,
max_connections 走 settings.redis_max_connections.
"""

from __future__ import annotations

import asyncio
import logging

import redis.asyncio as aioredis

from src.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

_client: aioredis.Redis | None = None
_client_lock = asyncio.Lock()


async def get_redis_client(settings: Settings | None = None) -> aioredis.Redis | None:
    """获取全局 Redis 单例 (惰性初始化, 双重检查锁).

    Returns:
        Redis 客户端单例; Redis 未配置或连接失败返回 None (降级无缓存).
    """
    global _client
    if _client is not None:
        return _client
    settings = settings or get_settings()
    # 所有字段均已在 Settings 中声明, 直接访问 (消除 getattr 防御式编程)
    redis_url = settings.redis_url or None
    if not redis_url:
        return None

    async with _client_lock:
        if _client is not None:
            return _client
        try:
            redis_auth = settings.redis_auth or None
            max_connections = settings.redis_max_connections or 10
            socket_timeout = settings.redis_socket_timeout or 5.0
            _client = aioredis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True,
                password=redis_auth,
                max_connections=max_connections,
                socket_timeout=socket_timeout,
                health_check_interval=30,
            )
            await _client.ping()
            logger.info("Redis 客户端已就绪: %s", redis_url)
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis 客户端初始化失败, 降级无缓存: %s", e)
            _client = None
    return _client


async def close_redis_client() -> None:
    """关闭 Redis 客户端 (供 server.py lifespan 关闭时调用)."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis 客户端关闭失败: %s", e)
        _client = None


__all__ = ["get_redis_client", "close_redis_client"]
