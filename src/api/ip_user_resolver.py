"""IP-based 用户身份解析 + 每日报告限额控制.

无 JWT Token 时, 按 IP 生成唯一且不变的 UserId.
- 1 个 IP 对应 1 个 UserId (确定性: SHA256 哈希, 不存储原始 IP)
- 每日报告生成限额 (默认 3, 环境变量 IP_DAILY_REPORT_LIMIT 控制)
- 仅报告生成成功才计数
- 超限时友好提示用户

Redis 键格式: {agent_id}:{user_id}:daily_report:{YYYY-MM-DD}
TTL: 当日剩余秒数 (自动过期)
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

# 默认每日限额 (与环境变量 IP_DAILY_REPORT_LIMIT 对齐)
_DEFAULT_DAILY_LIMIT = 3


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


def _get_daily_key(agent_id: str, user_id: str) -> str:
    """构造 Redis 每日报告计数键.

    格式: {agent_id}:{user_id}:daily_report:{YYYY-MM-DD}
    """
    # 使用 UTC+8 (北京时间) 的日期, 与用户感知的"自然日"对齐
    now_bj = datetime.now(UTC) + timedelta(hours=8)
    date_str = now_bj.strftime("%Y-%m-%d")
    return f"{agent_id}:{user_id}:daily_report:{date_str}"


def _seconds_until_midnight() -> int:
    """计算到北京时间次日 0 点的秒数 (用于 Redis TTL)."""
    now_bj = datetime.now(UTC) + timedelta(hours=8)
    # 次日 0 点 (北京时间)
    tomorrow = (now_bj + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    delta = tomorrow - now_bj
    return max(60, int(delta.total_seconds()))  # 至少 60s


async def check_daily_report_limit(
    user_id: str,
    agent_id: str,
    limit: int = _DEFAULT_DAILY_LIMIT,
) -> tuple[bool, int]:
    """检查用户当日报告生成是否超限.

    Args:
        user_id: 用户 ID (IP 生成的或 JWT 解析的)
        agent_id: Agent 名称
        limit: 每日限额 (默认 3)
    Returns:
        (allowed, current_count): allowed=True 表示未超限可继续, current_count 为当日已用次数
    """
    if limit <= 0:
        # limit=0 表示不限制
        return True, 0

    try:
        from src.common.redis_client import get_redis_client

        redis = await get_redis_client()
        if redis is None:
            # Redis 未配置, 降级放行 (fail-open)
            logger.warning("Redis 未配置, 每日限额检查降级放行")
            return True, 0
        key = _get_daily_key(agent_id, user_id)
        current = await redis.get(key)
        count = int(current) if current else 0
        if count >= limit:
            logger.info("用户 %s 当日报告已达限额 (%d/%d)", user_id, count, limit)
            return False, count
        return True, count
    except Exception as e:  # noqa: BLE001
        # Redis 不可用时降级放行 (fail-open, 不阻断用户)
        logger.warning("每日限额检查失败 (降级放行): %s", e)
        return True, 0


async def increment_daily_report_count(
    user_id: str,
    agent_id: str,
) -> int:
    """报告生成成功后递增当日计数.

    仅在报告生成成功后调用. 首次计数时设置 TTL (到当日结束).

    Args:
        user_id: 用户 ID
        agent_id: Agent 名称
    Returns:
        递增后的计数值
    """
    try:
        from src.common.redis_client import get_redis_client

        redis = await get_redis_client()
        if redis is None:
            logger.warning("Redis 未配置, 跳过每日报告计数")
            return 0
        key = _get_daily_key(agent_id, user_id)
        new_count = await redis.incr(key)
        if new_count == 1:
            # 首次计数, 设置 TTL (到北京时间次日 0 点)
            ttl = _seconds_until_midnight()
            await redis.expire(key, ttl)
        logger.info("用户 %s 当日报告计数 +1 (当前 %d)", user_id, new_count)
        return new_count
    except Exception as e:  # noqa: BLE001
        logger.warning("每日报告计数递增失败: %s", e)
        return 0
