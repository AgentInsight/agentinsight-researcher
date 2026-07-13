"""ArxivScraper 单元测试 (Arxiv 论文抓取器).

测试覆盖:
1. 实例化: url/session 字段正确赋值, name 类属性
2. arxiv_id 提取: URL 末段 + .pdf 后缀剥离
3. 成功路径: mock arxiv 库元数据 + 摘要内容
4. 元数据为空: 返回空结果
5. PDF 全文提取: 成功时附加 Full Content
6. PDF 全文提取失败: 仅用摘要
7. ImportError: arxiv 库未安装返回空
8. 异常处理: 抓取失败返回空
9. _extract_pdf_text: pypdf 多页提取
10. content_type 为 'arxiv'

单元测试在构建期执行, 不依赖外部服务.
所有 arxiv/pypdf/httpx 调用全部 mock, 不下载真实论文.
"""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from src.skills.researcher.scrapers.arxiv_scraper import ArxivScraper


def _make_mock_arxiv_result(
    *,
    title: str = "测试论文标题",
    authors: list[str] | None = None,
    summary: str = "这是论文摘要内容",
    pdf_url: str = "https://arxiv.org/pdf/2401.12345",
    published: datetime.datetime | None = None,
) -> MagicMock:
    """构造 mock arxiv.Result 对象."""
    result = MagicMock()
    result.title = title
    result.authors = authors or ["Author A", "Author B"]
    result.summary = summary
    result.pdf_url = pdf_url
    result.published = published or datetime.datetime(2024, 1, 15)
    # authors 的 str() 转换: arxiv 库中 Result.authors 是 Author 对象列表
    result.authors = [MagicMock(__str__=lambda self, a=a: a) for a in (authors or ["Author A", "Author B"])]
    return result


def _make_mock_arxiv_module(result: MagicMock | None = None) -> MagicMock:
    """构造 mock arxiv 模块 (Client/Search)."""
    arxiv_mod = MagicMock()
    client = MagicMock()
    search = MagicMock()
    results_list = [result] if result is not None else []
    client.results = MagicMock(return_value=results_list)
    arxiv_mod.Client = MagicMock(return_value=client)
    arxiv_mod.Search = MagicMock(return_value=search)
    return arxiv_mod


class TestArxivScraperInstantiation:
    """实例化测试."""

    def test_default_name(self) -> None:
        """类属性 name 应为 'arxiv'."""
        assert ArxivScraper.name == "arxiv"

    def test_init_assigns_url_and_session(self) -> None:
        """__init__ 应正确赋值 url 与 session."""
        session = AsyncMock()
        scraper = ArxivScraper("https://arxiv.org/abs/2401.12345", session=session)
        assert scraper.url == "https://arxiv.org/abs/2401.12345"
        assert scraper.session is session

    def test_init_session_defaults_none(self) -> None:
        """session 默认为 None."""
        scraper = ArxivScraper("https://arxiv.org/abs/2401.12345")
        assert scraper.session is None


class TestArxivScraperScrape:
    """scrape 方法测试."""

    @pytest.mark.asyncio
    async def test_scrape_success_metadata_only(self) -> None:
        """成功抓取元数据 (PDF 全文提取失败时仅用摘要)."""
        mock_result = _make_mock_arxiv_result(
            title="深度学习论文",
            summary="本文提出了一种新的深度学习方法",
        )
        mock_arxiv = _make_mock_arxiv_module(mock_result)

        with (
            patch.dict("sys.modules", {"arxiv": mock_arxiv}),
            patch(
                "src.skills.researcher.scrapers.arxiv_scraper._download_pdf_with_retry",
                AsyncMock(side_effect=Exception("下载失败")),
            ),
            patch("os.path.exists", return_value=False),
        ):
            scraper = ArxivScraper("https://arxiv.org/abs/2401.12345")
            result = await scraper.scrape()

        assert result["content_type"] == "arxiv"
        assert result["title"] == "深度学习论文"
        assert "深度学习论文" in result["content"]
        assert "本文提出了一种新的深度学习方法" in result["content"]
        assert "Authors:" in result["content"]
        assert "Published:" in result["content"]
        assert result["image_urls"] == []
        assert result["url"] == "https://arxiv.org/abs/2401.12345"

    @pytest.mark.asyncio
    async def test_scrape_success_with_full_text(self) -> None:
        """成功抓取元数据 + PDF 全文."""
        mock_result = _make_mock_arxiv_result(
            title="全文论文",
            summary="摘要内容",
            pdf_url="https://arxiv.org/pdf/2401.99999",
        )
        mock_arxiv = _make_mock_arxiv_module(mock_result)

        # mock pypdf reader
        mock_page = MagicMock()
        mock_page.extract_text = MagicMock(return_value="这是 PDF 全文内容")
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        with (
            patch.dict("sys.modules", {"arxiv": mock_arxiv}),
            patch(
                "src.skills.researcher.scrapers.arxiv_scraper._download_pdf_with_retry",
                AsyncMock(),
            ),
            patch("pypdf.PdfReader", return_value=mock_reader),
            patch("os.path.exists", return_value=True),
            patch("os.unlink"),
        ):
            scraper = ArxivScraper("https://arxiv.org/abs/2401.99999")
            result = await scraper.scrape()

        assert result["title"] == "全文论文"
        assert "摘要内容" in result["content"]
        assert "Full Content:" in result["content"]
        assert "这是 PDF 全文内容" in result["content"]

    @pytest.mark.asyncio
    async def test_scrape_no_results_returns_empty(self) -> None:
        """arxiv 查询无结果时应返回空."""
        mock_arxiv = _make_mock_arxiv_module(result=None)

        with patch.dict("sys.modules", {"arxiv": mock_arxiv}):
            scraper = ArxivScraper("https://arxiv.org/abs/nonexistent")
            result = await scraper.scrape()

        assert result["url"] == "https://arxiv.org/abs/nonexistent"
        assert result["content"] == ""
        assert result["title"] == ""
        assert result["image_urls"] == []

    @pytest.mark.asyncio
    async def test_arxiv_import_error_returns_empty(self) -> None:
        """arxiv 库未安装时应返回空结果."""
        with patch.dict("sys.modules", {"arxiv": None}):
            scraper = ArxivScraper("https://arxiv.org/abs/2401.12345")
            result = await scraper.scrape()
        assert result["content"] == ""
        assert result["url"] == "https://arxiv.org/abs/2401.12345"

    @pytest.mark.asyncio
    async def test_scrape_exception_returns_empty(self) -> None:
        """scrape 过程中抛异常应返回空结果."""
        mock_arxiv = MagicMock()
        mock_arxiv.Client = MagicMock(side_effect=Exception("Client 创建失败"))

        with patch.dict("sys.modules", {"arxiv": mock_arxiv}):
            scraper = ArxivScraper("https://arxiv.org/abs/2401.12345")
            result = await scraper.scrape()
        assert result["content"] == ""
        assert result["url"] == "https://arxiv.org/abs/2401.12345"

    @pytest.mark.asyncio
    async def test_pdf_url_with_pdf_suffix(self) -> None:
        """URL 以 .pdf 结尾时应正确剥离后缀提取 arxiv_id."""
        mock_result = _make_mock_arxiv_result(title="PDF URL 论文")
        mock_arxiv = _make_mock_arxiv_module(mock_result)

        with (
            patch.dict("sys.modules", {"arxiv": mock_arxiv}),
            patch(
                "src.skills.researcher.scrapers.arxiv_scraper._download_pdf_with_retry",
                AsyncMock(side_effect=Exception("下载失败")),
            ),
            patch("os.path.exists", return_value=False),
        ):
            scraper = ArxivScraper("https://arxiv.org/pdf/2401.54321.pdf")
            result = await scraper.scrape()

        # arxiv.Search 应被调用 (arxiv_id 提取成功)
        assert mock_arxiv.Search.called
        assert result["title"] == "PDF URL 论文"

    @pytest.mark.asyncio
    async def test_content_format_contains_sections(self) -> None:
        """内容应包含 Title/Authors/Published/Summary 段落."""
        mock_result = _make_mock_arxiv_result(
            title="格式测试",
            summary="格式摘要",
        )
        mock_arxiv = _make_mock_arxiv_module(mock_result)

        with (
            patch.dict("sys.modules", {"arxiv": mock_arxiv}),
            patch(
                "src.skills.researcher.scrapers.arxiv_scraper._download_pdf_with_retry",
                AsyncMock(side_effect=Exception("下载失败")),
            ),
            patch("os.path.exists", return_value=False),
        ):
            scraper = ArxivScraper("https://arxiv.org/abs/2401.00001")
            result = await scraper.scrape()

        content = result["content"]
        assert "Title: 格式测试" in content
        assert "Authors:" in content
        assert "Published: 2024-01-15" in content
        assert "Summary: 格式摘要" in content


class TestArxivScraperExtractPdfText:
    """_extract_pdf_text 静态方法测试."""

    def test_single_page(self) -> None:
        """单页 PDF 文本提取."""
        mock_page = MagicMock()
        mock_page.extract_text = MagicMock(return_value="单页内容")
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        with patch("pypdf.PdfReader", return_value=mock_reader):
            result = ArxivScraper._extract_pdf_text("/fake/path.pdf")
        assert result == "单页内容"

    def test_multi_page_join(self) -> None:
        """多页 PDF 应以 \\n\\n 拼接."""
        pages = ["页1", "页2", "页3"]
        mock_reader = MagicMock()
        mock_reader.pages = [
            MagicMock(extract_text=MagicMock(return_text=t)) if False
            else (lambda t=t: MagicMock(extract_text=MagicMock(return_value=t)))()
            for t in pages
        ]

        with patch("pypdf.PdfReader", return_value=mock_reader):
            result = ArxivScraper._extract_pdf_text("/fake/path.pdf")
        assert result == "页1\n\n页2\n\n页3"

    def test_import_error_returns_empty(self) -> None:
        """pypdf 未安装时应返回空字符串."""
        with patch.dict("sys.modules", {"pypdf": None}):
            result = ArxivScraper._extract_pdf_text("/fake/path.pdf")
        assert result == ""

    def test_parse_exception_returns_empty(self) -> None:
        """pypdf 解析异常时应返回空字符串."""
        with patch("pypdf.PdfReader", side_effect=Exception("解析失败")):
            result = ArxivScraper._extract_pdf_text("/fake/path.pdf")
        assert result == ""
