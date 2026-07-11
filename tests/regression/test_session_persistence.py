"""回归测试: 会话持久化.

- 会话持久化到 Postgres Checkpointer; 内存 Checkpointer 仅 ENV=dev 允许
- thread_id (session_id) 做会话隔离键, 由请求上下文注入
- 会话间状态通过 Postgres Checkpointer 隔离, 禁止共享可变内存
- 回归测试为合并 main 前门禁, 不推荐跳过
- 超时设置: 回归测试 300s

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/regression/test_session_persistence.py -v -m regression

验证: 同一 session_id 两次请求 → 后端 Checkpoint 上下文保持.
第一次问"记住关键词XYZ", 第二次问"我刚才说的关键词是什么", 验证响应含 XYZ.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

import httpx
import pytest

# 测试目标地址从环境变量注入
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")

# 回归测试超时 300s (两次请求, 每次 60-120s)
REGRESSION_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)

# 唯一关键词 (避免误判)
TEST_KEYWORD = f"XYZ789TEST{uuid.uuid4().hex[:6].upper()}"


def _log(msg: str) -> None:
    """带时间戳输出, 便于追踪长流程进度."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


@pytest.mark.regression
def test_session_id_consistency() -> None:
    """验证会话持久化: 同一 session_id 两次请求 → 上下文保持.

    会话间状态通过 Postgres Checkpointer 隔离.
    第一次请求让 agent 记住关键词, 第二次请求询问关键词, 验证响应包含该关键词.

    注意: 由于意图分类 (CHAT/RESEARCH), 两次请求可能走不同 graph.
    chat graph 与 researcher graph 共用 checkpointer, 但状态 schema 不同.
    若上下文未保持, 测试将失败 (揭示会话持久化问题).
    """
    sid = f"test_session_persistence_{uuid.uuid4().hex[:12]}"

    # 第一次请求: 让 agent 记住关键词
    first_query = f"请记住以下关键词: {TEST_KEYWORD}. 后续我会问你这个关键词."
    _log(f"第一次请求开始: session={sid}, query={first_query[:60]}")

    with httpx.Client(timeout=REGRESSION_TIMEOUT) as client:
        r1 = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": first_query}],
                "stream": False,
                "session_id": sid,
            },
        )
    assert r1.status_code == 200, f"第一次请求非 200: {r1.status_code} {r1.text[:300]}"
    first_content = r1.json()["choices"][0]["message"]["content"]
    _log(f"第一次响应完成: 内容长度 {len(first_content)} 字")
    _log(f"第一次响应预览: {first_content[:200]}")

    # 第二次请求: 询问关键词 (同一 session_id)
    second_query = "我刚才让你记住的关键词是什么? 请直接回答关键词本身."
    _log(f"第二次请求开始: query={second_query[:60]}")

    with httpx.Client(timeout=REGRESSION_TIMEOUT) as client:
        r2 = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": second_query}],
                "stream": False,
                "session_id": sid,
            },
        )
    assert r2.status_code == 200, f"第二次请求非 200: {r2.status_code} {r2.text[:300]}"
    second_content = r2.json()["choices"][0]["message"]["content"]
    _log(f"第二次响应完成: 内容长度 {len(second_content)} 字")
    _log(f"第二次响应预览: {second_content[:300]}")

    # 验证第二次响应包含关键词 (会话持久化生效)
    assert TEST_KEYWORD in second_content, (
        f"会话持久化验证失败: 第二次响应未包含关键词 {TEST_KEYWORD}\n"
        f"第一次查询: {first_query}\n"
        f"第一次响应: {first_content[:300]}\n"
        f"第二次查询: {second_query}\n"
        f"第二次响应: {second_content[:500]}"
    )
    _log(f"会话持久化验证通过: 第二次响应包含关键词 {TEST_KEYWORD}")


@pytest.mark.regression
def test_different_session_isolation() -> None:
    """验证不同 session_id 隔离: 两个不同 session 不共享上下文.

    会话间状态通过 Postgres Checkpointer 隔离.
    """
    sid_a = f"test_session_iso_a_{uuid.uuid4().hex[:12]}"
    sid_b = f"test_session_iso_b_{uuid.uuid4().hex[:12]}"
    keyword_a = f"KEY_A_{uuid.uuid4().hex[:6].upper()}"

    # 在 session A 记住关键词
    _log(f"session A ({sid_a}) 记住关键词: {keyword_a}")
    with httpx.Client(timeout=REGRESSION_TIMEOUT) as client:
        r_a = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": f"请记住关键词: {keyword_a}"}],
                "stream": False,
                "session_id": sid_a,
            },
        )
    assert r_a.status_code == 200

    # 在 session B 询问关键词 (不同 session_id)
    _log(f"session B ({sid_b}) 询问关键词")
    with httpx.Client(timeout=REGRESSION_TIMEOUT) as client:
        r_b = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [
                    {
                        "role": "user",
                        "content": "我刚才让你记住的关键词是什么? 请直接回答.",
                    }
                ],
                "stream": False,
                "session_id": sid_b,
            },
        )
    assert r_b.status_code == 200
    content_b = r_b.json()["choices"][0]["message"]["content"]
    _log(f"session B 响应预览: {content_b[:300]}")

    # 隔离验证: session B 不应包含 session A 的关键词
    assert keyword_a not in content_b, (
        f"会话隔离失效: session B 泄露了 session A 的关键词 {keyword_a}\n"
        f"session B 响应: {content_b[:500]}"
    )
    _log("会话隔离验证通过: session B 未泄露 session A 的上下文")


# ========== 异步回归测试 (httpx.AsyncClient, 仅验证 HTTP 状态码/会话隔离, 不依赖完整 LLM 研究) ==========
# 新增测试不依赖外部 LLM 调用, 仅验证 HTTP 状态码而非内容.
# 使用短查询 (SHORT_QUERY/OFF_TOPIC) 触发快速响应路径, 验证会话隔离与持久化机制.

# 异步测试超时 (短查询响应快速; 含一次研究流式头验证)
ASYNC_TEST_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)


@pytest.mark.regression
async def test_session_persistence_stream_then_non_stream() -> None:
    """同一 session 先流式后非流式 → 两次请求均 200 (会话跨模式持久化).

    会话持久化到 Postgres Checkpointer, 跨请求保持.
    验证同一 session_id 支持流式与非流式混合请求.
    """
    sid = f"test_session_mix_{uuid.uuid4().hex[:12]}"
    _log(f"跨模式会话测试开始: session={sid}")

    # 第一次: 流式短查询 (快速, 不走研究图)
    async with httpx.AsyncClient(timeout=ASYNC_TEST_TIMEOUT) as client:
        async with client.stream(
            "POST",
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": True,
                "session_id": sid,
            },
        ) as r1:
            assert r1.status_code == 200, f"第一次流式请求非 200: {r1.status_code}"
            assert r1.headers.get("x-session-id") == sid, (
                f"第一次 X-Session-Id 不匹配: 期望={sid}, 实际={r1.headers.get('x-session-id')}"
            )
    _log(f"第一次流式请求通过: session={sid}")

    # 第二次: 非流式短查询 (同一 session)
    async with httpx.AsyncClient(timeout=ASYNC_TEST_TIMEOUT) as client:
        r2 = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "在吗"}],
                "stream": False,
                "session_id": sid,
            },
        )
    assert r2.status_code == 200, f"第二次非流式请求非 200: {r2.status_code}"
    assert r2.json()["object"] == "chat.completion"
    assert r2.headers.get("x-session-id") == sid, (
        f"第二次 X-Session-Id 不匹配: 期望={sid}, 实际={r2.headers.get('x-session-id')}"
    )
    _log(f"跨模式会话验证通过: session={sid}")


@pytest.mark.regression
async def test_multi_session_parallel_isolation() -> None:
    """3 个并行会话 → 各自 200 + X-Session-Id 隔离.

    每个 Agent 应支持并发多会话; 会话间状态通过 Checkpointer 隔离.
    """
    sids = [f"test_session_parallel_{i}_{uuid.uuid4().hex[:8]}" for i in range(3)]
    _log(f"并行会话测试开始: sessions={sids}")

    async def _query(sid: str) -> str:
        async with httpx.AsyncClient(timeout=ASYNC_TEST_TIMEOUT) as client:
            r = await client.post(
                f"{AGENT_URL}/v1/chat/completions",
                json={
                    "model": "agentinsight-researcher",
                    "messages": [{"role": "user", "content": "你好"}],
                    "stream": False,
                    "session_id": sid,
                },
            )
            assert r.status_code == 200, f"session {sid} 非 200: {r.status_code}"
            return r.headers.get("x-session-id", "")

    # 并行发起 3 个会话请求
    results = await asyncio.gather(*[_query(sid) for sid in sids])

    # 验证每个会话返回各自的 session_id (隔离)
    for sid, returned_sid in zip(sids, results, strict=False):
        assert returned_sid == sid, f"并行会话隔离失效: 期望={sid}, 返回={returned_sid}"
    _log(f"并行会话隔离验证通过: {len(sids)} 个会话各自隔离")


@pytest.mark.regression
async def test_session_switching_context_isolation() -> None:
    """会话切换 A→B→A → 每次 X-Session-Id 与请求一致 (切换不混淆).

    会话间状态隔离, 切换会话不混淆上下文.
    """
    sid_a = f"test_session_switch_a_{uuid.uuid4().hex[:8]}"
    sid_b = f"test_session_switch_b_{uuid.uuid4().hex[:8]}"
    _log(f"会话切换测试开始: A={sid_a}, B={sid_b}")

    async with httpx.AsyncClient(timeout=ASYNC_TEST_TIMEOUT) as client:
        # A 会话第一次
        r_a1 = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
                "session_id": sid_a,
            },
        )
        assert r_a1.status_code == 200, f"A 第一次请求非 200: {r_a1.status_code}"
        assert r_a1.headers.get("x-session-id") == sid_a, (
            f"A 第一次 X-Session-Id 不匹配: 期望={sid_a}, 实际={r_a1.headers.get('x-session-id')}"
        )

        # 切换到 B 会话
        r_b = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "嗨"}],
                "stream": False,
                "session_id": sid_b,
            },
        )
        assert r_b.status_code == 200, f"B 请求非 200: {r_b.status_code}"
        assert r_b.headers.get("x-session-id") == sid_b, (
            f"B X-Session-Id 不匹配: 期望={sid_b}, 实际={r_b.headers.get('x-session-id')}"
        )

        # 切回 A 会话
        r_a2 = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "在吗"}],
                "stream": False,
                "session_id": sid_a,
            },
        )
        assert r_a2.status_code == 200, f"A 第二次请求非 200: {r_a2.status_code}"
        assert r_a2.headers.get("x-session-id") == sid_a, (
            f"A 第二次 X-Session-Id 不匹配: 期望={sid_a}, 实际={r_a2.headers.get('x-session-id')}"
        )
    _log("会话切换隔离验证通过: A→B→A 各自 session_id 一致")


@pytest.mark.regression
async def test_long_session_id_persistence() -> None:
    """超长 session_id (1K 字符) → 200 + X-Session-Id 一致.

    thread_id 做会话隔离键, 不应限制长度.
    """
    long_sid = "test_long_session_" + "x" * 1000
    _log(f"超长 session_id 测试开始: 长度={len(long_sid)}")
    async with httpx.AsyncClient(timeout=ASYNC_TEST_TIMEOUT) as client:
        r = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
                "session_id": long_sid,
            },
        )
    assert r.status_code == 200, f"超长 session_id 请求非 200: {r.status_code}"
    returned_sid = r.headers.get("x-session-id", "")
    assert returned_sid == long_sid, (
        f"超长 session_id X-Session-Id 不匹配: 期望长度={len(long_sid)}, "
        f"实际长度={len(returned_sid)}"
    )
    _log(f"超长 session_id 验证通过: 长度={len(long_sid)}")


@pytest.mark.regression
async def test_session_id_with_safe_special_chars() -> None:
    """session_id 含安全特殊字符 (连字符/下划线) → 200 + X-Session-Id 一致.

    session_id 由请求上下文注入, 支持标准标识符字符.
    """
    special_sid = f"test-session-id_{uuid.uuid4().hex[:12]}"
    _log(f"特殊字符 session_id 测试开始: {special_sid}")
    async with httpx.AsyncClient(timeout=ASYNC_TEST_TIMEOUT) as client:
        r = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
                "session_id": special_sid,
            },
        )
    assert r.status_code == 200, f"含特殊字符 session_id 请求非 200: {r.status_code}"
    assert r.headers.get("x-session-id") == special_sid, (
        f"特殊字符 session_id X-Session-Id 不匹配: 期望={special_sid}, "
        f"实际={r.headers.get('x-session-id')}"
    )
    _log(f"特殊字符 session_id 验证通过: {special_sid}")


@pytest.mark.regression
async def test_session_resume_after_short_query() -> None:
    """短查询后继续同会话研究请求 → 两次均 200 (会话复用, 跨意图持久化).

    会话持久化, 支持跨意图复用.
    验证短查询 (SHORT_QUERY) 不破坏会话, 后续研究请求仍可使用同一 session.
    第二次研究请求用 stream=true, 仅验证响应头 (不等待完整研究).
    """
    sid = f"test_session_resume_{uuid.uuid4().hex[:12]}"
    _log(f"跨意图会话复用测试开始: session={sid}")

    # 第一次: 短查询 (快速, SHORT_QUERY 路径)
    async with httpx.AsyncClient(timeout=ASYNC_TEST_TIMEOUT) as client:
        r1 = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
                "session_id": sid,
            },
        )
    assert r1.status_code == 200, f"第一次短查询非 200: {r1.status_code}"
    assert r1.headers.get("x-session-id") == sid, (
        f"第一次 X-Session-Id 不匹配: 期望={sid}, 实际={r1.headers.get('x-session-id')}"
    )
    _log(f"第一次短查询通过: session={sid}")

    # 第二次: 研究请求流式 (同一 session, 仅验证响应头)
    async with httpx.AsyncClient(timeout=ASYNC_TEST_TIMEOUT) as client:
        async with client.stream(
            "POST",
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "分析 Python GIL 的影响"}],
                "stream": True,
                "report_type": "basic_report",
                "session_id": sid,
            },
        ) as r2:
            assert r2.status_code == 200, f"第二次研究请求非 200: {r2.status_code}"
            assert r2.headers.get("x-session-id") == sid, (
                f"第二次 X-Session-Id 不匹配: 期望={sid}, 实际={r2.headers.get('x-session-id')}"
            )
    _log(f"跨意图会话复用验证通过: session={sid}")
