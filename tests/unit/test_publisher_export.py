"""单元测试: Publisher.export_multiple_formats 多格式并行导出 (P2-01/P1-4).

验证 src/skills/researcher/publisher.py 的 export_multiple_formats:
- 一次报告生成多种格式 (markdown/html/pdf_path/docx/json/latex/epub)
- asyncio.gather 并行执行 (P1-4), return_exceptions=True 隔离单格式失败
- user_id/session_id 透传给 publish() 用于 trace_chain
- 未知格式跳过 (warning, 不影响其他格式)
- 单格式异常隔离 (不阻断其他格式导出)

AGENTS.md 第 13 章: 单元测试不依赖外部服务 (PDF/EPUB 用 mock 验证调用).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.config.settings import Settings
from src.skills.researcher.publisher import Publisher

pytestmark = pytest.mark.unit


@pytest.fixture()
def publisher() -> Publisher:
    """构造 Publisher 实例 (隔离 .env)."""
    return Publisher(settings=Settings(_env_file=None))


# ========== 多格式并行导出 ==========


@pytest.mark.asyncio
async def test_export_multiple_formats_returns_all_formats(
    publisher: Publisher,
) -> None:
    """一次导出 markdown + html + json → 返回 dict 含三种格式内容."""
    report_md = "# Test Report\n\ncontent here"
    formats = ["markdown", "html", "json"]

    result = await publisher.export_multiple_formats(
        report_md,
        formats,
        title="Test",
        user_id="u1",
        session_id="s1",
    )

    assert "markdown" in result
    assert "html" in result
    assert "json" in result
    assert result["markdown"] == report_md
    assert isinstance(result["html"], str)
    assert "<html" in result["html"] or "<!DOCTYPE" in result["html"]
    assert isinstance(result["json"], str)


@pytest.mark.asyncio
async def test_export_multiple_formats_passes_user_id_session_id_to_publish(
    publisher: Publisher,
) -> None:
    """user_id/session_id 透传给 publish() 用于 trace_chain."""
    with patch.object(publisher, "publish", new=AsyncMock()) as mock_publish:
        # mock publish 返回不同 format 的结果
        mock_publish.side_effect = lambda report_md, **kwargs: {
            "format": kwargs["output_format"],
            "content": f"content-{kwargs['output_format']}",
            "path": None,
        }

        await publisher.export_multiple_formats(
            "report",
            ["markdown", "html"],
            user_id="user-123",
            session_id="session-456",
            title="T",
        )

    # 验证每次 publish 调用都含 user_id/session_id
    for call in mock_publish.call_args_list:
        assert call.kwargs["user_id"] == "user-123"
        assert call.kwargs["session_id"] == "session-456"


@pytest.mark.asyncio
async def test_export_multiple_formats_unknown_format_skipped(
    publisher: Publisher,
) -> None:
    """未知格式 (如 "xml") → 跳过, 不影响其他格式."""
    result = await publisher.export_multiple_formats(
        "report",
        ["markdown", "xml", "unknown"],
        title="T",
    )

    # 仅 markdown 被导出, xml/unknown 被跳过
    assert "markdown" in result
    assert "xml" not in result
    assert "unknown" not in result


@pytest.mark.asyncio
async def test_export_multiple_formats_case_insensitive(
    publisher: Publisher,
) -> None:
    """格式名大小写不敏感 (MARKDOWN/markdown 等价)."""
    result = await publisher.export_multiple_formats(
        "report",
        ["MARKDOWN", "Html"],
        title="T",
    )

    # 大写格式名应被识别 (转小写后查表)
    assert "markdown" in result
    assert "html" in result


@pytest.mark.asyncio
async def test_export_multiple_formats_single_failure_isolated(
    publisher: Publisher,
) -> None:
    """单格式异常 → 隔离, 不影响其他格式 (return_exceptions=True)."""
    # 让 html 抛异常, markdown 正常
    original_publish = publisher.publish

    async def _mock_publish(report_md: str, **kwargs: Any) -> dict[str, Any]:
        if kwargs["output_format"] == "html":
            raise RuntimeError("html conversion failed")
        return await original_publish(report_md, **kwargs)

    with patch.object(publisher, "publish", side_effect=_mock_publish):
        result = await publisher.export_multiple_formats(
            "report",
            ["markdown", "html", "json"],
            title="T",
        )

    # markdown + json 正常返回, html 被隔离跳过
    assert "markdown" in result
    assert "json" in result
    assert "html" not in result  # 异常格式不在结果中


@pytest.mark.asyncio
async def test_export_multiple_formats_pdf_returns_path(
    publisher: Publisher,
) -> None:
    """pdf 格式 → 结果 key 为 pdf_path, value 为文件路径."""
    fake_pdf_path = "/tmp/test_report.pdf"

    async def _mock_publish(report_md: str, **kwargs: Any) -> dict[str, Any]:
        if kwargs["output_format"] == "pdf":
            return {"format": "pdf", "content": None, "path": fake_pdf_path}
        return {"format": kwargs["output_format"], "content": "x", "path": None}

    with patch.object(publisher, "publish", side_effect=_mock_publish):
        result = await publisher.export_multiple_formats(
            "report",
            ["markdown", "pdf"],
            session_id="sess-1",
        )

    assert result["markdown"] == "x"
    assert result["pdf_path"] == fake_pdf_path  # pdf 取 path 字段


@pytest.mark.asyncio
async def test_export_multiple_formats_empty_formats_returns_empty_dict(
    publisher: Publisher,
) -> None:
    """空 formats 列表 → 返回空 dict (不调用 publish)."""
    with patch.object(publisher, "publish", new=AsyncMock()) as mock_publish:
        result = await publisher.export_multiple_formats(
            "report",
            [],
            title="T",
        )

    assert result == {}
    mock_publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_export_multiple_formats_all_unknown_formats_returns_empty(
    publisher: Publisher,
) -> None:
    """全部格式均未知 → 返回空 dict (全部跳过)."""
    with patch.object(publisher, "publish", new=AsyncMock()) as mock_publish:
        result = await publisher.export_multiple_formats(
            "report",
            ["xml", "csv", "yaml"],
            title="T",
        )

    assert result == {}
    mock_publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_export_multiple_formats_parallel_execution(
    publisher: Publisher,
) -> None:
    """多格式并行执行 (asyncio.gather), 验证 publish 被并发调用."""
    call_times: list[float] = []
    import time

    async def _slow_publish(report_md: str, **kwargs: Any) -> dict[str, Any]:
        call_times.append(time.time())
        await asyncio.sleep(0.1)  # 模拟耗时
        return {"format": kwargs["output_format"], "content": "x", "path": None}

    with patch.object(publisher, "publish", side_effect=_slow_publish):
        import time as _time

        start = _time.time()
        await publisher.export_multiple_formats(
            "report",
            ["markdown", "html", "json", "latex"],
            title="T",
        )
        elapsed = _time.time() - start

    # 4 个格式并行 (各 0.1s), 总耗时 < 0.4s (串行则需 0.4s+)
    assert elapsed < 0.35, f"并行执行耗时过长: {elapsed:.2f}s"
    # 4 个格式都被调用
    assert len(call_times) == 4


# ========== 透传参数完整性 ==========


@pytest.mark.asyncio
async def test_export_multiple_formats_passes_all_metadata_to_publish(
    publisher: Publisher,
) -> None:
    """title/sources/agent_role_server/research_mode 全部透传给 publish."""
    sources = [{"url": "http://x", "title": "X"}]

    with patch.object(publisher, "publish", new=AsyncMock()) as mock_publish:
        mock_publish.return_value = {"format": "json", "content": "{}", "path": None}

        await publisher.export_multiple_formats(
            "report",
            ["json"],
            title="My Title",
            sources=sources,
            agent_role_server="analyst",
            research_mode="general",
            user_id="u1",
            session_id="s1",
        )

    call_kwargs = mock_publish.call_args.kwargs
    assert call_kwargs["title"] == "My Title"
    assert call_kwargs["sources"] == sources
    assert call_kwargs["agent_role_server"] == "analyst"
    assert call_kwargs["research_mode"] == "general"
    assert call_kwargs["user_id"] == "u1"
    assert call_kwargs["session_id"] == "s1"
