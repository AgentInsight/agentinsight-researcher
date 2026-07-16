"""BeautifulSoupScraper 单元测试 (默认主力抓取器).

测试覆盖:
1. 实例化: url/session 字段正确赋值, name 类属性
2. 空 session 返回空结果
3. 成功路径: mock httpx 响应, 验证 content/title/image_urls/content_type
4. HTML 清理: script/style/nav/footer/header 应被移除
5. 图片提取: 仅 http/https 开头的 src, 限 top 4
6. 标题提取: <title> 标签内容
7. HTML 大小上限: 超过 5MB 截断
8. 异常处理: HTTP 错误/解析异常返回空结果
9. data-src 回退: img data-src 属性

单元测试在构建期执行, 不依赖外部服务.
所有 httpx 调用全部 mock, 不发起真实网络请求.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit

from src.skills.researcher.scrapers.beautiful_soup_scraper import BeautifulSoupScraper


def _make_mock_response(html: str, encoding: str = "utf-8") -> AsyncMock:
    """构造 mock httpx 响应.

    注: httpx.Response.raise_for_status() 是同步方法, 用 MagicMock (非 AsyncMock)
    避免 'coroutine never awaited' RuntimeWarning.
    P1-16 修复后源码访问 response.content (bytes) 而非 response.text (str),
    故 mock 需同时设置 content (bytes) 和 encoding 以支持 .decode() 调用.
    """
    resp = AsyncMock()
    resp.text = html
    resp.content = html.encode(encoding)
    resp.encoding = encoding
    resp.raise_for_status = MagicMock()
    return resp


def _make_mock_session(html: str) -> AsyncMock:
    """构造 mock httpx.AsyncClient session."""
    session = AsyncMock()
    session.get = AsyncMock(return_value=_make_mock_response(html))
    return session


class TestBeautifulSoupScraperInstantiation:
    """实例化测试."""

    def test_default_name(self) -> None:
        """类属性 name 应为 'bs'."""
        assert BeautifulSoupScraper.name == "bs"

    def test_init_assigns_url_and_session(self) -> None:
        """__init__ 应正确赋值 url 与 session."""
        session = AsyncMock()
        scraper = BeautifulSoupScraper("https://example.com", session=session)
        assert scraper.url == "https://example.com"
        assert scraper.session is session

    def test_init_session_defaults_none(self) -> None:
        """session 默认为 None."""
        scraper = BeautifulSoupScraper("https://example.com")
        assert scraper.session is None


class TestBeautifulSoupScraperScrape:
    """scrape 方法测试."""

    @pytest.mark.asyncio
    async def test_no_session_returns_empty(self) -> None:
        """session=None 时应返回空结果."""
        scraper = BeautifulSoupScraper("https://example.com", session=None)
        result = await scraper.scrape()
        assert result["url"] == "https://example.com"
        assert result["content"] == ""
        assert result["title"] == ""
        assert result["image_urls"] == []

    @pytest.mark.asyncio
    async def test_scrape_success_basic(self) -> None:
        """成功抓取应返回 content_type='html'."""
        html = """
        <html><head><title>测试页面</title></head>
        <body>
            <article>
                <h1>标题</h1>
                <p>段落内容</p>
            </article>
        </body></html>
        """
        session = _make_mock_session(html)
        scraper = BeautifulSoupScraper("https://example.com", session=session)
        result = await scraper.scrape()
        assert result["content_type"] == "html"
        assert result["title"] == "测试页面"
        assert "标题" in result["content"]
        assert "段落内容" in result["content"]
        assert result["url"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_script_style_removed(self) -> None:
        """script/style/nav/footer/header 应被移除."""
        html = """
        <html><body>
            <header>页眉</header>
            <nav>导航栏</nav>
            <script>alert('x');</script>
            <style>body { color: red; }</style>
            <footer>页脚</footer>
            <article><p>正文内容</p></article>
        </body></html>
        """
        session = _make_mock_session(html)
        scraper = BeautifulSoupScraper("https://example.com", session=session)
        result = await scraper.scrape()
        assert "正文内容" in result["content"]
        assert "导航栏" not in result["content"]
        assert "alert" not in result["content"]
        assert "页脚" not in result["content"]
        assert "页眉" not in result["content"]

    @pytest.mark.asyncio
    async def test_image_extraction_top4(self) -> None:
        """img src 应被提取, 限 top 4."""
        html = """
        <html><body>
            <article>
                <p>内容</p>
                <img src="https://example.com/img1.jpg" />
                <img src="https://example.com/img2.png" />
                <img src="https://example.com/img3.gif" />
                <img src="https://example.com/img4.webp" />
                <img src="https://example.com/img5.bmp" />
            </article>
        </body></html>
        """
        session = _make_mock_session(html)
        scraper = BeautifulSoupScraper("https://example.com", session=session)
        result = await scraper.scrape()
        assert len(result["image_urls"]) == 4
        assert "https://example.com/img1.jpg" in result["image_urls"]
        assert "https://example.com/img5.bmp" not in result["image_urls"]

    @pytest.mark.asyncio
    async def test_image_skips_non_http(self) -> None:
        """非 http/https 开头的 img src 应被跳过."""
        html = """
        <html><body>
            <article>
                <img src="/relative/path.jpg" />
                <img src="data:image/png;base64,abc" />
                <img src="https://example.com/valid.jpg" />
            </article>
        </body></html>
        """
        session = _make_mock_session(html)
        scraper = BeautifulSoupScraper("https://example.com", session=session)
        result = await scraper.scrape()
        assert result["image_urls"] == ["https://example.com/valid.jpg"]

    @pytest.mark.asyncio
    async def test_data_src_fallback(self) -> None:
        """img data-src 属性应作为 src 回退."""
        html = """
        <html><body>
            <article>
                <img data-src="https://example.com/lazy.jpg" />
            </article>
        </body></html>
        """
        session = _make_mock_session(html)
        scraper = BeautifulSoupScraper("https://example.com", session=session)
        result = await scraper.scrape()
        assert "https://example.com/lazy.jpg" in result["image_urls"]

    @pytest.mark.asyncio
    async def test_title_extraction(self) -> None:
        """<title> 标签内容应被提取为 title."""
        html = "<html><head><title>我的网页标题</title></head><body><p>内容</p></body></html>"
        session = _make_mock_session(html)
        scraper = BeautifulSoupScraper("https://example.com", session=session)
        result = await scraper.scrape()
        assert result["title"] == "我的网页标题"

    @pytest.mark.asyncio
    async def test_empty_title_when_missing(self) -> None:
        """无 <title> 标签时 title 应为空字符串."""
        html = "<html><body><p>无标题页面</p></body></html>"
        session = _make_mock_session(html)
        scraper = BeautifulSoupScraper("https://example.com", session=session)
        result = await scraper.scrape()
        assert result["title"] == ""

    @pytest.mark.asyncio
    async def test_html_truncation_over_5mb(self) -> None:
        """HTML 超过 5MB 应被截断 (不抛异常)."""
        # 构造 6MB HTML (1MB = 1024*1024 字符)
        large_content = "x" * (6 * 1024 * 1024)
        html = f"<html><body><p>{large_content}</p></body></html>"
        session = _make_mock_session(html)
        scraper = BeautifulSoupScraper("https://example.com", session=session)
        result = await scraper.scrape()
        # 截断后内容应存在 (不抛异常, 返回非空 content)
        assert result["content_type"] == "html"
        assert len(result["content"]) > 0


class TestBeautifulSoupScraperErrorHandling:
    """异常处理测试."""

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self) -> None:
        """HTTP 请求异常时应返回空结果."""
        session = AsyncMock()
        session.get = AsyncMock(side_effect=Exception("HTTP error"))
        scraper = BeautifulSoupScraper("https://example.com", session=session)
        result = await scraper.scrape()
        assert result["url"] == "https://example.com"
        assert result["content"] == ""
        assert result["title"] == ""
        assert result["image_urls"] == []

    @pytest.mark.asyncio
    async def test_raise_for_status_error_returns_empty(self) -> None:
        """raise_for_status 抛异常时应返回空结果."""
        resp = AsyncMock()
        resp.text = "<html></html>"
        resp.encoding = "utf-8"
        resp.raise_for_status = MagicMock(side_effect=Exception("404 Not Found"))
        session = AsyncMock()
        session.get = AsyncMock(return_value=resp)
        scraper = BeautifulSoupScraper("https://example.com", session=session)
        result = await scraper.scrape()
        assert result["content"] == ""
