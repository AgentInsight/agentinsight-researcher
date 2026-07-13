"""FirecrawlScraper 单元测试 (Firecrawl 商业服务抓取器).

测试覆盖:
1. 实例化: 从 settings 读取 api_key/api_url
2. 无 api_key: 返回空结果
3. SDK 成功路径: mock firecrawl.FirecrawlApp
4. SDK ImportError → 降级 HTTP API
5. SDK 异常 → 降级 HTTP API
6. HTTP API 成功路径: mock httpx
7. HTTP API 异常: 返回空结果
8. _normalize_result: 各种 payload 结构
9. 图片提取: ogImages/images 字段
10. content_type 为 'markdown'

单元测试在构建期执行, 不依赖外部服务.
所有 firecrawl/httpx 调用全部 mock, 不调用真实 API.

注: firecrawl 模块可能未安装, 测试通过 patch.dict("sys.modules", ...)
注入 mock 模块来模拟已安装/未安装状态.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from src.skills.researcher.scrapers.firecrawl_scraper import FirecrawlScraper


def _make_mock_settings(
    *,
    api_key: str | None = "test-firecrawl-key",
    api_url: str = "https://api.firecrawl.dev",
) -> MagicMock:
    """构造 mock Settings (绕过 .env 加载)."""
    settings = MagicMock()
    settings.firecrawl_api_key = api_key
    settings.firecrawl_api_url = api_url
    return settings


def _make_firecrawl_payload(
    *,
    markdown: str = "# 标题\n\n正文内容",
    title: str = "页面标题",
    images: list[str] | None = None,
    html: str = "",
) -> dict:
    """构造 Firecrawl v1 响应 payload."""
    data = {
        "markdown": markdown,
        "html": html,
        "metadata": {
            "title": title,
            "sourceURL": "https://example.com",
        },
    }
    if images:
        data["metadata"]["ogImages"] = images
    return {"success": True, "data": data}


def _make_mock_httpx_response(
    *,
    json_data: dict | None = None,
) -> AsyncMock:
    """构造 mock httpx 响应."""
    resp = AsyncMock()
    resp.json = MagicMock(return_value=json_data or {})
    resp.raise_for_status = MagicMock()
    return resp


def _make_mock_firecrawl_module(
    *,
    scrape_url_return: dict | None = None,
    scrape_url_side_effect: Exception | None = None,
) -> MagicMock:
    """构造 mock firecrawl 模块.

    firecrawl 模块可能未安装, 通过 patch.dict("sys.modules", ...)
    注入此 mock 模块来模拟已安装状态.
    """
    mock_module = MagicMock()
    mock_app = MagicMock()
    if scrape_url_side_effect is not None:
        mock_app.scrape_url = MagicMock(side_effect=scrape_url_side_effect)
    else:
        mock_app.scrape_url = MagicMock(
            return_value=scrape_url_return or _make_firecrawl_payload()
        )
    mock_module.FirecrawlApp = MagicMock(return_value=mock_app)
    return mock_module


def _patch_httpx_client(mock_resp: AsyncMock) -> patch:
    """构造 httpx.AsyncClient patcher (返回 mock client)."""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return patch("httpx.AsyncClient", return_value=mock_client)


class TestFirecrawlScraperInstantiation:
    """实例化测试."""

    def test_default_name(self) -> None:
        """类属性 name 应为 'firecrawl'."""
        assert FirecrawlScraper.name == "firecrawl"

    def test_init_reads_api_key_from_settings(self) -> None:
        """__init__ 应从 settings 读取 api_key."""
        mock_settings = _make_mock_settings(api_key="my-key", api_url="https://custom.api")
        with patch(
            "src.skills.researcher.scrapers.firecrawl_scraper.get_settings",
            return_value=mock_settings,
        ):
            scraper = FirecrawlScraper("https://example.com")
        assert scraper.api_key == "my-key"
        assert scraper.api_url == "https://custom.api"
        assert scraper.url == "https://example.com"

    def test_init_api_key_none(self) -> None:
        """api_key 为 None 时也应正常实例化."""
        mock_settings = _make_mock_settings(api_key=None)
        with patch(
            "src.skills.researcher.scrapers.firecrawl_scraper.get_settings",
            return_value=mock_settings,
        ):
            scraper = FirecrawlScraper("https://example.com")
        assert scraper.api_key is None


class TestFirecrawlScraperScrape:
    """scrape 方法测试."""

    @pytest.mark.asyncio
    async def test_no_api_key_returns_empty(self) -> None:
        """api_key 未配置时应返回空结果."""
        mock_settings = _make_mock_settings(api_key=None)
        with patch(
            "src.skills.researcher.scrapers.firecrawl_scraper.get_settings",
            return_value=mock_settings,
        ):
            scraper = FirecrawlScraper("https://example.com")
            result = await scraper.scrape()
        assert result["url"] == "https://example.com"
        assert result["content"] == ""
        assert result["title"] == ""
        assert result["image_urls"] == []

    @pytest.mark.asyncio
    async def test_sdk_success(self) -> None:
        """SDK 成功路径 (firecrawl-py 已安装)."""
        mock_settings = _make_mock_settings()
        payload = _make_firecrawl_payload(
            markdown="# SDK 标题\n\nSDK 正文",
            title="SDK 页面",
            images=["https://example.com/img1.jpg", "https://example.com/img2.jpg"],
        )
        mock_firecrawl_module = _make_mock_firecrawl_module(scrape_url_return=payload)

        with (
            patch(
                "src.skills.researcher.scrapers.firecrawl_scraper.get_settings",
                return_value=mock_settings,
            ),
            patch.dict("sys.modules", {"firecrawl": mock_firecrawl_module}),
        ):
            scraper = FirecrawlScraper("https://example.com")
            result = await scraper.scrape()

        assert result["content_type"] == "markdown"
        assert result["title"] == "SDK 页面"
        assert "SDK 正文" in result["content"]
        assert "https://example.com/img1.jpg" in result["image_urls"]
        assert "https://example.com/img2.jpg" in result["image_urls"]

    @pytest.mark.asyncio
    async def test_sdk_import_error_fallback_http(self) -> None:
        """SDK ImportError 时应降级走 HTTP API."""
        mock_settings = _make_mock_settings()
        payload = _make_firecrawl_payload(
            markdown="HTTP API 内容",
            title="HTTP 标题",
        )
        mock_resp = _make_mock_httpx_response(json_data=payload)

        with (
            patch(
                "src.skills.researcher.scrapers.firecrawl_scraper.get_settings",
                return_value=mock_settings,
            ),
            patch.dict("sys.modules", {"firecrawl": None}),
            _patch_httpx_client(mock_resp),
        ):
            scraper = FirecrawlScraper("https://example.com")
            result = await scraper.scrape()

        assert result["content_type"] == "markdown"
        assert result["title"] == "HTTP 标题"
        assert "HTTP API 内容" in result["content"]

    @pytest.mark.asyncio
    async def test_sdk_exception_fallback_http(self) -> None:
        """SDK 抛异常时应降级走 HTTP API."""
        mock_settings = _make_mock_settings()
        payload = _make_firecrawl_payload(markdown="降级内容", title="降级标题")
        mock_resp = _make_mock_httpx_response(json_data=payload)

        mock_firecrawl_module = _make_mock_firecrawl_module(
            scrape_url_side_effect=Exception("SDK 调用失败"),
        )

        with (
            patch(
                "src.skills.researcher.scrapers.firecrawl_scraper.get_settings",
                return_value=mock_settings,
            ),
            patch.dict("sys.modules", {"firecrawl": mock_firecrawl_module}),
            _patch_httpx_client(mock_resp),
        ):
            scraper = FirecrawlScraper("https://example.com")
            result = await scraper.scrape()

        assert result["title"] == "降级标题"
        assert "降级内容" in result["content"]

    @pytest.mark.asyncio
    async def test_http_api_exception_returns_empty(self) -> None:
        """HTTP API 也失败时应返回空结果."""
        mock_settings = _make_mock_settings()

        with (
            patch(
                "src.skills.researcher.scrapers.firecrawl_scraper.get_settings",
                return_value=mock_settings,
            ),
            patch.dict("sys.modules", {"firecrawl": None}),
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=Exception("HTTP API 调用失败"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            scraper = FirecrawlScraper("https://example.com")
            result = await scraper.scrape()

        assert result["content"] == ""
        assert result["title"] == ""
        assert result["image_urls"] == []


class TestFirecrawlScraperNormalizeResult:
    """_normalize_result 归一化测试."""

    def _make_scraper(self) -> FirecrawlScraper:
        """构造带 mock settings 的 scraper 实例."""
        mock_settings = _make_mock_settings()
        with patch(
            "src.skills.researcher.scrapers.firecrawl_scraper.get_settings",
            return_value=mock_settings,
        ):
            return FirecrawlScraper("https://example.com")

    def test_standard_payload(self) -> None:
        """标准 Firecrawl v1 响应结构."""
        scraper = self._make_scraper()
        payload = _make_firecrawl_payload(
            markdown="内容",
            title="标题",
            images=["https://example.com/a.jpg"],
        )
        result = scraper._normalize_result(payload)

        assert result["content"] == "内容"
        assert result["title"] == "标题"
        assert result["image_urls"] == ["https://example.com/a.jpg"]
        assert result["content_type"] == "markdown"

    def test_empty_data(self) -> None:
        """data 为空字典时应返回空内容."""
        scraper = self._make_scraper()
        result = scraper._normalize_result({"success": True, "data": {}})

        assert result["content"] == ""
        assert result["title"] == ""

    def test_missing_data_key(self) -> None:
        """payload 无 data 键时应返回空内容."""
        scraper = self._make_scraper()
        result = scraper._normalize_result({"success": False})

        assert result["content"] == ""
        assert result["title"] == ""

    def test_html_fallback_when_no_markdown(self) -> None:
        """无 markdown 时应回退到 html."""
        scraper = self._make_scraper()
        payload = {
            "data": {
                "html": "<p>HTML 内容</p>",
                "metadata": {"title": "HTML 标题"},
            }
        }
        result = scraper._normalize_result(payload)

        assert result["content"] == "<p>HTML 内容</p>"
        assert result["title"] == "HTML 标题"

    def test_images_top4_limit(self) -> None:
        """图片应限制为 top 4."""
        scraper = self._make_scraper()
        payload = _make_firecrawl_payload(
            images=[
                "https://example.com/1.jpg",
                "https://example.com/2.jpg",
                "https://example.com/3.jpg",
                "https://example.com/4.jpg",
                "https://example.com/5.jpg",
            ],
        )
        result = scraper._normalize_result(payload)

        assert len(result["image_urls"]) == 4

    def test_images_fallback_to_images_key(self) -> None:
        """无 ogImages 时应回退到 images 字段."""
        scraper = self._make_scraper()
        payload = {
            "data": {
                "markdown": "内容",
                "metadata": {
                    "title": "标题",
                    "images": ["https://example.com/img.jpg"],
                },
            }
        }
        result = scraper._normalize_result(payload)

        assert result["image_urls"] == ["https://example.com/img.jpg"]
