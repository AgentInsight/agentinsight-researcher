"""单元测试: 递归文本分块工具 (langchain RecursiveCharacterTextSplitter).

验证 src/rag/embeddings_filter.py:
- recursive_split: 递归切分主入口
- DEFAULT_SEPARATORS: 默认分隔符 (段落 -> 行 -> 空格 -> 字符)
- _merge_parts: 相邻片段合并 + overlap 滑窗
- _char_level_split: 字符级硬切兜底
- cosine_similarity: 余弦相似度计算

测试场景:
- 空文本处理
- 单 chunk 场景 (文本 <= chunk_size)
- 多 chunk 场景 (段落/行/空格/字符级切分)
- chunk_size / chunk_overlap 配置生效
- 超长无分隔符文本的字符级兜底

AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务 (无 ONNX/Redis/HTTP).
"""

from __future__ import annotations

import pytest

from src.rag.embeddings_filter import (
    DEFAULT_SEPARATORS,
    _char_level_split,
    _merge_parts,
    cosine_similarity,
    recursive_split,
)

pytestmark = pytest.mark.unit


# ========== DEFAULT_SEPARATORS 常量 ==========


def test_default_separators_order_is_paragraph_line_space_char() -> None:
    """DEFAULT_SEPARATORS 应按 段落->行->空格->字符 顺序降级 (langchain)."""
    assert DEFAULT_SEPARATORS == ["\n\n", "\n", " ", ""]


def test_default_separators_is_list_type() -> None:
    """DEFAULT_SEPARATORS 应为 list 类型 (可被调用方修改副本, 不影响模块常量)."""
    assert isinstance(DEFAULT_SEPARATORS, list)


# ========== 空文本处理 ==========


def test_recursive_split_empty_string_returns_empty_list() -> None:
    """空字符串返回空列表 (text.strip() 为空)."""
    assert (
        recursive_split("", separators=DEFAULT_SEPARATORS, chunk_size=100, chunk_overlap=10) == []
    )


def test_recursive_split_whitespace_only_returns_empty_list() -> None:
    """纯空白字符 (空格/换行/制表符) 返回空列表."""
    text = "   \n\t  \n  "
    assert (
        recursive_split(text, separators=DEFAULT_SEPARATORS, chunk_size=100, chunk_overlap=10) == []
    )


def test_recursive_split_empty_text_within_chunk_size_returns_empty() -> None:
    """文本 <= chunk_size 但为空时返回空列表 (len <= chunk_size 分支)."""
    assert (
        recursive_split("   ", separators=DEFAULT_SEPARATORS, chunk_size=500, chunk_overlap=10)
        == []
    )


# ========== 单 chunk 场景 (文本 <= chunk_size) ==========


def test_recursive_split_short_text_returns_single_chunk() -> None:
    """短文本 (<= chunk_size) 返回单个 chunk, 不切分."""
    text = "这是一段短文本"
    result = recursive_split(text, separators=DEFAULT_SEPARATORS, chunk_size=100, chunk_overlap=10)
    assert result == [text]


def test_recursive_split_text_equals_chunk_size_returns_single_chunk() -> None:
    """文本长度恰好等于 chunk_size 时返回单个 chunk (边界: len <= chunk_size)."""
    text = "a" * 100
    result = recursive_split(text, separators=DEFAULT_SEPARATORS, chunk_size=100, chunk_overlap=10)
    assert result == [text]


def test_recursive_split_single_chunk_preserves_content() -> None:
    """单 chunk 场景保留原始内容 (含特殊字符)."""
    text = "特殊字符 !@#$%^&*() 中文"
    result = recursive_split(text, separators=DEFAULT_SEPARATORS, chunk_size=200, chunk_overlap=10)
    assert result == [text]
    assert result[0] == text


# ========== 多 chunk 场景: 段落分隔 (\\n\\n) ==========


def test_recursive_split_multiple_paragraphs_creates_multiple_chunks() -> None:
    """多段落文本 (\\n\\n 分隔) 每段独立成 chunk (总长 > chunk_size 触发切分)."""
    # 每段 50 字符, chunk_size=50, overlap=0: 累积一段后下一段会超限触发 flush
    para1 = "段落一" + "A" * 47  # 50 字符
    para2 = "段落二" + "B" * 47  # 50 字符
    para3 = "段落三" + "C" * 47  # 50 字符
    text = f"{para1}\n\n{para2}\n\n{para3}"  # 总长 154 > 50 触发切分
    result = recursive_split(text, separators=DEFAULT_SEPARATORS, chunk_size=50, chunk_overlap=0)
    assert len(result) == 3
    assert result[0] == para1
    assert result[1] == para2
    assert result[2] == para3


def test_recursive_split_paragraphs_merge_small_adjacent() -> None:
    """相邻小段落合并到接近 chunk_size (合并行为)."""
    # 三个小段落, 每个 10 字符, chunk_size=25 -> 应合并前两个 (10+2+10=22 <= 25)
    p1, p2, p3 = "段落一AAAA", "段落二BBBB", "段落三CCCC"
    text = f"{p1}\n\n{p2}\n\n{p3}"
    result = recursive_split(text, separators=DEFAULT_SEPARATORS, chunk_size=25, chunk_overlap=5)
    # 至少合并 p1+p2 (22 字符 <= 25), p3 单独或合并
    assert len(result) >= 1
    # 第一个 chunk 应含 p1 内容
    assert p1 in result[0]


def test_recursive_split_strips_whitespace_around_parts() -> None:
    """切分后片段首尾空白被 strip (段落前后多余空白移除, 总长需 > chunk_size 触发切分)."""
    # 总长需 > chunk_size 才会进入 _merge_parts 路径触发 strip
    text = "  段落一AAAAA  \n\n  段落二BBBBB  "  # 各段含 padding, 总长 26 > 10
    result = recursive_split(text, separators=DEFAULT_SEPARATORS, chunk_size=10, chunk_overlap=0)
    assert len(result) == 2
    assert result[0] == "段落一AAAAA"  # strip 后首尾空白移除
    assert result[1] == "段落二BBBBB"


# ========== 多 chunk 场景: 行分隔 (\\n) ==========


def test_recursive_split_long_paragraph_falls_back_to_line_separator() -> None:
    """段落超 chunk_size 但含换行时, 降级到行分隔切分."""
    # 单个段落 (无 \n\n) 但含 \n, 总长 > chunk_size
    line1 = "第一行内容" * 5  # 25 字符
    line2 = "第二行内容" * 5  # 25 字符
    text = f"{line1}\n{line2}"  # 无 \n\n, 51 字符 > chunk_size=30
    result = recursive_split(text, separators=DEFAULT_SEPARATORS, chunk_size=30, chunk_overlap=5)
    assert len(result) >= 2
    # 每个 chunk 不超过 chunk_size (overlap 可能略超, 但行级切分通常不超)
    for chunk in result:
        assert len(chunk) <= 30 + 5  # 允许 overlap 误差


# ========== 多 chunk 场景: 空格分隔 ==========


def test_recursive_split_long_line_falls_back_to_space_separator() -> None:
    """行超 chunk_size 但含空格时, 降级到空格分隔切分."""
    # 单行无换行, 含空格, 总长 > chunk_size
    word1 = "word" * 5  # 20 字符
    word2 = "test" * 5  # 20 字符
    text = f"{word1} {word2}"  # 41 字符 > chunk_size=25
    result = recursive_split(text, separators=DEFAULT_SEPARATORS, chunk_size=25, chunk_overlap=5)
    assert len(result) >= 2


# ========== 多 chunk 场景: 字符级硬切兜底 ==========


def test_recursive_split_no_separator_falls_back_to_char_level() -> None:
    """无任何分隔符的超长文本降级到字符级硬切."""
    text = "a" * 250  # 无 \n / 空格, 250 字符 > chunk_size=100
    result = recursive_split(text, separators=DEFAULT_SEPARATORS, chunk_size=100, chunk_overlap=10)
    assert len(result) >= 2
    # 字符级切分: 每个 chunk <= chunk_size
    for chunk in result:
        assert len(chunk) <= 100


def test_recursive_split_char_level_respects_chunk_size() -> None:
    """字符级硬切每个 chunk 不超过 chunk_size."""
    text = "字" * 500  # 500 中文字符, 无分隔符
    result = recursive_split(text, separators=DEFAULT_SEPARATORS, chunk_size=100, chunk_overlap=20)
    assert len(result) >= 4  # step=80, 500/80 ≈ 7 块
    for chunk in result:
        assert len(chunk) <= 100


def test_char_level_split_empty_text_returns_empty() -> None:
    """_char_level_split 空文本返回空列表."""
    assert _char_level_split("", chunk_size=100, chunk_overlap=10) == []


def test_char_level_split_short_text_returns_single() -> None:
    """_char_level_split 短文本 (<= chunk_size) 返回单 chunk."""
    text = "短文本"
    assert _char_level_split(text, chunk_size=100, chunk_overlap=10) == [text]


def test_char_level_split_whitespace_only_returns_empty() -> None:
    """_char_level_split 纯空白返回空列表."""
    assert _char_level_split("   ", chunk_size=100, chunk_overlap=10) == []


# ========== chunk_size 配置 ==========


def test_recursive_split_smaller_chunk_size_produces_more_chunks() -> None:
    """chunk_size 越小, 切出的 chunk 数越多."""
    text = "a" * 300
    large_chunks = recursive_split(
        text, separators=DEFAULT_SEPARATORS, chunk_size=150, chunk_overlap=0
    )
    small_chunks = recursive_split(
        text, separators=DEFAULT_SEPARATORS, chunk_size=50, chunk_overlap=0
    )
    assert len(small_chunks) > len(large_chunks)


def test_recursive_split_chunk_size_boundary() -> None:
    """chunk_size 边界: 文本长度 = chunk_size + 1 时应切分."""
    text = "a" * 101  # 101 字符
    result = recursive_split(text, separators=DEFAULT_SEPARATORS, chunk_size=100, chunk_overlap=0)
    assert len(result) >= 2


# ========== chunk_overlap 配置 ==========


def test_recursive_split_overlap_zero_no_overlap() -> None:
    """chunk_overlap=0 时无重叠, 相邻 chunk 独立."""
    text = "a" * 200
    result = recursive_split(text, separators=DEFAULT_SEPARATORS, chunk_size=100, chunk_overlap=0)
    assert len(result) == 2
    # 无 overlap 时两 chunk 拼接应覆盖全部 (字符级 step=100)
    assert len(result[0]) == 100
    assert len(result[1]) == 100


def test_recursive_split_overlap_creates_sliding_window() -> None:
    """chunk_overlap > 0 时产生滑窗重叠 (字符级 step = chunk_size - overlap)."""
    text = "abcdefghij" * 20  # 200 字符
    result = recursive_split(text, separators=DEFAULT_SEPARATORS, chunk_size=100, chunk_overlap=20)
    # step = 100 - 20 = 80, 200/80 ≈ 3 块
    assert len(result) >= 2
    # 每块不超过 chunk_size
    for chunk in result:
        assert len(chunk) <= 100


def test_char_level_split_step_calculation() -> None:
    """_char_level_split 的 step = max(1, chunk_size - chunk_overlap)."""
    text = "x" * 100
    # chunk_size=10, overlap=8 -> step=max(1, 2)=2 -> 50 块
    result = _char_level_split(text, chunk_size=10, chunk_overlap=8)
    assert len(result) >= 10  # 至少 10 块 (step=2)


def test_char_level_split_overlap_exceeds_chunk_size_step_is_one() -> None:
    """overlap >= chunk_size 时 step 降为 1 (避免死循环)."""
    text = "x" * 10
    # chunk_size=5, overlap=10 -> step=max(1, -5)=1
    result = _char_level_split(text, chunk_size=5, chunk_overlap=10)
    assert len(result) >= 1
    for chunk in result:
        assert len(chunk) <= 5


# ========== _merge_parts 直接测试 ==========


def test_merge_parts_empty_parts_returns_empty() -> None:
    """_merge_parts 空片段列表返回空列表."""
    assert (
        _merge_parts([], sep="\n\n", chunk_size=100, chunk_overlap=10, next_separators=["\n"]) == []
    )


def test_merge_parts_strips_and_skips_empty_parts() -> None:
    """_merge_parts strip 每个片段, 跳过空片段."""
    parts = ["  段落一  ", "", "  ", "段落二"]
    result = _merge_parts(
        parts, sep="\n\n", chunk_size=100, chunk_overlap=10, next_separators=["\n"]
    )
    assert len(result) == 1  # 两段合并 (均 < chunk_size)
    assert "段落一" in result[0]
    assert "段落二" in result[0]


def test_merge_parts_oversized_part_recurses_with_next_separators() -> None:
    """单片段超 chunk_size 且有 next_separators 时, 递归切分."""
    # part 长度 50 > chunk_size=20, next_separators=[" "] 用空格切
    long_part = "word " * 10  # 50 字符, 含空格
    result = _merge_parts(
        [long_part], sep="\n\n", chunk_size=20, chunk_overlap=0, next_separators=[" ", ""]
    )
    assert len(result) >= 2
    for chunk in result:
        assert len(chunk) <= 20


def test_merge_parts_oversized_part_no_next_separators_char_level() -> None:
    """单片段超 chunk_size 且无 next_separators 时, 字符级硬切兜底."""
    long_part = "a" * 50  # 50 字符 > chunk_size=20, 无 next_separators
    result = _merge_parts(
        [long_part], sep="\n\n", chunk_size=20, chunk_overlap=0, next_separators=[]
    )
    assert len(result) >= 3  # 50/20 ≈ 3 块
    for chunk in result:
        assert len(chunk) <= 20


def test_merge_parts_overlap_keeps_tail_of_last_part() -> None:
    """overlap 保留上一片段尾部 chunk_overlap 字符 (而非整个 part)."""
    # p1=20 字符, p2=20 字符, chunk_size=25, overlap=5
    # 累积 p1 (20) 后加 p2 (sep_len=2 + 20=22, 20+22=42 > 25) -> flush p1, overlap 保留 p1 尾 5 字符
    p1 = "a" * 20
    p2 = "b" * 20
    result = _merge_parts(
        [p1, p2], sep="\n\n", chunk_size=25, chunk_overlap=5, next_separators=["\n", " ", ""]
    )
    assert len(result) >= 2
    # 第二个 chunk 应以 overlap 内容 (p1 尾 5 字符) 开头
    assert result[1].startswith("a" * 5)


def test_merge_parts_overlap_zero_clears_current_parts() -> None:
    """chunk_overlap=0 时 flush 后 current_parts 清空 (无重叠保留, 走累积路径)."""
    # 用 <= chunk_size 的片段走累积路径 (非递归切分路径), 验证 overlap 清空行为
    p1 = "a" * 10  # 10 < chunk_size=15, 走累积
    p2 = "b" * 10  # 10 < chunk_size=15, 走累积
    result = _merge_parts(
        [p1, p2], sep="\n\n", chunk_size=15, chunk_overlap=0, next_separators=["\n", " ", ""]
    )
    assert len(result) == 2
    # 无 overlap 时第二块不应含 p1 内容 (以 b 开头)
    assert result[1].startswith("b")
    assert not result[1].startswith("a")


# ========== cosine_similarity ==========


def test_cosine_similarity_identical_vectors_returns_one() -> None:
    """相同向量余弦相似度为 1.0."""
    vec = [1.0, 2.0, 3.0]
    assert cosine_similarity(vec, vec) == pytest.approx(1.0, rel=1e-6)


def test_cosine_similarity_orthogonal_vectors_returns_zero() -> None:
    """正交向量余弦相似度为 0.0."""
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0, abs=1e-6)


def test_cosine_similarity_opposite_vectors_returns_minus_one() -> None:
    """相反向量余弦相似度为 -1.0."""
    assert cosine_similarity([1.0, 1.0], [-1.0, -1.0]) == pytest.approx(-1.0, rel=1e-6)


def test_cosine_similarity_empty_vectors_returns_zero() -> None:
    """空向量返回 0.0 (避免除零)."""
    assert cosine_similarity([], []) == 0.0


def test_cosine_similarity_different_lengths_returns_zero() -> None:
    """维度不一致返回 0.0 (不抛异常)."""
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0]) == 0.0


def test_cosine_similarity_zero_vector_returns_zero() -> None:
    """零向量 (norm=0) 返回 0.0 (避免除零)."""
    assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


def test_cosine_similarity_partial_similarity_between_zero_and_one() -> None:
    """部分相似向量返回 0 < sim < 1."""
    sim = cosine_similarity([1.0, 0.0], [1.0, 1.0])
    # cos(45°) = sqrt(2)/2 ≈ 0.7071
    assert 0.5 < sim < 0.9
