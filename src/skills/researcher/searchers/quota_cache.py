"""搜索引擎额度缓存 (Redis 实现, v1.1 新增).

当引擎返回 HTTP 429 (频率限制) 或 402 (付费额度已满) 时,
将其标记为不可用并写入 Redis, TTL 根据额度时限自动过期 (最高 24 小时).

缓存 key 格式: {agent_id}:_global:searcher:quota:{engine_name}
缓存 value: JSON {"engine": "metaso", "reset_at": "2026-07-06T00:00:00Z", "reason": "429"}

用户需求 6 (v1.1): 调用时如果额度已满，将其放入缓存中标识不可用，
                  下次调用则忽略，缓存时间根据额度时限，最高为 24 小时。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import orjson

from src.common.redis_client import get_redis_client
from src.config.settings import Settings

logger = logging.getLogger(__name__)

# 最高缓存 24 小时 (用户需求硬上限)
MAX_CACHE_TTL_SECONDS = 24 * 3600


class QuotaCache:
    """搜索引擎额度缓存 (Redis 实现, 异步)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._redis: Any | None = None
        # redis_url 优先从 settings 读取, 不存在则禁用
        # 字段已在 Settings 中声明, 直接访问 (消除 getattr 防御式编程)
        self._redis_url = settings.redis_url or None
        self._enabled = bool(self._redis_url)

    async def _get_redis(self) -> Any | None:
        """惰性初始化 Redis 连接 (复用 common.redis_client 全局单例)."""
        if not self._enabled:
            return None
        if self._redis is not None:
            return self._redis
        # 复用全局单例 (password/encoding/max_connections 由 get_redis_client 统一处理)
        self._redis = await get_redis_client(self._settings)
        if self._redis is None:
            self._enabled = False
            return None
        return self._redis

    def _cache_key(self, engine: str) -> str:
        """生成缓存 key.

        遵循 Redis 约定：
          {agent_id}:{user_id}:{module}:{type}:{id}
        此处为搜索引擎全局级缓存 (不区分用户)，使用固定前缀。
        """
        agent_id = self._settings.agent_name or "agentinsight-researcher"
        return f"{agent_id}:_global:searcher:quota:{engine}"

    @staticmethod
    def _calc_ttl(reset_at: datetime) -> int:
        """计算 TTL (秒)，最高 24 小时.

        Args:
            reset_at: 额度重置时间 (UTC)

        Returns:
            TTL 秒数，范围 [60, 86400]
        """
        now = datetime.now(UTC)
        if reset_at.tzinfo is None:
            reset_at = reset_at.replace(tzinfo=UTC)
        delta = (reset_at - now).total_seconds()
        # 限制范围：最小 60 秒 (避免立即过期)，最大 24 小时 (硬上限)
        ttl = int(max(60, min(delta, MAX_CACHE_TTL_SECONDS)))
        return ttl

    async def mark_exceeded(
        self,
        engine: str,
        reset_at: datetime,
        reason: str = "429",
    ) -> None:
        """标记引擎额度已满.

        Args:
            engine: 引擎名称
            reset_at: 额度重置时间 (UTC)
            reason: 原因 ("429" / "402" / "quota_exceeded")
        """
        r = await self._get_redis()
        if r is None:
            logger.debug(f"QuotaCache 禁用，跳过标记 {engine}")
            return

        ttl = self._calc_ttl(reset_at)
        cache_key = self._cache_key(engine)
        cache_value = orjson.dumps(
            {
                "engine": engine,
                "reset_at": reset_at.isoformat(),
                "reason": reason,
                "marked_at": datetime.now(UTC).isoformat(),
            },
        )

        try:
            await r.set(cache_key, cache_value, ex=ttl)
            logger.info(
                f"QuotaCache 标记 {engine} 不可用，TTL={ttl}s，reset_at={reset_at.isoformat()}"
            )
        except Exception as e:
            logger.warning(f"QuotaCache 标记 {engine} 失败: {e}")

    async def is_exceeded(self, engine: str) -> bool:
        """检查引擎是否在额度缓存中 (不可用).

        Args:
            engine: 引擎名称

        Returns:
            True 表示额度已满，应跳过；False 表示可调用
        """
        r = await self._get_redis()
        if r is None:
            return False

        try:
            value = await r.get(self._cache_key(engine))
            if value is None:
                return False
            data: dict[str, Any] = orjson.loads(value)
            logger.debug(f"QuotaCache 命中 {engine} 不可用，reset_at={data.get('reset_at')}")
            return True
        except Exception as e:
            logger.warning(f"QuotaCache 查询 {engine} 失败: {e}")
            return False

    async def clear(self, engine: str) -> None:
        """手动清除某引擎的额度缓存 (管理员/测试用)."""
        r = await self._get_redis()
        if r is None:
            return
        try:
            await r.delete(self._cache_key(engine))
            logger.info(f"QuotaCache 清除 {engine} 缓存")
        except Exception as e:
            logger.warning(f"QuotaCache 清除 {engine} 失败: {e}")

    async def list_exceeded(self) -> list[dict[str, Any]]:
        """列出当前所有额度已满的引擎 (监控用).

        使用 SCAN 迭代器替代 KEYS 命令, 避免 O(N) 阻塞 Redis.
        """
        r = await self._get_redis()
        if r is None:
            return []
        try:
            agent_id = self._settings.agent_name or "agentinsight-researcher"
            pattern = f"{agent_id}:_global:searcher:quota:*"
            # SCAN 迭代器, count=100 平衡 RTT 与阻塞时间
            keys: list[str] = []
            async for key in r.scan_iter(match=pattern, count=100):
                keys.append(key)
            result: list[dict[str, Any]] = []
            for key in keys:
                value = await r.get(key)
                if value:
                    result.append(orjson.loads(value))
            return result
        except Exception as e:
            logger.warning(f"QuotaCache 列出失败: {e}")
            return []
