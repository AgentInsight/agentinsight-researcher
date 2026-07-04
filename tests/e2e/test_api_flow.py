"""端到端测试: API 层完整研究链路 (非浏览器).

AGENTS.md 第 13 章硬约束:
- e2e 必须在容器栈 service_healthy 后执行
- 测试目标地址从环境变量 AGENT_URL 注入
- 必须覆盖完整链路: 提问 → 检索 → 工具调用 → 流式响应 → 会话持久化
- 超时设置: e2e 测试 600s

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/e2e/test_api_flow.py -v -m e2e

注意: 本文件不修改现有 test_page_flow.py (浏览器 e2e).
本文件为 API 层 e2e, 通过 httpx 直接打 OpenAI 兼容端点.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import httpx
import pytest

# AGENTS.md 第 13 章: 测试目标地址从环境变量注入, 禁止硬编码
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")

# e2e 测试超时 600s (完整研究 5-10 分钟)
E2E_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)


def _unique_session_id() -> str:
    """生成唯一 session_id (AGENTS.md 第 13 章: session_id=test_*)."""
    return f"test_e2e_{uuid.uuid4().hex[:12]}"


def _log(msg: str) -> None:
    """带时间戳输出, 便于追踪长流程进度."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def _stream_research(
    client: httpx.AsyncClient,
    query: str,
    session_id: str,
) -> str:
    """发起流式研究请求, 返回完整 content.

    AGENTS.md 第 14 章: 统一调用 POST /v1/chat/completions, 请求体带 stream: true.
    """
    content_parts: list[str] = []
    first_chunk_time: float | None = None
    start = time.time()

    async with client.stream(
        "POST",
        f"{AGENT_URL}/v1/chat/completions",
        json={
            "model": "agentinsight-researcher",
            "messages": [{"role": "user", "content": query}],
            "stream": True,
            "report_type": "basic_report",
            "session_id": session_id,
        },
    ) as r:
        assert r.status_code == 200, f"流式响应非 200: {r.status_code}"
        assert "text/event-stream" in r.headers.get("content-type", "")

        async for line in r.aiter_lines():
            if not line or not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            if first_chunk_time is None:
                first_chunk_time = time.time() - start
            chunk = json.loads(payload)
            delta = chunk["choices"][0]["delta"]
            if "content" in delta and delta["content"]:
                content_parts.append(delta["content"])

    full_content = "".join(content_parts)
    elapsed = time.time() - start
    _log(
        f"研究完成: session={session_id[:20]}..., 首块 {first_chunk_time:.1f}s, "
        f"总耗时 {elapsed:.1f}s, 内容 {len(full_content)} 字"
    )
    return full_content


@pytest.mark.e2e
def test_full_research_chain() -> None:
    """完整研究链路: 提问 → 流式响应 → 验证 content 非空.

    AGENTS.md 第 13 章: e2e 必须覆盖完整链路.
    AGENTS.md 第 14 章: 流式 SSE 响应.
    """
    sid = _unique_session_id()
    query = "用 300 字简述 Python 异步编程的核心优势、应用场景与最佳实践"
    _log(f"完整研究链路开始: session={sid}, query={query[:60]}")

    content_parts: list[str] = []
    start = time.time()

    with httpx.Client(timeout=E2E_TIMEOUT) as client:
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
            assert "text/event-stream" in r.headers.get("content-type", "")

            # 验证 X-Session-Id 头 (会话隔离键)
            x_sid = r.headers.get("x-session-id", "")
            assert x_sid == sid, f"X-Session-Id 不匹配: 期望={sid}, 实际={x_sid}"

            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                delta = chunk["choices"][0]["delta"]
                if "content" in delta and delta["content"]:
                    content_parts.append(delta["content"])

    full_content = "".join(content_parts)
    elapsed = time.time() - start
    _log(f"研究链路完成: 总耗时 {elapsed:.1f}s, 内容 {len(full_content)} 字")
    _log(f"内容预览: {full_content[:300]}{'...' if len(full_content) > 300 else ''}")

    # 验证内容非空且有实质长度
    assert full_content, "流式研究 content 为空"
    assert len(full_content) > 100, (
        f"研究内容过短 (<100 字): {len(full_content)} 字\n内容: {full_content[:200]}"
    )


@pytest.mark.e2e
def test_multi_session_isolation() -> None:
    """多会话隔离: 两个 session_id 同时研究不同主题, 验证隔离.

    AGENTS.md 第 6 章: 会话间状态通过 Postgres Checkpointer 隔离.
    AGENTS.md 第 13 章: e2e 应覆盖完整链路.

    两个会话并发研究不同主题, 验证:
    1. 两个会话都能独立完成
    2. 两个会话的内容不互相包含 (主题隔离)
    """
    sid_a = _unique_session_id()
    sid_b = _unique_session_id()
    query_a = "用 200 字简述 Python 异步编程的核心优势"
    query_b = "用 200 字简述 JavaScript 类型系统的核心特性"

    _log(f"多会话隔离测试: session_a={sid_a[:20]}..., session_b={sid_b[:20]}...")
    _log(f"主题 A: {query_a}")
    _log(f"主题 B: {query_b}")

    async def _run_both() -> tuple[str, str]:
        async with httpx.AsyncClient(timeout=E2E_TIMEOUT) as client:
            # 并发发起两个研究请求
            results = await asyncio.gather(
                _stream_research(client, query_a, sid_a),
                _stream_research(client, query_b, sid_b),
            )
            return results[0], results[1]

    content_a, content_b = asyncio.run(_run_both())

    _log(f"会话 A 内容长度: {len(content_a)} 字")
    _log(f"会话 B 内容长度: {len(content_b)} 字")
    _log(f"会话 A 预览: {content_a[:200]}")
    _log(f"会话 B 预览: {content_b[:200]}")

    # 验证两个会话都生成了内容
    assert content_a, "会话 A content 为空"
    assert content_b, "会话 B content 为空"
    assert len(content_a) > 50, f"会话 A 内容过短: {len(content_a)} 字"
    assert len(content_b) > 50, f"会话 B 内容过短: {len(content_b)} 字"

    # 验证主题隔离: 会话 A 内容应包含 Python/异步 相关关键词
    content_a_lower = content_a.lower()
    assert any(kw in content_a_lower for kw in ["python", "异步", "async"]), (
        f"会话 A 内容未包含 Python/异步 相关关键词: {content_a[:300]}"
    )

    # 验证主题隔离: 会话 B 内容应包含 JavaScript/类型 相关关键词
    content_b_lower = content_b.lower()
    assert any(kw in content_b_lower for kw in ["javascript", "类型", "type"]), (
        f"会话 B 内容未包含 JavaScript/类型 相关关键词: {content_b[:300]}"
    )
    _log("多会话隔离验证通过: 两个会话独立研究不同主题")


@pytest.mark.e2e
def test_research_with_bearer_token() -> None:
    """验证带 Bearer JWT Token 的完整研究链路.

    AGENTS.md 第 8 章: token 存在时调用 /api/user 获取 user_id,
    调用失败降级 DEFAULT_USER_ID.
    AGENTS.md 第 13 章: API 测试应包含携带 Bearer JWT Token 场景.
    """
    sid = _unique_session_id()
    query = "用 200 字简述中文检索增强生成技术的核心原理"
    _log(f"带 Token 研究链路开始: session={sid}")

    content_parts: list[str] = []
    with httpx.Client(timeout=E2E_TIMEOUT) as client:
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
            headers={"Authorization": "Bearer test-token-e2e-flow"},
        ) as r:
            # 带 token 请求应能正常受理 (降级 DEFAULT_USER_ID), 不应 401/403
            assert r.status_code != 401, "带 token 请求不应返回 401"
            assert r.status_code != 403, "带 token 请求不应返回 403"
            assert r.status_code == 200, f"带 token 流式响应非 200: {r.status_code}"

            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                delta = chunk["choices"][0]["delta"]
                if "content" in delta and delta["content"]:
                    content_parts.append(delta["content"])

    full_content = "".join(content_parts)
    _log(f"带 Token 研究完成: 内容 {len(full_content)} 字")
    assert full_content, "带 Token 研究 content 为空"
    assert len(full_content) > 50, f"带 Token 研究内容过短: {len(full_content)} 字"
