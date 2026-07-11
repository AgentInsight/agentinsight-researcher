"""递归文本分块工具.

保留的 `_recursive_split` / `_merge_parts` / `_char_level_split` 为纯文本切分工具,
被 `BM25Filter` 与 `WrittenContentCompressor` 复用, 保证 chunk 级一致性.

AGENTS.md 第 7 章: Embedding 调用统一走 rag/embeddings.py (私有数据) 或
rag/fastembed_client.py (上下文压缩), 禁止业务代码直连 API.
"""

from __future__ import annotations

import logging
from typing import cast

logger = logging.getLogger(__name__)


# 递归分隔符 (默认 separators)
# 优先按段落分, 段落过大时按行分, 再按空格分, 最后按字符分.
_RECURSIVE_SEPARATORS: list[str] = ["\n\n", "\n", " ", ""]


def recursive_split(
    text: str,
    *,
    separators: list[str],
    chunk_size: int,
    chunk_overlap: int,
) -> list[str]:
    """递归切分文本 (RecursiveCharacterTextSplitter._split_text).

    算法:
    1. 用第一个 separator 切分文本
    2. 对每个片段: 若长度 <= chunk_size, 保留; 否则用下一个 separator 递归切分
    3. 合并相邻小片段直到接近 chunk_size
    4. 应用 chunk_overlap 滑窗

    简化实现 (与 langchain 行为对齐, 不依赖 langchain):
    - 优先用段落分隔, 段落过大时降级到行, 再降级到空格, 最后到字符
    - 滑窗 overlap 保证跨块语义连续
    """
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    # 找到第一个能切分的 separator (切分后片段数 > 1)
    for sep_idx, sep in enumerate(separators):
        if sep == "":
            # 最后一级: 字符级硬切
            break
        parts = text.split(sep) if sep else [text]
        if len(parts) > 1:
            # 用此 separator 切分, 合并相邻片段
            return _merge_parts(
                parts,
                sep=sep,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                next_separators=separators[sep_idx + 1 :],
            )

    # 所有 separator 都无法切分 (单段超长无分隔符), 字符级硬切
    return _char_level_split(
        text,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )


def _merge_parts(
    parts: list[str],
    *,
    sep: str,
    chunk_size: int,
    chunk_overlap: int,
    next_separators: list[str],
) -> list[str]:
    """合并相邻片段到 chunk_size, 超长片段递归切分.

    Args:
        parts: 切分后的片段列表
        sep: 当前级 separator (用于合并时还原)
        chunk_size: 块大小上限
        chunk_overlap: 块重叠
        next_separators: 下一级 separators (递归用)

    overlap 仅保留上一片段尾部 chunk_overlap 字符 (而非整个 part),
    避免累积后超长违反 chunk_size 上限. 同时增加超长 part 无下一级 separator 时
    的字符级兜底.
    """
    chunks: list[str] = []
    current_parts: list[str] = []
    current_len = 0

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # 单片段超长: 递归用下一级 separator 切分
        if len(part) > chunk_size and next_separators:
            # 先 flush 当前累积的片段
            if current_parts:
                chunks.append(sep.join(current_parts))
                current_parts = []
                current_len = 0
            # 递归切分超长片段
            sub_chunks = recursive_split(
                part,
                separators=next_separators,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            chunks.extend(sub_chunks)
            continue

        # 单片段超长且无下一级 separator: 字符级硬切兜底
        if len(part) > chunk_size:
            if current_parts:
                chunks.append(sep.join(current_parts))
                current_parts = []
                current_len = 0
            sub_chunks = _char_level_split(
                part,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            chunks.extend(sub_chunks)
            continue

        # 累积片段直到接近 chunk_size
        sep_len = len(sep) if current_parts else 0
        if current_len + sep_len + len(part) > chunk_size and current_parts:
            chunks.append(sep.join(current_parts))
            # overlap: 保留最后一个片段的尾部字符 (而非整个 part),
            # 避免累积后超长违反 chunk_size 上限
            if chunk_overlap > 0 and current_parts:
                last_part = current_parts[-1]
                if len(last_part) > chunk_overlap:
                    overlap_text = last_part[-chunk_overlap:]
                    current_parts = [overlap_text]
                    current_len = len(overlap_text)
                else:
                    # 上一片段本身短于 overlap, 直接清空 (不保留)
                    current_parts = []
                    current_len = 0
            else:
                current_parts = []
                current_len = 0
        current_parts.append(part)
        current_len += sep_len + len(part)

    if current_parts:
        chunks.append(sep.join(current_parts))

    return [c for c in chunks if c.strip()]


def _char_level_split(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> list[str]:
    """字符级硬切 (最后兜底, 无 separator 可用时)."""
    if len(text) <= chunk_size:
        return [text] if text.strip() else []
    step = max(1, chunk_size - chunk_overlap)
    chunks: list[str] = []
    for i in range(0, len(text), step):
        chunk = text[i : i + chunk_size]
        if chunk.strip():
            chunks.append(chunk)
        if i + chunk_size >= len(text):
            break
    return chunks


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """余弦相似度 (与 ContextManager._cosine_similarity 对齐)."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
    norm_a = sum(a * a for a in vec_a) ** 0.5
    norm_b = sum(b * b for b in vec_b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return cast(float, dot / (norm_a * norm_b))


# 默认递归分隔符 (供 BM25Filter / WrittenContentCompressor 复用)
DEFAULT_SEPARATORS: list[str] = _RECURSIVE_SEPARATORS
