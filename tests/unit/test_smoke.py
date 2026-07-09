"""冒烟测试: 核心模块可导入 + 核心函数可调用.

AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
冒烟测试只验证核心模块的导入性与基本可实例化性, 不深入业务逻辑:
- Settings SSOT 加载
- LLMClient (LiteLLM 网关) 实例化
- EmbeddingsClient 实例化 + 静态方法
- QdrantManager 实例化 (不连接 Qdrant)
- HybridRetriever 不在冒烟范围 (依赖 Redis/Qdrant/Embeddings 初始化, 易失败)
- LangGraph 图构建 (build_researcher_graph, 关闭 checkpointer)
- 关键节点函数可调用 (agent_creator_node 等)

冒烟测试失败说明核心模块存在导入/实例化问题, 应在 CI 构建期就阻断.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# ========== 核心配置模块 ==========


def test_settings_importable() -> None:
    """验证 src.config.settings 可导入."""
    from src.config.settings import Settings, get_settings

    assert Settings is not None
    assert callable(get_settings)


def test_settings_instantiable_without_env() -> None:
    """验证 Settings 可在无 .env 时实例化 (使用默认值)."""
    from src.config.settings import Settings

    settings = Settings(_env_file=None)
    # 默认值检查 (AGENTS.md 第 1 章: agent_name=agentinsight-researcher)
    assert settings.agent_name == "agentinsight-researcher"
    # 默认 LLM 三级分层 (AGENTS.md 第 9 章)
    assert settings.fast_llm
    assert settings.smart_llm
    assert settings.strategic_llm
    # Qdrant 默认配置 (AGENTS.md 第 7 章)
    assert settings.qdrant_collection == "agents"
    assert settings.qdrant_vector_size == 768


def test_get_settings_cached_singleton() -> None:
    """验证 get_settings 返回缓存实例 (lru_cache)."""
    from src.config.settings import get_settings

    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


# ========== LLM 网关模块 ==========


def test_llm_client_importable() -> None:
    """验证 src.llm.client 可导入."""
    from src.llm.client import LLMClient, LLMTier

    assert LLMClient is not None
    assert LLMTier is not None


def test_llm_client_instantiable() -> None:
    """验证 LLMClient 可在无 LLM API Key 时实例化."""
    from src.config.settings import Settings
    from src.llm.client import LLMClient

    settings = Settings(_env_file=None)
    client = LLMClient(settings)
    assert client is not None
    # 初始会话成本应为 0
    stats = client.get_session_cost()
    assert stats["call_count"] == 0
    assert stats["cost_usd"] == 0.0


def test_llm_tier_enum_complete() -> None:
    """验证 LLMTier 枚举包含三级 (FAST/SMART/STRATEGIC)."""
    from src.llm.client import LLMTier

    tiers = {t.name for t in LLMTier}
    assert "FAST" in tiers
    assert "SMART" in tiers
    assert "STRATEGIC" in tiers


# ========== Embeddings 模块 ==========


def test_embeddings_client_importable() -> None:
    """验证 src.rag.embeddings 可导入."""
    from src.rag.embeddings import EmbeddingsClient, get_embeddings_client

    assert EmbeddingsClient is not None
    assert callable(get_embeddings_client)


def test_embeddings_client_instantiable() -> None:
    """验证 EmbeddingsClient 可在无 API Key 时实例化."""
    from src.config.settings import Settings
    from src.rag.embeddings import EmbeddingsClient

    settings = Settings(_env_file=None)
    client = EmbeddingsClient(settings)
    assert client is not None


def test_embeddings_generate_point_id_static() -> None:
    """验证 EmbeddingsClient.generate_point_id 静态方法可调用 (uuid5 幂等)."""
    from src.rag.embeddings import EmbeddingsClient

    # 同一输入应生成相同 id (uuid5 幂等, AGENTS.md 第 7 章)
    id1 = EmbeddingsClient.generate_point_id("ns1", "content")
    id2 = EmbeddingsClient.generate_point_id("ns1", "content")
    id3 = EmbeddingsClient.generate_point_id("ns2", "content")
    assert id1 == id2, "uuid5 应幂等: 同 namespace+content 应生成相同 id"
    assert id1 != id3, "不同 namespace 应生成不同 id"


# ========== Qdrant 模块 ==========


def test_qdrant_manager_importable() -> None:
    """验证 src.rag.qdrant_manager 可导入."""
    from src.rag.qdrant_manager import QdrantManager, get_qdrant_manager

    assert QdrantManager is not None
    assert callable(get_qdrant_manager)


def test_qdrant_manager_instantiable() -> None:
    """验证 QdrantManager 可实例化 (不连接 Qdrant 服务).

    QdrantManager.__init__ 创建 AsyncQdrantClient 但不发起连接,
    连接发生在 ensure_collection/search 等异步方法中.
    """
    from src.config.settings import Settings
    from src.rag.qdrant_manager import QdrantManager

    settings = Settings(_env_file=None)
    mgr = QdrantManager(settings)
    assert mgr is not None


# ========== LangGraph 图模块 ==========


def test_graph_state_importable() -> None:
    """验证 src.graph.state 可导入."""
    from src.graph.state import ResearcherState

    assert ResearcherState is not None


def test_graph_nodes_importable() -> None:
    """验证 src.graph.nodes 所有关键节点函数可导入."""
    from src.graph.nodes import (
        agent_creator_node,
        deep_research_node,
        fact_checker_node,
        publisher_node,
        report_generator_node,
        research_conductor_node,
        reviewer_node,
        reviser_node,
        source_curator_node,
    )

    # 所有节点应为可调用对象 (函数/部分函数)
    for node in (
        agent_creator_node,
        deep_research_node,
        fact_checker_node,
        publisher_node,
        report_generator_node,
        research_conductor_node,
        reviewer_node,
        reviser_node,
        source_curator_node,
    ):
        assert callable(node), f"节点 {node} 不可调用"


def test_graph_builder_importable() -> None:
    """验证 src.graph.builder 可导入."""
    from src.graph.builder import build_researcher_graph

    assert callable(build_researcher_graph)


def test_graph_edges_importable() -> None:
    """验证 src.graph.edges 可导入 (路由函数)."""
    from src.graph import edges

    assert edges is not None


# ========== API 路由模块 ==========


def test_api_routes_importable() -> None:
    """验证 src.api.routes 可导入 (FastAPI 路由)."""
    from src.api import routes

    assert routes is not None


def test_api_middleware_importable() -> None:
    """验证 src.api.middleware 可导入 (JWT/安全头中间件)."""
    from src.api.middleware import JWTAuthMiddleware, SecurityHeadersMiddleware

    assert JWTAuthMiddleware is not None
    assert SecurityHeadersMiddleware is not None


# ========== 可观测性模块 ==========


def test_observability_tracing_importable() -> None:
    """验证 src.observability.tracing 可导入 (6 类 trace_xxx)."""
    from src.observability.tracing import (
        trace_agent,
        trace_chain,
        trace_embedding,
        trace_generation,
        trace_retriever,
        trace_tool,
    )

    # 所有 trace_xxx 应为可调用 (异步上下文管理器工厂)
    for trace_fn in (
        trace_agent,
        trace_chain,
        trace_embedding,
        trace_generation,
        trace_retriever,
        trace_tool,
    ):
        assert callable(trace_fn), f"trace 函数 {trace_fn} 不可调用"


# ========== Memory 模块 ==========


def test_memory_checkpointer_importable() -> None:
    """验证 src.memory.checkpointer 可导入."""
    from src.memory import checkpointer

    assert checkpointer is not None


def test_memory_db_initializer_importable() -> None:
    """验证 src.memory.db_initializer 可导入 (init_database)."""
    from src.memory.db_initializer import init_database

    assert callable(init_database)


# ========== Skills 模块 ==========


def test_skills_agent_creator_importable() -> None:
    """验证 src.skills.researcher.agent_creator 可导入 (GPTR agent_creator)."""
    from src.skills.researcher.agent_creator import AgentCreator

    assert AgentCreator is not None


def test_skills_research_conductor_importable() -> None:
    """验证 src.skills.researcher.research_conductor 可导入."""
    from src.skills.researcher.research_conductor import ResearchConductor

    assert ResearchConductor is not None


def test_skills_report_generator_importable() -> None:
    """验证 src.skills.researcher.report_generator 可导入."""
    from src.skills.researcher.report_generator import ReportGenerator

    assert ReportGenerator is not None


# ========== FastAPI 服务端 ==========


def test_server_app_importable() -> None:
    """验证 server.py 可导入 (FastAPI app 实例).

    AGENTS.md 第 12 章: server.py 为入口, CMD ["python", "server.py"].
    本测试仅验证模块可导入, 不实际启动服务 (避免触发 lifespan).
    """
    import server

    assert hasattr(server, "app"), "server 模块缺少 app 属性 (FastAPI 实例)"
