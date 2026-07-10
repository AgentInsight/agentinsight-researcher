"""性能测试: 搜索结果 Redis 缓存性能 (8 项优化之搜索缓存).

AGENTS.md 第 7/13 章硬约束:
- Redis 键应加前缀 {agent_id}:{user_id}:
- 搜索缓存 TTL=300s (5 分钟), 相同 query+engine 5min TTL
- 性能测试以单元测试为主 (mock + time.perf_counter), 不依赖容器栈

覆盖 trace 4ad14970 优化项:
1. 搜索结果 Redis 缓存命中场景的延迟对比
2. 缓存 key 生成性能 (sha256)
3. TTL=300s 过期后重新搜索

执行方式:
    pytest tests/performance/test_search_cache_perf.py -v -m performance -s
"""

from __future__ import annotations

import hashlib
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.skills.researcher.research_conductor import ResearchConductor

pytestmark = pytest.mark.performance


def _make_settings(**overrides: object) -> Settings:
    """构造测试 Settings (跳过 .env 加载)."""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


class _FakeSearcher:
    """模拟搜索引擎 (带可控延迟)."""

    name: str = "test_engine"

    def __init__(self, search_delay_ms: float = 50.0) -> None:
        self._search_delay_ms = search_delay_ms
        self.call_count = 0

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        self.call_count += 1
        # 模拟搜索网络延迟 (用 asyncio.sleep 避免阻塞事件循环, ruff ASYNC251)
        import asyncio

        await asyncio.sleep(self._search_delay_ms / 1000.0)
        return [
            {
                "title": f"搜索结果 {i} for {query}",
                "url": f"https://example.com/{i}",
                "snippet": f"这是关于 {query} 的搜索结果 {i}",
                "source": self.name,
                "region": "cn",
            }
            for i in range(max_results)
        ]


class _FakeRedis:
    """模拟 Redis 客户端 (内存存储 + TTL 模拟)."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float]] = {}  # key → (value, expire_at)
        self.get_count = 0
        self.setex_count = 0

    async def get(self, key: str) -> str | None:
        self.get_count += 1
        if key in self._store:
            value, expire_at = self._store[key]
            if time.time() < expire_at:
                return value
            del self._store[key]
        return None

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.setex_count += 1
        self._store[key] = (value, time.time() + ttl)

    async def aclose(self) -> None:
        pass

    def fast_forward(self, seconds: float) -> None:
        """快进时间 (模拟 TTL 过期)."""
        now = time.time()
        expired = [k for k, (_, exp) in self._store.items() if exp <= now + seconds]
        for k in expired:
            del self._store[k]


def _make_conductor(settings: Settings) -> ResearchConductor:
    """构造 ResearchConductor (mock LLM/ContextManager 依赖)."""
    mock_llm = MagicMock()
    mock_cm = MagicMock()
    mock_pf = MagicMock()
    return ResearchConductor(
        settings=settings,
        llm=mock_llm,  # type: ignore[arg-type]
        context_manager=mock_cm,  # type: ignore[arg-type]
        prompt_family=mock_pf,  # type: ignore[arg-type]
    )


# ========== 搜索结果 Redis 缓存命中场景的延迟对比 ==========


async def test_cache_hit_vs_miss_latency() -> None:
    """验证搜索结果 Redis 缓存命中 vs 未命中的延迟对比.

    _cached_search 在缓存命中时直接返回 Redis 中的 JSON,
    未命中时调用 searcher.search (含网络延迟).

    阈值: 缓存命中延迟 < 未命中延迟的 20% (跳过搜索网络调用).
    """
    settings = _make_settings(search_cache_ttl=300)
    conductor = _make_conductor(settings)
    fake_redis = _FakeRedis()
    searcher = _FakeSearcher(search_delay_ms=80.0)

    query = "人工智能医疗应用研究"

    with patch(
        "src.skills.researcher.research_conductor.get_redis_client",
        new=AsyncMock(return_value=fake_redis),
    ):
        # 1. 缓存未命中 (首次搜索, 含 searcher.search 延迟)
        start = time.perf_counter()
        result_miss = await conductor._cached_search(
            searcher, query, max_results=5, query_domains=None, user_id="test_user"
        )
        miss_elapsed = time.perf_counter() - start

        # 2. 缓存命中 (第二次相同 query, 直接从 Redis 读)
        start = time.perf_counter()
        result_hit = await conductor._cached_search(
            searcher, query, max_results=5, query_domains=None, user_id="test_user"
        )
        hit_elapsed = time.perf_counter() - start

    assert len(result_miss) == 5, f"未命中应返回 5 条结果, 实际: {len(result_miss)}"
    assert len(result_hit) == 5, f"命中应返回 5 条结果, 实际: {len(result_hit)}"
    assert searcher.call_count == 1, (
        f"缓存命中不应调用 searcher, 实际调用: {searcher.call_count} 次"
    )

    # 缓存命中应远快于未命中
    ratio = hit_elapsed / miss_elapsed if miss_elapsed > 0 else 1.0
    assert ratio < 0.2, (
        f"缓存命中 {hit_elapsed:.4f}s 未远快于未命中 {miss_elapsed:.4f}s "
        f"(比值 {ratio:.2f}x), 缓存效果不明显"
    )
    print(
        f"\n[cache_hit_vs_miss] 未命中={miss_elapsed:.4f}s | 命中={hit_elapsed:.4f}s | "
        f"ratio={ratio:.2f}x"
    )


# ========== 缓存 key 生成性能 (sha256) ==========


async def test_cache_key_generation_performance() -> None:
    """验证缓存 key 生成 (sha256) 性能.

    _cached_search 用 sha256(query) 生成缓存 key.
    10000 次 sha256 应在合理时间内完成 (< 1s).

    阈值: 10000 次 sha256 < 1.0s.
    """
    queries = [f"搜索查询测试 {i} 人工智能医疗深度学习" for i in range(10000)]

    start = time.perf_counter()
    for q in queries:
        _ = hashlib.sha256(q.encode("utf-8")).hexdigest()
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, f"10000 次 sha256 key 生成耗时 {elapsed:.3f}s 超过阈值 1.0s"
    per_op_us = (elapsed / 10000) * 1_000_000
    print(f"\n[cache_key_sha256] 10000 次 = {elapsed:.4f}s | 平均 {per_op_us:.1f}μs/op")


async def test_cache_key_isolation_by_user_and_engine() -> None:
    """验证缓存 key 按 agent_id + user_id + engine 隔离.

    AGENTS.md 第 7 章: Redis 键应加前缀 {agent_id}:{user_id}:.
    不同 user_id 或 engine 的相同 query 应生成不同 cache_key.
    """
    settings = _make_settings(search_cache_ttl=300)
    conductor = _make_conductor(settings)
    fake_redis = _FakeRedis()

    searcher_a = _FakeSearcher()
    searcher_a.name = "engine_a"
    searcher_b = _FakeSearcher()
    searcher_b.name = "engine_b"

    query = "相同查询词"

    with patch(
        "src.skills.researcher.research_conductor.get_redis_client",
        new=AsyncMock(return_value=fake_redis),
    ):
        # user_a + engine_a
        await conductor._cached_search(
            searcher_a, query, max_results=3, query_domains=None, user_id="user_a"
        )
        # user_a + engine_b (不同 engine, 应未命中)
        await conductor._cached_search(
            searcher_b, query, max_results=3, query_domains=None, user_id="user_a"
        )
        # user_b + engine_a (不同 user, 应未命中)
        await conductor._cached_search(
            searcher_a, query, max_results=3, query_domains=None, user_id="user_b"
        )
        # user_a + engine_a (相同, 应命中)
        await conductor._cached_search(
            searcher_a, query, max_results=3, query_domains=None, user_id="user_a"
        )

    # searcher_a 调用 2 次 (user_a 首次 + user_b 首次), 第 4 次命中缓存
    # searcher_b 调用 1 次 (user_a + engine_b)
    assert searcher_a.call_count == 2, (
        f"searcher_a 应调用 2 次 (user_a + user_b 首次), 实际: {searcher_a.call_count}"
    )
    assert searcher_b.call_count == 1, f"searcher_b 应调用 1 次, 实际: {searcher_b.call_count}"


# ========== TTL=300s 过期后重新搜索 ==========


async def test_ttl_expiry_triggers_research() -> None:
    """验证 TTL=300s 过期后缓存失效, 触发重新搜索.

    search_cache_ttl=300 (5 分钟), 过期后 _cached_search 应重新调用 searcher.
    通过 _FakeRedis.fast_forward 模拟时间快进.
    """
    settings = _make_settings(search_cache_ttl=300)
    conductor = _make_conductor(settings)
    fake_redis = _FakeRedis()
    searcher = _FakeSearcher(search_delay_ms=10.0)

    query = "TTL 过期测试查询"

    with patch(
        "src.skills.researcher.research_conductor.get_redis_client",
        new=AsyncMock(return_value=fake_redis),
    ):
        # 1. 首次搜索 (未命中 → 搜索 + 写缓存)
        result_1 = await conductor._cached_search(
            searcher, query, max_results=3, query_domains=None, user_id="test_user"
        )
        assert searcher.call_count == 1, "首次应调用 searcher"

        # 2. 第二次搜索 (命中缓存, 不调用 searcher)
        result_2 = await conductor._cached_search(
            searcher, query, max_results=3, query_domains=None, user_id="test_user"
        )
        assert searcher.call_count == 1, "缓存命中不应调用 searcher"
        assert result_1 == result_2, "缓存结果应与首次一致"

        # 3. 快进 301s (超过 TTL=300s)
        fake_redis.fast_forward(301.0)

        # 4. 第三次搜索 (缓存过期 → 重新搜索)
        result_3 = await conductor._cached_search(
            searcher, query, max_results=3, query_domains=None, user_id="test_user"
        )
        assert searcher.call_count == 2, (
            f"TTL 过期后应重新调用 searcher, 实际调用: {searcher.call_count} 次"
        )
        assert len(result_3) == 3, f"重新搜索应返回 3 条结果, 实际: {len(result_3)}"

    print(f"\n[ttl_expiry] searcher 调用 {searcher.call_count} 次 (首次 + TTL 过期后重新搜索)")


async def test_cache_only_stores_non_empty_results() -> None:
    """验证仅缓存非空结果 (空结果不写缓存).

    _cached_search 第 3 步: "仅缓存非空结果, TTL=5min".
    空结果不写缓存, 避免缓存穿透.
    """
    settings = _make_settings(search_cache_ttl=300)
    conductor = _make_conductor(settings)
    fake_redis = _FakeRedis()

    # searcher 返回空结果
    searcher = _FakeSearcher()
    searcher.search = AsyncMock(return_value=[])  # type: ignore[method-assign]

    query = "空结果查询"

    with patch(
        "src.skills.researcher.research_conductor.get_redis_client",
        new=AsyncMock(return_value=fake_redis),
    ):
        # 首次搜索 (空结果, 不写缓存)
        result_1 = await conductor._cached_search(
            searcher, query, max_results=5, query_domains=None, user_id="test_user"
        )
        assert result_1 == [], "应返回空结果"
        assert fake_redis.setex_count == 0, "空结果不应写入缓存"

        # 第二次搜索 (缓存无数据, 再次调用 searcher)
        await conductor._cached_search(
            searcher, query, max_results=5, query_domains=None, user_id="test_user"
        )
        assert searcher.search.call_count == 2, (
            f"空结果不缓存, 第二次应再次调用 searcher, 实际调用: {searcher.search.call_count} 次"
        )
