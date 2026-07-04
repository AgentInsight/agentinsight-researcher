"""回归测试: basic_report 完整研究流程.

AGENTS.md 第 13 章硬约束:
- 回归测试在 docker compose up -d 且全部容器 service_healthy 后执行
- 回归测试为合并 main 前门禁, 不推荐跳过
- 测试目标地址从环境变量 AGENT_URL 注入
- 每次用唯一 session_id=test_regression_* (测试数据隔离)
- 超时设置: 回归测试 300s

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/regression/test_research_basic_report.py -v -m regression

覆盖完整研究链路: 提问 → 检索 → 报告生成.
"""

from __future__ import annotations

import json
import os
import time
import uuid

import httpx
import pytest

# AGENTS.md 第 13 章: 测试目标地址从环境变量注入
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")

# 回归测试超时 300s (basic_report 研究 3-5 分钟)
REGRESSION_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)

# 报告内容最小长度 (字符)
MIN_CONTENT_LENGTH = 200


def _unique_session_id() -> str:
    """生成唯一 session_id (AGENTS.md 第 13 章: session_id=test_regression_*)."""
    return f"test_regression_{uuid.uuid4().hex[:12]}"


def _log(msg: str) -> None:
    """带时间戳输出, 便于追踪长流程进度."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


@pytest.mark.regression
def test_basic_report_non_stream() -> None:
    """basic_report 非流式完整流程: stream=false → 200 + content 非空 + >200 字.

    AGENTS.md 第 13 章: 回归测试为合并门禁.
    AGENTS.md 第 14 章: OpenAI 兼容端点非流式响应.
    """
    sid = _unique_session_id()
    query = "用 300 字简述 Python 异步编程的核心优势与应用场景"
    _log(f"非流式 basic_report 开始: session={sid}, query={query[:60]}")

    with httpx.Client(timeout=REGRESSION_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": query}],
                "stream": False,
                "report_type": "basic_report",
                "session_id": sid,
            },
        )

    assert r.status_code == 200, f"basic_report 非流式响应非 200: {r.status_code} {r.text[:500]}"
    data = r.json()
    assert data["object"] == "chat.completion"
    assert len(data["choices"]) == 1
    content = data["choices"][0]["message"]["content"]
    assert content, "basic_report 内容为空"
    assert len(content) >= MIN_CONTENT_LENGTH, (
        f"basic_report 内容过短 (<{MIN_CONTENT_LENGTH} 字): {len(content)} 字\n"
        f"内容预览: {content[:200]}"
    )
    _log(f"非流式 basic_report 完成: 内容长度 {len(content)} 字")
    _log(f"内容预览: {content[:200]}{'...' if len(content) > 200 else ''}")


@pytest.mark.regression
def test_basic_report_stream() -> None:
    """basic_report 流式完整流程: stream=true → SSE 流 + content 非空.

    AGENTS.md 第 13/14 章: 流式 SSE + 完整研究链路.
    """
    sid = _unique_session_id()
    query = "用 300 字简述 JavaScript 类型系统的核心特性"
    _log(f"流式 basic_report 开始: session={sid}, query={query[:60]}")

    content_parts: list[str] = []
    chunks_count = 0
    first_chunk_time: float | None = None
    start_time = time.time()

    with httpx.Client(timeout=REGRESSION_TIMEOUT) as client:
        with client.stream(
            "POST",
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": query}],
                "stream": True,
                "report_type": "basic_report",
                "session_id": sid,
            },
        ) as r:
            assert r.status_code == 200, f"流式响应非 200: {r.status_code}"
            content_type = r.headers.get("content-type", "")
            assert "text/event-stream" in content_type

            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                chunks_count += 1
                if first_chunk_time is None:
                    first_chunk_time = time.time() - start_time
                chunk = json.loads(payload)
                delta = chunk["choices"][0]["delta"]
                if "content" in delta and delta["content"]:
                    content_parts.append(delta["content"])

    full_content = "".join(content_parts)
    elapsed = time.time() - start_time
    _log(
        f"流式 basic_report 完成: 首块 {first_chunk_time:.1f}s, "
        f"总耗时 {elapsed:.1f}s, 帧数 {chunks_count}, 内容 {len(full_content)} 字"
    )

    # 验证内容非空
    assert full_content, "流式 basic_report content 为空"
    assert len(full_content) >= MIN_CONTENT_LENGTH, (
        f"流式 basic_report 内容过短 (<{MIN_CONTENT_LENGTH} 字): {len(full_content)} 字\n"
        f"内容预览: {full_content[:200]}"
    )
    # 验证 SSE 帧数 (首块 + 内容块 + 末块)
    assert chunks_count >= 3, f"SSE 帧数不足: {chunks_count}"
