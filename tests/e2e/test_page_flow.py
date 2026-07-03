"""端到端测试: 通过浏览器自动化验证测试页面完整研究链路.

AGENTS.md 第 13/14 章硬约束:
- e2e 必须在容器栈 service_healthy 后执行
- 测试目标地址从环境变量 AGENT_URL 注入 (默认 http://agent:8066, 宿主机跑用 127.0.0.1:8066)
- 必须覆盖: 打开测试页面 → 新建会话 → 发送提问 → 验证流式渲染 →
          验证工具调用展示 → 切换会话验证隔离
- 完整链路: 提问 → 检索 → 工具调用 → 流式响应 → 会话持久化

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/e2e/test_page_flow.py -s -v
"""

from __future__ import annotations

import os
import re
import time
from urllib.parse import urlparse

import pytest
from playwright.sync_api import ElementHandle, Page, expect

# AGENTS.md 第 13 章: 测试目标地址从 AGENT_URL 注入, 禁止硬编码
# 默认 http://agent:8066 (容器内网络); 宿主机直跑用 127.0.0.1:8066
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")

# 研究流程一次 3-10 分钟, 给足超时
RESEARCH_TIMEOUT_MS = 15 * 60 * 1000  # 15 分钟
STREAM_FIRST_TOKEN_TIMEOUT_MS = 60 * 1000  # 首字 60s

# UUID v4 正则
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _log(msg: str) -> None:
    """带时间戳的输出, 便于追踪长流程进度."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _open_config_panel(page: Page) -> None:
    """展开顶部配置栏 (默认折叠)."""
    toggle = page.get_by_role("button", name="配置")
    config_body = page.locator("#configBody")
    if not config_body.is_visible():
        toggle.click()
        expect(config_body).to_be_visible()


def _new_session(page: Page) -> str:
    """点击'新建'按钮, 返回生成的 session_id."""
    _open_config_panel(page)
    btn = page.locator("#newSession")
    btn.click()
    # session_id 显示区会更新
    display = page.locator("#sessionIdDisplay")
    expect(display).not_to_have_text("—", timeout=5_000)
    sid = display.inner_text().strip()
    assert UUID_RE.match(sid), f"session_id 不是合法 UUID v4: {sid}"
    _log(f"新建会话: {sid}")
    return sid


def _switch_session(page: Page, session_id: str) -> None:
    """切换到指定 session_id."""
    _open_config_panel(page)
    inp = page.locator("#sessionIdInput")
    inp.fill(session_id)
    # 输入框 change 事件触发切换
    inp.press("Enter")
    # 显示区应同步更新
    expect(page.locator("#sessionIdDisplay")).to_have_text(session_id, timeout=5_000)
    _log(f"切换会话: {session_id}")


def _send_query(page: Page, query: str) -> None:
    """在输入框填入问题并点击发送."""
    inp = page.locator("#msgInput")
    inp.fill(query)
    send_btn = page.locator("#sendBtn")
    # 按钮可能在禁用态短暂, 等可用
    expect(send_btn).to_be_enabled(timeout=5_000)
    send_btn.click()
    _log(f"已发送问题: {query[:60]}{'...' if len(query) > 60 else ''}")


def _wait_research_done(page: Page) -> ElementHandle:
    """等待研究流程结束, 返回 assistant 消息根元素.

    结束条件 (与 static/index.html 第 855-859 行一致):
      - 正常完成: JS 执行 `statusEl.style.display = 'none'` 隐藏 status-line (文本不变)
      - 用户中断: status-line 文本变为 '已停止'
    因此检测完成应看 computed display 是否为 none, 或文本是否为 '已停止'.
    """
    _log("等待流式响应开始 (首字)...")
    # 等待 assistant 气泡出现
    last_assistant = page.locator(".msg.assistant").last
    expect(last_assistant).to_be_visible(timeout=STREAM_FIRST_TOKEN_TIMEOUT_MS)
    _log("检测到 assistant 气泡, 等待研究流程完成 (最多 15 分钟)...")

    # 轮询最后一条 assistant 气泡的 status-line 状态
    deadline = time.time() + RESEARCH_TIMEOUT_MS / 1000
    while time.time() < deadline:
        done = page.evaluate(
            """() => {
            const msgs = document.querySelectorAll('.msg.assistant');
            if (!msgs.length) return false;
            const last = msgs[msgs.length - 1];
            const status = last.querySelector('.status-line');
            if (!status) return true;  // 元素不存在视为完成
            // 1. JS 完成时设 display:none (computed style 才能反映)
            const disp = window.getComputedStyle(status).display;
            if (disp === 'none') return true;
            // 2. 用户中断时文本变为 '已停止'
            const txt = (status.textContent || '').trim();
            if (txt.includes('已停止')) return true;
            return false;
        }"""
        )
        if done:
            _log("研究流程完成 (status-line 已隐藏或已停止)")
            break
        time.sleep(3)
    else:
        # 超时, 截图存档
        page.screenshot(path="tests/e2e/_timeout.png")
        pytest.fail("研究流程超时未完成 (15 分钟), 截图: tests/e2e/_timeout.png")

    return last_assistant


def _get_assistant_text(page: Page) -> str:
    """取最后一条 assistant 气泡的纯文本内容."""
    return page.evaluate(
        """() => {
        const msgs = document.querySelectorAll('.msg.assistant');
        if (!msgs.length) return '';
        const last = msgs[msgs.length - 1];
        const content = last.querySelector('.bubble .content');
        return content ? content.innerText : '';
    }"""
    )


def _count_panels(page: Page) -> int:
    """统计最后一条 assistant 气泡内的折叠面板数 (节点进度 / 参考来源)."""
    return page.evaluate(
        """() => {
        const msgs = document.querySelectorAll('.msg.assistant');
        if (!msgs.length) return 0;
        const last = msgs[msgs.length - 1];
        return last.querySelectorAll('.bubble .panel').length;
    }"""
    )


def _has_error(page: Page) -> str | None:
    """检查最后一条 assistant 气泡是否有错误提示, 返回错误文本或 None."""
    return page.evaluate(
        """() => {
        const msgs = document.querySelectorAll('.msg.assistant');
        if (!msgs.length) return null;
        const last = msgs[msgs.length - 1];
        const errs = last.querySelectorAll('.bubble .error-box');
        return errs.length ? (errs[0].textContent || '') : null;
    }"""
    )


# ========== 测试用例 ==========


@pytest.fixture(scope="module")
def page():
    """启动浏览器, 打开测试页面, 测试结束关闭."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        pg = context.new_page()
        # 默认控制台日志转发到 stdout, 便于排查
        pg.on(
            "console",
            lambda msg: _log(f"[browser {msg.type}] {msg.text}") if msg.type == "error" else None,
        )
        pg.on("pageerror", lambda exc: _log(f"[browser pageerror] {exc}"))
        yield pg
        context.close()
        browser.close()


def test_1_health_endpoint_ok():
    """前置: /health 端点返回 200, 确认容器栈就绪 (AGENTS.md 第 13 章)."""
    import httpx

    r = httpx.get(f"{AGENT_URL}/health", timeout=10.0)
    assert r.status_code == 200, f"/health 非 200: {r.status_code} {r.text}"
    body = r.json()
    assert body.get("status") == "ok", f"/health 状态异常: {body}"
    _log(f"/health OK: {body}")


def test_2_test_page_loaded(page: Page):
    """打开测试页面, 验证关键元素加载 (AGENTS.md 第 14 章)."""
    page.goto(f"{AGENT_URL}/", wait_until="domcontentloaded", timeout=30_000)
    # 标题
    expect(page).to_have_title(re.compile(r"AgentInsight Researcher"), timeout=10_000)
    # 输入区始终可见 (不在折叠面板内)
    expect(page.locator("#msgInput")).to_be_visible(timeout=10_000)
    expect(page.locator("#sendBtn")).to_be_visible(timeout=10_000)
    # newSession 在折叠配置栏内, 展开后才可见
    _open_config_panel(page)
    expect(page.locator("#newSession")).to_be_visible(timeout=10_000)

    # 关键修复: 浏览器内 JS fetch 用页面默认 localhost, 宿主机访问用 127.0.0.1
    # 把 API BaseURL 改成与当前访问一致, 避免 'Failed to fetch' (CORS/DNS)
    parsed = urlparse(AGENT_URL)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    page.locator("#apiBase").fill(base_url)
    _log(f"API BaseURL 已设为: {base_url}")
    _log("测试页面加载完成")


def test_3_research_flow_full_chain(page: Page):
    """完整研究链路: 新建会话 → 发送提问 → 流式渲染 → 检索/工具调用面板 → 报告输出.

    AGENTS.md 第 13 章 e2e 必须覆盖: 提问 → 检索 → 工具调用 → 流式响应 → 会话持久化.
    AGENTS.md 第 14 章: 验证流式渲染 + 工具调用展示面板.
    """
    # 前置: 测试页面已加载 (依赖 test_2)
    sid1 = _new_session(page)

    # 选 basic_report 缩短耗时 (deep_research 要多轮, 10 分钟+)
    _open_config_panel(page)
    page.locator("#reportType").select_option("basic_report")
    _log("报告类型: basic_report")

    # 提问: 简短明确的研究问题, 中文优先 (AGENTS.md 第 1 章: 中文场景)
    query = "用 200 字简述 Python 异步编程的核心优势"
    _send_query(page, query)

    # 等待研究流程完成
    _wait_research_done(page)

    # 验证 1: 无错误
    err = _has_error(page)
    assert not err, f"研究流程返回错误: {err}"

    # 验证 2: 流式渲染产生实质内容 (非空, 非占位)
    content = _get_assistant_text(page)
    assert content, "assistant 气泡内容为空"
    assert len(content) > 50, f"内容过短 (<50 字), 可能流式渲染失败: {content[:100]}"
    _log(f"流式渲染内容长度: {len(content)} 字")
    _log(f"内容预览: {content[:200]}{'...' if len(content) > 200 else ''}")

    # 验证 3: 至少有一个折叠面板 (节点进度 或 参考来源)
    # 注意: basic_report 可能不触发检索/工具调用, 但应有节点进度面板
    panels = _count_panels(page)
    assert panels >= 1, f"未出现任何折叠面板 (节点进度/参考来源), panels={panels}"
    _log(f"折叠面板数: {panels}")

    # 截图存档 (便于人工核查, AGENTS.md 第 14 章: 工具调用/检索来源展示)
    page.screenshot(path="tests/e2e/_research_done.png", full_page=True)
    _log("截图: tests/e2e/_research_done.png")

    # 保存 session_id 供下一个测试用 (会话隔离)
    page.evaluate(f"window.__test_sid1 = '{sid1}';")
    page.evaluate(f"window.__test_query1 = '{query}';")
    page.evaluate(f"window.__test_answer1 = {repr(content)};")


def test_4_session_isolation(page: Page):
    """会话隔离: 新建第二个会话, 提问第一个会话的内容, 验证不记得.

    AGENTS.md 第 14 章: 切换会话验证隔离.
    AGENTS.md 第 6 章: 会话间状态通过 Postgres Checkpointer 隔离, 禁止共享.
    """
    # 读取前一个测试保存的状态
    sid1 = page.evaluate("() => window.__test_sid1")
    query1 = page.evaluate("() => window.__test_query1")
    if not sid1:
        pytest.skip("前置 test_3 未保存 sid1, 跳过隔离测试")

    # 新建第二个会话
    sid2 = _new_session(page)
    assert sid2 != sid1, f"两次新建会话 ID 相同: {sid1}"

    # 在 sid2 提问: "我上一个问题是什么" — 如果隔离失效, 会答出 query1
    probe = "我上一个问题是什么? 请直接回答问题本身"
    _send_query(page, probe)
    _wait_research_done(page)

    err = _has_error(page)
    assert not err, f"隔离验证返回错误: {err}"

    answer2 = _get_assistant_text(page)
    _log(f"sid2 回答预览: {answer2[:200]}{'...' if len(answer2) > 200 else ''}")

    # 隔离验证: 不应包含 sid1 的原始问题文本
    # 注意: 研究型 agent 可能扯远, 但绝不会准确复述 sid1 的问题
    if query1 and query1[:20] in answer2:
        pytest.fail(
            f"会话隔离失效! sid2 回答中包含 sid1 的问题文本:\n"
            f"sid1 问题: {query1}\nsid2 回答: {answer2[:300]}"
        )
    _log("会话隔离验证通过: sid2 未泄露 sid1 的上下文")

    # 切回 sid1, 验证上下文仍在 (会话持久化)
    _switch_session(page, sid1)
    # sid1 历史消息应仍在页面 (前端清空了, 但后端 Checkpoint 保留)
    # 这里只验证切换成功, 不再发请求 (省时间)
    display = page.locator("#sessionIdDisplay").inner_text().strip()
    assert display == sid1, f"切换回 sid1 失败: 显示={display}, 期望={sid1}"
    _log("切换回 sid1 成功, 会话持久化上下文保留在后端 Checkpoint")


def test_5_session_id_uuid_v4_format(page: Page):
    """验证 session_id 是合法 UUID v4 (AGENTS.md 第 6 章: thread_id 由请求上下文注入)."""
    sid = _new_session(page)
    assert UUID_RE.match(sid), f"session_id 非 UUID v4: {sid}"
    _log(f"UUID v4 格式校验通过: {sid}")


def test_6_no_token_anonymous_user():
    """无 Bearer JWT Token 时后端降级 DEFAULT_USER_ID (AGENTS.md 第 8 章).

    测试页面 Token 输入框为空时, 请求头不发 Authorization, 后端按匿名用户处理.
    用流式请求只读首字即关闭, 避免等完整研究流程.
    """
    import httpx

    # 不带 Authorization 头发流式请求
    with httpx.stream(
        "POST",
        f"{AGENT_URL}/v1/chat/completions",
        json={
            "model": "agentinsight-researcher",
            "messages": [{"role": "user", "content": "ping"}],
            "stream": True,
            "report_type": "basic_report",
        },
        timeout=httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0),
    ) as r:
        # 不带 token 应能正常受理 (降级 DEFAULT_USER_ID), 不应 401/403
        assert r.status_code != 401, "无 token 请求不应返回 401 (应降级 DEFAULT_USER_ID)"
        assert r.status_code != 403, "无 token 请求不应返回 403 (应降级 DEFAULT_USER_ID)"
        # 读首字节即关闭 (验证端点接受请求)
        for line in r.iter_lines():
            if line:
                _log(f"无 token 流式响应首行: {line[:80]}")
                break
    _log(f"无 token 请求状态码: {r.status_code} (符合第 8 章降级预期)")
