"""单元测试: BM25 自动调用 update_bm25_corpus.

验证 src/rag/retriever.py _ensure_bm25_corpus 在 retrieve 入口自动调用:
- retrieve 入口自动触发 _ensure_bm25_corpus (无需业务代码显式调用)
- _ensure_bm25_corpus 内存缓存命中 (版本一致跳过重拉)
- _ensure_bm25_corpus 内存缓存未命中 (版本变更触发重拉)
- _ensure_bm25_corpus 合并多 namespace 文档
- _ensure_bm25_corpus singleflight 锁 (并发同 namespace 只拉一次)
- _ensure_bm25_corpus 清理已不在检索列表的 namespace 内存缓存
- _ensure_bm25_corpus 失败不阻断检索 (BM25 路径返回空)
- update_bm25_corpus 由 _ensure_bm25_corpus 自动调用

检索必须混合 BM25 + 向量.
单元测试不依赖外部服务 (mock Qdrant/Redis/Embeddings).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings
from src.rag.retriever import HybridRetriever

pytestmark = pytest.mark.unit


@pytest.fixture()
def settings() -> Settings:
    """测试用 settings (rerank 关闭, 聚焦 BM25 路径)."""
    return Settings(
        rerank_enabled=False,
        score_threshold=0.3,
        _env_file=None,
    )


@pytest.fixture()
def retriever(settings: Settings) -> HybridRetriever:
    """构造 HybridRetriever (mock embeddings/qdrant/rerank_client/redis).

    通过 __new__ 跳过 __init__ 避免真实 httpx/Qdrant 初始化,
    手动设置必要属性.
    """
    obj = HybridRetriever.__new__(HybridRetriever)
    obj.settings = settings
    obj._embeddings = MagicMock()
    obj._qdrant = MagicMock()
    obj._rerank_client = MagicMock()
    obj._redis = None
    obj._redis_initialized = True  # 跳过 Redis 初始化
    obj._bm25_corpus = []
    obj._bm25_docs = []
    obj._bm25 = None
    obj._bm25_per_namespace = {}
    import weakref

    obj._bm25_load_locks = weakref.WeakValueDictionary()
    obj._token_cache = {}
    obj._inflight_locks = weakref.WeakValueDictionary()
    return obj


def _make_doc(content: str, namespace: str = "ns1") -> dict[str, Any]:
    """构造测试文档."""
    return {
        "content": content,
        "metadata": {"title": content[:20]},
        "namespace": namespace,
    }


# ========== _ensure_bm25_corpus 自动调用契约 ==========


async def test_ensure_bm25_corpus_empty_namespaces_noop(retriever: HybridRetriever) -> None:
    """_ensure_bm25_corpus: namespaces 为空时直接返回 (不拉取)."""
    await retriever._ensure_bm25_corpus([], user_id=None)
    # 不应调用任何 Qdrant/Redis
    assert retriever._bm25 is None
    assert retriever._bm25_docs == []


async def test_ensure_bm25_corpus_loads_from_qdrant_on_first_call(
    retriever: HybridRetriever,
) -> None:
    """_ensure_bm25_corpus: 首次调用从 Qdrant scroll 拉取语料并重建 BM25."""
    docs = [_make_doc("量子计算研究"), _make_doc("新能源汽车分析")]
    retriever._qdrant.scroll_all_by_namespace = AsyncMock(return_value=docs)
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    retriever._get_bm25_version = AsyncMock(return_value=1)

    namespaces = ["agent-data"]
    await retriever._ensure_bm25_corpus(namespaces, user_id=None)

    # 应调用 scroll_all_by_namespace 拉取
    retriever._qdrant.scroll_all_by_namespace.assert_called_once_with("agent-data")
    # BM25 应已初始化
    assert retriever._bm25 is not None
    assert len(retriever._bm25_docs) == 2
    # 内存缓存应记录已加载版本
    assert "agent-data" in retriever._bm25_per_namespace
    assert retriever._bm25_per_namespace["agent-data"][1] == 1


async def test_ensure_bm25_corpus_memory_cache_hit_skips_reload(
    retriever: HybridRetriever,
) -> None:
    """_ensure_bm25_corpus: 内存缓存命中 (版本一致) 跳过重拉 Qdrant."""
    # 预设内存缓存 (版本 1, 已加载)
    docs = [_make_doc("已缓存文档")]
    retriever._bm25_per_namespace = {"agent-data": (docs, 1)}
    retriever._qdrant.scroll_all_by_namespace = AsyncMock(
        side_effect=AssertionError("缓存命中不应重拉 Qdrant")
    )
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    retriever._get_bm25_version = AsyncMock(return_value=1)  # 版本一致

    await retriever._ensure_bm25_corpus(["agent-data"], user_id=None)

    # 不应调用 scroll (缓存命中)
    retriever._qdrant.scroll_all_by_namespace.assert_not_called()
    # BM25 应已重建 (合并缓存文档)
    assert retriever._bm25 is not None
    assert len(retriever._bm25_docs) == 1


async def test_ensure_bm25_corpus_version_change_triggers_reload(
    retriever: HybridRetriever,
) -> None:
    """_ensure_bm25_corpus: 版本号变更 (文档新增/删除) 触发重拉."""
    # 预设内存缓存 (版本 1)
    old_docs = [_make_doc("旧文档")]
    retriever._bm25_per_namespace = {"agent-data": (old_docs, 1)}
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    # 版本号已升到 2 (文档新增后 invalidate_bm25_cache INCR)
    retriever._get_bm25_version = AsyncMock(return_value=2)
    new_docs = [_make_doc("新文档1"), _make_doc("新文档2")]
    retriever._qdrant.scroll_all_by_namespace = AsyncMock(return_value=new_docs)

    await retriever._ensure_bm25_corpus(["agent-data"], user_id=None)

    # 应重拉 (版本变更)
    retriever._qdrant.scroll_all_by_namespace.assert_called_once_with("agent-data")
    # 内存缓存应更新到版本 2
    assert retriever._bm25_per_namespace["agent-data"][1] == 2
    assert len(retriever._bm25_per_namespace["agent-data"][0]) == 2
    # BM25 应基于新文档重建
    assert len(retriever._bm25_docs) == 2


async def test_ensure_bm25_corpus_merges_multiple_namespaces(
    retriever: HybridRetriever,
) -> None:
    """_ensure_bm25_corpus: 合并多 namespace 文档 (共享 + 用户私有)."""
    shared_docs = [_make_doc("共享文档", "agent-data")]
    private_docs = [_make_doc("私有文档", "agent-data:user1")]
    retriever._qdrant.scroll_all_by_namespace = AsyncMock(
        side_effect=lambda ns: shared_docs if ns == "agent-data" else private_docs
    )
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    retriever._get_bm25_version = AsyncMock(return_value=1)

    namespaces = ["agent-data", "agent-data:user1"]
    await retriever._ensure_bm25_corpus(namespaces, user_id="user1")

    # BM25 应含两个 namespace 的合并文档
    assert len(retriever._bm25_docs) == 2
    contents = {d["content"] for d in retriever._bm25_docs}
    assert "共享文档" in contents
    assert "私有文档" in contents


async def test_ensure_bm25_corpus_clears_stale_namespace_cache(
    retriever: HybridRetriever,
) -> None:
    """_ensure_bm25_corpus: 清理已不在检索列表的 namespace 内存缓存 (避免无界增长)."""
    # 预设过时缓存 (user2 的私有 ns, 本次不检索)
    retriever._bm25_per_namespace = {
        "agent-data": ([_make_doc("共享")], 1),
        "agent-data:user2": ([_make_doc("user2 私有")], 1),  # 过时
    }
    retriever._qdrant.scroll_all_by_namespace = AsyncMock(return_value=[_make_doc("共享")])
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    retriever._get_bm25_version = AsyncMock(return_value=1)

    # 本次只检索 agent-data (无 user_id)
    await retriever._ensure_bm25_corpus(["agent-data"], user_id=None)

    # user2 的私有 ns 缓存应被清理
    assert "agent-data:user2" not in retriever._bm25_per_namespace
    assert "agent-data" in retriever._bm25_per_namespace


async def test_ensure_bm25_corpus_qdrant_failure_returns_empty(
    retriever: HybridRetriever,
) -> None:
    """_ensure_bm25_corpus: Qdrant scroll 失败时该 ns 文档为空 (不阻断检索)."""
    retriever._qdrant.scroll_all_by_namespace = AsyncMock(
        side_effect=RuntimeError("Qdrant unreachable")
    )
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    retriever._get_bm25_version = AsyncMock(return_value=1)

    # 不应抛异常 (降级为空语料)
    await retriever._ensure_bm25_corpus(["agent-data"], user_id=None)

    # BM25 应为 None (空语料)
    assert retriever._bm25 is None
    assert retriever._bm25_docs == []


async def test_ensure_bm25_corpus_skips_rebuild_when_no_change(
    retriever: HybridRetriever,
) -> None:
    """_ensure_bm25_corpus: 语料无变化时不重建 BM25Okapi (避免重复计算)."""
    docs = [_make_doc("已加载")]
    retriever._bm25_per_namespace = {"agent-data": (docs, 1)}
    retriever._bm25 = MagicMock()  # 已初始化
    retriever._qdrant.scroll_all_by_namespace = AsyncMock(side_effect=AssertionError("不应重拉"))
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    retriever._get_bm25_version = AsyncMock(return_value=1)

    original_bm25 = retriever._bm25
    await retriever._ensure_bm25_corpus(["agent-data"], user_id=None)

    # BM25 对象应未被替换 (无变化不重建)
    assert retriever._bm25 is original_bm25


# ========== singleflight 锁 (并发同 namespace 只拉一次) ==========


async def test_ensure_bm25_corpus_singleflight_concurrent_same_ns(
    retriever: HybridRetriever,
) -> None:
    """singleflight: 并发同 namespace 只拉一次 Qdrant."""
    call_count = 0

    async def counting_scroll(ns: str) -> list[dict[str, Any]]:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)  # 模拟拉取延迟
        return [_make_doc("文档")]

    retriever._qdrant.scroll_all_by_namespace = AsyncMock(side_effect=counting_scroll)
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    retriever._get_bm25_version = AsyncMock(return_value=1)

    # 5 个协程并发 _ensure_bm25_corpus 同一 namespace
    await asyncio.gather(
        *[retriever._ensure_bm25_corpus(["agent-data"], user_id=None) for _ in range(5)]
    )

    assert call_count == 1, f"并发同 ns 应只拉一次, 实际 {call_count} 次"


# ========== retrieve 入口自动调用 _ensure_bm25_corpus ==========


async def test_retrieve_auto_calls_ensure_bm25_corpus(
    retriever: HybridRetriever,
) -> None:
    """retrieve 入口自动调用 _ensure_bm25_corpus (P0 BM25 断点修复核心).

    场景: retrieve 被调用时, 应自动触发 _ensure_bm25_corpus 加载语料,
    无需业务代码显式调用 update_bm25_corpus.
    """
    # mock build_data_namespaces 返回有数据
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    retriever._qdrant.namespace_has_data = AsyncMock(return_value=True)
    retriever._qdrant.scroll_all_by_namespace = AsyncMock(return_value=[_make_doc("测试")])
    retriever._get_bm25_version = AsyncMock(return_value=1)
    # mock 向量检索返回空 (聚焦 BM25 自动调用验证)
    retriever._embeddings.embed_query = AsyncMock(return_value=[0.1] * 10)
    retriever._qdrant.search = AsyncMock(return_value=[])
    # mock 缓存未命中
    retriever._get_cache = AsyncMock(return_value=None)
    retriever._set_cache = AsyncMock(return_value=None)

    ensure_called = False
    original_ensure = retriever._ensure_bm25_corpus

    async def tracking_ensure(namespaces: list[str], user_id: str | None) -> None:
        nonlocal ensure_called
        ensure_called = True
        await original_ensure(namespaces, user_id)

    retriever._ensure_bm25_corpus = tracking_ensure

    await retriever.retrieve("测试查询", user_id=None)

    assert ensure_called, "retrieve 应自动调用 _ensure_bm25_corpus"


async def test_retrieve_bm25_load_failure_does_not_block(
    retriever: HybridRetriever,
) -> None:
    """retrieve: _ensure_bm25_corpus 失败不阻断检索 (BM25 路径返回空, 向量仍工作)."""
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    retriever._qdrant.namespace_has_data = AsyncMock(return_value=True)
    # _ensure_bm25_corpus 内部失败
    retriever._get_bm25_version = AsyncMock(side_effect=RuntimeError("Redis 爆炸"))
    # 向量检索正常
    retriever._embeddings.embed_query = AsyncMock(return_value=[0.1] * 10)
    vector_result = [
        {"content": "向量结果", "score": 0.9, "metadata": {}, "namespace": "agent-data"}
    ]
    retriever._qdrant.search = AsyncMock(return_value=vector_result)
    retriever._get_cache = AsyncMock(return_value=None)
    retriever._set_cache = AsyncMock(return_value=None)

    # 不应抛异常
    results = await retriever.retrieve("测试", user_id=None)
    # 向量结果应返回 (BM25 失败降级空)
    assert len(results) >= 1
    assert results[0]["content"] == "向量结果"


# ========== update_bm25_corpus 由 _ensure_bm25_corpus 自动调用 ==========


async def test_update_bm25_corpus_called_by_ensure(retriever: HybridRetriever) -> None:
    """update_bm25_corpus 由 _ensure_bm25_corpus 自动调用.

    场景: update_bm25_corpus 由 _ensure_bm25_corpus
    在 retrieve 入口自动调用. 验证 _ensure_bm25_corpus 内部调用 update_bm25_corpus.
    """
    docs = [_make_doc("文档1"), _make_doc("文档2")]
    retriever._qdrant.scroll_all_by_namespace = AsyncMock(return_value=docs)
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    retriever._get_bm25_version = AsyncMock(return_value=1)

    update_called = False
    original_update = retriever.update_bm25_corpus

    def tracking_update(d: list[dict[str, Any]]) -> None:
        nonlocal update_called
        update_called = True
        original_update(d)

    retriever.update_bm25_corpus = tracking_update

    await retriever._ensure_bm25_corpus(["agent-data"], user_id=None)

    assert update_called, "_ensure_bm25_corpus 应自动调用 update_bm25_corpus"


async def test_update_bm25_corpus_empty_docs_sets_bm25_none(
    retriever: HybridRetriever,
) -> None:
    """update_bm25_corpus: 空文档列表时 _bm25 设为 None (BM25Okapi 不接受空语料)."""
    retriever.update_bm25_corpus([])
    assert retriever._bm25 is None
    assert retriever._bm25_docs == []
    assert retriever._bm25_corpus == []


async def test_update_bm25_corpus_non_empty_initializes_bm25(
    retriever: HybridRetriever,
) -> None:
    """update_bm25_corpus: 非空文档列表初始化 BM25Okapi."""
    docs = [_make_doc("量子计算"), _make_doc("人工智能")]
    retriever.update_bm25_corpus(docs)
    assert retriever._bm25 is not None
    assert len(retriever._bm25_docs) == 2
    assert len(retriever._bm25_corpus) == 2  # jieba 分词后


# ========== _bm25_cache_uid 共享/私有 namespace 区分 ==========


def test_bm25_cache_uid_shared_namespace_uses_anonymous(
    retriever: HybridRetriever,
) -> None:
    """_bm25_cache_uid: 共享 namespace 用 anonymous 常量 (跨用户共享缓存).

    default_user_id 环境变量已移除, RAG 层共享 namespace
    缓存键用 _ANONYMOUS_USER_ID = "anonymous" 常量替代.
    """
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    uid = retriever._bm25_cache_uid("agent-data", user_id="user123")
    assert uid == "anonymous"


def test_bm25_cache_uid_private_namespace_uses_user_id(
    retriever: HybridRetriever,
) -> None:
    """_bm25_cache_uid: 用户私有 namespace 用实际 user_id (隔离缓存)."""
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    uid = retriever._bm25_cache_uid("agent-data:user123", user_id="user123")
    assert uid == "user123"


def test_bm25_cache_uid_private_namespace_none_user_falls_back(
    retriever: HybridRetriever,
) -> None:
    """_bm25_cache_uid: 用户私有 namespace + user_id=None → anonymous 常量."""
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    uid = retriever._bm25_cache_uid("agent-data:user123", user_id=None)
    assert uid == "anonymous"
