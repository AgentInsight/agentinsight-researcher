"""单元测试: Chat Graph 构建器.

验证 src/graph/chat_builder.py:
- build_chat_graph: 单节点 chat 图 (START → chat → END)
- use_checkpointer=True 时挂载 PostgresSaver (mock)
- use_checkpointer=False 时不挂 checkpointer

图结构 (P2-Future-03):
    START → chat → END

集成 (routes.py 检测追问 vs 新研究):
- has_report 且无 report_type → 走 chat graph (追问模式)
- 否则 → 走 researcher graph (新研究)

复用同一 PostgresSaver (同 thread_id 隔离), 支持多会话并发.
AGENTS.md 第 5/6 章: 生产 StateGraph 必须挂 PostgresSaver, thread_id 从请求上下文注入.
AGENTS.md 第 13 章: 单元测试不依赖外部服务 (Postgres 全部 mock).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.config.settings import Settings
from src.graph.chat_builder import build_chat_graph

pytestmark = pytest.mark.unit


def _edge_pairs(compiled: Any) -> list[tuple[str, str]]:
    """提取编译图的边列表 (source, target).

    使用 compiled.get_graph().edges 获取 Graph 对象的边,
    每条边有 source/target 属性 (含 __start__/__end__ 伪节点).
    """
    graph = compiled.get_graph()
    return [(e.source, e.target) for e in graph.edges]


# ========== build_chat_graph 单节点结构 ==========


class TestBuildChatGraph:
    """build_chat_graph 单节点图结构测试."""

    @pytest.mark.asyncio
    async def test_build_chat_graph_single_node(self) -> None:
        """验证 START → chat → END 单节点结构.

        图结构 (P2-Future-03):
            START → chat → END
        """
        settings = Settings(_env_file=None)
        # use_checkpointer=False 跳过 Postgres 依赖
        graph = await build_chat_graph(settings, use_checkpointer=False)

        # 1. 节点验证: chat 节点存在 (不含 __start__/__end__ 伪节点)
        node_names = set(graph.nodes.keys())
        assert "chat" in node_names

        # 2. 边验证: __start__ → chat → __end__
        edges = _edge_pairs(graph)
        assert ("__start__", "chat") in edges
        assert ("chat", "__end__") in edges

        # 3. 仅一个业务节点 (chat), 不含其他节点
        business_nodes = node_names - {"__start__"}
        assert len(business_nodes) == 1
        assert business_nodes == {"chat"}

    @pytest.mark.asyncio
    async def test_build_chat_graph_checkpointer_attached(self) -> None:
        """验证 use_checkpointer=True 时挂载 PostgresSaver (mock).

        AGENTS.md 第 5/6 章: 生产 StateGraph 必须挂 PostgresSaver (同 thread_id 隔离).
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
        ) as mock_get:
            graph = await build_chat_graph(settings, use_checkpointer=True)
            # 验证 get_checkpointer 被调用 (生产模式挂载 PostgresSaver)
            mock_get.assert_awaited_once_with(settings)
            # 验证 checkpointer 已挂载到编译图
            assert graph.checkpointer is mock_checkpointer

    @pytest.mark.asyncio
    async def test_build_chat_graph_no_checkpointer_when_disabled(self) -> None:
        """验证 use_checkpointer=False 时图不挂 checkpointer."""
        settings = Settings(_env_file=None)
        graph = await build_chat_graph(settings, use_checkpointer=False)
        assert graph.checkpointer is None

    @pytest.mark.asyncio
    async def test_build_chat_graph_default_settings(self) -> None:
        """验证 settings=None 时用 get_settings() 默认配置 (不报错)."""
        default_settings = Settings(_env_file=None)
        with patch(
            "src.graph.chat_builder.get_settings",
            return_value=default_settings,
        ):
            graph = await build_chat_graph(use_checkpointer=False)
            assert "chat" in graph.nodes

    @pytest.mark.asyncio
    async def test_build_chat_graph_entry_and_exit(self) -> None:
        """验证 chat 图入口为 chat 节点, 出口为 END.

        START → chat → END (无分支, 无循环).
        """
        settings = Settings(_env_file=None)
        graph = await build_chat_graph(settings, use_checkpointer=False)
        edges = _edge_pairs(graph)

        # 入口: __start__ → chat
        start_edges = [e for e in edges if e[0] == "__start__"]
        assert len(start_edges) == 1
        assert start_edges[0] == ("__start__", "chat")

        # 出口: chat → __end__
        end_edges = [e for e in edges if e[1] == "__end__"]
        assert len(end_edges) == 1
        assert end_edges[0] == ("chat", "__end__")

        # 总边数 = 2 (仅入口 + 出口, 无其他边)
        assert len(edges) == 2

    @pytest.mark.asyncio
    async def test_build_chat_graph_checkpointer_not_called_when_disabled(
        self,
    ) -> None:
        """验证 use_checkpointer=False 时不调用 get_checkpointer."""
        settings = Settings(_env_file=None)
        with patch(
            "src.memory.checkpointer.get_checkpointer",
            new_callable=AsyncMock,
        ) as mock_get:
            await build_chat_graph(settings, use_checkpointer=False)
            mock_get.assert_not_called()
