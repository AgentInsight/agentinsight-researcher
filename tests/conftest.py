"""测试全局配置: 加载 .env 文件 (如果存在).

AGENTS.md 第 11/13 章:
- 密钥仅环境变量注入, 禁止硬编码/入仓
- 测试目标地址从环境变量 AGENT_URL 注入
- 测试数据隔离: namespace=test_* + user_id=test_* + session_id=test_*

本模块在 pytest 收集前加载项目根目录的 .env 文件 (python-dotenv),
使功能/API/回归/e2e 测试能读取 EMBEDDINGS_API_KEY / QDRANT_API_KEY 等配置.
不覆盖已有环境变量 (override=False), 允许 CI 显式注入.

注意: .env 中 QDRANT_URL/EMBEDDINGS_URL/POSTGRES_HOST/REDIS_HOST 是容器内服务地址
(如 http://qdrant:6333), 宿主机无法解析. 宿主机运行测试时需覆盖为 127.0.0.1.
通过 *_HOST 环境变量允许 CI 注入自定义宿主机地址.

AGENTS.md 第 13 章: 功能/回归/API/e2e 测试应在 docker compose up -d 且全部容器
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


# 需要容器栈运行的测试 marker (AGENTS.md 第 13 章)
# exploratory: 探索性测试 (边界/降级场景), 同样依赖容器栈
_SERVICE_DEPENDENT_MARKS = {
    "functional",
    "regression",
    "api",
    "e2e",
    "performance",
    "exploratory",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """收集后: 若 agent 服务不可达, 自动跳过依赖容器栈的测试.

    AGENTS.md 第 13 章: 功能/回归/API/e2e 测试应在 docker compose up -d 后执行.
    本地无容器栈时自动跳过, 避免大量连接失败噪声.
    """
    if _is_agent_reachable():
        return
    skip_marker = pytest.mark.skip(
        reason="容器栈未运行 (agent 服务不可达), 跳过功能/回归/API/e2e 测试"
    )
    for item in items:
        item_marks = {m.name for m in item.iter_markers()}
        if item_marks & _SERVICE_DEPENDENT_MARKS:
            item.add_marker(skip_marker)
