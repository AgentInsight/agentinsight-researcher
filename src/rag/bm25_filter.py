"""BM25Filter 关键词过滤器 (V4-P3 L2 方案, 替代 EmbeddingsFilter 中段路由).

对标 GPTR EmbeddingsFilter 的轻量替代方案, 针对本项目 TEI CPU 部署的性能瓶颈:
- EmbeddingsFilter: 258 chunks × TEI 推理 = ~43 分钟 (Trace aac742d8 实测)
- BM25Filter: 258 chunks × 本地 jieba+BM25 = ~2 秒 (1000× 加速)

设计原则:
1. 签名与 EmbeddingsFilter.filter 完全一致, 支持平滑替换
2. 复用 EmbeddingsFilter._recursive_split 静态方法 (保证 chunk 级一致性)
3. 复用 retriever._get_tokens 模式 (jieba 分词 + LRU 缓存, 但实例独立)
4. 零网络调用, 零 TEI 依赖, 纯本地 CPU 计算
5. 降级策略与 EmbeddingsFilter 对齐 (失败返回原文前 N 条)

三层路由策略 (context_manager.get_similar_content):
- Layer 1 Fast Path: < 8K 字符, 直接拼接原文 (零计算)
- Layer 2 BM25Filter: 8K-50K 字符, jieba+BM25Okapi 本地过滤 (主路径)
- Layer 3 EmbeddingsFilter: > 50K 字符, TEI embedding 兜底 (高精度)

AGENTS.md 第 7 章: BM25 用 rank-bm25+jieba (已声明, 零新依赖).
AGENTS.md 第 10 章: 检索节点必带 trace_retriever span (含 matched/candidate_count/retriever_type/top_score).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from rank_bm25 import BM25Okapi

from src.config.settings import Settings, get_settings
from src.observability.tracing import trace_retriever
from src.rag.embeddings_filter import EmbeddingsFilter

logger = logging.getLogger(__name__)

# 递归分隔符 (与 EmbeddingsFilter 对齐, 保证 chunk 级一致性)
_RECURSIVE_SEPARATORS: list[str] = ["\n\n", "\n", " ", ""]

# 分词缓存上限 (与 HybridRetriever._get_tokens 一致)
_TOKEN_CACHE_MAX_SIZE: int = 2000


class BM25Filter:
    """BM25 关键词过滤器 (L2 方案, 替代 EmbeddingsFilter 中段路由).

    核心流程:
    1. 递归分块 (复用 EmbeddingsFilter._recursive_split, chunk_size=1000, overlap=100)
    2. jieba 中文分词 + 实例级 LRU 缓存 (FIFO 淘汰, 上限 2000 条)
    3. BM25Okapi 语料构建 (显式传 k1=settings.bm25_k1, b=settings.bm25_b)
    4. BM25 打分 + Top-K 召回 (零网络调用)

    对比 EmbeddingsFilter:
    - 优势: 零网络调用, 10-200ms 响应 (vs 200-2000ms), 无 TEI 依赖
    - 劣势: 关键词匹配, 语义召回弱 (无法处理同义词/跨语言)
    - 定位: 8K-50K 字符区段主路径, >50K 降级到 EmbeddingsFilter

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

        签名与 EmbeddingsFilter.filter 完全一致, 支持平滑替换.

        Args:
            query: 查询文本 (用于 BM25 打分)
            documents: 文档列表, 每个 dict 含 content/url 等字段
            max_results: 最多返回条数
            threshold: BM25 分数阈值 (None 时用 settings.bm25_filter_score_threshold)
            user_id: 用户 ID (仅用于 trace, BM25 不参与隔离)
            session_id: 会话 ID (仅用于 trace)

        Returns:
            过滤后的内容字符串列表, 按 BM25 分数降序.
            失败时降级返回原文前 N 条 (与 EmbeddingsFilter 降级策略对齐).
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
                # 1. 递归分块 (复用 EmbeddingsFilter 静态方法, 保证 chunk 级一致性)
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
                    return [
                        str(d.get("content", "")) for d in documents[:max_results]
                    ]

                # 2. jieba 分词 + 缓存
                chunk_tokens = [self._get_tokens(c["content"]) for c in chunks]
                query_tokens = self._get_tokens(query)

                if not query_tokens or not any(chunk_tokens):
                    span.update(
                        output={"matched": 0},
                        metadata={
                            "candidate_count": len(chunks),
                            "retriever_type": "bm25",
                            "top_score": 0.0,
                        },
                    )
                    return [
                        str(d.get("content", "")) for d in documents[:max_results]
                    ]

                # 3. BM25 语料构建 (显式传 k1/b, 修复 retriever.py:561 历史遗留未传参问题)
                bm25 = BM25Okapi(
                    chunk_tokens,
                    k1=self.settings.bm25_k1,
                    b=self.settings.bm25_b,
                )

                # 4. 打分 + 阈值过滤
                scores = bm25.get_scores(query_tokens)
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
        """递归分块 (复用 EmbeddingsFilter._recursive_split, 保证 chunk 级一致性).

        与 EmbeddingsFilter._split_documents_recursive 实现对齐,
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
            # 复用 EmbeddingsFilter 静态方法 (与 WrittenContentCompressor 同款用法)
            split_texts = EmbeddingsFilter._recursive_split(
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
