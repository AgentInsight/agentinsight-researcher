"""MarkItDownScraper 单元测试 (Office 文档抓取器).

测试覆盖:
1. 实例化: url/session 字段正确赋值, name 类属性
2. _get_suffix: 各类 Office 文档后缀提取
3. 成功路径: mock httpx + MarkItDown 转换
4. ImportError: markitdown 未安装返回空
5. HTTP 错误: 下载失败返回空
6. 异常处理: 转换失败返回空
7. 临时文件清理: finally 块删除临时文件
8. content_type 为 'document'
9. 标题提取: MarkItDown 返回的 title

单元测试在构建期执行, 不依赖外部服务.
所有 httpx/markitdown/文件系统调用全部 mock, 不下载真实文档.

注: markitdown 模块可能未安装, 测试通过 patch.dict("sys.modules", ...)
注入 mock 模块来模拟已安装/未安装状态.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from src.skills.researcher.scrapers.markitdown_scraper import MarkItDownScraper
from src.skills.researcher.scrapers.markitdown_scraper import _get_suffix


def _make_mock_httpx_response(
    *,
    content: bytes = b"fake docx bytes",
    status_code: int = 200,
) -> AsyncMock:
    """构造 mock httpx 响应."""
    resp = AsyncMock()
    resp.content = content
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    return resp


def _make_mock_markitdown_result(
    *,
    text_content: str = "转换后的 Markdown 内容",
    title: str = "文档标题",
) -> MagicMock:
    """构造 mock MarkItDown.convert 返回结果."""
    result = MagicMock()
    result.text_content = text_content
    result.title = title
    return result


def _make_mock_markitdown_module(
    *,
    convert_return: MagicMock | None = None,
    convert_side_effect: Exception | None = None,
) -> MagicMock:
    """构造 mock markitdown 模块.

    markitdown 模块可能未安装, 通过 patch.dict("sys.modules", ...)
    注入此 mock 模块来模拟已安装状态.
    """
    mock_module = MagicMock()
    mock_md_instance = MagicMock()
    if convert_side_effect is not None:
        mock_md_instance.convert = MagicMock(side_effect=convert_side_effect)
    else:
        mock_md_instance.convert = MagicMock(return_value=convert_return or _make_mock_markitdown_result())
    mock_module.MarkItDown = MagicMock(return_value=mock_md_instance)
    return mock_module


def _patch_httpx_client(mock_resp: AsyncMock) -> patch:
    """构造 httpx.AsyncClient patcher (返回 mock client)."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return patch("httpx.AsyncClient", return_value=mock_client)


class TestMarkItDownScraperInstantiation:
    """实例化测试."""

    def test_default_name(self) -> None:
        """类属性 name 应为 'markitdown'."""
        assert MarkItDownScraper.name == "markitdown"

    def test_init_assigns_url_and_session(self) -> None:
        """__init__ 应正确赋值 url 与 session."""
        session = AsyncMock()
        scraper = MarkItDownScraper("https://example.com/doc.docx", session=session)
        assert scraper.url == "https://example.com/doc.docx"
        assert scraper.session is session

    def test_init_session_defaults_none(self) -> None:
        """session 默认为 None."""
        scraper = MarkItDownScraper("https://example.com/doc.docx")
        assert scraper.session is None


class TestGetSuffix:
    """_get_suffix 后缀提取测试."""

    def test_docx(self) -> None:
        assert _get_suffix("https://example.com/doc.docx") == ".docx"

    def test_pptx(self) -> None:
        assert _get_suffix("https://example.com/slides.pptx") == ".pptx"

    def test_xlsx(self) -> None:
        assert _get_suffix("https://example.com/data.xlsx") == ".xlsx"

    def test_doc(self) -> None:
        assert _get_suffix("https://example.com/old.doc") == ".doc"

    def test_ppt(self) -> None:
        assert _get_suffix("https://example.com/old.ppt") == ".ppt"

    def test_xls(self) -> None:
        assert _get_suffix("https://example.com/old.xls") == ".xls"

    def test_no_match_defaults_docx(self) -> None:
        """无匹配后缀时默认返回 .docx."""
        assert _get_suffix("https://example.com/unknown.txt") == ".docx"

    def test_case_insensitive(self) -> None:
        """大写后缀应被识别 (URL 转 lower)."""
        assert _get_suffix("https://example.com/DOC.DOCX") == ".docx"

    def test_url_with_query_params(self) -> None:
        """带查询参数的 URL 应正确提取后缀."""
        assert _get_suffix("https://example.com/doc.docx?v=1&t=2") == ".docx"


class TestMarkItDownScraperScrape:
    """scrape 方法测试."""

    @pytest.mark.asyncio
    async def test_scrape_success(self) -> None:
        """成功下载 + MarkItDown 转换."""
        mock_resp = _make_mock_httpx_response(content=b"fake docx")
        mock_md_result = _make_mock_markitdown_result(
            text_content="# 标题\n\n正文内容",
            title="文档标题",
        )
        mock_md_module = _make_mock_markitdown_module(convert_return=mock_md_result)

        with (
            _patch_httpx_client(mock_resp),
            patch.dict("sys.modules", {"markitdown": mock_md_module}),
            patch("os.remove"),
        ):
            scraper = MarkItDownScraper("https://example.com/doc.docx")
            result = await scraper.scrape()

        assert result["content_type"] == "document"
        assert result["title"] == "文档标题"
        assert "正文内容" in result["content"]
        assert result["image_urls"] == []
        assert result["url"] == "https://example.com/doc.docx"

    @pytest.mark.asyncio
    async def test_import_error_returns_empty(self) -> None:
        """markitdown 未安装时应返回空结果 (content=None)."""
        mock_resp = _make_mock_httpx_response()

        with (
            _patch_httpx_client(mock_resp),
            patch.dict("sys.modules", {"markitdown": None}),
            patch("os.remove"),
        ):
            scraper = MarkItDownScraper("https://example.com/doc.docx")
            result = await scraper.scrape()

        assert result["content"] is None
        assert result["title"] == ""
        assert result["image_urls"] == []

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self) -> None:
        """HTTP 下载失败时应返回空结果."""
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock(side_effect=Exception("404 Not Found"))

        with (
            _patch_httpx_client(mock_resp),
            patch("os.remove"),
        ):
            scraper = MarkItDownScraper("https://example.com/missing.docx")
            result = await scraper.scrape()

        assert result["content"] is None
        assert result["title"] == ""

    @pytest.mark.asyncio
    async def test_convert_exception_returns_empty(self) -> None:
        """MarkItDown 转换异常时应返回空结果."""
        mock_resp = _make_mock_httpx_response()
        mock_md_module = _make_mock_markitdown_module(
            convert_side_effect=Exception("转换失败"),
        )

        with (
            _patch_httpx_client(mock_resp),
            patch.dict("sys.modules", {"markitdown": mock_md_module}),
            patch("os.remove"),
        ):
            scraper = MarkItDownScraper("https://example.com/doc.docx")
            result = await scraper.scrape()

        assert result["content"] is None

    @pytest.mark.asyncio
    async def test_empty_text_content(self) -> None:
        """MarkItDown 返回空 text_content 时 content 应为空字符串."""
        mock_resp = _make_mock_httpx_response()
        mock_md_result = _make_mock_markitdown_result(
            text_content=None,
            title="标题",
        )
        mock_md_module = _make_mock_markitdown_module(convert_return=mock_md_result)

        with (
            _patch_httpx_client(mock_resp),
            patch.dict("sys.modules", {"markitdown": mock_md_module}),
            patch("os.remove"),
        ):
            scraper = MarkItDownScraper("https://example.com/doc.docx")
            result = await scraper.scrape()

        assert result["content"] == ""
        assert result["title"] == "标题"

    @pytest.mark.asyncio
    async def test_temp_file_cleanup(self) -> None:
        """成功转换后应删除临时文件."""
        mock_resp = _make_mock_httpx_response()
        mock_md_module = _make_mock_markitdown_module()

        with (
            _patch_httpx_client(mock_resp),
            patch.dict("sys.modules", {"markitdown": mock_md_module}),
            patch("os.remove") as mock_remove,
        ):
            scraper = MarkItDownScraper("https://example.com/doc.docx")
            await scraper.scrape()

        mock_remove.assert_called()

    @pytest.mark.asyncio
    async def test_temp_file_cleanup_silent_on_oserror(self) -> None:
        """临时文件删除失败 (OSError) 应静默忽略, 不抛异常."""
        mock_resp = _make_mock_httpx_response()
        mock_md_module = _make_mock_markitdown_module()

        with (
            _patch_httpx_client(mock_resp),
            patch.dict("sys.modules", {"markitdown": mock_md_module}),
            patch("os.remove", side_effect=OSError("删除失败")),
        ):
            scraper = MarkItDownScraper("https://example.com/doc.docx")
            result = await scraper.scrape()

        # OSError 被静默忽略, 返回正常结果
        assert result["content_type"] == "document"
