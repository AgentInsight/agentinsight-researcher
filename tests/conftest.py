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
"""

from __future__ import annotations

import os
from pathlib import Path

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
