"""单元测试: ResearchConductor._cached_search 带缓存的搜索.

验证 src/skills/researcher/research_conductor.py 的 _cached_search 方法:
- Redis 缓存命中场景 (相同 query+engine 返回缓存结果)
- Redis 缓存未命中场景 (调用真实搜索后写入缓存)
- Redis 不可用时降级直接搜索
- 缓存 key 格式: {agent_id}:{user_id}:search:result:{engine}:{query_hash}
- TTL=300s (5min, 取自 settings.search_cache_ttl)
- 仅缓存非空结果
- 缓存读取异常降级
- 缓存写入异常不阻断

单元测试不依赖外部服务 (Redis / Searcher 全部 mock).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.llm.client import LLMClient
from src.skills.researcher.context_manager import ContextManager
from src.skills.researcher.prompts import PromptFamily
from src.skills.researcher.research_conductor import ResearchConductor

pytestmark = pytest.mark.unit


@pytest.fixture()
def settings() -> Settings:
    """构造最小 Settings (跳过 .env 加载, mcp_strategy=disabled 避免 MCP mock)."""
    return Settings(_env_file=None, mcp_strategy="disabled")


@pytest.fixture()
def mock_llm() -> MagicMock:
    """Mock LLMClient (achat 为 AsyncMock, _cached_search 不调用 LLM)."""
    llm = MagicMock(spec=LLMClient)
    llm.achat = AsyncMock()
    return llm


@pytest.fixture()
def mock_context_manager() -> MagicMock:
    """Mock ContextManager."""
    cm = MagicMock(spec=ContextManager)
    cm.get_similar_content = AsyncMock(return_value="")
    return cm


@pytest.fixture()
def mock_prompt_family() -> MagicMock:
    """Mock PromptFamily."""
    pf = MagicMock(spec=PromptFamily)
    return pf


@pytest.fixture()
def conductor(
    settings: Settings,
    mock_llm: MagicMock,
    mock_context_manager: MagicMock,
    mock_prompt_family: MagicMock,
) -> ResearchConductor:
    """构造 ResearchConductor (依赖全部 mock, _cached_search 不触发 LLM/CM)."""
    return ResearchConductor(
        settings=settings,
        llm=mock_llm,
        context_manager=mock_context_manager,
        prompt_family=mock_prompt_family,
    )


@pytest.fixture()
def mock_searcher() -> MagicMock:
    """构造 Mock 搜索引擎 (含 name 属性与 search 异步方法)."""
    searcher = MagicMock()
    searcher.name = "test_engine"
    searcher.search = AsyncMock(return_value=[])
    return searcher


def _make_results(n: int = 2) -> list[dict[str, Any]]:
    """构造 n 条搜索结果."""
    return [
        {"url": f"https://example.com/{i}", "title": f"result{i}", "snippet": f"snippet{i}"}
        for i in range(n)
    ]


def _expected_cache_key(
    query: str,
    engine_name: str,
    agent_id: str,
    user_id: str,
) -> str:
    """计算期望的缓存 key (与 _cached_search 内部逻辑一致)."""
    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
    uid = user_id or "anonymous"
    return f"{agent_id}:{uid}:search:result:{engine_name}:{query_hash}"


# ========== Redis 缓存命中 ==========


@pytest.mark.asyncio
@patch("src.skills.researcher.research_conductor.get_redis_client", new_callable=AsyncMock)
async def test_cached_search_cache_hit_returns_cached(
    mock_get_redis: AsyncMock,
    conductor: ResearchConductor,
    mock_searcher: MagicMock,
    settings: Settings,
) -> None:
    """测试缓存命中时直接返回缓存结果, 不调用 searcher.search.

    场景: Redis 中已存在相同 query+engine 的缓存, _cached_search 应:
    1. 调用 redis.get(cache_key) 读取缓存
    2. 命中则 json.loads 后直接返回
    3. 不调用 searcher.search (跳过真实搜索)
    """
    cached_results = _make_results(3)
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=json.dumps(cached_results, ensure_ascii=False))
    mock_redis.setex = AsyncMock()
    mock_get_redis.return_value = mock_redis

    result = await conductor._cached_search(
        mock_searcher,
        "测试查询",
        max_results=5,
        query_domains=None,
        user_id="user123",
    )

    # 验证返回缓存结果
    assert result == cached_results
    # 验证读取缓存
    mock_redis.get.assert_awaited_once()
    # 验证未调用真实搜索
    mock_searcher.search.assert_not_awaited()
    # 验证未写入缓存 (命中后不写)
    mock_redis.setex.assert_not_awaited()


@pytest.mark.asyncio
@patch("src.skills.researcher.research_conductor.get_redis_client", new_callable=AsyncMock)
async def test_cached_search_cache_hit_same_query_engine(
    mock_get_redis: AsyncMock,
    conductor: ResearchConductor,
    mock_searcher: MagicMock,
) -> None:
    """测试相同 query+engine 第二次调用命中缓存.

    场景: 第一次调用未命中 → 搜索 → 写缓存; 第二次相同 query+engine 命中缓存.
    验证缓存 key 的唯一性由 query + engine 共同决定.
    """
    call_count = 0

    async def mock_get(key: str) -> str | None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None  # 第一次未命中
        return json.dumps(_make_results(2), ensure_ascii=False)  # 第二次命中

    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(side_effect=mock_get)
    mock_redis.setex = AsyncMock()
    mock_get_redis.return_value = mock_redis

    mock_searcher.search = AsyncMock(return_value=_make_results(2))

    # 第一次: 未命中, 执行搜索
    result1 = await conductor._cached_search(
        mock_searcher, "相同查询", max_results=5, query_domains=None, user_id="u1"
    )
    assert len(result1) == 2
    mock_searcher.search.assert_awaited_once()

    # 第二次: 命中缓存, 不执行搜索
    result2 = await conductor._cached_search(
        mock_searcher, "相同查询", max_results=5, query_domains=None, user_id="u1"
    )
    assert len(result2) == 2
    # searcher.search 仍只被调用一次 (第二次命中缓存)
    assert mock_searcher.search.await_count == 1


# ========== Redis 缓存未命中 ==========


@pytest.mark.asyncio
@patch("src.skills.researcher.research_conductor.get_redis_client", new_callable=AsyncMock)
async def test_cached_search_cache_miss_calls_search_and_writes(
    mock_get_redis: AsyncMock,
    conductor: ResearchConductor,
    mock_searcher: MagicMock,
    settings: Settings,
) -> None:
    """测试缓存未命中时调用真实搜索并写入缓存.

    场景: Redis 中无缓存, _cached_search 应:
    1. redis.get 返回 None
    2. 调用 searcher.search 执行搜索
    3. 用 redis.setex 写入缓存 (TTL=search_cache_ttl)
    4. 返回搜索结果
    """
    search_results = _make_results(2)
    mock_searcher.search = AsyncMock(return_value=search_results)

    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.setex = AsyncMock()
    mock_get_redis.return_value = mock_redis

    result = await conductor._cached_search(
        mock_searcher,
        "未命中查询",
        max_results=5,
        query_domains=None,
        user_id="user123",
    )

    # 验证返回搜索结果
    assert result == search_results
    # 验证调用了搜索
    mock_searcher.search.assert_awaited_once_with("未命中查询", max_results=5, query_domains=None)
    # 验证写入了缓存
    mock_redis.setex.assert_awaited_once()
    # 验证 TTL 来自 settings.search_cache_ttl
    ttl = mock_redis.setex.call_args[0][1]
    assert ttl == settings.search_cache_ttl
    # 验证缓存内容是 JSON 序列化的搜索结果
    cached_value = mock_redis.setex.call_args[0][2]
    assert json.loads(cached_value) == search_results


# ========== 缓存 key 格式 ==========


@pytest.mark.asyncio
@patch("src.skills.researcher.research_conductor.get_redis_client", new_callable=AsyncMock)
async def test_cached_search_cache_key_format(
    mock_get_redis: AsyncMock,
    conductor: ResearchConductor,
    mock_searcher: MagicMock,
    settings: Settings,
) -> None:
    """测试缓存 key 格式: {agent_id}:{user_id}:search:result:{engine}:{query_hash}.

    验证 key 各组成部分:
    - agent_id = settings.agent_name
    - user_id (None 时降级为 "anonymous")
    - 固定段 "search:result"
    - engine = searcher.name
    - query_hash = sha256(query) hexdigest
    """
    mock_searcher.search = AsyncMock(return_value=_make_results(1))
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.setex = AsyncMock()
    mock_get_redis.return_value = mock_redis

    query = "缓存key测试"
    user_id = "test_user_001"

    await conductor._cached_search(
        mock_searcher, query, max_results=3, query_domains=None, user_id=user_id
    )

    expected_key = _expected_cache_key(
        query=query,
        engine_name="test_engine",
        agent_id=settings.agent_name,
        user_id=user_id,
    )
    # redis.get 与 redis.setex 应使用相同的 key
    assert mock_redis.get.call_args[0][0] == expected_key
    assert mock_redis.setex.call_args[0][0] == expected_key


@pytest.mark.asyncio
@patch("src.skills.researcher.research_conductor.get_redis_client", new_callable=AsyncMock)
async def test_cached_search_cache_key_anonymous_user(
    mock_get_redis: AsyncMock,
    conductor: ResearchConductor,
    mock_searcher: MagicMock,
    settings: Settings,
) -> None:
    """测试 user_id 为 None 时缓存 key 使用 'anonymous' 降级."""
    mock_searcher.search = AsyncMock(return_value=_make_results(1))
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.setex = AsyncMock()
    mock_get_redis.return_value = mock_redis

    await conductor._cached_search(
        mock_searcher, "查询", max_results=3, query_domains=None, user_id=None
    )

    expected_key = _expected_cache_key(
        query="查询",
        engine_name="test_engine",
        agent_id=settings.agent_name,
        user_id="anonymous",
    )
    assert mock_redis.get.call_args[0][0] == expected_key


@pytest.mark.asyncio
@patch("src.skills.researcher.research_conductor.get_redis_client", new_callable=AsyncMock)
async def test_cached_search_different_queries_different_keys(
    mock_get_redis: AsyncMock,
    conductor: ResearchConductor,
    mock_searcher: MagicMock,
) -> None:
    """测试不同 query 生成不同缓存 key (query_hash 不同)."""
    mock_searcher.search = AsyncMock(return_value=_make_results(1))
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.setex = AsyncMock()
    mock_get_redis.return_value = mock_redis

    await conductor._cached_search(
        mock_searcher, "查询A", max_results=3, query_domains=None, user_id="u1"
    )
    await conductor._cached_search(
        mock_searcher, "查询B", max_results=3, query_domains=None, user_id="u1"
    )

    key1 = mock_redis.get.call_args_list[0][0][0]
    key2 = mock_redis.get.call_args_list[1][0][0]
    assert key1 != key2


# ========== TTL = 300s (5min) ==========


@pytest.mark.asyncio
@patch("src.skills.researcher.research_conductor.get_redis_client", new_callable=AsyncMock)
async def test_cached_search_ttl_matches_settings(
    mock_get_redis: AsyncMock,
    conductor: ResearchConductor,
    mock_searcher: MagicMock,
    settings: Settings,
) -> None:
    """测试 setex 的 TTL 取自 settings.search_cache_ttl (默认 300s = 5min)."""
    mock_searcher.search = AsyncMock(return_value=_make_results(1))
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.setex = AsyncMock()
    mock_get_redis.return_value = mock_redis

    await conductor._cached_search(
        mock_searcher, "TTL测试", max_results=3, query_domains=None, user_id="u1"
    )

    ttl = mock_redis.setex.call_args[0][1]
    assert ttl == settings.search_cache_ttl
    assert ttl == 300  # 默认 5min


# ========== 仅缓存非空结果 ==========


@pytest.mark.asyncio
@patch("src.skills.researcher.research_conductor.get_redis_client", new_callable=AsyncMock)
async def test_cached_search_empty_result_not_cached(
    mock_get_redis: AsyncMock,
    conductor: ResearchConductor,
    mock_searcher: MagicMock,
) -> None:
    """测试搜索返回空结果时不写入缓存 (仅缓存非空结果).

    场景: searcher.search 返回空列表, _cached_search 应:
    1. 调用搜索
    2. 返回空列表
    3. 不调用 redis.setex (空结果不缓存)
    """
    mock_searcher.search = AsyncMock(return_value=[])
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.setex = AsyncMock()
    mock_get_redis.return_value = mock_redis

    result = await conductor._cached_search(
        mock_searcher, "空结果查询", max_results=5, query_domains=None, user_id="u1"
    )

    assert result == []
    mock_searcher.search.assert_awaited_once()
    # 空结果不写入缓存
    mock_redis.setex.assert_not_awaited()


# ========== Redis 不可用降级 ==========


@pytest.mark.asyncio
@patch("src.skills.researcher.research_conductor.get_redis_client", new_callable=AsyncMock)
async def test_cached_search_redis_unavailable_degrades_to_direct_search(
    mock_get_redis: AsyncMock,
    conductor: ResearchConductor,
    mock_searcher: MagicMock,
) -> None:
    """测试 Redis 不可用时 (get_redis_client 返回 None) 降级为直接搜索.

    场景: Redis 未配置或连接失败, get_redis_client 返回 None,
    _cached_search 应跳过缓存读写, 直接调用 searcher.search.
    """
    mock_get_redis.return_value = None
    search_results = _make_results(2)
    mock_searcher.search = AsyncMock(return_value=search_results)

    result = await conductor._cached_search(
        mock_searcher, "无Redis查询", max_results=5, query_domains=None, user_id="u1"
    )

    # 验证返回搜索结果
    assert result == search_results
    # 验证调用了搜索
    mock_searcher.search.assert_awaited_once()


# ========== 缓存读取异常降级 ==========


@pytest.mark.asyncio
@patch("src.skills.researcher.research_conductor.get_redis_client", new_callable=AsyncMock)
async def test_cached_search_cache_read_exception_degrades(
    mock_get_redis: AsyncMock,
    conductor: ResearchConductor,
    mock_searcher: MagicMock,
) -> None:
    """测试缓存读取异常时降级为直接搜索.

    场景: redis.get 抛异常 (如连接断开), _cached_search 应:
    1. 捕获异常 (log warning)
    2. 降级调用 searcher.search
    3. 正常返回搜索结果
    """
    search_results = _make_results(2)
    mock_searcher.search = AsyncMock(return_value=search_results)

    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(side_effect=Exception("Redis connection lost"))
    mock_redis.setex = AsyncMock()
    mock_get_redis.return_value = mock_redis

    result = await conductor._cached_search(
        mock_searcher, "读取异常查询", max_results=5, query_domains=None, user_id="u1"
    )

    # 验证降级搜索后返回结果
    assert result == search_results
    mock_searcher.search.assert_awaited_once()


# ========== 缓存写入异常不阻断 ==========


@pytest.mark.asyncio
@patch("src.skills.researcher.research_conductor.get_redis_client", new_callable=AsyncMock)
async def test_cached_search_cache_write_exception_does_not_block(
    mock_get_redis: AsyncMock,
    conductor: ResearchConductor,
    mock_searcher: MagicMock,
) -> None:
    """测试缓存写入异常时不阻断主流程, 仍返回搜索结果.

    场景: redis.setex 抛异常 (如磁盘满), _cached_search 应:
    1. 捕获异常 (log warning)
    2. 正常返回搜索结果 (不阻断)
    """
    search_results = _make_results(2)
    mock_searcher.search = AsyncMock(return_value=search_results)

    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.setex = AsyncMock(side_effect=Exception("Redis write failed"))
    mock_get_redis.return_value = mock_redis

    result = await conductor._cached_search(
        mock_searcher, "写入异常查询", max_results=5, query_domains=None, user_id="u1"
    )

    # 验证写入异常不阻断, 仍返回搜索结果
    assert result == search_results
    mock_searcher.search.assert_awaited_once()
    mock_redis.setex.assert_awaited_once()


# ========== query_domains 透传 ==========


@pytest.mark.asyncio
@patch("src.skills.researcher.research_conductor.get_redis_client", new_callable=AsyncMock)
async def test_cached_search_passes_query_domains_to_searcher(
    mock_get_redis: AsyncMock,
    conductor: ResearchConductor,
    mock_searcher: MagicMock,
) -> None:
    """测试 query_domains 参数正确透传给 searcher.search."""
    mock_searcher.search = AsyncMock(return_value=_make_results(1))
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.setex = AsyncMock()
    mock_get_redis.return_value = mock_redis

    domains = ["example.com", "arxiv.org"]
    await conductor._cached_search(
        mock_searcher,
        "域名过滤查询",
        max_results=10,
        query_domains=domains,
        user_id="u1",
    )

    mock_searcher.search.assert_awaited_once_with(
        "域名过滤查询", max_results=10, query_domains=domains
    )
