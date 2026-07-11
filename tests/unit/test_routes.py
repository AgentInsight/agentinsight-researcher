"""单元测试: API 路由 (OpenAI 兼容端点骨架).

验证 /v1/chat/completions 流式与非流式响应格式.
不实际执行研究, 仅验证端点可访问与响应结构.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from server import app
from src.config.settings import Settings
from src.skills.researcher.query_classifier import QueryIntent


def test_models_endpoint():
    """测试 /v1/models 端点."""
    client = TestClient(app)
    response = client.get("/v1/models")
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "list"
    assert len(data["data"]) > 0
    assert data["data"][0]["id"] == "agentinsight-researcher"


def test_chat_completions_non_stream():
    """测试 /v1/chat/completions 非流式响应."""
    # mock 研究流水线 (意图分类 + 图执行), 避免 .env 存在时触发真实 LLM 调用挂起
    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(return_value=QueryIntent.RESEARCH)
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(
        return_value={
            "report_md": "测试报告内容",
            "sources": [],
            "curated_sources": [],
            "total_tokens": 10,
            "token_logs": [{"prompt_tokens": 5, "completion_tokens": 5}],
        }
    )
    with (
        patch("src.api.routes._has_report", new=AsyncMock(return_value=False)),
        patch(
            "src.api.routes.get_query_intent_classifier",
            return_value=mock_classifier,
        ),
        patch("src.api.routes._get_graph", new=AsyncMock(return_value=mock_graph)),
    ):
        client = TestClient(app)
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "研究中国新能源汽车行业"}],
                "stream": False,
            },
        )
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "chat.completion"
    assert len(data["choices"]) == 1
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert "content" in data["choices"][0]["message"]
    assert "usage" in data
    assert "total_tokens" in data["usage"]


def test_chat_completions_stream():
    """测试 /v1/chat/completions 流式 SSE 响应."""
    # mock 研究流水线 (意图分类 + 图执行), 避免 .env 存在时触发真实 LLM 调用挂起
    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(return_value=QueryIntent.RESEARCH)

    async def _fake_astream(*args: Any, **kwargs: Any) -> Any:
        yield {"report_generator": {"report_md": "测试报告内容"}}

    mock_graph = MagicMock()
    mock_graph.astream = _fake_astream

    with (
        patch("src.api.routes._has_report", new=AsyncMock(return_value=False)),
        patch(
            "src.api.routes.get_query_intent_classifier",
            return_value=mock_classifier,
        ),
        patch("src.api.routes._get_graph", new=AsyncMock(return_value=mock_graph)),
    ):
        client = TestClient(app)
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "研究 AI 行业"}],
                "stream": True,
            },
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]

            chunks = []
            for line in response.iter_lines():
                if line.startswith("data: "):
                    payload = line[6:]
                    if payload == "[DONE]":
                        break
                    chunks.append(json.loads(payload))

    # 应有首块(role) + 内容块 + 末块(finish_reason)
    assert len(chunks) >= 3
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


def test_chat_completions_empty_messages():
    """测试空 messages 列表应返回 400."""
    client = TestClient(app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "agentinsight-researcher",
            "messages": [],
            "stream": False,
        },
    )
    assert response.status_code == 400


def test_chat_completions_no_user_message():
    """测试无 user 消息应返回 400."""
    client = TestClient(app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "agentinsight-researcher",
            "messages": [{"role": "system", "content": "你是研究助手"}],
            "stream": False,
        },
    )
    assert response.status_code == 400


def test_chat_completions_empty_query():
    """测试空查询内容应返回 400."""
    client = TestClient(app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "agentinsight-researcher",
            "messages": [{"role": "user", "content": "  "}],
            "stream": False,
        },
    )
    assert response.status_code == 400


def test_security_headers():
    """测试安全响应头."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("X-XSS-Protection") == "1; mode=block"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


# ========== 文件上传 (用户需求 8) ==========


def test_files_upload_txt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """上传 .txt 文件 → 201 + file_id 三段格式 (agent_id:user_id:uuid)."""
    settings = Settings(_env_file=None, upload_dir=str(tmp_path))
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)

    client = TestClient(app)
    response = client.post(
        "/v1/files",
        files={"file": ("test.txt", b"hello world", "text/plain")},
    )
    assert response.status_code == 201
    data = response.json()
    assert "file_id" in data
    # file_id 格式: agent_id:user_id:uuid (三段)
    parts = data["file_id"].split(":")
    assert len(parts) == 3
    assert "filename" in data
    assert data["filename"] == "test.txt"
    assert data["extension"] == "txt"
    assert data["size_bytes"] == len(b"hello world")


def test_files_upload_too_large(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """上传超大文件 → 413 (mock settings.max_upload_size_mb)."""
    # max_upload_size_mb 为 int 类型, 设为 0 → 任何非空文件都超限
    settings = Settings(
        _env_file=None,
        upload_dir=str(tmp_path),
        max_upload_size_mb=0,
    )
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)

    client = TestClient(app)
    response = client.post(
        "/v1/files",
        files={"file": ("big.txt", b"x" * 2048, "text/plain")},
    )
    assert response.status_code == 413


def test_files_upload_invalid_extension(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """上传 .exe → 415 (扩展名白名单)."""
    settings = Settings(_env_file=None, upload_dir=str(tmp_path))
    monkeypatch.setattr("src.api.routes.get_settings", lambda: settings)

    client = TestClient(app)
    response = client.post(
        "/v1/files",
        files={"file": ("malware.exe", b"MZ\x90\x00", "application/octet-stream")},
    )
    assert response.status_code == 415


# ========== 人在回路反馈 ==========


def test_feedback_no_pending() -> None:
    """POST /v1/feedback 无待处理 → 404."""
    client = TestClient(app)
    response = client.post(
        "/v1/feedback",
        json={"session_id": "no-pending-session-xyz", "feedback": "approve"},
    )
    assert response.status_code == 404


# ========== Agent Discovery Protocol ==========


def test_agent_discovery_endpoint() -> None:
    """GET /.well-known/agent-discovery.json → 200."""
    client = TestClient(app)
    response = client.get("/.well-known/agent-discovery.json")
    assert response.status_code == 200
    data = response.json()
    assert "name" in data
    assert "version" in data
    assert "description" in data
