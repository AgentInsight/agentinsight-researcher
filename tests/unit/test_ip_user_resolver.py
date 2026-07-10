"""单元测试: IP-based 用户身份解析 + 每日报告限额控制.

验证 src/api/ip_user_resolver.py:
- generate_user_id_from_ip: 确定性/唯一性/格式/空 IP/None/IPv6/已知值
- get_client_ip: X-Forwarded-For/X-Real-IP/直连/无 client/多级代理/空格
- _get_daily_key: 格式/北京时间 (UTC+8)
- _seconds_until_midnight: 正整数/>=60/<=86400
- check_daily_report_limit: limit<=0/Redis 不可用/Redis 异常/未超限/已超限/首次
- increment_daily_report_count: Redis 不可用/首次计数/非首次/Redis 异常

AGENTS.md 第 8 章: 无 JWT Token 时按 IP 生成确定性 UserId (SHA256, 不存储原始 IP).
AGENTS.md 第 13 章: 单元测试不依赖外部服务 (Redis 全部 mock).
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC
from datetime import datetime as real_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api import ip_user_resolver
from src.api.ip_user_resolver import (
    _get_daily_key,
    _seconds_until_midnight,
    check_daily_report_limit,
    generate_user_id_from_ip,
    get_client_ip,
    increment_daily_report_count,
)

pytestmark = pytest.mark.unit

# 已知 SHA256[:24] 哈希值 (用于已知值验证)
_HASH_127 = "ip_" + hashlib.sha256(b"127.0.0.1").hexdigest()[:24]  # ip_12ca17b49af2289436f303e0
_HASH_0000 = "ip_" + hashlib.sha256(b"0.0.0.0").hexdigest()[:24]  # ip_19e36255972107d42b8cecb7
_HASH_IPV6 = "ip_" + hashlib.sha256(b"::1").hexdigest()[:24]  # ip_eff8e7ca506627fe...


# ========== generate_user_id_from_ip ==========


def test_generate_user_id_deterministic() -> None:
    """同一 IP 多次调用 → 结果相同 (确定性)."""
    result1 = generate_user_id_from_ip("192.168.1.1")
    result2 = generate_user_id_from_ip("192.168.1.1")
    assert result1 == result2


def test_generate_user_id_unique_for_different_ips() -> None:
    """不同 IP → 不同 UserId (唯一性)."""
    assert generate_user_id_from_ip("1.2.3.4") != generate_user_id_from_ip("5.6.7.8")


def test_generate_user_id_format() -> None:
    """格式: "ip_" 前缀 + 24 位 hex."""
    result = generate_user_id_from_ip("10.0.0.1")
    assert re.fullmatch(r"ip_[0-9a-f]{24}", result)


def test_generate_user_id_empty_string_uses_default() -> None:
    """空 IP → 用 "0.0.0.0" 生成 (不抛异常)."""
    assert generate_user_id_from_ip("") == _HASH_0000


def test_generate_user_id_none_uses_default() -> None:
    """None 输入 → 用 "0.0.0.0" 生成."""
    assert generate_user_id_from_ip(None) == _HASH_0000  # type: ignore[arg-type]


def test_generate_user_id_ipv6() -> None:
    """IPv6 输入 → 正常处理."""
    result = generate_user_id_from_ip("::1")
    assert result == _HASH_IPV6
    assert result.startswith("ip_")


def test_generate_user_id_known_value_127() -> None:
    """已知值验证: generate_user_id_from_ip("127.0.0.1") == ip_12ca17b49af2289436f303e0.

    注: 任务描述中列出的期望值 ip_846488f1dc5c07b4cebe5c14 实际是 "testclient" 的哈希
    (见 test_api_middleware.py 中 TestClient 默认 host). "127.0.0.1" 的真实
    SHA256[:24] 为 12ca17b49af2289436f303e0. 生产代码正确, 此处验证真实值.
    """
    assert generate_user_id_from_ip("127.0.0.1") == _HASH_127
    assert _HASH_127 == "ip_12ca17b49af2289436f303e0"


# ========== get_client_ip ==========


class _FakeRequest:
    """伪造 Starlette Request, 支持 headers + client.host."""

    def __init__(
        self,
        headers: dict[str, str] | None = None,
        client_host: str | None = None,
    ) -> None:
        self.headers = headers or {}
        if client_host is not None:
            self.client = SimpleNamespace(host=client_host)
        else:
            self.client = None


def test_get_client_ip_from_x_forwarded_for() -> None:
    """X-Forwarded-For 存在 → 取第一个 IP."""
    req = _FakeRequest(headers={"X-Forwarded-For": "1.2.3.4"})
    assert get_client_ip(req) == "1.2.3.4"


def test_get_client_ip_from_x_real_ip() -> None:
    """无 XFF, X-Real-IP 存在 → 取 X-Real-IP."""
    req = _FakeRequest(headers={"X-Real-IP": "9.9.9.9"})
    assert get_client_ip(req) == "9.9.9.9"


def test_get_client_ip_direct_connection() -> None:
    """无 XFF/XRI, 直连 → 取 request.client.host."""
    req = _FakeRequest(client_host="192.168.1.100")
    assert get_client_ip(req) == "192.168.1.100"


def test_get_client_ip_no_client_returns_default() -> None:
    """无 XFF/XRI, request.client=None → 返回 "0.0.0.0"."""
    req = _FakeRequest(client_host=None)
    assert get_client_ip(req) == "0.0.0.0"


def test_get_client_ip_xff_multiple_ips_takes_first() -> None:
    """XFF 含多个 IP "1.2.3.4, 5.6.7.8" → 取第一个 "1.2.3.4"."""
    req = _FakeRequest(headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8"})
    assert get_client_ip(req) == "1.2.3.4"


def test_get_client_ip_xff_strips_whitespace() -> None:
    """XFF 含空格 "  1.2.3.4  " → strip 后 "1.2.3.4"."""
    req = _FakeRequest(headers={"X-Forwarded-For": "  1.2.3.4  "})
    assert get_client_ip(req) == "1.2.3.4"


# ========== _get_daily_key ==========


def test_get_daily_key_format() -> None:
    """键格式: {agent_id}:{user_id}:daily_report:{YYYY-MM-DD}."""
    key = _get_daily_key("my-agent", "my-user")
    assert re.fullmatch(r"my-agent:my-user:daily_report:\d{4}-\d{2}-\d{2}", key)


def test_get_daily_key_uses_beijing_time() -> None:
    """日期为北京时间 (UTC+8), 不是 UTC.

    mock datetime 为 UTC 2026-07-10 20:00:00 → 北京时间 2026-07-11 04:00:00.
    键中应含北京日期 2026-07-11, 不含 UTC 日期 2026-07-10.
    """
    fixed_utc = real_datetime(2026, 7, 10, 20, 0, 0, tzinfo=UTC)
    with patch.object(ip_user_resolver, "datetime") as mock_dt:
        mock_dt.now.return_value = fixed_utc
        key = _get_daily_key("agent-1", "user-1")
    assert "2026-07-11" in key  # 北京日期
    assert "2026-07-10" not in key  # 非 UTC 日期


# ========== _seconds_until_midnight ==========


def test_seconds_until_midnight_positive() -> None:
    """返回正整数."""
    assert _seconds_until_midnight() > 0


def test_seconds_until_midnight_at_least_60() -> None:
    """至少 60 秒 (下限保护)."""
    assert _seconds_until_midnight() >= 60


def test_seconds_until_midnight_at_most_86400() -> None:
    """不超过 86400 秒 (24h)."""
    assert _seconds_until_midnight() <= 86400


# ========== Redis mock 辅助 ==========


def _make_fake_redis(
    *,
    get_return: str | None = None,
    get_side_effect: Exception | None = None,
    incr_return: int = 1,
    incr_side_effect: Exception | None = None,
) -> MagicMock:
    """构造 fake async Redis 客户端 (get/incr/expire 用 AsyncMock)."""
    redis = MagicMock()
    if get_side_effect is not None:
        redis.get = AsyncMock(side_effect=get_side_effect)
    else:
        redis.get = AsyncMock(return_value=get_return)
    if incr_side_effect is not None:
        redis.incr = AsyncMock(side_effect=incr_side_effect)
    else:
        redis.incr = AsyncMock(return_value=incr_return)
    redis.expire = AsyncMock()
    return redis


def _patch_get_redis_client(
    monkeypatch: pytest.MonkeyPatch,
    fake_redis: MagicMock | None,
) -> None:
    """mock src.common.redis_client.get_redis_client 返回 fake_redis (或 None)."""
    monkeypatch.setattr(
        "src.common.redis_client.get_redis_client",
        AsyncMock(return_value=fake_redis),
    )


# ========== check_daily_report_limit ==========


@pytest.mark.asyncio
async def test_check_limit_unlimited_when_limit_le_zero() -> None:
    """limit<=0 → 不限制, 返回 (True, 0), 不触碰 Redis."""
    allowed, count = await check_daily_report_limit("u", "a", limit=0)
    assert (allowed, count) == (True, 0)


@pytest.mark.asyncio
async def test_check_limit_degrades_when_redis_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_redis_client 返回 None → 降级放行 (True, 0)."""
    _patch_get_redis_client(monkeypatch, None)
    allowed, count = await check_daily_report_limit("u", "a", limit=3)
    assert (allowed, count) == (True, 0)


@pytest.mark.asyncio
async def test_check_limit_degrades_on_redis_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis 异常 (get 抛错) → 降级放行 (True, 0)."""
    fake = _make_fake_redis(get_side_effect=ConnectionError("redis down"))
    _patch_get_redis_client(monkeypatch, fake)
    allowed, count = await check_daily_report_limit("u", "a", limit=3)
    assert (allowed, count) == (True, 0)


@pytest.mark.asyncio
async def test_check_limit_under_limit_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """未超限 (count < limit) → (True, count)."""
    fake = _make_fake_redis(get_return="1")
    _patch_get_redis_client(monkeypatch, fake)
    allowed, count = await check_daily_report_limit("u", "a", limit=3)
    assert (allowed, count) == (True, 1)


@pytest.mark.asyncio
async def test_check_limit_at_limit_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """已超限 (count >= limit) → (False, count)."""
    fake = _make_fake_redis(get_return="3")
    _patch_get_redis_client(monkeypatch, fake)
    allowed, count = await check_daily_report_limit("u", "a", limit=3)
    assert (allowed, count) == (False, 3)


@pytest.mark.asyncio
async def test_check_limit_first_time_count_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """首次 (get 返回 None → count=0) → (True, 0)."""
    fake = _make_fake_redis(get_return=None)
    _patch_get_redis_client(monkeypatch, fake)
    allowed, count = await check_daily_report_limit("u", "a", limit=3)
    assert (allowed, count) == (True, 0)


# ========== increment_daily_report_count ==========


@pytest.mark.asyncio
async def test_increment_returns_zero_when_redis_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_redis_client 返回 None → 返回 0."""
    _patch_get_redis_client(monkeypatch, None)
    result = await increment_daily_report_count("u", "a")
    assert result == 0


@pytest.mark.asyncio
async def test_increment_first_count_sets_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """首次计数 (incr 返回 1) → 设置 TTL, 返回 1."""
    fake = _make_fake_redis(incr_return=1)
    _patch_get_redis_client(monkeypatch, fake)
    result = await increment_daily_report_count("u", "a")
    assert result == 1
    fake.incr.assert_awaited_once()
    fake.expire.assert_awaited_once()
    # TTL 应为正数 (到北京时间次日 0 点的秒数, 至少 60)
    ttl = fake.expire.await_args.args[1]
    assert isinstance(ttl, int)
    assert ttl >= 60


@pytest.mark.asyncio
async def test_increment_non_first_no_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """非首次计数 (incr 返回 2+) → 不设置 TTL, 返回计数值."""
    fake = _make_fake_redis(incr_return=5)
    _patch_get_redis_client(monkeypatch, fake)
    result = await increment_daily_report_count("u", "a")
    assert result == 5
    fake.incr.assert_awaited_once()
    fake.expire.assert_not_awaited()


@pytest.mark.asyncio
async def test_increment_returns_zero_on_redis_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis 异常 (incr 抛错) → 返回 0."""
    fake = _make_fake_redis(incr_side_effect=ConnectionError("redis down"))
    _patch_get_redis_client(monkeypatch, fake)
    result = await increment_daily_report_count("u", "a")
    assert result == 0
