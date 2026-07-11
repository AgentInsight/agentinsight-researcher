"""单元测试: HybridRetriever RRF 融合算法.

验证 RRF 倒数排名融合逻辑, 不实际连接 Qdrant/Embeddings.
"""

from __future__ import annotations

import pytest

from src.config.settings import Settings
from src.rag.retriever import HybridRetriever


def test_rrf_fuse_combines_vector_and_bm25():
    """测试 RRF 融合向量与 BM25 结果."""
    settings = Settings(vector_weight=0.7, bm25_weight=0.3, rrf_k=60, _env_file=None)
    retriever = HybridRetriever(settings)

    vector_results = [
        {"content": "文档A", "score": 0.9},
        {"content": "文档B", "score": 0.8},
    ]
    bm25_results = [
        {"content": "文档B", "score": 5.0},
        {"content": "文档C", "score": 4.0},
    ]

    fused = retriever._rrf_fuse(
        vector_results,
        bm25_results,
        vector_weight=0.7,
        bm25_weight=0.3,
    )

    # 应有 3 个不重复文档
    contents = [f["content"] for f in fused]
    assert set(contents) == {"文档A", "文档B", "文档C"}

    # 文档B 在两个来源都出现, 应排名靠前
    assert fused[0]["content"] == "文档B"


def test_rrf_fuse_empty_inputs():
    """测试 RRF 融合空输入."""
    settings = Settings(_env_file=None)
    retriever = HybridRetriever(settings)
    assert retriever._rrf_fuse([], [], vector_weight=0.7, bm25_weight=0.3) == []


def test_rrf_fuse_only_vector():
    """测试 RRF 仅向量结果."""
    settings = Settings(_env_file=None)
    retriever = HybridRetriever(settings)
    vector_results = [{"content": "文档A", "score": 0.9}]
    fused = retriever._rrf_fuse(
        vector_results,
        [],
        vector_weight=0.7,
        bm25_weight=0.3,
    )
    assert len(fused) == 1
    assert fused[0]["content"] == "文档A"


def test_rrf_score_formula():
    """测试 RRF 分数公式: weight / (k + rank + 1)."""
    settings = Settings(vector_weight=0.7, bm25_weight=0.3, rrf_k=60, _env_file=None)
    retriever = HybridRetriever(settings)

    # 单个向量结果, rank=0
    vector_results = [{"content": "唯一文档", "score": 0.9}]
    fused = retriever._rrf_fuse(
        vector_results,
        [],
        vector_weight=0.7,
        bm25_weight=0.3,
    )
    # 期望分数: 0.7 / (60 + 0 + 1) = 0.7 / 61
    expected = 0.7 / 61
    assert abs(fused[0]["score"] - expected) < 1e-6


@pytest.mark.asyncio
async def test_build_data_namespaces_shared_only_when_no_user():
    """测试无 user_id 且共享 namespace 有数据时, 只检索共享 namespace.

    3.5.2 死代码修复: 旧版 build_namespaces 已删除, 改测新版 build_data_namespaces.
    新版含私有数据存在性检查, 不连接 Qdrant 时用 fake manager 模拟.
    """
    settings = Settings(agent_name="agentinsight-researcher", _env_file=None)
    retriever = HybridRetriever(settings)

    # Fake QdrantManager: 共享 ns 有数据, 用户私有数据检查不被触发 (user_id=None)
    class _FakeQdrant:
        def build_data_shared_namespace(self) -> str:
            return "agentinsight-researcher-data"

        def build_data_user_namespace(self, user_id: str) -> str:
            return f"agentinsight-researcher-data:{user_id}"

        async def namespace_has_data(self, _namespace: str) -> bool:
            return True  # 共享 namespace 有数据

        async def has_user_private_data(self, _user_id: str) -> bool:
            return False  # 不应被调用 (user_id=None)

    retriever._qdrant = _FakeQdrant()  # type: ignore[assignment]

    namespaces, has_private = await retriever.build_data_namespaces(user_id=None)
    assert namespaces == ["agentinsight-researcher-data"]
    assert has_private is False


@pytest.mark.asyncio
async def test_build_data_namespaces_with_user_and_private_data():
    """测试有 user_id 且共享/私有均有数据时, 检索共享 + 用户私有 namespace."""
    settings = Settings(agent_name="agentinsight-researcher", _env_file=None)
    retriever = HybridRetriever(settings)

    class _FakeQdrant:
        def build_data_shared_namespace(self) -> str:
            return "agentinsight-researcher-data"

        def build_data_user_namespace(self, user_id: str) -> str:
            return f"agentinsight-researcher-data:{user_id}"

        async def namespace_has_data(self, _namespace: str) -> bool:
            return True  # 共享 namespace 有数据

        async def has_user_private_data(self, _user_id: str) -> bool:
            return True  # 用户有私有数据

    retriever._qdrant = _FakeQdrant()  # type: ignore[assignment]

    namespaces, has_private = await retriever.build_data_namespaces(user_id="user123")
    assert "agentinsight-researcher-data" in namespaces
    assert "agentinsight-researcher-data:user123" in namespaces
    assert has_private is True


@pytest.mark.asyncio
async def test_build_data_namespaces_empty_when_no_data():
    """测试共享与私有均无数据时返回空列表 (避免无意义 embeddings 调用)."""
    settings = Settings(agent_name="agentinsight-researcher", _env_file=None)
    retriever = HybridRetriever(settings)

    class _FakeQdrant:
        def build_data_shared_namespace(self) -> str:
            return "agentinsight-researcher-data"

        def build_data_user_namespace(self, user_id: str) -> str:
            return f"agentinsight-researcher-data:{user_id}"

        async def namespace_has_data(self, _namespace: str) -> bool:
            return False  # 共享 namespace 无数据

        async def has_user_private_data(self, _user_id: str) -> bool:
            return False  # 用户无私有数据

    retriever._qdrant = _FakeQdrant()  # type: ignore[assignment]

    namespaces, has_private = await retriever.build_data_namespaces(user_id="user123")
    assert namespaces == []
    assert has_private is False


# ========== P0 BM25 断点修复: _bm25_cache_uid / _bm25_version_key / _bm25_corpus_key ==========


def test_bm25_cache_uid_shared_namespace_uses_anonymous():
    """共享 namespace 使用 anonymous 常量作为缓存键 (跨用户共享).

    default_user_id 环境变量已移除, RAG 层共享 namespace
    缓存键用 _ANONYMOUS_USER_ID = "anonymous" 常量替代.
    """
    settings = Settings(agent_name="test-agent", _env_file=None)
    retriever = HybridRetriever(settings)

    class _FakeQdrant:
        def build_data_shared_namespace(self) -> str:
            return "test-agent-data"

    retriever._qdrant = _FakeQdrant()  # type: ignore[assignment]

    # 共享 namespace: 无论 user_id 是什么, 缓存 uid 都用 anonymous 常量
    assert retriever._bm25_cache_uid("test-agent-data", "user123") == "anonymous"
    assert retriever._bm25_cache_uid("test-agent-data", None) == "anonymous"


def test_bm25_cache_uid_private_namespace_uses_user_id():
    """用户私有 namespace 使用实际 user_id 作为缓存键 (隔离)."""
    settings = Settings(agent_name="test-agent", _env_file=None)
    retriever = HybridRetriever(settings)

    class _FakeQdrant:
        def build_data_shared_namespace(self) -> str:
            return "test-agent-data"

    retriever._qdrant = _FakeQdrant()  # type: ignore[assignment]

    # 私有 namespace: 使用实际 user_id
    assert retriever._bm25_cache_uid("test-agent-data:user123", "user123") == "user123"
    # user_id 缺失时降级到 anonymous 常量
    assert retriever._bm25_cache_uid("test-agent-data:user123", None) == "anonymous"


def test_bm25_version_key_format():
    """版本号 Redis 键格式: {agent_id}:{cache_uid}:rag:bm25_corpus_version:{namespace}."""
    settings = Settings(agent_name="test-agent", _env_file=None)
    retriever = HybridRetriever(settings)
    key = retriever._bm25_version_key("test-agent-data", "user123")
    assert key == "test-agent:user123:rag:bm25_corpus_version:test-agent-data"


def test_bm25_corpus_key_format_includes_version():
    """语料 Redis 键含版本号: {agent_id}:{cache_uid}:rag:bm25_corpus:{ns}:v{version}."""
    settings = Settings(agent_name="test-agent", _env_file=None)
    retriever = HybridRetriever(settings)
    key = retriever._bm25_corpus_key("test-agent-data", "user123", 5)
    assert key == "test-agent:user123:rag:bm25_corpus:test-agent-data:v5"
