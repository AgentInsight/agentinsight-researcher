"""单元测试: 通用熔断器.

验证 src/common/circuit_breaker.py 的 CircuitBreaker (CLOSED/OPEN/HALF_OPEN 三态):
- CLOSED: 初始状态, 失败计数 < threshold, 请求正常通过
- OPEN: 失败计数 >= threshold 且未过恢复时间, is_open()=True (快速失败)
- HALF_OPEN: 失败计数 >= threshold 但已过恢复时间, 允许试探请求
- record_success: 重置计数器 + 关闭熔断器 (相当于 reset)
- record_failure: 累计计数, 达阈值则开启熔断器

状态转换图:
    CLOSED --N次失败--> OPEN --超时--> HALF_OPEN
        ^                                  |      |
        |______成功______|      |__失败__|
                                    v
                                   OPEN

单元测试不依赖外部服务. 使用 time.sleep 等待恢复超时 (参考 test_tei_circuit_breaker.py 模式).
"""

from __future__ import annotations

import time

import pytest

from src.common.circuit_breaker import CircuitBreaker, CircuitState

pytestmark = pytest.mark.unit


# ========== CircuitState 枚举 ==========


def test_circuit_state_enum_values() -> None:
    """CircuitState 应有 CLOSED/OPEN/HALF_OPEN 三态."""
    assert CircuitState.CLOSED.value == "closed"
    assert CircuitState.OPEN.value == "open"
    assert CircuitState.HALF_OPEN.value == "half_open"


# ========== CLOSED 状态 (初始/正常) ==========


def test_initial_state_is_closed() -> None:
    """初始状态应为 CLOSED."""
    cb = CircuitBreaker()
    assert cb._state == CircuitState.CLOSED


def test_initial_is_open_returns_false() -> None:
    """初始状态 is_open() 应返回 False."""
    cb = CircuitBreaker()
    assert cb.is_open() is False


def test_initial_failure_count_is_zero() -> None:
    """初始失败计数应为 0."""
    cb = CircuitBreaker()
    assert cb._failure_count == 0


def test_below_threshold_not_open() -> None:
    """失败计数 < threshold → 不开启熔断 (保持 CLOSED)."""
    cb = CircuitBreaker(failure_threshold=5)
    for _ in range(4):  # 4 < 5
        cb.record_failure()
    assert cb.is_open() is False
    assert cb._state == CircuitState.CLOSED


# ========== OPEN 状态 (熔断) ==========


def test_at_threshold_opens() -> None:
    """失败计数 >= threshold → 开启熔断 (OPEN)."""
    cb = CircuitBreaker(failure_threshold=3)
    for _ in range(3):
        cb.record_failure()
    assert cb.is_open() is True
    assert cb._state == CircuitState.OPEN


def test_open_stays_open_before_recovery_timeout() -> None:
    """OPEN 状态 + 未过 recovery_timeout → is_open() 返回 True."""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=60.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open() is True
    # 未过恢复时间 → 仍开启
    assert cb.is_open() is True
    assert cb._state == CircuitState.OPEN


# ========== HALF_OPEN 状态 (半开试探) ==========


def test_half_open_after_recovery_timeout() -> None:
    """OPEN + 过 recovery_timeout → 转换为 HALF_OPEN, is_open()=False."""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open() is True

    time.sleep(0.15)  # 等待恢复时间

    # 超时后 is_open() 触发 HALF_OPEN 转换, 返回 False (允许试探)
    assert cb.is_open() is False
    assert cb._state == CircuitState.HALF_OPEN


def test_half_open_failure_count_not_reset() -> None:
    """HALF_OPEN 转换时 → 失败计数不清零 (试探成功才清零)."""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.15)

    cb.is_open()  # 触发 HALF_OPEN
    assert cb._failure_count == 2  # 未清零


def test_half_open_success_closes() -> None:
    """HALF_OPEN + record_success → 转换为 CLOSED, 计数清零."""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.15)

    cb.is_open()  # 触发 HALF_OPEN
    cb.record_success()  # 试探成功

    assert cb._state == CircuitState.CLOSED
    assert cb._failure_count == 0
    assert cb.is_open() is False


def test_half_open_failure_reopens() -> None:
    """HALF_OPEN + record_failure → 重新转换为 OPEN."""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.15)

    cb.is_open()  # 触发 HALF_OPEN
    cb.record_failure()  # 试探失败

    assert cb._state == CircuitState.OPEN
    assert cb.is_open() is True


# ========== record_success 行为 (重置) ==========


def test_record_success_resets_failure_count() -> None:
    """record_success 清零失败计数 (即使未达阈值)."""
    cb = CircuitBreaker(failure_threshold=5)
    cb.record_failure()
    cb.record_failure()
    assert cb._failure_count == 2

    cb.record_success()
    assert cb._failure_count == 0


def test_record_success_resets_state_to_closed() -> None:
    """record_success 重置状态为 CLOSED."""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    assert cb._state == CircuitState.OPEN

    time.sleep(0.15)
    cb.is_open()  # HALF_OPEN
    cb.record_success()

    assert cb._state == CircuitState.CLOSED


def test_record_success_when_already_closed_is_noop() -> None:
    """record_success 在 CLOSED 状态下无副作用 (幂等)."""
    cb = CircuitBreaker()
    cb.record_success()
    cb.record_success()
    assert cb._failure_count == 0
    assert cb._state == CircuitState.CLOSED


# ========== record_failure 行为 ==========


def test_record_failure_increments_count() -> None:
    """record_failure 每次失败计数 +1."""
    cb = CircuitBreaker(failure_threshold=10)
    cb.record_failure()
    assert cb._failure_count == 1
    cb.record_failure()
    assert cb._failure_count == 2


def test_record_failure_updates_last_failure_time() -> None:
    """record_failure 更新 _last_failure_time."""
    cb = CircuitBreaker(failure_threshold=10)
    old_time = cb._last_failure_time
    time.sleep(0.01)
    cb.record_failure()
    assert cb._last_failure_time > old_time


# ========== 自定义参数 ==========


def test_custom_failure_threshold() -> None:
    """自定义 failure_threshold=10 → 10 次失败才开启."""
    cb = CircuitBreaker(failure_threshold=10)
    for _ in range(9):
        cb.record_failure()
    assert cb.is_open() is False  # 9 < 10

    cb.record_failure()  # 10 次
    assert cb.is_open() is True


def test_custom_recovery_timeout() -> None:
    """自定义 recovery_timeout=0.05s → 0.05s 后半开."""
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05)
    cb.record_failure()
    assert cb.is_open() is True

    time.sleep(0.06)
    assert cb.is_open() is False  # HALF_OPEN


# ========== 完整状态转换循环 ==========


def test_full_state_transition_cycle() -> None:
    """完整状态转换: CLOSED → OPEN → HALF_OPEN → CLOSED (成功恢复)."""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)

    # CLOSED → OPEN (2 次失败)
    cb.record_failure()
    cb.record_failure()
    assert cb._state == CircuitState.OPEN

    # OPEN → HALF_OPEN (超时)
    time.sleep(0.15)
    cb.is_open()
    assert cb._state == CircuitState.HALF_OPEN

    # HALF_OPEN → CLOSED (试探成功)
    cb.record_success()
    assert cb._state == CircuitState.CLOSED
    assert cb._failure_count == 0
