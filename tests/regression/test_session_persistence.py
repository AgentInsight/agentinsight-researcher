"""回归测试: 会话持久化.

AGENTS.md 第 6/13 章硬约束:
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

import os
import time
import uuid

import httpx
import pytest

# AGENTS.md 第 13 章: 测试目标地址从环境变量注入
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

    AGENTS.md 第 6 章: 会话间状态通过 Postgres Checkpointer 隔离.
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

    AGENTS.md 第 6 章: 会话间状态通过 Postgres Checkpointer 隔离.
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
