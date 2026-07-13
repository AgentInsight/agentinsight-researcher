"""PyMuPDFScraper 单元测试 (PDF 抓取器).

测试覆盖:
1. 实例化: url/session 字段正确赋值, name 类属性
2. URL 模式: session=None 返回空结果
3. URL 模式: 成功下载 + pypdf 提取文本
4. URL 模式: HTTP 错误返回空结果
5. 本地路径模式: 文件不存在返回空结果
6. 本地路径模式: 成功提取文本
7. _extract_from_file: pypdf ImportError 返回空
8. _extract_from_file: 解析异常返回空
9. _extract_from_file: 多页文本拼接
10. content_type 为 'pdf'

单元测试在构建期执行, 不依赖外部服务.
所有 httpx/pypdf/文件系统调用全部 mock, 不下载真实 PDF.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from src.skills.researcher.scrapers.pymupdf_scraper import PyMuPDFScraper


def _make_mock_stream_response() -> AsyncMock:
    """构造 mock httpx 流式响应."""
    resp = AsyncMock()
    resp.raise_for_status = MagicMock()

    async def _aiter_bytes(chunk_size: int = 65536):
        yield b"fake pdf bytes"

    resp.aiter_bytes = _aiter_bytes
    return resp


def _make_mock_session_stream() -> AsyncMock:
    """构造 mock httpx.AsyncClient (支持 stream 上下文管理器)."""
    session = AsyncMock()
    stream_cm = AsyncMock()
    stream_cm.__aenter__ = AsyncMock(return_value=_make_mock_stream_response())
    stream_cm.__aexit__ = AsyncMock(return_value=None)
    session.stream = MagicMock(return_value=stream_cm)
    return session


def _make_mock_pdf_reader(pages_text: list[str]) -> MagicMock:
    """构造 mock pypdf.PdfReader."""
    reader = MagicMock()
    page_mocks = []
    for text in pages_text:
        page = MagicMock()
        page.extract_text = MagicMock(return_value=text)
        page_mocks.append(page)
    reader.pages = page_mocks
    return reader


class TestPyMuPDFScraperInstantiation:
    """实例化测试."""

    def test_default_name(self) -> None:
        """类属性 name 应为 'pdf'."""
        assert PyMuPDFScraper.name == "pdf"

    def test_init_assigns_url_and_session(self) -> None:
        """__init__ 应正确赋值 url 与 session."""
        session = AsyncMock()
        scraper = PyMuPDFScraper("https://example.com/doc.pdf", session=session)
        assert scraper.url == "https://example.com/doc.pdf"
        assert scraper.session is session

    def test_init_session_defaults_none(self) -> None:
        """session 默认为 None."""
        scraper = PyMuPDFScraper("https://example.com/doc.pdf")
        assert scraper.session is None


class TestPyMuPDFScraperExtractFromFile:
    """_extract_from_file 静态方法测试 (纯函数, 不涉及 IO)."""

    def test_single_page_extraction(self) -> None:
        """单页 PDF 应正确提取文本."""
        mock_reader = _make_mock_pdf_reader(["第一页内容"])
        with patch("pypdf.PdfReader", return_value=mock_reader):
            result = PyMuPDFScraper._extract_from_file("/fake/path.pdf")
        assert result == "第一页内容"

    def test_multi_page_extraction(self) -> None:
        """多页 PDF 应以 \\n\\n 拼接."""
        mock_reader = _make_mock_pdf_reader(["第一页", "第二页", "第三页"])
        with patch("pypdf.PdfReader", return_value=mock_reader):
            result = PyMuPDFScraper._extract_from_file("/fake/path.pdf")
        assert result == "第一页\n\n第二页\n\n第三页"

    def test_empty_page_text(self) -> None:
        """空页文本 (extract_text 返回 None) 应转为空字符串."""
        mock_reader = _make_mock_pdf_reader([None, "有内容"])
        with patch("pypdf.PdfReader", return_value=mock_reader):
            result = PyMuPDFScraper._extract_from_file("/fake/path.pdf")
        assert result == "\n\n有内容"

    def test_import_error_returns_empty(self) -> None:
        """pypdf 未安装时应返回空字符串."""
        with patch.dict("sys.modules", {"pypdf": None}):
            result = PyMuPDFScraper._extract_from_file("/fake/path.pdf")
        assert result == ""

    def test_parse_exception_returns_empty(self) -> None:
        """pypdf 解析异常时应返回空字符串."""
        with patch("pypdf.PdfReader", side_effect=Exception("解析失败")):
            result = PyMuPDFScraper._extract_from_file("/fake/path.pdf")
        assert result == ""


class TestPyMuPDFScraperScrapeUrl:
    """URL 模式 (http://开头) 测试."""

    @pytest.mark.asyncio
    async def test_url_no_session_returns_empty(self) -> None:
        """URL 模式 session=None 时应返回空结果."""
        scraper = PyMuPDFScraper("https://example.com/doc.pdf", session=None)
        result = await scraper.scrape()
        assert result["url"] == "https://example.com/doc.pdf"
        assert result["content"] == ""
        assert result["title"] == ""
        assert result["image_urls"] == []
        assert result["content_type"] == "pdf"

    @pytest.mark.asyncio
    async def test_url_success_extraction(self) -> None:
        """URL 模式成功下载 + pypdf 提取文本."""
        session = _make_mock_session_stream()
        mock_reader = _make_mock_pdf_reader(["PDF 文本内容"])

        with (
            patch("pypdf.PdfReader", return_value=mock_reader),
            patch("os.path.exists", return_value=False),
            patch("os.unlink"),
        ):
            scraper = PyMuPDFScraper("https://example.com/doc.pdf", session=session)
            result = await scraper.scrape()

        assert result["content_type"] == "pdf"
        assert result["content"] == "PDF 文本内容"
        assert result["title"] == ""
        assert result["image_urls"] == []

    @pytest.mark.asyncio
    async def test_url_http_error_returns_empty(self) -> None:
        """URL 模式 HTTP 错误应返回空结果 (异常路径不含 content_type)."""
        session = AsyncMock()
        stream_cm = AsyncMock()
        stream_cm.__aenter__ = AsyncMock(
            side_effect=Exception("HTTP 500 Internal Server Error")
        )
        stream_cm.__aexit__ = AsyncMock(return_value=None)
        session.stream = MagicMock(return_value=stream_cm)

        with (
            patch("os.path.exists", return_value=False),
            patch("os.unlink"),
        ):
            scraper = PyMuPDFScraper("https://example.com/doc.pdf", session=session)
            result = await scraper.scrape()

        # 异常路径返回的 dict 不含 content_type (与成功路径不同)
        assert result["content"] == ""
        assert result["title"] == ""
        assert result["image_urls"] == []
        assert result["url"] == "https://example.com/doc.pdf"

    @pytest.mark.asyncio
    async def test_url_pypdf_import_error_returns_empty(self) -> None:
        """URL 模式 pypdf 未安装时 content 应为空."""
        session = _make_mock_session_stream()

        with (
            patch.dict("sys.modules", {"pypdf": None}),
            patch("os.path.exists", return_value=False),
            patch("os.unlink"),
        ):
            scraper = PyMuPDFScraper("https://example.com/doc.pdf", session=session)
            result = await scraper.scrape()

        assert result["content"] == ""
        assert result["content_type"] == "pdf"


class TestPyMuPDFScraperScrapeLocalPath:
    """本地路径模式测试."""

    @pytest.mark.asyncio
    async def test_local_file_not_exists_returns_empty(self) -> None:
        """本地文件不存在时应返回空结果."""
        with patch("os.path.exists", return_value=False):
            scraper = PyMuPDFScraper("/nonexistent/path.pdf")
            result = await scraper.scrape()
        assert result["content"] == ""
        assert result["content_type"] == "pdf"

    @pytest.mark.asyncio
    async def test_local_file_success_extraction(self) -> None:
        """本地文件存在时应成功提取文本."""
        mock_reader = _make_mock_pdf_reader(["本地 PDF 内容"])
        with (
            patch("os.path.exists", return_value=True),
            patch("pypdf.PdfReader", return_value=mock_reader),
        ):
            scraper = PyMuPDFScraper("/local/path/doc.pdf")
            result = await scraper.scrape()
        assert result["content"] == "本地 PDF 内容"
        assert result["content_type"] == "pdf"
        assert result["title"] == ""
        assert result["image_urls"] == []

    @pytest.mark.asyncio
    async def test_local_file_multi_page(self) -> None:
        """本地多页 PDF 应正确拼接."""
        mock_reader = _make_mock_pdf_reader(["页1", "页2", "页3"])
        with (
            patch("os.path.exists", return_value=True),
            patch("pypdf.PdfReader", return_value=mock_reader),
        ):
            scraper = PyMuPDFScraper("/local/multi.pdf")
            result = await scraper.scrape()
        assert result["content"] == "页1\n\n页2\n\n页3"
