"""单元测试: 全局反馈队列管理器 (Human-in-the-loop).

验证 src/api/feedback_queue.py:
- FeedbackQueue.put_feedback: 有待处理 Future → 解决返回 True
- FeedbackQueue.put_feedback: 无待处理 → 返回 False
- FeedbackQueue.wait_feedback: 阻塞等待 → put_feedback 后返回 feedback
- FeedbackQueue.wait_feedback: 超时返回 None
- FeedbackQueue.has_pending: True/False
- FeedbackQueue.cleanup: 清理后 has_pending=False
- get_feedback_queue 单例

AGENTS.md 第 13 章: 单元测试不依赖外部服务.
"""

from __future__ import annotations

import asyncio

from src.api.feedback_queue import FeedbackQueue, get_feedback_queue

# ========== put_feedback ==========


async def test_put_feedback_resolves_pending() -> None:
    """有待处理 Future → put_feedback 解决返回 True."""
    queue = FeedbackQueue()
    session_id = "test-put-resolve"

    async def waiter() -> str | None:
        return await queue.wait_feedback(session_id, timeout_seconds=2.0)

    task = asyncio.create_task(waiter())
    # 让 waiter 任务启动并创建 Future
    await asyncio.sleep(0.05)

    assert queue.has_pending(session_id)

    ok = queue.put_feedback(session_id, "approved")
    assert ok is True

    result = await task
    assert result == "approved"


async def test_put_feedback_no_pending_returns_false() -> None:
    """无待处理 Future → put_feedback 返回 False."""
    queue = FeedbackQueue()
    ok = queue.put_feedback("nonexistent-session", "feedback")
    assert ok is False


async def test_put_feedback_already_done_returns_false() -> None:
    """Future 已解决 → 再次 put_feedback 返回 False."""
    queue = FeedbackQueue()
    session_id = "test-put-done"

    async def waiter() -> str | None:
        return await queue.wait_feedback(session_id, timeout_seconds=2.0)

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)

    # 第一次 put 成功
    ok1 = queue.put_feedback(session_id, "first")
    assert ok1 is True
    result = await task
    assert result == "first"

    # 第二次 put 应失败 (Future 已解决并在 finally 中被 pop)
    ok2 = queue.put_feedback(session_id, "second")
    assert ok2 is False


# ========== wait_feedback ==========


async def test_wait_feedback_returns_after_put() -> None:
    """阻塞等待 → put_feedback 后返回 feedback."""
    queue = FeedbackQueue()
    session_id = "test-wait-then-put"

    async def waiter() -> str | None:
        return await queue.wait_feedback(session_id, timeout_seconds=2.0)

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)

    queue.put_feedback(session_id, "user-feedback")

    result = await task
    assert result == "user-feedback"


async def test_wait_feedback_timeout_returns_none() -> None:
    """超时返回 None."""
    queue = FeedbackQueue()
    session_id = "test-wait-timeout"

    result = await queue.wait_feedback(session_id, timeout_seconds=0.1)
    assert result is None
    # 超时后 Future 应被清理 (finally pop)
    assert not queue.has_pending(session_id)


# ========== has_pending ==========


async def test_has_pending_true_when_waiting() -> None:
    """有等待中的 Future → has_pending 返回 True."""
    queue = FeedbackQueue()
    session_id = "test-has-pending-true"

    async def waiter() -> str | None:
        return await queue.wait_feedback(session_id, timeout_seconds=2.0)

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)

    assert queue.has_pending(session_id) is True

    # 清理 (会取消 Future, waiter 返回 None)
    queue.cleanup(session_id)
    result = await task
    assert result is None
    assert not queue.has_pending(session_id)


async def test_has_pending_false_when_no_future() -> None:
    """无 Future → has_pending 返回 False."""
    queue = FeedbackQueue()
    assert queue.has_pending("nonexistent") is False


async def test_has_pending_false_after_resolved() -> None:
    """Future 已解决 → has_pending 返回 False (done)."""
    queue = FeedbackQueue()
    session_id = "test-has-pending-resolved"

    fut = queue._create_future(session_id)
    queue.put_feedback(session_id, "done")

    assert fut.done()
    # has_pending 检查 not fut.done(), 已完成应返回 False
    assert queue.has_pending(session_id) is False


# ========== cleanup ==========


async def test_cleanup_clears_pending() -> None:
    """cleanup 清理后 has_pending=False."""
    queue = FeedbackQueue()
    session_id = "test-cleanup"

    async def waiter() -> str | None:
        return await queue.wait_feedback(session_id, timeout_seconds=2.0)

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)

    assert queue.has_pending(session_id) is True

    queue.cleanup(session_id)
    assert queue.has_pending(session_id) is False

    # waiter 应被取消并返回 None
    result = await task
    assert result is None


def test_cleanup_no_existing_future_is_noop() -> None:
    """cleanup 不存在的 session_id 是空操作 (不抛异常)."""
    queue = FeedbackQueue()
    queue.cleanup("never-existed")  # 不应抛异常


# ========== get_feedback_queue 单例 ==========


def test_get_feedback_queue_singleton() -> None:
    """get_feedback_queue 两次调用返回同一实例."""
    q1 = get_feedback_queue()
    q2 = get_feedback_queue()
    assert q1 is q2


def test_get_feedback_queue_returns_feedback_queue_instance() -> None:
    """get_feedback_queue 返回 FeedbackQueue 实例."""
    queue = get_feedback_queue()
    assert isinstance(queue, FeedbackQueue)
