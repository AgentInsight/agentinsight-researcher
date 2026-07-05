"""API 测试: 安全验证.

AGENTS.md 第 11/13/14 章硬约束:
- 安全响应头中间件不可绕过: nosniff / DENY / XSS-Protection / Referrer-Policy
- CORS 中间件正确响应 OPTIONS 预检请求 (CORS * 限制已移除, 见 AGENTS.md 第 11 章)
- Agent Discovery Protocol 公开发现端点: GET /.well-known/agent-discovery.json → 200

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/api/test_security.py -v -m api
"""

from __future__ import annotations

import os

import httpx
import pytest

# AGENTS.md 第 13 章: 测试目标地址从环境变量注入
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")

# API 测试超时 60s
API_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)


@pytest.mark.api
def test_security_headers() -> None:
    """验证安全响应头: nosniff / DENY / XSS-Protection / Referrer-Policy.

    AGENTS.md 第 11 章: 安全响应头中间件不可绕过.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.get(f"{AGENT_URL}/health")
    assert r.status_code == 200

    # X-Content-Type-Options: nosniff
    assert r.headers.get("x-content-type-options") == "nosniff", (
        f"X-Content-Type-Options 非 nosniff: {r.headers.get('x-content-type-options')}"
    )
    # X-Frame-Options: DENY
    assert r.headers.get("x-frame-options") == "DENY", (
        f"X-Frame-Options 非 DENY: {r.headers.get('x-frame-options')}"
    )
    # X-XSS-Protection: 1; mode=block
    xss = r.headers.get("x-xss-protection", "")
    assert "1" in xss and "mode=block" in xss, f"X-XSS-Protection 异常: {xss}"
    # Referrer-Policy: strict-origin-when-cross-origin
    assert r.headers.get("referrer-policy") == "strict-origin-when-cross-origin", (
        f"Referrer-Policy 异常: {r.headers.get('referrer-policy')}"
    )


@pytest.mark.api
def test_cors_config() -> None:
    """验证 CORS 中间件正确响应 OPTIONS 预检请求.

    AGENTS.md 第 11 章: CORS * 限制已移除, 允许配置 * 或具体白名单.
    本测试仅验证 CORS 中间件能正确返回 Access-Control-Allow-Origin 头.
    """
    # 发送带 Origin 的 OPTIONS 预检请求
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.options(
            f"{AGENT_URL}/v1/chat/completions",
            headers={
                "Origin": "http://localhost:8066",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
    # CORS 中间件应返回 Access-Control-Allow-Origin 头 (* 或具体 Origin)
    allow_origin = r.headers.get("access-control-allow-origin", "")
    assert allow_origin, (
        f"CORS 未返回 Access-Control-Allow-Origin 头: status={r.status_code}"
    )


@pytest.mark.api
def test_cors_allowed_origin() -> None:
    """验证白名单内 Origin 能正常获得 CORS 头.

    默认白名单: http://localhost:3000, http://localhost:8066.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.options(
            f"{AGENT_URL}/v1/chat/completions",
            headers={
                "Origin": "http://localhost:8066",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
    allow_origin = r.headers.get("access-control-allow-origin", "")
    # 白名单内 Origin 应被回显 (或返回 *)
    assert allow_origin in ("http://localhost:8066", "*"), (
        f"白名单 Origin 未获 CORS 头: allow_origin={allow_origin}"
    )


@pytest.mark.api
def test_agent_discovery() -> None:
    """验证 Agent Discovery Protocol: GET /.well-known/agent-discovery.json → 200.

    AGENTS.md 第 14 章: 公开发现端点, 无需鉴权.
    返回 Agent 元信息供客户端自动发现与对接.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.get(f"{AGENT_URL}/.well-known/agent-discovery.json")
    assert r.status_code == 200, f"agent-discovery 非 200: {r.status_code} {r.text}"
    data = r.json()
    # 必要字段校验
    assert "name" in data, f"缺少 name: {data}"
    assert "version" in data, f"缺少 version: {data}"
    assert "services" in data, f"缺少 services: {data}"
    assert "capabilities" in data, f"缺少 capabilities: {data}"
    assert "auth" in data, f"缺少 auth: {data}"
    # services 应包含 research 端点
    service_paths = [s.get("path") for s in data["services"]]
    assert "/v1/chat/completions" in service_paths, (
        f"services 缺少 /v1/chat/completions: {service_paths}"
    )
    # auth 应支持 bearer_jwt 和 none (AGENTS.md 第 8 章: 匿名降级)
    assert "bearer_jwt" in data["auth"], f"auth 缺少 bearer_jwt: {data['auth']}"
    assert "none" in data["auth"], f"auth 缺少 none: {data['auth']}"
