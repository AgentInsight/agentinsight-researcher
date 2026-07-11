"""BM25Filter 关键词过滤器 (替代 EmbeddingsFilter 中段路由).

针对本项目 TEI CPU 部署的性能瓶颈:
- EmbeddingsFilter 方案: 258 chunks × TEI 推理 = ~43 分钟 (实测)
- BM25Filter: 258 chunks × 本地 jieba+BM25 = ~2 秒 (1000× 加速)

设计原则:
1. 签名与 EmbeddingsFilter.filter 完全一致, 支持平滑替换
2. 复用 embeddings_filter.recursive_split 模块级函数 (保证 chunk 级一致性)
3. 复用 retriever._get_tokens 模式 (jieba 分词 + LRU 缓存, 但实例独立)
4. 零网络调用, 零 TEI 依赖, 纯本地 CPU 计算
5. 降级策略: 失败返回原文前 N 条

两层路由策略 (context_manager.get_similar_content):
- Layer 1 Fast Path: < 8K 字符, 直接拼接原文 (零计算)
- Layer 2 BM25Filter + 可选 FastEmbed 精排: >= 8K 字符
  * BM25 先召回 Top-50 (粗筛, 本地 jieba+BM25Okapi)
  * 总 chunk 数 <= 30 → 直接返回 BM25 结果 (跳过 Embeddings)
  * 总 chunk 数 > 30 → FastEmbed 从 Top-50 中再选 Top-20 (精排, 本地 bge-small-zh)

AGENTS.md 第 7 章: BM25 用 rank-bm25+jieba (已声明, 零新依赖).
AGENTS.md 第 10 章: 检索节点必带 trace_retriever span (含 matched/candidate_count/retriever_type/top_score).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

from rank_bm25 import BM25Okapi

from src.config.settings import Settings, get_settings
from src.observability.tracing import trace_retriever
from src.rag.embeddings_filter import DEFAULT_SEPARATORS, recursive_split

logger = logging.getLogger(__name__)

# 递归分隔符 (复用 embeddings_filter 模块级常量, 保证 chunk 级一致性)
_RECURSIVE_SEPARATORS: list[str] = DEFAULT_SEPARATORS

# 分词缓存上限 (与 HybridRetriever._get_tokens 一致)
_TOKEN_CACHE_MAX_SIZE: int = 2000


class BM25Filter:
    """BM25 关键词过滤器 (替代 EmbeddingsFilter 中段路由).

    核心流程:
    1. 递归分块 (复用 embeddings_filter.recursive_split, chunk_size=1000, overlap=100)
    2. jieba 中文分词 + 实例级 LRU 缓存 (FIFO 淘汰, 上限 2000 条)
    3. BM25Okapi 语料构建 (显式传 k1=settings.bm25_k1, b=settings.bm25_b)
    4. BM25 打分 + Top-K 召回 (零网络调用)

    对比 EmbeddingsFilter:
    - 优势: 零网络调用, 10-200ms 响应 (vs 200-2000ms), 无 TEI 依赖
    - 劣势: 关键词匹配, 语义召回弱 (无法处理同义词/跨语言)
    - 定位: >=8K 字符区段主路径 (含 >50K 超长上下文, 全量覆盖)

    用法:
        filt = BM25Filter(settings)
        chunks = await filt.filter(query, documents, max_results=10)
    """

    settings: Settings
    _token_cache: dict[str, list[str]]

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        # 实例级分词缓存 (不直接调 HybridRetriever._get_tokens, 避免反向依赖 rag/retriever.py)
        # AGENTS.md 第 3 章: rag/ 内部模块尽量不互相 import
        self._token_cache: dict[str, list[str]] = {}

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
        """按 BM25 分数过滤文档块, 返回 Top-K 内容列表.

        签名与 EmbeddingsFilter.filter 一致 (便于平滑替换).

        Args:
            query: 查询文本 (用于 BM25 打分)
            documents: 文档列表, 每个 dict 含 content/url 等字段
            max_results: 最多返回条数
            threshold: BM25 分数阈值 (None 时用 settings.bm25_filter_score_threshold)
            user_id: 用户 ID (仅用于 trace, BM25 不参与隔离)
            session_id: 会话 ID (仅用于 trace)

        Returns:
            过滤后的内容字符串列表, 按 BM25 分数降序.
            失败时降级返回原文前 N 条.
        """
        if not documents:
            return []

        try:
            async with trace_retriever(
                name="bm25-filter",
                input={"query": query[:100], "doc_count": len(documents)},
                metadata={
                    "retriever_type": "bm25",
                    "user_id": user_id,
                    "session_id": session_id,
                },
                user_id=user_id,
                session_id=session_id,
            ) as span:
                # 1. 递归分块 (复用 embeddings_filter.recursive_split, 保证 chunk 级一致性)
                chunks = self._split_documents_recursive(documents)
                if not chunks:
                    span.update(
                        output={"matched": 0},
                        metadata={
                            "candidate_count": 0,
                            "retriever_type": "bm25",
                            "top_score": 0.0,
                        },
                    )
                    return [str(d.get("content", "")) for d in documents[:max_results]]

                # 2. jieba 分词 + 缓存 (异步并行, 避免阻塞事件循环)
                chunk_tokens = await asyncio.gather(
                    *[self._get_tokens_async(c["content"]) for c in chunks]
                )
                query_tokens = await self._get_tokens_async(query)

                if not query_tokens or not any(chunk_tokens):
                    span.update(
                        output={"matched": 0},
                        metadata={
                            "candidate_count": len(chunks),
                            "retriever_type": "bm25",
                            "top_score": 0.0,
                        },
                    )
                    return [str(d.get("content", "")) for d in documents[:max_results]]

                # 3. BM25 语料构建 + 打分 (CPU 密集操作放入线程池, 避免阻塞事件循环)
                # 显式传 k1/b, 修复 retriever.py:561 历史遗留未传参问题
                scores = await asyncio.to_thread(self._build_and_score, chunk_tokens, query_tokens)
                scored: list[tuple[float, str]] = list(
                    zip(scores, [c["content"] for c in chunks], strict=False)
                )

                # 阈值过滤 (默认 0.0 = 仅过滤零分文档, BM25 分数无上界不可与 cosine 阈值复用)
                score_threshold = (
                    threshold
                    if threshold is not None
                    else self.settings.bm25_filter_score_threshold
                )
                filtered = [(s, c) for s, c in scored if s > score_threshold]

                # 按分数降序取 Top-K
                filtered.sort(key=lambda x: x[0], reverse=True)
                top_k = self.settings.bm25_filter_top_k
                result = [c for _, c in filtered[: max(max_results, top_k)]]

                span.update(
                    output={"matched": len(result)},
                    metadata={
                        "candidate_count": len(chunks),
                        "retriever_type": "bm25",
                        "top_score": float(filtered[0][0]) if filtered else 0.0,
                    },
                )
                return result
        except Exception as e:  # noqa: BLE001
            logger.warning("BM25Filter 失败, 降级返回原文: %s", e)
            return [str(d.get("content", "")) for d in documents[:max_results]]

    def _split_documents_recursive(
        self,
        documents: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """递归分块 (复用 embeddings_filter.recursive_split, 保证 chunk 级一致性).

        chunk_size/chunk_overlap 走 bm25_filter_chunk_size/overlap 配置.
        """
        chunk_size = self.settings.bm25_filter_chunk_size
        chunk_overlap = self.settings.bm25_filter_chunk_overlap
        chunks: list[dict[str, str]] = []

        for doc in documents:
            content = str(doc.get("content", ""))
            if not content:
                continue
            source = doc.get("url", "")
            # 复用模块级函数 (与 WrittenContentCompressor 同款用法)
            split_texts = recursive_split(
                content,
                separators=_RECURSIVE_SEPARATORS,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            for text in split_texts:
                if text.strip():
                    chunks.append({"content": text, "source": source})
        return chunks

    def _get_tokens(self, text: str) -> list[str]:
        """jieba 分词 + 实例级 LRU 缓存 (复用 retriever._get_tokens 模式).

        与 HybridRetriever._get_tokens 实现一致, 但实例独立 (避免反向依赖).
        缓存上限 2000 条, FIFO 淘汰.

        Args:
            text: 待分词文本

        Returns:
            分词结果列表
        """
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cached = self._token_cache.get(key)
        if cached is not None:
            return cached

        import jieba

        tokens = list(jieba.cut(text))
        # FIFO 淘汰: 超过上限删除最旧 (dict 在 Python 3.7+ 保持插入顺序)
        if len(self._token_cache) >= _TOKEN_CACHE_MAX_SIZE:
            self._token_cache.pop(next(iter(self._token_cache)))
        self._token_cache[key] = tokens
        return tokens

    async def _get_tokens_async(self, text: str) -> list[str]:
        """异步 jieba 分词 (不阻塞事件循环).

        将同步 jieba.cut 放入线程池执行, 复用实例级 LRU 缓存.
        """
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cached = self._token_cache.get(key)
        if cached is not None:
            return cached
        tokens = await asyncio.to_thread(self._tokenize_sync, text)
        # FIFO 淘汰: 超过上限删除最旧 (dict 在 Python 3.7+ 保持插入顺序)
        if len(self._token_cache) >= _TOKEN_CACHE_MAX_SIZE:
            self._token_cache.pop(next(iter(self._token_cache)))
        self._token_cache[key] = tokens
        return tokens

    def _tokenize_sync(self, text: str) -> list[str]:
        """同步分词 (在线程池中执行)."""
        import jieba

        return list(jieba.cut(text))

    def _build_and_score(
        self,
        chunk_tokens: list[list[str]],
        query_tokens: list[str],
    ) -> list[float]:
        """同步构建 BM25 并打分 (在线程池中执行).

        显式传 k1/b, 修复 retriever.py:561 历史遗留未传参问题.
        """
        bm25 = BM25Okapi(
            chunk_tokens,
            k1=self.settings.bm25_k1,
            b=self.settings.bm25_b,
        )
        scores = bm25.get_scores(query_tokens)
        return scores
