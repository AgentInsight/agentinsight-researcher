"""HTTP 客户端连接池统一管理 (P2-18).

集中管理 httpx.AsyncClient 实例, 避免各搜索器/抓取器各自创建导致连接数膨胀
(~400-500 MB 内存浪费).

架构边界: common/ 不依赖 agents/ 或业务模块. SSL 上下文 (certifi CA 包) 在本模块
独立实现, 逻辑与 src/skills/researcher/scrapers/__init__.py 的 get_ssl_context 一致,
避免 common/ 反向依赖 skills/.

生命周期:
- get_client(name): 获取/创建命名客户端 (惰性创建, asyncio.Lock 保护)
- close_all(): 关闭所有客户端 (研究完成后调用)
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import ssl

    import httpx

logger = logging.getLogger(__name__)

# ========== 模块级单例 ==========
_pool: HttpClientPool | None = None
_pool_lock: asyncio.Lock | None = None


def _get_ssl_context() -> ssl.SSLContext:
    """获取 SSL 上下文 (certifi CA 包, 独立实现避免依赖业务模块).

    与 scrapers/__init__.py 的 get_ssl_context 逻辑一致:
    - 优先使用 certifi.where() 指定 Mozilla CA 包
    - certifi 未安装时回退系统 CA 存并告警
    - 不使用 verify=False (安全硬约束)
    """
    import ssl

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        logger.warning(
            "certifi 未安装, 回退系统 CA 存 (可能触发 CERTIFICATE_VERIFY_FAILED); "
            "建议 pip install certifi>=2024.2.2",
        )
        return ssl.create_default_context()


class HttpClientPool:
    """httpx.AsyncClient 连接池 (单例).

    通过命名客户端复用 TCP 连接, 避免各搜索器/抓取器各自创建 httpx.AsyncClient
    导致连接数膨胀.

    配置 (所有命名客户端共用):
    - max_connections=50
    - max_keepalive_connections=20
    - keepalive_expiry=30s
    - timeout=Timeout(30.0, connect=10.0)
    - SSL: certifi CA 包

    线程安全: asyncio.Lock 保护客户端创建与关闭.
    """

    _clients: dict[str, httpx.AsyncClient]
    _lock: asyncio.Lock

    def __init__(self) -> None:
        self._clients = {}
        self._lock = asyncio.Lock()

    async def get_client(self, name: str = "default") -> httpx.AsyncClient:
        """获取命名客户端 (惰性创建, 双重检查锁定).

        相同 name 返回同一客户端实例, 复用 TCP 连接池.

        Args:
            name: 客户端名称 (按搜索器/模块命名, 如 "bocha"/"metaso"/"exa"/"searxng").

        Returns:
            httpx.AsyncClient 实例.
        """
        # 快速路径: 已存在直接返回 (无锁)
        client = self._clients.get(name)
        if client is not None:
            return client

        async with self._lock:
            # 双重检查锁定, 防止并发首次创建
            client = self._clients.get(name)
            if client is not None:
                return client

            import httpx

            client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(
                    max_connections=50,
                    max_keepalive_connections=20,
                    keepalive_expiry=30.0,
                ),
                verify=_get_ssl_context(),
            )
            self._clients[name] = client
            logger.debug("HttpClientPool 创建命名客户端: %s", name)
            return client

    async def close_all(self) -> None:
        """关闭所有客户端 (研究完成后调用).

        幂等: 多次调用安全. 关闭后清空 _clients, 后续 get_client 会重建.
        在锁外执行 aclose, 避免长时间持锁.
        """
        async with self._lock:
            if not self._clients:
                return
            clients = list(self._clients.values())
            self._clients.clear()

        closed = 0
        for client in clients:
            try:
                await client.aclose()
                closed += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("关闭 httpx.AsyncClient 失败 (不阻断): %s", e)
        if closed:
            logger.debug("HttpClientPool 已关闭 %d 个客户端", closed)


async def get_http_client_pool() -> HttpClientPool:
    """获取 HttpClientPool 单例 (asyncio.Lock 保护初始化).

    Returns:
        HttpClientPool 单例实例.
    """
    global _pool, _pool_lock

    if _pool is not None:
        return _pool

    # 锁惰性初始化 (避免模块导入时创建事件循环绑定)
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()

    async with _pool_lock:
        # 双重检查锁定, 防止并发首次创建
        if _pool is not None:
            return _pool

        _pool = HttpClientPool()
        return _pool
