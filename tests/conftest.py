"""测试全局配置: 加载 .env 文件 (如果存在).

- 密钥仅环境变量注入, 禁止硬编码/入仓
- 测试目标地址从环境变量 AGENT_URL 注入
- 测试数据隔离: namespace=test_* + user_id=test_* + session_id=test_*

本模块在 pytest 收集前加载项目根目录的 .env 文件 (python-dotenv),
使功能/API/回归/e2e 测试能读取 EMBEDDINGS_API_KEY / QDRANT_API_KEY 等配置.
不覆盖已有环境变量 (override=False), 允许 CI 显式注入.

注意: .env 中 QDRANT_URL/EMBEDDINGS_URL/POSTGRES_HOST/REDIS_HOST 是容器内服务地址
(如 http://qdrant:6333), 宿主机无法解析. 宿主机运行测试时需覆盖为 127.0.0.1.
通过 *_HOST 环境变量允许 CI 注入自定义宿主机地址.

功能/回归/API/e2e 测试应在 docker compose up -d 且全部容器
service_healthy 后执行. 当容器栈未运行时, 这些测试应自动跳过而非失败.
通过 pytest_collection_modifyitems 钩子检测 agent 服务可达性, 不可达时自动跳过.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# 加载 .env 文件 (如果存在), override=True 覆盖系统已有环境变量
# 原因: 宿主机可能有同名环境变量 (如 REDIS_AUTH) 与容器 .env 不一致,
# 测试需与容器配置一致, 因此 .env 优先级高于系统环境变量.
# CI 场景若无 .env 文件, 则完全依赖 CI 注入的环境变量.
try:
    from dotenv import load_dotenv

    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)
except ImportError:
    # python-dotenv 未安装时静默跳过 (单元测试不依赖 .env)
    pass

# 宿主机测试时, 覆盖容器内服务地址为宿主机地址
# (.env 中 QDRANT_URL=http://qdrant:6333 是容器内地址, 宿主机无法解析)
# 允许通过 QDRANT_HOST/EMBEDDINGS_HOST 等环境变量注入自定义地址 (CI 场景)
os.environ["QDRANT_URL"] = os.environ.get("QDRANT_HOST", "http://127.0.0.1:6333")
os.environ["EMBEDDINGS_URL"] = os.environ.get("EMBEDDINGS_HOST", "http://127.0.0.1:8088")
os.environ["POSTGRES_HOST"] = os.environ.get("POSTGRES_HOST_HOST", "127.0.0.1")
os.environ["REDIS_HOST"] = os.environ.get("REDIS_HOST_HOST", "127.0.0.1")
# 覆盖 REDIS_URL (settings.py 读取 REDIS_URL, .env 中是 redis://redis:6379/0 容器内地址)
# 密码通过 REDIS_AUTH 单独传递 (redis_client.py 第 45 行), URL 不含密码
os.environ["REDIS_URL"] = os.environ.get("REDIS_URL_HOST", "redis://127.0.0.1:6379/0")

# 绕过系统 HTTP 代理 (如 Clash/V2Ray 在 127.0.0.1:17890) 对 localhost 的拦截
# 否则 httpx 默认 trust_env=True 会走代理, localhost 请求返回 502
_no_proxy = "127.0.0.1,localhost,::1"
os.environ["NO_PROXY"] = _no_proxy
os.environ["no_proxy"] = _no_proxy


def _is_agent_reachable() -> bool:
    """检测 researcher agent 服务是否健康 (HTTP GET /health → 200 + status=ok + service 匹配).

    用于判断容器栈是否已启动且健康:
    - True: 功能/回归/API/e2e 测试可执行
    - False: 跳过这些测试 (避免无服务环境下的大量失败)

    注意: 端口 8066 可能被 AgentInsightService (.NET API) 占用,
    必须验证 service=agentinsight-researcher 才确认是本项目.
    """
    agent_url = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")
    try:
        import httpx

        # trust_env=False 绕过系统 HTTP 代理 (localhost 不应走代理)
        with httpx.Client(trust_env=False, timeout=3.0) as client:
            r = client.get(f"{agent_url}/health")
            if r.status_code != 200:
                return False
            body = r.json()
            # 必须同时验证 status + service, 排除 AgentInsightService 占用端口
            return body.get("status") == "ok" and body.get("service") == "agentinsight-researcher"
    except Exception:  # noqa: BLE001
        return False


# 需要容器栈运行的测试 marker
# exploratory: 探索性测试 (边界/降级场景), 同样依赖容器栈
_SERVICE_DEPENDENT_MARKS = {
    "functional",
    "regression",
    "api",
    "e2e",
    "performance",
    "exploratory",
}

# ========== 条件性测试 mark 配置管理 ==========
# 不在测试函数内用 pytest.skip(), 而是通过 mark + conftest 钩子统一管理.
# 用户原则: 特定条件下才测试的用例, 应通过配置管理而非标志为 skip.


def _is_self_host_false() -> bool:
    """检测服务端是否 SELF_HOST=False (强制 JWT 校验).

    通过 API 探测: 发送带 org_id 的无 token 请求,
    401 = SELF_HOST=False (强制 JWT 校验),
    200 = SELF_HOST=True (IP-based 降级, 跳过 JWT 校验).

    本地 .env 可能与容器 .env.qa 的 SELF_HOST 不一致, 通过 API 探测服务端实际状态.
    """
    agent_url = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")
    try:
        import httpx

        with httpx.Client(trust_env=False, timeout=5.0) as client:
            r = client.post(
                f"{agent_url}/v1/chat/completions",
                json={
                    "model": "agentinsight-researcher",
                    "messages": [{"role": "user", "content": "ping"}],
                    "stream": False,
                    "org_id": "test-self-host-probe",
                },
            )
            # 401 说明 SELF_HOST=False (强制 JWT 校验)
            return r.status_code == 401
    except Exception:  # noqa: BLE001
        return False


def _is_human_review_enabled() -> bool:
    """检测是否应运行人在回路测试.

    通过环境变量 RUN_HUMAN_REVIEW_TESTS 显式控制:
    - 设置为 1/true/yes: 用户确认服务端 human_review_enabled=True, 运行测试
    - 未设置或其它值: 跳过人在回路测试

    服务端 human_review_enabled 配置无法通过 API 直接探测,
    由用户显式设置环境变量告知测试框架.
    """
    return os.getenv("RUN_HUMAN_REVIEW_TESTS", "").lower() in ("1", "true", "yes")


def _is_websocket_available() -> bool:
    """检测 WebSocket 端点是否可用.

    若 human_review 已启用, WebSocket 必然可用 (人在回路依赖 WS).
    否则尝试连接探测 /v1/ws/{test_session_id}.
    """
    if _is_human_review_enabled():
        return True
    # 尝试连接探测
    import asyncio

    agent_url = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")
    ws_uri = f"{agent_url.replace('http://', 'ws://').replace('https://', 'wss://')}/v1/ws/test_probe"
    try:
        import websockets

        async def _probe() -> bool:
            try:
                ws = await asyncio.wait_for(
                    websockets.connect(ws_uri), timeout=3.0
                )
                await ws.close()
                return True
            except Exception:  # noqa: BLE001
                return False

        return asyncio.get_event_loop().run_until_complete(_probe())
    except Exception:  # noqa: BLE001
        return False


def _is_psutil_available() -> bool:
    """检测 psutil 库是否安装 (内存监控测试可选依赖)."""
    try:
        import psutil  # noqa: F401

        return True
    except ImportError:
        return False


# 条件性测试 mark 名称
_REQUIRES_SELF_HOST_FALSE_MARK = "requires_self_host_false"
_REQUIRES_HUMAN_REVIEW_MARK = "requires_human_review"
_REQUIRES_WEBSOCKET_MARK = "requires_websocket"
_REQUIRES_PSUTIL_MARK = "requires_psutil"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """收集后: 根据环境/配置自动跳过不满足条件的测试.

    1. 容器栈不可达: 跳过所有依赖容器栈的测试 (functional/regression/api/e2e/performance/exploratory)
    2. SELF_HOST=True: 跳过 requires_self_host_false 标记的测试 (401 校验不适用)
    3. human_review 未启用: 跳过 requires_human_review 标记的测试
    4. WebSocket 不可用: 跳过 requires_websocket 标记的测试
    5. psutil 未安装: 跳过 requires_psutil 标记的测试

    本机制替代测试函数内的 pytest.skip(), 实现配置管理.
    """
    agent_reachable = _is_agent_reachable()

    # 1. 容器栈不可达: 跳过所有依赖容器栈的测试
    if not agent_reachable:
        skip_marker = pytest.mark.skip(
            reason="容器栈未运行 (agent 服务不可达), 跳过功能/回归/API/e2e 测试"
        )
        for item in items:
            item_marks = {m.name for m in item.iter_markers()}
            if item_marks & _SERVICE_DEPENDENT_MARKS:
                item.add_marker(skip_marker)
        return  # 容器栈不可达时, 以下条件检测无意义

    # 2. SELF_HOST=True: 跳过 requires_self_host_false
    if not _is_self_host_false():
        skip_marker = pytest.mark.skip(
            reason="服务端 SELF_HOST=True (IP-based 解析), 401 校验测试不适用"
        )
        for item in items:
            item_marks = {m.name for m in item.iter_markers()}
            if _REQUIRES_SELF_HOST_FALSE_MARK in item_marks:
                item.add_marker(skip_marker)

    # 3. human_review 未启用: 跳过 requires_human_review
    if not _is_human_review_enabled():
        skip_marker = pytest.mark.skip(
            reason="未设置 RUN_HUMAN_REVIEW_TESTS=1, 人在回路测试跳过 "
            "(需服务端 human_review_enabled=True + 环境变量 RUN_HUMAN_REVIEW_TESTS=1)"
        )
        for item in items:
            item_marks = {m.name for m in item.iter_markers()}
            if _REQUIRES_HUMAN_REVIEW_MARK in item_marks:
                item.add_marker(skip_marker)

    # 4. WebSocket 不可用: 跳过 requires_websocket
    if not _is_websocket_available():
        skip_marker = pytest.mark.skip(
            reason="WebSocket 端点不可用 (未启用或需鉴权)"
        )
        for item in items:
            item_marks = {m.name for m in item.iter_markers()}
            if _REQUIRES_WEBSOCKET_MARK in item_marks:
                item.add_marker(skip_marker)

    # 5. psutil 未安装: 跳过 requires_psutil
    if not _is_psutil_available():
        skip_marker = pytest.mark.skip(
            reason="psutil 未安装, 内存监控测试跳过 (可选依赖, pip install psutil)"
        )
        for item in items:
            item_marks = {m.name for m in item.iter_markers()}
            if _REQUIRES_PSUTIL_MARK in item_marks:
                item.add_marker(skip_marker)
