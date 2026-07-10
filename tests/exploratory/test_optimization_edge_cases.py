"""探索性测试: 8 项优化的边界场景.

AGENTS.md 第 7/13 章硬约束:
- 所有外部输入经 Pydantic 校验
- 上下文压缩 CONTEXT_MAX_CHARS=800_000
- 测试数据隔离: namespace=test_* + user_id=test_* + session_id=test_*

覆盖边界场景:
1. 空查询的搜索缓存
2. 单字符查询的 FastEmbed 嵌入
3. 超长文本 (>800K chars) 的上下文压缩
4. 并发 100 个搜索缓存读取
5. batch_size=64 边界 (63/64/65 chunks)

本测试使用 mock 模拟异常场景, 不依赖容器栈.
标记为 exploratory + unit (unit 确保 conftest 不跳过).

执行方式:
    pytest tests/exploratory/test_optimization_edge_cases.py -v -m exploratory -s
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import time
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings

pytestmark = [pytest.mark.exploratory, pytest.mark.unit]


def _make_settings(**overrides: object) -> Settings:
    """构造测试 Settings (跳过 .env 加载)."""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


# ========== 空查询的搜索缓存 ==========


async def test_empty_query_search_cache() -> None:
    """边界: 空查询字符串的搜索缓存行为.

    _cached_search 用 sha256(query) 生成缓存 key.
    空查询 sha256("") 应正常生成 key, 不抛异常.
    缓存写入和读取应正常工作.
    """
    from src.skills.researcher.research_conductor import ResearchConductor

    settings = _make_settings(search_cache_ttl=300)
    mock_llm = MagicMock()
    mock_cm = MagicMock()
    mock_pf = MagicMock()
    conductor = ResearchConductor(
        settings=settings,
        llm=mock_llm,  # type: ignore[arg-type]
        context_manager=mock_cm,  # type: ignore[arg-type]
        prompt_family=mock_pf,  # type: ignore[arg-type]
    )

    # 内存 Redis 模拟
    redis_store: dict[str, str] = {}

    class _MiniRedis:
        async def get(self, key: str) -> str | None:
            return redis_store.get(key)

        async def setex(self, key: str, ttl: int, value: str) -> None:
            redis_store[key] = value

    class _MockSearcher:
        name = "test_engine"

        async def search(
            self, query: str, *, max_results: int = 5, **_kw: Any
        ) -> list[dict[str, Any]]:
            return [{"title": "空查询结果", "url": "http://example.com", "query": query}]

    searcher = _MockSearcher()

    with patch(
        "src.skills.researcher.research_conductor.get_redis_client",
        new=AsyncMock(return_value=_MiniRedis()),
    ):
        # 空查询首次搜索
        result_1 = await conductor._cached_search(
            searcher, "", max_results=5, query_domains=None, user_id="test"
        )
        # 空查询第二次 (应命中缓存)
        result_2 = await conductor._cached_search(
            searcher, "", max_results=5, query_domains=None, user_id="test"
        )

    assert len(result_1) == 1, "空查询应返回 1 条结果"
    assert len(result_2) == 1, "空查询缓存命中应返回 1 条结果"
    # 空查询的 sha256 key 应正常生成并写入缓存
    empty_hash = hashlib.sha256(b"").hexdigest()
    expected_key = f"{settings.agent_name}:test:search:result:test_engine:{empty_hash}"
    assert expected_key in redis_store, "空查询缓存 key 应已写入 Redis"


async def test_empty_query_sha256_key_generation() -> None:
    """边界: 空查询的 sha256 key 生成不抛异常.

    hashlib.sha256("".encode("utf-8")) 应返回有效的 64 字符 hex 字符串.
    """
    key = hashlib.sha256(b"").hexdigest()
    assert len(key) == 64, f"sha256 应返回 64 字符 hex, 实际: {len(key)}"
    assert key == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


# ========== 单字符查询的 FastEmbed 嵌入 ==========


async def test_single_char_query_fastembed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """边界: 单字符查询的 FastEmbed 嵌入.

    FastEmbed 应能处理单字符输入 (如 "A", "你", "1"),
    返回 512 维向量, 不抛异常.
    """
    from src.rag import fastembed_client as fe_module
    from src.rag.fastembed_client import FastEmbedClient

    # 安装 fake fastembed
    fake_module = types.ModuleType("fastembed")

    class _FakeTextEmbedding:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.01] * 512 for _ in texts]

    fake_module.TextEmbedding = _FakeTextEmbedding  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", fake_module)

    settings = _make_settings()
    fe_module._FASTEMBED_CACHE.clear()
    client = FastEmbedClient(settings)

    # 测试多种单字符输入
    single_chars = ["A", "你", "1", " ", "。", "\n", "\t"]
    for char in single_chars:
        vector = await client.embed_text(char)
        assert len(vector) == 512, f"单字符 '{char}' 嵌入维度应为 512, 实际: {len(vector)}"
        # 向量不应全零 (至少有 0.01 占位)
        assert any(v != 0.0 for v in vector), f"单字符 '{char}' 嵌入向量不应全零"

    fe_module._FASTEMBED_CACHE.clear()


async def test_single_char_query_cached_correctly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """边界: 单字符查询的缓存正确性.

    单字符查询的 sha256 key 应唯一, 缓存命中/未命中行为正确.
    """
    from src.rag import fastembed_client as fe_module
    from src.rag.fastembed_client import FastEmbedClient, _cache_key

    fake_module = types.ModuleType("fastembed")

    class _FakeTextEmbedding:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def embed(self, texts: list[str]) -> list[list[float]]:
            # 不同文本返回不同向量 (基于首字符 ord), 验证缓存隔离
            return [[float(ord(t[0]) if t else 0) / 1000.0] * 512 for t in texts]

    fake_module.TextEmbedding = _FakeTextEmbedding  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", fake_module)

    settings = _make_settings()
    fe_module._FASTEMBED_CACHE.clear()
    client = FastEmbedClient(settings)

    # 嵌入 "A" (ord=65 → 0.065)
    vec_1 = await client.embed_text("A")
    # 第二次嵌入 "A" (应命中缓存, 返回相同向量)
    vec_2 = await client.embed_text("A")
    # 嵌入 "B" (ord=66 → 0.066, 不同字符, 不应命中缓存)
    vec_3 = await client.embed_text("B")

    assert vec_1 == vec_2, "相同单字符应命中缓存返回相同向量"
    assert vec_1 != vec_3, "不同单字符应返回不同向量"

    # 验证缓存 key 不同
    key_a = _cache_key("A")
    key_b = _cache_key("B")
    assert key_a != key_b, "不同单字符的缓存 key 应不同"

    fe_module._FASTEMBED_CACHE.clear()


# ========== 超长文本 (>800K chars) 的上下文压缩 ==========


async def test_ultra_long_text_context_compression() -> None:
    """边界: 超长文本 (>800K chars) 的上下文压缩.

    AGENTS.md 第 6 章: CONTEXT_MAX_CHARS=800_000.
    compression_threshold=8000, 超过阈值触发 _hybrid_compress.
    本测试验证 850K 字符消息列表能被正确压缩 (不超时/不崩溃).
    """
    from src.skills.researcher.context_manager import ContextManager

    settings = _make_settings(compression_threshold=8000)
    cm = ContextManager(settings)

    # mock LLM 摘要 (避免真实 LLM 调用)
    cm._llm = MagicMock()
    cm._llm.achat = AsyncMock(return_value=MagicMock(content="超长文本摘要结果"))

    # 生成 850K 字符的消息列表
    # 每条消息 ~950 字符 (19字×50), 850 条 ≈ 813K 字符 > 800K
    base_content = "人工智能在医疗领域的应用前景非常广阔。" * 50  # ~950 字符
    messages = [{"role": "user", "content": base_content + f" 第{i}条消息"} for i in range(850)]
    total_chars = sum(len(m["content"]) for m in messages)
    assert total_chars > 800_000, f"测试数据应超过 800K chars, 实际: {total_chars}"

    start = time.perf_counter()
    compressed = await cm.compress_messages(messages)
    elapsed = time.perf_counter() - start

    # 压缩后消息数应远少于原始 (摘要 + 滑动窗口)
    assert len(compressed) < len(messages), (
        f"压缩后消息数 {len(compressed)} 应少于原始 {len(messages)}"
    )
    # 应在合理时间内完成 (< 30s, mock LLM)
    assert elapsed < 30.0, f"850K 字符压缩耗时 {elapsed:.3f}s 超过阈值 30s"
    # 第一条应为摘要消息
    assert "[历史摘要]" in compressed[0].get("content", ""), "压缩后第一条应为历史摘要消息"
    print(f"\n[ultra_long_text] {total_chars} chars → {len(compressed)} msgs in {elapsed:.3f}s")


async def test_context_compression_at_exact_threshold() -> None:
    """边界: 上下文字符数恰好等于 compression_threshold 时不触发压缩.

    compress_messages: total_chars > compression_threshold 才触发 _hybrid_compress.
    等于阈值时应走 SlidingWindowCompressor (不触发 LLM 摘要).
    """
    from src.skills.researcher.context_manager import ContextManager

    settings = _make_settings(compression_threshold=8000)
    cm = ContextManager(settings)

    # mock LLM (不应被调用)
    cm._llm = MagicMock()
    cm._llm.achat = AsyncMock(return_value=MagicMock(content="不应调用"))

    # 生成恰好 8000 字符的消息 (等于阈值, 不触发 _hybrid_compress)
    msg_content = "A" * 8000
    messages = [{"role": "user", "content": msg_content}]

    compressed = await cm.compress_messages(messages)

    # 等于阈值不应触发 LLM 摘要
    cm._llm.achat.assert_not_called()
    assert len(compressed) > 0, "等于阈值应走 SlidingWindowCompressor 返回非空"


# ========== 并发 100 个搜索缓存读取 ==========


async def test_concurrent_100_search_cache_reads() -> None:
    """边界: 并发 100 个搜索缓存读取.

    _cached_search 应支持并发读取, 不出现竞态条件.
    100 个并发请求中, 相同 query+engine+user 的应命中缓存.
    """
    from src.skills.researcher.research_conductor import ResearchConductor

    settings = _make_settings(search_cache_ttl=300)
    mock_llm = MagicMock()
    mock_cm = MagicMock()
    mock_pf = MagicMock()
    conductor = ResearchConductor(
        settings=settings,
        llm=mock_llm,  # type: ignore[arg-type]
        context_manager=mock_cm,  # type: ignore[arg-type]
        prompt_family=mock_pf,  # type: ignore[arg-type]
    )

    # 线程安全的内存 Redis
    redis_store: dict[str, str] = {}

    class _MiniRedis:
        async def get(self, key: str) -> str | None:
            return redis_store.get(key)

        async def setex(self, key: str, ttl: int, value: str) -> None:
            redis_store[key] = value

    search_count = 0

    class _MockSearcher:
        name = "test_engine"

        async def search(
            self, query: str, *, max_results: int = 5, **_kw: Any
        ) -> list[dict[str, Any]]:
            nonlocal search_count
            search_count += 1
            return [{"title": f"结果 for {query}", "url": "http://example.com"}]

    searcher = _MockSearcher()

    async def _cached_search_once(query: str) -> list[dict[str, Any]]:
        return await conductor._cached_search(
            searcher, query, max_results=5, query_domains=None, user_id="test_concurrent"
        )

    with patch(
        "src.skills.researcher.research_conductor.get_redis_client",
        new=AsyncMock(return_value=_MiniRedis()),
    ):
        # 先写入一条缓存
        await _cached_search_once("预热查询")

        # 并发 100 个相同查询 (应全部命中缓存)
        tasks = [_cached_search_once("预热查询") for _ in range(100)]
        results = await asyncio.gather(*tasks)

    # 所有结果应相同 (缓存命中)
    assert len(results) == 100, f"应返回 100 个结果, 实际: {len(results)}"
    for r in results:
        assert len(r) == 1, "每个结果应含 1 条搜索结果"
    # searcher 只应被调用 1 次 (预热), 100 个并发请求全命中缓存
    assert search_count == 1, (
        f"并发 100 个缓存读取应全部命中 (searcher 仅调用 1 次), 实际调用: {search_count} 次"
    )


async def test_concurrent_different_queries_no_collision() -> None:
    """边界: 并发不同查询的缓存 key 不冲突.

    100 个并发不同查询应生成 100 个不同缓存 key, 互不干扰.
    """
    from src.skills.researcher.research_conductor import ResearchConductor

    settings = _make_settings(search_cache_ttl=300)
    mock_llm = MagicMock()
    mock_cm = MagicMock()
    mock_pf = MagicMock()
    conductor = ResearchConductor(
        settings=settings,
        llm=mock_llm,  # type: ignore[arg-type]
        context_manager=mock_cm,  # type: ignore[arg-type]
        prompt_family=mock_pf,  # type: ignore[arg-type]
    )

    redis_store: dict[str, str] = {}

    class _MiniRedis:
        async def get(self, key: str) -> str | None:
            return redis_store.get(key)

        async def setex(self, key: str, ttl: int, value: str) -> None:
            redis_store[key] = value

    class _MockSearcher:
        name = "test_engine"

        async def search(
            self, query: str, *, max_results: int = 5, **_kw: Any
        ) -> list[dict[str, Any]]:
            return [{"title": f"结果 for {query}", "url": "http://example.com"}]

    searcher = _MockSearcher()

    queries = [f"并发查询 {i}" for i in range(100)]

    async def _search(query: str) -> list[dict[str, Any]]:
        return await conductor._cached_search(
            searcher, query, max_results=5, query_domains=None, user_id="test"
        )

    with patch(
        "src.skills.researcher.research_conductor.get_redis_client",
        new=AsyncMock(return_value=_MiniRedis()),
    ):
        tasks = [_search(q) for q in queries]
        results = await asyncio.gather(*tasks)

    # 100 个不同查询应生成 100 个不同缓存 key
    assert len(redis_store) == 100, f"100 个不同查询应生成 100 个缓存 key, 实际: {len(redis_store)}"
    # 所有结果应非空
    for r in results:
        assert len(r) == 1, "每个查询应返回 1 条结果"


# ========== batch_size=64 边界 (63/64/65 chunks) ==========


async def test_batch_size_64_boundary_63_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """边界: 63 chunks (batch_size=64 边界, 不足 1 批).

    _embed_parallel: 63 >= 32 (threshold) → 分批路径.
    63 / 64 = 1 批 (63 chunks), 不应丢失任何向量.
    """
    from src.rag import fastembed_client as fe_module
    from src.rag.fastembed_client import FastEmbedClient

    fake_module = types.ModuleType("fastembed")

    class _FakeTextEmbedding:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def embed(self, texts: list[str]) -> list[list[float]]:
            # 每个向量含唯一标识 (索引), 便于验证不丢失
            return [[float(i)] * 512 for i in range(len(texts))]

    fake_module.TextEmbedding = _FakeTextEmbedding  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", fake_module)

    settings = _make_settings()
    fe_module._FASTEMBED_CACHE.clear()
    client = FastEmbedClient(settings)

    texts = [f"chunk {i}" for i in range(63)]
    vectors = await client.embed_texts(texts)

    assert len(vectors) == 63, f"63 chunks 应返回 63 个向量, 实际: {len(vectors)}"
    # 验证向量不丢失 (每个向量的第一个元素 = 原始索引)
    for i, vec in enumerate(vectors):
        assert vec[0] == float(i), f"向量 {i} 的标识应为 {i}, 实际: {vec[0]} (可能丢失或乱序)"

    fe_module._FASTEMBED_CACHE.clear()


async def test_batch_size_64_boundary_64_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """边界: 64 chunks (batch_size=64 边界, 恰好 1 批).

    64 / 64 = 1 批 (64 chunks), 不应丢失任何向量.
    """
    from src.rag import fastembed_client as fe_module
    from src.rag.fastembed_client import FastEmbedClient

    fake_module = types.ModuleType("fastembed")

    class _FakeTextEmbedding:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[float(i)] * 512 for i in range(len(texts))]

    fake_module.TextEmbedding = _FakeTextEmbedding  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", fake_module)

    settings = _make_settings()
    fe_module._FASTEMBED_CACHE.clear()
    client = FastEmbedClient(settings)

    texts = [f"chunk {i}" for i in range(64)]
    vectors = await client.embed_texts(texts)

    assert len(vectors) == 64, f"64 chunks 应返回 64 个向量, 实际: {len(vectors)}"
    for i, vec in enumerate(vectors):
        assert vec[0] == float(i), f"向量 {i} 的标识应为 {i}, 实际: {vec[0]}"

    fe_module._FASTEMBED_CACHE.clear()


async def test_batch_size_64_boundary_65_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """边界: 65 chunks (batch_size=64 边界, 超出 1 批).

    65 / 64 = 2 批 (64 + 1), 不应丢失任何向量.
    验证跨批次结果拼接正确性.
    """
    from src.rag import fastembed_client as fe_module
    from src.rag.fastembed_client import FastEmbedClient

    fake_module = types.ModuleType("fastembed")

    class _FakeTextEmbedding:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def embed(self, texts: list[str]) -> list[list[float]]:
            # 从文本 "chunk {i}" 解析索引, 确保跨批次唯一标识不丢失
            result = []
            for t in texts:
                try:
                    idx = int(t.split()[-1])
                except (ValueError, IndexError):
                    idx = 0
                result.append([float(idx)] * 512)
            return result

    fake_module.TextEmbedding = _FakeTextEmbedding  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", fake_module)

    settings = _make_settings()
    fe_module._FASTEMBED_CACHE.clear()
    client = FastEmbedClient(settings)

    texts = [f"chunk {i}" for i in range(65)]
    vectors = await client.embed_texts(texts)

    assert len(vectors) == 65, f"65 chunks 应返回 65 个向量, 实际: {len(vectors)}"
    for i, vec in enumerate(vectors):
        assert vec[0] == float(i), f"向量 {i} 的标识应为 {i}, 实际: {vec[0]}"

    fe_module._FASTEMBED_CACHE.clear()


async def test_batch_size_64_boundary_all_vectors_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """边界: 63/64/65 chunks 对比验证, 所有场景向量数 = 输入数.

    综合验证 batch_size=64 边界附近不丢失向量.
    """
    from src.rag import fastembed_client as fe_module
    from src.rag.fastembed_client import FastEmbedClient

    fake_module = types.ModuleType("fastembed")

    class _FakeTextEmbedding:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.01] * 512 for _ in texts]

    fake_module.TextEmbedding = _FakeTextEmbedding  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", fake_module)

    settings = _make_settings()
    client = FastEmbedClient(settings)

    for chunk_count in [63, 64, 65]:
        fe_module._FASTEMBED_CACHE.clear()
        texts = [f"chunk {i}" for i in range(chunk_count)]
        vectors = await client.embed_texts(texts)
        assert len(vectors) == chunk_count, (
            f"{chunk_count} chunks 应返回 {chunk_count} 个向量, 实际: {len(vectors)}"
        )

    fe_module._FASTEMBED_CACHE.clear()
