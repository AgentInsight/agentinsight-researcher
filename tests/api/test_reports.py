"""API 测试: 报告下载端点 /v1/reports/{report_id}/download.

测试约定:
- API 测试在 docker compose up -d 且全部容器 service_healthy 后执行
- 测试目标地址从环境变量 AGENT_URL 注入
- 测试用例独立可重复运行, 不依赖执行顺序

本文件聚焦报告下载端点的 HTTP 契约测试:
- GET /v1/reports/{report_id}/download?format=markdown → 200 + text/markdown
- GET /v1/reports/{nonexistent_uuid}/download → 404 (报告不存在)
- GET /v1/reports/{report_id}/download?format=xml → 400 (不支持的格式)

报告下载端点 (routes.py download_report):
- report_id 应为 UUID, 不符合格式时走 session_id 兼容分支
- 支持格式: markdown / html / pdf / docx / json
- 数据隔离: report.user_id 必须匹配当前 user_id (匿名用户跳过)

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/api/test_reports.py -v -m api

注意: test_download_report_markdown 需先生成报告 (走完整研究流程, 耗时较长),
使用 module 级 fixture 共享 report_id 避免重复研究.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

# 测试目标地址从环境变量注入, 禁止硬编码
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")

# 快速端点超时 (404/400 校验, 不走研究)
QUICK_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)

# 研究流程超时 (生成报告需要完整研究, 300s)
REPORT_GEN_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)


@pytest.fixture(scope="module")
def generated_report_id() -> str:
    """生成一份报告用于下载测试 (module 级共享, 避免重复研究).

    走完整 basic_report 研究流程, 从响应中提取 report_id.
    研究失败或无 report_id 时 skip (不 fail, 因可能环境配置缺失).
    """
    sid = f"test_report_{uuid.uuid4().hex[:12]}"
    query = "用 200 字简述 Python 异步编程的核心优势"
    with httpx.Client(timeout=REPORT_GEN_TIMEOUT) as client:
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
    if r.status_code != 200:
        pytest.skip(f"研究请求失败 (status={r.status_code}), 无法生成报告用于下载测试")
    data = r.json()
    report_id = data.get("report_id")
    if not report_id:
        pytest.skip("响应未包含 report_id (可能 report_store 未配置), 跳过下载测试")
    return str(report_id)


# ========== 下载 Markdown 报告 ==========


@pytest.mark.api
def test_download_report_markdown(generated_report_id: str) -> None:
    """下载 Markdown 报告: GET /v1/reports/{report_id}/download?format=markdown → 200.

    报告按 report_id 下载, 支持多格式.
    验证: 200 + content-type 含 text/markdown + Content-Disposition 附件头.
    """
    with httpx.Client(timeout=QUICK_TIMEOUT) as client:
        r = client.get(
            f"{AGENT_URL}/v1/reports/{generated_report_id}/download",
            params={"format": "markdown"},
        )
    assert r.status_code == 200, f"下载 Markdown 报告非 200: {r.status_code} {r.text[:300]}"
    # content-type 应含 text/markdown
    content_type = r.headers.get("content-type", "")
    assert "text/markdown" in content_type, f"content-type 非 text/markdown: {content_type}"
    # Content-Disposition 应为附件 (attachment)
    disposition = r.headers.get("content-disposition", "")
    assert "attachment" in disposition, f"Content-Disposition 非 attachment: {disposition}"
    assert "filename=" in disposition, f"Content-Disposition 缺少 filename: {disposition}"
    # 响应体非空 (报告内容)
    assert r.content, "下载报告内容为空"


# ========== 报告不存在返回 404 ==========


@pytest.mark.api
def test_download_report_not_found_404() -> None:
    """报告 ID 不存在返回 404: GET /v1/reports/{nonexistent_uuid}/download → 404.

    API 测试应覆盖错误码.
    routes.py: report_id 合法 UUID 但 DB 中不存在 → session_id 兼容分支 →
    仍无匹配 → 404.
    """
    # 生成合法 UUID 但不存在的 report_id
    nonexistent_uuid = str(uuid.uuid4())
    with httpx.Client(timeout=QUICK_TIMEOUT) as client:
        r = client.get(f"{AGENT_URL}/v1/reports/{nonexistent_uuid}/download")
    assert r.status_code == 404, (
        f"不存在的 report_id 应返回 404, 实际: {r.status_code} {r.text[:200]}"
    )


# ========== 无效格式参数返回 400 ==========


@pytest.mark.api
def test_download_report_invalid_id_400(generated_report_id: str) -> None:
    """无效格式参数返回 400: GET /v1/reports/{report_id}/download?format=xml → 400.

    API 测试应覆盖错误码.
    routes.py: format 不在 [markdown/html/pdf/docx/json] 白名单 → 400.
    注意: "无效报告 ID" (非 UUID 格式) 实际走 session_id 兼容分支,
    不匹配时返回 404 而非 400; 真正返回 400 的场景是 format 参数不合法.
    """
    with httpx.Client(timeout=QUICK_TIMEOUT) as client:
        r = client.get(
            f"{AGENT_URL}/v1/reports/{generated_report_id}/download",
            params={"format": "xml"},  # 不支持的格式
        )
    assert r.status_code == 400, (
        f"不支持的格式 xml 应返回 400, 实际: {r.status_code} {r.text[:200]}"
    )
