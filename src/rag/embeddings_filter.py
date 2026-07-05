"""EmbeddingsFilter 独立类 (V2-P1, 对标 GPTR context/compression.py).

GPTR 设计:
- RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
- 递归分隔符 ["\n\n", "\n", " ", ""]
- cosine 相似度 + threshold=0.35 (ContextCompressor) / 0.5 (WrittenContentCompressor)
- Top-K 召回 (k=20)
- Fast Path: 文档总字符 < 8000 跳过 embedding 计算

AIR V2 旧版 _embeddings_filter 是 ContextManager 私有方法, 仅按 \\n\\n 一次切分,
无法处理长段落内嵌套结构. V2 提取为独立类, 实现递归分块, 与 GPTR 对齐.

AGENTS.md 第 7 章: Embedding 调用统一走 rag/embeddings.py, 禁止业务代码直连 API.
AGENTS.md 第 10 章: 高频调用启用 head-based 采样 (EmbeddingsClient 内部已包裹 trace_embedding).
"""

from __future__ import annotations

import logging
from typing import Any, cast

from src.config.settings import Settings, get_settings
from src.rag.embeddings import EmbeddingsClient, get_embeddings_client

logger = logging.getLogger(__name__)


# 递归分隔符 (对标 GPTR RecursiveCharacterTextSplitter 默认 separators)
# 优先按段落分, 段落过大时按行分, 再按空格分, 最后按字符分.
_RECURSIVE_SEPARATORS: list[str] = ["\n\n", "\n", " ", ""]


class EmbeddingsFilter:
    """Embeddings 相似度过滤器 (V2-P1, 对标 GPTR EmbeddingsFilter).

    核心流程:
    1. 递归分块 (RecursiveCharacterTextSplitter, chunk_size=1000, chunk_overlap=100)
    2. 批量嵌入 query + chunks
    3. cosine 相似度计算 + threshold 过滤
    4. Top-K 召回

    配置经 Settings 注入 (V2 走 settings, 旧版硬编码):
    - similarity_threshold (默认 0.35)
    - embeddings_filter_chunk_size (默认 1000)
    - embeddings_filter_chunk_overlap (默认 100)
    - embeddings_filter_top_k (默认 20)

    用法:
        filt = EmbeddingsFilter(settings)
        chunks = await filt.filter(query, documents, max_results=10)
    """

    settings: Settings
    _embeddings: EmbeddingsClient

    def __init__(
        self,
        settings: Settings | None = None,
        embeddings: EmbeddingsClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._embeddings = embeddings or get_embeddings_client()

    async def filter(
        self,
        query: str,
        documents: list[dict[str, Any]],
        *,
        max_results: int = 10,
        threshold: float | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[str]:
        """按相似度过滤文档块, 返回 Top-K 内容列表.

        Args:
            query: 查询文本 (用于相似度计算)
            documents: 文档列表, 每个 dict 含 content/url 等字段
            max_results: 最多返回条数
            threshold: 相似度阈值 (None 时用 settings.similarity_threshold)
            user_id: 用户 ID (隔离键)
            session_id: 会话 ID (隔离键)

        Returns:
            过滤后的内容字符串列表, 按相似度降序. 失败时降级返回原文前 N 条.
        """
        if not documents:
            return []

        try:
            # 1. 递归分块 (对标 GPTR RecursiveCharacterTextSplitter)
            chunks = self._split_documents_recursive(documents)

            if not chunks:
                return [str(d.get("content", "")) for d in documents[:max_results]]

            # 2. 批量嵌入 query + chunks
            texts = [query] + [c["content"] for c in chunks]
            vectors = await self._embeddings.embed_texts(
                texts,
                user_id=user_id,
                session_id=session_id,
            )

            if not vectors or len(vectors) < 2:
                # embedding 失败, 降级返回原文
                return [str(d.get("content", "")) for d in documents[:max_results]]

            query_vec = vectors[0]
            chunk_vecs = vectors[1:]

            # 3. cosine 相似度 + threshold 过滤
            sim_threshold = (
                threshold if threshold is not None else self.settings.similarity_threshold
            )
            scored: list[tuple[float, str]] = []
            for i, chunk_vec in enumerate(chunk_vecs):
                score = self._cosine_similarity(query_vec, chunk_vec)
                if score >= sim_threshold:
                    scored.append((score, chunks[i]["content"]))

            # 4. Top-K 召回 (按分数降序)
            scored.sort(key=lambda x: x[0], reverse=True)
            top_k = self.settings.embeddings_filter_top_k
            return [content for _, content in scored[: max(max_results, top_k)]]
        except Exception as e:  # noqa: BLE001
            logger.warning("EmbeddingsFilter 失败, 降级返回原文: %s", e)
            return [str(d.get("content", "")) for d in documents[:max_results]]

    def _split_documents_recursive(
        self,
        documents: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """递归分块 (对标 GPTR RecursiveCharacterTextSplitter).

        与旧版 _split_documents 的区别:
        - 旧版: 仅按 \\n\\n 一次切分, 段落超过 chunk_size 时按滑窗硬切
        - V2: 递归尝试 separators ["\\n\\n", "\\n", " ", ""], 段落过大时按下一级分隔符切分,
          保证语义完整性 (不会硬切句子)

        Args:
            documents: 文档列表

        Returns:
            分块列表, 每个含 content + source 字段
        """
        chunk_size = self.settings.embeddings_filter_chunk_size
        chunk_overlap = self.settings.embeddings_filter_chunk_overlap
        chunks: list[dict[str, str]] = []

        for doc in documents:
            content = str(doc.get("content", ""))
            if not content:
                continue
            source = doc.get("url", "")
            # 递归切分
            split_texts = self._recursive_split(
                content,
                separators=_RECURSIVE_SEPARATORS,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            for text in split_texts:
                if text.strip():
                    chunks.append({"content": text, "source": source})
        return chunks

    @staticmethod
    def _recursive_split(
        text: str,
        *,
        separators: list[str],
        chunk_size: int,
        chunk_overlap: int,
    ) -> list[str]:
        """递归切分文本 (对标 langchain RecursiveCharacterTextSplitter._split_text).

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
                return EmbeddingsFilter._merge_parts(
                    parts,
                    sep=sep,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    next_separators=separators[sep_idx + 1 :],
                )

        # 所有 separator 都无法切分 (单段超长无分隔符), 字符级硬切
        return EmbeddingsFilter._char_level_split(
            text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    @staticmethod
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

        V2-P1 修复: overlap 仅保留上一片段尾部 chunk_overlap 字符 (而非整个 part),
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
                sub_chunks = EmbeddingsFilter._recursive_split(
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
                sub_chunks = EmbeddingsFilter._char_level_split(
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

    @staticmethod
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

    @staticmethod
    def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
        """余弦相似度 (与 ContextManager._cosine_similarity 对齐)."""
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
        norm_a = sum(a * a for a in vec_a) ** 0.5
        norm_b = sum(b * b for b in vec_b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return cast(float, dot / (norm_a * norm_b))
