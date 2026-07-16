"""单元测试: FastAPI 应用骨架.

验证健康检查端点可用, 不依赖外部服务.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from server import app


def test_health_endpoint():
    """测试 /health 健康检查."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "agentinsight-researcher"
    assert data["version"] == "1.2.0"
