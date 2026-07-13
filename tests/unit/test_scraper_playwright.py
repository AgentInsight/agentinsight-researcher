"""PlaywrightScraper 单元测试 (JS 渲染抓取器).

测试覆盖:
1. 实例化: url/session 字段正确赋值, name 类属性
2. ImportError 降级: playwright 未安装时返回空结果
3. 成功路径: mock _PlaywrightPool + browser/context/page
4. 域名 Semaphore 限流: domain_sem 非 None 时加锁
5. 池化 acquire 失败 → 降级同步模式 (自建 browser)
6. 异常处理: scrape 失败返回空结果
7. 图片提取: 调用 get_relevant_images_from_html 评分排序
8. context.close 清理: finally 块释放资源
9. _get_domain 域名提取

单元测试在构建期执行, 不依赖外部服务.
所有 Playwright/httpx 调用全部 mock, 不启动真实 chromium.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def reset_pool() -> None:
    """每个测试前重置 _PlaywrightPool 单例状态 (ClassVar)."""
    from src.skills.researcher.scrapers.playwright_scraper import _PlaywrightPool

    _PlaywrightPool._instance = None
    _PlaywrightPool._lock = None
    _PlaywrightPool._pooled_browsers.clear()
    yield
    _PlaywrightPool._instance = None
    _PlaywrightPool._lock = None
    _PlaywrightPool._pooled_browsers.clear()


def _make_mock_page(
    *,
    inner_text: str = "页面正文内容",
    title: str = "页面标题",
    html_content: str = "<html><body><p>内容</p></body></html>",
) -> AsyncMock:
    """构造 mock Playwright page."""
    page = AsyncMock()
    page.goto = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.evaluate = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.inner_text = AsyncMock(return_value=inner_text)
    page.title = AsyncMock(return_value=title)
    page.content = AsyncMock(return_value=html_content)
    return page


def _make_mock_context(page: AsyncMock | None = None) -> AsyncMock:
    """构造 mock Playwright BrowserContext."""
    context = AsyncMock()
    context.new_page = AsyncMock(return_value=page or _make_mock_page())
    context.close = AsyncMock()
    return context


def _make_mock_browser(context: AsyncMock | None = None) -> AsyncMock:
    """构造 mock Playwright browser."""
    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=context or _make_mock_context())
    browser.close = AsyncMock()
    return browser


def _make_mock_pooled_browser(
    browser: AsyncMock | None = None,
    processing_count: int = 0,
) -> MagicMock:
    """构造 mock _PooledBrowser."""
    pooled = MagicMock()
    pooled.browser = browser or _make_mock_browser()
    pooled.playwright = MagicMock()
    pooled.processing_count = processing_count
    pooled.domain_semaphores = {}
    pooled.stopping = False
    pooled.acquire_domain = AsyncMock(return_value=None)
    pooled.stop = AsyncMock()
    return pooled


class TestPlaywrightScraperInstantiation:
    """实例化测试."""

    def test_default_name(self) -> None:
        """类属性 name 应为 'playwright'."""
        from src.skills.researcher.scrapers.playwright_scraper import PlaywrightScraper

        assert PlaywrightScraper.name == "playwright"

    def test_init_assigns_url_and_session(self) -> None:
        """__init__ 应正确赋值 url 与 session."""
        from src.skills.researcher.scrapers.playwright_scraper import PlaywrightScraper

        session = AsyncMock()
        scraper = PlaywrightScraper("https://example.com", session=session)
        assert scraper.url == "https://example.com"
        assert scraper.session is session

    def test_init_session_defaults_none(self) -> None:
        """session 默认为 None."""
        from src.skills.researcher.scrapers.playwright_scraper import PlaywrightScraper

        scraper = PlaywrightScraper("https://example.com")
        assert scraper.session is None


class TestPlaywrightScraperGetDomain:
    """_get_domain 域名提取测试."""

    def test_basic_url(self) -> None:
        """基本 URL 应提取二级域名."""
        from src.skills.researcher.scrapers.playwright_scraper import _get_domain

        assert _get_domain("https://www.example.com/path") == "example.com"

    def test_two_parts(self) -> None:
        """两段域名应原样返回."""
        from src.skills.researcher.scrapers.playwright_scraper import _get_domain

        assert _get_domain("https://example.com") == "example.com"

    def test_deep_subdomain(self) -> None:
        """多级子域名应提取二级域名."""
        from src.skills.researcher.scrapers.playwright_scraper import _get_domain

        assert _get_domain("https://a.b.c.example.com/page") == "example.com"


class TestPlaywrightScraperScrape:
    """scrape 方法测试."""

    @pytest.mark.asyncio
    async def test_import_error_returns_empty(self, reset_pool: None) -> None:
        """playwright 未安装时应返回空结果."""
        from src.skills.researcher.scrapers.playwright_scraper import PlaywrightScraper

        scraper = PlaywrightScraper("https://example.com")
        # 模拟 playwright.async_api 导入失败
        with patch.dict(
            "sys.modules",
            {"playwright": None, "playwright.async_api": None},
        ):
            result = await scraper.scrape()
        assert result["url"] == "https://example.com"
        assert result["content"] == ""
        assert result["title"] == ""
        assert result["image_urls"] == []

    @pytest.mark.asyncio
    async def test_scrape_success_via_pool(self, reset_pool: None) -> None:
        """通过浏览器池成功抓取."""
        from src.skills.researcher.scrapers.playwright_scraper import PlaywrightScraper

        page = _make_mock_page(
            inner_text="这是渲染后的正文",
            title="测试标题",
            html_content='<html><body><img src="https://example.com/img.jpg" class="featured"></body></html>',
        )
        context = _make_mock_context(page=page)
        browser = _make_mock_browser(context=context)
        pooled = _make_mock_pooled_browser(browser=browser)

        with (
            patch(
                "src.skills.researcher.scrapers.playwright_scraper._PlaywrightPool.acquire",
                AsyncMock(return_value=(browser, None, pooled)),
            ),
            patch(
                "src.skills.researcher.scrapers.playwright_scraper._PlaywrightPool.release",
                AsyncMock(),
            ),
            patch(
                "src.skills.researcher.scrapers.playwright_scraper.get_relevant_images_from_html",
                return_value=["https://example.com/img.jpg"],
                create=True,
            ),
            patch(
                "src.skills.researcher.scrapers.utils.get_relevant_images_from_html",
                return_value=["https://example.com/img.jpg"],
            ),
        ):
            scraper = PlaywrightScraper("https://example.com")
            result = await scraper.scrape()

        assert result["content_type"] == "html"
        assert result["title"] == "测试标题"
        assert result["content"] == "这是渲染后的正文"
        assert result["url"] == "https://example.com"
        assert "https://example.com/img.jpg" in result["image_urls"]
        # context 应被关闭
        context.close.assert_awaited()

    @pytest.mark.asyncio
    async def test_domain_semaphore_throttling(self, reset_pool: None) -> None:
        """domain_sem 非 None 时应加锁执行."""
        from src.skills.researcher.scrapers.playwright_scraper import PlaywrightScraper

        page = _make_mock_page(inner_text="正文", title="标题")
        context = _make_mock_context(page=page)
        browser = _make_mock_browser(context=context)
        pooled = _make_mock_pooled_browser(browser=browser)
        domain_sem = asyncio.Semaphore(1)

        with (
            patch(
                "src.skills.researcher.scrapers.playwright_scraper._PlaywrightPool.acquire",
                AsyncMock(return_value=(browser, domain_sem, pooled)),
            ),
            patch(
                "src.skills.researcher.scrapers.playwright_scraper._PlaywrightPool.release",
                AsyncMock(),
            ),
            patch(
                "src.skills.researcher.scrapers.utils.get_relevant_images_from_html",
                return_value=[],
            ),
        ):
            scraper = PlaywrightScraper("https://example.com")
            result = await scraper.scrape()

        assert result["content"] == "正文"
        context.close.assert_awaited()

    @pytest.mark.asyncio
    async def test_pool_acquire_failure_fallback_mode(self, reset_pool: None) -> None:
        """池化 acquire 失败时应降级到同步模式 (自建 browser)."""
        from src.skills.researcher.scrapers.playwright_scraper import PlaywrightScraper

        page = _make_mock_page(inner_text="降级模式正文", title="降级标题")
        context = _make_mock_context(page=page)
        browser = _make_mock_browser(context=context)

        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=browser)
        mock_playwright.stop = AsyncMock()

        with (
            patch(
                "src.skills.researcher.scrapers.playwright_scraper._PlaywrightPool.acquire",
                AsyncMock(side_effect=Exception("池启动失败")),
            ),
            patch(
                "src.skills.researcher.scrapers.playwright_scraper._build_launch_kwargs",
                AsyncMock(return_value={"headless": True}),
            ),
            patch(
                "playwright.async_api.async_playwright",
                create=True,
            ) as mock_async_pw,
            patch(
                "src.skills.researcher.scrapers.utils.get_relevant_images_from_html",
                return_value=[],
            ),
        ):
            mock_async_pw.return_value.start = AsyncMock(return_value=mock_playwright)
            scraper = PlaywrightScraper("https://example.com")
            result = await scraper.scrape()

        assert result["content"] == "降级模式正文"
        assert result["title"] == "降级标题"
        # 降级模式应关闭 browser + playwright
        browser.close.assert_awaited()
        mock_playwright.stop.assert_awaited()

    @pytest.mark.asyncio
    async def test_scrape_exception_returns_empty(self, reset_pool: None) -> None:
        """scrape 过程中抛异常应返回空结果."""
        from src.skills.researcher.scrapers.playwright_scraper import PlaywrightScraper

        browser = _make_mock_browser()
        pooled = _make_mock_pooled_browser(browser=browser)

        with (
            patch(
                "src.skills.researcher.scrapers.playwright_scraper._PlaywrightPool.acquire",
                AsyncMock(return_value=(browser, None, pooled)),
            ),
            patch(
                "src.skills.researcher.scrapers.playwright_scraper._PlaywrightPool.release",
                AsyncMock(),
            ),
        ):
            # browser.new_context 抛异常
            browser.new_context = AsyncMock(side_effect=Exception("context 创建失败"))
            scraper = PlaywrightScraper("https://example.com")
            result = await scraper.scrape()

        assert result["url"] == "https://example.com"
        assert result["content"] == ""
        assert result["title"] == ""
        assert result["image_urls"] == []

    @pytest.mark.asyncio
    async def test_image_extraction_via_utils(self, reset_pool: None) -> None:
        """图片提取应调用 get_relevant_images_from_html 评分排序."""
        from src.skills.researcher.scrapers.playwright_scraper import PlaywrightScraper

        page = _make_mock_page(
            html_content='<html><body><img src="/a.jpg" class="hero"></body></html>',
        )
        context = _make_mock_context(page=page)
        browser = _make_mock_browser(context=context)
        pooled = _make_mock_pooled_browser(browser=browser)

        mock_get_images = MagicMock(
            return_value=["https://example.com/a.jpg"],
        )

        with (
            patch(
                "src.skills.researcher.scrapers.playwright_scraper._PlaywrightPool.acquire",
                AsyncMock(return_value=(browser, None, pooled)),
            ),
            patch(
                "src.skills.researcher.scrapers.playwright_scraper._PlaywrightPool.release",
                AsyncMock(),
            ),
            patch(
                "src.skills.researcher.scrapers.utils.get_relevant_images_from_html",
                mock_get_images,
            ),
        ):
            scraper = PlaywrightScraper("https://example.com")
            result = await scraper.scrape()

        mock_get_images.assert_called_once()
        assert result["image_urls"] == ["https://example.com/a.jpg"]


class TestPlaywrightScraperContextCleanup:
    """资源清理测试."""

    @pytest.mark.asyncio
    async def test_context_closed_in_finally(self, reset_pool: None) -> None:
        """finally 块应关闭 context (即使 scrape 抛异常)."""
        from src.skills.researcher.scrapers.playwright_scraper import PlaywrightScraper

        page = _make_mock_page()
        context = _make_mock_context(page=page)
        # 让 page.inner_text 抛异常, 触发 finally
        page.inner_text = AsyncMock(side_effect=Exception("inner_text 失败"))
        browser = _make_mock_browser(context=context)
        pooled = _make_mock_pooled_browser(browser=browser)

        with (
            patch(
                "src.skills.researcher.scrapers.playwright_scraper._PlaywrightPool.acquire",
                AsyncMock(return_value=(browser, None, pooled)),
            ),
            patch(
                "src.skills.researcher.scrapers.playwright_scraper._PlaywrightPool.release",
                AsyncMock(),
            ),
        ):
            scraper = PlaywrightScraper("https://example.com")
            result = await scraper.scrape()

        # 异常被捕获, 返回空结果
        assert result["content"] == ""
        # context.close 仍应被调用
        context.close.assert_awaited()
        # pooled 槽位应被释放
        # (release 已 mock, 验证调用即可)
