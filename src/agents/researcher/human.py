"""HumanAgent 人在回路节点.

AGENTS.md 第 5 章: LangGraph StateGraph 唯一编排, 节点为纯函数.
AGENTS.md 第 10 章: 用 trace_chain 包裹 (异步上下文管理器).

流程:
    1. 通过 WebSocket 推送研究计划给前端 (human_feedback_request 消息)
    2. 阻塞等待用户反馈 (asyncio.Future, 带超时, 不阻塞线程)
    3. 返回 human_feedback (str|None) + revisions_count (int)

图编排 (multi_agent_builder.py, human_review_enabled=True 时启用):
    START → agent_creator → human → (accept → supervisor | revise → agent_creator)
    human 条件边: accept(human_feedback is None or revisions_count >= MAX) → supervisor
                  revise → agent_creator
"""

from __future__ import annotations

import logging
from typing import Any

from src.api.feedback_queue import get_feedback_queue
from src.api.websocket import WS_MSG_HUMAN_FEEDBACK_REQUEST, get_websocket_manager
from src.config.settings import Settings, get_settings
from src.graph.state import ResearcherState
from src.observability.tracing import trace_chain

logger = logging.getLogger(__name__)

# 表示接受/通过的关键词 (不区分大小写), 命中则视为审核通过
_ACCEPT_KEYWORDS: frozenset[str] = frozenset(
    {"", "approve", "accept", "ok", "lgtm", "通过", "接受", "同意", "没问题"}
)


class HumanAgent:
    """人在回路 Agent, 审核研究计划/大纲.

    AGENTS.md 第 5 章: 节点为纯函数, 禁止原地修改入参 State, 返回 delta dict.
    AGENTS.md 第 10 章: 用 trace_chain 包裹 (异步上下文管理器).
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def review_plan(self, state: ResearcherState) -> dict[str, Any]:
        """等待用户审核研究计划/大纲.

        1. 通过 WebSocket 推送计划给前端 (human_feedback_request 消息)
        2. 阻塞等待用户反馈 (asyncio.Future, 带超时, 默认 300s)
        3. 返回 human_feedback (str|None) + revisions_count (int)

        Returns:
            delta dict (由 reducer 合并, AGENTS.md 第 5 章):
                - human_feedback: str | None (None 表示接受/通过)
                - revisions_count: int (本次要求修订则 +1, 接受则 +0;
                  Annotated[int, operator.add] 累加)
        """
        session_id = state.get("session_id", "")
        user_id = state.get("user_id", "")
        revisions = int(state.get("revisions_count", 0))
        timeout = float(self.settings.human_review_timeout)
        max_revisions = self.settings.max_plan_revisions

        plan = self._build_plan(state)

        async with trace_chain(
            name="human-review",
            input={
                "session_id": session_id,
                "revisions_count": revisions,
                "max_revisions": max_revisions,
            },
            session_id=session_id,
            user_id=user_id,
        ):
            # 推送审核请求到前端 (WebSocket)
            manager = get_websocket_manager()
            sent = await manager.send_message(
                session_id,
                {
                    "type": WS_MSG_HUMAN_FEEDBACK_REQUEST,
                    "session_id": session_id,
                    "plan": plan,
                    "revisions_count": revisions,
                    "max_revisions": max_revisions,
                    "timeout_seconds": int(timeout),
                },
            )

            if not sent:
                # WebSocket 未连接: 自动通过 (不阻断研究流程)
                logger.warning("WebSocket 未连接, human review 自动通过 session=%s", session_id)
                return {"human_feedback": None, "revisions_count": 0}

            # 等待用户反馈 (asyncio.Future, 不阻塞线程)
            feedback = await get_feedback_queue().wait_feedback(session_id, timeout_seconds=timeout)

            if feedback is None:
                # 超时: 自动通过
                logger.warning("Human review 超时 (%ss), 自动通过 session=%s", timeout, session_id)
                return {"human_feedback": None, "revisions_count": 0}

            # 接受关键词 → 通过
            stripped = feedback.strip().lower()
            if stripped in _ACCEPT_KEYWORDS:
                logger.info("Human review 通过 session=%s", session_id)
                return {"human_feedback": None, "revisions_count": 0}

            # 有反馈内容 → 要求修订
            logger.info(
                "Human review 要求修订 session=%s revisions=%d feedback=%s",
                session_id,
                revisions + 1,
                feedback[:100],
            )
            return {"human_feedback": feedback, "revisions_count": 1}

    def _build_plan(self, state: ResearcherState) -> dict[str, Any]:
        """构建研究计划消息体 (推送给前端审核)."""
        return {
            "query": state.get("query", ""),
            "agent_role": state.get("agent_role", ""),
            "agent_role_server": state.get("agent_role_server", ""),
            "report_type": state.get("report_type", ""),
            "report_format": state.get("report_format", ""),
            "tone": state.get("tone", ""),
            "research_mode": state.get("research_mode", ""),
        }


async def human_node(
    state: ResearcherState,
    *,
    human_agent: HumanAgent,
) -> dict[str, Any]:
    """HumanAgent 节点 (LangGraph 节点包装).

    AGENTS.md 第 5 章: 节点为纯函数 async def node(state) -> dict.
    """
    return await human_agent.review_plan(state)
