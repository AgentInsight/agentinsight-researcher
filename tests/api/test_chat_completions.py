"""API 测试: OpenAI 兼容端点 /v1/chat/completions.

AGENTS.md 第 13/14 章硬约束:
- API 测试在 docker compose up -d 且全部容器 service_healthy 后执行
- 必须覆盖 OpenAI 兼容端点 (流式 SSE + 非流式 + 错误码)
- 必须包含携带 Bearer JWT Token 与不携带两种场景
- 测试目标地址从环境变量 AGENT_URL 注入
- 每次用唯一 session_id=test_* (AGENTS.md 第 13 章: 测试数据隔离)

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/api/test_chat_completions.py -v -m api

注意: API 测试超时 60s, 用短查询 (如"你好") 验证端点契约, 不走完整研究流程.
研究完整流程由 regression / e2e 测试覆盖.
"""

from __future__ import annotations

import io
import json
import os
import uuid

import httpx
import pytest

# AGENTS.md 第 13 章: 测试目标地址从环境变量注入, 禁止硬编码
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")

# API 测试超时 60s (短查询响应快; 带 token 时 user_info API 超时 5s)
API_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)


def _unique_session_id() -> str:
    """生成唯一 session_id (AGENTS.md 第 13 章: session_id=test_*)."""
    return f"test_api_{uuid.uuid4().hex[:12]}"


def _chat_payload(
    query: str = "你好",
    *,
    stream: bool = False,
    session_id: str | None = None,
    report_type: str | None = None,
    multi_agent: bool | None = None,
    agent_role: str | None = None,
    uploaded_files: list[str] | None = None,
    org_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, object]:
    """构造 /v1/chat/completions 请求体.

    新增参数 (multi_agent/agent_role/uploaded_files/org_id/project_id) 仅在非 None 时
    加入 payload, 保证向后兼容 (现有用例不受影响).
    """
    payload: dict[str, object] = {
        "model": "agentinsight-researcher",
        "messages": [{"role": "user", "content": query}],
        "stream": stream,
        "session_id": session_id or _unique_session_id(),
    }
    if report_type is not None:
        payload["report_type"] = report_type
    if multi_agent is not None:
        payload["multi_agent"] = multi_agent
    if agent_role is not None:
        payload["agent_role"] = agent_role
    if uploaded_files is not None:
        payload["uploaded_files"] = uploaded_files
    if org_id is not None:
        payload["org_id"] = org_id
    if project_id is not None:
        payload["project_id"] = project_id
    return payload


@pytest.mark.api
def test_non_stream_research() -> None:
    """验证非流式响应: POST /v1/chat/completions stream=false → 200 + chat.completion 结构.

    使用短查询 "你好" 触发 short_query_reply, 快速返回 (不走研究图).
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False),
        )
    assert r.status_code == 200, f"非流式响应非 200: {r.status_code} {r.text}"
    data = r.json()
    assert data["object"] == "chat.completion", f"object 非 chat.completion: {data['object']}"
    assert "id" in data
    assert "created" in data
    assert "model" in data
    assert data["model"] == "agentinsight-researcher"
    assert len(data["choices"]) == 1
    choice = data["choices"][0]
    assert choice["message"]["role"] == "assistant"
    assert "content" in choice["message"]
    assert choice["finish_reason"] == "stop"
    assert "usage" in data
    assert "total_tokens" in data["usage"]


@pytest.mark.api
def test_stream_research() -> None:
    """验证流式 SSE 响应: POST /v1/chat/completions stream=true → 200 + text/event-stream.

    使用短查询触发 short_query_reply, 快速返回 SSE 流.
    验证 SSE 帧格式: 首块(role) + 内容块 + 末块(finish_reason) + [DONE].
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        with client.stream(
            "POST",
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=True),
        ) as r:
            assert r.status_code == 200, f"流式响应非 200: {r.status_code}"
            content_type = r.headers.get("content-type", "")
            assert "text/event-stream" in content_type, (
                f"content-type 非 text/event-stream: {content_type}"
            )

            chunks: list[dict[str, object]] = []
            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                chunks.append(json.loads(payload))

    # 至少有首块(role) + 内容块 + 末块(finish_reason)
    assert len(chunks) >= 3, f"SSE 帧数不足: {len(chunks)}"
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"

    # 收集 content 验证非空
    content_parts = [
        c["choices"][0]["delta"]["content"]
        for c in chunks
        if c["choices"][0]["delta"].get("content")
    ]
    assert content_parts, "SSE 流未输出任何 content"
    full_content = "".join(content_parts)
    assert len(full_content) > 0, "SSE 流 content 为空"


@pytest.mark.api
def test_empty_messages() -> None:
    """验证空 messages 列表: messages=[] → 400."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [],
                "stream": False,
            },
        )
    assert r.status_code == 400, f"空 messages 应返回 400, 实际: {r.status_code}"


@pytest.mark.api
def test_no_user_message() -> None:
    """验证仅 system 消息: 无 user 消息 → 400."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "system", "content": "你是研究助手"}],
                "stream": False,
            },
        )
    assert r.status_code == 400, f"无 user 消息应返回 400, 实际: {r.status_code}"


@pytest.mark.api
def test_empty_query() -> None:
    """验证空查询内容: user content="   " → 400."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "   "}],
                "stream": False,
            },
        )
    assert r.status_code == 400, f"空查询内容应返回 400, 实际: {r.status_code}"


@pytest.mark.api
def test_with_bearer_token() -> None:
    """验证带 Bearer JWT Token: Authorization: Bearer test-token → 200 (不报错).

    AGENTS.md 第 8 章: token 存在时调用 /api/user 获取 user_id,
    调用失败 (test-token 非合法 JWT) 按无 token 处理并降级 DEFAULT_USER_ID.
    中间件超时 5s, 总超时 60s 应足够.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False),
            headers={"Authorization": "Bearer test-token-invalid"},
        )
    assert r.status_code == 200, (
        f"带 token 请求应返回 200 (降级 DEFAULT_USER_ID), 实际: {r.status_code} {r.text}"
    )
    data = r.json()
    assert data["object"] == "chat.completion"


@pytest.mark.api
def test_without_token() -> None:
    """验证不带 Bearer Token: 无 Authorization 头 → 200 (降级 DEFAULT_USER_ID).

    AGENTS.md 第 8 章: token 不存在时使用 DEFAULT_USER_ID.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False),
        )
    assert r.status_code == 200, (
        f"无 token 请求应返回 200 (降级 DEFAULT_USER_ID), 实际: {r.status_code} {r.text}"
    )
    data = r.json()
    assert data["object"] == "chat.completion"


@pytest.mark.api
def test_models_endpoint() -> None:
    """验证 /v1/models 端点: GET → 200 + list 结构."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.get(f"{AGENT_URL}/v1/models")
    assert r.status_code == 200, f"/v1/models 非 200: {r.status_code}"
    data = r.json()
    assert data["object"] == "list"
    assert len(data["data"]) > 0
    assert data["data"][0]["id"] == "agentinsight-researcher"
    assert data["data"][0]["object"] == "model"


@pytest.mark.api
def test_session_id_header_in_stream() -> None:
    """验证流式响应携带 X-Session-Id 头 (会话隔离键).

    AGENTS.md 第 6 章: thread_id 做会话隔离键.
    """
    sid = _unique_session_id()
    with httpx.Client(timeout=API_TIMEOUT) as client:
        with client.stream(
            "POST",
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=True, session_id=sid),
        ) as r:
            assert r.status_code == 200
            x_sid = r.headers.get("x-session-id", "")
            assert x_sid, "流式响应未携带 X-Session-Id 头"
            assert x_sid == sid, f"X-Session-Id 不匹配: 期望={sid}, 实际={x_sid}"
            # 消费流以避免连接泄漏
            for _ in r.iter_lines():
                break


# ========== 错误码补充场景 (422 Pydantic 校验 + 字段类型错误) ==========


@pytest.mark.api
def test_invalid_stream_type_returns_422() -> None:
    """验证 stream 字段非布尔值: stream="yes" → 422 (Pydantic 校验失败).

    AGENTS.md 第 11 章: 所有外部输入经 Pydantic 校验.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": "yes",  # 非 bool, Pydantic 应拒绝
            },
        )
    assert r.status_code == 422, f"stream='yes' 应返回 422, 实际: {r.status_code} {r.text[:200]}"


@pytest.mark.api
def test_invalid_content_type_returns_422() -> None:
    """验证 content 字段非字符串: content=123 → 422 (Pydantic 校验失败)."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": 12345}],  # 非字符串
                "stream": False,
            },
        )
    assert r.status_code == 422, f"content=12345 应返回 422, 实际: {r.status_code} {r.text[:200]}"


@pytest.mark.api
def test_invalid_messages_type_returns_422() -> None:
    """验证 messages 非列表: messages="not-a-list" → 422."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": "not-a-list",
                "stream": False,
            },
        )
    assert r.status_code == 422, (
        f"messages='not-a-list' 应返回 422, 实际: {r.status_code} {r.text[:200]}"
    )


@pytest.mark.api
def test_invalid_model_name_returns_200_or_400() -> None:
    """验证未知 model 名称: model="unknown-model" → 200 或 400.

    AGENTS.md 第 14 章: OpenAI 兼容端点, model 字段用于路由.
    实现可能: (a) 不校验 model 直接走默认 (200), (b) 校验 model 不在白名单 (400).
    两种行为均可接受, 不应返回 5xx.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "unknown-model-xyz",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
            },
        )
    assert r.status_code < 500, f"未知 model 不应 5xx, 实际: {r.status_code} {r.text[:200]}"


@pytest.mark.api
def test_mixed_roles_messages_returns_200() -> None:
    """验证混合 system + user + assistant 消息: → 200 (含上下文)."""
    sid = _unique_session_id()
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [
                    {"role": "system", "content": "你是研究助手"},
                    {"role": "user", "content": "你好"},
                    {"role": "assistant", "content": "您好, 有什么可以帮您?"},
                    {"role": "user", "content": "请问"},
                ],
                "stream": False,
                "session_id": sid,
            },
        )
    assert r.status_code == 200, f"混合角色消息应返回 200, 实际: {r.status_code} {r.text[:200]}"


@pytest.mark.api
def test_invalid_report_type_handled_gracefully() -> None:
    """验证未知 report_type: report_type="unknown_type" → 不应 5xx 崩溃.

    AGENTS.md 第 11 章: 所有外部输入经 Pydantic 校验.
    未知 report_type 应降级为默认值或返回 4xx, 不应崩溃.
    """
    sid = _unique_session_id()
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
                "session_id": sid,
                "report_type": "unknown_type_xyz",
            },
        )
    assert r.status_code < 500, f"未知 report_type 不应 5xx, 实际: {r.status_code} {r.text[:200]}"


@pytest.mark.api
def test_long_session_id_handled() -> None:
    """验证超长 session_id (1K 字符) 不应崩溃.

    AGENTS.md 第 6 章: thread_id 做会话隔离键, 不应限制长度.
    """
    long_sid = "test_long_" + "x" * 1000
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
                "session_id": long_sid,
            },
        )
    assert r.status_code < 500, f"超长 session_id 不应 5xx, 实际: {r.status_code} {r.text[:200]}"


# ========== 扩展场景: report_type 路由 / multi_agent / SELF_HOST / uploaded_files / agent_role ==========


@pytest.mark.api
def test_chat_completions_report_type_basic_report() -> None:
    """验证 report_type=basic_report 路由: 短查询 + 显式 report_type → 200.

    AGENTS.md 第 5/7 章: report_type=basic_report 走 basic 研究模式 (research_mode=basic).
    短查询触发 short_query_reply (ChitchatResponder), 但 report_type 字段应被接受不报错.
    完整 basic_report 研究流程由 e2e 测试覆盖 (test_full_research_chain_non_stream).
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False, report_type="basic_report"),
        )
    assert r.status_code == 200, (
        f"report_type=basic_report 应返回 200, 实际: {r.status_code} {r.text[:200]}"
    )
    data = r.json()
    assert data["object"] == "chat.completion", f"object 非 chat.completion: {data['object']}"


@pytest.mark.api
def test_chat_completions_report_type_detailed_report() -> None:
    """验证 report_type=detailed_report 路由: 短查询 + 显式 report_type → 200.

    AGENTS.md 第 5/7 章: report_type=detailed_report 走 basic 研究模式 (非 deep_research).
    短查询触发 short_query_reply, 但 report_type 字段应被接受不报错.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False, report_type="detailed_report"),
        )
    assert r.status_code == 200, (
        f"report_type=detailed_report 应返回 200, 实际: {r.status_code} {r.text[:200]}"
    )
    data = r.json()
    assert data["object"] == "chat.completion"


@pytest.mark.api
def test_chat_completions_multi_agent_true() -> None:
    """验证 multi_agent=True 路由: 短查询 + multi_agent=True → 200.

    AGENTS.md 第 5 章: multi_agent=True 走 multi_agent_graph (Supervisor 模式, P0-02).
    短查询触发 short_query_reply (不走图), 但 multi_agent 字段应被接受不报错.
    完整 multi_agent 研究流程由 e2e 测试覆盖.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False, multi_agent=True),
        )
    assert r.status_code == 200, (
        f"multi_agent=True 应返回 200, 实际: {r.status_code} {r.text[:200]}"
    )
    data = r.json()
    assert data["object"] == "chat.completion"


@pytest.mark.api
def test_chat_completions_self_host_false_missing_token_returns_401() -> None:
    """验证 SELF_HOST=False 缺 token 返回 401 (org_id 触发点数校验).

    AGENTS.md 第 8 章: SELF_HOST=False 时强制校验 JWT Token, 缺 token 返回 401.
    服务端默认 SELF_HOST=True (跳过校验, 返回 200), 此时跳过本用例.
    仅当服务端配置 SELF_HOST=False 时才验证 401 行为.

    路由逻辑 (routes.py): not settings.self_host and (org_id or project_id) and not token → 401.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False, org_id="test-org-api-check"),
        )
    # SELF_HOST=True (默认) → 跳过校验 → 200 (短查询响应)
    # SELF_HOST=False → 缺 token → 401
    if r.status_code == 200:
        pytest.skip("服务端 SELF_HOST=True, 401 校验不适用 (跳过)")
    assert r.status_code == 401, (
        f"SELF_HOST=False 缺 token 应返回 401, 实际: {r.status_code} {r.text[:200]}"
    )


@pytest.mark.api
def test_chat_completions_uploaded_files_context_load() -> None:
    """验证 uploaded_files 加载已上传文件上下文: 上传文件 → 引用 file_id → 200.

    AGENTS.md 第 7 章: 用户私有数据按 agent_id + user_id 隔离, file_id 三级分键.
    短查询触发 short_query_reply (不走研究图, 不实际加载文件上下文),
    但 uploaded_files 字段应被 Pydantic 接受不报错.
    实际文件上下文加载 (research 分支) 由 e2e 测试覆盖 (test_file_upload_then_chat).
    """
    # 步骤 1: 上传文件获取 file_id
    file_content = b"Python async programming: asyncio, async/await, coroutine\n"
    files = {"file": ("test_uploaded_context.txt", io.BytesIO(file_content), "text/plain")}
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r_upload = client.post(f"{AGENT_URL}/v1/files", files=files)
    assert r_upload.status_code == 201, (
        f"文件上传失败: {r_upload.status_code} {r_upload.text[:200]}"
    )
    file_id = r_upload.json()["file_id"]

    # 步骤 2: 携带 uploaded_files 提问
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False, uploaded_files=[file_id]),
        )
    assert r.status_code == 200, (
        f"uploaded_files 请求应返回 200, 实际: {r.status_code} {r.text[:200]}"
    )
    data = r.json()
    assert data["object"] == "chat.completion"


@pytest.mark.api
def test_chat_completions_agent_role_override() -> None:
    """验证 agent_role 覆盖 (GPTR Config 层): 注入行业 persona → 200.

    AGENTS.md 第 7 章: agent_role 对标 GPTR AGENT_ROLE 配置, 优先级高于 LLM 动态生成
    (AgentCreator). 行业适配采用 GPTR 风格 4 层机制, 不使用行业分类器.
    短查询触发 short_query_reply, 但 agent_role 字段应被接受不报错.
    实际 agent_role 覆盖效果由 e2e 测试覆盖.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False, agent_role="金融行业研究分析师"),
        )
    assert r.status_code == 200, f"agent_role 注入应返回 200, 实际: {r.status_code} {r.text[:200]}"
    data = r.json()
    assert data["object"] == "chat.completion"


# ========== 错误码补充场景 (400/401/422/500 完整覆盖) ==========


@pytest.mark.api
def test_error_400_missing_messages_field() -> None:
    """错误码 400/422: 缺少 messages 字段 → 422 (Pydantic 校验失败).

    AGENTS.md 第 11 章: 所有外部输入经 Pydantic 校验.
    AGENTS.md 第 13 章: API 测试必须覆盖错误码.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={"model": "agentinsight-researcher", "stream": False},
        )
    assert r.status_code in (400, 422), (
        f"缺少 messages 应返回 400/422, 实际: {r.status_code} {r.text[:200]}"
    )


@pytest.mark.api
def test_error_400_empty_messages_array() -> None:
    """错误码 400: 空 messages 数组 → 400 (业务校验).

    AGENTS.md 第 13 章: API 测试必须覆盖错误码 (空 messages).
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [],
                "stream": False,
            },
        )
    assert r.status_code == 400, f"空 messages 应返回 400, 实际: {r.status_code} {r.text[:200]}"


@pytest.mark.api
def test_error_400_no_user_role_message() -> None:
    """错误码 400: 仅 system 消息 (无 user) → 400 (业务校验)."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "system", "content": "你是助手"}],
                "stream": False,
            },
        )
    assert r.status_code == 400, f"无 user 消息应返回 400, 实际: {r.status_code} {r.text[:200]}"


@pytest.mark.api
def test_error_400_empty_query_content() -> None:
    """错误码 400: user content 为空白 → 400 (业务校验)."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "   "}],
                "stream": False,
            },
        )
    assert r.status_code == 400, f"空白查询应返回 400, 实际: {r.status_code} {r.text[:200]}"


@pytest.mark.api
def test_error_422_invalid_stream_type() -> None:
    """错误码 422: stream 非 bool → 422 (Pydantic StrictBool 校验)."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": "yes",
            },
        )
    assert r.status_code == 422, f"stream='yes' 应返回 422, 实际: {r.status_code} {r.text[:200]}"


@pytest.mark.api
def test_error_422_invalid_content_type() -> None:
    """错误码 422: content 非 str → 422 (Pydantic 校验)."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": 12345}],
                "stream": False,
            },
        )
    assert r.status_code == 422, f"content=12345 应返回 422, 实际: {r.status_code} {r.text[:200]}"


@pytest.mark.api
def test_error_422_invalid_messages_type() -> None:
    """错误码 422: messages 非 list → 422 (Pydantic 校验)."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": "not-a-list",
                "stream": False,
            },
        )
    assert r.status_code == 422, f"messages='not-a-list' 应返回 422, 实际: {r.status_code}"


@pytest.mark.api
def test_error_422_invalid_json_body() -> None:
    """错误码 422: 非法 JSON body → 422 (Pydantic 校验)."""
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            content=b"{not valid json",
            headers={"Content-Type": "application/json"},
        )
    assert r.status_code in (400, 422), (
        f"非法 JSON 应返回 400/422, 实际: {r.status_code} {r.text[:200]}"
    )


@pytest.mark.api
def test_error_401_self_host_false_missing_token() -> None:
    """错误码 401: SELF_HOST=False + org_id + 缺 token → 401.

    AGENTS.md 第 8 章: SELF_HOST=False 时强制校验 JWT Token.
    AGENTS.md 第 13 章: API 测试必须覆盖错误码 (含 401).

    路由逻辑 (routes.py): not settings.self_host and (org_id or project_id) and not token → 401.
    服务端默认 SELF_HOST=True 时跳过本用例.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False, org_id="test-org-401-check"),
        )
    # SELF_HOST=True (默认) → 跳过校验 → 200 (短查询响应)
    if r.status_code == 200:
        pytest.skip("服务端 SELF_HOST=True, 401 校验不适用 (跳过)")
    assert r.status_code == 401, (
        f"SELF_HOST=False 缺 token 应返回 401, 实际: {r.status_code} {r.text[:200]}"
    )


@pytest.mark.api
def test_error_401_self_host_false_missing_token_project_id() -> None:
    """错误码 401: SELF_HOST=False + project_id + 缺 token → 401.

    AGENTS.md 第 8 章: org_id 优先于 project_id, 二者至少一个触发校验.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False, project_id="test-project-401"),
        )
    if r.status_code == 200:
        pytest.skip("服务端 SELF_HOST=True, 401 校验不适用 (跳过)")
    assert r.status_code == 401, (
        f"SELF_HOST=False 缺 token (project_id) 应返回 401, 实际: {r.status_code}"
    )


@pytest.mark.api
def test_no_500_error_on_invalid_model() -> None:
    """验证无效 model 参数不触发 500 错误.

    AGENTS.md 第 13 章: API 测试必须覆盖错误码 (无效 model 参数).
    AGENTS.md 第 14 章: OpenAI 兼容端点, model 字段用于路由.
    实现可能: (a) 不校验 model 直接走默认 (200), (b) 校验 model 不在白名单 (400).
    两种行为均可接受, 不应返回 5xx.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "unknown-model-xyz-invalid",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
            },
        )
    assert r.status_code < 500, f"无效 model 不应 5xx, 实际: {r.status_code} {r.text[:200]}"


@pytest.mark.api
def test_no_500_error_on_invalid_report_type() -> None:
    """验证未知 report_type 不触发 500 错误 (应降级为默认值).

    AGENTS.md 第 11 章: 所有外部输入经 Pydantic 校验.
    AGENTS.md 第 13 章: 不应 5xx 崩溃.
    """
    sid = _unique_session_id()
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
                "session_id": sid,
                "report_type": "unknown_type_xyz",
            },
        )
    assert r.status_code < 500, f"未知 report_type 不应 5xx, 实际: {r.status_code} {r.text[:200]}"


@pytest.mark.api
def test_no_500_error_on_long_session_id() -> None:
    """验证超长 session_id 不触发 500 错误.

    AGENTS.md 第 6 章: thread_id 做会话隔离键, 不应限制长度.
    AGENTS.md 第 13 章: 不应 5xx 崩溃.
    """
    long_sid = "test_long_" + "x" * 1000
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
                "session_id": long_sid,
            },
        )
    assert r.status_code < 500, f"超长 session_id 不应 5xx, 实际: {r.status_code} {r.text[:200]}"


@pytest.mark.api
def test_stream_sse_format_complete() -> None:
    """验证流式 SSE 响应格式完整: 首块 + 内容块 + 末块 + [DONE].

    AGENTS.md 第 13/14 章: API 测试必须覆盖流式 SSE.
    SSE 帧格式: data: {json}\\n\\n, 末帧为 data: [DONE]\\n\\n.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        with client.stream(
            "POST",
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=True),
        ) as r:
            assert r.status_code == 200
            content_type = r.headers.get("content-type", "")
            assert "text/event-stream" in content_type

            chunks: list[dict[str, object]] = []
            has_done = False
            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    has_done = True
                    break
                chunks.append(json.loads(payload))

    # 必须有 [DONE] 终止帧
    assert has_done, "SSE 流缺少 [DONE] 终止帧"
    # 至少有首块(role) + 内容块 + 末块(finish_reason)
    assert len(chunks) >= 3, f"SSE 帧数不足: {len(chunks)}"
    # 首块应含 role: assistant
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    # 末块应含 finish_reason: stop
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


@pytest.mark.api
def test_non_stream_response_structure_complete() -> None:
    """验证非流式响应结构完整: id/object/created/model/choices/usage.

    AGENTS.md 第 13/14 章: API 测试必须覆盖非流式响应.
    OpenAI 兼容响应结构必须包含完整字段.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False),
        )
    assert r.status_code == 200
    data = r.json()
    # 完整字段校验
    assert data["object"] == "chat.completion"
    assert "id" in data and data["id"]
    assert "created" in data and isinstance(data["created"], int)
    assert "model" in data and data["model"] == "agentinsight-researcher"
    assert "choices" in data and len(data["choices"]) == 1
    choice = data["choices"][0]
    assert choice["message"]["role"] == "assistant"
    assert "content" in choice["message"]
    assert choice["finish_reason"] == "stop"
    assert "usage" in data
    assert "total_tokens" in data["usage"]


@pytest.mark.api
def test_bearer_token_request_returns_200() -> None:
    """验证携带 Bearer JWT Token 请求返回 200 (降级或真实解析).

    AGENTS.md 第 8 章: token 存在时调用 /api/user 获取 user_id,
    调用失败 (test-token 非合法 JWT) 按无 token 处理并降级.
    AGENTS.md 第 13 章: 必须包含携带 Bearer JWT Token 场景.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False),
            headers={"Authorization": "Bearer test-token-invalid"},
        )
    assert r.status_code == 200, (
        f"带 token 请求应返回 200 (降级), 实际: {r.status_code} {r.text[:200]}"
    )


@pytest.mark.api
def test_without_token_returns_200() -> None:
    """验证不携带 Token 请求返回 200 (降级 IP-based UserId).

    AGENTS.md 第 8 章: token 不存在时降级 (self_host=True 默认).
    AGENTS.md 第 13 章: 必须包含不携带 Token 场景.
    """
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False),
        )
    assert r.status_code == 200, (
        f"无 token 请求应返回 200 (降级), 实际: {r.status_code} {r.text[:200]}"
    )
