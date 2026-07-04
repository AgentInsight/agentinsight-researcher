"""单元测试: Postgres Checkpointer 单例配置.

验证 src/memory/checkpointer.py:
- ENV=dev 时返回 MemorySaver (不依赖 Postgres)
- get_checkpointer() 单例: 两次调用返回同一实例
- 单例快路径: 第二次调用忽略 settings 参数

AGENTS.md 第 6 章: 内存 Checkpoint 仅 ENV=dev 允许; 生产用 PostgresSaver.
AGENTS.md 第 13 章: 单元测试不依赖外部服务.
"""

from __future__ import annotations

import asyncio

import pytest
from langgraph.checkpoint.memory import MemorySaver

from src.config.settings import Settings
from src.memory import checkpointer as cp_module

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_checkpointer_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个测试前重置模块级单例和 asyncio.Lock.

    _pool_lock 在模块导入时创建, pytest-asyncio 每个测试用独立事件循环,
    故需替换为新鲜 Lock 避免跨事件循环绑定错误.
    """
    monkeypatch.setattr(cp_module, "_checkpointer_instance", None)
    monkeypatch.setattr(cp_module, "_pool_lock", asyncio.Lock())


async def test_dev_returns_memory_saver() -> None:
    """ENV=dev 时返回 MemorySaver 实例 (不依赖 Postgres)."""
    settings = Settings(env="dev", _env_file=None)
    checkpointer = await cp_module.get_checkpointer(settings)
    assert isinstance(checkpointer, MemorySaver)


async def test_singleton_returns_same_instance() -> None:
    """两次调用返回同一实例 (单例模式)."""
    settings = Settings(env="dev", _env_file=None)
    first = await cp_module.get_checkpointer(settings)
    second = await cp_module.get_checkpointer(settings)
    assert first is second


async def test_singleton_fast_path_ignores_settings() -> None:
    """第二次调用走快路径, 忽略 settings 参数直接返回已建单例."""
    settings_dev = Settings(env="dev", _env_file=None)
    first = await cp_module.get_checkpointer(settings_dev)
    # 第二次传 None, 快路径应直接返回 first (不调用 get_settings)
    second = await cp_module.get_checkpointer(None)
    assert first is second


async def test_prod_falls_back_to_memory_saver_on_pool_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ENV=prod 时连接池创建失败 → _create_postgres 内部降级返回 MemorySaver.

    降级逻辑在 _create_postgres_checkpointer 内部 try/except:
    AsyncConnectionPool.open() 失败时 catch 并返回 MemorySaver.
    """
    settings = Settings(env="prod", _env_file=None)

    # 注入伪造 psycopg_pool 模块, 使 AsyncConnectionPool.open() 抛异常
    import sys
    import types

    class _FailPool:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def open(self) -> None:
            raise RuntimeError("pool open failed")

    fake_psycopg_pool = types.ModuleType("psycopg_pool")
    fake_psycopg_pool.AsyncConnectionPool = _FailPool  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg_pool", fake_psycopg_pool)

    checkpointer = await cp_module.get_checkpointer(settings)
    assert isinstance(checkpointer, MemorySaver)
