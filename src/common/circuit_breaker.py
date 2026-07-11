"""通用熔断器 (复用 EmbeddingsCircuitBreaker 模式).

连续 N 次失败后熔断 T 秒, 期间快速失败不发起请求.
熔断恢复后进入 HALF_OPEN, 成功则 CLOSED, 失败则重新 OPEN.
"""

from __future__ import annotations

import logging
import time
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """熔断器状态."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """通用熔断器 (连续 N 次失败熔断 T 秒).

    用法:
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
        if cb.is_open():
            return []  # 快速失败
        try:
            result = await do_request()
            cb.record_success()
            return result
        except Exception:
            cb.record_failure()
            return []
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._failure_count: int = 0
        self._state: CircuitState = CircuitState.CLOSED
        self._last_failure_time: float = 0.0

    def is_open(self) -> bool:
        """检查熔断器是否开启 (True=快速失败)."""
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._last_failure_time >= self._recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                logger.info("熔断器进入 HALF_OPEN 状态, 尝试恢复")
                return False
            return True
        return False

    def record_success(self) -> None:
        """记录成功 (重置计数器, 关闭熔断器)."""
        if self._state == CircuitState.HALF_OPEN:
            logger.info("熔断器恢复 CLOSED (HALF_OPEN → CLOSED)")
        self._failure_count = 0
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """记录失败 (累计计数, 达到阈值则开启熔断器)."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self._failure_threshold:
            if self._state != CircuitState.OPEN:
                logger.warning(
                    "熔断器开启 (OPEN): 连续失败 %d 次, 熔断 %.0fs",
                    self._failure_count,
                    self._recovery_timeout,
                )
            self._state = CircuitState.OPEN
