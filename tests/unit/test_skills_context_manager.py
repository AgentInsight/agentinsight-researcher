"""单元测试: ContextManager 静态纯函数.

验证 _split_documents / _cosine_similarity / _truncate_by_words,
不依赖 Embeddings / LLM / Qdrant.
AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
"""

from __future__ import annotations

import pytest

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
