"""全局反馈队列管理器 (P0-Future-03 Human-in-the-loop).

用 dict[str, asyncio.Future] 按 session_id 索引, 异步协调 HumanAgent 等待与
用户反馈提交. 这是异步协调器 (不是业务状态), 不违反 AGENTS.md 第 5 章
"节点禁止全局可变状态" 约束 (节点状态走 State/Checkpoint, 此处仅协调 await).

对标 GPTR backend/server/feedback_queue 模式:
- HumanAgent 调用 wait_feedback() 阻塞等待 (asyncio.Future, 不阻塞线程)
- /v1/feedback 端点或 WebSocket human_feedback 消息调用 put_feedback() 提交反馈
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

logger = logging.getLogger(__name__)


class FeedbackQueue:
    """按 session_id 索引的反馈等待/提交协调器.

    每个 session_id 同时只能有一个待处理的反馈 Future.
    HumanAgent 等待 → 用户提交反馈 → Future 解决 → HumanAgent 返回.
    """

    _instance: ClassVar[FeedbackQueue | None] = None

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future[str]] = {}

    def _create_future(self, session_id: str) -> asyncio.Future[str]:
        """为 session_id 创建待解决的 Future (HumanAgent 等待用).

        若已存在未完成的 Future, 先取消旧的 (避免泄漏).
        必须在运行中的事件循环内调用.
        """
        old = self._futures.get(session_id)
        if old is not None and not old.done():
            old.cancel()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._futures[session_id] = fut
        return fut

    def put_feedback(self, session_id: str, feedback: str) -> bool:
        """提交用户反馈, 解决 Future.

        返回是否成功设置 (False 表示无待处理 Future 或已被解决).
        """
        fut = self._futures.get(session_id)
        if fut is None or fut.done():
            return False
        try:
            fut.set_result(feedback)
            return True
        except asyncio.InvalidStateError:
            return False

    async def wait_feedback(self, session_id: str, *, timeout_seconds: float) -> str | None:
        """等待反馈, 超时返回 None.

        创建 Future 并等待, 超时或被取消均返回 None.
        """
        fut = self._create_future(session_id)
        try:
            return await asyncio.wait_for(fut, timeout=timeout_seconds)
        except TimeoutError:
            logger.warning("反馈等待超时 session=%s timeout=%ss", session_id, timeout_seconds)
            return None
        except asyncio.CancelledError:
            logger.warning("反馈等待被取消 session=%s", session_id)
            return None
        finally:
            self._futures.pop(session_id, None)

    def cleanup(self, session_id: str) -> None:
        """清理 session_id 的 Future (会话结束/断开时调用)."""
        fut = self._futures.pop(session_id, None)
        if fut is not None and not fut.done():
            fut.cancel()

    def has_pending(self, session_id: str) -> bool:
        """是否存在待处理的反馈 Future."""
        fut = self._futures.get(session_id)
        return fut is not None and not fut.done()


_feedback_queue: FeedbackQueue | None = None


def get_feedback_queue() -> FeedbackQueue:
    """获取全局 FeedbackQueue 单例 (异步协调器, 非业务状态)."""
    global _feedback_queue
    if _feedback_queue is None:
        _feedback_queue = FeedbackQueue()
    return _feedback_queue
