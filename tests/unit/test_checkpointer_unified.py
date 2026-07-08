"""单元测试: Checkpointer 统一 PostgresSaver (分支优化方案 P-Checkpointer 补充).

验证 src/memory/checkpointer.py 中 test_memory_checkpointer.py 未覆盖的分支优化点:
- 双重检查锁并发安全 (asyncio.gather 多个 get_checkpointer 并发只创建一个实例)
- 连接池 min/max 从 settings 读取 (P2-6 配置化, min_size > max_size 时钳制)
- setup() 失败路径 (区别于 pool.open 失败, 抛 RuntimeError 不降级 MemorySaver)
- _create_postgres_checkpointer 直接调用 (内部实现契约)
- 连接池 kwargs 透传 (autocommit/prepare_threshold/row_factory)

AGENTS.md 第 5/6 章: StateGraph 必须挂 PostgresSaver, 移除 dev/prod 分支与 MemorySaver 降级.
AGENTS.md 第 13 章: 单元测试不依赖外部服务 (mock psycopg_pool/AsyncPostgresSaver).
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

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


def _install_fake_postgres(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pool_class: type | None = None,
    saver_class: type | None = None,
) -> tuple[type, type]:
    """注入伪造的 psycopg_pool / AsyncPostgresSaver 模块.

    Args:
        pool_class: 自定义 pool 类 (None 用默认 _FakePool)
        saver_class: 自定义 saver 类 (None 用默认 _FakeCheckpointer)

    Returns:
        (pool_class, saver_class) 元组, 测试可进一步断言.
    """

    class _FakePool:
        """记录构造参数, 供测试断言 min/max/kwargs 透传."""

        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def open(self) -> None:
            return None

    class _FakeCheckpointer:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def setup(self) -> None:
            return None

    final_pool = pool_class or _FakePool
    final_saver = saver_class or _FakeCheckpointer

    fake_psycopg_pool = types.ModuleType("psycopg_pool")
    fake_psycopg_pool.AsyncConnectionPool = final_pool  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg_pool", fake_psycopg_pool)

    fake_psycopg_rows = types.ModuleType("psycopg.rows")
    fake_psycopg_rows.dict_row = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg.rows", fake_psycopg_rows)

    fake_pg_saver = types.ModuleType("langgraph.checkpoint.postgres.aio")
    fake_pg_saver.AsyncPostgresSaver = final_saver  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.postgres.aio", fake_pg_saver)

    return final_pool, final_saver


# ========== 双重检查锁并发安全 ==========


async def test_concurrent_get_checkpointer_creates_single_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """双重检查锁: 并发调用 get_checkpointer 只创建一个实例.

    场景: 多个协程同时首次调用 get_checkpointer, _pool_lock 保证只创建一次.
    期望: 所有协程拿到同一实例, _create_postgres_checkpointer 只被调用一次.
    """
    _install_fake_postgres(monkeypatch)
    settings = Settings(env="prod", _env_file=None)

    create_count = 0
    original_create = cp_module._create_postgres_checkpointer

    async def counting_create(s: Settings) -> Any:
        nonlocal create_count
        create_count += 1
        return await original_create(s)

    monkeypatch.setattr(cp_module, "_create_postgres_checkpointer", counting_create)

    # 10 个协程并发首次调用
    results = await asyncio.gather(*[cp_module.get_checkpointer(settings) for _ in range(10)])

    assert create_count == 1, f"并发下应只创建一次, 实际创建 {create_count} 次"
    assert all(r is results[0] for r in results), "所有并发调用应返回同一实例"


async def test_double_checked_locking_fast_path_no_lock_contention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """快路径: 单例已存在时不获取 _pool_lock, 直接返回.

    场景: 单例已建, 后续调用走快路径 (line 57-58), 不进入 async with _pool_lock.
    期望: 返回已存单例, 无锁开销.
    """
    _install_fake_postgres(monkeypatch)
    settings = Settings(env="dev", _env_file=None)

    first = await cp_module.get_checkpointer(settings)
    # 持锁期间再次调用, 快路径应直接返回不阻塞
    async with cp_module._pool_lock:
        second = await cp_module.get_checkpointer(settings)
    assert first is second


# ========== 连接池配置 (P2-6) ==========


async def test_pool_min_max_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """P2-6: 连接池 min/max 从 settings.postgres_pool_min_size/max_size 读取."""
    captured: dict[str, Any] = {}

    class _CapturingPool:
        def __init__(self, **kwargs: Any) -> None:
            captured["kwargs"] = kwargs

        async def open(self) -> None:
            return None

    _install_fake_postgres(monkeypatch, pool_class=_CapturingPool)
    settings = Settings(
        env="prod",
        postgres_pool_min_size=3,
        postgres_pool_max_size=15,
        _env_file=None,
    )

    await cp_module.get_checkpointer(settings)

    assert captured["kwargs"]["min_size"] == 3
    assert captured["kwargs"]["max_size"] == 15


async def test_pool_min_size_clamped_when_exceeds_max(monkeypatch: pytest.MonkeyPatch) -> None:
    """min_size > max_size 时钳制为 max_size (避免 AsyncConnectionPool ValueError).

    场景: settings.postgres_pool_min_size=20 > postgres_pool_max_size=5.
    期望: 实际 min_size=5, max_size=5 (min_size = max(min(20, 5), 1) = 5).
    """
    captured: dict[str, Any] = {}

    class _CapturingPool:
        def __init__(self, **kwargs: Any) -> None:
            captured["kwargs"] = kwargs

        async def open(self) -> None:
            return None

    _install_fake_postgres(monkeypatch, pool_class=_CapturingPool)
    settings = Settings(
        env="prod",
        postgres_pool_min_size=20,
        postgres_pool_max_size=5,
        _env_file=None,
    )

    await cp_module.get_checkpointer(settings)

    assert captured["kwargs"]["min_size"] == 5
    assert captured["kwargs"]["max_size"] == 5


async def test_pool_min_size_floored_to_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """min_size 至少为 1 (max(min, max), 1), 避免 0 连接池."""
    captured: dict[str, Any] = {}

    class _CapturingPool:
        def __init__(self, **kwargs: Any) -> None:
            captured["kwargs"] = kwargs

        async def open(self) -> None:
            return None

    _install_fake_postgres(monkeypatch, pool_class=_CapturingPool)
    settings = Settings(
        env="prod",
        postgres_pool_min_size=0,
        postgres_pool_max_size=0,
        _env_file=None,
    )

    await cp_module.get_checkpointer(settings)

    # max_size = max(0, 1) = 1; min_size = max(min(0, 1), 1) = 1
    assert captured["kwargs"]["max_size"] == 1
    assert captured["kwargs"]["min_size"] == 1


async def test_pool_kwargs_passthrough_autocommit_prepare_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连接池 kwargs 透传 autocommit=True / prepare_threshold=0 / row_factory=dict_row."""
    captured: dict[str, Any] = {}

    class _CapturingPool:
        def __init__(self, **kwargs: Any) -> None:
            captured["kwargs"] = kwargs

        async def open(self) -> None:
            return None

    _install_fake_postgres(monkeypatch, pool_class=_CapturingPool)
    settings = Settings(env="prod", _env_file=None)

    await cp_module.get_checkpointer(settings)

    inner = captured["kwargs"]["kwargs"]
    assert inner["autocommit"] is True
    assert inner["prepare_threshold"] == 0
    # row_factory 为注入的 dict_row (此处为 object 占位)
    assert "row_factory" in inner


async def test_pool_open_false_then_explicit_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """AsyncConnectionPool(open=False) 构造后显式调用 open() (P0-02 连接池复用模式)."""
    open_calls: list[bool] = []

    class _DeferredOpenPool:
        def __init__(self, **kwargs: Any) -> None:
            # 断言 open=False 在构造参数中
            assert kwargs.get("open") is False, "应传 open=False 延迟打开"

        async def open(self) -> None:
            open_calls.append(True)

    _install_fake_postgres(monkeypatch, pool_class=_DeferredOpenPool)
    settings = Settings(env="prod", _env_file=None)

    await cp_module.get_checkpointer(settings)

    assert len(open_calls) == 1, "应显式调用一次 open()"


# ========== setup() 失败路径 ==========


async def test_setup_failure_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """setup() 失败 (区别于 pool.open 失败) 也抛 RuntimeError, 不降级 MemorySaver.

    场景: pool.open() 成功, 但 AsyncPostgresSaver.setup() 抛异常 (如权限不足建表).
    期望: 包装为 RuntimeError, 不回退 MemorySaver.
    """

    class _OkPool:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def open(self) -> None:
            return None

    class _SetupFailSaver:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def setup(self) -> None:
            raise PermissionError("permission denied for table checkpoints")

    _install_fake_postgres(monkeypatch, pool_class=_OkPool, saver_class=_SetupFailSaver)
    settings = Settings(env="prod", _env_file=None)

    with pytest.raises(RuntimeError, match="PostgresSaver 初始化失败"):
        await cp_module.get_checkpointer(settings)


async def test_setup_failure_does_not_fallback_memory_saver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分支优化 P-Checkpointer: setup() 失败后单例保持 None, 不缓存失败的 MemorySaver.

    场景: setup() 失败抛 RuntimeError, _checkpointer_instance 应仍为 None,
    下次调用应重试 _create_postgres_checkpointer (而非返回缓存的 MemorySaver).
    """
    _install_fake_postgres(
        monkeypatch,
        saver_class=type(
            "_SetupFailSaver",
            (),
            {
                "__init__": lambda self, **kw: None,
                "setup": lambda self: (_ for _ in ()).throw(PermissionError("setup fail")),
            },
        ),
    )
    settings = Settings(env="prod", _env_file=None)

    with pytest.raises(RuntimeError):
        await cp_module.get_checkpointer(settings)

    # 单例应未被设置 (失败不缓存)
    assert cp_module._checkpointer_instance is None


# ========== _create_postgres_checkpointer 直接调用契约 ==========


async def test_create_postgres_checkpointer_returns_setup_saver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_create_postgres_checkpointer 返回已 setup() 的 saver 实例."""
    _install_fake_postgres(monkeypatch)
    settings = Settings(env="prod", _env_file=None)

    saver = await cp_module._create_postgres_checkpointer(settings)
    # 应为 _FakeCheckpointer 实例 (已 setup)
    assert saver.__class__.__name__ == "_FakeCheckpointer"


async def test_create_postgres_checkpointer_wraps_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_create_postgres_checkpointer 包装原始异常为 RuntimeError (chain 不丢)."""

    class _FailPool:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def open(self) -> None:
            raise ConnectionError("ECONNREFUSED")

    _install_fake_postgres(monkeypatch, pool_class=_FailPool)
    settings = Settings(env="prod", _env_file=None)

    with pytest.raises(RuntimeError) as exc_info:
        await cp_module._create_postgres_checkpointer(settings)

    # 原始异常应作为 __cause__ 链接 (raise ... from exc)
    assert isinstance(exc_info.value.__cause__, ConnectionError)


async def test_singleton_cached_after_success_ignores_new_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单例缓存后, 传不同 settings 也返回首实例 (首次 settings 生效)."""
    _install_fake_postgres(monkeypatch)
    settings_a = Settings(env="prod", postgres_pool_max_size=10, _env_file=None)
    first = await cp_module.get_checkpointer(settings_a)

    # 第二次传不同 pool 配置, 快路径应忽略
    settings_b = Settings(env="prod", postgres_pool_max_size=20, _env_file=None)
    second = await cp_module.get_checkpointer(settings_b)
    assert first is second


# ========== 无 MemorySaver 降级验证 ==========


async def test_no_memory_saver_import_in_module() -> None:
    """分支优化 P-Checkpointer: 模块源码不应 import MemorySaver (确认无降级路径)."""
    import inspect

    source = inspect.getsource(cp_module)
    # 不应出现 MemorySaver 的导入或使用 (注释中提及"不再降级"是允许的)
    # 检查 import 行不应含 MemorySaver
    import_lines = [
        line
        for line in source.splitlines()
        if line.strip().startswith("from ") or line.strip().startswith("import ")
    ]
    for line in import_lines:
        assert "MemorySaver" not in line, f"不应 import MemorySaver: {line}"


async def test_no_env_branch_in_create(monkeypatch: pytest.MonkeyPatch) -> None:
    """分支优化 P-Checkpointer: _create_postgres_checkpointer 不按 env 分支选 MemorySaver."""
    _install_fake_postgres(monkeypatch)
    # dev 与 prod 走同一创建路径, 均得 PostgresSaver
    dev_saver = await cp_module._create_postgres_checkpointer(Settings(env="dev", _env_file=None))
    prod_saver = await cp_module._create_postgres_checkpointer(Settings(env="prod", _env_file=None))
    assert dev_saver.__class__.__name__ == prod_saver.__class__.__name__ == "_FakeCheckpointer"
