"""单元测试: RAG 降级链集成.

验证 src/rag/retriever.py + src/rag/embeddings.py + src/rag/bm25_filter.py 的降级链:
- Qdrant 不可用降级 (ENV=dev 限定, 检索不抛异常, 降级返回空/BM25 失败路径)
- Embeddings API key 鉴权 (Authorization: Bearer)
- Rerank API key 鉴权 (Authorization: Bearer)
- BM25 + jieba 中文分词边界 (rank-bm25 + jieba)
- vector_weight=0.7 / bm25_weight=0.3 配置变化对 RRF 融合分数的影响

- Qdrant 不可用时降级内存检索仅限 ENV=dev; 生产应告警并失败转移
- Embeddings/Rerank TEI 服务通过环境变量 API_KEY 开启鉴权,
  客户端通过 embeddings_api_key/rerank_api_key 配置传递 Authorization: Bearer <key>
- BM25 用 rank-bm25+jieba (中文分词 + IDF)
- 检索必须混合 BM25 + 向量, 默认 vector_weight=0.7 / bm25_weight=0.3

单元测试在构建期执行, 不依赖外部服务.
所有外部依赖 (Qdrant/Redis/TEI) 全部 mock.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from rank_bm25 import BM25Okapi

from src.config.settings import Settings
from src.rag.bm25_filter import BM25Filter
from src.rag.embeddings import EmbeddingsClient
from src.rag.retriever import HybridRetriever

pytestmark = pytest.mark.unit


# ========== 共享 fixture ==========


def _make_retriever(settings: Settings) -> HybridRetriever:
    """构造 HybridRetriever (跳过 __init__, 手动设置必要属性)."""
    obj = HybridRetriever.__new__(HybridRetriever)
    obj.settings = settings
    obj._embeddings = MagicMock()
    obj._qdrant = MagicMock()
    obj._rerank_client = MagicMock()
    obj._redis = None
    obj._redis_initialized = True
    obj._bm25_corpus = []
    obj._bm25_docs = []
    obj._bm25 = None
    obj._bm25_per_namespace = {}
    import weakref

    obj._bm25_load_locks = weakref.WeakValueDictionary()
    obj._token_cache = {}
    obj._inflight_locks = weakref.WeakValueDictionary()
    return obj


# ========== 1. Qdrant 不可用降级 (ENV=dev 限定) ==========


async def test_qdrant_unavailable_degrades_to_memory_dev_only() -> None:
    """Qdrant 不可用时降级 (ENV=dev), 检索不抛异常, 降级返回空结果.

    Qdrant 不可用时降级内存检索仅限 ENV=dev;
    生产应告警并失败转移. 本测试验证 dev 环境下 Qdrant 异常时:
    - _ensure_bm25_corpus 不抛异常 (BM25 路径降级返回空)
    - _vector_search 不抛异常 (向量路径降级返回空)
    - retrieve 整体返回 [] (优雅降级, 不阻断业务)
    """
    settings = Settings(env="dev", _env_file=None)
    retriever = _make_retriever(settings)

    # mock namespace 有数据 (使检索路径进入 BM25+Vector)
    retriever._qdrant.build_data_shared_namespace = MagicMock(return_value="agent-data")
    retriever._qdrant.namespace_has_data = AsyncMock(return_value=True)
    # Qdrant scroll 失败 (BM25 语料拉取失败)
    retriever._qdrant.scroll_all_by_namespace = AsyncMock(
        side_effect=RuntimeError("Qdrant unreachable")
    )
    # 版本号读取也失败 (Redis 不可用, 降级默认 1)
    retriever._get_bm25_version = AsyncMock(side_effect=RuntimeError("Redis down"))
    # 向量检索失败 (Qdrant search 异常)
    retriever._embeddings.embed_query = AsyncMock(return_value=[0.1] * 10)
    retriever._qdrant.search = AsyncMock(side_effect=RuntimeError("Qdrant search failed"))
    # 缓存未命中
    retriever._get_cache = AsyncMock(return_value=None)
    retriever._set_cache = AsyncMock(return_value=None)

    # 不应抛异常 (dev 环境降级返回空)
    results = await retriever.retrieve("测试查询", user_id=None, top_k=5)

    # 降级: 返回空列表 (BM25 失败 + 向量失败)
    assert results == []
    # _set_cache 应被调用 (写入空结果缓存)
    retriever._set_cache.assert_awaited()


# ========== 2. Embeddings API key 鉴权头 ==========


def test_embeddings_api_key_auth_header() -> None:
    """Embeddings API key 鉴权: 客户端注入 Authorization: Bearer <key>.

    TEI 服务通过环境变量 API_KEY 开启鉴权,
    客户端通过 embeddings_api_key 配置传递 Authorization: Bearer 请求头.
    验证:
    - embeddings_api_key 非空时, httpx.AsyncClient headers 含 Bearer
    - embeddings_api_key 为空时, 不注入 Authorization 头
    """
    # 非空 API key: 应注入 Bearer
    settings_with_key = Settings(embeddings_api_key="tei-secret-key-123", _env_file=None)
    client_with_key = EmbeddingsClient(settings_with_key)
    assert client_with_key._client.headers["authorization"] == "Bearer tei-secret-key-123"

    # 空 API key: 不应注入 Authorization 头
    settings_no_key = Settings(embeddings_api_key=None, _env_file=None)
    client_no_key = EmbeddingsClient(settings_no_key)
    assert "authorization" not in client_no_key._client.headers


# ========== 3. Rerank API key 鉴权头 ==========


def test_rerank_api_key_auth_header() -> None:
    """Rerank API key 鉴权: 客户端注入 Authorization: Bearer <key>.

    Rerank TEI 服务通过 API_KEY 开启鉴权,
    客户端通过 rerank_api_key 配置传递 Authorization: Bearer 请求头.
    验证 HybridRetriever 的 _rerank_client 在构造时正确注入鉴权头.
    """
    # 非空 rerank_api_key: _rerank_client headers 应含 Bearer
    settings_with_key = Settings(rerank_api_key="rerank-secret-456", _env_file=None)
    retriever_with_key = HybridRetriever(settings_with_key)
    assert retriever_with_key._rerank_client.headers["authorization"] == "Bearer rerank-secret-456"

    # 空 rerank_api_key: 不应注入 Authorization 头
    settings_no_key = Settings(rerank_api_key=None, _env_file=None)
    retriever_no_key = HybridRetriever(settings_no_key)
    assert "authorization" not in retriever_no_key._rerank_client.headers


# ========== 4. BM25 + jieba 中文分词边界 ==========


async def test_bm25_chinese_tokenization_with_jieba() -> None:
    """BM25 + jieba 中文分词边界: 中文短语/复合词正确分词与召回.

    BM25 用 rank-bm25+jieba (中文分词 + IDF).
    验证:
    - jieba 正确切分中文复合词 (如 "量子计算" → ["量子", "计算"])
    - BM25Okapi 对中文分词后的语料能正确打分 (相关文档 score > 0,
      无共同词的文档 score = 0 被过滤)
    - BM25Filter 集成 jieba 分词 + BM25Okapi 端到端流程
    """
    settings = Settings(_env_file=None)
    bm25_filter = BM25Filter(settings)

    # 构造含中文复合词的文档 (3 篇, 使 IDF > 0)
    documents = [
        {
            "content": "量子计算是利用量子力学原理进行计算的新技术, 量子比特是其基本单位.",
            "url": "https://example.com/quantum",
        },
        {
            "content": "新能源汽车使用电池作为动力来源, 锂电池技术是关键.",
            "url": "https://example.com/ev",
        },
        {
            "content": "人工智能通过神经网络模拟人类学习过程, 深度学习是其分支.",
            "url": "https://example.com/ai",
        },
    ]

    # 查询 "量子计算" 应优先召回量子计算文档
    result = await bm25_filter.filter(
        "量子计算",
        documents,
        max_results=3,
    )

    # 应返回非空结果列表
    assert isinstance(result, list)
    assert len(result) > 0
    # 所有结果应为字符串
    assert all(isinstance(c, str) for c in result)
    # 量子计算文档应出现在结果中 (jieba 切分 "量子计算" → ["量子", "计算"],
    # 与第一篇文档有共同词项, BM25 score > 0)
    assert any("量子计算" in c for c in result)

    # 验证 jieba 分词边界: "量子计算" 切分结果含 "量子" 与 "计算"
    tokens = bm25_filter._get_tokens("量子计算")
    assert "量子" in tokens or "计算" in tokens, (
        f"jieba 应切分 '量子计算' 为含 '量子'/'计算' 的 token 列表, 实际: {tokens}"
    )


# ========== 5. vector_weight / bm25_weight 配置变化 ==========


def test_vector_weight_bm25_weight_config_variation() -> None:
    """vector_weight=0.7 / bm25_weight=0.3 配置变化对 RRF 融合分数与排序的影响.

    默认 vector_weight=0.7 / bm25_weight=0.3, RRF k=60.
    验证:
    - 文档在向量与 BM25 中排名不同时, 权重变化影响融合分数
    - 高 vector_weight 偏向向量排名高的文档; 高 bm25_weight 偏向 BM25 排名高的文档
    - 极端权重 (0.9/0.1 vs 0.1/0.9) 会导致最终排序翻转
    """
    k = 60  # rrf_k

    # 构造 docA/docB 在向量与 BM25 中排名相反的场景:
    # - 向量: [docB, docA] (docB rank 0, docA rank 1)
    # - BM25: [docA, docB] (docA rank 0, docB rank 1)
    # 不同权重会使 docA/docB 的融合分数此消彼长, 排序可能翻转.
    vector_results = [
        {"content": "docB", "score": 0.9},
        {"content": "docA", "score": 0.8},
    ]
    bm25_results = [
        {"content": "docA", "score": 5.0},
        {"content": "docB", "score": 3.0},
    ]

    def _fuse(vw: float, bw: float) -> list[dict[str, Any]]:
        settings = Settings(vector_weight=vw, bm25_weight=bw, rrf_k=k, _env_file=None)
        retriever = HybridRetriever(settings)
        return retriever._rrf_fuse(vector_results, bm25_results, vector_weight=vw, bm25_weight=bw)

    # 默认配置 (0.7/0.3): vector 权重高, docB (vector rank 0) 应排第一
    fused_default = _fuse(0.7, 0.3)
    # docA = 0.7/(k+2) + 0.3/(k+1)  (vector rank 1, bm25 rank 0)
    # docB = 0.7/(k+1) + 0.3/(k+2)  (vector rank 0, bm25 rank 1)
    expected_docA_default = 0.7 / (k + 2) + 0.3 / (k + 1)  # noqa: N806
    expected_docB_default = 0.7 / (k + 1) + 0.3 / (k + 2)  # noqa: N806
    scores_default = {f["content"]: f["score"] for f in fused_default}
    assert abs(scores_default["docA"] - expected_docA_default) < 1e-9
    assert abs(scores_default["docB"] - expected_docB_default) < 1e-9
    # docB 应排第一 (vector 权重高, docB 在 vector 中 rank 0)
    assert fused_default[0]["content"] == "docB"

    # 反转配置 (0.3/0.7): bm25 权重高, docA (bm25 rank 0) 应排第一
    flipped = _fuse(0.3, 0.7)
    scores_flipped = {f["content"]: f["score"] for f in flipped}
    expected_docA_flipped = 0.3 / (k + 2) + 0.7 / (k + 1)  # noqa: N806
    expected_docB_flipped = 0.3 / (k + 1) + 0.7 / (k + 2)  # noqa: N806
    assert abs(scores_flipped["docA"] - expected_docA_flipped) < 1e-9
    assert abs(scores_flipped["docB"] - expected_docB_flipped) < 1e-9
    # docA 应排第一 (bm25 权重高, docA 在 bm25 中 rank 0) - 排序翻转
    assert flipped[0]["content"] == "docA"

    # 平衡配置 (0.5/0.5): 两文档贡献对称, 分数应相同 (tie)
    balanced = _fuse(0.5, 0.5)
    scores_balanced = {f["content"]: f["score"] for f in balanced}
    expected_docA_balanced = 0.5 / (k + 2) + 0.5 / (k + 1)  # noqa: N806
    expected_docB_balanced = 0.5 / (k + 1) + 0.5 / (k + 2)  # noqa: N806
    assert abs(scores_balanced["docA"] - expected_docA_balanced) < 1e-9
    assert abs(scores_balanced["docB"] - expected_docB_balanced) < 1e-9
    # 平衡权重下两文档分数应相等 (对称场景)
    assert abs(scores_balanced["docA"] - scores_balanced["docB"]) < 1e-9

    # 三种配置下 docA 的融合分数应各不相同 (权重变化影响融合分数)
    docA_scores = {  # noqa: N806
        scores_default["docA"],
        scores_flipped["docA"],
        scores_balanced["docA"],
    }
    assert len(docA_scores) == 3, f"三种权重配置下 docA 应有 3 个不同融合分数, 实际: {docA_scores}"

    # 验证 BM25Okapi 在不同权重下仍可正常构建 (jieba 分词 + 中文语料)
    docs = [
        {"content": "量子计算研究", "metadata": {}, "namespace": "ns1"},
        {"content": "人工智能发展", "metadata": {}, "namespace": "ns1"},
        {"content": "新能源技术", "metadata": {}, "namespace": "ns1"},
    ]
    settings_for_corpus = Settings(vector_weight=0.7, bm25_weight=0.3, _env_file=None)
    retriever_for_corpus = HybridRetriever(settings_for_corpus)
    retriever_for_corpus.update_bm25_corpus(docs)
    assert retriever_for_corpus._bm25 is not None
    assert isinstance(retriever_for_corpus._bm25, BM25Okapi)
