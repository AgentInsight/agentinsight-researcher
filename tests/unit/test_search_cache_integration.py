"""单元测试: Redis 缓存 + ResearchConductor 集成测试.

验证 src/common/redis_client.py 与 src/skills/researcher/research_conductor.py 的集成:
- _cached_search 与 Redis 客户端的集成 (读缓存 → 命中跳过搜索 / 未命中搜索后写缓存)
- 缓存 key 格式 {agent_id}:{user_id}:search:result:{engine}:{query_hash}
- 多搜索引擎并行搜索的缓存命中 (相同 query+engine 第二次命中缓存)
- 缓存 TTL 过期后重新搜索 (Redis 返回 None → 重新调用 searcher)
- Redis 连接失败时不阻断搜索 (get_redis_client 返回 None → 直接搜索, 不写缓存)

AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
所有外部依赖 (Redis / Searchers / LLM / ContextManager) 全部 mock.
AGENTS.md 第 7 章: Redis 键应加前缀 {agent_id}:{user_id}:, 应设 TTL.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.skills.researcher.research_conductor import ResearchConductor

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture(autouse=True)
def _reset_redis_singleton() -> None:
    """每个用例前后重置 Redis 模块级 _client 单例 (避免用例间污染)."""
    from src.common import redis_client as redis_mod

    redis_mod._client = None
    yield
    redis_mod._client = None


@pytest.fixture()
def settings() -> Settings:
    """构造最小 Settings (跳过 .env 加载, mcp_strategy=disabled 避免 MCP mock)."""
    return Settings(
        _env_file=None,
        mcp_strategy="disabled",
        agent_name="test-agent",
        search_cache_ttl=300,
    )


@pytest.fixture()
def mock_llm() -> MagicMock:
    """构造 mock LLMClient."""
    llm = MagicMock()
    llm.achat = AsyncMock()
    return llm


@pytest.fixture()
def mock_context_manager() -> MagicMock:
    """构造 mock ContextManager."""
    cm = MagicMock()
    cm.get_similar_content = AsyncMock(return_value="compressed context")
    return cm


@pytest.fixture()
def mock_prompt_family() -> MagicMock:
    """构造 mock PromptFamily."""
    pf = MagicMock()
    pf.planner_prompt.return_value = "test planner prompt"
    return pf


@pytest.fixture()
def conductor(
    settings: Settings,
    mock_llm: MagicMock,
    mock_context_manager: MagicMock,
    mock_prompt_family: MagicMock,
) -> ResearchConductor:
    """构造 ResearchConductor (依赖全部 mock)."""
    return ResearchConductor(
        settings=settings,
        llm=mock_llm,
        context_manager=mock_context_manager,
        prompt_family=mock_prompt_family,
    )


def _make_searcher(name: str, results: list[dict[str, Any]] | None = None) -> MagicMock:
    """构造 mock BaseSearcher (含 name 属性 + search AsyncMock)."""
    searcher = MagicMock()
    searcher.name = name
    searcher.search = AsyncMock(return_value=results if results is not None else [])
    searcher.close = AsyncMock()
    return searcher


def _make_mock_redis(*, cached_value: str | None = None) -> MagicMock:
    """构造 mock aioredis.Redis (get/setex/ping 为 AsyncMock)."""
    r = MagicMock()
    r.get = AsyncMock(return_value=cached_value)
    r.setex = AsyncMock()
    r.ping = AsyncMock()
    return r


# ========== TestCachedSearchRedisIntegration: _cached_search 与 Redis 集成 ==========


class TestCachedSearchRedisIntegration:
    """验证 _cached_search 与 Redis 客户端的读/写集成."""

    async def test_cache_miss_searches_and_writes_cache(
        self,
        conductor: ResearchConductor,
        settings: Settings,
    ) -> None:
        """缓存未命中: 调用 searcher.search, 结果写入 Redis (setex with TTL)."""
        searcher = _make_searcher("bocha", [{"url": "https://a.com", "title": "A"}])
        mock_redis = _make_mock_redis(cached_value=None)

        with patch(
            "src.skills.researcher.research_conductor.get_redis_client",
            return_value=mock_redis,
        ):
            result = await conductor._cached_search(
                searcher,
                "测试查询",
                max_results=5,
                query_domains=None,
                user_id="user-1",
            )

        searcher.search.assert_awaited_once()
        assert result == [{"url": "https://a.com", "title": "A"}]
        # 验证写入缓存 (setex 带 TTL)
        mock_redis.setex.assert_awaited_once()
        setex_args = mock_redis.setex.call_args
        assert setex_args[0][1] == settings.search_cache_ttl, "setex 第二参数应为 search_cache_ttl"

    async def test_cache_hit_skips_search(
        self,
        conductor: ResearchConductor,
    ) -> None:
        """缓存命中: 直接返回缓存结果, 不调用 searcher.search."""
        cached = [{"url": "https://cached.com", "title": "Cached"}]
        searcher = _make_searcher("bocha")
        mock_redis = _make_mock_redis(cached_value=json.dumps(cached, ensure_ascii=False))

        with patch(
            "src.skills.researcher.research_conductor.get_redis_client",
            return_value=mock_redis,
        ):
            result = await conductor._cached_search(
                searcher,
                "测试查询",
                max_results=5,
                query_domains=None,
                user_id="user-1",
            )

        searcher.search.assert_not_awaited()
        assert result == cached

    async def test_cache_key_format(
        self,
        conductor: ResearchConductor,
        settings: Settings,
    ) -> None:
        """缓存 key 格式: {agent_id}:{user_id}:search:result:{engine}:{query_hash}."""
        searcher = _make_searcher("tavily", [{"url": "https://t.com", "title": "T"}])
        mock_redis = _make_mock_redis(cached_value=None)
        query = "格式验证查询"
        query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
        expected_key = f"{settings.agent_name}:user-1:search:result:tavily:{query_hash}"

        with patch(
            "src.skills.researcher.research_conductor.get_redis_client",
            return_value=mock_redis,
        ):
            await conductor._cached_search(
                searcher,
                query,
                max_results=5,
                query_domains=None,
                user_id="user-1",
            )

        actual_key = mock_redis.get.call_args[0][0]
        assert actual_key == expected_key, f"缓存 key 格式不符: {actual_key}"
        # 写入缓存的 key 也应一致
        write_key = mock_redis.setex.call_args[0][0]
        assert write_key == expected_key

    async def test_cache_key_anonymous_user(
        self,
        conductor: ResearchConductor,
        settings: Settings,
    ) -> None:
        """user_id=None 时, 缓存 key 使用 'anonymous' 占位."""
        searcher = _make_searcher("bocha")
        mock_redis = _make_mock_redis(cached_value=None)
        query = "匿名查询"
        query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
        expected_key = f"{settings.agent_name}:anonymous:search:result:bocha:{query_hash}"

        with patch(
            "src.skills.researcher.research_conductor.get_redis_client",
            return_value=mock_redis,
        ):
            await conductor._cached_search(
                searcher,
                query,
                max_results=5,
                query_domains=None,
                user_id=None,
            )

        actual_key = mock_redis.get.call_args[0][0]
        assert actual_key == expected_key


# ========== TestMultipleEnginesCacheHit: 多搜索引擎并行搜索缓存命中 ==========


class TestMultipleEnginesCacheHit:
    """验证多搜索引擎并行搜索时缓存命中行为."""

    async def test_multiple_engines_same_query_separate_cache_keys(
        self,
        conductor: ResearchConductor,
    ) -> None:
        """2 个搜索引擎相同 query: cache key 按 engine 区分, 互不干扰."""
        searcher_a = _make_searcher("bocha", [{"url": "https://a.com"}])
        searcher_b = _make_searcher("tavily", [{"url": "https://b.com"}])
        mock_redis = _make_mock_redis(cached_value=None)
        captured_keys: list[str] = []

        async def _capture_get(key: str) -> None:
            captured_keys.append(key)
            return None

        mock_redis.get = AsyncMock(side_effect=_capture_get)

        with patch(
            "src.skills.researcher.research_conductor.get_redis_client",
            return_value=mock_redis,
        ):
            import asyncio

            results = await asyncio.gather(
                conductor._cached_search(
                    searcher_a, "共享查询", max_results=5, query_domains=None, user_id="u1"
                ),
                conductor._cached_search(
                    searcher_b, "共享查询", max_results=5, query_domains=None, user_id="u1"
                ),
            )

        # 两个 engine 均调用 search (缓存未命中)
        searcher_a.search.assert_awaited_once()
        searcher_b.search.assert_awaited_once()
        # cache key 各不同 (engine 名区分)
        assert len(captured_keys) == 2
        assert captured_keys[0] != captured_keys[1]
        assert "bocha" in captured_keys[0]
        assert "tavily" in captured_keys[1]
        # 结果各自独立
        assert results[0] == [{"url": "https://a.com"}]
        assert results[1] == [{"url": "https://b.com"}]

    async def test_same_engine_same_query_second_call_hits_cache(
        self,
        conductor: ResearchConductor,
    ) -> None:
        """同一 engine 同一 query: 第二次调用命中缓存, searcher.search 仅调用一次."""
        cached = [{"url": "https://cached.com", "title": "Cached"}]
        searcher = _make_searcher("bocha", [{"url": "https://fresh.com", "title": "Fresh"}])
        # 第二次 get 返回缓存 (模拟第一次写入后第二次命中)
        call_count = 0

        async def _get_side_effect(key: str) -> str | None:
            nonlocal call_count
            call_count += 1
            return json.dumps(cached, ensure_ascii=False) if call_count > 1 else None

        mock_redis = _make_mock_redis()
        mock_redis.get = AsyncMock(side_effect=_get_side_effect)

        with patch(
            "src.skills.researcher.research_conductor.get_redis_client",
            return_value=mock_redis,
        ):
            first = await conductor._cached_search(
                searcher, "重复查询", max_results=5, query_domains=None, user_id="u1"
            )
            second = await conductor._cached_search(
                searcher, "重复查询", max_results=5, query_domains=None, user_id="u1"
            )

        # 第一次未命中 → 调用 search; 第二次命中 → 不调用 search
        assert searcher.search.await_count == 1
        assert first == [{"url": "https://fresh.com", "title": "Fresh"}]
        assert second == cached


# ========== TestCacheTtlExpiry: 缓存 TTL 过期后重新搜索 ==========


class TestCacheTtlExpiry:
    """验证缓存 TTL 过期后的重新搜索行为."""

    async def test_cache_expired_re_searches(
        self,
        conductor: ResearchConductor,
    ) -> None:
        """Redis 返回 None (TTL 过期/无缓存) → 重新调用 searcher.search."""
        searcher = _make_searcher("bocha", [{"url": "https://re-search.com"}])
        mock_redis = _make_mock_redis(cached_value=None)

        with patch(
            "src.skills.researcher.research_conductor.get_redis_client",
            return_value=mock_redis,
        ):
            result = await conductor._cached_search(
                searcher,
                "过期查询",
                max_results=5,
                query_domains=None,
                user_id="u1",
            )

        searcher.search.assert_awaited_once()
        assert result == [{"url": "https://re-search.com"}]
        # 过期后应重新写入缓存
        mock_redis.setex.assert_awaited_once()

    async def test_empty_result_not_cached(
        self,
        conductor: ResearchConductor,
    ) -> None:
        """搜索返回空结果 → 不写入缓存 (仅缓存非空结果)."""
        searcher = _make_searcher("bocha", [])
        mock_redis = _make_mock_redis(cached_value=None)

        with patch(
            "src.skills.researcher.research_conductor.get_redis_client",
            return_value=mock_redis,
        ):
            result = await conductor._cached_search(
                searcher,
                "空结果查询",
                max_results=5,
                query_domains=None,
                user_id="u1",
            )

        assert result == []
        searcher.search.assert_awaited_once()
        mock_redis.setex.assert_not_awaited()


# ========== TestRedisUnavailableFallback: Redis 连接失败不阻断搜索 ==========


class TestRedisUnavailableFallback:
    """验证 Redis 不可用时的降级策略 (不阻断搜索)."""

    async def test_redis_none_falls_back_to_direct_search(
        self,
        conductor: ResearchConductor,
    ) -> None:
        """get_redis_client 返回 None → 直接搜索, 不读/写缓存."""
        searcher = _make_searcher("bocha", [{"url": "https://direct.com"}])

        with patch(
            "src.skills.researcher.research_conductor.get_redis_client",
            return_value=None,
        ):
            result = await conductor._cached_search(
                searcher,
                "无缓存查询",
                max_results=5,
                query_domains=None,
                user_id="u1",
            )

        searcher.search.assert_awaited_once()
        assert result == [{"url": "https://direct.com"}]

    async def test_redis_get_error_falls_back_to_search(
        self,
        conductor: ResearchConductor,
    ) -> None:
        """Redis get 异常 → 降级直接搜索 (不阻断)."""
        searcher = _make_searcher("bocha", [{"url": "https://fallback.com"}])
        mock_redis = _make_mock_redis()
        mock_redis.get = AsyncMock(side_effect=RuntimeError("redis connection lost"))

        with patch(
            "src.skills.researcher.research_conductor.get_redis_client",
            return_value=mock_redis,
        ):
            result = await conductor._cached_search(
                searcher,
                "异常查询",
                max_results=5,
                query_domains=None,
                user_id="u1",
            )

        searcher.search.assert_awaited_once()
        assert result == [{"url": "https://fallback.com"}]

    async def test_redis_setex_error_does_not_block(
        self,
        conductor: ResearchConductor,
    ) -> None:
        """Redis setex 异常 → 不阻断返回结果 (仅告警)."""
        searcher = _make_searcher("bocha", [{"url": "https://ok.com"}])
        mock_redis = _make_mock_redis(cached_value=None)
        mock_redis.setex = AsyncMock(side_effect=RuntimeError("redis write failed"))

        with patch(
            "src.skills.researcher.research_conductor.get_redis_client",
            return_value=mock_redis,
        ):
            result = await conductor._cached_search(
                searcher,
                "写入异常查询",
                max_results=5,
                query_domains=None,
                user_id="u1",
            )

        # 搜索正常完成, 结果正确返回
        searcher.search.assert_awaited_once()
        assert result == [{"url": "https://ok.com"}]
