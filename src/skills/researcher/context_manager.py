"""ContextManager 上下文管理者.

对标 GPT Researcher skills/context_manager.py + context/compression.py.
AGENTS.md 用户需求 10: Token 优化核心.

核心优化:
1. EmbeddingsFilter: 按 similarity_threshold (默认 0.35) 过滤文档块
2. 小文档快速路径: 低于 COMPRESSION_THRESHOLD (8000 字符) 跳过压缩
3. 跨子主题去重: WrittenContentCompressor 已写章节相似度过滤
4. Word Limit: MAX_CONTEXT_WORDS (25000) 截断
"""

from __future__ import annotations

import logging
from typing import Any, cast

from src.config.settings import Settings, get_settings
from src.observability.tracing import trace_chain
from src.rag.embeddings import EmbeddingsClient

logger = logging.getLogger(__name__)


class ContextManager:
    """上下文管理者 (Token 优化核心).

    对标 GPT Researcher ContextManager + ContextCompressor.
    """

    settings: Settings
    _embeddings: EmbeddingsClient

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._embeddings = EmbeddingsClient(self.settings)

    async def get_similar_content(
        self,
        query: str,
        documents: list[dict[str, Any]],
        *,
        max_results: int = 10,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> str:
        """获取与查询相似的内容 (压缩 + 过滤).

        对标 GPT Researcher ContextCompressor.async_get_context.
        关键优化: 小文档快速路径, 跳过 embedding 计算.
        """
        async with trace_chain(
            name="context-compress",
            input={
                "query": query[:100],
                "doc_count": len(documents),
                "total_chars": sum(len(d.get("content", "")) for d in documents),
            },
            user_id=user_id,
            session_id=session_id,
        ) as span:
            if not documents:
                span.update(output={"context": ""})
                return ""

            total_chars = sum(len(str(d.get("content", ""))) for d in documents)
            chunk_threshold = self.settings.compression_threshold

            # 小文档快速路径 (对标 GPT Researcher 关键优化)
            if total_chars < chunk_threshold and len(documents) <= max_results:
                context = "\n\n".join(str(d.get("content", "")) for d in documents[:max_results])
                span.update(output={"context_len": len(context), "fast_path": True})
                return context

            # 大文档: 用 embedding 相似度过滤
            compressed = await self._embeddings_filter(
                query,
                documents,
                max_results,
                user_id=user_id,
                session_id=session_id,
            )

            # Word Limit 截断 (对标 GPT Researcher MAX_CONTEXT_WORDS)
            context = self._truncate_by_words(compressed, self.settings.max_context_words)

            span.update(output={"context_len": len(context), "fast_path": False})
            return context

    async def _embeddings_filter(
        self,
        query: str,
        documents: list[dict[str, Any]],
        max_results: int,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[str]:
        """EmbeddingsFilter 相似度过滤.

        对标 GPT Researcher context/compression.py 的 EmbeddingsFilter.
        按 similarity_threshold (默认 0.35) 过滤文档块.
        """
        if not documents:
            return []

        try:
            # 分块 (对标 RecursiveCharacterTextSplitter chunk_size=1000)
            chunks = self._split_documents(documents, chunk_size=1000, chunk_overlap=100)

            # 批量嵌入 chunks 与 query
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

            # 计算余弦相似度
            threshold = self.settings.similarity_threshold
            scored: list[tuple[float, str]] = []
            for i, chunk_vec in enumerate(chunk_vecs):
                score = self._cosine_similarity(query_vec, chunk_vec)
                if score >= threshold:
                    scored.append((score, chunks[i]["content"]))

            # 按分数降序取 top max_results
            scored.sort(key=lambda x: x[0], reverse=True)
            return [content for _, content in scored[:max_results]]
        except Exception as e:  # noqa: BLE001
            logger.warning("EmbeddingsFilter 失败, 降级返回原文: %s", e)
            return [str(d.get("content", "")) for d in documents[:max_results]]

    @staticmethod
    def _split_documents(
        documents: list[dict[str, Any]],
        *,
        chunk_size: int = 1000,
        chunk_overlap: int = 100,
    ) -> list[dict[str, str]]:
        """文档分块 (对标 RecursiveCharacterTextSplitter)."""
        chunks: list[dict[str, str]] = []
        for doc in documents:
            content = str(doc.get("content", ""))
            if not content:
                continue
            # 按段落分, 再按 chunk_size 滑窗
            paragraphs = content.split("\n\n")
            for para in paragraphs:
                if len(para) <= chunk_size:
                    chunks.append({"content": para, "source": doc.get("url", "")})
                else:
                    # 滑窗分块
                    step = chunk_size - chunk_overlap
                    for i in range(0, len(para), step):
                        chunk = para[i : i + chunk_size]
                        if chunk:
                            chunks.append({"content": chunk, "source": doc.get("url", "")})
        return chunks

    @staticmethod
    def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
        """余弦相似度."""
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
        norm_a = sum(a * a for a in vec_a) ** 0.5
        norm_b = sum(b * b for b in vec_b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return cast(float, dot / (norm_a * norm_b))

    @staticmethod
    def _truncate_by_words(texts: list[str], max_words: int) -> str:
        """按词数截断 (对标 GPT Researcher MAX_CONTEXT_WORDS)."""
        result: list[str] = []
        word_count = 0
        for text in texts:
            words = text.split()
            if word_count + len(words) > max_words:
                # 截断最后一个
                remaining = max_words - word_count
                if remaining > 0:
                    result.append(" ".join(words[:remaining]))
                break
            result.append(text)
            word_count += len(words)
        return "\n\n".join(result)

    async def get_similar_written_contents(
        self,
        query: str,
        written_sections: list[dict[str, str]],
        *,
        max_results: int = 10,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[str]:
        """跨子主题去重 (对标 WrittenContentCompressor).

        从已写章节里找相似内容, 避免重复.
        """
        if not written_sections:
            return []

        try:
            # 嵌入查询与已写章节
            texts = [query] + [s.get("content", "") for s in written_sections]
            vectors = await self._embeddings.embed_texts(
                texts,
                user_id=user_id,
                session_id=session_id,
            )
            if not vectors or len(vectors) < 2:
                return []

            query_vec = vectors[0]
            section_vecs = vectors[1:]

            # 相似度阈值 0.5 (对标 GPT Researcher WrittenContentCompressor)
            threshold = 0.5
            scored: list[tuple[float, str]] = []
            for i, sec_vec in enumerate(section_vecs):
                score = self._cosine_similarity(query_vec, sec_vec)
                if score >= threshold:
                    scored.append((score, written_sections[i].get("content", "")))

            scored.sort(key=lambda x: x[0], reverse=True)
            return [content for _, content in scored[:max_results]]
        except Exception as e:  # noqa: BLE001
            logger.warning("WrittenContent 去重失败: %s", e)
            return []
