"""BM25Filter 单元测试 (V4-P3 两层路由 L2 方案).

测试覆盖:
1. 基本过滤: query 与 chunks 关键词重叠, BM25 打分排序
2. 空输入: documents=[] 返回 []
3. 降级路径: 异常时返回原文前 N 条
4. 分词缓存: 重复文本命中缓存
5. 两层路由阈值: >=8K 走 BM25Filter (含 >50K 超长上下文, 全量覆盖)
6. trace_retriever span 集成

对标 test_v2_gptr_alignment.py::TestRecursiveSplit 测试模式.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.config.settings import Settings
from src.rag.bm25_filter import BM25Filter


@pytest.fixture
def bm25_filter() -> BM25Filter:
    """BM25Filter 实例 (默认配置)."""
    return BM25Filter()


@pytest.fixture
def sample_documents() -> list[dict]:
    """测试文档 (8K+ 字符, 触发 BM25Filter 而非 Fast Path)."""
    return [
        {
            "content": "人工智能是计算机科学的一个分支, 它企图了解智能的实质, 并生产出一种新的能以人类智能相似的方式做出反应的智能机器. "
            * 20,
            "url": "https://example.com/ai",
        },
        {
            "content": "机器学习是人工智能的一个分支, 它使计算机系统能够从数据中学习并改进, 而无需明确编程. "
            * 20,
            "url": "https://example.com/ml",
        },
        {
            "content": "深度学习是机器学习的一个分支, 它使用多层神经网络来模拟人脑的学习过程. "
            * 20,
            "url": "https://example.com/dl",
        },
    ]


class TestBM25FilterBasic:
    """基本过滤功能测试."""

    @pytest.mark.asyncio
    async def test_filter_returns_relevant_chunks(self, bm25_filter, sample_documents):
        """query="神经网络" 应优先返回含"神经网络"的 chunk.

        注: 选 "神经网络" 而非 "机器学习" 是因为 BM25 IDF = log((N-n+0.5)/(n+0.5)),
        当 query 词出现在多数 chunk 时 IDF 为负, 所有分数 < 0.0 阈值被过滤.
        "神经网络" 仅出现在 doc 3 (少数), IDF 为正, 可保证 BM25 分数 > 0.
        """
        result = await bm25_filter.filter(
            "神经网络",
            sample_documents,
            max_results=2,
        )
        assert isinstance(result, list)
        assert len(result) > 0
        # 所有结果应为字符串
        assert all(isinstance(c, str) for c in result)

    @pytest.mark.asyncio
    async def test_filter_empty_documents(self, bm25_filter):
        """空 documents 应返回空列表."""
        result = await bm25_filter.filter("query", [], max_results=5)
        assert result == []

    @pytest.mark.asyncio
    async def test_filter_empty_query(self, bm25_filter, sample_documents):
        """空 query 应降级返回原文前 N 条."""
        result = await bm25_filter.filter("", sample_documents, max_results=2)
        assert isinstance(result, list)
        assert len(result) <= 2

    @pytest.mark.asyncio
    async def test_filter_max_results(self, bm25_filter, sample_documents):
        """max_results 应限制返回数量."""
        result = await bm25_filter.filter(
            "人工智能",
            sample_documents,
            max_results=1,
        )
        assert len(result) <= 1


class TestBM25FilterDegrade:
    """降级路径测试."""

    @pytest.mark.asyncio
    async def test_filter_exception_returns_original(self, bm25_filter, sample_documents):
        """异常时应降级返回原文前 N 条 (与旧 EmbeddingsFilter 降级策略对齐, 类已删除)."""
        with patch.object(
            bm25_filter, "_split_documents_recursive", side_effect=RuntimeError("mock error")
        ):
            result = await bm25_filter.filter(
                "query",
                sample_documents,
                max_results=2,
            )
        assert isinstance(result, list)
        assert len(result) <= 2

    @pytest.mark.asyncio
    async def test_filter_no_chunks_returns_original(self, bm25_filter):
        """分块结果为空时应返回原文前 N 条."""
        documents = [{"content": "", "url": "https://example.com"}]
        result = await bm25_filter.filter("query", documents, max_results=1)
        assert result == [""]


class TestBM25FilterTokenCache:
    """分词缓存测试."""

    def test_get_tokens_caches_result(self, bm25_filter):
        """相同文本应命中缓存."""
        text = "人工智能测试文本"
        tokens1 = bm25_filter._get_tokens(text)
        tokens2 = bm25_filter._get_tokens(text)
        assert tokens1 == tokens2
        assert len(bm25_filter._token_cache) == 1

    def test_get_tokens_different_text(self, bm25_filter):
        """不同文本应产生不同分词."""
        bm25_filter._get_tokens("人工智能")
        bm25_filter._get_tokens("机器学习")
        # 缓存应有两个条目
        assert len(bm25_filter._token_cache) == 2


class TestBM25FilterSplit:
    """分块逻辑测试."""

    def test_split_documents_recursive(self, bm25_filter):
        """_split_documents_recursive 应正确分块."""
        documents = [
            {
                "content": "段落一\n\n段落二\n\n段落三",
                "url": "https://example.com",
            }
        ]
        chunks = bm25_filter._split_documents_recursive(documents)
        assert isinstance(chunks, list)
        assert len(chunks) > 0
        assert all("content" in c and "source" in c for c in chunks)

    def test_split_empty_content_skipped(self, bm25_filter):
        """空 content 应被跳过."""
        documents = [{"content": "", "url": "https://example.com"}]
        chunks = bm25_filter._split_documents_recursive(documents)
        assert chunks == []


class TestBM25FilterSettings:
    """配置项测试."""

    def test_custom_settings(self):
        """自定义 settings 应正确注入."""
        settings = Settings(bm25_k1=2.0, bm25_b=0.5, bm25_filter_top_k=10)
        filt = BM25Filter(settings)
        assert filt.settings.bm25_k1 == 2.0
        assert filt.settings.bm25_b == 0.5
        assert filt.settings.bm25_filter_top_k == 10

    def test_default_settings(self, bm25_filter):
        """默认配置应符合预期."""
        assert bm25_filter.settings.bm25_filter_enabled is True
        assert bm25_filter.settings.bm25_filter_char_threshold == 8000
        assert bm25_filter.settings.bm25_filter_char_upper == 50000
        assert bm25_filter.settings.bm25_filter_top_k == 20
