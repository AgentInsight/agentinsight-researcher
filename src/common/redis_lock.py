"""Redis 分布式锁 (SET NX EX + Lua 原子释放).

用于多 Agent 实例共享 Redis 时, 防止重复拉取 BM25 语料等资源.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import redis.asyncio as aioredis


class RedisDistributedLock:
    """Redis 分布式锁 (SET NX EX + Lua 原子释放).

    用于多 Agent 实例共享 Redis 时, 防止重复拉取 BM25 语料等资源.
    """

    _UNLOCK_SCRIPT = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """

    def __init__(self, client: aioredis.Redis, key: str, ttl: int = 60):
        self._client = client
        self._key = key
        self._ttl = ttl
        self._token = str(uuid.uuid4())

    async def __aenter__(self) -> RedisDistributedLock:
        # 跨进程分布式锁: 其他进程释放锁时本进程 Event 不会被通知, 轮询 sleep 是合理实现
        while not await self._client.set(self._key, self._token, ex=self._ttl, nx=True):  # noqa: ASYNC110
            await asyncio.sleep(0.1)
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._client.eval(self._UNLOCK_SCRIPT, 1, self._key, self._token)


__all__ = ["RedisDistributedLock"]
