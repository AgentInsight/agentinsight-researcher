"""单元测试: Agent Discovery Protocol 端点.

验证 src/api/agent_discovery.py:
- GET /.well-known/agent-discovery.json 返回 200
- 响应含 name/version/description
- services 数组含 5 个服务
- capabilities 数组含 7 项能力
- auth 数组含 bearer_jwt + none

单元测试不依赖外部服务.
公开发现端点, 无需鉴权, auth 含 bearer_jwt (可选) 与 none (匿名降级).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from server import app


def test_agent_discovery_returns_200() -> None:
    """测试 Agent Discovery 端点返回 200."""
    client = TestClient(app)
    response = client.get("/.well-known/agent-discovery.json")
    assert response.status_code == 200


def test_agent_discovery_has_metadata() -> None:
    """测试响应含 name/version/description."""
    client = TestClient(app)
    data = client.get("/.well-known/agent-discovery.json").json()
    assert "name" in data
    assert "version" in data
    assert "description" in data
    assert isinstance(data["name"], str)
    assert isinstance(data["version"], str)
    assert isinstance(data["description"], str)
    # name 不应为空
    assert len(data["name"]) > 0


def test_agent_discovery_has_5_services() -> None:
    """测试 services 数组含 5 个服务."""
    client = TestClient(app)
    data = client.get("/.well-known/agent-discovery.json").json()
    services = data["services"]
    assert isinstance(services, list)
    assert len(services) == 5
    # 每个服务应含 name/path/method
    for svc in services:
        assert "name" in svc
        assert "path" in svc
        assert "method" in svc


def test_agent_discovery_has_7_capabilities() -> None:
    """测试 capabilities 数组含 7 项能力."""
    client = TestClient(app)
    data = client.get("/.well-known/agent-discovery.json").json()
    capabilities = data["capabilities"]
    assert isinstance(capabilities, list)
    assert len(capabilities) == 7


def test_agent_discovery_auth_has_bearer_and_none() -> None:
    """测试 auth 数组含 bearer_jwt + none."""
    client = TestClient(app)
    data = client.get("/.well-known/agent-discovery.json").json()
    auth = data["auth"]
    assert isinstance(auth, list)
    assert "bearer_jwt" in auth
    assert "none" in auth


def test_agent_discovery_content_type_json() -> None:
    """测试响应 Content-Type 为 application/json."""
    client = TestClient(app)
    response = client.get("/.well-known/agent-discovery.json")
    assert "application/json" in response.headers["content-type"]
