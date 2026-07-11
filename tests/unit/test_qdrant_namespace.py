"""单元测试: Qdrant namespace 与 Embeddings 点 id 生成.

验证 namespace 隔离规则与幂等点 id 生成.
不实际连接 Qdrant, 仅测试逻辑.
"""

from __future__ import annotations

from src.config.settings import Settings
from src.rag.embeddings import EmbeddingsClient
from src.rag.qdrant_manager import QdrantManager


def test_shared_namespace():
    """测试共享知识库 namespace = agent_id."""
    settings = Settings(agent_name="agentinsight-researcher", _env_file=None)
    mgr = QdrantManager(settings)
    assert mgr.build_shared_namespace() == "agentinsight-researcher"


def test_user_namespace():
    """测试用户私有 namespace = {agent_id}:{user_id}."""
    settings = Settings(agent_name="agentinsight-researcher", _env_file=None)
    mgr = QdrantManager(settings)
    assert mgr.build_user_namespace("user123") == "agentinsight-researcher:user123"


def test_point_id_idempotent():
    """测试点 id 幂等生成 (相同 namespace+content 生成相同 id)."""
    ns = "agentinsight-researcher"
    content = "这是一段测试内容"
    id1 = EmbeddingsClient.generate_point_id(ns, content)
    id2 = EmbeddingsClient.generate_point_id(ns, content)
    assert id1 == id2  # 幂等


def test_point_id_differs_by_namespace():
    """测试不同 namespace 生成不同 id."""
    content = "相同内容"
    id1 = EmbeddingsClient.generate_point_id("ns1", content)
    id2 = EmbeddingsClient.generate_point_id("ns2", content)
    assert id1 != id2


def test_point_id_differs_by_content():
    """测试不同 content 生成不同 id."""
    ns = "agentinsight-researcher"
    id1 = EmbeddingsClient.generate_point_id(ns, "内容A")
    id2 = EmbeddingsClient.generate_point_id(ns, "内容B")
    assert id1 != id2


def test_point_id_is_uuid_format():
    """测试点 id 为 UUID 格式."""
    point_id = EmbeddingsClient.generate_point_id("ns", "content")
    # UUID 字符串格式: 8-4-4-4-12
    parts = point_id.split("-")
    assert len(parts) == 5
    assert len(parts[0]) == 8
    assert len(parts[1]) == 4
    assert len(parts[2]) == 4
    assert len(parts[3]) == 4
    assert len(parts[4]) == 12
