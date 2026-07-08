"""单元测试: BM25 Redis 缓存 (版本号/TTL/降级).

验证 src/rag/retriever.py BM25 语料 Redis 缓存机制:
- _get_bm25_version: 读取版本号 (默认 1 / Redis 命中 / bytes 解码 / 异常降级)
- _load_namespace_corpus: Redis 缓存命中 / 未命中走 Qdrant scroll / 写回 Redis
- invalidate_bm25_cache: 版本号 INCR / 内存缓存清除 / Redis 不可用降级
- _bm25_version_key / _bm25_corpus_key 键格式 (含 namespace + 版本号)
- _BM25_CORPUS_CACHE_TTL / _BM25_CORPUS_DEFAULT_VERSION 常量契约

AGENTS.md 第 7 章: Redis 键应加前缀 {agent_id}:{user_id}:, 应设 TTL.
AGENTS.md 第 13 章: 单元测试不依赖外部服务 (mock Redis/Qdrant).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest

from src.config.settings import Settings
from src.rag.retriever import HybridRetriever

pytestmark = pytest.mark.unit


@pytest.fixture()
def settings() -> Settings:
    """测试用 settings."""
    return Settings(_env_file=None)


@pytest.fixture()
def retriever(settings: Settings) -> HybridRetriever:
    """构造 HybridRetriever (mock 依赖, 手动设置属性)."""
    obj = HybridRetriever.__new__(HybridRetriever)
    obj.settings = settings
    obj._embeddings = MagicMock()
    obj._qdrant = MagicMock()
    obj._rerank_client = MagicMock()
    obj._redis = None
    obj._redis_initialized = True
    obj._bm25_corpus = []
    obj._bm25_docs = []
    obj._bm25 = None
    obj._bm25_per_namespace = {}
    import weakref

    obj._bm25_load_locks = weakref.WeakValueDictionary()
    obj._token_cache = {}
    obj._inflight_locks = weakref.WeakValueDictionary()
    return obj


# ========== 常量契约 ==========


def test_bm25_corpus_cache_ttl_is_24_hours() -> None:
    """_BM25_CORPUS_CACHE_TTL = 86400 (24 小时兜底过期)."""
    assert HybridRetriever._BM25_CORPUS_CACHE_TTL == 86400


def test_bm25_corpus_default_version_is_1() -> None:
    """_BM25_CORPUS_DEFAULT_VERSION = 1 (从未 INCR 过的 namespace 默认版本)."""
    assert HybridRetriever._BM25_CORPUS_DEFAULT_VERSION == 1


# ========== 键格式 ==========


def test_bm25_version_key_format(retriever: HybridRetriever) -> None:
    """_bm25_version_key 格式: {agent_id}:{cache_uid}:rag:bm25_corpus_version:{namespace}."""
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    key = retriever._bm25_version_key("agent-data", "user1")
    assert key == f"{retriever.settings.agent_name}:user1:rag:bm25_corpus_version:agent-data"


def test_bm25_corpus_key_format_includes_version(retriever: HybridRetriever) -> None:
    """_bm25_corpus_key 格式: {agent_id}:{cache_uid}:rag:bm25_corpus:{namespace}:v{version}."""
    key = retriever._bm25_corpus_key("agent-data", "user1", 3)
    assert key == f"{retriever.settings.agent_name}:user1:rag:bm25_corpus:agent-data:v3"


def test_bm25_corpus_key_version_change_invalidates_cache(
    retriever: HybridRetriever,
) -> None:
    """版本号变更 → corpus_key 不同 → 旧缓存自然失效 (键变更)."""
    key_v1 = retriever._bm25_corpus_key("ns", "uid", 1)
    key_v2 = retriever._bm25_corpus_key("ns", "uid", 2)
    assert key_v1 != key_v2


# ========== _get_bm25_version ==========


async def test_get_bm25_version_redis_none_returns_default(retriever: HybridRetriever) -> None:
    """_get_bm25_version: Redis 不可用 (None) → 返回默认版本号 1."""
    retriever._redis = None
    retriever._redis_initialized = True
    version = await retriever._get_bm25_version("agent-data", "user1")
    assert version == 1


async def test_get_bm25_version_redis_miss_returns_default(
    retriever: HybridRetriever,
) -> None:
    """_get_bm25_version: Redis 命中但 key 不存在 → 返回默认版本号 1 (不写入)."""
    retriever._redis = MagicMock()
    retriever._redis.get = AsyncMock(return_value=None)
    retriever._redis_initialized = True

    version = await retriever._get_bm25_version("agent-data", "user1")

    assert version == 1
    # 不应 INCR (未设置时不写入, 避免无数据 namespace 产生垃圾键)
    retriever._redis.set.assert_not_called()


async def test_get_bm25_version_redis_hit_string(retriever: HybridRetriever) -> None:
    """_get_bm25_version: Redis 命中 (str 类型, decode_responses=True) → 返回版本号."""
    retriever._redis = MagicMock()
    retriever._redis.get = AsyncMock(return_value="5")
    retriever._redis_initialized = True

    version = await retriever._get_bm25_version("agent-data", "user1")

    assert version == 5


async def test_get_bm25_version_redis_hit_bytes(retriever: HybridRetriever) -> None:
    """_get_bm25_version: Redis 命中 (bytes 类型) → 解码后返回版本号."""
    retriever._redis = MagicMock()
    retriever._redis.get = AsyncMock(return_value=b"7")
    retriever._redis_initialized = True

    version = await retriever._get_bm25_version("agent-data", "user1")

    assert version == 7


async def test_get_bm25_version_redis_exception_returns_default(
    retriever: HybridRetriever,
) -> None:
    """_get_bm25_version: Redis 异常 → 降级返回默认版本号 1 (不阻断)."""
    retriever._redis = MagicMock()
    retriever._redis.get = AsyncMock(side_effect=RuntimeError("Redis timeout"))
    retriever._redis_initialized = True

    version = await retriever._get_bm25_version("agent-data", "user1")

    assert version == 1


async def test_get_bm25_version_triggers_lazy_redis_init(retriever: HybridRetriever) -> None:
    """_get_bm25_version: Redis 未初始化时触发 _ensure_redis (惰性初始化)."""
    retriever._redis_initialized = False
    retriever._redis = None
    with patch.object(retriever, "_ensure_redis", new=AsyncMock(return_value=None)) as mock_ensure:
        version = await retriever._get_bm25_version("agent-data", "user1")
        mock_ensure.assert_called_once()
        assert version == 1


# ========== _load_namespace_corpus ==========


async def test_load_namespace_corpus_redis_hit_returns_cached(
    retriever: HybridRetriever,
) -> None:
    """_load_namespace_corpus: Redis 缓存命中 → 直接反序列化返回 (快速路径)."""
    cached_docs = [{"content": "缓存文档", "metadata": {}, "namespace": "ns"}]
    retriever._redis = MagicMock()
    retriever._redis.get = AsyncMock(return_value=orjson.dumps(cached_docs))
    retriever._redis_initialized = True
    retriever._qdrant.scroll_all_by_namespace = AsyncMock(
        side_effect=AssertionError("缓存命中不应拉 Qdrant")
    )

    docs = await retriever._load_namespace_corpus("ns", "uid", 1)

    assert len(docs) == 1
    assert docs[0]["content"] == "缓存文档"
    retriever._qdrant.scroll_all_by_namespace.assert_not_called()


async def test_load_namespace_corpus_redis_bytes_hit(retriever: HybridRetriever) -> None:
    """_load_namespace_corpus: Redis 缓存命中 (bytes 类型) → 解码后返回."""
    cached_docs = [{"content": "字节缓存"}]
    retriever._redis = MagicMock()
    # orjson.dumps 返回 bytes (模拟 decode_responses=False 的 Redis 客户端)
    retriever._redis.get = AsyncMock(return_value=orjson.dumps(cached_docs))
    retriever._redis_initialized = True
    retriever._qdrant.scroll_all_by_namespace = AsyncMock(
        side_effect=AssertionError("缓存命中不应拉 Qdrant")
    )

    docs = await retriever._load_namespace_corpus("ns", "uid", 1)

    assert len(docs) == 1
    assert docs[0]["content"] == "字节缓存"


async def test_load_namespace_corpus_redis_miss_falls_back_to_qdrant(
    retriever: HybridRetriever,
) -> None:
    """_load_namespace_corpus: Redis 未命中 → Qdrant scroll 拉取."""
    retriever._redis = MagicMock()
    retriever._redis.get = AsyncMock(return_value=None)
    retriever._redis_initialized = True
    qdrant_docs = [{"content": "Qdrant 文档", "metadata": {}, "namespace": "ns"}]
    retriever._qdrant.scroll_all_by_namespace = AsyncMock(return_value=qdrant_docs)

    docs = await retriever._load_namespace_corpus("ns", "uid", 1)

    assert len(docs) == 1
    assert docs[0]["content"] == "Qdrant 文档"
    retriever._qdrant.scroll_all_by_namespace.assert_called_once_with("ns")


async def test_load_namespace_corpus_writes_back_to_redis(
    retriever: HybridRetriever,
) -> None:
    """_load_namespace_corpus: Qdrant 拉取后写回 Redis 缓存 (TTL 24h)."""
    retriever._redis = MagicMock()
    retriever._redis.get = AsyncMock(return_value=None)
    retriever._redis.set = AsyncMock()
    retriever._redis_initialized = True
    qdrant_docs = [{"content": "新文档"}]
    retriever._qdrant.scroll_all_by_namespace = AsyncMock(return_value=qdrant_docs)

    await retriever._load_namespace_corpus("ns", "uid", 1)

    # 应写回 Redis (含 TTL)
    retriever._redis.set.assert_called_once()
    call_args = retriever._redis.set.call_args
    assert call_args.kwargs.get("ex") == 86400 or call_args.args[-1] == 86400


async def test_load_namespace_corpus_redis_write_failure_silent(
    retriever: HybridRetriever,
) -> None:
    """_load_namespace_corpus: Redis 写回失败不阻断 (下次仍从 Qdrant 拉)."""
    retriever._redis = MagicMock()
    retriever._redis.get = AsyncMock(return_value=None)
    retriever._redis.set = AsyncMock(side_effect=RuntimeError("Redis 写爆"))
    retriever._redis_initialized = True
    retriever._qdrant.scroll_all_by_namespace = AsyncMock(return_value=[{"content": "文档"}])

    # 不应抛异常
    docs = await retriever._load_namespace_corpus("ns", "uid", 1)
    assert len(docs) == 1


async def test_load_namespace_corpus_empty_docs_no_redis_write(
    retriever: HybridRetriever,
) -> None:
    """_load_namespace_corpus: Qdrant 返回空文档时不写回 Redis (避免缓存空列表)."""
    retriever._redis = MagicMock()
    retriever._redis.get = AsyncMock(return_value=None)
    retriever._redis.set = AsyncMock()
    retriever._redis_initialized = True
    retriever._qdrant.scroll_all_by_namespace = AsyncMock(return_value=[])

    await retriever._load_namespace_corpus("ns", "uid", 1)

    # 空 docs 不应写回 (if docs 检查)
    retriever._redis.set.assert_not_called()


async def test_load_namespace_corpus_redis_none_uses_qdrant(
    retriever: HybridRetriever,
) -> None:
    """_load_namespace_corpus: Redis 不可用 (None) → 走 Qdrant scroll."""
    retriever._redis = None
    retriever._redis_initialized = True
    retriever._qdrant.scroll_all_by_namespace = AsyncMock(return_value=[{"content": "文档"}])

    docs = await retriever._load_namespace_corpus("ns", "uid", 1)

    assert len(docs) == 1
    retriever._qdrant.scroll_all_by_namespace.assert_called_once_with("ns")


async def test_load_namespace_corpus_redis_read_exception_falls_back(
    retriever: HybridRetriever,
) -> None:
    """_load_namespace_corpus: Redis 读取异常 → 降级到 Qdrant scroll."""
    retriever._redis = MagicMock()
    retriever._redis.get = AsyncMock(side_effect=RuntimeError("Redis 读爆"))
    retriever._redis_initialized = True
    retriever._qdrant.scroll_all_by_namespace = AsyncMock(return_value=[{"content": "降级文档"}])

    docs = await retriever._load_namespace_corpus("ns", "uid", 1)

    assert len(docs) == 1
    assert docs[0]["content"] == "降级文档"


# ========== invalidate_bm25_cache ==========


async def test_invalidate_bm25_cache_clears_memory_cache(retriever: HybridRetriever) -> None:
    """invalidate_bm25_cache: 清除内存缓存 (_bm25_per_namespace)."""
    retriever._bm25_per_namespace = {"agent-data": ([{"content": "旧"}], 1)}
    retriever._redis = None
    retriever._redis_initialized = True
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")

    await retriever.invalidate_bm25_cache("agent-data", user_id=None)

    assert "agent-data" not in retriever._bm25_per_namespace


async def test_invalidate_bm25_cache_redis_incr_version(retriever: HybridRetriever) -> None:
    """invalidate_bm25_cache: Redis INCR 版本号 (旧缓存键自然失效)."""
    retriever._bm25_per_namespace = {"agent-data": ([], 1)}
    retriever._redis = MagicMock()
    retriever._redis.incr = AsyncMock(return_value=2)
    retriever._redis_initialized = True
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")

    await retriever.invalidate_bm25_cache("agent-data", user_id=None)

    # 应 INCR 版本号
    retriever._redis.incr.assert_called_once()
    version_key = retriever._redis.incr.call_args.args[0]
    assert "bm25_corpus_version" in version_key
    assert "agent-data" in version_key


async def test_invalidate_bm25_cache_redis_none_only_clears_memory(
    retriever: HybridRetriever,
) -> None:
    """invalidate_bm25_cache: Redis 不可用时仅清内存 (下次 retrieve 从 Qdrant 拉)."""
    retriever._bm25_per_namespace = {"agent-data": ([], 1)}
    retriever._redis = None
    retriever._redis_initialized = True
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")

    # 不应抛异常
    await retriever.invalidate_bm25_cache("agent-data", user_id=None)

    assert "agent-data" not in retriever._bm25_per_namespace


async def test_invalidate_bm25_cache_redis_incr_failure_silent(
    retriever: HybridRetriever,
) -> None:
    """invalidate_bm25_cache: Redis INCR 失败仅告警 (内存已清, 行为正确)."""
    retriever._bm25_per_namespace = {"agent-data": ([], 1)}
    retriever._redis = MagicMock()
    retriever._redis.incr = AsyncMock(side_effect=RuntimeError("Redis INCR 失败"))
    retriever._redis_initialized = True
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")

    # 不应抛异常
    await retriever.invalidate_bm25_cache("agent-data", user_id=None)

    # 内存缓存应已清
    assert "agent-data" not in retriever._bm25_per_namespace


async def test_invalidate_bm25_cache_triggers_lazy_redis_init(
    retriever: HybridRetriever,
) -> None:
    """invalidate_bm25_cache: Redis 未初始化时触发 _ensure_redis."""
    retriever._redis_initialized = False
    retriever._redis = None
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")

    with patch.object(retriever, "_ensure_redis", new=AsyncMock(return_value=None)) as mock_ensure:
        await retriever.invalidate_bm25_cache("agent-data", user_id=None)
        mock_ensure.assert_called_once()


async def test_invalidate_bm25_cache_private_namespace_uses_user_id(
    retriever: HybridRetriever,
) -> None:
    """invalidate_bm25_cache: 用户私有 namespace 用实际 user_id 作 cache_uid."""
    retriever._redis = MagicMock()
    retriever._redis.incr = AsyncMock(return_value=2)
    retriever._redis_initialized = True
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")

    await retriever.invalidate_bm25_cache("agent-data:user123", user_id="user123")

    version_key = retriever._redis.incr.call_args.args[0]
    # cache_uid 应为 user123 (私有 ns)
    assert "user123" in version_key
    assert "agent-data:user123" in version_key


# ========== 版本号失效机制端到端 ==========


async def test_version_invalidation_flow_end_to_end(retriever: HybridRetriever) -> None:
    """版本号失效端到端: INCR 后 _ensure_bm25_corpus 检测版本变更重拉.

    场景:
    1. 首次 _ensure_bm25_corpus 加载版本 1
    2. 文档新增后 invalidate_bm25_cache INCR 到版本 2
    3. 再次 _ensure_bm25_corpus 检测版本变更, 重拉 Qdrant
    """
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    retriever._redis = MagicMock()
    retriever._redis.incr = AsyncMock(return_value=2)
    retriever._redis_initialized = True

    # 1. 首次加载版本 1
    retriever._get_bm25_version = AsyncMock(return_value=1)
    retriever._qdrant.scroll_all_by_namespace = AsyncMock(return_value=[{"content": "旧文档"}])
    await retriever._ensure_bm25_corpus(["agent-data"], user_id=None)
    assert retriever._bm25_per_namespace["agent-data"][1] == 1

    # 2. 文档新增, invalidate 版本号
    await retriever.invalidate_bm25_cache("agent-data", user_id=None)
    assert "agent-data" not in retriever._bm25_per_namespace

    # 3. 再次加载, 版本号已升到 2, 应重拉
    retriever._get_bm25_version = AsyncMock(return_value=2)
    retriever._qdrant.scroll_all_by_namespace = AsyncMock(return_value=[{"content": "新文档"}])
    await retriever._ensure_bm25_corpus(["agent-data"], user_id=None)

    assert retriever._bm25_per_namespace["agent-data"][1] == 2
    assert len(retriever._bm25_docs) == 1
    assert retriever._bm25_docs[0]["content"] == "新文档"
