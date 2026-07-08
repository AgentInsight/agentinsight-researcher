"""单元测试: human_node 接入 multi_agent_builder (P0-Future-03 人在回路修复).

验证 src/agents/researcher/human.py + src/graph/multi_agent_builder.py 的接入:
- human_node 节点接入主图 (human_review_enabled=True 时启用)
- human_review_enabled=False 时跳过 human 节点 (agent_creator → researcher 直连)
- create_human_review_guard 路由: accept (feedback is None) | revise (有反馈)
- max_plan_revisions 守卫: 达上限强制 accept (AGENTS.md 第 5 章 max_iterations 硬上限)
- HumanAgent.review_plan: WebSocket 未连接/超时 → 自动通过 (不阻断)
- 接受关键词 ("approve"/"通过"/"" 等) → accept, 其他反馈 → revise

AGENTS.md 第 5 章: LangGraph StateGraph 唯一编排, 节点为纯函数.
AGENTS.md 第 13 章: 单元测试不依赖外部服务 (WebSocket/feedback_queue 全部 mock).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.researcher.human import _ACCEPT_KEYWORDS, HumanAgent, human_node
from src.config.settings import Settings
from src.graph.multi_agent_builder import (
    build_multi_agent_graph,
    create_human_review_guard,
)

pytestmark = pytest.mark.unit


# ========== create_human_review_guard 路由 ==========


def test_human_review_guard_accept_when_feedback_none() -> None:
    """feedback=None (用户接受) → 返回 "accept"."""
    guard = create_human_review_guard(max_plan_revisions=3)
    state: dict[str, Any] = {"human_feedback": None, "revisions_count": 0}
    assert guard(state) == "accept"


def test_human_review_guard_revise_when_feedback_present() -> None:
    """feedback 非 None (要求修订) → 返回 "revise"."""
    guard = create_human_review_guard(max_plan_revisions=3)
    state: dict[str, Any] = {"human_feedback": "请加更多数据", "revisions_count": 0}
    assert guard(state) == "revise"


def test_human_review_guard_force_accept_at_max_revisions() -> None:
    """达 max_plan_revisions 上限 → 强制 accept (max_iterations 硬上限)."""
    guard = create_human_review_guard(max_plan_revisions=3)
    state: dict[str, Any] = {
        "human_feedback": "still needs revision",
        "revisions_count": 3,  # 达上限
    }
    assert guard(state) == "accept"


def test_human_review_guard_revise_below_max_revisions() -> None:
    """revisions_count < max → 仍可 revise."""
    guard = create_human_review_guard(max_plan_revisions=3)
    state: dict[str, Any] = {
        "human_feedback": "再次修订",
        "revisions_count": 2,  # 未达上限
    }
    assert guard(state) == "revise"


def test_human_review_guard_default_max_revisions() -> None:
    """默认 max_plan_revisions=3 (与 settings 默认一致)."""
    guard = create_human_review_guard()  # 默认 3
    state: dict[str, Any] = {
        "human_feedback": "revise",
        "revisions_count": 2,
    }
    assert guard(state) == "revise"


# ========== human_node 接入主图 (build_multi_agent_graph) ==========


@pytest.mark.asyncio
async def test_build_graph_with_human_review_enabled_adds_human_node() -> None:
    """human_review_enabled=True → 主图含 human 节点 + agent_creator→human→researcher 边."""
    settings = Settings(
        _env_file=None,
        human_review_enabled=True,
        max_plan_revisions=2,
    )

    compiled = await build_multi_agent_graph(settings, use_checkpointer=False)
    graph = compiled.get_graph()
    node_names = set(graph.nodes.keys())

    # human 节点已添加
    assert "human" in node_names
    # agent_creator 与 researcher 仍存在
    assert "agent_creator" in node_names
    assert "researcher" in node_names

    # 边验证
    edges = [(e.source, e.target) for e in graph.edges]
    # agent_creator → human (人在回路接入)
    assert ("agent_creator", "human") in edges
    # human → researcher (accept 分支)
    assert ("human", "researcher") in edges
    # human → agent_creator (revise 分支, 修订循环)
    assert ("human", "agent_creator") in edges


@pytest.mark.asyncio
async def test_build_graph_with_human_review_disabled_skips_human_node() -> None:
    """human_review_enabled=False → 主图不含 human 节点, agent_creator→researcher 直连."""
    settings = Settings(
        _env_file=None,
        human_review_enabled=False,
    )

    compiled = await build_multi_agent_graph(settings, use_checkpointer=False)
    graph = compiled.get_graph()
    node_names = set(graph.nodes.keys())

    # human 节点未添加
    assert "human" not in node_names
    # agent_creator → researcher 直连
    edges = [(e.source, e.target) for e in graph.edges]
    assert ("agent_creator", "researcher") in edges
    # 不应存在 agent_creator → human 边
    assert ("agent_creator", "human") not in edges


# ========== HumanAgent.review_plan 行为 ==========


@pytest.mark.asyncio
async def test_review_plan_websocket_not_connected_auto_accept() -> None:
    """WebSocket 未连接 → 自动通过 (返回 human_feedback=None, 不阻断研究)."""
    settings = Settings(_env_file=None, human_review_timeout=1)
    agent = HumanAgent(settings=settings)
    state: dict[str, Any] = {
        "session_id": "test-session",
        "user_id": "test-user",
        "revisions_count": 0,
        "query": "test query",
    }

    with patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws:
        mock_ws = MagicMock()
        mock_ws.send_message = AsyncMock(return_value=False)  # 未连接
        mock_get_ws.return_value = mock_ws

        result = await agent.review_plan(state)

    assert result["human_feedback"] is None
    assert result["revisions_count"] == 0


@pytest.mark.asyncio
async def test_review_plan_timeout_auto_accept() -> None:
    """WebSocket 已连接但等待反馈超时 → 自动通过."""
    settings = Settings(_env_file=None, human_review_timeout=1)
    agent = HumanAgent(settings=settings)
    state: dict[str, Any] = {
        "session_id": "test-session",
        "user_id": "test-user",
        "revisions_count": 0,
        "query": "test query",
    }

    with (
        patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
        patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
    ):
        mock_ws = MagicMock()
        mock_ws.send_message = AsyncMock(return_value=True)  # 已连接
        mock_get_ws.return_value = mock_ws

        mock_queue = MagicMock()
        mock_queue.wait_feedback = AsyncMock(return_value=None)  # 超时返回 None
        mock_get_queue.return_value = mock_queue

        result = await agent.review_plan(state)

    assert result["human_feedback"] is None
    assert result["revisions_count"] == 0


@pytest.mark.asyncio
async def test_review_plan_accept_keyword_returns_none() -> None:
    """用户反馈含接受关键词 ("approve"/"通过"等) → human_feedback=None."""
    settings = Settings(_env_file=None, human_review_timeout=10)
    agent = HumanAgent(settings=settings)
    state: dict[str, Any] = {
        "session_id": "test-session",
        "user_id": "test-user",
        "revisions_count": 0,
        "query": "test query",
    }

    with (
        patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
        patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
    ):
        mock_ws = MagicMock()
        mock_ws.send_message = AsyncMock(return_value=True)
        mock_get_ws.return_value = mock_ws

        mock_queue = MagicMock()
        mock_queue.wait_feedback = AsyncMock(return_value="approve")
        mock_get_queue.return_value = mock_queue

        result = await agent.review_plan(state)

    assert result["human_feedback"] is None
    assert result["revisions_count"] == 0


@pytest.mark.asyncio
async def test_review_plan_revision_feedback_returns_revise() -> None:
    """用户反馈非接受关键词 → 要求修订 (human_feedback=反馈内容, revisions_count +1)."""
    settings = Settings(_env_file=None, human_review_timeout=10)
    agent = HumanAgent(settings=settings)
    state: dict[str, Any] = {
        "session_id": "test-session",
        "user_id": "test-user",
        "revisions_count": 1,
        "query": "test query",
    }

    with (
        patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
        patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
    ):
        mock_ws = MagicMock()
        mock_ws.send_message = AsyncMock(return_value=True)
        mock_get_ws.return_value = mock_ws

        mock_queue = MagicMock()
        mock_queue.wait_feedback = AsyncMock(return_value="请增加更多数据来源")
        mock_get_queue.return_value = mock_queue

        result = await agent.review_plan(state)

    assert result["human_feedback"] == "请增加更多数据来源"
    assert result["revisions_count"] == 1  # 累加 +1


# ========== 接受关键词集合 ==========


def test_accept_keywords_includes_empty_string() -> None:
    """空字符串视为接受 (前端默认提交空表示通过)."""
    assert "" in _ACCEPT_KEYWORDS


def test_accept_keywords_includes_common_terms() -> None:
    """常见接受关键词: approve/accept/ok/lgtm/通过/接受/同意/没问题."""
    expected = {"approve", "accept", "ok", "lgtm", "通过", "接受", "同意", "没问题"}
    assert expected.issubset(_ACCEPT_KEYWORDS)


# ========== human_node 节点包装 ==========


@pytest.mark.asyncio
async def test_human_node_delegates_to_agent() -> None:
    """human_node 是 HumanAgent.review_plan 的纯函数包装 (AGENTS.md 第 5 章)."""
    state: dict[str, Any] = {
        "session_id": "test-session",
        "user_id": "test-user",
        "revisions_count": 0,
        "query": "test query",
    }
    human_agent = MagicMock()
    human_agent.review_plan = AsyncMock(return_value={"human_feedback": None, "revisions_count": 0})

    result = await human_node(state, human_agent=human_agent)

    human_agent.review_plan.assert_awaited_once_with(state)
    assert result == {"human_feedback": None, "revisions_count": 0}


# ========== _build_plan 构建计划消息体 ==========


def test_build_plan_returns_dict_with_state_fields() -> None:
    """_build_plan 返回含 query/agent_role/report_type 等字段的 dict."""
    agent = HumanAgent(settings=Settings(_env_file=None))
    state: dict[str, Any] = {
        "query": "research query",
        "agent_role": "analyst",
        "agent_role_server": "server persona",
        "report_type": "detailed_report",
        "report_format": "markdown",
        "tone": "objective",
        "research_mode": "general",
    }

    plan = agent._build_plan(state)

    assert plan["query"] == "research query"
    assert plan["agent_role"] == "analyst"
    assert plan["agent_role_server"] == "server persona"
    assert plan["report_type"] == "detailed_report"
    assert plan["report_format"] == "markdown"
    assert plan["tone"] == "objective"
    assert plan["research_mode"] == "general"


def test_build_plan_handles_missing_state_fields() -> None:
    """_build_plan 对缺失字段返回空字符串 (不抛 KeyError)."""
    agent = HumanAgent(settings=Settings(_env_file=None))
    state: dict[str, Any] = {}  # 空 state

    plan = agent._build_plan(state)

    assert plan["query"] == ""
    assert plan["agent_role"] == ""
    assert plan["report_type"] == ""
