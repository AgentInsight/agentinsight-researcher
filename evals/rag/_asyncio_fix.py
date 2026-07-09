"""Asyncio 兼容性工具 (修复 Python 3.14 + nest_asyncio 冲突).

RAGAS 的 executor.py 在模块导入时调用 nest_asyncio.apply(), 该补丁与 Python 3.14 的
asyncio.timeout() 不兼容 (RuntimeError "Timeout should be used inside a task").
本模块提供 save/restore 机制: 在 import ragas 前保存原始 asyncio 方法,
import 后恢复, 撤销 nest_asyncio 补丁.

用法:
    from evals.rag._asyncio_fix import save_original_asyncio, restore_original_asyncio

    saved = save_original_asyncio()
    from ragas.metrics import Faithfulness  # 触发 nest_asyncio.apply()
    restore_original_asyncio(saved)  # 撤销补丁
"""

from __future__ import annotations


def save_original_asyncio() -> dict:
    """保存原始 asyncio 方法 (在 import ragas 之前调用)."""
    import asyncio.events
    import asyncio.futures
    import asyncio.tasks

    return {
        "run": asyncio.run,
        "Task": asyncio.tasks.Task,
        "_CTask": getattr(asyncio.tasks, "_CTask", None),
        "Future": asyncio.futures.Future,
        "_CFuture": getattr(asyncio.futures, "_CFuture", None),
        "get_event_loop": asyncio.get_event_loop,
        "events_get_event_loop": asyncio.events.get_event_loop,
        "_get_event_loop": getattr(asyncio.events, "_get_event_loop", None),
    }


def restore_original_asyncio(saved: dict) -> None:
    """恢复原始 asyncio 方法 (在 import ragas 之后调用, 撤销 nest_asyncio 补丁)."""
    import asyncio.events
    import asyncio.futures
    import asyncio.tasks

    asyncio.run = saved["run"]
    asyncio.tasks.Task = saved["Task"]
    if saved["_CTask"] is not None:
        asyncio.tasks._CTask = saved["_CTask"]
    asyncio.futures.Future = saved["Future"]
    if saved["_CFuture"] is not None:
        asyncio.futures._CFuture = saved["_CFuture"]
    asyncio.get_event_loop = saved["get_event_loop"]
    asyncio.events.get_event_loop = saved["events_get_event_loop"]
    if saved["_get_event_loop"] is not None:
        asyncio.events._get_event_loop = saved["_get_event_loop"]
    # Clear nest_patched flag
    if hasattr(asyncio, "_nest_patched"):
        del asyncio._nest_patched
