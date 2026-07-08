"""单元测试: TEI Embeddings 熔断器 (P0-1).

验证 src/rag/embeddings.py 的 EmbeddingsCircuitBreaker:
- CLOSED 状态: 失败计数 < threshold, 请求正常通过
- OPEN 状态: 失败计数 ≥ threshold 且未过恢复时间, is_open()=True
- HALF_OPEN 状态: 失败计数 ≥ threshold 但已过恢复时间, 允许试探请求
- record_success: 成功调用清零失败计数, 关闭熔断器
- record_failure: 失败计数 +1, 达阈值开启熔断
- EmbeddingsCircuitOpenError: 熔断开启时 embed_texts 直接抛异常

AGENTS.md 第 13 章: 单元测试不依赖外部服务.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.rag.embeddings import (
    _CIRCUIT_FAILURE_THRESHOLD,
    _CIRCUIT_RECOVERY_TIMEOUT,
    _EMBED_CACHE,
    EmbeddingsCircuitBreaker,
    EmbeddingsCircuitOpenError,
    EmbeddingsClient,
)

pytestmark = pytest.mark.unit


# ========== 常量验证 ==========


def test_circuit_failure_threshold_is_5() -> None:
    """_CIRCUIT_FAILURE_THRESHOLD 应为 5 (P0-1 默认值)."""
    assert _CIRCUIT_FAILURE_THRESHOLD == 5


def test_circuit_recovery_timeout_is_60() -> None:
    """_CIRCUIT_RECOVERY_TIMEOUT 应为 60.0s (P0-1 默认值)."""
    assert _CIRCUIT_RECOVERY_TIMEOUT == 60.0


# ========== CLOSED 状态 (正常) ==========


def test_circuit_breaker_initial_state_closed() -> None:
    """初始状态: CLOSED (失败计数 0, 未开启)."""
    cb = EmbeddingsCircuitBreaker()
    assert cb.is_open() is False
    assert cb._failure_count == 0
    assert cb._open is False


def test_circuit_breaker_below_threshold_not_open() -> None:
    """失败计数 < threshold → 不开启熔断."""
    cb = EmbeddingsCircuitBreaker(failure_threshold=5)
    for _ in range(4):  # 4 < 5
        cb.record_failure()
    assert cb.is_open() is False


# ========== OPEN 状态 (熔断) ==========


def test_circuit_breaker_opens_at_threshold() -> None:
    """失败计数 >= threshold → 开启熔断 (OPEN)."""
    cb = EmbeddingsCircuitBreaker(failure_threshold=3)
    for _ in range(3):
        cb.record_failure()
    assert cb.is_open() is True
    assert cb._open is True


def test_circuit_breaker_stays_open_before_recovery_timeout() -> None:
    """熔断开启后, 未过 recovery_timeout → 保持 OPEN."""
    cb = EmbeddingsCircuitBreaker(failure_threshold=2, recovery_timeout=60.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open() is True

    # 模拟未过恢复时间 (不修改 _last_failure_time)
    assert cb.is_open() is True  # 仍开启


# ========== HALF_OPEN 状态 (半开试探) ==========


def test_circuit_breaker_half_open_after_recovery_timeout() -> None:
    """熔断开启后, 过 recovery_timeout → 半开 (允许试探请求)."""
    cb = EmbeddingsCircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open() is True

    # 等待恢复时间
    time.sleep(0.15)

    # 半开: is_open() 返回 False (允许试探)
    assert cb.is_open() is False
    # 但失败计数未清零 (试探成功才清零)
    assert cb._failure_count == 2


def test_circuit_breaker_half_open_success_closes() -> None:
    """半开试探成功 → record_success 清零失败计数, 关闭熔断."""
    cb = EmbeddingsCircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.15)

    cb.is_open()  # 触发半开
    cb.record_success()  # 试探成功

    assert cb._failure_count == 0
    assert cb._open is False
    assert cb.is_open() is False


def test_circuit_breaker_half_open_failure_reopens() -> None:
    """半开试探失败 → record_failure 重新开启熔断."""
    cb = EmbeddingsCircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.15)

    cb.is_open()  # 触发半开
    cb.record_failure()  # 试探失败

    # 重新开启 (failure_count 已 >= threshold)
    assert cb._open is True
    assert cb.is_open() is True


# ========== record_success 行为 ==========


def test_record_success_resets_failure_count() -> None:
    """record_success 清零失败计数 (即使未达阈值)."""
    cb = EmbeddingsCircuitBreaker(failure_threshold=5)
    cb.record_failure()
    cb.record_failure()
    assert cb._failure_count == 2

    cb.record_success()
    assert cb._failure_count == 0
    assert cb._open is False


def test_record_success_when_already_clean_no_op() -> None:
    """record_success 在已清零状态下无副作用 (幂等)."""
    cb = EmbeddingsCircuitBreaker()
    cb.record_success()
    cb.record_success()
    assert cb._failure_count == 0
    assert cb._open is False


# ========== record_failure 行为 ==========


def test_record_failure_increments_count() -> None:
    """record_failure 每次失败计数 +1."""
    cb = EmbeddingsCircuitBreaker(failure_threshold=10)
    cb.record_failure()
    assert cb._failure_count == 1
    cb.record_failure()
    assert cb._failure_count == 2


def test_record_failure_updates_last_failure_time() -> None:
    """record_failure 更新 _last_failure_time."""
    cb = EmbeddingsCircuitBreaker(failure_threshold=10)
    old_time = cb._last_failure_time
    time.sleep(0.01)
    cb.record_failure()
    assert cb._last_failure_time > old_time


def test_record_failure_does_not_reopen_after_close() -> None:
    """已开启后再次 record_failure 不重复打日志 (not self._open 守卫)."""
    cb = EmbeddingsCircuitBreaker(failure_threshold=2)
    cb.record_failure()
    cb.record_failure()
    assert cb._open is True

    # 第三次失败: 不应重复开启 (not self._open 守卫)
    cb.record_failure()
    assert cb._failure_count == 3
    assert cb._open is True  # 仍开启, 未重复操作


# ========== EmbeddingsClient 集成 ==========


@pytest.mark.asyncio
async def test_embed_texts_raises_when_circuit_open() -> None:
    """熔断开启时, embed_texts 直接抛 EmbeddingsCircuitOpenError."""
    settings = Settings(_env_file=None)
    client = EmbeddingsClient(settings=settings)
    # 强制开启熔断
    client._circuit_breaker._failure_count = _CIRCUIT_FAILURE_THRESHOLD
    client._circuit_breaker._open = True
    client._circuit_breaker._last_failure_time = time.time()  # 刚开启, 未过恢复时间

    with pytest.raises(EmbeddingsCircuitOpenError):
        await client.embed_texts(["test text"])


@pytest.mark.asyncio
async def test_embed_texts_records_success_on_ok_response() -> None:
    """TEI 返回 200 → record_success 清零失败计数."""
    # 清除模块级缓存, 避免命中缓存跳过 TEI 调用 (record_success 不触发)
    _EMBED_CACHE.clear()

    settings = Settings(_env_file=None)
    client = EmbeddingsClient(settings=settings)
    # 预设一些失败计数
    client._circuit_breaker._failure_count = 2

    # mock httpx 返回 200
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [[0.1] * 1024]  # 单文本 1024 维

    with patch.object(client._client, "post", new=AsyncMock(return_value=mock_response)):
        await client.embed_texts(["test"])

    # 成功 → 失败计数清零
    assert client._circuit_breaker._failure_count == 0
    assert client._circuit_breaker._open is False


@pytest.mark.asyncio
async def test_embed_texts_records_failure_on_5xx() -> None:
    """TEI 返回 5xx → record_failure 失败计数 +1."""
    # 清除模块级缓存, 避免命中缓存跳过 TEI 调用
    _EMBED_CACHE.clear()

    settings = Settings(_env_file=None)
    client = EmbeddingsClient(settings=settings)

    # mock httpx 返回 500 — raise_for_status 必须抛 HTTPStatusError (模拟真实 httpx 5xx)
    import httpx

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "Server Error",
            request=httpx.Request("POST", "http://fake/embed"),
            response=httpx.Response(500, text="Internal Server Error"),
        )
    )

    with patch.object(client._client, "post", new=AsyncMock(return_value=mock_response)):
        with pytest.raises(Exception):  # noqa: B017
            await client.embed_texts(["test"])

    # 5xx 重试后仍失败 → record_failure
    assert client._circuit_breaker._failure_count >= 1


# ========== is_circuit_open 公开接口 ==========


def test_is_circuit_open_delegates_to_breaker() -> None:
    """EmbeddingsClient.is_circuit_open() 委托给 _circuit_breaker.is_open()."""
    settings = Settings(_env_file=None)
    client = EmbeddingsClient(settings=settings)

    # 初始状态: 未开启
    assert client.is_circuit_open() is False

    # 强制开启
    client._circuit_breaker._open = True
    client._circuit_breaker._failure_count = _CIRCUIT_FAILURE_THRESHOLD
    client._circuit_breaker._last_failure_time = time.time()

    assert client.is_circuit_open() is True


# ========== 自定义阈值 ==========


def test_custom_failure_threshold() -> None:
    """自定义 failure_threshold=10 → 10 次失败才开启."""
    cb = EmbeddingsCircuitBreaker(failure_threshold=10)
    for _ in range(9):
        cb.record_failure()
    assert cb.is_open() is False  # 9 < 10

    cb.record_failure()  # 10 次
    assert cb.is_open() is True


def test_custom_recovery_timeout() -> None:
    """自定义 recovery_timeout=0.05s → 0.05s 后半开."""
    cb = EmbeddingsCircuitBreaker(failure_threshold=1, recovery_timeout=0.05)
    cb.record_failure()
    assert cb.is_open() is True

    time.sleep(0.06)
    assert cb.is_open() is False  # 半开
