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

import asyncio
import hashlib
import logging
from typing import Any

import numpy as np

from src.config.settings import Settings, get_settings
from src.llm.client import LLMClient, LLMTier, get_llm_client
from src.observability.tracing import trace_chain
from src.rag.embeddings import EmbeddingsClient, get_embeddings_client
from src.rag.fastembed_client import FastEmbedClient, get_fastembed_client

logger = logging.getLogger(__name__)


class ContextManager:
    """上下文管理者 (Token 优化核心).

    对标 GPT Researcher ContextManager + ContextCompressor.
    """

    settings: Settings
    _embeddings: EmbeddingsClient
    _llm: LLMClient
    _compressor: SlidingWindowCompressor
    _written_compressor: WrittenContentCompressor
    _fastembed: FastEmbedClient

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._embeddings = get_embeddings_client()
        self._llm = get_llm_client()
        self._compressor = SlidingWindowCompressor(self.settings)
        self._written_compressor = WrittenContentCompressor(self.settings)
        self._fastembed = get_fastembed_client()

    async def compress_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """滑动窗口 + LLM 摘要压缩消息列表 (P1-01).

        AGENTS.md 第 6 章: 保留最近 25% 消息为原文, 其余 LLM 摘要化.
        供后续节点 (writer/proofreader 等) 在写入会话前调用.

        3.6.1 死代码修复: 本方法实现完整可用, 供 chat_agent.py 的 chat 方法
        (或 multi_agent_builder.py 的 researcher 节点) 在写入会话前调用做长会话压缩.
        典型调用方式:

            from src.skills.researcher.context_manager import ContextManager

            cm = ContextManager(settings)
            # 写入 Checkpointer 前检查阈值 (AGENTS.md 第 6 章 CONTEXT_MAX_CHARS)
            if total_chars > settings.compression_threshold:
                messages = await cm.compress_messages(messages)

        V4-P1-04: 当上下文总字符数超过 compression_threshold 时, 切换为
        滑动窗口+摘要混合压缩 (保留最近 N 条原文, 远期 LLM 摘要), 避免
        纯 LLM 摘要丢失近期细节, 同时降低成本.
        """
        # V4-P1-04: 超阈值触发混合压缩策略
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        if total_chars > self.settings.compression_threshold:
            return await self._hybrid_compress(
                messages,
                self.settings.context_compressed_target,
            )
        return await self._compressor.compress(messages)

    async def _hybrid_compress(
        self,
        messages: list[dict[str, Any]],
        target_tokens: int,
    ) -> list[dict[str, Any]]:
        """滑动窗口+摘要混合压缩 (V4-P1-04).

        策略:
        - 近期内容 (最后 N 条): 保留原文 (滑动窗口), N = settings.context_sliding_window
        - 远期内容: LLM 摘要为一段文本, 不超过 target_tokens 字符
        - 阈值: 由调用方在内容超过 compression_threshold 时触发本方法

        Args:
            messages: 原始消息列表 [{"role": "...", "content": "..."}, ...]
            target_tokens: 摘要目标字符数上限

        Returns:
            [{"role": "system", "content": "[历史摘要] ..."}, *recent_messages]
        """
        n = self.settings.context_sliding_window
        # 消息数不足滑动窗口大小, 直接返回原文
        if len(messages) <= n:
            return messages

        # 1. 分割: 近期 messages[-N:] 保留原文, 远期 messages[:-N] 需要摘要
        recent_messages = messages[-n:]
        old_messages = messages[:-n]

        # 2. 远期内容: 调用 LLM 摘要为一段文本
        old_text = "\n".join(
            f"[{m.get('role', 'user')}] {m.get('content', '')[:1000]}" for m in old_messages
        )
        summary = await self._summarize_old_messages(old_text, target_tokens)

        # 3. 返回: [摘要文本] + 近期原文 messages
        if not summary:
            # 摘要失败时降级: 仅返回近期原文, 避免远期噪声
            return recent_messages
        summary_msg: dict[str, Any] = {
            "role": "system",
            "content": f"[历史摘要] {summary}",
        }
        return [summary_msg] + recent_messages

    async def _summarize_old_messages(
        self,
        text: str,
        target_tokens: int,
    ) -> str:
        """LLM 摘要远期消息文本 (V4-P1-04).

        Args:
            text: 远期消息拼接文本
            target_tokens: 摘要字符数上限

        Returns:
            摘要文本, 失败返回空字符串
        """
        if not text.strip():
            return ""
        # max_tokens 为 token 数, 取合理上限避免超模型限制
        max_tokens = min(max(target_tokens // 2, 500), 2000)
        prompt = f"""请将以下研究上下文压缩为简洁摘要, 保留关键事实与结论, 不超过 {target_tokens} 字:

{text[:8000]}

摘要:"""
        messages = [{"role": "user", "content": prompt}]
        try:
            response = await self._llm.achat(
                messages,
                tier=LLMTier.FAST,
                max_tokens=max_tokens,
                temperature=0.3,
                span_name="context-hybrid-summarize",
                step="context_manager",
            )
            return response.content
        except Exception as e:  # noqa: BLE001
            logger.warning("混合压缩 LLM 摘要失败, 降级返回空摘要: %s", e)
            return ""

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

        P0-3: TEI 熔断器开启时降级关键词匹配, 避免等待 90s timeout.
        V4-P3 L2: 两层路由 (Fast Path <8K | BM25Filter >=8K).
          性能: 258 chunks × TEI 推理 43min → BM25 本地 2s (1000× 加速).
        注: EmbeddingsFilter 已移除 (TEI CPU 部署性能瓶颈), 全量由 BM25Filter 覆盖.
        """
        # 重置已写入内容记录 (P1-02), 每次新查询开始时清空
        self._written_compressor.reset()

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

            # P0-3: TEI 熔断器开启时走关键词匹配降级 (避免 90s timeout 雪崩)
            if self._embeddings.is_circuit_open():
                logger.warning(
                    "TEI 熔断器开启, context-compress 降级关键词匹配 (doc_count=%d)",
                    len(documents),
                )
                context = self._keyword_fallback(query, documents, max_results)
                span.update(
                    output={
                        "context_len": len(context),
                        "fast_path": False,
                        "fallback": "keyword",
                    }
                )
                return context

            # ━━━━━━━━━━━ Layer 1: Fast Path (< 8K 字符, 零计算) ━━━━━━━━━━━
            if (
                total_chars < self.settings.bm25_filter_char_threshold
                and len(documents) <= max_results
            ):
                context = "\n\n".join(
                    str(d.get("content", "")) for d in documents[:max_results]
                )
                span.update(
                    output={"context_len": len(context), "fast_path": True, "layer": "fast"}
                )
                return context

            # ━━━━━━━━━━━ Layer 2: BM25 + Embeddings 两阶段检索 (>= 8K 字符) ━━━━━━━━━━━
            # V4-P3 L2: 两阶段检索
            #   BM25 先召回 Top-50 (粗筛, 快), 再根据 chunk 数决定是否精排:
            #   - 总 chunk 数 <= 30 → 直接返回 BM25 结果 (精排候选太少, 没必要)
            #   - 总 chunk 数 > 30 → Embeddings 从 Top-50 中再选 Top-20 (精排, 准)
            if self.settings.bm25_filter_enabled:
                bm25_results = await self._bm25_filter(
                    query,
                    documents,
                    max_results=self.settings.bm25_filter_top_k_for_rerank,
                    user_id=user_id,
                    session_id=session_id,
                )

                total_chunks = len(bm25_results)
                if total_chunks <= self.settings.embeddings_rerank_chunk_threshold:
                    context = await self._post_filter_compress(
                        bm25_results,
                        user_id=user_id,
                        session_id=session_id,
                        span=span,
                        layer="bm25",
                    )
                    return context

                reranked = await self._embeddings_rerank(
                    query,
                    bm25_results,
                    max_results=self.settings.embeddings_rerank_top_k,
                    user_id=user_id,
                    session_id=session_id,
                )
                context = await self._post_filter_compress(
                    reranked,
                    user_id=user_id,
                    session_id=session_id,
                    span=span,
                    layer="bm25+embeddings",
                )
                return context

            # bm25_filter_enabled=False 时降级关键词匹配 (不调 EmbeddingsFilter)
            context = self._keyword_fallback(query, documents, max_results)
            span.update(
                output={
                    "context_len": len(context),
                    "fast_path": False,
                    "fallback": "keyword",
                }
            )
            return context

    async def _post_filter_compress(
        self,
        compressed: list[str],
        *,
        user_id: str | None,
        session_id: str | None,
        span: Any,
        layer: str,
    ) -> str:
        """后处理: 去重 + Word Limit 截断."""
        # WrittenContentCompressor 跨子主题去重 (P1-02)
        deduped: list[str] = []
        for chunk in compressed:
            if await self._written_compressor.should_keep(chunk):
                deduped.append(chunk)

        # Word Limit 截断 (对标 GPT Researcher MAX_CONTEXT_WORDS)
        context = self._truncate_by_words(deduped, self.settings.max_context_words)
        span.update(
            output={"context_len": len(context), "fast_path": False, "layer": layer}
        )
        return context

    async def _embeddings_rerank(
        self,
        query: str,
        documents: list[str],
        max_results: int,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[str]:
        """Embeddings 精排: 从 BM25 候选中再选 Top-K (两阶段检索第二阶段).

        使用 FastEmbed 本地模型 (bge-small-zh-v1.5, 512维), 不依赖远程 TEI.

        Args:
            query: 查询文本
            documents: BM25 粗筛后的候选内容列表
            max_results: 精排后返回数量
            user_id: 用户 ID (仅用于 trace)
            session_id: 会话 ID (仅用于 trace)

        Returns:
            精排后的内容字符串列表, 按 Embeddings 相似度降序.
            FastEmbed 失败时降级返回原 BM25 结果前 N 条.
        """
        if not documents:
            return []

        try:
            async with trace_retriever(
                name="embeddings-rerank",
                input={"query": query[:100], "candidate_count": len(documents)},
                metadata={
                    "retriever_type": "fastembed",
                    "user_id": user_id,
                    "session_id": session_id,
                },
                user_id=user_id,
                session_id=session_id,
            ) as span:
                query_emb = await self._fastembed.embed_text(query)
                doc_embs = await self._fastembed.embed_texts(documents)

                similarities: list[tuple[float, str]] = []
                for doc, emb in zip(documents, doc_embs, strict=False):
                    sim = self._cosine_similarity(query_emb, emb)
                    similarities.append((sim, doc))

                similarities.sort(key=lambda x: x[0], reverse=True)
                result = [doc for _, doc in similarities[:max_results]]

                span.update(
                    output={"matched": len(result)},
                    metadata={
                        "candidate_count": len(documents),
                        "retriever_type": "fastembed",
                        "top_score": float(similarities[0][0]) if similarities else 0.0,
                    },
                )
                return result
        except Exception as e:  # noqa: BLE001
            logger.warning("FastEmbed 精排失败, 降级返回 BM25 结果: %s", e)
            return documents[:max_results]

    @staticmethod
    def _cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
        """计算两个向量的余弦相似度."""
        if not vec1 or not vec2:
            return 0.0
        dot = sum(a * b for a, b in zip(vec1, vec2, strict=False))
        norm1 = (sum(x * x for x in vec1)) ** 0.5
        norm2 = (sum(x * x for x in vec2)) ** 0.5
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    async def _bm25_filter(
        self,
        query: str,
        documents: list[dict[str, Any]],
        max_results: int,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[str]:
        """BM25Filter 关键词过滤 (V4-P3 L2, 替代 EmbeddingsFilter 中段路由).

        本地 jieba+BM25Okapi, 零网络调用, 10-200ms 响应.
        超时或异常降级关键词匹配 (与 _embeddings_filter 降级策略对齐).
        """
        if not documents:
            return []

        from src.rag.bm25_filter import BM25Filter

        try:
            filt = BM25Filter(self.settings)
            return await asyncio.wait_for(
                filt.filter(
                    query,
                    documents,
                    max_results=max_results,
                    user_id=user_id,
                    session_id=session_id,
                ),
                timeout=self.settings.bm25_filter_timeout,
            )
        except TimeoutError:
            logger.warning(
                "BM25Filter %.1fs 超时, 降级关键词匹配 (doc_count=%d)",
                self.settings.bm25_filter_timeout,
                len(documents),
            )
            return self._keyword_fallback_split(query, documents, max_results)
        except Exception as e:  # noqa: BLE001
            logger.warning("BM25Filter 失败, 降级关键词匹配: %s", e)
            return self._keyword_fallback_split(query, documents, max_results)

    @staticmethod
    def _keyword_fallback(
        query: str,
        documents: list[dict[str, Any]],
        max_results: int,
    ) -> str:
        """P0-3: TEI 故障降级路径 — 关键词匹配 + 字符长度排序.

        无 embedding 依赖, 用 jieba 分词计算 query 与各 document 的关键词重叠度,
        按重叠度 + 字符长度排序取 Top-K. 精度低于 embedding 相似度, 但远优于
        无限等待 TEI timeout (P95 450s → <2s).

        Args:
            query: 用户查询
            documents: 文档列表 [{"content": "...", ...}, ...]
            max_results: 返回文档数上限

        Returns:
            拼接后的上下文字符串 (\\n\\n 分隔).
        """
        try:
            import jieba

            query_keywords = set(jieba.cut(query))
            # 去除单字符停用词 (粗略)
            query_keywords = {w for w in query_keywords if len(w.strip()) >= 2}
        except ImportError:
            # jieba 未安装时用空格分词降级
            query_keywords = {w for w in query.split() if len(w) >= 2}

        scored: list[tuple[int, int, str]] = []
        for doc in documents:
            content = str(doc.get("content", ""))
            try:
                import jieba

                doc_keywords = set(jieba.cut(content[:2000]))
                doc_keywords = {w for w in doc_keywords if len(w.strip()) >= 2}
            except ImportError:
                doc_keywords = {w for w in content[:2000].split() if len(w) >= 2}
            overlap = len(query_keywords & doc_keywords)
            scored.append((overlap, len(content), content))

        # 排序: 关键词重叠度优先, 同分按字符长度优先 (内容更丰富的优先)
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return "\n\n".join(c for _, _, c in scored[:max_results])

    async def _embeddings_filter(
        self,
        query: str,
        documents: list[dict[str, Any]],
        max_results: int,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[str]:
        """EmbeddingsFilter 相似度过滤 (V4-P3 暂停使用, 保留代码供未来重新启用).

        V2-P1 优化 (对标 GPTR context/compression.py EmbeddingsFilter):
        - 旧版: 私有方法 + _split_documents 仅按 \\n\\n 一次切分 (硬切)
        - V2: 调用独立 EmbeddingsFilter 类, 递归分块 (\\n\\n → \\n → 空格 → 字符),
          保证语义完整性, 与 GPTR RecursiveCharacterTextSplitter 对齐.

        P0-3: 加 15s asyncio.wait_for timeout, 超时降级关键词匹配.
              TEI 故障时 EmbeddingsFilter 内部 embed_texts 会触发熔断器 fast-fail
              (EmbeddingsCircuitOpenError), 此处一并降级.

        按 similarity_threshold (默认 0.35) 过滤文档块.

        V4-P3: 暂停使用 (TEI CPU 部署性能瓶颈), 主路由改为 BM25Filter.
               保留代码供未来 TEI GPU 部署或外部 Embeddings 服务时重新启用.
        """
        if not documents:
            return []

        # V2-P1: 委托给独立 EmbeddingsFilter 类 (递归分块 + 相似度过滤)
        from src.rag.embeddings_filter import EmbeddingsFilter

        try:
            filt = EmbeddingsFilter(self.settings, self._embeddings)
            # P0-3: 15s timeout, 超时降级 (避免 TEI 卡 90s timeout 拖累 context-compress)
            return await asyncio.wait_for(
                filt.filter(
                    query,
                    documents,
                    max_results=max_results,
                    user_id=user_id,
                    session_id=session_id,
                ),
                timeout=15.0,
            )
        except TimeoutError:
            logger.warning(
                "EmbeddingsFilter 15s 超时, 降级关键词匹配 (doc_count=%d)", len(documents)
            )
            return self._keyword_fallback_split(query, documents, max_results)
        except Exception as e:  # noqa: BLE001
            # P0-3: TEI 熔断或 EmbeddingsFilter 异常时降级
            logger.warning("EmbeddingsFilter 失败, 降级关键词匹配: %s", e)
            return self._keyword_fallback_split(query, documents, max_results)

    @staticmethod
    def _keyword_fallback_split(
        query: str,
        documents: list[dict[str, Any]],
        max_results: int,
    ) -> list[str]:
        """P0-3: BM25Filter 失败降级 — 关键词匹配返回 list[str] (兼容原签名).

        与 _keyword_fallback 类似但返回 list[str] (每个元素为单个文档内容),
        供 BM25Filter 超时/异常降级时按 list 处理.
        """
        try:
            import jieba

            query_keywords = {w for w in jieba.cut(query) if len(w.strip()) >= 2}
        except ImportError:
            query_keywords = {w for w in query.split() if len(w) >= 2}

        scored: list[tuple[int, int, str]] = []
        for doc in documents:
            content = str(doc.get("content", ""))
            try:
                import jieba

                doc_keywords = {w for w in jieba.cut(content[:2000]) if len(w.strip()) >= 2}
            except ImportError:
                doc_keywords = {w for w in content[:2000].split() if len(w) >= 2}
            overlap = len(query_keywords & doc_keywords)
            scored.append((overlap, len(content), content))

        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [c for _, _, c in scored[:max_results]]

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
    def _cosine_similarity_batch(
        new_vecs: list[list[float]],
        written_vecs: list[list[float]],
    ) -> np.ndarray:
        """批量余弦相似度 (numpy 矩阵加速, P4 修复 context-compress 性能瓶颈).

        一次性计算 M 条新向量与 N 条已写入向量之间的两两余弦相似度,
        替代旧版 WrittenContentCompressor.should_keep 内的 O(N*M) 双重 for 循环.

        Args:
            new_vecs: 新向量列表 (M 条, 每条 D 维)
            written_vecs: 已写入向量列表 (N 条, 每条 D 维)

        Returns:
            shape=(M, N) 的 numpy 数组, 每元素为对应位置的余弦相似度.
            若任一输入为空或维度不匹配, 返回 shape=(0, 0) 的空数组.
        """
        if not new_vecs or not written_vecs:
            return np.zeros((0, 0), dtype=np.float32)
        new_matrix = np.asarray(new_vecs, dtype=np.float32)
        written_matrix = np.asarray(written_vecs, dtype=np.float32)
        if new_matrix.ndim != 2 or written_matrix.ndim != 2:
            return np.zeros((0, 0), dtype=np.float32)
        # L2 归一化 (避免除零, 零向量范数置 1, 结果为 0)
        new_norms = np.linalg.norm(new_matrix, axis=1, keepdims=True)
        written_norms = np.linalg.norm(written_matrix, axis=1, keepdims=True)
        new_norms = np.where(new_norms == 0, 1.0, new_norms)
        written_norms = np.where(written_norms == 0, 1.0, written_norms)
        new_normalized = new_matrix / new_norms
        written_normalized = written_matrix / written_norms
        # 矩阵乘法: (M, D) @ (D, N) = (M, N)
        return new_normalized @ written_normalized.T

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


class SlidingWindowCompressor:
    """滑动窗口 + LLM 摘要压缩器 (AGENTS.md 第 6 章 P1-01).

    策略: 保留最近 25% 消息为原文, 其余 LLM 摘要化.
    对标 GPT Researcher 上下文压缩, 但增强 LLM 摘要能力.
    """

    settings: Settings
    _llm: LLMClient
    recent_ratio: float
    max_summary_tokens: int

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        recent_ratio: float = 0.25,
        max_summary_tokens: int = 2000,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or get_llm_client()
        self.recent_ratio = recent_ratio
        self.max_summary_tokens = max_summary_tokens

    async def compress(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """压缩消息列表.

        Args:
            messages: [{"role": "...", "content": "..."}, ...]

        Returns:
            [{"role": "system", "content": "[历史摘要] ..."}, *recent_messages]
        """
        if len(messages) <= 4:
            return messages

        # 1. 分割: 最近 25% 保留原文, 其余摘要化
        recent_count = max(1, int(len(messages) * self.recent_ratio))
        old_messages = messages[:-recent_count]
        recent_messages = messages[-recent_count:]

        # 2. LLM 摘要旧消息
        old_text = "\n".join(
            f"[{m.get('role', 'user')}] {m.get('content', '')[:1000]}" for m in old_messages
        )
        summary = await self._summarize(old_text)

        # 3. 拼接: 摘要 + 最近原文
        summary_msg: dict[str, Any] = {
            "role": "system",
            "content": f"[历史摘要] {summary}",
        }
        return [summary_msg] + recent_messages

    async def _summarize(self, text: str) -> str:
        """LLM 摘要文本."""
        if not text.strip():
            return ""
        prompt = f"""请将以下研究上下文压缩为简洁摘要, 保留关键事实与结论, 不超过 {self.max_summary_tokens} 字:

{text[:8000]}

摘要:"""
        messages = [{"role": "user", "content": prompt}]
        response = await self._llm.achat(
            messages,
            tier=LLMTier.FAST,
            max_tokens=self.max_summary_tokens,
            temperature=0.3,
            span_name="context-summarize",
            step="context_manager",
        )
        return response.content


class WrittenContentCompressor:
    """已写入内容去重器 (P1-02, 对标 GPT Researcher WrittenContentCompressor).

    V2-P1 优化 (对标 GPTR):
    - 阈值走 settings.written_content_similarity_threshold (旧版硬编码 0.5)
    - chunk 级去重 (旧版整篇 content 比对, V2 切成 chunks 后逐 chunk 比对,
      与 GPTR WrittenContentCompressor 对齐)
    - 多查询并集去重 (对标 GPTR current_subtopic + draft_section_titles 并集)

    用 EmbeddingsClient 对已写入内容做相似度去重,
    避免重复内容进入上下文.
    """

    settings: Settings
    _embeddings: EmbeddingsClient
    threshold: float
    # V2-P1: chunk 级去重, 替代旧版整篇 content 比对
    _written_embeddings: list[list[float]]
    _written_chunks: list[str]
    # P4 修复: 单条 chunk embedding 缓存 (key=chunk sha256), 避免同一 chunk
    # 在不同 content 中重复嵌入; reset() 时一并清空.
    _chunk_cache: dict[str, list[float]]

    def __init__(
        self,
        settings: Settings | None = None,
        similarity_threshold: float | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._embeddings = get_embeddings_client()
        # V2-P1: 阈值走 settings (优先级: 参数 > settings > 默认 0.5)
        self.threshold = (
            similarity_threshold
            if similarity_threshold is not None
            else getattr(self.settings, "written_content_similarity_threshold", 0.5)
        )
        self._written_embeddings = []
        self._written_chunks = []
        self._chunk_cache = {}

    async def compute_embedding(
        self,
        content: str,
    ) -> tuple[list[str], list[list[float]]]:
        """锁外计算 content 的 chunk embeddings (P4 修复: 缩小锁粒度).

        在锁外完成网络 I/O (embed_texts), 利用 _chunk_cache 缓存单条 chunk 的
        embedding, 避免同一 chunk 在不同 content 中重复嵌入. 返回 (chunks,
        content_embs) 供 check_and_update 在锁内做 numpy 相似度比对.

        拆分动机: 旧版 should_keep 在 dedup_lock 内部调用 embed_texts, 使并行
        退化为串行 (P50=176s). 现将 embed_texts 移到锁外, 锁仅保护
        _written_embeddings / _written_chunks 的并发修改.

        Args:
            content: 待判断的内容字符串

        Returns:
            (chunks, content_embs): chunks 为分块后的文本列表, content_embs 为
            对应 embedding 列表. content 为空或 embedding 失败时返回 ([], []),
            调用方应据此降级保留内容.
        """
        if not content.strip():
            return [], []

        # V2-P1: chunk 级切分 (与 EmbeddingsFilter 同款递归分块, chunk_size=1000)
        from src.rag.embeddings_filter import EmbeddingsFilter

        chunks = EmbeddingsFilter._recursive_split(
            content,
            separators=["\n\n", "\n", " ", ""],
            chunk_size=self.settings.embeddings_filter_chunk_size,
            chunk_overlap=self.settings.embeddings_filter_chunk_overlap,
        )
        if not chunks:
            chunks = [content]

        # P4 修复: 单条 chunk embedding 缓存, 避免同一 chunk 在不同 content 中重复嵌入
        content_embs: list[list[float]] = [[] for _ in chunks]
        miss_indices: list[int] = []
        miss_chunks: list[str] = []
        for i, chunk in enumerate(chunks):
            cache_key = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
            cached = self._chunk_cache.get(cache_key)
            if cached is not None:
                content_embs[i] = cached
            else:
                miss_indices.append(i)
                miss_chunks.append(chunk)

        if miss_chunks:
            try:
                miss_embs = await self._embeddings.embed_texts(miss_chunks)
            except Exception as e:  # noqa: BLE001
                logger.warning("WrittenContentCompressor embedding 失败, 保留内容: %s", e)
                return [], []
            for idx, chunk, emb in zip(miss_indices, miss_chunks, miss_embs, strict=True):
                content_embs[idx] = emb
                cache_key = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
                self._chunk_cache[cache_key] = emb

        return chunks, content_embs

    def check_and_update(
        self,
        chunks: list[str],
        content_embs: list[list[float]],
    ) -> bool:
        """锁内同步: numpy 矩阵比对相似度 + 更新内部状态 (P4 修复).

        在锁内执行 (保护 _written_embeddings 和 _written_chunks 的并发修改),
        用 numpy 矩阵乘法一次性计算所有相似度, 替代旧版 O(N*M) 双重 for 循环.
        任意 chunk 与已写入 chunks 的最高相似度 >= threshold 即丢弃整篇 content,
        保留原语义.

        Args:
            chunks: 分块后的文本列表 (来自 compute_embedding)
            content_embs: 对应的 embedding 列表 (来自 compute_embedding)

        Returns:
            True 表示应保留 (无高度相似的已写入内容), False 表示应丢弃.
            chunks/content_embs 为空 (compute_embedding 失败) 时降级保留.
        """
        # compute_embedding 失败时返回 ([], []), 此处降级保留
        if not chunks or not content_embs:
            return True

        # 首次写入: 直接记录所有 chunks 的 embedding, 无需比对
        if not self._written_embeddings:
            self._written_embeddings.extend(content_embs)
            self._written_chunks.extend(chunks)
            return True

        # P4 修复: numpy 矩阵运算替代双重 for 循环
        # sim_matrix shape = (len(content_embs), len(self._written_embeddings))
        sim_matrix = ContextManager._cosine_similarity_batch(
            content_embs,
            self._written_embeddings,
        )
        if sim_matrix.size == 0:
            # 矩阵为空 (维度不匹配等), 降级保留并记录
            self._written_embeddings.extend(content_embs)
            self._written_chunks.extend(chunks)
            return True

        # 每个 chunk 与所有已写入 chunks 的最高相似度
        max_sims = np.max(sim_matrix, axis=1)
        # 任意 chunk 的最高相似度 >= threshold 即丢弃整篇 content (保留原语义)
        if float(np.max(max_sims)) >= self.threshold:
            return False

        # 无高度相似: 记录新 chunks 的 embedding
        self._written_embeddings.extend(content_embs)
        self._written_chunks.extend(chunks)
        return True

    async def should_keep(self, content: str) -> bool:
        """判断内容是否应保留 (兼容入口, 内部调用 compute_embedding + check_and_update).

        V2-P1: chunk 级去重. 旧版整篇 content 比对, 当 content 较长时
        相似度被稀释, 误判率高. V2 切成 chunks 后取最高相似度判断,
        与 GPTR WrittenContentCompressor 对齐.

        P4 修复: 内部拆分为 compute_embedding (锁外 I/O) + check_and_update
        (锁内 numpy 比对). 单调用方可直接用此方法; 并行场景应分别调用两步
        以缩小锁粒度 (见 report_generator._research_and_write_subtopic).

        Args:
            content: 待判断的内容字符串

        Returns:
            True 表示应保留 (无高度相似的已写入内容), False 表示应丢弃.
            空 content 返回 False (保留原语义); embedding 失败降级返回 True.
        """
        # 保留原语义: 空 content 直接丢弃 (不进入 compute_embedding)
        if not content.strip():
            return False
        chunks, content_embs = await self.compute_embedding(content)
        return self.check_and_update(chunks, content_embs)

    def reset(self) -> None:
        """重置已写入内容记录 (每次新研究调用)."""
        self._written_embeddings.clear()
        self._written_chunks.clear()
        self._chunk_cache.clear()
