"""API 测试: 人在回路反馈端点 /v1/feedback.

测试约定:
- API 测试在 docker compose up -d 且全部容器 service_healthy 后执行
- /v1/feedback 仅用于人在回路审核反馈
- 未启用 human_review_enabled 时前端不应调用, 但端点本身应可用

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/api/test_feedback.py -v -m api

注意: 不测试人在回路完整流程 (需 human_review_enabled=true + WebSocket 配合).
仅验证端点契约: 无待处理反馈时返回 404.
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
def test_feedback_no_pending() -> None:
    """验证无待处理反馈: POST /v1/feedback 随机 session_id → 404.

    /v1/feedback 为允许调用的端点 (人在回路反馈通道).
    HumanAgent 未在等待时, FeedbackQueue.put_feedback() 返回 False → 404.
    """
    random_sid = f"test_feedback_{uuid.uuid4().hex[:12]}"
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/feedback",
            json={"session_id": random_sid, "feedback": "approve"},
        )
    assert r.status_code == 404, f"无待处理反馈应返回 404, 实际: {r.status_code} {r.text}"


@pytest.mark.api
def test_feedback_missing_session_id() -> None:
    """验证缺少 session_id: POST /v1/feedback 无 session_id → 422 (Pydantic 校验)."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/feedback",
            json={"feedback": "approve"},
        )
    assert r.status_code == 422, f"缺少 session_id 应返回 422, 实际: {r.status_code}"


@pytest.mark.api
def test_feedback_missing_feedback() -> None:
    """验证缺少 feedback: POST /v1/feedback 无 feedback → 422 (Pydantic 校验)."""
    random_sid = f"test_feedback_{uuid.uuid4().hex[:12]}"
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/feedback",
            json={"session_id": random_sid},
        )
    assert r.status_code == 422, f"缺少 feedback 应返回 422, 实际: {r.status_code}"
