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
本文件为 API 层 e2e, 通过 httpx.AsyncClient 直接打 OpenAI 兼容端点.

覆盖 8 个核心场景 (任务要求):
1. 完整研究流程 (非流式) → 200 + 报告结构
2. 完整研究流程 (流式 SSE) → 200 + SSE chunks + finish_reason=stop
3. 多会话并发 (3 个 session_id) → 验证不串扰
4. 文件上传 + Chat 联动 → 上传 → 用 file_id 提问
5. 模型列表 GET /v1/models → 返回 agentinsight-researcher
6. Agent 发现 GET /.well-known/agent-discovery.json → schema 完整
7. 健康检查 GET /health → status=ok + service=agentinsight-researcher
8. 错误处理 → 无效 JSON 422 + 不存在端点 404
"""

from __future__ import annotations

import asyncio
import io
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

# 短超时 (用于健康检查/模型列表/Agent 发现等静态端点, 不走研究流程)
QUICK_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


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
    *,
    token: str | None = None,
) -> tuple[str, list[dict[str, object]], str | None]:
    """发起流式研究请求, 返回 (完整 content, 全部 chunks, finish_reason).

    AGENTS.md 第 14 章: 统一调用 POST /v1/chat/completions, 请求体带 stream: true.
    返回 chunks 供调用方断言 SSE 帧结构 (首块 role / 末块 finish_reason).

    Args:
        client: httpx.AsyncClient
        query: 研究查询
        session_id: 会话 ID (thread_id)
        token: 可选 Bearer JWT Token, 提供时携带 Authorization 头
    """
    content_parts: list[str] = []
    chunks: list[dict[str, object]] = []
    finish_reason: str | None = None
    first_chunk_time: float | None = None
    start = time.time()
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

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
        headers=headers,
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
            chunks.append(chunk)
            delta = chunk["choices"][0]["delta"]
            if "content" in delta and delta["content"]:
                content_parts.append(delta["content"])
            fr = chunk["choices"][0].get("finish_reason")
            if fr:
                finish_reason = fr

    full_content = "".join(content_parts)
    elapsed = time.time() - start
    _log(
        f"研究完成: session={session_id[:20]}..., 首块 "
        f"{first_chunk_time:.1f}s, 总耗时 {elapsed:.1f}s, "
        f"内容 {len(full_content)} 字, 帧数 {len(chunks)}"
    )
    return full_content, chunks, finish_reason


# ========== 场景 1: 完整研究流程 (非流式) ==========


@pytest.mark.e2e
async def test_full_research_chain_non_stream() -> None:
    """完整研究流程 (非流式): 提问 → 200 → 验证响应包含报告结构.

    AGENTS.md 第 13 章: e2e 必须覆盖完整链路.
    AGENTS.md 第 14 章: OpenAI 兼容非流式响应.
    验证响应体关键字段: object/choices/message/content/finish_reason/usage.
    """
    sid = _unique_session_id()
    query = "用 300 字简述 Python 异步编程的核心优势、应用场景与最佳实践"
    _log(f"非流式研究开始: session={sid}, query={query[:60]}")

    async with httpx.AsyncClient(timeout=E2E_TIMEOUT) as client:
        r = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": query}],
                "stream": False,
                "report_type": "basic_report",
                "session_id": sid,
            },
        )

    assert r.status_code == 200, f"非流式响应非 200: {r.status_code} {r.text[:300]}"
    data = r.json()

    # OpenAI 兼容响应结构
    assert data["object"] == "chat.completion", f"object 非 chat.completion: {data['object']}"
    assert "id" in data, "缺少 id 字段"
    assert "created" in data, "缺少 created 字段"
    assert data["model"] == "agentinsight-researcher", f"model 不匹配: {data['model']}"

    # choices 结构
    assert len(data["choices"]) == 1, f"choices 长度非 1: {len(data['choices'])}"
    choice = data["choices"][0]
    assert choice["message"]["role"] == "assistant"
    assert choice["finish_reason"] == "stop", f"finish_reason 非 stop: {choice['finish_reason']}"

    # 报告内容: 非空且有实质长度 (basic_report 应生成完整报告)
    content = choice["message"].get("content", "")
    assert content, "非流式响应 content 为空"
    assert len(content) > 100, f"报告内容过短 (<100 字): {len(content)} 字\n内容: {content[:200]}"
    _log(f"非流式报告内容长度: {len(content)} 字")
    _log(f"内容预览: {content[:300]}{'...' if len(content) > 300 else ''}")

    # usage 字段 (含 cost_usd)
    assert "usage" in data, "缺少 usage 字段"
    assert "total_tokens" in data["usage"], "usage 缺少 total_tokens"
    assert data["usage"]["total_tokens"] > 0, f"total_tokens 应 > 0: {data['usage']}"

    # 报告结构: 至少包含段落 (markdown 报告应含 \n\n 分隔的段落或标题符号)
    has_structure = ("\n\n" in content) or ("#" in content) or ("-" in content)
    assert has_structure, f"报告内容缺乏结构 (无段落/标题/列表): {content[:300]}"


# ========== 场景 2: 完整研究流程 (流式 SSE) ==========


@pytest.mark.e2e
async def test_full_research_chain_stream() -> None:
    """完整研究流程 (流式 SSE): 提问 → 200 → 验证 SSE chunks → 验证 finish_reason=stop.

    AGENTS.md 第 13 章: e2e 必须覆盖完整链路.
    AGENTS.md 第 14 章: 流式 SSE 响应.
    验证: 首块 role=assistant + 末块 finish_reason=stop + content 非空.
    """
    sid = _unique_session_id()
    query = "用 300 字简述 Python 异步编程的核心优势、应用场景与最佳实践"
    _log(f"流式研究链路开始: session={sid}, query={query[:60]}")

    async with httpx.AsyncClient(timeout=E2E_TIMEOUT) as client:
        content, chunks, finish_reason = await _stream_research(client, query, sid)

    # SSE 帧结构验证: 至少有首块(role) + 内容块 + 末块(finish_reason)
    assert len(chunks) >= 3, f"SSE 帧数不足: {len(chunks)}"

    # 首块应含 role=assistant
    first_delta = chunks[0]["choices"][0]["delta"]
    assert first_delta.get("role") == "assistant", f"首块 role 非 assistant: {first_delta}"

    # 末块应含 finish_reason=stop (AGENTS.md 第 14 章: SSE 末块标记)
    assert finish_reason == "stop", f"finish_reason 非 stop: {finish_reason}\n末块: {chunks[-1]}"

    # content 非空且有实质长度
    assert content, "流式研究 content 为空"
    assert len(content) > 100, f"研究内容过短 (<100 字): {len(content)} 字\n内容: {content[:200]}"
    _log(f"流式研究完成: content {len(content)} 字, 帧数 {len(chunks)}, finish={finish_reason}")
    _log(f"内容预览: {content[:300]}{'...' if len(content) > 300 else ''}")

    # 报告结构验证 (与场景 1 一致)
    has_structure = ("\n\n" in content) or ("#" in content) or ("-" in content)
    assert has_structure, f"流式报告内容缺乏结构: {content[:300]}"


# ========== 场景 3: 多会话并发 (3 个 session_id) ==========


@pytest.mark.e2e
async def test_multi_session_concurrent_isolation() -> None:
    """多会话并发隔离: 3 个不同 session_id 并发请求 → 验证响应不串扰.

    AGENTS.md 第 6 章: 会话间状态通过 Postgres Checkpointer 隔离.
    AGENTS.md 第 13 章: e2e 应覆盖完整链路.

    3 个会话并发研究不同主题, 验证:
    1. 3 个会话都能独立完成
    2. 3 个会话的内容互不包含 (主题隔离, 不串扰)
    """
    sid_a = _unique_session_id()
    sid_b = _unique_session_id()
    sid_c = _unique_session_id()
    query_a = "用 200 字简述 Python 异步编程的核心优势"
    query_b = "用 200 字简述 JavaScript 类型系统的核心特性"
    query_c = "用 200 字简述 Rust 所有权机制的核心规则"

    _log(f"多会话并发测试 (3 个): a={sid_a[:20]}..., b={sid_b[:20]}..., c={sid_c[:20]}...")
    _log(f"主题 A: {query_a}")
    _log(f"主题 B: {query_b}")
    _log(f"主题 C: {query_c}")

    async with httpx.AsyncClient(timeout=E2E_TIMEOUT) as client:
        # 3 个会话并发 (asyncio.gather 真并发)
        results = await asyncio.gather(
            _stream_research(client, query_a, sid_a),
            _stream_research(client, query_b, sid_b),
            _stream_research(client, query_c, sid_c),
        )

    content_a, _, _ = results[0]
    content_b, _, _ = results[1]
    content_c, _, _ = results[2]

    _log(f"会话 A 内容长度: {len(content_a)} 字")
    _log(f"会话 B 内容长度: {len(content_b)} 字")
    _log(f"会话 C 内容长度: {len(content_c)} 字")

    # 验证 3 个会话都生成了内容
    assert content_a, "会话 A content 为空"
    assert content_b, "会话 B content 为空"
    assert content_c, "会话 C content 为空"
    assert len(content_a) > 50, f"会话 A 内容过短: {len(content_a)} 字"
    assert len(content_b) > 50, f"会话 B 内容过短: {len(content_b)} 字"
    assert len(content_c) > 50, f"会话 C 内容过短: {len(content_c)} 字"

    # 主题隔离验证: 各会话内容应包含对应主题关键词
    content_a_lower = content_a.lower()
    assert any(kw in content_a_lower for kw in ["python", "异步", "async"]), (
        f"会话 A 内容未包含 Python/异步 相关关键词: {content_a[:300]}"
    )

    content_b_lower = content_b.lower()
    assert any(kw in content_b_lower for kw in ["javascript", "类型", "type", "js"]), (
        f"会话 B 内容未包含 JavaScript/类型 相关关键词: {content_b[:300]}"
    )

    content_c_lower = content_c.lower()
    assert any(kw in content_c_lower for kw in ["rust", "所有权", "ownership"]), (
        f"会话 C 内容未包含 Rust/所有权 相关关键词: {content_c[:300]}"
    )

    _log("多会话并发隔离验证通过: 3 个会话独立研究不同主题")


# ========== 场景 4: 文件上传 + Chat 联动 ==========


@pytest.mark.e2e
async def test_file_upload_then_chat() -> None:
    """文件上传 + Chat 联动: 上传文件 → 用 file_id 提问 → 验证响应.

    AGENTS.md 第 7 章: 用户私有数据按 agent_id + user_id 隔离.
    AGENTS.md 第 13 章: e2e 应覆盖完整链路.
    AGENTS.md 第 14 章: 测试页面应能上传文件作为研究数据源.

    流程:
    1. POST /v1/files 上传 .txt 文件 → 201 + file_id
    2. POST /v1/chat/completions 携带 uploaded_files=[file_id] → 200 + 报告
    """
    sid = _unique_session_id()
    file_content = (
        "Python 异步编程核心要点:\n"
        "1. asyncio 事件循环\n"
        "2. async/await 语法\n"
        "3. 协程 (coroutine) 并发\n"
        "4. aiohttp/httpx 异步 HTTP 客户端\n"
        "5. 适用于 I/O 密集型场景: 网络请求/数据库/文件读写\n"
    ).encode()
    _log(f"文件上传+Chat 测试: session={sid}, 文件大小 {len(file_content)} 字节")

    async with httpx.AsyncClient(timeout=E2E_TIMEOUT) as client:
        # 步骤 1: 上传文件
        files = {"file": ("test_research_source.txt", io.BytesIO(file_content), "text/plain")}
        r_upload = await client.post(f"{AGENT_URL}/v1/files", files=files)

    assert r_upload.status_code == 201, (
        f"文件上传非 201: {r_upload.status_code} {r_upload.text[:300]}"
    )
    upload_data = r_upload.json()
    assert "file_id" in upload_data, f"上传响应缺少 file_id: {upload_data}"
    assert upload_data["filename"] == "test_research_source.txt"
    assert upload_data["size_bytes"] == len(file_content)
    assert upload_data["extension"] == "txt"
    file_id = upload_data["file_id"]
    _log(f"文件上传成功: file_id={file_id}")

    # 步骤 2: 用 file_id 提问 (uploaded_files 联动)
    query = "基于上传的文件内容, 用 200 字总结 Python 异步编程的核心要点"
    _log(f"联动提问: query={query[:60]}")

    async with httpx.AsyncClient(timeout=E2E_TIMEOUT) as client:
        r_chat = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": query}],
                "stream": False,
                "report_type": "basic_report",
                "session_id": sid,
                "uploaded_files": [file_id],
            },
        )

    assert r_chat.status_code == 200, f"联动 Chat 非 200: {r_chat.status_code} {r_chat.text[:300]}"
    chat_data = r_chat.json()
    assert chat_data["object"] == "chat.completion"
    assert chat_data["choices"][0]["finish_reason"] == "stop"

    content = chat_data["choices"][0]["message"].get("content", "")
    assert content, "联动 Chat 响应 content 为空"
    assert len(content) > 50, f"联动 Chat 内容过短: {len(content)} 字"
    _log(f"联动 Chat 完成: content {len(content)} 字")
    _log(f"内容预览: {content[:300]}{'...' if len(content) > 300 else ''}")


# ========== 场景 5: 模型列表 GET /v1/models ==========


@pytest.mark.e2e
async def test_models_endpoint() -> None:
    """模型列表: GET /v1/models → 200 + 返回 agentinsight-researcher.

    AGENTS.md 第 13 章: e2e 应覆盖完整链路 (含静态端点契约).
    AGENTS.md 第 14 章: OpenAI 兼容端点.
    """
    async with httpx.AsyncClient(timeout=QUICK_TIMEOUT) as client:
        r = await client.get(f"{AGENT_URL}/v1/models")

    assert r.status_code == 200, f"/v1/models 非 200: {r.status_code} {r.text[:200]}"
    data = r.json()

    # OpenAI 兼容响应结构
    assert data["object"] == "list", f"object 非 list: {data['object']}"
    assert len(data["data"]) > 0, "data 列表为空"

    # 应包含 agentinsight-researcher 模型
    model_ids = [m["id"] for m in data["data"]]
    assert "agentinsight-researcher" in model_ids, (
        f"模型列表缺少 agentinsight-researcher: {model_ids}"
    )

    # 模型对象结构校验
    researcher_model = next(m for m in data["data"] if m["id"] == "agentinsight-researcher")
    assert researcher_model["object"] == "model", f"object 非 model: {researcher_model}"
    assert "created" in researcher_model, "模型缺少 created 字段"
    assert "owned_by" in researcher_model, "模型缺少 owned_by 字段"
    _log(f"模型列表验证通过: {model_ids}")


# ========== 场景 6: Agent 发现 GET /.well-known/agent-discovery.json ==========


@pytest.mark.e2e
async def test_agent_discovery() -> None:
    """Agent 发现: GET /.well-known/agent-discovery.json → 200 + schema 完整.

    AGENTS.md 第 8 章: auth 含 bearer_jwt (可选) 与 none (匿名降级).
    AGENTS.md 第 11 章: 公开发现端点, 无需鉴权.
    AGENTS.md 第 14 章: Agent Discovery Protocol 公开发现端点.
    """
    async with httpx.AsyncClient(timeout=QUICK_TIMEOUT) as client:
        r = await client.get(f"{AGENT_URL}/.well-known/agent-discovery.json")

    assert r.status_code == 200, f"agent-discovery 非 200: {r.status_code} {r.text[:200]}"
    data = r.json()

    # 必要字段校验 (src/api/agent_discovery.py)
    assert "name" in data, f"缺少 name: {data}"
    assert "version" in data, f"缺少 version: {data}"
    assert "description" in data, f"缺少 description: {data}"
    assert "services" in data, f"缺少 services: {data}"
    assert "capabilities" in data, f"缺少 capabilities: {data}"
    assert "auth" in data, f"缺少 auth: {data}"

    # services 应包含核心端点
    assert isinstance(data["services"], list), f"services 非列表: {type(data['services'])}"
    assert len(data["services"]) > 0, "services 列表为空"
    service_paths = [s.get("path") for s in data["services"]]
    assert "/v1/chat/completions" in service_paths, (
        f"services 缺少 /v1/chat/completions: {service_paths}"
    )
    assert "/health" in service_paths, f"services 缺少 /health: {service_paths}"

    # 每个 service 应含 name/path/method/description
    for svc in data["services"]:
        assert "name" in svc, f"service 缺少 name: {svc}"
        assert "path" in svc, f"service 缺少 path: {svc}"
        assert "method" in svc, f"service 缺少 method: {svc}"

    # capabilities 应非空
    assert isinstance(data["capabilities"], list), (
        f"capabilities 非列表: {type(data['capabilities'])}"
    )
    assert len(data["capabilities"]) > 0, "capabilities 列表为空"

    # auth 应支持 bearer_jwt 和 none (AGENTS.md 第 8 章: 匿名降级)
    assert isinstance(data["auth"], list), f"auth 非列表: {type(data['auth'])}"
    assert "bearer_jwt" in data["auth"], f"auth 缺少 bearer_jwt: {data['auth']}"
    assert "none" in data["auth"], f"auth 缺少 none: {data['auth']}"
    _log(f"Agent Discovery 验证通过: name={data['name']}, version={data['version']}")


# ========== 场景 7: 健康检查 GET /health ==========


@pytest.mark.e2e
async def test_health_endpoint() -> None:
    """健康检查: GET /health → 200 + status=ok + service=agentinsight-researcher.

    AGENTS.md 第 13 章: e2e 前置依赖容器栈 service_healthy.
    AGENTS.md 第 11 章: 安全响应头中间件不可绕过 (附带验证).
    """
    async with httpx.AsyncClient(timeout=QUICK_TIMEOUT) as client:
        r = await client.get(f"{AGENT_URL}/health")

    assert r.status_code == 200, f"/health 非 200: {r.status_code} {r.text[:200]}"
    body = r.json()

    # 健康检查响应字段 (server.py: status + service + version)
    assert body.get("status") == "ok", f"/health status 异常: {body}"
    assert body.get("service") == "agentinsight-researcher", f"/health service 异常: {body}"
    assert "version" in body, f"/health 缺少 version 字段: {body}"
    _log(f"/health 验证通过: {body}")

    # 附带验证安全响应头 (AGENTS.md 第 11 章: 不可绕过)
    assert r.headers.get("x-content-type-options") == "nosniff", (
        f"X-Content-Type-Options 非 nosniff: {r.headers.get('x-content-type-options')}"
    )
    assert r.headers.get("x-frame-options") == "DENY", (
        f"X-Frame-Options 非 DENY: {r.headers.get('x-frame-options')}"
    )


# ========== 场景 8: 错误处理 (无效 JSON + 不存在端点) ==========


@pytest.mark.e2e
async def test_error_handling_invalid_json_and_404() -> None:
    """错误处理: 无效 JSON → 422 + 不存在端点 → 404.

    AGENTS.md 第 11 章: 所有外部输入经 Pydantic 校验.
    AGENTS.md 第 13 章: API 测试应覆盖错误码.
    AGENTS.md 第 14 章: 错误处理应在页面显式提示, 不推荐静默失败.
    """
    async with httpx.AsyncClient(timeout=QUICK_TIMEOUT) as client:
        # 子场景 1: 无效 JSON body → 422 (Pydantic 校验失败)
        # 发送非法 JSON (content-type=application/json 但 body 非合法 JSON)
        r_invalid_json = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            content="{not a valid json",
            headers={"content-type": "application/json"},
        )
        assert r_invalid_json.status_code == 422, (
            f"无效 JSON 应返回 422, 实际: {r_invalid_json.status_code} {r_invalid_json.text[:200]}"
        )

        # 子场景 2: 缺少必填字段 (messages) → 422
        r_missing_field = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={"model": "agentinsight-researcher"},  # 缺少 messages
        )
        assert r_missing_field.status_code == 422, (
            f"缺少 messages 字段应返回 422, 实际: {r_missing_field.status_code}"
        )

        # 子场景 3: 不存在的端点 → 404
        r_not_found = await client.get(f"{AGENT_URL}/v1/nonexistent-endpoint-xyz")
        assert r_not_found.status_code == 404, (
            f"不存在端点应返回 404, 实际: {r_not_found.status_code} {r_not_found.text[:200]}"
        )

        # 子场景 4: 不存在的报告 report_id → 404
        # report_id 非 UUID 格式时走 deprecated session_id 兼容分支, 仍应返回 404
        r_report_not_found = await client.get(
            f"{AGENT_URL}/v1/reports/nonexistent-report-id/download"
        )
        assert r_report_not_found.status_code == 404, (
            f"不存在报告应返回 404, 实际: {r_report_not_found.status_code}"
        )

    _log("错误处理验证通过: 无效 JSON → 422, 缺字段 → 422, 不存在端点 → 404, 不存在报告 → 404")


# ========== 场景 9: 带 Bearer JWT Token 的完整研究链路 (AGENTS.md 第 13 章) ==========


@pytest.mark.e2e
async def test_research_with_bearer_token() -> None:
    """验证带 Bearer JWT Token 的完整研究链路.

    AGENTS.md 第 8 章: token 存在时调用 /api/user 获取 user_id,
    调用失败降级 IP-based UserId.
    AGENTS.md 第 13 章: API 测试应包含携带 Bearer JWT Token 场景.
    """
    sid = _unique_session_id()
    query = "用 200 字简述中文检索增强生成技术的核心原理"
    _log(f"带 Token 研究链路开始: session={sid}")

    async with httpx.AsyncClient(timeout=E2E_TIMEOUT) as client:
        content, chunks, finish_reason = await _stream_research(
            client, query, sid, token="test-token-e2e-flow"
        )

    # 带 token 请求应能正常受理 (降级 IP-based UserId), 不应 401/403
    assert content, "带 Token 研究 content 为空"
    assert len(content) > 50, f"带 Token 研究内容过短: {len(content)} 字"
    assert finish_reason == "stop", f"带 Token 研究 finish_reason 非 stop: {finish_reason}"
    _log(f"带 Token 研究完成: content {len(content)} 字, finish={finish_reason}")
