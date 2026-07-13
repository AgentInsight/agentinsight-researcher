"""单元测试: IP-based 用户身份解析 + 每日报告限额控制 (数据库模式).

验证 src/api/ip_user_resolver.py:
- generate_user_id_from_ip: 确定性/唯一性/格式/空 IP/None/IPv6/已知值
- get_client_ip: X-Forwarded-For/X-Real-IP/直连/无 client/多级代理/空格
- _get_beijing_date: 北京时间 (UTC+8) 格式
- _get_daily_limit_from_db: 用户有限额/用户无限额/用户>系统/用户<系统 (COALESCE 优先级)
- _get_daily_usage_from_db: 有记录/无记录
- increment_daily_report_count: 首次 INSERT/后续 UPDATE/异常向上抛出
- check_daily_report_limit: limit<=0/数据库异常降级/未超限/已超限/4 种限额场景

无 JWT Token 时按 IP 生成确定性 UserId (SHA256, 不存储原始 IP).
单元测试不依赖外部服务 (Postgres 全部 mock).
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
    _get_beijing_date,
    _get_daily_limit_from_db,
    _get_daily_usage_from_db,
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
    """已知值验证: generate_user_id_from_ip("127.0.0.1") == ip_12ca17b49af2289436f303e0."""
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


# ========== _get_beijing_date ==========


def test_get_beijing_date_format() -> None:
    """返回格式 YYYY-MM-DD."""
    date_str = _get_beijing_date()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str)


def test_get_beijing_date_uses_utc_plus_8() -> None:
    """日期为北京时间 (UTC+8), 不是 UTC.

    mock datetime 为 UTC 2026-07-10 20:00:00 → 北京时间 2026-07-11 04:00:00.
    应返回北京日期 2026-07-11, 非 UTC 日期 2026-07-10.
    """
    fixed_utc = real_datetime(2026, 7, 10, 20, 0, 0, tzinfo=UTC)
    with patch.object(ip_user_resolver, "datetime") as mock_dt:
        mock_dt.now.return_value = fixed_utc
        date_str = _get_beijing_date()
    assert date_str == "2026-07-11"  # 北京日期


def test_get_beijing_date_utc_midnight_is_beijing_morning() -> None:
    """UTC 00:00:00 → 北京时间 08:00:00 (同一天)."""
    fixed_utc = real_datetime(2026, 7, 15, 0, 0, 0, tzinfo=UTC)
    with patch.object(ip_user_resolver, "datetime") as mock_dt:
        mock_dt.now.return_value = fixed_utc
        date_str = _get_beijing_date()
    assert date_str == "2026-07-15"  # UTC 0点 = 北京 8点, 同一天


def test_get_beijing_date_utc_16_is_beijing_next_day() -> None:
    """UTC 16:00:00 → 北京时间次日 00:00:00 (跨天)."""
    fixed_utc = real_datetime(2026, 7, 15, 16, 0, 0, tzinfo=UTC)
    with patch.object(ip_user_resolver, "datetime") as mock_dt:
        mock_dt.now.return_value = fixed_utc
        date_str = _get_beijing_date()
    assert date_str == "2026-07-16"  # UTC 16点 = 北京次日 0点


# ========== 数据库 mock 辅助 ==========


def _make_fake_pool(*, fetchrow_return: dict | None = None, fetchrow_side_effect=None) -> MagicMock:
    """构造 fake asyncpg 连接池.

    Args:
        fetchrow_return: fetchrow 返回的行 (字典模拟)
        fetchrow_side_effect: fetchrow 抛出的异常 (用于测试异常路径)
    """
    conn = MagicMock()
    if fetchrow_side_effect is not None:
        conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        conn.fetchrow = AsyncMock(return_value=fetchrow_return)

    pool = MagicMock()
    # pool.acquire() 是 async context manager
    acquire_cm = AsyncMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_cm)
    return pool


def _patch_get_pool(monkeypatch: pytest.MonkeyPatch, fake_pool: MagicMock) -> None:
    """mock src.memory.db_initializer.get_pool 返回 fake_pool."""
    monkeypatch.setattr(
        "src.memory.db_initializer.get_pool",
        AsyncMock(return_value=fake_pool),
    )


# ========== _get_daily_limit_from_db: 4 种限额场景 ==========


@pytest.mark.asyncio
async def test_get_limit_user_has_own_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """场景 1: 用户有专属限额 → 返回用户限额.

    report_limits 表: user_id='ip_xxx' → daily_limit=10 (用户专属)
    """
    fake_pool = _make_fake_pool(fetchrow_return={"effective_limit": 10})
    _patch_get_pool(monkeypatch, fake_pool)

    result = await _get_daily_limit_from_db("ip_user123")
    assert result == 10


@pytest.mark.asyncio
async def test_get_limit_user_has_no_limit_uses_system(monkeypatch: pytest.MonkeyPatch) -> None:
    """场景 2: 用户无专属限额 → 回退到系统默认限额.

    COALESCE(user_subquery=NULL, system_subquery=5, 0) = 5
    """
    fake_pool = _make_fake_pool(fetchrow_return={"effective_limit": 5})
    _patch_get_pool(monkeypatch, fake_pool)

    result = await _get_daily_limit_from_db("ip_user_no_limit")
    assert result == 5


@pytest.mark.asyncio
async def test_get_limit_user_greater_than_system(monkeypatch: pytest.MonkeyPatch) -> None:
    """场景 3: 用户限额 (10) > 系统限额 (5) → 用用户的 (10).

    验证 COALESCE 优先取用户子查询, 而非 MAX(user, system).
    """
    fake_pool = _make_fake_pool(fetchrow_return={"effective_limit": 10})
    _patch_get_pool(monkeypatch, fake_pool)

    result = await _get_daily_limit_from_db("ip_user_high")
    assert result == 10  # 用户的, 不是系统的 5


@pytest.mark.asyncio
async def test_get_limit_user_less_than_system(monkeypatch: pytest.MonkeyPatch) -> None:
    """场景 4: 用户限额 (3) < 系统限额 (5) → 用用户的 (3).

    验证 COALESCE 优先取用户子查询 (即使比系统小), 而非 MAX(user, system).
    这是与旧 MAX() 实现的关键差异: 用户有限额就用用户的, 不取较高者.
    """
    fake_pool = _make_fake_pool(fetchrow_return={"effective_limit": 3})
    _patch_get_pool(monkeypatch, fake_pool)

    result = await _get_daily_limit_from_db("ip_user_low")
    assert result == 3  # 用户的, 不是系统的 5


@pytest.mark.asyncio
async def test_get_limit_neither_user_nor_system_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """场景 5: 用户和系统均无限额记录 → COALESCE 返回 0 (不限制).

    COALESCE(NULL, NULL, 0) = 0
    """
    fake_pool = _make_fake_pool(fetchrow_return={"effective_limit": 0})
    _patch_get_pool(monkeypatch, fake_pool)

    result = await _get_daily_limit_from_db("ip_user_no_records")
    assert result == 0


@pytest.mark.asyncio
async def test_get_limit_db_exception_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """场景 6: 数据库异常 → fetchrow 抛错, _get_daily_limit_from_db 向上抛出.

    注: _get_daily_limit_from_db 不吞异常, 由 check_daily_report_limit 的 try/except 降级.
    """
    fake_pool = _make_fake_pool(fetchrow_side_effect=ConnectionError("postgres down"))
    _patch_get_pool(monkeypatch, fake_pool)

    with pytest.raises(ConnectionError):
        await _get_daily_limit_from_db("ip_user_err")


# ========== _get_daily_usage_from_db ==========


@pytest.mark.asyncio
async def test_get_usage_has_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """有当日使用记录 → 返回 daily_count."""
    fake_pool = _make_fake_pool(fetchrow_return={"daily_count": 3})
    _patch_get_pool(monkeypatch, fake_pool)

    result = await _get_daily_usage_from_db("ip_user123")
    assert result == 3


@pytest.mark.asyncio
async def test_get_usage_no_record_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """无当日使用记录 (fetchrow 返回 None) → 返回 0."""
    fake_pool = _make_fake_pool(fetchrow_return=None)
    _patch_get_pool(monkeypatch, fake_pool)

    result = await _get_daily_usage_from_db("ip_user_new")
    assert result == 0


# ========== increment_daily_report_count ==========


@pytest.mark.asyncio
async def test_increment_first_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """首次计数 → INSERT, 返回 1."""
    fake_pool = _make_fake_pool(fetchrow_return={"daily_count": 1})
    _patch_get_pool(monkeypatch, fake_pool)

    result = await increment_daily_report_count("ip_user_new", "agent1")
    assert result == 1


@pytest.mark.asyncio
async def test_increment_subsequent_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """后续计数 → UPDATE +1, 返回递增后的值."""
    fake_pool = _make_fake_pool(fetchrow_return={"daily_count": 5})
    _patch_get_pool(monkeypatch, fake_pool)

    result = await increment_daily_report_count("ip_user_existing", "agent1")
    assert result == 5


@pytest.mark.asyncio
async def test_increment_propagates_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """数据库异常 → 异常向上抛出 (不吞掉, 由调用方决定).

    验证: increment_daily_report_count 不再有内部 try/except 吞掉异常.
    """
    fake_pool = _make_fake_pool(fetchrow_side_effect=ConnectionError("postgres down"))
    _patch_get_pool(monkeypatch, fake_pool)

    with pytest.raises(ConnectionError):
        await increment_daily_report_count("ip_user_err", "agent1")


# ========== check_daily_report_limit: 综合 4 种限额场景 ==========


def _patch_both_db_funcs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    limit: int,
    usage: int,
) -> None:
    """同时 mock _get_daily_limit_from_db 和 _get_daily_usage_from_db.

    Args:
        limit: 有效限额 (从 report_limits 表读取)
        usage: 当日已用次数 (从 daily_report_usage 表读取)
    """
    monkeypatch.setattr(
        "src.api.ip_user_resolver._get_daily_limit_from_db",
        AsyncMock(return_value=limit),
    )
    monkeypatch.setattr(
        "src.api.ip_user_resolver._get_daily_usage_from_db",
        AsyncMock(return_value=usage),
    )


@pytest.mark.asyncio
async def test_check_limit_user_has_limit_under(monkeypatch: pytest.MonkeyPatch) -> None:
    """场景 1: 用户有限额 (10), 已用 3 (< 10) → 允许."""
    _patch_both_db_funcs(monkeypatch, limit=10, usage=3)

    allowed, count, eff_limit = await check_daily_report_limit("ip_user", "agent1")
    assert (allowed, count, eff_limit) == (True, 3, 10)


@pytest.mark.asyncio
async def test_check_limit_user_has_limit_at(monkeypatch: pytest.MonkeyPatch) -> None:
    """场景 1: 用户有限额 (10), 已用 10 (>= 10) → 拒绝."""
    _patch_both_db_funcs(monkeypatch, limit=10, usage=10)

    allowed, count, eff_limit = await check_daily_report_limit("ip_user", "agent1")
    assert (allowed, count, eff_limit) == (False, 10, 10)


@pytest.mark.asyncio
async def test_check_limit_user_has_limit_over(monkeypatch: pytest.MonkeyPatch) -> None:
    """场景 1: 用户有限额 (10), 已用 15 (> 10) → 拒绝."""
    _patch_both_db_funcs(monkeypatch, limit=10, usage=15)

    allowed, count, eff_limit = await check_daily_report_limit("ip_user", "agent1")
    assert (allowed, count, eff_limit) == (False, 15, 10)


@pytest.mark.asyncio
async def test_check_limit_user_no_limit_uses_system_under(monkeypatch: pytest.MonkeyPatch) -> None:
    """场景 2: 用户无限额, 系统限额 (5), 已用 2 (< 5) → 允许."""
    _patch_both_db_funcs(monkeypatch, limit=5, usage=2)

    allowed, count, eff_limit = await check_daily_report_limit("ip_user", "agent1")
    assert (allowed, count, eff_limit) == (True, 2, 5)


@pytest.mark.asyncio
async def test_check_limit_user_no_limit_uses_system_at(monkeypatch: pytest.MonkeyPatch) -> None:
    """场景 2: 用户无限额, 系统限额 (5), 已用 5 (>= 5) → 拒绝."""
    _patch_both_db_funcs(monkeypatch, limit=5, usage=5)

    allowed, count, eff_limit = await check_daily_report_limit("ip_user", "agent1")
    assert (allowed, count, eff_limit) == (False, 5, 5)


@pytest.mark.asyncio
async def test_check_limit_user_greater_than_system(monkeypatch: pytest.MonkeyPatch) -> None:
    """场景 3: 用户限额 (10) > 系统限额 (5), 已用 7 (< 10) → 允许.

    若用 MAX() 会得 10 (用户优先, 与 COALESCE 一致);
    若用系统 5 则会拒绝 (错误). 验证用用户的 10.
    """
    _patch_both_db_funcs(monkeypatch, limit=10, usage=7)

    allowed, count, eff_limit = await check_daily_report_limit("ip_user", "agent1")
    assert (allowed, count, eff_limit) == (True, 7, 10)


@pytest.mark.asyncio
async def test_check_limit_user_greater_than_system_at_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """场景 3: 用户限额 (10) > 系统限额 (5), 已用 10 (>= 10) → 拒绝."""
    _patch_both_db_funcs(monkeypatch, limit=10, usage=10)

    allowed, count, eff_limit = await check_daily_report_limit("ip_user", "agent1")
    assert (allowed, count, eff_limit) == (False, 10, 10)


@pytest.mark.asyncio
async def test_check_limit_user_less_than_system(monkeypatch: pytest.MonkeyPatch) -> None:
    """场景 4: 用户限额 (3) < 系统限额 (5), 已用 2 (< 3) → 允许.

    若用 MAX() 会得 5 (系统), 但用户有限额应优先用用户的 3;
    已用 2 < 3 → 允许. 验证用用户的 3.
    """
    _patch_both_db_funcs(monkeypatch, limit=3, usage=2)

    allowed, count, eff_limit = await check_daily_report_limit("ip_user", "agent1")
    assert (allowed, count, eff_limit) == (True, 2, 3)


@pytest.mark.asyncio
async def test_check_limit_user_less_than_system_at_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """场景 4: 用户限额 (3) < 系统限额 (5), 已用 3 (>= 3) → 拒绝.

    关键: 若用 MAX() 得 5, 则 3 < 5 会允许 (错误);
    用 COALESCE 用户优先得 3, 3 >= 3 拒绝 (正确).
    这是与旧 MAX() 实现的核心差异验证.
    """
    _patch_both_db_funcs(monkeypatch, limit=3, usage=3)

    allowed, count, eff_limit = await check_daily_report_limit("ip_user", "agent1")
    assert (allowed, count, eff_limit) == (False, 3, 3)


@pytest.mark.asyncio
async def test_check_limit_user_less_than_system_between(monkeypatch: pytest.MonkeyPatch) -> None:
    """场景 4 变体: 用户限额 (3) < 系统限额 (5), 已用 4 (> 3 但 < 5) → 拒绝.

    关键: 若用 MAX() 得 5, 4 < 5 会允许 (错误);
    用 COALESCE 用户优先得 3, 4 >= 3 拒绝 (正确).
    这是最关键的差异验证用例.
    """
    _patch_both_db_funcs(monkeypatch, limit=3, usage=4)

    allowed, count, eff_limit = await check_daily_report_limit("ip_user", "agent1")
    assert (allowed, count, eff_limit) == (False, 4, 3)


@pytest.mark.asyncio
async def test_check_limit_zero_means_unlimited(monkeypatch: pytest.MonkeyPatch) -> None:
    """限额 = 0 (数据库无配置) → 降级到环境变量 ip_daily_report_limit.

    _get_daily_limit_from_db 返回 0 时, check_daily_report_limit 降级到
    settings.ip_daily_report_limit. mock settings 返回 0 → 不限制.
    """
    monkeypatch.setattr(
        "src.api.ip_user_resolver._get_daily_limit_from_db",
        AsyncMock(return_value=0),
    )
    # mock settings.ip_daily_report_limit = 0 (不限制)
    fake_settings = MagicMock()
    fake_settings.ip_daily_report_limit = 0
    monkeypatch.setattr("src.config.settings.get_settings", lambda: fake_settings)

    allowed, count, eff_limit = await check_daily_report_limit("ip_user", "agent1")
    assert (allowed, count, eff_limit) == (True, 0, 0)


@pytest.mark.asyncio
async def test_check_limit_db_zero_falls_back_to_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """数据库无配置 (返回 0) → 降级到环境变量 ip_daily_report_limit=5 → 检查使用次数."""
    monkeypatch.setattr(
        "src.api.ip_user_resolver._get_daily_limit_from_db",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        "src.api.ip_user_resolver._get_daily_usage_from_db",
        AsyncMock(return_value=2),
    )
    # mock settings.ip_daily_report_limit = 5 (环境变量 fallback)
    fake_settings = MagicMock()
    fake_settings.ip_daily_report_limit = 5
    monkeypatch.setattr("src.config.settings.get_settings", lambda: fake_settings)

    allowed, count, eff_limit = await check_daily_report_limit("ip_user", "agent1")
    assert (allowed, count, eff_limit) == (True, 2, 5)


@pytest.mark.asyncio
async def test_check_limit_db_exception_degrades_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """数据库异常 → check_daily_report_limit 降级放行 (fail-open).

    _get_daily_limit_from_db 抛异常 → check_daily_report_limit 的 try/except 捕获 →
    返回 (True, 0, _DEFAULT_DAILY_LIMIT=5).
    """
    monkeypatch.setattr(
        "src.api.ip_user_resolver._get_daily_limit_from_db",
        AsyncMock(side_effect=ConnectionError("postgres down")),
    )

    allowed, count, eff_limit = await check_daily_report_limit("ip_user", "agent1")
    assert allowed is True  # fail-open
    assert count == 0
    assert eff_limit == 5  # _DEFAULT_DAILY_LIMIT


@pytest.mark.asyncio
async def test_check_limit_first_time_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """首次使用: 限额 5, 已用 0 → 允许."""
    _patch_both_db_funcs(monkeypatch, limit=5, usage=0)

    allowed, count, eff_limit = await check_daily_report_limit("ip_new_user", "agent1")
    assert (allowed, count, eff_limit) == (True, 0, 5)
