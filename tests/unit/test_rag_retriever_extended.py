"""单元测试: HybridRetriever 扩展方法.

验证 src/rag/retriever.py:
- _deduplicate_by_content_hash: 重复内容去重, body 字段回退
- _cache_key: 格式 {agent_id}:{user_id}:rag:retriever:{md5(query)}
- _get_cache: Redis None / 异常降级
- _set_cache: Redis None 跳过 / ex=TTL / 异常静默
- _rerank: 成功路径 / 空 docs / HTTP 失败降级
- _bm25_search: _bm25=None / score<=0 过滤
- update_bm25_corpus: 空语料 _bm25=None

AGENTS.md 第 13 章: 单元测试不依赖外部服务.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.rag.retriever import HybridRetriever


def _make_retriever(settings: Settings | None = None) -> HybridRetriever:
    """构造 HybridRetriever (不依赖外部服务, 构造时不连接)."""
    settings = settings or Settings(_env_file=None)
    return HybridRetriever(settings)


# ========== _deduplicate_by_content_hash ==========


def test_deduplicate_by_content_hash_removes_duplicates() -> None:
    """重复 content 去重, 保留首次出现."""
    retriever = _make_retriever()
    results = [
        {"content": "文档A", "score": 0.9},
        {"content": "文档B", "score": 0.8},
        {"content": "文档A", "score": 0.7},  # 重复
    ]
    deduped = retriever._deduplicate_by_content_hash(results)
    assert len(deduped) == 2
    assert deduped[0]["content"] == "文档A"
    assert deduped[1]["content"] == "文档B"


def test_deduplicate_by_content_hash_body_fallback() -> None:
    """content 缺失时回退到 body 字段."""
    retriever = _make_retriever()
    results = [
        {"body": "内容X", "score": 0.9},
        {"body": "内容X", "score": 0.8},  # 重复 (按 body hash)
        {"body": "内容Y", "score": 0.7},
    ]
    deduped = retriever._deduplicate_by_content_hash(results)
    assert len(deduped) == 2


def test_deduplicate_by_content_hash_empty() -> None:
    """空列表返回空列表."""
    retriever = _make_retriever()
    assert retriever._deduplicate_by_content_hash([]) == []


# ========== _cache_key ==========


def test_cache_key_format() -> None:
    """缓存键格式: {agent_id}:{user_id}:rag:retriever:{sha256(query)}."""
    settings = Settings(agent_name="test-agent", _env_file=None)
    retriever = _make_retriever(settings)
    key = retriever._cache_key("hello", "user123")
    expected_hash = hashlib.sha256(b"hello").hexdigest()
    assert key == f"test-agent:user123:rag:retriever:{expected_hash}"


def test_cache_key_user_id_fallback() -> None:
    """user_id 缺失时用 anonymous 常量.

    AGENTS.md 第 8 章: default_user_id 环境变量已移除, RAG 层无 user_id 时
    用 _ANONYMOUS_USER_ID = "anonymous" 常量替代.
    """
    settings = Settings(agent_name="test-agent", _env_file=None)
    retriever = _make_retriever(settings)
    key = retriever._cache_key("query", None)
    expected_hash = hashlib.sha256(b"query").hexdigest()
    assert key == f"test-agent:anonymous:rag:retriever:{expected_hash}"


# ========== _get_cache ==========


@pytest.mark.asyncio
async def test_get_cache_redis_none_returns_none() -> None:
    """Redis 为 None 时返回 None."""
    retriever = _make_retriever()
    retriever._redis = None
    result = await retriever._get_cache("key")
    assert result is None


@pytest.mark.asyncio
async def test_get_cache_redis_exception_returns_none() -> None:
    """Redis 异常时降级返回 None."""
    retriever = _make_retriever()

    class _FailingRedis:
        async def get(self, _key: str) -> Any:
            raise RuntimeError("redis down")

    retriever._redis = _FailingRedis()
    result = await retriever._get_cache("key")
    assert result is None


@pytest.mark.asyncio
async def test_get_cache_redis_returns_data() -> None:
    """Redis 命中时返回反序列化的数据.

    _get_cache 命中后会调用 zadd 更新 LRU 访问时间 (redis_cache_lru_enabled 默认 True),
    故 _FakeRedis 需实现 zadd 方法, 否则 AttributeError 被 except 捕获返回 None.
    """
    retriever = _make_retriever()
    cached_data = [{"content": "doc", "score": 0.9}]

    class _FakeRedis:
        def __init__(self, data: str | None) -> None:
            self._data = data

        async def get(self, _key: str) -> str | None:
            return self._data

        async def zadd(self, _key: str, _mapping: dict[str, float]) -> int:
            """LRU 访问时间更新: 简单返回 0 即可, 测试不关心 LRU 副作用."""
            return 0

    retriever._redis = _FakeRedis(json.dumps(cached_data))
    retriever._redis_initialized = True  # 避免 _ensure_redis 覆盖 mock
    result = await retriever._get_cache("key")
    assert result == cached_data


@pytest.mark.asyncio
async def test_get_cache_redis_miss_returns_none() -> None:
    """Redis 未命中 (返回 None) 时返回 None."""
    retriever = _make_retriever()

    class _FakeRedis:
        async def get(self, _key: str) -> None:
            return None

    retriever._redis = _FakeRedis()
    result = await retriever._get_cache("key")
    assert result is None


# ========== _set_cache ==========


@pytest.mark.asyncio
async def test_set_cache_redis_none_skips() -> None:
    """Redis 为 None 时静默跳过."""
    retriever = _make_retriever()
    retriever._redis = None
    await retriever._set_cache("key", [{"content": "doc"}])  # 不应抛异常


@pytest.mark.asyncio
async def test_set_cache_writes_with_ttl() -> None:
    """Redis 写入, 验证 ex=TTL."""
    retriever = _make_retriever()

    class _CapturingRedis:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def set(self, key: str, value: str, ex: int | None = None) -> Any:
            self.calls.append({"key": key, "value": value, "ex": ex})

    fake_redis = _CapturingRedis()
    retriever._redis = fake_redis
    retriever._redis_initialized = True  # 避免 _ensure_redis 覆盖 mock
    data = [{"content": "doc", "score": 0.9}]
    await retriever._set_cache("test-key", data)

    assert len(fake_redis.calls) == 1
    call = fake_redis.calls[0]
    assert call["key"] == "test-key"
    assert call["ex"] == HybridRetriever.RETRIEVER_CACHE_TTL
    assert json.loads(call["value"]) == data


@pytest.mark.asyncio
async def test_set_cache_redis_exception_silent() -> None:
    """Redis 异常时静默 (不抛)."""
    retriever = _make_retriever()

    class _FailingRedis:
        async def set(self, _key: str, _value: str, ex: int | None = None) -> Any:
            raise RuntimeError("redis write failed")

    retriever._redis = _FailingRedis()
    await retriever._set_cache("key", [{"content": "doc"}])  # 不应抛异常


# ========== _rerank ==========


class _FakeRerankResponse:
    """伪造 httpx rerank 响应."""

    def __init__(self, json_data: Any, status_code: int = 200) -> None:
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._json_data


class _FakeRerankClient:
    """伪造 httpx.AsyncClient for rerank."""

    def __init__(
        self,
        response: _FakeRerankResponse | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._response = response
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> _FakeRerankResponse:
        self.calls.append({"url": url, **kwargs})
        if self._exc is not None:
            raise self._exc
        if self._response is None:
            return _FakeRerankResponse([])
        return self._response

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_rerank_success_path() -> None:
    """rerank 成功路径: 按 relevance_score 重排, 低于阈值丢弃."""
    settings = Settings(score_threshold=0.3, _env_file=None)
    retriever = _make_retriever(settings)

    docs = [
        {"content": "文档A", "score": 0.9},
        {"content": "文档B", "score": 0.8},
        {"content": "文档C", "score": 0.7},
    ]
    fake_response = _FakeRerankResponse(
        [
            {"index": 2, "relevance_score": 0.95},
            {"index": 0, "relevance_score": 0.5},
            {"index": 1, "relevance_score": 0.2},  # 低于阈值 0.3
        ]
    )
    retriever._rerank_client = _FakeRerankClient(response=fake_response)  # type: ignore[assignment]

    result = await retriever._rerank("query", docs, top_k=3)

    # 应返回 2 个 (0.2 被阈值过滤)
    assert len(result) == 2
    assert result[0]["content"] == "文档C"  # score=0.95
    assert result[0]["score"] == 0.95
    assert result[1]["content"] == "文档A"  # score=0.5
    assert result[1]["score"] == 0.5


@pytest.mark.asyncio
async def test_rerank_empty_docs_returns_empty() -> None:
    """rerank 空 docs 返回 []."""
    retriever = _make_retriever()
    result = await retriever._rerank("query", [], top_k=5)
    assert result == []


@pytest.mark.asyncio
async def test_rerank_http_failure_falls_back() -> None:
    """rerank HTTP 失败时降级用 docs[:top_k]."""
    settings = Settings(score_threshold=0.3, _env_file=None)
    retriever = _make_retriever(settings)

    docs = [
        {"content": "文档A", "score": 0.9},
        {"content": "文档B", "score": 0.8},
        {"content": "文档C", "score": 0.7},
    ]
    retriever._rerank_client = _FakeRerankClient(exc=RuntimeError("rerank service down"))  # type: ignore[assignment]

    result = await retriever._rerank("query", docs, top_k=2)

    # 降级返回 docs[:top_k]
    assert len(result) == 2
    assert result[0]["content"] == "文档A"
    assert result[1]["content"] == "文档B"


@pytest.mark.asyncio
async def test_rerank_raise_for_status_falls_back() -> None:
    """rerank 返回 HTTP 错误状态时降级用 docs[:top_k]."""
    settings = Settings(score_threshold=0.3, _env_file=None)
    retriever = _make_retriever(settings)

    docs = [{"content": "文档A", "score": 0.9}]
    fake_response = _FakeRerankResponse([], status_code=500)
    retriever._rerank_client = _FakeRerankClient(response=fake_response)  # type: ignore[assignment]

    result = await retriever._rerank("query", docs, top_k=1)
    assert len(result) == 1
    assert result[0]["content"] == "文档A"


# ========== _bm25_search ==========


@pytest.mark.asyncio
async def test_bm25_search_no_corpus_returns_empty() -> None:
    """_bm25=None 时返回 []."""
    retriever = _make_retriever()
    retriever._bm25 = None
    retriever._bm25_docs = []
    result = await retriever._bm25_search("query", 10)
    assert result == []


@pytest.mark.asyncio
async def test_bm25_search_filters_zero_scores() -> None:
    """score<=0 的文档被过滤 (无共享词项的文档得分为 0)."""
    retriever = _make_retriever()

    docs = [
        {"content": "机器学习是人工智能的子领域", "metadata": {}, "namespace": "ns1"},
        {"content": "深度学习使用神经网络", "metadata": {}, "namespace": "ns1"},
        {"content": "今天天气很好适合出去玩", "metadata": {}, "namespace": "ns2"},  # 不相关
    ]
    retriever.update_bm25_corpus(docs)

    results = await retriever._bm25_search("机器学习", 10)
    contents = [r["content"] for r in results]
    # 不相关文档应被过滤 (score <= 0)
    assert "今天天气很好适合出去玩" not in contents
    assert len(results) >= 1


# ========== update_bm25_corpus ==========


def test_update_bm25_corpus_empty_sets_bm25_none() -> None:
    """空语料时 _bm25 设为 None."""
    retriever = _make_retriever()
    retriever.update_bm25_corpus([])
    assert retriever._bm25 is None
    assert retriever._bm25_docs == []
    assert retriever._bm25_corpus == []


def test_update_bm25_corpus_non_empty_initializes_bm25() -> None:
    """非空语料时 _bm25 被初始化."""
    retriever = _make_retriever()
    docs = [{"content": "测试文档", "metadata": {}, "namespace": "ns"}]
    retriever.update_bm25_corpus(docs)
    assert retriever._bm25 is not None
    assert len(retriever._bm25_docs) == 1
    assert len(retriever._bm25_corpus) == 1


# ========== score_threshold 不应用于 RRF 融合分数 (追加 P6 集成) ==========


async def test_score_threshold_not_applied_to_rrf_scores() -> None:
    """score_threshold=0.3 在 RRF 融合分数不应用 (仅 rerank 启用时生效).

    AGENTS.md 第 7 章: score_threshold 默认 0.3, 低于阈值丢弃
    (仅当 rerank 启用时生效, RRF 融合分数不应用此阈值).

    场景: rerank_enabled=False, RRF 融合后分数远低于 0.3 (如 0.01),
    但不应被 score_threshold 过滤. 验证 retrieve 主流程 (rerank 关闭分支)
    直接取 fused[:k], 不套用 score_threshold.
    """
    settings = Settings(
        rerank_enabled=False,
        score_threshold=0.3,
        vector_weight=0.7,
        bm25_weight=0.3,
        rrf_k=60,
        _env_file=None,
    )
    retriever = _make_retriever(settings)

    # mock 必要依赖: namespace 有数据, 缓存未命中
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    retriever._qdrant.namespace_has_data = AsyncMock(return_value=True)
    retriever._get_cache = AsyncMock(return_value=None)
    retriever._set_cache = AsyncMock(return_value=None)
    retriever._ensure_bm25_corpus = AsyncMock(return_value=None)

    # 向量检索返回低分结果 (RRF 融合后约 0.7/61 ≈ 0.0115, 远低于 0.3)
    retriever._embeddings.embed_query = AsyncMock(return_value=[0.1] * 10)
    retriever._qdrant.search = AsyncMock(
        return_value=[
            {"content": "低分候选1", "score": 0.9, "metadata": {}, "namespace": "ns"},
            {"content": "低分候选2", "score": 0.8, "metadata": {}, "namespace": "ns"},
        ]
    )
    # BM25 返回空 (聚焦验证 RRF 低分不过滤)
    retriever._bm25_search = AsyncMock(return_value=[])

    results = await retriever.retrieve("测试查询", user_id=None, top_k=5)

    # 即使 RRF 分数 < 0.3, rerank 关闭时也应返回结果 (不套用 score_threshold)
    assert len(results) == 2
    contents = [r["content"] for r in results]
    assert "低分候选1" in contents
    assert "低分候选2" in contents
    # 验证 RRF 融合分数确实 < 0.3 (确认是低分场景)
    assert all(r["score"] < 0.3 for r in results)


# ========== Embeddings head-based 采样 (追加 P6 集成) ==========


async def test_embeddings_head_based_sampling() -> None:
    """Embeddings head-based 采样 (tracing_embedding_sample_rate=0.5).

    AGENTS.md 第 10 章: trace_embedding head-based 采样, 默认
    tracing_embedding_sample_rate=0.5 (高频 embed 调用降采样减存储压力).

    验证:
    - sample_rate=1.0: 所有 embed 调用均创建实际 span (不采样)
    - sample_rate=0.0: 所有 embed 调用降级为 _NoopSpan (全采样丢弃)
    - 默认 sample_rate=0.5: random.random() > 0.5 时降级 _NoopSpan
    """
    from src.observability import tracing as tracing_module
    from src.observability.tracing import AgentInsightTracer, _NoopSpan

    # mock _get_client 返回非 None (绕过 SDK 不可用降级, 进入采样逻辑)
    mock_client = MagicMock()
    mock_ctx = MagicMock()
    mock_span = MagicMock()
    mock_client.start_as_current_observation.return_value = mock_ctx
    mock_ctx.__enter__ = MagicMock(return_value=mock_span)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    # 1. sample_rate=1.0: 不采样, 应创建实际 span (调用 start_as_current_observation)
    settings_full = Settings(tracing_embedding_sample_rate=1.0, _env_file=None)
    with (
        patch.object(tracing_module, "_get_client", return_value=mock_client),
        patch.object(tracing_module, "get_settings", return_value=settings_full),
        patch.object(tracing_module.random, "random", return_value=0.9),
    ):
        tracer_full = AgentInsightTracer()
        async with tracer_full.trace_embedding(name="embed-test") as span:
            # sample_rate=1.0 不采样, span 应为实际 span (非 _NoopSpan)
            assert not isinstance(span, _NoopSpan), (
                "sample_rate=1.0 时应创建实际 span, 不应降级 _NoopSpan"
            )
    # 应调用 start_as_current_observation (创建实际 span)
    mock_client.start_as_current_observation.assert_called_once()

    # 2. sample_rate=0.0: 全采样丢弃, 应 yield _NoopSpan (不创建实际 span)
    mock_client.reset_mock()
    settings_zero = Settings(tracing_embedding_sample_rate=0.0, _env_file=None)
    with (
        patch.object(tracing_module, "_get_client", return_value=mock_client),
        patch.object(tracing_module, "get_settings", return_value=settings_zero),
        patch.object(tracing_module.random, "random", return_value=0.9),
    ):
        tracer_zero = AgentInsightTracer()
        async with tracer_zero.trace_embedding(name="embed-test") as span:
            assert isinstance(span, _NoopSpan), "sample_rate=0.0 时应降级 yield _NoopSpan"
    # 不应调用 start_as_current_observation (采样丢弃, 不创建 span)
    mock_client.start_as_current_observation.assert_not_called()

    # 3. 默认 sample_rate=0.5 + random.random()=0.6 (> 0.5): 应降级 _NoopSpan
    mock_client.reset_mock()
    settings_default = Settings(tracing_embedding_sample_rate=0.5, _env_file=None)
    with (
        patch.object(tracing_module, "_get_client", return_value=mock_client),
        patch.object(tracing_module, "get_settings", return_value=settings_default),
        patch.object(tracing_module.random, "random", return_value=0.6),
    ):
        tracer_default = AgentInsightTracer()
        async with tracer_default.trace_embedding(name="embed-test") as span:
            assert isinstance(span, _NoopSpan), (
                "sample_rate=0.5 + random=0.6 (>0.5) 时应降级 _NoopSpan"
            )
    mock_client.start_as_current_observation.assert_not_called()

    # 4. 默认 sample_rate=0.5 + random.random()=0.3 (<= 0.5): 应创建实际 span
    mock_client.reset_mock()
    with (
        patch.object(tracing_module, "_get_client", return_value=mock_client),
        patch.object(tracing_module, "get_settings", return_value=settings_default),
        patch.object(tracing_module.random, "random", return_value=0.3),
    ):
        tracer_default2 = AgentInsightTracer()
        async with tracer_default2.trace_embedding(name="embed-test") as span:
            # 应进入实际 span 分支 (非 _NoopSpan)
            assert not isinstance(span, _NoopSpan), (
                "sample_rate=0.5 + random=0.3 (<=0.5) 时应创建实际 span"
            )
    mock_client.start_as_current_observation.assert_called_once()


# ========== BM25 单独路径: 仅向量无 BM25 结果时的降级 (追加 P6 集成) ==========


async def test_bm25_only_path_no_vector_results() -> None:
    """BM25 单独路径: 向量检索无结果时, BM25 结果仍可经 RRF 融合返回.

    AGENTS.md 第 7 章: 检索必须混合 BM25 + 向量. 本测试验证当向量检索
    返回空 (如 query embedding 失败或 Qdrant 无匹配) 时, BM25 路径仍能
    独立提供结果, RRF 融合不会因向量空而失败.

    场景:
    - 向量检索返回 [] (无结果)
    - BM25 检索返回 2 条结果
    - RRF 融合应仅基于 BM25 排名计算分数 (bm25_weight / (k + rank + 1))
    - 最终返回 BM25 结果 (经 _deduplicate_by_content_hash 去重)
    """
    settings = Settings(
        rerank_enabled=False,
        vector_weight=0.7,
        bm25_weight=0.3,
        rrf_k=60,
        _env_file=None,
    )
    retriever = _make_retriever(settings)

    # mock namespace 有数据, 缓存未命中
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    retriever._qdrant.namespace_has_data = AsyncMock(return_value=True)
    retriever._get_cache = AsyncMock(return_value=None)
    retriever._set_cache = AsyncMock(return_value=None)
    retriever._ensure_bm25_corpus = AsyncMock(return_value=None)

    # 向量检索返回空 (无向量结果)
    retriever._embeddings.embed_query = AsyncMock(return_value=[0.1] * 10)
    retriever._qdrant.search = AsyncMock(return_value=[])

    # BM25 检索返回 2 条结果
    bm25_results = [
        {
            "content": "BM25 相关文档1",
            "score": 5.0,
            "metadata": {"source": "bm25"},
            "namespace": "agent-data",
        },
        {
            "content": "BM25 相关文档2",
            "score": 3.0,
            "metadata": {"source": "bm25"},
            "namespace": "agent-data",
        },
    ]
    retriever._bm25_search = AsyncMock(return_value=bm25_results)

    results = await retriever.retrieve("测试查询", user_id=None, top_k=5)

    # 应返回 BM25 结果 (向量空不影响 BM25 路径)
    assert len(results) == 2
    contents = [r["content"] for r in results]
    assert "BM25 相关文档1" in contents
    assert "BM25 相关文档2" in contents

    # RRF 融合分数应仅基于 BM25 排名 (rank 0: 0.3/(60+1), rank 1: 0.3/(60+2))
    expected_first = 0.3 / (60 + 0 + 1)  # bm25_weight / (k + rank + 1)
    expected_second = 0.3 / (60 + 1 + 1)
    assert abs(results[0]["score"] - expected_first) < 1e-9
    assert abs(results[1]["score"] - expected_second) < 1e-9

    # 排序: BM25 rank 0 (score=5.0) 应在前, rank 1 (score=3.0) 应在后
    assert results[0]["content"] == "BM25 相关文档1"
    assert results[1]["content"] == "BM25 相关文档2"
