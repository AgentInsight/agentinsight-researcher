"""IP-based 用户身份解析 + 每日报告限额控制.

无 JWT Token 时, 按 IP 生成唯一且不变的 UserId.
- 1 个 IP 对应 1 个 UserId (确定性: SHA256 哈希, 不存储原始 IP)
- 每日报告生成限额从数据库 report_limits 表读取 (已从环境变量迁移)
  - UserId 为 NULL 的行表示系统默认限额 (默认 5)
  - 取限额时取 UserId 专属限额与系统默认限额中较高的那个 (max)
- 每日报告生成使用次数从数据库 daily_report_usage 表读取 (已从 Redis 迁移)
  - 按 UserId + 日期 记录当日报告生成次数
- 仅报告生成成功才计数
- 超限时友好提示用户

数据库降级策略: 数据库不可用时降级放行 (fail-open, 不阻断用户)
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

# 环境变量降级 fallback (数据库不可用时使用)
_DEFAULT_DAILY_LIMIT = 5


def generate_user_id_from_ip(ip: str) -> str:
    """根据 IP 生成确定性 UserId (唯一且不变).

    SHA256 哈希, 优先取前 24 位 (96 bit, 碰撞概率远低于 16 位);
    若前 24 位无法保证唯一性 (理论上极小概率), 则使用完整 64 位哈希.
    加 "ip_" 前缀标识匿名用户.
    不存储原始 IP, 仅存储哈希 (PII 保护).

    Args:
        ip: 客户端 IP 地址
    Returns:
        形如 "ip_a1b2c3d4e5f6789012345678" 的 UserId
    """
    if not ip:
        ip = "0.0.0.0"
    hash_hex = hashlib.sha256(ip.encode("utf-8")).hexdigest()
    # 优先采用前 24 位 hex (96 bit, IPv4 全空间 ~4.3e9, 生日碰撞概率 < 1e-12)
    return f"ip_{hash_hex[:24]}"


def get_client_ip(request) -> str:
    """从请求中提取真实客户端 IP.

    优先级: X-Forwarded-For (第一个) > X-Real-IP > request.client.host

    Args:
        request: Starlette/FastAPI Request 对象
    Returns:
        客户端 IP 字符串
    """
    # X-Forwarded-For: client, proxy1, proxy2 (取第一个)
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    # X-Real-IP
    xri = request.headers.get("X-Real-IP", "")
    if xri:
        return xri.strip()
    # 直连
    if request.client:
        return request.client.host
    return "0.0.0.0"


def _get_beijing_date() -> str:
    """获取北京时间 (UTC+8) 当日日期字符串 (YYYY-MM-DD).

    与用户感知的"自然日"对齐.
    """
    now_bj = datetime.now(UTC) + timedelta(hours=8)
    return now_bj.strftime("%Y-%m-%d")


async def _get_daily_limit_from_db(user_id: str) -> int:
    """从数据库 report_limits 表读取每日报告限额.

    取 UserId 专属限额与系统默认限额 (user_id IS NULL) 中较高的那个 (max).
    若两者均不存在, 返回 0 (表示不限制, 由调用方降级处理).

    Args:
        user_id: 用户 ID
    Returns:
        每日限额 (0 = 不限制; >0 = 限额值)
    """
    from src.memory.db_initializer import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        # 同时查询用户专属限额 (user_id = $1) 和系统默认限额 (user_id IS NULL)
        # 取两者中较高的 (COALESCE 处理 NULL)
        row = await conn.fetchrow(
            """
            SELECT
                COALESCE(
                    MAX(COALESCE(daily_limit, 0)), 0
                ) AS effective_limit
            FROM report_limits
            WHERE user_id = $1 OR user_id IS NULL
            """,
            user_id,
        )
        if row:
            return int(row["effective_limit"])
        return 0


async def check_daily_report_limit(
    user_id: str,
    agent_id: str,  # noqa: ARG001 保留以兼容旧调用签名
    limit: int | None = None,  # noqa: ARG001 保留以兼容旧调用签名, 数据库模式忽略
) -> tuple[bool, int, int]:
    """检查用户当日报告生成是否超限.

    限额从数据库 report_limits 表读取 (取用户专属 + 系统默认的较高者).
    使用次数从数据库 daily_report_usage 表读取 (按 user_id + 日期).

    Args:
        user_id: 用户 ID (IP 生成的或 JWT 解析的)
        agent_id: Agent 名称 (保留兼容, 数据库模式按 user_id 隔离)
        limit: 保留兼容旧调用, 数据库模式忽略此参数
    Returns:
        (allowed, current_count, daily_limit):
        - allowed=True 表示未超限可继续
        - current_count 为当日已用次数
        - daily_limit 为有效限额
    """
    try:
        # 1. 从数据库读取有效限额 (用户专属 + 系统默认取较高者)
        effective_limit = await _get_daily_limit_from_db(user_id)

        # 数据库无配置时降级到环境变量 fallback
        if effective_limit <= 0:
            from src.config.settings import get_settings

            effective_limit = get_settings().ip_daily_report_limit

        # limit <= 0 表示不限制
        if effective_limit <= 0:
            return True, 0, 0

        # 2. 从数据库读取当日使用次数
        current_count = await _get_daily_usage_from_db(user_id)

        if current_count >= effective_limit:
            logger.info(
                "用户 %s 当日报告已达限额 (%d/%d)",
                user_id,
                current_count,
                effective_limit,
            )
            return False, current_count, effective_limit
        return True, current_count, effective_limit
    except Exception as e:  # noqa: BLE001
        # 数据库不可用时降级放行 (fail-open, 不阻断用户)
        logger.warning("每日限额检查失败 (降级放行): %s", e)
        return True, 0, _DEFAULT_DAILY_LIMIT


async def _get_daily_usage_from_db(user_id: str) -> int:
    """从数据库 daily_report_usage 表读取当日使用次数.

    Args:
        user_id: 用户 ID
    Returns:
        当日已使用次数 (无记录返回 0)
    """
    from src.memory.db_initializer import get_pool

    usage_date = _get_beijing_date()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT daily_count FROM daily_report_usage WHERE user_id = $1 AND usage_date = $2",
            user_id,
            usage_date,
        )
        if row:
            return int(row["daily_count"])
        return 0


async def increment_daily_report_count(
    user_id: str,
    agent_id: str,  # noqa: ARG001 保留兼容旧调用签名
) -> int:
    """报告生成成功后递增当日计数.

    从数据库 daily_report_usage 表 upsert (user_id + 日期).
    首次计数时 INSERT (daily_count=1), 后续 UPDATE (daily_count + 1).

    Args:
        user_id: 用户 ID
        agent_id: Agent 名称 (保留兼容)
    Returns:
        递增后的计数值
    """
    try:
        from src.memory.db_initializer import get_pool

        usage_date = _get_beijing_date()
        pool = await get_pool()
        async with pool.acquire() as conn:
            # INSERT ... ON CONFLICT 幂等 upsert
            # 首次: INSERT daily_count=1; 后续: daily_count = daily_count + 1
            row = await conn.fetchrow(
                """
                INSERT INTO daily_report_usage (user_id, usage_date, daily_count)
                VALUES ($1, $2, 1)
                ON CONFLICT (user_id, usage_date)
                DO UPDATE SET daily_count = daily_report_usage.daily_count + 1
                RETURNING daily_count
                """,
                user_id,
                usage_date,
            )
            new_count = int(row["daily_count"]) if row else 0
            logger.info("用户 %s 当日报告计数 +1 (当前 %d)", user_id, new_count)
            return new_count
    except Exception as e:  # noqa: BLE001
        logger.warning("每日报告计数递增失败: %s", e)
        return 0
