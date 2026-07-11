"""单元测试专用配置: SELF_HOST=True 绕过 JWT 强制校验.

AGENTS.md 第 8 章:
- self_host=True (自托管): JWT Token 可选, 不存在时降级 IP-based UserId
- self_host=False (云托管): 强制校验 JWT Token, 不存在时返回 401

单元测试不应依赖 JWT Token, 路由逻辑测试需要绕过认证.
此 conftest 在全局 conftest (加载 .env) 之后执行, 覆盖 SELF_HOST=True.

注意: API 测试 (tests/api/) 与功能测试 (tests/functional/) 在容器栈运行,
使用 .env / .env.qa 中的实际 SELF_HOST 配置, 不受此文件影响.
"""

from __future__ import annotations

import os

# 单元测试绕过 JWT 强制校验 (self_host=True)
# 全局 conftest.py 已加载 .env (可能 SELF_HOST=False), 此处覆盖为 True
os.environ["SELF_HOST"] = "True"

# 清除 settings 缓存, 确保下次 get_settings() 读取新的 SELF_HOST 值
try:
    from src.config.settings import get_settings

    get_settings.cache_clear()
except ImportError:
    # server.py 尚未导入时, 忽略 (首次 import 会读取新值)
    pass
