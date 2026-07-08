"""功能测试: 验证 Redis 服务 (缓存 + 限流 + 短期会话).

AGENTS.md 第 1/6/7 章硬约束:
- Redis 用途: 热点缓存 + 限流 + 短期会话 (第 1 章)
- 会话级数据按 agent_id + user_id + session_id 三级分键 (第 6 章)
- Redis 键格式: {agent_id}:{user_id}:{module}:{type}:{id}, 禁止裸键 (第 7 章)
- 应设 TTL, 禁止永久键 (配置数据除外) (第 7 章)
- 测试数据隔离: agent_id=test_* + user_id=test_* (第 13 章)

执行方式 (宿主机, 容器栈已 healthy):
    set REDIS_HOST=127.0.0.1
    pytest tests/functional/test_redis_service.py -v -m functional
"""

from __future__ import annotations

import os
import uuid

import pytest
import redis  # type: ignore[import-not-found]

# Redis 连接配置 (宿主机直连, 从环境变量注入)
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_AUTH = os.getenv("REDIS_AUTH", "")
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

# 测试数据隔离前缀 (AGENTS.md 第 13 章: agent_id=test_* / user_id=test_*)
TEST_AGENT_ID = f"test_redis_agent_{uuid.uuid4().hex[:8]}"
TEST_USER_ID = f"test_redis_user_{uuid.uuid4().hex[:8]}"

# 连接超时 (功能测试不应长时间阻塞)
REDIS_TIMEOUT = 5


def _get_redis_client() -> redis.Redis:
    """构造 Redis 客户端 (同步, 与 test_container_health.py 一致的鉴权模式)."""
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        username="default" if REDIS_AUTH else None,
        password=REDIS_AUTH if REDIS_AUTH else None,
        socket_connect_timeout=REDIS_TIMEOUT,
        socket_timeout=REDIS_TIMEOUT,
    )


@pytest.mark.functional
def test_redis_ping() -> None:
    """验证 Redis PING 响应 (redis-cli ping 等价).

    AGENTS.md 第 1 章: Redis 为缓存/限流/短期会话基础设施, 必须 PING → True.
    """
    client = _get_redis_client()
    try:
        pong = client.ping()
        assert pong is True, f"Redis PING 返回非 True: {pong}"
    except redis.RedisError as e:
        pytest.fail(f"Redis PING 失败: {e}")
    finally:
        client.close()


@pytest.mark.functional
def test_redis_set_get_ttl() -> None:
    """验证 SET + GET + TTL, 含 agent_id:user_id: 前缀键格式.

    AGENTS.md 第 7 章:
    - 键格式 {agent_id}:{user_id}:{module}:{type}:{id}
    - 应设 TTL, 禁止永久键
    """
    client = _get_redis_client()
    # 完整键格式: {agent_id}:{user_id}:{module}:{type}:{id}
    key = f"{TEST_AGENT_ID}:{TEST_USER_ID}:cache:kv:item-1"
    value = "test-value-payload"
    ttl_seconds = 60
    try:
        # SET 带 TTL
        client.set(key, value, ex=ttl_seconds)

        # GET 应返回原值
        got = client.get(key)
        assert got is not None, f"GET 返回 None, 键未写入: {key}"
        # redis-py 返回 bytes, 解码验证
        assert got.decode() == value, f"GET 值不匹配: {got!r} != {value!r}"

        # TTL 应 > 0 且 <= ttl_seconds (未过期且未超原值)
        ttl = client.ttl(key)
        assert ttl > 0, f"TTL 非正, 键可能已过期或为永久键: ttl={ttl}"
        assert ttl <= ttl_seconds, f"TTL 超出设定值: ttl={ttl} > {ttl_seconds}"
    except redis.RedisError as e:
        pytest.fail(f"Redis SET/GET/TTL 失败: {e}")
    finally:
        # 清理测试键
        try:
            client.delete(key)
        except redis.RedisError:  # noqa: BLE001
            pass
        client.close()


@pytest.mark.functional
def test_redis_key_prefix_isolation() -> None:
    """验证不同 agent_id/user_id 前缀的键互不干扰 (数据隔离).

    AGENTS.md 第 7 章:
    - 所有键应加前缀 {agent_id}:{user_id}:
    - 会话级数据按 agent_id + user_id + session_id 三级分键, 禁止全局共享
    - 不同 agent/user 的同名 id 键应隔离 (前缀不同即不同键)
    """
    client = _get_redis_client()
    # 两个不同的 agent_id + user_id 组合, 相同的 module/type/id
    agent_a = f"test_redis_iso_a_{uuid.uuid4().hex[:8]}"
    user_a = f"test_redis_iso_ua_{uuid.uuid4().hex[:8]}"
    agent_b = f"test_redis_iso_b_{uuid.uuid4().hex[:8]}"
    user_b = f"test_redis_iso_ub_{uuid.uuid4().hex[:8]}"

    # 相同的 id 后缀, 仅前缀不同 → 应为两个独立键
    key_a = f"{agent_a}:{user_a}:cache:kv:shared-id"
    key_b = f"{agent_b}:{user_b}:cache:kv:shared-id"
    value_a = "payload-from-agent-a"
    value_b = "payload-from-agent-b"
    try:
        client.set(key_a, value_a, ex=60)
        client.set(key_b, value_b, ex=60)

        # 各自 GET 应返回各自的值, 互不覆盖
        got_a = client.get(key_a)
        got_b = client.get(key_b)
        assert got_a is not None and got_b is not None, "键未写入"
        assert got_a.decode() == value_a, f"agent_a 键值被污染: {got_a!r}"
        assert got_b.decode() == value_b, f"agent_b 键值被污染: {got_b!r}"

        # 验证两键确实独立: 删除 a 不影响 b
        client.delete(key_a)
        assert client.get(key_a) is None, "删除后 agent_a 键仍存在"
        assert client.get(key_b) is not None, "删除 agent_a 误伤 agent_b (隔离失效)"
        assert client.get(key_b).decode() == value_b, "agent_b 值在删除 a 后改变"
    except redis.RedisError as e:
        pytest.fail(f"Redis 前缀隔离测试失败: {e}")
    finally:
        # 清理: agent_a 已删除, 仅剩 agent_b
        try:
            client.delete(key_b)
        except redis.RedisError:  # noqa: BLE001
            pass
        client.close()
