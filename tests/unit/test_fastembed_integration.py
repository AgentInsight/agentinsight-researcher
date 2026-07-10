"""单元测试: FastEmbed + ContextManager 集成测试.

验证 src/rag/fastembed_client.py 与 src/skills/researcher/context_manager.py 的集成:
- FastEmbed 嵌入结果被 ContextManager._embeddings_rerank 正确使用 (批量调用 + 余弦排序)
- WrittenContentCompressor._chunk_cache 双级缓存 (doc 级 + chunk 级) 命中场景
- _post_filter_compress 复用 _embeddings_rerank 已缓存的 embedding (零额外 FastEmbed 调用)
- FastEmbedClient batch_size=64 批量推理与进程内缓存交互

AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
所有外部依赖 (FastEmbed 模型 / TEI / LLM) 全部 mock.
AGENTS.md 第 7 章硬约束: 上下文压缩统一用 FastEmbed (bge-small-zh-v1.5, 512维),
不依赖远程 TEI.
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.rag.fastembed_client import FastEmbedClient
from src.skills.researcher.context_manager import ContextManager

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture(autouse=True)
def _clear_fastembed_cache() -> None:
    """每个用例前后清空 FastEmbed 进程内缓存 + 全局单例 (避免用例间污染)."""
    from src.rag import fastembed_client as fe_mod

    fe_mod._FASTEMBED_CACHE.clear()
    fe_mod._client = None
    yield
    fe_mod._FASTEMBED_CACHE.clear()
    fe_mod._client = None


@pytest.fixture()
def test_settings() -> Settings:
    """构造测试 Settings (跳过 .env 加载, 使用默认值)."""
    return Settings(_env_file=None)


@pytest.fixture()
def mock_embeddings() -> MagicMock:
    """构造 mock EmbeddingsClient (仅用于 is_circuit_open 探测, 返回 False)."""
    emb = MagicMock()
    emb.is_circuit_open = MagicMock(return_value=False)
    return emb


@pytest.fixture()
def mock_llm() -> MagicMock:
    """构造 mock LLMClient."""
    llm = MagicMock()
    llm.achat = AsyncMock(return_value=MagicMock(content="mocked summary"))
    return llm


@pytest.fixture()
def fastembed_mock() -> MagicMock:
    """构造 mock FastEmbedClient.

    embed_texts 按 input 文本索引返回可控向量 (512维), 用于验证排序与缓存:
    - 索引 0 (query): [1.0, 0.0, ...]
    - 索引 1 (doc1, 与 query 相似): [1.0, 0.0, ...]
    - 其他: 正交向量
    """
    client = MagicMock()

    async def _embed_texts(texts: list[str], **kwargs: object) -> list[list[float]]:
        vecs: list[list[float]] = []
        for i, _t in enumerate(texts):
            v = [0.0] * 512
            # query (i=0) 与 doc1 (i=1) 共享方向 [1,0,0,...] → 余弦相似度=1.0
            # 其余 doc 用正交方向 → 相似度=0.0
            v[0 if i <= 1 else (i - 1)] = 1.0
            vecs.append(v)
        return vecs

    client.embed_texts = AsyncMock(side_effect=_embed_texts)
    return client


@pytest.fixture()
def context_manager(
    test_settings: Settings,
    fastembed_mock: MagicMock,
    mock_embeddings: MagicMock,
    mock_llm: MagicMock,
) -> ContextManager:
    """构造 ContextManager (注入 mock 依赖, 保留真实 WrittenContentCompressor).

    关键: 不 mock _written_compressor, 使其 _chunk_cache 为真实 dict,
    便于验证 _embeddings_rerank 与 _post_filter_compress 间的缓存复用.
    """
    with (
        patch(
            "src.skills.researcher.context_manager.get_embeddings_client",
            return_value=mock_embeddings,
        ),
        patch(
            "src.skills.researcher.context_manager.get_llm_client",
            return_value=mock_llm,
        ),
        patch(
            "src.skills.researcher.context_manager.get_fastembed_client",
            return_value=fastembed_mock,
        ),
    ):
        cm = ContextManager(test_settings)
    return cm


@pytest.fixture()
def mock_model_env() -> tuple[MagicMock, MagicMock]:
    """Patch fastembed.TextEmbedding + anyio.Path, 返回 (mock_cls, mock_model).

    用于需要真实 FastEmbedClient (验证 _ensure_model / _embed_parallel 逻辑) 的用例.
    mock_model.embed 按批次大小返回等长 512 维向量.
    """
    mock_model = MagicMock()
    mock_model.embed = MagicMock(side_effect=lambda batch: [[0.1] * 512 for _ in batch])
    mock_te_cls = MagicMock(return_value=mock_model)

    mock_path = MagicMock()
    mock_path.exists = AsyncMock(return_value=False)

    with (
        patch("fastembed.TextEmbedding", mock_te_cls),
        patch("anyio.Path", return_value=mock_path),
    ):
        yield mock_te_cls, mock_model


# ========== TestFastEmbedRerankIntegration: _embeddings_rerank 与 FastEmbed 集成 ==========


class TestFastEmbedRerankIntegration:
    """验证 ContextManager._embeddings_rerank 正确使用 FastEmbed 嵌入结果."""

    async def test_embeddings_rerank_uses_fastembed_batched_call(
        self,
        context_manager: ContextManager,
        fastembed_mock: MagicMock,
    ) -> None:
        """_embeddings_rerank 合并为 1 次批量调用 embed_texts([query]+documents)."""
        docs = ["文档一内容", "文档二内容", "文档三内容"]
        await context_manager._embeddings_rerank("查询", docs, max_results=2)

        fastembed_mock.embed_texts.assert_awaited_once()
        call_args = fastembed_mock.embed_texts.call_args[0][0]
        assert call_args[0] == "查询"
        assert call_args[1:] == docs

    async def test_embeddings_rerank_ranks_by_cosine_similarity(
        self,
        context_manager: ContextManager,
        fastembed_mock: MagicMock,
    ) -> None:
        """_embeddings_rerank 按 FastEmbed 余弦相似度排序, 最相似的 doc 排首位."""
        docs = ["相似文档", "无关文档"]
        result = await context_manager._embeddings_rerank("查询", docs, max_results=2)

        # fastembed_mock: query(i=0) 与 doc1(i=1) 共享方向 → 相似度=1.0
        # doc2(i=2) 正交 → 相似度=0.0 → result[0] 应为 "相似文档"
        assert result[0] == "相似文档"
        assert len(result) == 2

    async def test_embeddings_rerank_empty_documents_returns_empty(
        self,
        context_manager: ContextManager,
        fastembed_mock: MagicMock,
    ) -> None:
        """空文档列表 → 直接返回空列表, 不调用 FastEmbed."""
        result = await context_manager._embeddings_rerank("查询", [], max_results=5)
        assert result == []
        fastembed_mock.embed_texts.assert_not_awaited()

    async def test_embeddings_rerank_failure_degrades_to_bm25_order(
        self,
        context_manager: ContextManager,
        fastembed_mock: MagicMock,
    ) -> None:
        """FastEmbed 调用失败 → 降级返回 BM25 结果前 N 条 (不抛异常)."""
        fastembed_mock.embed_texts = AsyncMock(side_effect=RuntimeError("model error"))
        docs = ["文档A", "文档B", "文档C"]
        result = await context_manager._embeddings_rerank("查询", docs, max_results=2)
        assert result == docs[:2]


# ========== TestChunkCacheDualLevel: _chunk_cache 双级缓存命中 ==========


class TestChunkCacheDualLevel:
    """验证 _embeddings_rerank 填充 _chunk_cache 的 doc 级 + chunk 级键."""

    async def test_chunk_cache_doc_level_key_populated(
        self,
        context_manager: ContextManager,
        fastembed_mock: MagicMock,
    ) -> None:
        """_embeddings_rerank 后, _chunk_cache 含 doc 级键 (sha256(doc_text))."""
        cache = context_manager._written_compressor._chunk_cache
        assert len(cache) == 0

        docs = ["短文档内容一", "短文档内容二"]
        await context_manager._embeddings_rerank("查询", docs, max_results=2)

        for doc_text in docs:
            doc_key = hashlib.sha256(doc_text.encode("utf-8")).hexdigest()
            assert doc_key in cache, f"doc 级缓存键缺失: {doc_text}"

    async def test_chunk_cache_chunk_level_key_populated(
        self,
        context_manager: ContextManager,
        fastembed_mock: MagicMock,
    ) -> None:
        """_embeddings_rerank 后, _chunk_cache 含 chunk 级键 (与 compute_embedding_batch 查询键对齐)."""
        from src.rag.embeddings_filter import DEFAULT_SEPARATORS, recursive_split

        cache = context_manager._written_compressor._chunk_cache
        docs = ["短文档内容"]
        await context_manager._embeddings_rerank("查询", docs, max_results=1)

        # 拆分为 chunk 级 (与 _embeddings_rerank 内部拆分逻辑一致)
        doc_text = docs[0]
        chunks = recursive_split(
            doc_text,
            separators=DEFAULT_SEPARATORS,
            chunk_size=context_manager.settings.embeddings_filter_chunk_size,
            chunk_overlap=context_manager.settings.embeddings_filter_chunk_overlap,
        ) or [doc_text]
        for chunk_text in chunks:
            chunk_key = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
            assert chunk_key in cache, f"chunk 级缓存键缺失: {chunk_text!r}"

    async def test_chunk_cache_hit_avoids_recomputation(
        self,
        context_manager: ContextManager,
        fastembed_mock: MagicMock,
    ) -> None:
        """compute_embedding_batch 命中 _chunk_cache 时不调用 FastEmbed."""
        docs = ["已缓存文档"]
        # 第一次: _embeddings_rerank 填充 _chunk_cache
        await context_manager._embeddings_rerank("查询", docs, max_results=1)
        assert fastembed_mock.embed_texts.await_count == 1

        # 第二次: compute_embedding_batch 应命中缓存, 不调用 FastEmbed
        chunks, embs = await context_manager._written_compressor.compute_embedding_batch(docs)
        assert fastembed_mock.embed_texts.await_count == 1, "缓存命中不应触发 FastEmbed 调用"
        # 返回的 embedding 应为缓存值 (非空)
        for emb in embs[0]:
            assert emb != [], "缓存命中应返回非空 embedding"


# ========== TestPostFilterCompressReuse: _post_filter_compress 复用 embedding ==========


class TestPostFilterCompressReuse:
    """验证 _post_filter_compress 复用 _embeddings_rerank 缓存的 embedding."""

    async def test_post_filter_compress_zero_fastembed_after_rerank(
        self,
        context_manager: ContextManager,
        fastembed_mock: MagicMock,
    ) -> None:
        """_embeddings_rerank 后调用 _post_filter_compress, 不再触发 FastEmbed 调用."""
        docs = ["文档甲内容", "文档乙内容"]
        reranked = await context_manager._embeddings_rerank("查询", docs, max_results=2)
        calls_after_rerank = fastembed_mock.embed_texts.await_count
        assert calls_after_rerank == 1

        span = MagicMock()
        await context_manager._post_filter_compress(
            reranked,
            user_id=None,
            session_id=None,
            span=span,
            layer="bm25+fastembed",
        )
        assert fastembed_mock.embed_texts.await_count == calls_after_rerank, (
            "_post_filter_compress 应复用 _embeddings_rerank 缓存, 不触发额外 FastEmbed 调用"
        )

    async def test_post_filter_compress_cold_cache_triggers_fastembed(
        self,
        context_manager: ContextManager,
        fastembed_mock: MagicMock,
    ) -> None:
        """未先调用 _embeddings_rerank (缓存冷) 时, _post_filter_compress 触发 FastEmbed."""
        docs = ["全新文档内容"]
        span = MagicMock()
        await context_manager._post_filter_compress(
            docs,
            user_id=None,
            session_id=None,
            span=span,
            layer="bm25",
        )
        assert fastembed_mock.embed_texts.await_count == 1, (
            "缓存冷时应触发 FastEmbed 计算 embedding"
        )


# ========== TestFastEmbedBatchInference: batch_size=64 批量推理与缓存交互 ==========


class TestFastEmbedBatchInference:
    """验证 FastEmbedClient batch_size=64 分批并行推理与缓存交互."""

    async def test_large_batch_uses_parallel_64(
        self,
        test_settings: Settings,
        mock_model_env: tuple[MagicMock, MagicMock],
    ) -> None:
        """65 texts (>= 阈值 32) → 分 2 批 (64 + 1), model.embed 调用 2 次."""
        _mock_te_cls, mock_model = mock_model_env
        client = FastEmbedClient(test_settings)
        texts = [f"文本片段-{i}" for i in range(65)]

        await client.embed_texts(texts)

        # batch_size=64: 65 texts → [0:64] + [64:65] → 2 批 → model.embed 调用 2 次
        assert mock_model.embed.call_count == 2, (
            f"65 texts 应分 2 批, 实际 model.embed 调用 {mock_model.embed.call_count} 次"
        )
        assert client._initialized is True

    async def test_small_batch_uses_single_thread_offload(
        self,
        test_settings: Settings,
        mock_model_env: tuple[MagicMock, MagicMock],
    ) -> None:
        """31 texts (< 阈值 32) → 单次 to_thread, model.embed 调用 1 次."""
        _mock_te_cls, mock_model = mock_model_env
        client = FastEmbedClient(test_settings)
        texts = [f"小批文本-{i}" for i in range(31)]

        await client.embed_texts(texts)

        assert mock_model.embed.call_count == 1, (
            f"31 texts 应单次推理, 实际 model.embed 调用 {mock_model.embed.call_count} 次"
        )

    async def test_cache_hit_after_batch_inference(
        self,
        test_settings: Settings,
        mock_model_env: tuple[MagicMock, MagicMock],
    ) -> None:
        """首次批量推理后缓存写入, 第二次相同 texts 全命中缓存 (零推理)."""
        _mock_te_cls, mock_model = mock_model_env
        client = FastEmbedClient(test_settings)
        texts = [f"缓存文本-{i}" for i in range(70)]

        # 第一次: 全部 miss → 批量推理
        await client.embed_texts(texts)
        first_count = mock_model.embed.call_count
        assert first_count > 0

        # 第二次: 相同 texts → 全部命中缓存, 不触发推理
        await client.embed_texts(texts)
        assert mock_model.embed.call_count == first_count, "缓存命中后不应触发额外 model.embed 调用"

    async def test_partial_cache_hit_only_embeds_misses(
        self,
        test_settings: Settings,
        mock_model_env: tuple[MagicMock, MagicMock],
    ) -> None:
        """部分缓存命中: 仅对 miss 文本触发推理, 命中文本直接返回缓存值."""
        _mock_te_cls, mock_model = mock_model_env
        client = FastEmbedClient(test_settings)
        # 先缓存前 3 条 (小批量, 单次推理)
        cached_texts = [f"已缓存-{i}" for i in range(3)]
        await client.embed_texts(cached_texts)
        assert mock_model.embed.call_count == 1

        # 混合 3 已缓存 + 2 新增 → 仅 2 miss 触发推理 (小批量单次)
        mixed_texts = cached_texts + [f"新增-{i}" for i in range(2)]
        await client.embed_texts(mixed_texts)
        assert mock_model.embed.call_count == 2, "仅 miss 文本应触发推理"

    async def test_empty_texts_returns_empty(
        self,
        test_settings: Settings,
        mock_model_env: tuple[MagicMock, MagicMock],
    ) -> None:
        """空文本列表 → 返回空列表, 不加载模型."""
        _mock_te_cls, mock_model = mock_model_env
        client = FastEmbedClient(test_settings)
        result = await client.embed_texts([])
        assert result == []
        assert client._initialized is False
        mock_model.embed.assert_not_called()
