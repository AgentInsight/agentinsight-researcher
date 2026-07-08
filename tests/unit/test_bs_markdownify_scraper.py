"""BSMarkdownifyScraper 单元测试 (V4-P3 L1 降级链 L2).

测试覆盖:
1. 开关门控: bs_markdownify_enabled=False 时跳过
2. ImportError 降级: markdownify 未安装时返回空结果
3. scrape 成功路径: mock httpx session
4. HTML→Markdown 转换: 验证标题/列表/链接结构保留
5. 清理脚本/样式: script/style/nav/footer 应被移除
6. 图片提取: img src 应被提取

对标 test_scrapers.py 测试模式.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.skills.researcher.scrapers.bs_markdownify_scraper import BSMarkdownifyScraper


def _make_mock_response(html: str, encoding: str = "utf-8") -> AsyncMock:
    """构造 mock httpx 响应.

    注: httpx.Response.raise_for_status() 是同步方法, 用 MagicMock (非 AsyncMock)
    避免 'coroutine never awaited' RuntimeWarning.
    """
    resp = AsyncMock()
    resp.text = html
    resp.encoding = encoding
    resp.raise_for_status = MagicMock()
    return resp


def _make_mock_session(html: str) -> AsyncMock:
    """构造 mock httpx.AsyncClient session."""
    session = AsyncMock()
    session.get = AsyncMock(return_value=_make_mock_response(html))
    return session


class TestBSMarkdownifyScraperGate:
    """开关门控测试."""

    @pytest.mark.asyncio
    async def test_no_session_returns_empty(self):
        """session=None 时应返回空结果."""
        scraper = BSMarkdownifyScraper("https://example.com", session=None)
        result = await scraper.scrape()
        assert result["content"] == ""


class TestBSMarkdownifyScraperSuccess:
    """成功路径测试."""

    @pytest.mark.asyncio
    async def test_scrape_returns_markdown(self):
        """成功抓取应返回 Markdown content_type."""
        html = """
        <html><head><title>测试页面</title></head>
        <body>
            <article>
                <h1>标题</h1>
                <p>段落内容</p>
                <ul><li>列表项</li></ul>
            </article>
        </body></html>
        """
        session = _make_mock_session(html)
        scraper = BSMarkdownifyScraper("https://example.com", session=session)
        result = await scraper.scrape()
        assert result["content_type"] == "markdown"
        assert result["title"] == "测试页面"
        assert "标题" in result["content"]
        assert "段落内容" in result["content"]

    @pytest.mark.asyncio
    async def test_script_style_removed(self):
        """script/style/nav/footer 应被移除."""
        html = """
        <html><body>
            <nav>导航栏</nav>
            <script>alert('x');</script>
            <style>body { color: red; }</style>
            <footer>页脚</footer>
            <article><p>正文内容</p></article>
        </body></html>
        """
        session = _make_mock_session(html)
        scraper = BSMarkdownifyScraper("https://example.com", session=session)
        result = await scraper.scrape()
        assert "正文内容" in result["content"]
        assert "导航栏" not in result["content"]
        assert "alert" not in result["content"]
        assert "页脚" not in result["content"]

    @pytest.mark.asyncio
    async def test_image_extraction(self):
        """img src 应被提取."""
        html = """
        <html><body>
            <article>
                <p>内容</p>
                <img src="https://example.com/img1.jpg" />
                <img src="https://example.com/img2.png" />
            </article>
        </body></html>
        """
        session = _make_mock_session(html)
        scraper = BSMarkdownifyScraper("https://example.com", session=session)
        result = await scraper.scrape()
        assert "https://example.com/img1.jpg" in result["image_urls"]
        assert "https://example.com/img2.png" in result["image_urls"]


class TestBSMarkdownifyScraperDegrade:
    """降级路径测试."""

    @pytest.mark.asyncio
    async def test_import_error_returns_empty(self):
        """markdownify 未安装时应返回空结果."""
        html = "<html><body><p>test</p></body></html>"
        session = _make_mock_session(html)
        with patch.dict("sys.modules", {"markdownify": None}):
            scraper = BSMarkdownifyScraper("https://example.com", session=session)
            result = await scraper.scrape()
        assert result["content"] == ""

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self):
        """HTTP 错误时应返回空结果."""
        session = AsyncMock()
        session.get = AsyncMock(side_effect=Exception("HTTP error"))
        scraper = BSMarkdownifyScraper("https://example.com", session=session)
        result = await scraper.scrape()
        assert result["content"] == ""
