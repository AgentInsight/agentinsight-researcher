"""TavilyExtractScraper 单元测试 (Tavily Extract 商业服务抓取器).

测试覆盖:
1. 实例化: 从 settings 读取 api_key, _api_url 类属性
2. 无 api_key: 返回空结果
3. 成功路径: mock httpx POST 响应
4. failed_results: 返回空结果
5. 异常处理: 抓取失败返回空结果
6. _normalize_result: 各种 payload 结构
7. title 启发式提取: 从 raw_content 首行提取
8. session 复用: 传入 httpx.AsyncClient 兼容 session 时不自建
9. content_type 为 'text'
10. 图片 top 4 限制

单元测试在构建期执行, 不依赖外部服务.
所有 httpx 调用全部 mock, 不调用真实 Tavily API.

注: tavily_extract_scraper.py 使用 isinstance(session, httpx.AsyncClient)
判断是否复用 session, 测试中需用 spec=httpx.AsyncClient 构造 mock
以通过 isinstance 检查.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

pytestmark = pytest.mark.unit

from src.skills.researcher.scrapers.tavily_extract_scraper import TavilyExtractScraper


def _make_mock_settings(
    *,
    api_key: str | None = "test-tavily-key",
) -> MagicMock:
    """构造 mock Settings (绕过 .env 加载)."""
    settings = MagicMock()
    settings.tavily_api_key = api_key
    return settings


def _make_tavily_payload(
    *,
    raw_content: str = "这是提取的纯文本内容",
    images: list[str] | None = None,
    url: str = "https://example.com",
) -> dict:
    """构造 Tavily Extract 响应 payload."""
    item = {"url": url, "raw_content": raw_content}
    if images:
        item["images"] = images
    return {"results": [item]}


def _make_mock_httpx_response(
    *,
    json_data: dict | None = None,
) -> AsyncMock:
    """构造 mock httpx 响应."""
    resp = AsyncMock()
    resp.json = MagicMock(return_value=json_data or {})
    resp.raise_for_status = MagicMock()
    return resp


def _make_mock_session_with_spec(
    *,
    post_return: AsyncMock | None = None,
    post_side_effect: Exception | None = None,
) -> AsyncMock:
    """构造 mock httpx.AsyncClient session (spec=httpx.AsyncClient).

    使用 spec 使 isinstance(session, httpx.AsyncClient) 返回 True,
    从而触发 tavily_extract_scraper.py 的 session 复用路径.
    """
    session = AsyncMock(spec=httpx.AsyncClient)
    if post_side_effect is not None:
        session.post = AsyncMock(side_effect=post_side_effect)
    else:
        session.post = AsyncMock(return_value=post_return or _make_mock_httpx_response())
    return session


class TestTavilyExtractScraperInstantiation:
    """实例化测试."""

    def test_default_name(self) -> None:
        """类属性 name 应为 'tavily_extract'."""
        assert TavilyExtractScraper.name == "tavily_extract"

    def test_api_url_class_var(self) -> None:
        """_api_url 类属性应为 Tavily Extract 端点."""
        assert TavilyExtractScraper._api_url == "https://api.tavily.com/extract"

    def test_init_reads_api_key_from_settings(self) -> None:
        """__init__ 应从 settings 读取 tavily_api_key."""
        mock_settings = _make_mock_settings(api_key="my-tavily-key")
        with patch(
            "src.skills.researcher.scrapers.tavily_extract_scraper.get_settings",
            return_value=mock_settings,
        ):
            scraper = TavilyExtractScraper("https://example.com")
        assert scraper.api_key == "my-tavily-key"
        assert scraper.url == "https://example.com"

    def test_init_api_key_none(self) -> None:
        """api_key 为 None 时也应正常实例化."""
        mock_settings = _make_mock_settings(api_key=None)
        with patch(
            "src.skills.researcher.scrapers.tavily_extract_scraper.get_settings",
            return_value=mock_settings,
        ):
            scraper = TavilyExtractScraper("https://example.com")
        assert scraper.api_key is None


class TestTavilyExtractScraperScrape:
    """scrape 方法测试."""

    @pytest.mark.asyncio
    async def test_no_api_key_returns_empty(self) -> None:
        """api_key 未配置时应返回空结果."""
        mock_settings = _make_mock_settings(api_key=None)
        with patch(
            "src.skills.researcher.scrapers.tavily_extract_scraper.get_settings",
            return_value=mock_settings,
        ):
            scraper = TavilyExtractScraper("https://example.com")
            result = await scraper.scrape()
        assert result["url"] == "https://example.com"
        assert result["content"] == ""
        assert result["title"] == ""
        assert result["image_urls"] == []

    @pytest.mark.asyncio
    async def test_scrape_success(self) -> None:
        """成功路径: 通过 spec=httpx.AsyncClient 的 mock session 复用."""
        mock_settings = _make_mock_settings()
        payload = _make_tavily_payload(
            raw_content="提取的文本内容",
            images=["https://example.com/img1.jpg"],
        )
        mock_resp = _make_mock_httpx_response(json_data=payload)
        session = _make_mock_session_with_spec(post_return=mock_resp)

        with patch(
            "src.skills.researcher.scrapers.tavily_extract_scraper.get_settings",
            return_value=mock_settings,
        ):
            scraper = TavilyExtractScraper("https://example.com", session=session)
            result = await scraper.scrape()

        assert result["content_type"] == "text"
        assert result["content"] == "提取的文本内容"
        assert "https://example.com/img1.jpg" in result["image_urls"]
        assert result["url"] == "https://example.com"
        session.post.assert_awaited()

    @pytest.mark.asyncio
    async def test_failed_results_returns_empty(self) -> None:
        """failed_results 非空时应返回空结果."""
        mock_settings = _make_mock_settings()
        payload = {
            "results": [],
            "failed_results": [
                {
                    "url": "https://example.com",
                    "error_code": "BAD_URL",
                    "error_message": "URL 无效",
                }
            ],
        }
        mock_resp = _make_mock_httpx_response(json_data=payload)
        session = _make_mock_session_with_spec(post_return=mock_resp)

        with patch(
            "src.skills.researcher.scrapers.tavily_extract_scraper.get_settings",
            return_value=mock_settings,
        ):
            scraper = TavilyExtractScraper("https://example.com", session=session)
            result = await scraper.scrape()

        assert result["content"] == ""
        assert result["title"] == ""
        assert result["image_urls"] == []

    @pytest.mark.asyncio
    async def test_http_exception_returns_empty(self) -> None:
        """HTTP 调用异常时应返回空结果."""
        mock_settings = _make_mock_settings()
        session = _make_mock_session_with_spec(
            post_side_effect=Exception("网络错误"),
        )

        with patch(
            "src.skills.researcher.scrapers.tavily_extract_scraper.get_settings",
            return_value=mock_settings,
        ):
            scraper = TavilyExtractScraper("https://example.com", session=session)
            result = await scraper.scrape()

        assert result["content"] == ""
        assert result["url"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_session_reuse(self) -> None:
        """传入 httpx.AsyncClient 兼容 session 时应复用 (不自建 client).

        注: 不能 patch httpx.AsyncClient, 否则 isinstance(session, httpx.AsyncClient)
        检查会因 httpx.AsyncClient 被替换为 MagicMock 而失败.
        通过 spec=httpx.AsyncClient 构造的 mock session 可通过 isinstance 检查,
        从而触发 session 复用路径 (client_owner=False).
        """
        mock_settings = _make_mock_settings()
        payload = _make_tavily_payload(raw_content="复用 session 内容")
        mock_resp = _make_mock_httpx_response(json_data=payload)
        session = _make_mock_session_with_spec(post_return=mock_resp)

        with patch(
            "src.skills.researcher.scrapers.tavily_extract_scraper.get_settings",
            return_value=mock_settings,
        ):
            scraper = TavilyExtractScraper("https://example.com", session=session)
            result = await scraper.scrape()

        # session 被复用: post 被调用且返回预期内容
        assert result["content"] == "复用 session 内容"
        session.post.assert_awaited()
        # session.aclose 不应被调用 (client_owner=False, 复用 session 不关闭)
        session.aclose.assert_not_called()

    @pytest.mark.asyncio
    async def test_images_top4_limit(self) -> None:
        """图片应限制为 top 4."""
        mock_settings = _make_mock_settings()
        payload = _make_tavily_payload(
            raw_content="内容",
            images=[
                "https://example.com/1.jpg",
                "https://example.com/2.jpg",
                "https://example.com/3.jpg",
                "https://example.com/4.jpg",
                "https://example.com/5.jpg",
            ],
        )
        mock_resp = _make_mock_httpx_response(json_data=payload)
        session = _make_mock_session_with_spec(post_return=mock_resp)

        with patch(
            "src.skills.researcher.scrapers.tavily_extract_scraper.get_settings",
            return_value=mock_settings,
        ):
            scraper = TavilyExtractScraper("https://example.com", session=session)
            result = await scraper.scrape()

        assert len(result["image_urls"]) == 4


class TestTavilyExtractScraperNormalizeResult:
    """_normalize_result 归一化测试."""

    def _make_scraper(self) -> TavilyExtractScraper:
        """构造带 mock settings 的 scraper 实例."""
        mock_settings = _make_mock_settings()
        with patch(
            "src.skills.researcher.scrapers.tavily_extract_scraper.get_settings",
            return_value=mock_settings,
        ):
            return TavilyExtractScraper("https://example.com")

    def test_standard_payload(self) -> None:
        """标准 Tavily Extract 响应结构."""
        scraper = self._make_scraper()
        payload = _make_tavily_payload(
            raw_content="文本内容",
            images=["https://example.com/a.jpg"],
        )
        result = scraper._normalize_result(payload)

        assert result["content"] == "文本内容"
        assert result["image_urls"] == ["https://example.com/a.jpg"]
        assert result["content_type"] == "text"

    def test_empty_results(self) -> None:
        """results 为空列表时应返回空结果."""
        scraper = self._make_scraper()
        result = scraper._normalize_result({"results": []})

        assert result["content"] == ""
        assert result["title"] == ""

    def test_missing_results_key(self) -> None:
        """payload 无 results 键时应返回空结果."""
        scraper = self._make_scraper()
        result = scraper._normalize_result({})

        assert result["content"] == ""

    def test_title_extraction_from_first_line(self) -> None:
        """title 应从 raw_content 首行启发式提取."""
        scraper = self._make_scraper()
        payload = _make_tavily_payload(raw_content="# 我的标题\n\n正文内容")
        result = scraper._normalize_result(payload)

        assert result["title"] == "我的标题"
        assert "正文内容" in result["content"]

    def test_title_skipped_when_first_line_too_long(self) -> None:
        """首行超过 200 字符时 title 应为空."""
        scraper = self._make_scraper()
        long_first_line = "x" * 201
        payload = _make_tavily_payload(raw_content=f"{long_first_line}\n正文")
        result = scraper._normalize_result(payload)

        assert result["title"] == ""

    def test_raw_content_none(self) -> None:
        """raw_content 为 None 时 content 应为空字符串."""
        scraper = self._make_scraper()
        payload = {"results": [{"url": "https://example.com", "raw_content": None}]}
        result = scraper._normalize_result(payload)

        assert result["content"] == ""
        assert result["title"] == ""

    def test_images_none(self) -> None:
        """images 为 None 时 image_urls 应为空列表."""
        scraper = self._make_scraper()
        payload = {"results": [{"url": "https://example.com", "raw_content": "内容"}]}
        result = scraper._normalize_result(payload)

        assert result["image_urls"] == []

    def test_failed_results_logged(self) -> None:
        """failed_results 存在时应记录日志并返回空."""
        scraper = self._make_scraper()
        payload = {
            "results": [],
            "failed_results": [
                {
                    "url": "https://example.com",
                    "error_code": "EXTRACTION_FAILED",
                    "error_message": "提取失败",
                }
            ],
        }
        result = scraper._normalize_result(payload)

        assert result["content"] == ""
