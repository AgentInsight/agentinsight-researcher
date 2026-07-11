"""API 测试: 文件上传端点 /v1/files.

测试约定:
- API 测试在 docker compose up -d 且全部容器 service_healthy 后执行
- 测试目标地址从环境变量 AGENT_URL 注入

文件上传约束:
- 用户私有数据按 agent_id + user_id 隔离
- 校验文件大小 (max_upload_size_mb, 默认 50MB) → 超限 413
- 校验扩展名白名单 (pdf/docx/md/txt/html/csv/xlsx/pptx) → 不支持 415

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/api/test_files.py -v -m api
"""

from __future__ import annotations

import io
import os

import httpx
import pytest

# 测试目标地址从环境变量注入
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")

# API 测试超时 60s
API_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0)

# 默认上传大小限制 (与 settings.max_upload_size_mb 一致)
DEFAULT_MAX_SIZE_MB = 50


@pytest.mark.api
def test_upload_txt() -> None:
    """验证上传 .txt 文件: POST /v1/files → 201 + file_id."""
    content = b"This is a test file for API testing.\n" * 10
    files = {"file": ("test_upload.txt", io.BytesIO(content), "text/plain")}
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(f"{AGENT_URL}/v1/files", files=files)
    assert r.status_code == 201, f"上传 .txt 非 201: {r.status_code} {r.text}"
    data = r.json()
    assert "file_id" in data, f"响应缺少 file_id: {data}"
    assert "filename" in data
    assert data["filename"] == "test_upload.txt"
    assert "size_bytes" in data
    assert data["size_bytes"] == len(content)
    assert data["extension"] == "txt"


@pytest.mark.api
def test_upload_md() -> None:
    """验证上传 .md 文件: POST /v1/files → 201."""
    content = b"# Test Markdown\n\nThis is a **test** file.\n"
    files = {"file": ("test_upload.md", io.BytesIO(content), "text/markdown")}
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(f"{AGENT_URL}/v1/files", files=files)
    assert r.status_code == 201, f"上传 .md 非 201: {r.status_code} {r.text}"
    data = r.json()
    assert "file_id" in data
    assert data["extension"] == "md"


@pytest.mark.api
def test_upload_too_large() -> None:
    """验证超大文件上传: 超过 max_upload_size_mb → 413.

    安全约束 (大小限制).
    默认限制 50MB, 发送 51MB 触发 413.
    """
    # 构造略超限制的文件内容 (51MB)
    oversized_mb = DEFAULT_MAX_SIZE_MB + 1
    # 使用 chunked 写入避免一次性分配大 BytesIO
    chunk = b"x" * (1024 * 1024)  # 1MB
    buf = io.BytesIO()
    for _ in range(oversized_mb):
        buf.write(chunk)
    buf.seek(0)

    files = {"file": ("too_large.txt", buf, "text/plain")}
    with httpx.Client(
        timeout=httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0)
    ) as client:
        r = client.post(f"{AGENT_URL}/v1/files", files=files)
    assert r.status_code == 413, f"超大文件应返回 413, 实际: {r.status_code} {r.text[:200]}"


@pytest.mark.api
def test_upload_invalid_ext() -> None:
    """验证不支持扩展名: .exe → 415.

    扩展名白名单 (pdf/docx/md/txt/html/csv/xlsx/pptx).
    """
    content = b"MZ\x90\x00"  # PE 文件头伪造成 exe
    files = {"file": ("malicious.exe", io.BytesIO(content), "application/octet-stream")}
    with httpx.Client(timeout=API_TIMEOUT) as client:
        r = client.post(f"{AGENT_URL}/v1/files", files=files)
    assert r.status_code == 415, f"不支持扩展名应返回 415, 实际: {r.status_code} {r.text[:200]}"
