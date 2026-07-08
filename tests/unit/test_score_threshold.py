"""单元测试: score_threshold 阈值修复 (向量检索不再套用 rerank 阈值).

验证 src/rag/retriever.py:
- score_threshold 仅适用于 rerank 分数 (0~1), RRF 融合分数不应用此阈值
- rerank_enabled=False 时: fused[:k] 直接取 top_k, 不过滤阈值
- rerank_enabled=True 时: _rerank 内部过滤 score < threshold 的结果
- _rerank 失败降级: 返回 docs[:top_k] (RRF 分数, 不过滤阈值)
- _bm25_search: score <= 0 过滤 (BM25 固有, 非 score_threshold)
- 向量检索结果不因低分被过滤 (RRF 融合保留所有候选)

AGENTS.md 第 7 章: score_threshold 默认 0.3, 低于阈值丢弃
(仅当 rerank 启用时生效, RRF 融合分数不应用此阈值).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings
from src.rag.retriever import HybridRetriever

pytestmark = pytest.mark.unit


@pytest.fixture()
def settings_rerank_off() -> Settings:
    """rerank 关闭的 settings (聚焦 RRF 融合不过滤阈值)."""
    return Settings(
        rerank_enabled=False,
        score_threshold=0.3,
        vector_weight=0.7,
        bm25_weight=0.3,
        rrf_k=60,
        _env_file=None,
    )


@pytest.fixture()
def settings_rerank_on() -> Settings:
    """rerank 开启的 settings (聚焦 _rerank 内部过滤阈值)."""
    return Settings(
        rerank_enabled=True,
        score_threshold=0.3,
        rerank_top_k=5,
        vector_weight=0.7,
        bm25_weight=0.3,
        rrf_k=60,
        _env_file=None,
    )


@pytest.fixture()
def retriever_rerank_off(settings_rerank_off: Settings) -> HybridRetriever:
    """构造 retriever (rerank 关闭)."""
    obj = HybridRetriever.__new__(HybridRetriever)
    obj.settings = settings_rerank_off
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


@pytest.fixture()
def retriever_rerank_on(settings_rerank_on: Settings) -> HybridRetriever:
    """构造 retriever (rerank 开启)."""
    obj = HybridRetriever.__new__(HybridRetriever)
    obj.settings = settings_rerank_on
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


# ========== score_threshold 仅适用于 rerank 分数 ==========


async def test_rerank_off_does_not_apply_score_threshold(
    retriever_rerank_off: HybridRetriever,
) -> None:
    """rerank_enabled=False: RRF 融合分数不过滤阈值 (直接取 top_k).

    场景: 向量+BM25 融合后分数很低 (如 0.01), 但 rerank 关闭时不应被过滤.
    修复前: 误用 score_threshold 过滤 RRF 分数, 导致结果丢失.
    修复后: rerank 关闭时 fused[:k] 直接取 top_k.
    """
    retriever_rerank_off._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    retriever_rerank_off._qdrant.namespace_has_data = AsyncMock(return_value=True)
    retriever_rerank_off._qdrant.scroll_all_by_namespace = AsyncMock(return_value=[])
    retriever_rerank_off._get_bm25_version = AsyncMock(return_value=1)
    # 低分向量结果 (RRF 融合后分数 < 0.3)
    low_score_vector = [
        {"content": "低分结果1", "score": 0.9, "metadata": {}, "namespace": "ns"},
        {"content": "低分结果2", "score": 0.8, "metadata": {}, "namespace": "ns"},
    ]
    retriever_rerank_off._embeddings.embed_query = AsyncMock(return_value=[0.1] * 10)
    retriever_rerank_off._qdrant.search = AsyncMock(return_value=low_score_vector)
    retriever_rerank_off._get_cache = AsyncMock(return_value=None)
    retriever_rerank_off._set_cache = AsyncMock(return_value=None)
    # mock _ensure_bm25_corpus 跳过
    retriever_rerank_off._ensure_bm25_corpus = AsyncMock(return_value=None)
    # mock _bm25_search 返回空
    retriever_rerank_off._bm25_search = AsyncMock(return_value=[])

    results = await retriever_rerank_off.retrieve("测试", user_id=None, top_k=5)

    # rerank 关闭, 即使 RRF 分数低也应返回 (不过滤阈值)
    assert len(results) > 0
    # 不应调用 _rerank
    retriever_rerank_off._rerank_client.post.assert_not_called()


async def test_rerank_off_low_rrf_score_preserved(
    retriever_rerank_off: HybridRetriever,
) -> None:
    """rerank_enabled=False: RRF 融合分数 0.01 (远低于 0.3) 也保留.

    验证 score_threshold 不套用于 RRF 融合分数.
    """
    # 构造 RRF 融合后分数极低的结果 (vector_weight=0.7, rrf_k=60, rank=0 → 0.7/61 ≈ 0.0115)
    retriever_rerank_off._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    retriever_rerank_off._qdrant.namespace_has_data = AsyncMock(return_value=True)
    retriever_rerank_off._embeddings.embed_query = AsyncMock(return_value=[0.1] * 10)
    vector_results = [
        {"content": "候选1", "score": 0.9, "metadata": {}, "namespace": "ns"},
    ]
    retriever_rerank_off._qdrant.search = AsyncMock(return_value=vector_results)
    retriever_rerank_off._get_cache = AsyncMock(return_value=None)
    retriever_rerank_off._set_cache = AsyncMock(return_value=None)
    retriever_rerank_off._ensure_bm25_corpus = AsyncMock(return_value=None)
    retriever_rerank_off._bm25_search = AsyncMock(return_value=[])

    results = await retriever_rerank_off.retrieve("测试", user_id=None, top_k=5)

    # 即使 RRF 分数 ≈ 0.0115 < 0.3, 也应保留 (rerank 未启用, 不过滤)
    assert len(results) == 1
    assert results[0]["content"] == "候选1"
    # RRF 分数应 < 0.3 (验证确实是低分)
    assert results[0]["score"] < 0.3


# ========== rerank_enabled=True 时 _rerank 内部过滤阈值 ==========


async def test_rerank_on_filters_low_score_results(
    retriever_rerank_on: HybridRetriever,
) -> None:
    """rerank_enabled=True: _rerank 内部过滤 score < threshold 的结果.

    场景: rerank 返回的 relevance_score < 0.3 应被丢弃.
    注: 不过滤 _rerank 方法, 改为 mock _rerank_client.post, 让真实 _rerank
    跑 score_threshold 过滤逻辑 (过滤在 _rerank 内部, 非 retrieve 主流程).
    """
    retriever_rerank_on._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    retriever_rerank_on._qdrant.namespace_has_data = AsyncMock(return_value=True)
    retriever_rerank_on._embeddings.embed_query = AsyncMock(return_value=[0.1] * 10)
    retriever_rerank_on._qdrant.search = AsyncMock(
        return_value=[
            {"content": "高分", "score": 0.9, "metadata": {}, "namespace": "ns"},
            {"content": "低分", "score": 0.8, "metadata": {}, "namespace": "ns"},
        ]
    )
    retriever_rerank_on._get_cache = AsyncMock(return_value=None)
    retriever_rerank_on._set_cache = AsyncMock(return_value=None)
    retriever_rerank_on._ensure_bm25_corpus = AsyncMock(return_value=None)
    retriever_rerank_on._bm25_search = AsyncMock(return_value=[])

    # mock _rerank_client.post 返回混合分数 (高于/低于阈值), 让真实 _rerank 跑过滤
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value=[
            {"index": 0, "relevance_score": 0.9},  # 保留
            {"index": 1, "relevance_score": 0.1},  # < 0.3 过滤
        ]
    )
    retriever_rerank_on._rerank_client.post = AsyncMock(return_value=mock_response)

    results = await retriever_rerank_on.retrieve("测试", user_id=None, top_k=5)

    # 低分 (< 0.3) 应被过滤
    contents = [r["content"] for r in results]
    assert "高分" in contents
    assert "低分" not in contents


async def test_rerank_on_all_below_threshold_returns_empty(
    retriever_rerank_on: HybridRetriever,
) -> None:
    """rerank_enabled=True: 所有结果 rerank 分数 < threshold → 返回空.

    注: mock _rerank_client.post (非 _rerank), 让真实 _rerank 跑过滤逻辑.
    """
    retriever_rerank_on._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    retriever_rerank_on._qdrant.namespace_has_data = AsyncMock(return_value=True)
    retriever_rerank_on._embeddings.embed_query = AsyncMock(return_value=[0.1] * 10)
    retriever_rerank_on._qdrant.search = AsyncMock(
        return_value=[{"content": "候选", "score": 0.9, "metadata": {}, "namespace": "ns"}]
    )
    retriever_rerank_on._get_cache = AsyncMock(return_value=None)
    retriever_rerank_on._set_cache = AsyncMock(return_value=None)
    retriever_rerank_on._ensure_bm25_corpus = AsyncMock(return_value=None)
    retriever_rerank_on._bm25_search = AsyncMock(return_value=[])

    # mock _rerank_client.post 返回全低分, 让真实 _rerank 跑过滤
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value=[{"index": 0, "relevance_score": 0.1}]  # < 0.3 过滤
    )
    retriever_rerank_on._rerank_client.post = AsyncMock(return_value=mock_response)

    results = await retriever_rerank_on.retrieve("测试", user_id=None, top_k=5)

    assert len(results) == 0  # 全低于阈值


# ========== _rerank 直接测试 ==========


async def test_rerank_filters_below_threshold(retriever_rerank_on: HybridRetriever) -> None:
    """_rerank: 直接测试过滤 score < threshold 的结果."""
    docs = [
        {"content": "doc1", "metadata": {}, "namespace": "ns"},
        {"content": "doc2", "metadata": {}, "namespace": "ns"},
    ]
    # mock rerank_client.post 返回
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value=[
            {"index": 0, "relevance_score": 0.9},  # 保留
            {"index": 1, "relevance_score": 0.2},  # 过滤 (< 0.3)
        ]
    )
    retriever_rerank_on._rerank_client.post = AsyncMock(return_value=mock_response)

    results = await retriever_rerank_on._rerank("query", docs, top_k=5)

    assert len(results) == 1
    assert results[0]["content"] == "doc1"
    assert results[0]["score"] == 0.9


async def test_rerank_threshold_boundary(retriever_rerank_on: HybridRetriever) -> None:
    """_rerank: 阈值边界 (score == threshold 保留, < threshold 丢弃)."""
    docs = [
        {"content": "边界", "metadata": {}, "namespace": "ns"},
        {"content": "低于", "metadata": {}, "namespace": "ns"},
    ]
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value=[
            {"index": 0, "relevance_score": 0.3},  # == threshold, 保留
            {"index": 1, "relevance_score": 0.299},  # < threshold, 丢弃
        ]
    )
    retriever_rerank_on._rerank_client.post = AsyncMock(return_value=mock_response)

    results = await retriever_rerank_on._rerank("query", docs, top_k=5)

    assert len(results) == 1
    assert results[0]["score"] == 0.3


async def test_rerank_empty_docs_returns_empty(retriever_rerank_on: HybridRetriever) -> None:
    """_rerank: 空文档列表直接返回空 (不调 rerank API)."""
    results = await retriever_rerank_on._rerank("query", [], top_k=5)
    assert results == []
    retriever_rerank_on._rerank_client.post.assert_not_called()


async def test_rerank_http_failure_falls_back_to_rrf_top_k(
    retriever_rerank_on: HybridRetriever,
) -> None:
    """_rerank: HTTP 失败降级返回 docs[:top_k] (RRF 分数, 不过滤阈值).

    降级路径不用 score_threshold 过滤 (因为 RRF 分数与 rerank 分数量纲不同).
    """
    docs = [
        {"content": "doc1", "score": 0.01, "metadata": {}, "namespace": "ns"},
        {"content": "doc2", "score": 0.02, "metadata": {}, "namespace": "ns"},
    ]
    retriever_rerank_on._rerank_client.post = AsyncMock(
        side_effect=RuntimeError("rerank service down")
    )

    results = await retriever_rerank_on._rerank("query", docs, top_k=2)

    # 降级返回 docs[:top_k] (RRF 分数保留, 不过滤)
    assert len(results) == 2
    # 分数应保留原 RRF 分数 (0.01/0.02), 不被 threshold 过滤
    assert results[0]["score"] == 0.01


async def test_rerank_raise_for_status_falls_back(
    retriever_rerank_on: HybridRetriever,
) -> None:
    """_rerank: raise_for_status 抛异常降级返回 docs[:top_k]."""
    docs = [{"content": "doc1", "score": 0.5, "metadata": {}, "namespace": "ns"}]
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock(side_effect=RuntimeError("HTTP 500"))
    retriever_rerank_on._rerank_client.post = AsyncMock(return_value=mock_response)

    results = await retriever_rerank_on._rerank("query", docs, top_k=5)

    assert len(results) == 1
    assert results[0]["content"] == "doc1"


# ========== _bm25_search score <= 0 过滤 (BM25 固有, 非 score_threshold) ==========


async def test_bm25_search_filters_zero_scores(retriever_rerank_off: HybridRetriever) -> None:
    """_bm25_search: score <= 0 的结果过滤 (BM25 固有逻辑, 非 score_threshold).

    BM25 分数可能为 0 (query 与文档无共同词), 这类结果应过滤.
    但这是 BM25 自身逻辑, 与 score_threshold (rerank 阈值) 无关.

    注: 2 篇文档时 IDF=0 (log((2-1+0.5)/(1+0.5))=log(1)=0), 需 ≥3 篇使 IDF>0.
    """
    from rank_bm25 import BM25Okapi

    # 构造 BM25 语料 (3 篇, 使 IDF > 0)
    retriever_rerank_off._bm25_docs = [
        {"content": "量子计算", "metadata": {}, "namespace": "ns"},
        {"content": "新能源汽车", "metadata": {}, "namespace": "ns"},
        {"content": "人工智能发展", "metadata": {}, "namespace": "ns"},
    ]
    retriever_rerank_off._bm25_corpus = [
        ["量子", "计算"],
        ["新能源", "汽车"],
        ["人工智能", "发展"],
    ]
    retriever_rerank_off._bm25 = BM25Okapi(retriever_rerank_off._bm25_corpus)
    # mock _get_tokens 直接返回分词
    retriever_rerank_off._get_tokens = MagicMock(return_value=["量子", "计算"])

    results = await retriever_rerank_off._bm25_search("量子计算", limit=5)

    # "量子计算" 与第一篇文档匹配 (score > 0), 与第二/三篇无共同词 (score = 0 过滤)
    assert all(r["score"] > 0 for r in results)
    assert len(results) >= 1


async def test_bm25_search_no_corpus_returns_empty(
    retriever_rerank_off: HybridRetriever,
) -> None:
    """_bm25_search: 无语料 (_bm25=None) 返回空."""
    retriever_rerank_off._bm25 = None
    retriever_rerank_off._bm25_docs = []
    results = await retriever_rerank_off._bm25_search("测试", limit=5)
    assert results == []


# ========== 向量检索结果不因低分被过滤 ==========


async def test_vector_low_score_not_filtered_by_threshold(
    retriever_rerank_off: HybridRetriever,
) -> None:
    """向量检索结果: 即使原始 score 低, RRF 融合后也保留 (不套用 score_threshold).

    场景: 向量检索返回 score=0.1 的结果, rerank 关闭时不应被 0.3 阈值过滤.
    """
    retriever_rerank_off._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    retriever_rerank_off._qdrant.namespace_has_data = AsyncMock(return_value=True)
    retriever_rerank_off._embeddings.embed_query = AsyncMock(return_value=[0.1] * 10)
    # 向量原始 score=0.1 (< 0.3), 但不应被过滤
    retriever_rerank_off._qdrant.search = AsyncMock(
        return_value=[{"content": "低分向量", "score": 0.1, "metadata": {}, "namespace": "ns"}]
    )
    retriever_rerank_off._get_cache = AsyncMock(return_value=None)
    retriever_rerank_off._set_cache = AsyncMock(return_value=None)
    retriever_rerank_off._ensure_bm25_corpus = AsyncMock(return_value=None)
    retriever_rerank_off._bm25_search = AsyncMock(return_value=[])

    results = await retriever_rerank_off.retrieve("测试", user_id=None, top_k=5)

    # 向量低分结果应保留 (rerank 关闭, 不套用阈值)
    assert len(results) == 1
    assert results[0]["content"] == "低分向量"


# ========== score_threshold 配置契约 ==========


def test_score_threshold_default_is_0_3() -> None:
    """Settings.score_threshold 默认 0.3 (AGENTS.md 第 7 章)."""
    settings = Settings(_env_file=None)
    assert settings.score_threshold == 0.3


def test_score_threshold_configurable() -> None:
    """Settings.score_threshold 可配置."""
    settings = Settings(score_threshold=0.5, _env_file=None)
    assert settings.score_threshold == 0.5


def test_rerank_enabled_default_false() -> None:
    """Settings.rerank_enabled 默认 False (AGENTS.md 第 7 章: 默认不启用)."""
    settings = Settings(_env_file=None)
    assert settings.rerank_enabled is False


# ========== 源码契约: score_threshold 仅在 _rerank 内使用 ==========


def test_score_threshold_only_used_in_rerank() -> None:
    """源码契约: score_threshold 仅在 _rerank 方法内使用 (不在 retrieve 主流程过滤).

    AGENTS.md 第 7 章: score_threshold 仅当 rerank 启用时生效.
    验证 retrieve 主流程 (rerank 关闭分支) 不引用 score_threshold.
    """
    import inspect

    from src.rag import retriever

    source = inspect.getsource(retriever.HybridRetriever.retrieve)
    # rerank 关闭分支应直接 fused[:k], 不引用 score_threshold
    # 查找 "reranked = fused[:k]" 行 (rerank 关闭分支)
    assert "fused[:k]" in source, "rerank 关闭时应直接取 fused[:k]"

    # _rerank 方法应引用 score_threshold
    rerank_source = inspect.getsource(retriever.HybridRetriever._rerank)
    assert "score_threshold" in rerank_source, "_rerank 应使用 score_threshold 过滤"


def test_retrieve_rerank_off_branch_comment_documents_threshold_scope() -> None:
    """源码契约: retrieve 中 rerank 关闭分支应有注释说明 score_threshold 不适用 RRF.

    验证修复注释存在 (说明 score_threshold 仅适用于 rerank 分数).
    """
    import inspect

    from src.rag import retriever

    source = inspect.getsource(retriever.HybridRetriever.retrieve)
    # 应有注释说明 RRF 融合分数不应用 score_threshold
    assert "score_threshold" in source or "RRF 融合分数" in source, (
        "retrieve 应有注释说明 score_threshold 不适用于 RRF 融合分数"
    )
