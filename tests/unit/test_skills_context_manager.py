"""单元测试: ContextManager 静态纯函数 + _embeddings_rerank 缓存复用.

验证:
- _split_documents / _cosine_similarity / _truncate_by_words (静态纯函数, 不依赖外部服务)
- _embeddings_rerank: FastEmbed 精排 + doc 级/chunk 级双缓存 (P1 优化 trace 4ad14970)
- _chunk_cache key 生成: sha256(doc_text) + sha256(chunk) 双键
- _post_filter_compress 复用 _embeddings_rerank 的 embedding 缓存

单元测试在构建期执行, 不依赖外部服务.
_embeddings_rerank 测试全部 mock FastEmbedClient (无 ONNX 加载).
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.rag.embeddings_filter import DEFAULT_SEPARATORS, recursive_split
from src.skills.researcher.context_manager import ContextManager

pytestmark = pytest.mark.unit


# ========== _split_documents ==========


def test_split_documents_short_paragraph_kept_as_one() -> None:
    """测试短段落 (<= chunk_size) 保留为单个 chunk."""
    docs = [{"content": "短段落", "url": "https://x.com"}]
    chunks = ContextManager._split_documents(docs, chunk_size=1000, chunk_overlap=100)
    assert len(chunks) == 1
    assert chunks[0]["content"] == "短段落"
    assert chunks[0]["source"] == "https://x.com"


def test_split_documents_multiple_paragraphs() -> None:
    """测试多段落生成多个 chunk."""
    content = "段落一\n\n段落二\n\n段落三"
    docs = [{"content": content, "url": "https://x.com"}]
    chunks = ContextManager._split_documents(docs, chunk_size=1000, chunk_overlap=100)
    assert len(chunks) == 3
    assert chunks[0]["content"] == "段落一"
    assert chunks[1]["content"] == "段落二"
    assert chunks[2]["content"] == "段落三"


def test_split_documents_long_paragraph_sliding_window() -> None:
    """测试长段落按 chunk_size 滑窗分块."""
    long_para = "a" * 2500  # 2500 字符
    docs = [{"content": long_para, "url": "https://x.com"}]
    chunks = ContextManager._split_documents(docs, chunk_size=1000, chunk_overlap=100)
    # 步长 = 1000 - 100 = 900; 2500 字符应生成 ceil(2500/900) = 3 块
    assert len(chunks) == 3
    # 每块不超过 chunk_size
    for chunk in chunks:
        assert len(chunk["content"]) <= 1000


def test_split_documents_empty_content_skipped() -> None:
    """测试空 content 的 doc 被跳过."""
    docs = [
        {"content": "", "url": "https://x.com"},
        {"content": "实际内容", "url": "https://y.com"},
    ]
    chunks = ContextManager._split_documents(docs, chunk_size=1000, chunk_overlap=100)
    assert len(chunks) == 1
    assert chunks[0]["content"] == "实际内容"


def test_split_documents_empty_list_returns_empty() -> None:
    """测试空文档列表返回空列表."""
    assert ContextManager._split_documents([], chunk_size=1000, chunk_overlap=100) == []


def test_split_documents_default_kwargs() -> None:
    """测试默认 chunk_size=1000, chunk_overlap=100 (无显式传参)."""
    docs = [{"content": "a" * 1500}]
    chunks = ContextManager._split_documents(docs)
    # 应使用默认值, 1500 字符按步长 900 分成 2 块
    assert len(chunks) == 2


def test_split_documents_source_field_from_url() -> None:
    """测试 chunk 的 source 字段来自 doc 的 url."""
    docs = [{"content": "x", "url": "https://example.com/page"}]
    chunks = ContextManager._split_documents(docs, chunk_size=1000, chunk_overlap=100)
    assert chunks[0]["source"] == "https://example.com/page"


# ========== _cosine_similarity_batch ==========


def test_cosine_similarity_identical_vectors_returns_1() -> None:
    """测试相同向量余弦相似度为 1.0."""
    vec = [1.0, 2.0, 3.0]
    sim = ContextManager._cosine_similarity_batch([vec], [vec])[0, 0]
    assert sim == pytest.approx(1.0, rel=1e-6)


def test_cosine_similarity_orthogonal_vectors_returns_0() -> None:
    """测试正交向量余弦相似度为 0.0."""
    vec1 = [1.0, 0.0]
    vec2 = [0.0, 1.0]
    sim = ContextManager._cosine_similarity_batch([vec1], [vec2])[0, 0]
    assert sim == pytest.approx(0.0, abs=1e-6)


def test_cosine_similarity_opposite_vectors_returns_minus_1() -> None:
    """测试相反向量余弦相似度为 -1.0."""
    vec1 = [1.0, 1.0]
    vec2 = [-1.0, -1.0]
    sim = ContextManager._cosine_similarity_batch([vec1], [vec2])[0, 0]
    assert sim == pytest.approx(-1.0, rel=1e-6)


def test_cosine_similarity_empty_vectors_returns_0() -> None:
    """测试空输入返回空矩阵 (shape=(0, 0))."""
    result = ContextManager._cosine_similarity_batch([], [])
    assert result.shape == (0, 0)


def test_cosine_similarity_different_lengths_raises() -> None:
    """测试维度不一致时 numpy 矩阵乘法抛出 ValueError (批量 API 不兼容不同维度)."""
    vec1 = [1.0, 2.0, 3.0]
    vec2 = [1.0, 2.0]
    with pytest.raises(ValueError):
        ContextManager._cosine_similarity_batch([vec1], [vec2])


def test_cosine_similarity_zero_vector_returns_0() -> None:
    """测试零向量 (norm=0) 返回 0.0 (避免除零, 范数置 1)."""
    vec1 = [0.0, 0.0]
    vec2 = [1.0, 2.0]
    sim = ContextManager._cosine_similarity_batch([vec1], [vec2])[0, 0]
    assert sim == pytest.approx(0.0, abs=1e-6)


def test_cosine_similarity_partial_similarity() -> None:
    """测试部分相似向量 (0 < sim < 1)."""
    vec1 = [1.0, 0.0]
    vec2 = [1.0, 1.0]
    sim = ContextManager._cosine_similarity_batch([vec1], [vec2])[0, 0]
    # cos(45°) = sqrt(2)/2 ≈ 0.7071
    assert 0.5 < sim < 0.9


# ========== _truncate_by_words ==========


def test_truncate_by_words_under_limit_returns_all() -> None:
    """测试总词数 < max_words 时返回全部."""
    texts = ["hello world", "foo bar"]
    result = ContextManager._truncate_by_words(texts, max_words=10)
    assert "hello world" in result
    assert "foo bar" in result


def test_truncate_by_words_exceeds_limit_truncates() -> None:
    """测试总词数超 max_words 时截断."""
    texts = ["one two three", "four five six"]
    result = ContextManager._truncate_by_words(texts, max_words=4)
    # 第一段 3 词全保留, 第二段仅保留 1 词
    assert "one two three" in result
    assert "four" in result
    # 不应含完整第二段
    assert "five" not in result


def test_truncate_by_words_first_text_exceeds_limit() -> None:
    """测试第一段已超 max_words 时仅返回截断后的第一段."""
    texts = ["a b c d e f"]
    result = ContextManager._truncate_by_words(texts, max_words=3)
    assert result == "a b c"


def test_truncate_by_words_empty_list_returns_empty() -> None:
    """测试空列表返回空串."""
    assert ContextManager._truncate_by_words([], max_words=100) == ""


def test_truncate_by_words_zero_max_returns_empty() -> None:
    """测试 max_words=0 返回空串."""
    result = ContextManager._truncate_by_words(["a b c"], max_words=0)
    assert result == ""


def test_truncate_by_words_joins_with_double_newline() -> None:
    """测试多段拼接用双换行分隔."""
    texts = ["hello world", "foo bar"]
    result = ContextManager._truncate_by_words(texts, max_words=10)
    assert "hello world\n\nfoo bar" in result


def test_truncate_by_words_handles_chinese_text() -> None:
    """测试中文文本按空格分词 (中文无空格则视为单词)."""
    texts = ["中文无空格视为一词"]
    result = ContextManager._truncate_by_words(texts, max_words=5)
    # 中文无空格, split() 返回单元素列表
    assert "中文无空格视为一词" in result


# ========== _embeddings_rerank: FastEmbed 精排 + 缓存复用 ==========
#
# _embeddings_rerank 缓存 rerank 结果的 embedding,
# 供后续 _post_filter_compress 复用. 拆分为
# doc 级 + chunk 级双缓存, 覆盖单 chunk 与多 chunk 场景.


@pytest.fixture()
def rerank_settings() -> Settings:
    """测试用 Settings (小 chunk_size 便于测试多 chunk 缓存场景)."""
    return Settings(
        embeddings_filter_chunk_size=20,
        embeddings_filter_chunk_overlap=0,
        _env_file=None,
    )


@pytest.fixture()
def mock_fastembed() -> MagicMock:
    """构造 mock FastEmbedClient (embed_texts 由各测试设置返回值)."""
    fe = MagicMock()
    fe.embed_texts = AsyncMock(return_value=[])
    return fe


@pytest.fixture()
def context_manager_for_rerank(
    rerank_settings: Settings,
    mock_fastembed: MagicMock,
) -> ContextManager:
    """构造 ContextManager (注入 mock FastEmbed, 保留真实 WrittenContentCompressor).

    保留真实 WrittenContentCompressor 以验证 _chunk_cache 双键缓存机制;
    FastEmbed/LLM/Embeddings 全部 mock, 不依赖外部服务.
    """
    with (
        patch(
            "src.skills.researcher.context_manager.get_embeddings_client",
            return_value=MagicMock(),
        ),
        patch(
            "src.skills.researcher.context_manager.get_llm_client",
            return_value=MagicMock(),
        ),
        patch(
            "src.skills.researcher.context_manager.get_fastembed_client",
            return_value=mock_fastembed,
        ),
    ):
        cm = ContextManager(rerank_settings)
    return cm


def _expected_chunks(doc: str, settings: Settings) -> list[str]:
    """用与实现相同的 recursive_split 计算预期 chunk 列表."""
    return recursive_split(
        doc,
        separators=DEFAULT_SEPARATORS,
        chunk_size=settings.embeddings_filter_chunk_size,
        chunk_overlap=settings.embeddings_filter_chunk_overlap,
    ) or [doc]


# ========== _embeddings_rerank: 基本行为 ==========


@pytest.mark.asyncio
async def test_embeddings_rerank_empty_documents_returns_empty(
    context_manager_for_rerank: ContextManager,
    mock_fastembed: MagicMock,
) -> None:
    """空文档列表返回 [], 不调用 FastEmbed."""
    result = await context_manager_for_rerank._embeddings_rerank("query", [], 5)

    assert result == []
    mock_fastembed.embed_texts.assert_not_called()


@pytest.mark.asyncio
async def test_embeddings_rerank_sorts_by_cosine_similarity_descending(
    context_manager_for_rerank: ContextManager,
    mock_fastembed: MagicMock,
) -> None:
    """结果按 FastEmbed 余弦相似度降序排列."""
    query = "AI 研究"
    doc1 = "AI 研究报告"  # 高相似
    doc2 = "美食烹饪指南"  # 低相似
    doc3 = "人工智能研究"  # 最高相似 (与 query 向量相同)
    # query_emb=[1,0], doc1_emb=[0.9,0.1], doc2_emb=[0.1,0.9], doc3_emb=[1,0]
    mock_fastembed.embed_texts.return_value = [
        [1.0, 0.0],  # query
        [0.9, 0.1],  # doc1
        [0.1, 0.9],  # doc2
        [1.0, 0.0],  # doc3 (与 query 相同, sim=1.0)
    ]

    result = await context_manager_for_rerank._embeddings_rerank(
        query, [doc1, doc2, doc3], max_results=3
    )

    # 降序: doc3 (sim=1.0) > doc1 (sim≈0.994) > doc2 (sim≈0.110)
    assert len(result) == 3
    assert result[0] == doc3
    assert result[1] == doc1
    assert result[2] == doc2


@pytest.mark.asyncio
async def test_embeddings_rerank_truncates_to_max_results(
    context_manager_for_rerank: ContextManager,
    mock_fastembed: MagicMock,
) -> None:
    """结果数超过 max_results 时截断."""
    docs = [f"文档{i}" for i in range(5)]
    # 5 个文档 + 1 个 query = 6 个向量
    mock_fastembed.embed_texts.return_value = [
        [1.0, 0.0],  # query
        [0.9, 0.0],  # doc0
        [0.8, 0.0],  # doc1
        [0.7, 0.0],  # doc2
        [0.6, 0.0],  # doc3
        [0.5, 0.0],  # doc4
    ]

    result = await context_manager_for_rerank._embeddings_rerank("q", docs, max_results=2)

    assert len(result) == 2
    # 相似度最高的两个: doc0 (0.9) > doc1 (0.8)
    assert result[0] == "文档0"
    assert result[1] == "文档1"


@pytest.mark.asyncio
async def test_embeddings_rerank_calls_fastembed_once_with_query_plus_docs(
    context_manager_for_rerank: ContextManager,
    mock_fastembed: MagicMock,
) -> None:
    """FastEmbed 批量调用: 1 次 embed_texts, 入参为 [query] + documents (优化)."""
    query = "查询"
    docs = ["文档一", "文档二", "文档三"]
    mock_fastembed.embed_texts.return_value = [
        [1.0] * 512,  # query
        [0.9] * 512,  # doc1
        [0.8] * 512,  # doc2
        [0.7] * 512,  # doc3
    ]

    await context_manager_for_rerank._embeddings_rerank(query, docs, max_results=3)

    # 仅调用 1 次 (批量优化, 消除串行 await)
    mock_fastembed.embed_texts.assert_called_once()
    # 入参应为 [query] + docs
    call_args = mock_fastembed.embed_texts.call_args
    texts_arg = call_args.args[0]
    assert texts_arg == [query, "文档一", "文档二", "文档三"]


@pytest.mark.asyncio
async def test_embeddings_rerank_degrades_on_fastembed_failure(
    context_manager_for_rerank: ContextManager,
    mock_fastembed: MagicMock,
) -> None:
    """FastEmbed 异常时降级返回原 BM25 结果前 N 条."""
    docs = ["文档一", "文档二", "文档三"]
    mock_fastembed.embed_texts.side_effect = RuntimeError("FastEmbed model load failed")

    result = await context_manager_for_rerank._embeddings_rerank("q", docs, max_results=2)

    # 降级: 返回 documents[:max_results]
    assert result == ["文档一", "文档二"]


# ========== _chunk_cache key 生成: doc 级缓存 (sha256(doc_text)) ==========


@pytest.mark.asyncio
async def test_embeddings_rerank_caches_doc_level_key(
    context_manager_for_rerank: ContextManager,
    mock_fastembed: MagicMock,
    rerank_settings: Settings,
) -> None:
    """_embeddings_rerank 缓存 doc 级 key: sha256(doc_text) -> doc_emb."""
    doc = "短文档"  # <= chunk_size, 单 chunk
    doc_emb = [0.5, 0.5]
    mock_fastembed.embed_texts.return_value = [
        [1.0, 0.0],  # query
        doc_emb,  # doc
    ]

    await context_manager_for_rerank._embeddings_rerank("q", [doc], max_results=1)

    cache = context_manager_for_rerank._written_compressor._chunk_cache
    expected_key = hashlib.sha256(doc.encode("utf-8")).hexdigest()
    assert expected_key in cache
    assert cache[expected_key] == doc_emb


@pytest.mark.asyncio
async def test_embeddings_rerank_caches_multiple_docs_separately(
    context_manager_for_rerank: ContextManager,
    mock_fastembed: MagicMock,
) -> None:
    """多个文档各自生成独立的 doc 级缓存 key."""
    doc1 = "文档一"
    doc2 = "文档二"
    doc1_emb = [0.9, 0.1]
    doc2_emb = [0.1, 0.9]
    mock_fastembed.embed_texts.return_value = [
        [1.0, 0.0],  # query
        doc1_emb,
        doc2_emb,
    ]

    await context_manager_for_rerank._embeddings_rerank("q", [doc1, doc2], max_results=2)

    cache = context_manager_for_rerank._written_compressor._chunk_cache
    key1 = hashlib.sha256(doc1.encode("utf-8")).hexdigest()
    key2 = hashlib.sha256(doc2.encode("utf-8")).hexdigest()
    assert cache[key1] == doc1_emb
    assert cache[key2] == doc2_emb


# ========== _chunk_cache key 生成: chunk 级缓存 (sha256(chunk)) ==========


@pytest.mark.asyncio
async def test_embeddings_rerank_single_chunk_doc_level_equals_chunk_level_key(
    context_manager_for_rerank: ContextManager,
    mock_fastembed: MagicMock,
    rerank_settings: Settings,
) -> None:
    """单 chunk 场景: doc 级 key 与 chunk 级 key 相同 (doc <= chunk_size 不切分)."""
    doc = "短文档"  # 3 字符 < chunk_size=20, 不切分
    doc_emb = [0.5, 0.5]
    mock_fastembed.embed_texts.return_value = [[1.0, 0.0], doc_emb]

    await context_manager_for_rerank._embeddings_rerank("q", [doc], max_results=1)

    cache = context_manager_for_rerank._written_compressor._chunk_cache
    doc_key = hashlib.sha256(doc.encode("utf-8")).hexdigest()
    chunks = _expected_chunks(doc, rerank_settings)
    # 单 chunk: chunks == [doc], chunk key == doc key
    assert len(chunks) == 1
    assert chunks[0] == doc
    chunk_key = hashlib.sha256(chunks[0].encode("utf-8")).hexdigest()
    assert doc_key == chunk_key
    # 缓存中应有该 key (doc 级与 chunk 级写入同一 key)
    assert cache[doc_key] == doc_emb


@pytest.mark.asyncio
async def test_embeddings_rerank_multi_chunk_caches_all_chunk_keys(
    context_manager_for_rerank: ContextManager,
    mock_fastembed: MagicMock,
    rerank_settings: Settings,
) -> None:
    """多 chunk 场景: 每个 chunk 的 sha256 key 均缓存, 值为同一 doc_emb (P1 trace 4ad14970)."""
    # 3 段落各 9 字符, 总 31 > chunk_size=20, 切分为多 chunk
    doc = "段落一AAAA\n\n段落二BBBB\n\n段落三CCCC"
    doc_emb = [0.7, 0.3]
    mock_fastembed.embed_texts.return_value = [[1.0, 0.0], doc_emb]

    await context_manager_for_rerank._embeddings_rerank("q", [doc], max_results=1)

    cache = context_manager_for_rerank._written_compressor._chunk_cache
    chunks = _expected_chunks(doc, rerank_settings)
    # 应切分为多个 chunk (多 chunk 场景)
    assert len(chunks) >= 2, f"预期多 chunk, 实际 {len(chunks)} chunk"
    # 每个 chunk 的 key 应在缓存中, 值均为 doc_emb
    for chunk in chunks:
        chunk_key = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
        assert chunk_key in cache, f"chunk key 不在缓存中: {chunk_key}"
        assert cache[chunk_key] == doc_emb
    # doc 级 key 也应在缓存中
    doc_key = hashlib.sha256(doc.encode("utf-8")).hexdigest()
    assert doc_key in cache
    assert cache[doc_key] == doc_emb


@pytest.mark.asyncio
async def test_embeddings_rerank_chunk_level_key_aligns_with_compute_embedding_batch(
    context_manager_for_rerank: ContextManager,
    mock_fastembed: MagicMock,
    rerank_settings: Settings,
) -> None:
    """chunk 级缓存 key 与 compute_embedding_batch 查询 key 对齐.

    双缓存确保多 chunk 场景命中: doc 级缓存 sha256(doc_text),
    chunk 级缓存 sha256(chunk) 与 compute_embedding_batch 查询 key 对齐.
    """
    doc = "段落一AAAA\n\n段落二BBBB\n\n段落三CCCC"
    doc_emb = [0.7, 0.3]
    mock_fastembed.embed_texts.return_value = [[1.0, 0.0], doc_emb]

    await context_manager_for_rerank._embeddings_rerank("q", [doc], max_results=1)

    cache = context_manager_for_rerank._written_compressor._chunk_cache
    chunks = _expected_chunks(doc, rerank_settings)
    # compute_embedding_batch 会用 sha256(chunk) 查询, 应全部命中
    for chunk in chunks:
        query_key = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
        assert cache.get(query_key) is not None, (
            f"compute_embedding_batch 查询 key 未命中: chunk={chunk!r}"
        )


# ========== _post_filter_compress 复用 _embeddings_rerank 的 embedding 缓存 ==========


@pytest.mark.asyncio
async def test_post_filter_compress_reuses_cached_embeddings_no_new_fastembed_call(
    context_manager_for_rerank: ContextManager,
    mock_fastembed: MagicMock,
    rerank_settings: Settings,
) -> None:
    """_post_filter_compress 通过 compute_embedding_batch 复用缓存, 不再调用 FastEmbed.

    P1 优化核心: _embeddings_rerank 预填充 chunk 级缓存后,
    _post_filter_compress 的 compute_embedding_batch 全部命中缓存,
    避免重复 embed_texts 调用.
    """
    doc1 = "段落一AAAA\n\n段落二BBBB"  # 多 chunk
    doc2 = "短文档"  # 单 chunk
    doc1_emb = [0.9, 0.1]
    doc2_emb = [0.1, 0.9]
    mock_fastembed.embed_texts.return_value = [
        [1.0, 0.0],  # query
        doc1_emb,
        doc2_emb,
    ]

    # 1. _embeddings_rerank 预填充缓存 (调用 embed_texts 1 次)
    await context_manager_for_rerank._embeddings_rerank("q", [doc1, doc2], max_results=2)
    assert mock_fastembed.embed_texts.call_count == 1

    # 2. 重置 mock 调用计数 (保留 return_value)
    mock_fastembed.embed_texts.reset_mock()

    # 3. compute_embedding_batch 应全部命中缓存, 不调用 embed_texts
    (
        all_chunks,
        all_embs,
    ) = await context_manager_for_rerank._written_compressor.compute_embedding_batch([doc1, doc2])
    mock_fastembed.embed_texts.assert_not_called()

    # 4. 返回的 embeddings 应为缓存的 doc_emb (非空)
    for _chunks, embs in zip(all_chunks, all_embs, strict=False):
        for emb in embs:
            assert emb != [], "缓存命中应返回非空 embedding"


@pytest.mark.asyncio
async def test_post_filter_compress_reuses_embeddings_via_method(
    context_manager_for_rerank: ContextManager,
    mock_fastembed: MagicMock,
    rerank_settings: Settings,
) -> None:
    """_post_filter_compress 方法复用 _embeddings_rerank 缓存 (端到端验证)."""
    doc1 = "段落一AAAA\n\n段落二BBBB"
    doc2 = "短文档"
    doc1_emb = [0.9, 0.1]
    doc2_emb = [0.1, 0.9]
    mock_fastembed.embed_texts.return_value = [
        [1.0, 0.0],
        doc1_emb,
        doc2_emb,
    ]

    # 1. 预填充缓存
    await context_manager_for_rerank._embeddings_rerank("q", [doc1, doc2], max_results=2)
    mock_fastembed.embed_texts.reset_mock()

    # 2. 调用 _post_filter_compress (内部调 compute_embedding_batch + check_and_update_batch)
    span = MagicMock()
    context = await context_manager_for_rerank._post_filter_compress(
        compressed=[doc1, doc2],
        user_id="test_user",
        session_id="test_session",
        span=span,
        layer="test_layer",
    )

    # 3. FastEmbed 不应被再次调用 (缓存全命中)
    mock_fastembed.embed_texts.assert_not_called()
    # 4. span.update 被调用 (含 context_len)
    span.update.assert_called()
    # 5. 返回非空上下文 (两文档均保留, 因 _written_embeddings 初始为空)
    assert len(context) > 0


@pytest.mark.asyncio
async def test_compute_embedding_batch_cache_miss_calls_fastembed(
    context_manager_for_rerank: ContextManager,
    mock_fastembed: MagicMock,
) -> None:
    """未预热缓存时 compute_embedding_batch 调用 FastEmbed (反向验证缓存机制)."""
    doc = "段落一AAAA\n\n段落二BBBB"
    expected_chunks = _expected_chunks(doc, context_manager_for_rerank.settings)
    miss_embs = [[0.5, 0.5] for _ in expected_chunks]
    mock_fastembed.embed_texts.return_value = miss_embs

    # 不调用 _embeddings_rerank 预热, 直接调 compute_embedding_batch
    (
        all_chunks,
        all_embs,
    ) = await context_manager_for_rerank._written_compressor.compute_embedding_batch([doc])

    # 缓存未命中, 应调用 FastEmbed
    mock_fastembed.embed_texts.assert_called_once()
    # 返回的 chunks 应与预期一致
    assert len(all_chunks) == 1
    assert all_chunks[0] == expected_chunks
