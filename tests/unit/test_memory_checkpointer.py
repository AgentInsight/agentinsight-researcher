"""单元测试: Postgres Checkpointer 单例配置.

验证 src/memory/checkpointer.py:
- 分支优化方案 P-Checkpointer: 统一 PostgresSaver (移除 dev/prod 分支与 MemorySaver 降级)
- get_checkpointer() 单例: 两次调用返回同一实例
- 单例快路径: 第二次调用忽略 settings 参数
- 连接池创建失败时抛出 RuntimeError (fail fast, 不再降级 MemorySaver)

AGENTS.md 第 6 章: StateGraph 必须挂 PostgresSaver.
AGENTS.md 第 13 章: 单元测试不依赖外部服务 (mock psycopg_pool/AsyncPostgresSaver).
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

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


def _install_fake_postgres(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """注入伪造的 psycopg_pool / AsyncPostgresSaver 模块, 避免依赖真实 Postgres.

    返回 fake_psycopg_pool 模块对象, 测试可进一步定制 pool 行为.
    """

    class _FakePool:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def open(self) -> None:
            return None

    class _FakeCheckpointer:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def setup(self) -> None:
            return None

    fake_psycopg_pool = types.ModuleType("psycopg_pool")
    fake_psycopg_pool.AsyncConnectionPool = _FakePool  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg_pool", fake_psycopg_pool)

    fake_psycopg_rows = types.ModuleType("psycopg.rows")
    fake_psycopg_rows.dict_row = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg.rows", fake_psycopg_rows)

    fake_pg_saver = types.ModuleType("langgraph.checkpoint.postgres.aio")
    fake_pg_saver.AsyncPostgresSaver = _FakeCheckpointer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.postgres.aio", fake_pg_saver)

    return fake_psycopg_pool


async def test_dev_uses_postgres_saver(monkeypatch: pytest.MonkeyPatch) -> None:
    """分支优化 P-Checkpointer: ENV=dev 时也使用 PostgresSaver (不再返回 MemorySaver)."""
    _install_fake_postgres(monkeypatch)
    settings = Settings(env="dev", _env_file=None)
    checkpointer = await cp_module.get_checkpointer(settings)
    # 应为 _FakeCheckpointer 实例 (即 AsyncPostgresSaver), 而非 MemorySaver
    assert checkpointer.__class__.__name__ == "_FakeCheckpointer"


async def test_prod_uses_postgres_saver(monkeypatch: pytest.MonkeyPatch) -> None:
    """ENV=prod 时使用 PostgresSaver."""
    _install_fake_postgres(monkeypatch)
    settings = Settings(env="prod", _env_file=None)
    checkpointer = await cp_module.get_checkpointer(settings)
    assert checkpointer.__class__.__name__ == "_FakeCheckpointer"


async def test_singleton_returns_same_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """两次调用返回同一实例 (单例模式)."""
    _install_fake_postgres(monkeypatch)
    settings = Settings(env="dev", _env_file=None)
    first = await cp_module.get_checkpointer(settings)
    second = await cp_module.get_checkpointer(settings)
    assert first is second


async def test_singleton_fast_path_ignores_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """第二次调用走快路径, 忽略 settings 参数直接返回已建单例."""
    _install_fake_postgres(monkeypatch)
    settings_dev = Settings(env="dev", _env_file=None)
    first = await cp_module.get_checkpointer(settings_dev)
    # 第二次传 None, 快路径应直接返回 first (不调用 get_settings)
    second = await cp_module.get_checkpointer(None)
    assert first is second


async def test_pool_failure_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """分支优化 P-Checkpointer: 连接池创建失败 → 抛出 RuntimeError (不再降级 MemorySaver).

    fail fast 策略: AsyncConnectionPool.open() 失败时 catch 并抛 RuntimeError,
    由调用方决定是否阻断启动.
    """
    settings = Settings(env="prod", _env_file=None)

    class _FailPool:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def open(self) -> None:
            raise RuntimeError("pool open failed")

    fake_psycopg_pool = types.ModuleType("psycopg_pool")
    fake_psycopg_pool.AsyncConnectionPool = _FailPool  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg_pool", fake_psycopg_pool)

    fake_psycopg_rows = types.ModuleType("psycopg.rows")
    fake_psycopg_rows.dict_row = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg.rows", fake_psycopg_rows)

    fake_pg_saver = types.ModuleType("langgraph.checkpoint.postgres.aio")

    class _UnusedCheckpointer:
        async def setup(self) -> None:
            raise AssertionError("不应到达 setup (pool.open 已失败)")

    fake_pg_saver.AsyncPostgresSaver = _UnusedCheckpointer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.postgres.aio", fake_pg_saver)

    with pytest.raises(RuntimeError, match="PostgresSaver 初始化失败"):
        await cp_module.get_checkpointer(settings)
