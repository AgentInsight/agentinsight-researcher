"""端到端测试: 人在回路完整流程 (human_review_enabled + WebSocket + FeedbackQueue).

AGENTS.md 第 13/14 章硬约束:
- e2e 必须在容器栈 service_healthy 后执行
- 测试目标地址从环境变量 AGENT_URL 注入
- /v1/feedback 为允许调用的端点 (人在回路反馈通道)
- WS /v1/ws/{session_id} 为允许调用的端点 (人在回路审核请求通道)
- 仅 human_review_enabled=True 时前端才应调用 /v1/feedback 与 WS

WebSocket 8 类结构化消息 (src/api/websocket.py):
    1. logs: 日志信息
    2. content: 内容块 (报告正文流式)
    3. node_progress: 节点进度
    4. sources: 检索来源
    5. tool_call: 工具调用
    6. report: 完整报告
    7. human_feedback_request: 人在回路审核请求
    8. error: 错误信息

覆盖 3 个核心场景:
1. 审核通过流程: multi_agent=True → human_feedback_request → feedback="approve" → 完成
2. 审核修订流程: multi_agent=True → human_feedback_request → feedback="修订意见" → 重做
3. WebSocket 8 类结构化消息推送验证

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/e2e/test_human_in_loop.py -v -m e2e

注意: 人在回路测试需 human_review_enabled=True (默认 False).
服务端未启用时自动 skip, 不 fail.
WebSocket 鉴权 (prod 环境) 或 Origin 校验失败时自动 skip.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import httpx
import pytest

# websockets 库 (uvicorn[standard] 包含此依赖)
try:
    import websockets
    import websockets.exceptions

    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

# AGENTS.md 第 13 章: 测试目标地址从环境变量注入, 禁止硬编码
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")

# WebSocket 基础 URL (http→ws, https→wss)
WS_BASE = AGENT_URL.replace("http://", "ws://").replace("https://", "wss://")

# e2e 测试超时 600s (完整研究 + 人在回路等待)
E2E_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)

# WebSocket 操作超时
WS_CONNECT_TIMEOUT = 10  # 连接超时
WS_FEEDBACK_WAIT_TIMEOUT = 120  # 等待 human_feedback_request 超时 (研究启动需时间)
WS_MSG_RECV_TIMEOUT = 30  # 单条消息接收超时

# WebSocket 8 类结构化消息类型 (src/api/websocket.py ALL_WS_MSG_TYPES)
KNOWN_WS_MSG_TYPES: set[str] = {
    "logs",
    "content",
    "node_progress",
    "sources",
    "tool_call",
    "report",
    "human_feedback_request",
    "error",
    # pong 是 ping 的响应, 不属于 8 类但为合法响应类型
    "pong",
}


def _unique_session_id() -> str:
    """生成唯一 session_id (AGENTS.md 第 13 章: session_id=test_*)."""
    return f"test_e2e_hil_{uuid.uuid4().hex[:12]}"


def _log(msg: str) -> None:
    """带时间戳输出, 便于追踪长流程进度."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def _connect_websocket(session_id: str) -> object | None:
    """连接 WebSocket, 返回 ws 对象; 连接失败 (鉴权/Origin/未启用) 返回 None.

    AGENTS.md 第 14 章: /v1/ws/{session_id} 为允许调用的端点.
    生产环境强制 JWT 鉴权 + Origin 校验.
    """
    ws_uri = f"{WS_BASE}/v1/ws/{session_id}"
    try:
        # 尝试连接 (不传 Origin, 避免触发 Origin 校验;
        # 若服务端要求鉴权, 连接会被拒绝并返回 4001)
        ws = await asyncio.wait_for(
            websockets.connect(ws_uri),
            timeout=WS_CONNECT_TIMEOUT,
        )
        return ws
    except TimeoutError:
        _log(f"WebSocket 连接超时: {ws_uri}")
        return None
    except Exception as e:  # noqa: BLE001
        _log(f"WebSocket 连接失败 (可能需鉴权或未启用): {type(e).__name__}: {e}")
        return None


async def _recv_ws_message(ws: object, recv_timeout: float) -> dict[str, object] | None:
    """接收并解析一条 WebSocket JSON 消息, 超时返回 None."""
    try:
        async with asyncio.timeout(recv_timeout):
            raw = await ws.recv()  # type: ignore[union-attr]
        return json.loads(raw)  # type: ignore[arg-type]
    except TimeoutError:
        return None
    except Exception:  # noqa: BLE001
        return None


async def _start_research(
    client: httpx.AsyncClient,
    session_id: str,
    query: str,
    *,
    multi_agent: bool = True,
) -> httpx.Response:
    """发起非流式研究请求 (后台任务用)."""
    payload: dict[str, object] = {
        "model": "agentinsight-researcher",
        "messages": [{"role": "user", "content": query}],
        "stream": False,
        "report_type": "basic_report",
        "session_id": session_id,
    }
    if multi_agent:
        payload["multi_agent"] = True
    return await client.post(
        f"{AGENT_URL}/v1/chat/completions",
        json=payload,
    )


# ========== 场景 1: 审核通过流程 ==========


@pytest.mark.e2e
async def test_human_feedback_approve_flow() -> None:
    """审核通过流程: multi_agent=True → human_feedback_request → approve → 完成.

    AGENTS.md 第 14 章: 人在回路端点 POST /v1/feedback.
    feedback="approve" 表示接受研究计划/大纲, 研究继续执行.

    流程:
    1. 连接 WebSocket (session_id 隔离)
    2. 启动 multi_agent=True 研究 (后台任务)
    3. 等待 human_feedback_request 消息 (human_review_enabled=True 时)
    4. 提交 feedback="approve" → 研究继续
    5. 验证研究完成 (200 + content 非空)

    注意: human_review_enabled=False (默认) 时, 研究不暂停, 无 human_feedback_request,
    测试自动 skip.
    """
    if not HAS_WEBSOCKETS:
        pytest.skip("websockets 库未安装, 跳过 WebSocket 测试")

    sid = _unique_session_id()
    query = "用 200 字简述 Python 异步编程的核心优势"
    _log(f"审核通过流程测试: session={sid}")

    # 步骤 1: 连接 WebSocket
    ws = await _connect_websocket(sid)
    if ws is None:
        pytest.skip("WebSocket 连接失败 (可能未启用或需鉴权)")

    try:
        # 等待连接确认 (logs 消息)
        connect_msg = await _recv_ws_message(ws, WS_MSG_RECV_TIMEOUT)
        if connect_msg is None:
            pytest.skip("WebSocket 连接后未收到确认消息")
        _log(f"WebSocket 已连接: {connect_msg.get('type')}")

        # 步骤 2: 启动研究 (后台任务)
        async with httpx.AsyncClient(timeout=E2E_TIMEOUT) as client:
            research_task = asyncio.create_task(_start_research(client, sid, query))
            _log(f"研究已启动 (后台): query={query[:60]}")

            # 步骤 3: 等待 human_feedback_request 消息
            feedback_request_received = False
            deadline = time.time() + WS_FEEDBACK_WAIT_TIMEOUT
            while time.time() < deadline:
                msg = await _recv_ws_message(ws, 5.0)
                if msg is None:
                    # 检查研究是否已完成 (无 human_feedback_request → human_review 未启用)
                    if research_task.done():
                        _log("研究已完成, 未收到 human_feedback_request (human_review 未启用)")
                        break
                    continue
                msg_type = msg.get("type", "")
                _log(f"收到 WS 消息: type={msg_type}")
                if msg_type == "human_feedback_request":
                    feedback_request_received = True
                    break
                # 收到 error 或其他消息, 继续等待

            if not feedback_request_received:
                research_task.cancel()
                pytest.skip("human_review_enabled=False, 未收到人在回路审核请求 (跳过)")

            # 步骤 4: 提交 approve 反馈
            _log("提交 approve 反馈")
            r_feedback = await client.post(
                f"{AGENT_URL}/v1/feedback",
                json={"session_id": sid, "feedback": "approve"},
            )
            assert r_feedback.status_code == 200, (
                f"提交 approve 反馈非 200: {r_feedback.status_code} {r_feedback.text[:200]}"
            )

            # 步骤 5: 等待研究完成
            r_research = await research_task
            assert r_research.status_code == 200, (
                f"研究完成非 200: {r_research.status_code} {r_research.text[:300]}"
            )
            data = r_research.json()
            assert data["object"] == "chat.completion"
            content = data["choices"][0]["message"].get("content", "")
            assert content, "审核通过后研究 content 为空"
            _log(f"审核通过流程完成: content {len(content)} 字")
    finally:
        await ws.close()  # type: ignore[union-attr]


# ========== 场景 2: 审核修订流程 ==========


@pytest.mark.e2e
async def test_human_feedback_revise_flow() -> None:
    """审核修订流程: multi_agent=True → human_feedback_request → 修订意见 → 重做 → 完成.

    AGENTS.md 第 14 章: feedback 非 approve/accept/通过 等关键词时视为修订意见.
    修订意见触发回 agent_creator 重新生成角色, 研究重做.

    流程:
    1. 连接 WebSocket
    2. 启动 multi_agent=True 研究 (后台任务)
    3. 等待 human_feedback_request 消息
    4. 提交修订意见 feedback="请增加性能基准测试数据"
    5. 验证研究重做并完成 (200 + content 非空)

    注意: human_review_enabled=False 时自动 skip.
    """
    if not HAS_WEBSOCKETS:
        pytest.skip("websockets 库未安装, 跳过 WebSocket 测试")

    sid = _unique_session_id()
    query = "用 200 字简述 Python 异步编程的应用场景"
    _log(f"审核修订流程测试: session={sid}")

    # 步骤 1: 连接 WebSocket
    ws = await _connect_websocket(sid)
    if ws is None:
        pytest.skip("WebSocket 连接失败 (可能未启用或需鉴权)")

    try:
        # 等待连接确认
        connect_msg = await _recv_ws_message(ws, WS_MSG_RECV_TIMEOUT)
        if connect_msg is None:
            pytest.skip("WebSocket 连接后未收到确认消息")

        # 步骤 2: 启动研究
        async with httpx.AsyncClient(timeout=E2E_TIMEOUT) as client:
            research_task = asyncio.create_task(_start_research(client, sid, query))
            _log(f"研究已启动 (后台): query={query[:60]}")

            # 步骤 3: 等待 human_feedback_request
            feedback_request_received = False
            deadline = time.time() + WS_FEEDBACK_WAIT_TIMEOUT
            while time.time() < deadline:
                msg = await _recv_ws_message(ws, 5.0)
                if msg is None:
                    if research_task.done():
                        break
                    continue
                msg_type = msg.get("type", "")
                _log(f"收到 WS 消息: type={msg_type}")
                if msg_type == "human_feedback_request":
                    feedback_request_received = True
                    break

            if not feedback_request_received:
                research_task.cancel()
                pytest.skip("human_review_enabled=False, 未收到人在回路审核请求 (跳过)")

            # 步骤 4: 提交修订意见
            revise_feedback = "请增加性能基准测试数据与实际案例"
            _log(f"提交修订意见: {revise_feedback}")
            r_feedback = await client.post(
                f"{AGENT_URL}/v1/feedback",
                json={"session_id": sid, "feedback": revise_feedback},
            )
            assert r_feedback.status_code == 200, (
                f"提交修订意见非 200: {r_feedback.status_code} {r_feedback.text[:200]}"
            )

            # 步骤 5: 等待研究重做并完成
            # 修订后研究可能再次请求审核, 也可能直接完成 (取决于实现)
            # 收集后续 WebSocket 消息, 等待研究完成
            second_feedback_deadline = time.time() + WS_FEEDBACK_WAIT_TIMEOUT
            while time.time() < second_feedback_deadline:
                msg = await _recv_ws_message(ws, 5.0)
                if msg is not None:
                    msg_type = msg.get("type", "")
                    _log(f"修订后收到 WS 消息: type={msg_type}")
                    if msg_type == "human_feedback_request":
                        # 第二次审核请求, 直接 approve 完成
                        _log("第二次审核请求, 自动 approve")
                        await client.post(
                            f"{AGENT_URL}/v1/feedback",
                            json={"session_id": sid, "feedback": "approve"},
                        )
                if research_task.done():
                    break

            r_research = await research_task
            assert r_research.status_code == 200, (
                f"修订后研究完成非 200: {r_research.status_code} {r_research.text[:300]}"
            )
            data = r_research.json()
            content = data["choices"][0]["message"].get("content", "")
            assert content, "修订后研究 content 为空"
            _log(f"审核修订流程完成: content {len(content)} 字")
    finally:
        await ws.close()  # type: ignore[union-attr]


# ========== 场景 3: WebSocket 8 类结构化消息推送 ==========


@pytest.mark.e2e
async def test_websocket_eight_message_types() -> None:
    """WebSocket 8 类结构化消息推送验证.

    AGENTS.md 第 14 章: 服务端推送 8 类结构化消息.
    src/api/websocket.py ALL_WS_MSG_TYPES:
        logs / content / node_progress / sources / tool_call /
        report / human_feedback_request / error

    流程:
    1. 连接 WebSocket → 验证收到 logs 消息 (连接确认)
    2. 发送 ping → 验证收到 pong 响应
    3. 发送 human_feedback (无待处理) → 验证收到 error 消息
    4. 启动研究 (后台) → 收集消息, 验证类型均属 8 类已知类型
    5. 验证至少收到 logs 类型 (连接确认必发)

    注意: content/node_progress/sources/tool_call/report/human_feedback_request
    是否出现取决于研究流程是否集成 WebSocket 推送; 本用例验证已收到的
    所有消息类型均属 8 类已知类型 (类型契约验证).
    """
    if not HAS_WEBSOCKETS:
        pytest.skip("websockets 库未安装, 跳过 WebSocket 测试")

    sid = _unique_session_id()
    _log(f"WebSocket 8 类消息测试: session={sid}")

    # 步骤 1: 连接 WebSocket
    ws = await _connect_websocket(sid)
    if ws is None:
        pytest.skip("WebSocket 连接失败 (可能未启用或需鉴权)")

    received_types: set[str] = set()

    try:
        # 等待连接确认 (logs 消息)
        connect_msg = await _recv_ws_message(ws, WS_MSG_RECV_TIMEOUT)
        if connect_msg is None:
            pytest.skip("WebSocket 连接后未收到确认消息")
        msg_type = connect_msg.get("type", "")
        received_types.add(msg_type)
        _log(f"连接确认消息: type={msg_type}")
        assert msg_type == "logs", f"连接确认消息类型非 logs: {msg_type}"

        # 步骤 2: 发送 ping → 验证 pong
        await ws.send(json.dumps({"type": "ping"}))  # type: ignore[union-attr]
        pong_msg = await _recv_ws_message(ws, WS_MSG_RECV_TIMEOUT)
        if pong_msg is not None:
            pong_type = pong_msg.get("type", "")
            received_types.add(pong_type)
            _log(f"ping 响应: type={pong_type}")
            assert pong_type == "pong", f"ping 响应类型非 pong: {pong_type}"

        # 步骤 3: 发送 human_feedback (无待处理) → 验证 error
        await ws.send(json.dumps({"type": "human_feedback", "feedback": "test"}))  # type: ignore[union-attr]
        error_msg = await _recv_ws_message(ws, WS_MSG_RECV_TIMEOUT)
        if error_msg is not None:
            error_type = error_msg.get("type", "")
            received_types.add(error_type)
            _log(f"human_feedback (无待处理) 响应: type={error_type}")
            assert error_type == "error", f"无待处理反馈响应类型非 error: {error_type}"

        # 步骤 4: 启动研究 (后台) → 收集消息
        query = "用 200 字简述 Python 异步编程的核心优势"
        async with httpx.AsyncClient(timeout=E2E_TIMEOUT) as client:
            research_task = asyncio.create_task(_start_research(client, sid, query))
            _log(f"研究已启动 (后台): query={query[:60]}")

            # 收集 WebSocket 消息 (最多 60s 或研究完成)
            collect_deadline = time.time() + 60
            while time.time() < collect_deadline:
                msg = await _recv_ws_message(ws, 3.0)
                if msg is not None:
                    r_type = msg.get("type", "")
                    received_types.add(r_type)
                    _log(f"研究期间 WS 消息: type={r_type}")
                if research_task.done():
                    _log("研究已完成, 停止收集 WS 消息")
                    break

            # 等待研究完成 (如未完成)
            if not research_task.done():
                r = await research_task
            else:
                r = research_task.result()

        assert r.status_code == 200, f"研究非 200: {r.status_code}"

        # 步骤 5: 验证所有收到的消息类型均属 8 类已知类型 (含 pong)
        unknown_types = received_types - KNOWN_WS_MSG_TYPES
        assert not unknown_types, (
            f"收到未知消息类型: {unknown_types} (已知类型: {KNOWN_WS_MSG_TYPES})"
        )

        # 验证至少收到 logs 类型 (连接确认必发)
        assert "logs" in received_types, (
            f"未收到 logs 消息 (连接确认必发), 已收到: {received_types}"
        )

        _log(f"WebSocket 8 类消息验证通过: 收到 {len(received_types)} 种类型: {received_types}")
    finally:
        await ws.close()  # type: ignore[union-attr]
