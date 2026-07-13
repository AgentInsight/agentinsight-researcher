"""单元测试: HumanAgent 人在回路 Agent.

验证 src/agents/researcher/human.py:
- HumanAgent.__init__: 默认 settings / 显式 settings
- review_plan: WebSocket 推送 + 反馈等待 + 路由决策
- review_plan: WebSocket 未连接 → 自动通过 (不阻断)
- review_plan: 反馈超时 → 自动通过
- review_plan: 接受关键词路由 (approve/通过/""/lgtm 等)
- review_plan: 修订反馈路由 (revisions_count +1)
- _build_plan: 计划消息体构建 (含 7 字段 + 缺失字段兜底)
- human_node: 节点纯函数包装
- _ACCEPT_KEYWORDS: 接受关键词集合

节点为纯函数, 单一职责无副作用.
trace_chain span 包裹 (异步上下文管理器).
单元测试不依赖外部服务 (WebSocket/feedback_queue 全部 mock).

注: tests/unit/test_human_node_integration.py 已覆盖 human_node 接入 multi_agent_builder
的集成场景, 本文件聚焦 HumanAgent 单元行为 (与图构建解耦).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.researcher.human import (
    _ACCEPT_KEYWORDS,
    HumanAgent,
    human_node,
)
from src.api.websocket import WS_MSG_HUMAN_FEEDBACK_REQUEST
from src.config.settings import Settings

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture()
def settings() -> Settings:
    """构造 HumanAgent 测试 Settings."""
    return Settings(
        _env_file=None,
        human_review_timeout=10,  # 短超时便于测试
        max_plan_revisions=3,
    )


@pytest.fixture()
def base_state() -> dict[str, Any]:
    """基础研究状态 (含 human review 所需字段)."""
    return {
        "session_id": "test-session",
        "user_id": "test-user",
        "revisions_count": 0,
        "query": "分析新能源汽车市场",
        "agent_role": "金融分析师",
        "agent_role_server": "financial_analyst",
        "report_type": "detailed_report",
        "report_format": "markdown",
        "tone": "objective",
        "research_mode": "detailed",
    }


def _make_ws_manager(connected: bool = True) -> MagicMock:
    """构造 mock WebSocketManager."""
    manager = MagicMock()
    manager.send_message = AsyncMock(return_value=connected)
    return manager


def _make_feedback_queue(feedback: str | None = None) -> MagicMock:
    """构造 mock FeedbackQueue."""
    queue = MagicMock()
    queue.wait_feedback = AsyncMock(return_value=feedback)
    return queue


# ========== HumanAgent.__init__ ==========


class TestHumanAgentInit:
    """HumanAgent 初始化测试."""

    def test_init_with_explicit_settings(self) -> None:
        """显式传入 settings 应被使用."""
        settings = Settings(_env_file=None, human_review_timeout=99)
        agent = HumanAgent(settings=settings)
        assert agent.settings is settings
        assert agent.settings.human_review_timeout == 99

    def test_init_uses_default_settings_when_none(self) -> None:
        """无 settings 参数 → 使用 get_settings() 默认值."""
        agent = HumanAgent()
        assert agent.settings is not None
        # 应有 human_review_timeout 字段
        assert hasattr(agent.settings, "human_review_timeout")

    def test_agent_has_settings_attribute(self, settings: Settings) -> None:
        """HumanAgent 应有 settings 属性."""
        agent = HumanAgent(settings=settings)
        assert hasattr(agent, "settings")
        assert agent.settings.human_review_timeout == 10


# ========== review_plan: WebSocket 未连接 → 自动通过 ==========


class TestReviewPlanWebSocketDisconnected:
    """review_plan WebSocket 未连接场景测试."""

    @pytest.mark.asyncio
    async def test_websocket_disconnected_auto_accepts(
        self, settings: Settings, base_state: dict[str, Any]
    ) -> None:
        """WebSocket 未连接 → 返回 human_feedback=None, 不阻断研究."""
        agent = HumanAgent(settings=settings)
        with patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws:
            mock_get_ws.return_value = _make_ws_manager(connected=False)

            result = await agent.review_plan(base_state)

        assert result["human_feedback"] is None
        assert result["revisions_count"] == 0

    @pytest.mark.asyncio
    async def test_websocket_disconnected_does_not_wait_feedback(
        self, settings: Settings, base_state: dict[str, Any]
    ) -> None:
        """WebSocket 未连接 → 不应调用 feedback_queue.wait_feedback."""
        agent = HumanAgent(settings=settings)
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
        ):
            mock_get_ws.return_value = _make_ws_manager(connected=False)
            mock_queue = _make_feedback_queue()
            mock_get_queue.return_value = mock_queue

            await agent.review_plan(base_state)

        mock_queue.wait_feedback.assert_not_awaited()


# ========== review_plan: 反馈超时 → 自动通过 ==========


class TestReviewPlanTimeout:
    """review_plan 反馈超时场景测试."""

    @pytest.mark.asyncio
    async def test_timeout_auto_accepts(self, settings: Settings, base_state: dict[str, Any]) -> None:
        """反馈超时 (wait_feedback 返回 None) → 自动通过."""
        agent = HumanAgent(settings=settings)
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
        ):
            mock_get_ws.return_value = _make_ws_manager(connected=True)
            mock_get_queue.return_value = _make_feedback_queue(feedback=None)  # 超时

            result = await agent.review_plan(base_state)

        assert result["human_feedback"] is None
        assert result["revisions_count"] == 0

    @pytest.mark.asyncio
    async def test_timeout_passes_settings_timeout_to_queue(
        self, settings: Settings, base_state: dict[str, Any]
    ) -> None:
        """wait_feedback 应使用 settings.human_review_timeout."""
        agent = HumanAgent(settings=settings)
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
        ):
            mock_get_ws.return_value = _make_ws_manager(connected=True)
            mock_queue = _make_feedback_queue(feedback=None)
            mock_get_queue.return_value = mock_queue

            await agent.review_plan(base_state)

        mock_queue.wait_feedback.assert_awaited_once()
        call_kwargs = mock_queue.wait_feedback.call_args.kwargs
        assert call_kwargs["timeout_seconds"] == float(settings.human_review_timeout)


# ========== review_plan: 接受关键词路由 ==========


class TestReviewPlanAcceptKeywords:
    """review_plan 接受关键词路由测试."""

    @pytest.mark.parametrize("keyword", ["", "approve", "accept", "ok", "lgtm"])
    @pytest.mark.asyncio
    async def test_english_accept_keywords_returns_none(
        self,
        settings: Settings,
        base_state: dict[str, Any],
        keyword: str,
    ) -> None:
        """英文接受关键词 → human_feedback=None."""
        agent = HumanAgent(settings=settings)
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
        ):
            mock_get_ws.return_value = _make_ws_manager(connected=True)
            mock_get_queue.return_value = _make_feedback_queue(feedback=keyword)

            result = await agent.review_plan(base_state)

        assert result["human_feedback"] is None
        assert result["revisions_count"] == 0

    @pytest.mark.parametrize("keyword", ["通过", "接受", "同意", "没问题"])
    @pytest.mark.asyncio
    async def test_chinese_accept_keywords_returns_none(
        self,
        settings: Settings,
        base_state: dict[str, Any],
        keyword: str,
    ) -> None:
        """中文接受关键词 → human_feedback=None."""
        agent = HumanAgent(settings=settings)
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
        ):
            mock_get_ws.return_value = _make_ws_manager(connected=True)
            mock_get_queue.return_value = _make_feedback_queue(feedback=keyword)

            result = await agent.review_plan(base_state)

        assert result["human_feedback"] is None
        assert result["revisions_count"] == 0

    @pytest.mark.asyncio
    async def test_accept_keyword_case_insensitive(
        self, settings: Settings, base_state: dict[str, Any]
    ) -> None:
        """接受关键词应不区分大小写 (APPROVE → 接受)."""
        agent = HumanAgent(settings=settings)
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
        ):
            mock_get_ws.return_value = _make_ws_manager(connected=True)
            mock_get_queue.return_value = _make_feedback_queue(feedback="APPROVE")

            result = await agent.review_plan(base_state)

        assert result["human_feedback"] is None

    @pytest.mark.asyncio
    async def test_accept_keyword_strips_whitespace(
        self, settings: Settings, base_state: dict[str, Any]
    ) -> None:
        """接受关键词应 strip 前后空白 ('  approve  ' → 接受)."""
        agent = HumanAgent(settings=settings)
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
        ):
            mock_get_ws.return_value = _make_ws_manager(connected=True)
            mock_get_queue.return_value = _make_feedback_queue(feedback="  approve  ")

            result = await agent.review_plan(base_state)

        assert result["human_feedback"] is None


# ========== review_plan: 修订反馈路由 ==========


class TestReviewPlanRevision:
    """review_plan 修订反馈路由测试."""

    @pytest.mark.asyncio
    async def test_revision_feedback_returns_feedback_and_increments_count(
        self, settings: Settings, base_state: dict[str, Any]
    ) -> None:
        """非接受关键词反馈 → 返回 feedback + revisions_count=1 (累加)."""
        agent = HumanAgent(settings=settings)
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
        ):
            mock_get_ws.return_value = _make_ws_manager(connected=True)
            mock_get_queue.return_value = _make_feedback_queue(
                feedback="请增加更多数据来源"
            )

            result = await agent.review_plan(base_state)

        assert result["human_feedback"] == "请增加更多数据来源"
        assert result["revisions_count"] == 1  # Annotated[int, operator.add] 累加

    @pytest.mark.asyncio
    async def test_revision_feedback_preserved_as_is(
        self, settings: Settings, base_state: dict[str, Any]
    ) -> None:
        """修订反馈内容应原样保留 (不修改)."""
        agent = HumanAgent(settings=settings)
        long_feedback = "请修订第3章的数据分析部分, 加入更多对比图表"
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
        ):
            mock_get_ws.return_value = _make_ws_manager(connected=True)
            mock_get_queue.return_value = _make_feedback_queue(feedback=long_feedback)

            result = await agent.review_plan(base_state)

        assert result["human_feedback"] == long_feedback

    @pytest.mark.asyncio
    async def test_revision_count_independent_of_state_count(
        self, settings: Settings, base_state: dict[str, Any]
    ) -> None:
        """delta 返回 revisions_count=1 (累加语义), 与 state 中已有次数无关."""
        # state 中已有 revisions_count=2, delta 返回 1 (累加 → 3)
        base_state["revisions_count"] = 2
        agent = HumanAgent(settings=settings)
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
        ):
            mock_get_ws.return_value = _make_ws_manager(connected=True)
            mock_get_queue.return_value = _make_feedback_queue(feedback="再修订一次")

            result = await agent.review_plan(base_state)

        # delta 应为 +1 (Annotated[int, operator.add] 累加语义)
        assert result["revisions_count"] == 1


# ========== review_plan: WebSocket 推送内容 ==========


class TestReviewPlanWebSocketPush:
    """review_plan WebSocket 推送内容测试."""

    @pytest.mark.asyncio
    async def test_push_message_uses_correct_type(
        self, settings: Settings, base_state: dict[str, Any]
    ) -> None:
        """推送消息 type 应为 WS_MSG_HUMAN_FEEDBACK_REQUEST."""
        agent = HumanAgent(settings=settings)
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
        ):
            mock_ws = _make_ws_manager(connected=True)
            mock_get_ws.return_value = mock_ws
            mock_get_queue.return_value = _make_feedback_queue(feedback=None)

            await agent.review_plan(base_state)

        mock_ws.send_message.assert_awaited_once()
        pushed_msg = mock_ws.send_message.call_args.args[1]
        assert pushed_msg["type"] == WS_MSG_HUMAN_FEEDBACK_REQUEST

    @pytest.mark.asyncio
    async def test_push_message_contains_session_id(
        self, settings: Settings, base_state: dict[str, Any]
    ) -> None:
        """推送消息应含 session_id."""
        agent = HumanAgent(settings=settings)
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
        ):
            mock_ws = _make_ws_manager(connected=True)
            mock_get_ws.return_value = mock_ws
            mock_get_queue.return_value = _make_feedback_queue(feedback=None)

            await agent.review_plan(base_state)

        pushed_msg = mock_ws.send_message.call_args.args[1]
        assert pushed_msg["session_id"] == "test-session"

    @pytest.mark.asyncio
    async def test_push_message_contains_plan(
        self, settings: Settings, base_state: dict[str, Any]
    ) -> None:
        """推送消息应含 plan 字段 (含 query/agent_role 等)."""
        agent = HumanAgent(settings=settings)
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
        ):
            mock_ws = _make_ws_manager(connected=True)
            mock_get_ws.return_value = mock_ws
            mock_get_queue.return_value = _make_feedback_queue(feedback=None)

            await agent.review_plan(base_state)

        pushed_msg = mock_ws.send_message.call_args.args[1]
        assert "plan" in pushed_msg
        assert pushed_msg["plan"]["query"] == "分析新能源汽车市场"

    @pytest.mark.asyncio
    async def test_push_message_contains_revisions_and_max(
        self, settings: Settings, base_state: dict[str, Any]
    ) -> None:
        """推送消息应含 revisions_count 与 max_revisions."""
        agent = HumanAgent(settings=settings)
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
        ):
            mock_ws = _make_ws_manager(connected=True)
            mock_get_ws.return_value = mock_ws
            mock_get_queue.return_value = _make_feedback_queue(feedback=None)

            await agent.review_plan(base_state)

        pushed_msg = mock_ws.send_message.call_args.args[1]
        assert pushed_msg["revisions_count"] == 0
        assert pushed_msg["max_revisions"] == settings.max_plan_revisions

    @pytest.mark.asyncio
    async def test_push_message_contains_timeout_seconds(
        self, settings: Settings, base_state: dict[str, Any]
    ) -> None:
        """推送消息应含 timeout_seconds."""
        agent = HumanAgent(settings=settings)
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
        ):
            mock_ws = _make_ws_manager(connected=True)
            mock_get_ws.return_value = mock_ws
            mock_get_queue.return_value = _make_feedback_queue(feedback=None)

            await agent.review_plan(base_state)

        pushed_msg = mock_ws.send_message.call_args.args[1]
        assert pushed_msg["timeout_seconds"] == int(settings.human_review_timeout)


# ========== review_plan: 边界条件 ==========


class TestReviewPlanEdgeCases:
    """review_plan 边界条件测试."""

    @pytest.mark.asyncio
    async def test_missing_session_id_uses_empty_string(
        self, settings: Settings
    ) -> None:
        """state 缺 session_id → 使用空字符串 (不抛异常)."""
        agent = HumanAgent(settings=settings)
        state: dict[str, Any] = {"user_id": "u1"}
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
        ):
            mock_get_ws.return_value = _make_ws_manager(connected=False)
            mock_get_queue.return_value = _make_feedback_queue()

            result = await agent.review_plan(state)

        assert result["human_feedback"] is None
        # WebSocket 调用应使用空字符串作为 session_id
        mock_get_ws.return_value.send_message.assert_awaited_once()
        assert mock_get_ws.return_value.send_message.call_args.args[0] == ""

    @pytest.mark.asyncio
    async def test_missing_revisions_count_defaults_to_zero(
        self, settings: Settings
    ) -> None:
        """state 缺 revisions_count → 默认 0 (int(state.get('revisions_count', 0)))."""
        agent = HumanAgent(settings=settings)
        state: dict[str, Any] = {"session_id": "s1", "user_id": "u1"}
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
        ):
            mock_get_ws.return_value = _make_ws_manager(connected=False)
            mock_get_queue.return_value = _make_feedback_queue()

            result = await agent.review_plan(state)

        assert result["revisions_count"] == 0

    @pytest.mark.asyncio
    async def test_user_id_passed_to_trace_chain(
        self, settings: Settings, base_state: dict[str, Any]
    ) -> None:
        """trace_chain 应接收 user_id 参数."""
        agent = HumanAgent(settings=settings)
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
            patch("src.agents.researcher.human.trace_chain") as mock_trace_chain,
        ):
            mock_get_ws.return_value = _make_ws_manager(connected=False)
            mock_get_queue.return_value = _make_feedback_queue()
            mock_trace_chain.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_trace_chain.return_value.__aexit__ = AsyncMock(return_value=None)

            await agent.review_plan(base_state)

        mock_trace_chain.assert_called_once()
        call_kwargs = mock_trace_chain.call_args.kwargs
        assert call_kwargs["user_id"] == "test-user"
        assert call_kwargs["session_id"] == "test-session"

    @pytest.mark.asyncio
    async def test_string_revisions_count_converted_to_int(
        self, settings: Settings
    ) -> None:
        """revisions_count 为字符串时应转为 int."""
        agent = HumanAgent(settings=settings)
        state: dict[str, Any] = {
            "session_id": "s1",
            "user_id": "u1",
            "revisions_count": "2",  # 字符串
        }
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
        ):
            mock_ws = _make_ws_manager(connected=False)
            mock_get_ws.return_value = mock_ws
            mock_get_queue.return_value = _make_feedback_queue()

            await agent.review_plan(state)

        # 推送消息中 revisions_count 应为 int(2)
        pushed_msg = mock_ws.send_message.call_args.args[1]
        assert pushed_msg["revisions_count"] == 2
        assert isinstance(pushed_msg["revisions_count"], int)


# ========== _build_plan: 计划消息体构建 ==========


class TestBuildPlan:
    """_build_plan 计划消息体构建测试."""

    def test_build_plan_returns_all_seven_fields(self, settings: Settings) -> None:
        """_build_plan 应返回 7 个字段 (query/agent_role/agent_role_server/
        report_type/report_format/tone/research_mode)."""
        agent = HumanAgent(settings=settings)
        state: dict[str, Any] = {
            "query": "q",
            "agent_role": "r",
            "agent_role_server": "rs",
            "report_type": "rt",
            "report_format": "rf",
            "tone": "t",
            "research_mode": "rm",
        }
        plan = agent._build_plan(state)
        expected_keys = {
            "query",
            "agent_role",
            "agent_role_server",
            "report_type",
            "report_format",
            "tone",
            "research_mode",
        }
        assert set(plan.keys()) == expected_keys

    def test_build_plan_preserves_field_values(self, settings: Settings) -> None:
        """_build_plan 应原样保留字段值."""
        agent = HumanAgent(settings=settings)
        state: dict[str, Any] = {
            "query": "研究问题",
            "agent_role": "金融分析师",
            "agent_role_server": "financial_analyst",
            "report_type": "detailed_report",
            "report_format": "html",
            "tone": "analytical",
            "research_mode": "detailed",
        }
        plan = agent._build_plan(state)
        assert plan["query"] == "研究问题"
        assert plan["agent_role"] == "金融分析师"
        assert plan["agent_role_server"] == "financial_analyst"
        assert plan["report_type"] == "detailed_report"
        assert plan["report_format"] == "html"
        assert plan["tone"] == "analytical"
        assert plan["research_mode"] == "detailed"

    def test_build_plan_missing_fields_return_empty_string(
        self, settings: Settings
    ) -> None:
        """_build_plan 对缺失字段应返回空字符串 (不抛 KeyError)."""
        agent = HumanAgent(settings=settings)
        state: dict[str, Any] = {}  # 空 state
        plan = agent._build_plan(state)
        for key in (
            "query",
            "agent_role",
            "agent_role_server",
            "report_type",
            "report_format",
            "tone",
            "research_mode",
        ):
            assert plan[key] == ""

    def test_build_plan_partial_state(self, settings: Settings) -> None:
        """部分字段存在的 state → 存在的字段保留, 缺失的为空."""
        agent = HumanAgent(settings=settings)
        state: dict[str, Any] = {"query": "q1", "tone": "analytical"}
        plan = agent._build_plan(state)
        assert plan["query"] == "q1"
        assert plan["tone"] == "analytical"
        assert plan["agent_role"] == ""
        assert plan["report_type"] == ""


# ========== _ACCEPT_KEYWORDS: 接受关键词集合 ==========


class TestAcceptKeywords:
    """_ACCEPT_KEYWORDS 接受关键词集合测试."""

    def test_empty_string_in_keywords(self) -> None:
        """空字符串应在集合中 (前端默认提交空表示通过)."""
        assert "" in _ACCEPT_KEYWORDS

    def test_english_keywords_in_set(self) -> None:
        """英文关键词: approve/accept/ok/lgtm."""
        assert "approve" in _ACCEPT_KEYWORDS
        assert "accept" in _ACCEPT_KEYWORDS
        assert "ok" in _ACCEPT_KEYWORDS
        assert "lgtm" in _ACCEPT_KEYWORDS

    def test_chinese_keywords_in_set(self) -> None:
        """中文关键词: 通过/接受/同意/没问题."""
        assert "通过" in _ACCEPT_KEYWORDS
        assert "接受" in _ACCEPT_KEYWORDS
        assert "同意" in _ACCEPT_KEYWORDS
        assert "没问题" in _ACCEPT_KEYWORDS

    def test_keywords_is_frozenset(self) -> None:
        """_ACCEPT_KEYWORDS 应为 frozenset (不可变)."""
        assert isinstance(_ACCEPT_KEYWORDS, frozenset)

    def test_non_accept_word_not_in_keywords(self) -> None:
        """非接受词不应在集合中."""
        assert "revise" not in _ACCEPT_KEYWORDS
        assert "no" not in _ACCEPT_KEYWORDS
        assert "修改" not in _ACCEPT_KEYWORDS


# ========== human_node: 节点纯函数包装 ==========


class TestHumanNode:
    """human_node 节点包装测试."""

    @pytest.mark.asyncio
    async def test_human_node_delegates_to_agent_review_plan(
        self, settings: Settings, base_state: dict[str, Any]
    ) -> None:
        """human_node 应委托给 HumanAgent.review_plan."""
        agent = MagicMock()
        agent.review_plan = AsyncMock(
            return_value={"human_feedback": None, "revisions_count": 0}
        )

        result = await human_node(base_state, human_agent=agent)

        agent.review_plan.assert_awaited_once_with(base_state)
        assert result == {"human_feedback": None, "revisions_count": 0}

    @pytest.mark.asyncio
    async def test_human_node_passes_state_as_positional_arg(
        self, base_state: dict[str, Any]
    ) -> None:
        """human_node 应将 state 作为位置参数传给 review_plan."""
        agent = MagicMock()
        agent.review_plan = AsyncMock(return_value={"human_feedback": None, "revisions_count": 0})

        await human_node(base_state, human_agent=agent)

        call_args = agent.review_plan.call_args
        assert call_args.args[0] is base_state
        assert call_args.kwargs == {}

    @pytest.mark.asyncio
    async def test_human_node_returns_dict(self, base_state: dict[str, Any]) -> None:
        """human_node 返回值应为 dict (delta)."""
        agent = MagicMock()
        expected_delta = {"human_feedback": "请修订", "revisions_count": 1}
        agent.review_plan = AsyncMock(return_value=expected_delta)

        result = await human_node(base_state, human_agent=agent)

        assert isinstance(result, dict)
        assert result is expected_delta

    @pytest.mark.asyncio
    async def test_human_node_accepts_human_agent_kwarg_only(
        self, base_state: dict[str, Any]
    ) -> None:
        """human_node 的 human_agent 应为关键字参数 (与 LangGraph 节点签名约定一致)."""
        agent = MagicMock()
        agent.review_plan = AsyncMock(return_value={"human_feedback": None, "revisions_count": 0})

        # 应通过关键字参数传递
        await human_node(base_state, human_agent=agent)

        agent.review_plan.assert_awaited_once()


# ========== review_plan: trace_chain 包裹 ==========


class TestReviewPlanTracing:
    """review_plan trace_chain 包裹测试."""

    @pytest.mark.asyncio
    async def test_trace_chain_called_with_correct_name(
        self, settings: Settings, base_state: dict[str, Any]
    ) -> None:
        """trace_chain 应以 'human-review' 名称调用."""
        agent = HumanAgent(settings=settings)
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
            patch("src.agents.researcher.human.trace_chain") as mock_trace_chain,
        ):
            mock_get_ws.return_value = _make_ws_manager(connected=False)
            mock_get_queue.return_value = _make_feedback_queue()
            mock_trace_chain.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_trace_chain.return_value.__aexit__ = AsyncMock(return_value=None)

            await agent.review_plan(base_state)

        mock_trace_chain.assert_called_once()
        call_kwargs = mock_trace_chain.call_args.kwargs
        assert call_kwargs["name"] == "human-review"

    @pytest.mark.asyncio
    async def test_trace_chain_input_contains_revisions_info(
        self, settings: Settings, base_state: dict[str, Any]
    ) -> None:
        """trace_chain input 应含 revisions_count 与 max_revisions."""
        agent = HumanAgent(settings=settings)
        with (
            patch("src.agents.researcher.human.get_websocket_manager") as mock_get_ws,
            patch("src.agents.researcher.human.get_feedback_queue") as mock_get_queue,
            patch("src.agents.researcher.human.trace_chain") as mock_trace_chain,
        ):
            mock_get_ws.return_value = _make_ws_manager(connected=False)
            mock_get_queue.return_value = _make_feedback_queue()
            mock_trace_chain.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_trace_chain.return_value.__aexit__ = AsyncMock(return_value=None)

            await agent.review_plan(base_state)

        call_kwargs = mock_trace_chain.call_args.kwargs
        assert "revisions_count" in call_kwargs["input"]
        assert "max_revisions" in call_kwargs["input"]
        assert call_kwargs["input"]["max_revisions"] == settings.max_plan_revisions
