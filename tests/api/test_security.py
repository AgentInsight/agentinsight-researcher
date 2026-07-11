"""API 测试: 安全验证.

测试约定:
- 安全响应头中间件不可绕过: nosniff / DENY / XSS-Protection / Referrer-Policy
- CORS 中间件正确响应 OPTIONS 预检请求 (CORS * 限制已移除)
- Agent Discovery Protocol 公开发现端点: GET /.well-known/agent-discovery.json → 200

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/api/test_security.py -v -m api
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

# 测试目标地址从环境变量注入
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")

# API 测试超时 60s
API_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)


@pytest.mark.api
def test_security_headers() -> None:
    """验证安全响应头: nosniff / DENY / XSS-Protection / Referrer-Policy.

    安全响应头中间件不可绕过.
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

    CORS * 限制已移除, 允许配置 * 或具体白名单.
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
    assert allow_origin, f"CORS 未返回 Access-Control-Allow-Origin 头: status={r.status_code}"


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

    公开发现端点, 无需鉴权.
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
    # auth 应支持 bearer_jwt 和 none (匿名降级)
    assert "bearer_jwt" in data["auth"], f"auth 缺少 bearer_jwt: {data['auth']}"
    assert "none" in data["auth"], f"auth 缺少 none: {data['auth']}"


# ============================================================================
# JWT Token 身份解析安全测试 (安全硬约束)
# - Bearer JWT Token 有效时调用 GET /api/user 获取 user_id
# - Token 不存在时降级 (self_host=True → IP-based UserId)
# - Token 调用失败时降级并告警
# - 禁止将原始 JWT token 写入日志或持久化存储 (PII 安全硬约束)
# ============================================================================


@pytest.mark.api
def test_jwt_token_not_in_response_headers() -> None:
    """验证 JWT Token 不出现在任何响应头中 (PII 安全硬约束).

    禁止将原始 JWT token 写入日志或持久化存储;
    API 响应禁止返回密码/密钥原文.
    """
    test_token = f"eyJhbGciOiJIUzI1NiJ9.{uuid.uuid4().hex}.{uuid.uuid4().hex}"
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
                "session_id": f"test_sec_jwt_{uuid.uuid4().hex[:8]}",
            },
            headers={"Authorization": f"Bearer {test_token}"},
        )
    assert r.status_code == 200, f"请求失败: {r.status_code} {r.text}"
    # 所有响应头都不应含原始 token
    for header_value in r.headers.values():
        assert test_token not in header_value, "JWT Token 泄漏在响应头中"


@pytest.mark.api
def test_jwt_token_not_in_response_body_with_org_id() -> None:
    """验证携带 org_id 时 JWT Token 不出现在响应 body 中 (PII 安全硬约束).

    SELF_HOST=False 时 org_id 触发点数校验, 需 token.
    禁止将原始 JWT token 写入持久化存储.
    """
    test_token = f"eyJhbGciOiJIUzI1NiJ9.{uuid.uuid4().hex}.{uuid.uuid4().hex}"
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
                "session_id": f"test_sec_org_{uuid.uuid4().hex[:8]}",
                "org_id": "test-org-jwt-check",
            },
            headers={"Authorization": f"Bearer {test_token}"},
        )
    # SELF_HOST=True → 200 (跳过校验); SELF_HOST=False → 200 或 401 (缺权限)
    # 无论哪种情况, 响应都不应含原始 token
    assert test_token not in r.text, f"JWT Token 泄漏在响应 body 中: {r.text[:200]}"


@pytest.mark.api
def test_no_token_degrades_to_ip_based_user_id() -> None:
    """验证无 token 时降级 IP-based UserId (self_host=True 默认模式).

    self_host=True 时 token 不存在按 IP 生成确定性 UserId.
    服务端默认 SELF_HOST=True, 无 token 应返回 200.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
                "session_id": f"test_sec_notoken_{uuid.uuid4().hex[:8]}",
            },
        )
    assert r.status_code == 200, (
        f"无 token (self_host=True) 应降级返回 200, 实际: {r.status_code} {r.text}"
    )


@pytest.mark.api
def test_invalid_bearer_token_degrades_gracefully() -> None:
    """验证无效 Bearer Token 调用失败时优雅降级 (self_host=True).

    token 调用失败时降级 IP-based UserId 并告警.
    test-token-invalid 非合法 JWT, user_info API 会返回 4xx, 应降级.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
                "session_id": f"test_sec_invalid_{uuid.uuid4().hex[:8]}",
            },
            headers={"Authorization": "Bearer invalid-token-will-fail"},
        )
    # self_host=True 时, token 调用失败应降级返回 200
    assert r.status_code == 200, (
        f"无效 token (self_host=True) 应降级返回 200, 实际: {r.status_code} {r.text}"
    )


@pytest.mark.api
def test_non_bearer_auth_header_treated_as_no_token() -> None:
    """验证非 Bearer 格式的 Authorization 头按无 token 处理.

    仅 Bearer 前缀的 token 被识别.
    Basic 认证头应被视为无 token, 降级 IP-based UserId.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
                "session_id": f"test_sec_basic_{uuid.uuid4().hex[:8]}",
            },
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
    assert r.status_code == 200, (
        f"Basic 认证头应按无 token 处理 (降级), 实际: {r.status_code} {r.text}"
    )


@pytest.mark.api
def test_empty_bearer_token_treated_as_no_token() -> None:
    """验证 'Bearer ' (空 token) 按无 token 处理.

    token 提取后为空字符串时视为无 token.

    注: httpx 严格校验 header 值, 'Bearer ' (空格后无内容) 会被拒绝.
    使用 http.client 标准库发送原始 HTTP 请求绕过 httpx 校验.
    """
    import http.client
    import json as _json

    session_id = f"test_sec_empty_{uuid.uuid4().hex[:8]}"
    body = _json.dumps(
        {
            "model": "agentinsight-researcher",
            "messages": [{"role": "user", "content": "你好"}],
            "stream": False,
            "session_id": session_id,
        }
    )
    conn = http.client.HTTPConnection("127.0.0.1", 8066, timeout=60.0)
    try:
        conn.request(
            "POST",
            "/v1/chat/completions",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer ",  # 空 token
            },
        )
        resp = conn.getresponse()
        status = resp.status
        resp_body = resp.read().decode("utf-8", errors="replace")
    finally:
        conn.close()
    assert status == 200, (
        f"空 Bearer token 应按无 token 处理 (降级), 实际: {status} {resp_body[:200]}"
    )


@pytest.mark.api
def test_public_health_endpoint_no_jwt_required() -> None:
    """验证 /health 公开路径无需 JWT (健康检查不应强制鉴权).

    /health 为公开路径, JWT 中间件应跳过.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.get(f"{AGENT_URL}/health")
    assert r.status_code == 200, f"/health 应返回 200 (无需 JWT), 实际: {r.status_code}"
    data = r.json()
    assert data.get("status") == "ok"


@pytest.mark.api
def test_jwt_token_not_in_stream_response_with_org_id() -> None:
    """验证流式响应中 JWT Token 不泄漏 (携带 org_id 场景).

    禁止将原始 JWT token 写入日志或持久化存储.
    """
    test_token = f"eyJhbGciOiJIUzI1NiJ9.{uuid.uuid4().hex}.{uuid.uuid4().hex}"
    with httpx.Client(timeout=API_TIMEOUT) as client:
        with client.stream(
            "POST",
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": True,
                "session_id": f"test_sec_stream_{uuid.uuid4().hex[:8]}",
                "org_id": "test-org-stream",
            },
            headers={"Authorization": f"Bearer {test_token}"},
        ) as r:
            assert r.status_code == 200
            full_text = ""
            for line in r.iter_lines():
                full_text += line + "\n"
    assert test_token not in full_text, "JWT Token 泄漏在流式响应中"
