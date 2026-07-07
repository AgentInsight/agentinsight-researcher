"""单元测试: 多 Agent 图构建器.

验证 src/graph/multi_agent_builder.py:
- build_revision_subgraph: reviewer↔reviser 子图结构与守卫
- build_multi_agent_graph: 线性+条件边主图结构与 max_iterations 守卫

图结构 (对标 GPT Researcher multi_agents/main.py):
    START → agent_creator → researcher → writer → fact_checker
    fact_checker → (accept → revision 子图 | revise → writer)
    revision 子图 → visualizer → publisher → END

    revision 子图: START → reviewer → (accept → END | revise → reviser)
                   reviser → reviewer

守卫 (AGENTS.md 第 5 章: max_iterations 为硬上限):
- fact_checker revise → writer: create_fact_check_guard(graph_max_iterations)
- reviewer revise → reviser: create_revision_guard(max_revisions)

AGENTS.md 第 13 章: 单元测试不依赖外部服务 (use_checkpointer=False 跳过 Postgres).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.graph.multi_agent_builder import (
    build_multi_agent_graph,
    build_revision_subgraph,
)

pytestmark = pytest.mark.unit


def _edge_pairs(compiled: Any) -> list[tuple[str, str]]:
    """提取编译图的边列表 (source, target).

    使用 compiled.get_graph().edges 获取 Graph 对象的边,
    每条边有 source/target 属性 (含 __start__/__end__ 伪节点).
    """
    graph = compiled.get_graph()
    return [(e.source, e.target) for e in graph.edges]


# ========== build_revision_subgraph ==========


class TestBuildRevisionSubgraph:
    """build_revision_subgraph 子图结构测试."""

    def test_build_revision_subgraph_structure(self) -> None:
        """验证 reviewer↔reviser 子图节点与边.

        子图结构:
            START → reviewer → (accept → END | revise → reviser)
            reviser → reviewer
        """
        settings = Settings(_env_file=None, max_revisions=3)
        subgraph = build_revision_subgraph(settings)

        # 1. 节点验证: reviewer + reviser (不含 __start__/__end__ 伪节点)
        node_names = set(subgraph.nodes.keys())
        assert "reviewer" in node_names
        assert "reviser" in node_names

        # 2. 边验证
        edges = _edge_pairs(subgraph)
        # __start__ → reviewer (入口)
        assert ("__start__", "reviewer") in edges
        # reviewer → reviser (条件边 revise 分支)
        assert ("reviewer", "reviser") in edges
        # reviewer → __end__ (条件边 accept 分支)
        assert ("reviewer", "__end__") in edges
        # reviser → reviewer (循环回边)
        assert ("reviser", "reviewer") in edges

    def test_build_revision_subgraph_uses_max_revisions(self) -> None:
        """验证子图用 settings.max_revisions 创建守卫 (达上限强制 accept)."""
        settings = Settings(_env_file=None, max_revisions=5)
        with patch(
            "src.graph.multi_agent_builder.create_revision_guard"
        ) as mock_guard:
            mock_guard.return_value = lambda state: "accept"
            build_revision_subgraph(settings)
            mock_guard.assert_called_once_with(5)

    def test_build_revision_subgraph_no_checkpointer(self) -> None:
        """验证子图不挂 checkpointer (由父图统一持久化)."""
        settings = Settings(_env_file=None)
        subgraph = build_revision_subgraph(settings)
        # 子图编译时未传 checkpointer, 属性应为 None
        assert subgraph.checkpointer is None

    def test_build_revision_subgraph_default_settings(self) -> None:
        """验证 settings=None 时用 get_settings() 默认配置 (不报错)."""
        # 用 patch 控制默认配置避免依赖 .env
        default_settings = Settings(_env_file=None)
        with patch(
            "src.graph.multi_agent_builder.get_settings",
            return_value=default_settings,
        ):
            subgraph = build_revision_subgraph()
            assert "reviewer" in subgraph.nodes
            assert "reviser" in subgraph.nodes


# ========== build_multi_agent_graph ==========


class TestBuildMultiAgentGraph:
    """build_multi_agent_graph 主图结构测试."""

    @pytest.mark.asyncio
    async def test_build_researcher_graph_linear_flow(self) -> None:
        """验证 agent_creator→researcher→writer→fact_checker 线性流.

        完整图结构:
            START → agent_creator → researcher → writer → fact_checker
            fact_checker → (accept → revision | revise → writer)
            revision → visualizer → publisher → END
        """
        settings = Settings(_env_file=None)
        # use_checkpointer=False 跳过 Postgres 依赖
        graph = await build_multi_agent_graph(settings, use_checkpointer=False)

        # 1. 节点验证
        node_names = set(graph.nodes.keys())
        assert "agent_creator" in node_names
        assert "researcher" in node_names
        assert "writer" in node_names
        assert "fact_checker" in node_names
        assert "revision" in node_names  # 子图作为节点
        assert "visualizer" in node_names
        assert "publisher" in node_names

        # 2. 边验证
        edges = _edge_pairs(graph)
        # __start__ → agent_creator (入口)
        assert ("__start__", "agent_creator") in edges
        # 线性边: agent_creator → researcher → writer → fact_checker
        assert ("agent_creator", "researcher") in edges
        assert ("researcher", "writer") in edges
        assert ("writer", "fact_checker") in edges
        # fact_checker 条件边: accept → revision, revise → writer
        assert ("fact_checker", "revision") in edges
        assert ("fact_checker", "writer") in edges
        # revision → visualizer → publisher
        assert ("revision", "visualizer") in edges
        assert ("visualizer", "publisher") in edges
        # publisher → __end__
        assert ("publisher", "__end__") in edges

    @pytest.mark.asyncio
    async def test_build_researcher_graph_max_iterations_guard(self) -> None:
        """验证 graph_max_iterations 守卫正确传递给 create_fact_check_guard.

        AGENTS.md 第 5 章: max_iterations 为硬上限, 不可软超时.
        iteration_count 由 fact_checker 节点累加, 达上限强制 accept.
        """
        settings = Settings(_env_file=None, graph_max_iterations=7)
        with patch(
            "src.graph.multi_agent_builder.create_fact_check_guard"
        ) as mock_guard:
            mock_guard.return_value = lambda state: "accept"
            await build_multi_agent_graph(settings, use_checkpointer=False)
            mock_guard.assert_called_once_with(7)

    @pytest.mark.asyncio
    async def test_build_researcher_graph_max_revisions_guard(self) -> None:
        """验证 max_revisions 守卫正确传递给子图 create_revision_guard."""
        settings = Settings(_env_file=None, max_revisions=4)
        with patch(
            "src.graph.multi_agent_builder.create_revision_guard"
        ) as mock_guard:
            mock_guard.return_value = lambda state: "accept"
            await build_multi_agent_graph(settings, use_checkpointer=False)
            mock_guard.assert_called_once_with(4)

    @pytest.mark.asyncio
    async def test_build_researcher_graph_no_checkpointer_when_disabled(
        self,
    ) -> None:
        """验证 use_checkpointer=False 时图不挂 checkpointer."""
        settings = Settings(_env_file=None)
        graph = await build_multi_agent_graph(settings, use_checkpointer=False)
        assert graph.checkpointer is None

    @pytest.mark.asyncio
    async def test_build_researcher_graph_attaches_checkpointer(self) -> None:
        """验证 use_checkpointer=True 时挂载 checkpointer (mock get_checkpointer).

        用 langgraph MemorySaver 作为真实 BaseCheckpointSaver 实例
        (graph.compile 会校验 checkpointer 类型, MagicMock 不通过).
        """
        from langgraph.checkpoint.memory import MemorySaver

        settings = Settings(_env_file=None)
        mock_checkpointer = MemorySaver()
        with patch(
            "src.memory.checkpointer.get_checkpointer",
            new_callable=AsyncMock,
            return_value=mock_checkpointer,
        ):
            graph = await build_multi_agent_graph(
                settings, use_checkpointer=True
            )
            assert graph.checkpointer is mock_checkpointer

    @pytest.mark.asyncio
    async def test_build_researcher_graph_default_settings(self) -> None:
        """验证 settings=None 时用 get_settings() 默认配置 (不报错)."""
        default_settings = Settings(_env_file=None)
        with patch(
            "src.graph.multi_agent_builder.get_settings",
            return_value=default_settings,
        ):
            graph = await build_multi_agent_graph(use_checkpointer=False)
            assert "agent_creator" in graph.nodes
            assert "publisher" in graph.nodes

    @pytest.mark.asyncio
    async def test_build_researcher_graph_entry_point_is_agent_creator(
        self,
    ) -> None:
        """验证入口节点为 agent_creator (START → agent_creator)."""
        settings = Settings(_env_file=None)
        graph = await build_multi_agent_graph(settings, use_checkpointer=False)
        edges = _edge_pairs(graph)
        assert ("__start__", "agent_creator") in edges

    @pytest.mark.asyncio
    async def test_build_researcher_graph_publisher_to_end(self) -> None:
        """验证 publisher → END 为终止节点 (有终有始)."""
        settings = Settings(_env_file=None)
        graph = await build_multi_agent_graph(settings, use_checkpointer=False)
        edges = _edge_pairs(graph)
        assert ("publisher", "__end__") in edges
