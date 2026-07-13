"""回归测试: basic_report 完整研究流程.

- 回归测试在 docker compose up -d 且全部容器 service_healthy 后执行
- 回归测试为合并 main 前门禁, 不应跳过
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

# 测试目标地址从环境变量注入
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")

# 回归测试超时 300s (basic_report 研究 3-5 分钟)
REGRESSION_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)

# 报告内容最小长度 (字符)
MIN_CONTENT_LENGTH = 200


def _unique_session_id() -> str:
    """生成唯一 session_id (session_id=test_regression_*)."""
    return f"test_regression_{uuid.uuid4().hex[:12]}"


def _log(msg: str) -> None:
    """带时间戳输出, 便于追踪长流程进度."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


@pytest.mark.regression
def test_basic_report_non_stream() -> None:
    """basic_report 非流式完整流程: stream=false → 200 + content 非空 + >200 字.

    回归测试为合并门禁.
    OpenAI 兼容端点非流式响应.
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

    流式 SSE + 完整研究链路.
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


# ========== 异步回归测试 (httpx.AsyncClient, 仅验证 HTTP 状态码/响应头, 不依赖完整 LLM 研究) ==========
# 新增测试不依赖外部 LLM 调用, 仅验证 HTTP 状态码而非内容.
# 流式研究请求的 StreamingResponse 在查询分类后立即返回 200 + headers,
# 实际研究 (SMART LLM/检索) 在流式生成器中执行, 不消费流式体即不阻塞.

# 异步测试超时 (仅验证 HTTP 头/状态码, 不等待完整研究)
ASYNC_TEST_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)


@pytest.mark.regression
async def test_detailed_report_stream_headers_accepted() -> None:
    """detailed_report 流式路由: stream=true → 200 + text/event-stream + X-Session-Id.

    覆盖 report_type=detailed_report. 仅验证响应头 (StreamingResponse 立即返回),
    不消费流式体, 不依赖完整 SMART LLM 研究.
    """
    sid = _unique_session_id()
    _log(f"detailed_report 流式开始: session={sid}")
    async with httpx.AsyncClient(timeout=ASYNC_TEST_TIMEOUT) as client:
        async with client.stream(
            "POST",
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [
                    {"role": "user", "content": "分析 Python 异步编程的核心优势与应用场景"}
                ],
                "stream": True,
                "report_type": "detailed_report",
                "session_id": sid,
            },
        ) as r:
            assert r.status_code == 200, f"detailed_report 流式响应非 200: {r.status_code}"
            content_type = r.headers.get("content-type", "")
            assert "text/event-stream" in content_type, (
                f"content-type 非 text/event-stream: {content_type}"
            )
            assert r.headers.get("x-session-id") == sid, (
                f"X-Session-Id 不匹配: 期望={sid}, 实际={r.headers.get('x-session-id')}"
            )
    _log(f"detailed_report 流式响应头验证通过: session={sid}")


@pytest.mark.regression
async def test_summary_report_stream_headers_accepted() -> None:
    """summary 流式路由: stream=true → 200 + text/event-stream.

    覆盖 report_type=summary (ResearchConductor._conduct_summary).
    """
    sid = _unique_session_id()
    _log(f"summary 流式开始: session={sid}")
    async with httpx.AsyncClient(timeout=ASYNC_TEST_TIMEOUT) as client:
        async with client.stream(
            "POST",
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "简述 Rust 语言的内存安全机制"}],
                "stream": True,
                "report_type": "summary",
                "session_id": sid,
            },
        ) as r:
            assert r.status_code == 200, f"summary 流式响应非 200: {r.status_code}"
            assert "text/event-stream" in r.headers.get("content-type", ""), (
                f"content-type 非 text/event-stream: {r.headers.get('content-type')}"
            )
    _log(f"summary 流式响应头验证通过: session={sid}")


@pytest.mark.regression
async def test_subtopics_report_stream_headers_accepted() -> None:
    """subtopics 流式路由: stream=true → 200 + text/event-stream.

    覆盖 report_type=subtopics (ResearchConductor._conduct_subtopics).
    """
    sid = _unique_session_id()
    _log(f"subtopics 流式开始: session={sid}")
    async with httpx.AsyncClient(timeout=ASYNC_TEST_TIMEOUT) as client:
        async with client.stream(
            "POST",
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "梳理 Kubernetes 网络插件的核心子主题"}],
                "stream": True,
                "report_type": "subtopics",
                "session_id": sid,
            },
        ) as r:
            assert r.status_code == 200, f"subtopics 流式响应非 200: {r.status_code}"
            assert "text/event-stream" in r.headers.get("content-type", ""), (
                f"content-type 非 text/event-stream: {r.headers.get('content-type')}"
            )
    _log(f"subtopics 流式响应头验证通过: session={sid}")


@pytest.mark.regression
async def test_deep_research_stream_headers_accepted() -> None:
    """deep_research 流式路由: stream=true → 200 + text/event-stream.

    覆盖 report_type=deep_research (research_mode=deep, 递归深度研究).
    仅验证响应头, 不等待递归研究完成.
    """
    sid = _unique_session_id()
    _log(f"deep_research 流式开始: session={sid}")
    async with httpx.AsyncClient(timeout=ASYNC_TEST_TIMEOUT) as client:
        async with client.stream(
            "POST",
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [
                    {"role": "user", "content": "深入研究大语言模型在代码生成领域的最新进展"}
                ],
                "stream": True,
                "report_type": "deep_research",
                "session_id": sid,
            },
        ) as r:
            assert r.status_code == 200, f"deep_research 流式响应非 200: {r.status_code}"
            assert "text/event-stream" in r.headers.get("content-type", ""), (
                f"content-type 非 text/event-stream: {r.headers.get('content-type')}"
            )
    _log(f"deep_research 流式响应头验证通过: session={sid}")


@pytest.mark.regression
async def test_invalid_report_type_list_returns_422() -> None:
    """report_type 为列表类型 → 422 (Pydantic 校验失败, 错误降级).

    所有外部输入经 Pydantic 校验.
    report_type 字段为 str | None, 传入 list 应被拒绝.
    """
    sid = _unique_session_id()
    async with httpx.AsyncClient(timeout=ASYNC_TEST_TIMEOUT) as client:
        r = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
                "session_id": sid,
                "report_type": ["basic_report"],  # list, 非 str
            },
        )
    assert r.status_code == 422, (
        f"report_type=list 应返回 422, 实际: {r.status_code} {r.text[:200]}"
    )


@pytest.mark.regression
async def test_unknown_report_type_string_stream_no_5xx() -> None:
    """未知 report_type 字符串流式: stream=true → 200 (降级为 basic, 不 5xx 崩溃).

    未知 report_type 应降级为默认值, 不应崩溃.
    routes.py 将未知 type 映射为 research_mode=basic, StreamingResponse 立即返回.
    """
    sid = _unique_session_id()
    _log(f"未知 report_type 流式开始: session={sid}")
    async with httpx.AsyncClient(timeout=ASYNC_TEST_TIMEOUT) as client:
        async with client.stream(
            "POST",
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "分析 Go 语言的并发模型"}],
                "stream": True,
                "report_type": "unknown_type_xyz",
                "session_id": sid,
            },
        ) as r:
            assert r.status_code < 500, f"未知 report_type 不应 5xx, 实际: {r.status_code}"
    _log(f"未知 report_type 降级验证通过: status={r.status_code}, session={sid}")
