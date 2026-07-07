"""单元测试: 网页抓取器模块.

验证 src/skills/researcher/scrapers/ 下所有抓取器与调度逻辑:
- BeautifulSoupScraper: HTML 抓取 (默认主力, 剥离 script/style/nav/footer/header)
- ArxivScraper: Arxiv 论文抓取 (元数据 + 全文)
- PyMuPDFScraper: PDF 抓取 (fitz 提取文本)
- scrape_urls: 并发抓取多个 URL (WorkerPool + GlobalRateLimiter)
- WorkerPool: 并发限流 (asyncio.Semaphore)
- GlobalRateLimiter: 全局速率限制 (单例, asyncio.Lock)
- scrape_with_fallback: 降级链 (BS → Playwright, lightweight 模式跳过降级)

AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务
(HTTP/arxiv/PyMuPDF/Playwright 全部 mock).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.skills.researcher.scrapers import (
    GlobalRateLimiter,
    WorkerPool,
    get_global_rate_limiter,
    scrape_urls,
    scrape_with_fallback,
)
from src.skills.researcher.scrapers.arxiv_scraper import ArxivScraper
from src.skills.researcher.scrapers.beautiful_soup_scraper import BeautifulSoupScraper
from src.skills.researcher.scrapers.pymupdf_scraper import PyMuPDFScraper

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture()
def settings() -> Settings:
    """构造最小 Settings (跳过 .env 加载, 使用默认值)."""
    return Settings(_env_file=None)


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """每个测试前后重置 GlobalRateLimiter 单例状态 (避免跨测试污染).

    GlobalRateLimiter 是单例, 跨测试共享 _rate_limit_delay / _last_request_time,
    不重置会导致速率限制测试影响后续测试.
    """
    limiter = get_global_rate_limiter()
    limiter.configure(0.0)
    limiter._last_request_time = 0.0
    yield
    limiter.configure(0.0)
    limiter._last_request_time = 0.0


def _make_mock_response(
    text: str,
    *,
    encoding: str = "utf-8",
    status_code: int = 200,
) -> MagicMock:
    """构造 mock httpx 响应 (BeautifulSoupScraper 使用)."""
    response = MagicMock()
    response.text = text
    response.encoding = encoding
    response.status_code = status_code
    response.raise_for_status = MagicMock()
    return response


def _make_mock_session(response: MagicMock) -> MagicMock:
    """构造 mock httpx.AsyncClient session (get 返回 mock 响应)."""
    session = MagicMock()
    session.get = AsyncMock(return_value=response)
    return session


# ========== BeautifulSoupScraper ==========


@pytest.mark.asyncio
async def test_beautiful_soup_scraper_extracts_text() -> None:
    """测试 BeautifulSoup 从含内容的 HTML 提取正文与标题.

    BeautifulSoupScraper.scrape 调用 session.get → response.text → BeautifulSoup 解析,
    返回 {url, content, title, image_urls, content_type}.
    """
    html = """
    <html>
    <head><title>测试页面标题</title></head>
    <body>
        <h1>主标题</h1>
        <p>这是一段测试正文内容, 用于验证 BeautifulSoupScraper 能正确提取文本.</p>
        <p>第二段正文, 确保多段落都被提取.</p>
    </body>
    </html>
    """
    response = _make_mock_response(html)
    session = _make_mock_session(response)

    scraper = BeautifulSoupScraper("https://example.com/page", session=session)
    result = await scraper.scrape()

    assert result["url"] == "https://example.com/page"
    assert result["title"] == "测试页面标题"
    assert "主标题" in result["content"]
    assert "这是一段测试正文内容" in result["content"]
    assert "第二段正文" in result["content"]
    assert result["content_type"] == "html"
    assert isinstance(result["image_urls"], list)
    # session.get 被调用 (带 timeout 参数)
    session.get.assert_awaited_once()
    call_args, call_kwargs = session.get.call_args
    assert call_args[0] == "https://example.com/page"
    assert call_kwargs["timeout"] == 15.0


@pytest.mark.asyncio
async def test_beautiful_soup_scraper_empty_html() -> None:
    """测试空 HTML (无 title/body 内容) 返回空内容.

    无 <title> 时 title="", 无正文时 content="".
    """
    html = "<html><head></head><body></body></html>"
    response = _make_mock_response(html)
    session = _make_mock_session(response)

    scraper = BeautifulSoupScraper("https://example.com/empty", session=session)
    result = await scraper.scrape()

    assert result["url"] == "https://example.com/empty"
    assert result["content"] == ""
    assert result["title"] == ""
    assert result["image_urls"] == []


@pytest.mark.asyncio
async def test_beautiful_soup_scraper_handles_scripts() -> None:
    """测试 script/style/nav/footer/header 标签被剥离, 不出现在正文.

    BeautifulSoupScraper 调用 soup.decompose() 清理这些标签,
    确保脚本代码与样式不污染正文.
    """
    html = """
    <html>
    <head><title>脚本测试</title>
        <script>var malicious = 'should_be_stripped';</script>
        <style>body { color: red; } .hidden { display: none; }</style>
    </head>
    <body>
        <header>页眉导航</header>
        <nav>菜单链接</nav>
        <p>这是需要保留的正文内容.</p>
        <script>alert('also_stripped');</script>
        <footer>页脚版权信息</footer>
    </body>
    </html>
    """
    response = _make_mock_response(html)
    session = _make_mock_session(response)

    scraper = BeautifulSoupScraper("https://example.com", session=session)
    result = await scraper.scrape()

    # 正文被保留
    assert "这是需要保留的正文内容" in result["content"]
    # 脚本/样式/导航/页眉/页脚被剥离
    assert "malicious" not in result["content"]
    assert "should_be_stripped" not in result["content"]
    assert "alert" not in result["content"]
    assert "also_stripped" not in result["content"]
    assert "color: red" not in result["content"]
    assert "display: none" not in result["content"]
    assert "页眉导航" not in result["content"]
    assert "菜单链接" not in result["content"]
    assert "页脚版权信息" not in result["content"]


# ========== ArxivScraper ==========


@pytest.mark.asyncio
@patch("src.skills.researcher.scrapers.arxiv_scraper._download_pdf_with_retry")
async def test_arxiv_scraper_parses_paper(mock_download: AsyncMock) -> None:
    """测试 Arxiv 抓取器从 arxiv 库提取论文标题/作者/摘要/发布日期.

    ArxivScraper.scrape 调用 arxiv.Client().results() 获取元数据,
    格式化为 "Title: ...\\nAuthors: ...\\nPublished: ...\\nSummary: ..." 字符串.
    PDF 全文下载失败时仅用摘要 (异常被 catch).
    """
    # Mock arxiv 库
    mock_arxiv = MagicMock()
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.title = "Attention Is All You Need"
    mock_result.authors = ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"]
    mock_result.published = datetime(2017, 6, 12)
    mock_result.summary = (
        "The dominant sequence transduction models are based on complex recurrent "
        "or convolutional neural networks. We propose a new simple network architecture."
    )
    mock_result.pdf_url = "https://arxiv.org/pdf/1706.03762"
    mock_client.results.return_value = [mock_result]
    mock_arxiv.Client.return_value = mock_client

    # Mock PDF 下载失败 (跳过全文, 仅用摘要)
    mock_download.side_effect = Exception("PDF download failed (mock)")

    url = "https://arxiv.org/abs/1706.03762"

    with patch.dict("sys.modules", {"arxiv": mock_arxiv}):
        scraper = ArxivScraper(url)
        result = await scraper.scrape()

    assert result["url"] == url
    assert result["title"] == "Attention Is All You Need"
    assert result["content_type"] == "arxiv"
    # 内容含标题
    assert "Attention Is All You Need" in result["content"]
    # 内容含作者
    assert "Ashish Vaswani" in result["content"]
    assert "Noam Shazeer" in result["content"]
    assert "Niki Parmar" in result["content"]
    # 内容含发布日期
    assert "2017-06-12" in result["content"]
    # 内容含摘要
    assert "dominant sequence transduction" in result["content"]
    # arxiv 库被调用
    mock_arxiv.Client.assert_called_once()


@pytest.mark.asyncio
@patch("src.skills.researcher.scrapers.arxiv_scraper._download_pdf_with_retry")
async def test_arxiv_scraper_no_results_returns_empty(mock_download: AsyncMock) -> None:
    """测试 arxiv 库返回空结果时, 抓取器返回空内容.

    client.results() 返回空列表时, paper_info=None,
    返回 {content:"", title:"", image_urls:[]}.
    """
    mock_arxiv = MagicMock()
    mock_client = MagicMock()
    mock_client.results.return_value = []  # 无结果
    mock_arxiv.Client.return_value = mock_client

    url = "https://arxiv.org/abs/0000.00000"

    with patch.dict("sys.modules", {"arxiv": mock_arxiv}):
        scraper = ArxivScraper(url)
        result = await scraper.scrape()

    assert result["url"] == url
    assert result["content"] == ""
    assert result["title"] == ""
    mock_download.assert_not_awaited()  # 无元数据时不下载 PDF


# ========== PyMuPDFScraper ==========


@pytest.mark.asyncio
async def test_pymupdf_scraper_extracts_pdf_text(tmp_path) -> None:
    """测试 PyMuPDF 抓取器从本地 PDF 文件提取文本.

    PyMuPDFScraper 对本地路径调用 _extract_from_file → fitz.open → page.get_text,
    多页文本以 "\\n\\n" 拼接. 使用 mock fitz 避免依赖真实 PDF.
    """
    # 创建临时 PDF 文件 (内容无所谓, fitz 被 mock)
    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 mock pdf content")

    # Mock fitz 模块
    mock_fitz = MagicMock()
    mock_doc = MagicMock()
    mock_page1 = MagicMock()
    mock_page1.get_text.return_value = "这是第一页的 PDF 文本内容."
    mock_page2 = MagicMock()
    mock_page2.get_text.return_value = "这是第二页的 PDF 文本内容."
    # doc 迭代产出 page1, page2
    mock_doc.__iter__ = MagicMock(return_value=iter([mock_page1, mock_page2]))
    mock_doc.close = MagicMock()
    mock_fitz.open.return_value = mock_doc

    with patch.dict("sys.modules", {"fitz": mock_fitz}):
        scraper = PyMuPDFScraper(str(pdf_path))
        result = await scraper.scrape()

    assert result["url"] == str(pdf_path)
    assert "第一页的 PDF 文本内容" in result["content"]
    assert "第二页的 PDF 文本内容" in result["content"]
    # 两页文本以 \n\n 拼接
    assert "\n\n" in result["content"]
    assert result["content_type"] == "pdf"
    assert result["title"] == ""
    assert result["image_urls"] == []
    # fitz.open 被调用
    mock_fitz.open.assert_called_once_with(str(pdf_path))
    # doc 被关闭
    mock_doc.close.assert_called_once()


@pytest.mark.asyncio
async def test_pymupdf_scraper_local_file_not_exists() -> None:
    """测试本地路径不存在时返回空内容 (不抛异常)."""
    scraper = PyMuPDFScraper("/nonexistent/path/to/file.pdf")
    result = await scraper.scrape()

    assert result["url"] == "/nonexistent/path/to/file.pdf"
    assert result["content"] == ""
    assert result["content_type"] == "pdf"


# ========== scrape_urls ==========


@pytest.mark.asyncio
@patch("src.skills.researcher.scrapers.scrape_with_fallback", new_callable=AsyncMock)
async def test_scrape_urls_aggregates_results(mock_scrape: AsyncMock) -> None:
    """测试 scrape_urls 并发抓取多个 URL 并聚合结果.

    返回 list[dict], 每个含 {url, content, title, image_urls},
    顺序与输入一致 (asyncio.gather 保序).
    """
    urls = [
        "https://example.com/page1",
        "https://example.com/page2",
        "https://example.com/page3",
    ]

    def mock_fn(url: str, **kwargs: object) -> dict:
        return {
            "url": url,
            "content": f"This is the scraped content for {url}. " * 5,
            "title": f"Title for {url}",
            "image_urls": [],
        }

    mock_scrape.side_effect = mock_fn

    results = await scrape_urls(urls)

    assert len(results) == 3
    # 结果顺序与输入一致
    assert results[0]["url"] == urls[0]
    assert results[1]["url"] == urls[1]
    assert results[2]["url"] == urls[2]
    # 每个结果含 content
    for r in results:
        assert r["content"]
        assert "scraped content" in r["content"]
    # scrape_with_fallback 被调用 3 次
    assert mock_scrape.await_count == 3


@pytest.mark.asyncio
@patch("src.skills.researcher.scrapers.scrape_with_fallback", new_callable=AsyncMock)
async def test_scrape_urls_handles_failures(mock_scrape: AsyncMock) -> None:
    """测试一个 URL 抓取失败时, 其他 URL 仍正常返回.

    失败 URL 返回 content="" 或 content=None, 被 scrape_urls 末尾的
    [r for r in results if r.get("content")] 过滤掉.
    """

    def mock_fn(url: str, **kwargs: object) -> dict:
        if "fail" in url:
            return {"url": url, "content": "", "title": "", "image_urls": []}
        return {
            "url": url,
            "content": f"Successful content for {url}. " * 10,
            "title": "",
            "image_urls": [],
        }

    mock_scrape.side_effect = mock_fn

    urls = [
        "https://example.com/ok1",
        "https://example.com/fail",
        "https://example.com/ok2",
    ]
    results = await scrape_urls(urls)

    # 失败 URL 被过滤, 仅剩 2 个成功结果
    assert len(results) == 2
    result_urls = [r["url"] for r in results]
    assert "https://example.com/fail" not in result_urls
    assert "https://example.com/ok1" in result_urls
    assert "https://example.com/ok2" in result_urls


@pytest.mark.asyncio
async def test_scrape_urls_empty_input() -> None:
    """测试空 URL 列表返回空列表 (scrape_urls 早返回 [])."""
    results = await scrape_urls([])
    assert results == []


@pytest.mark.asyncio
@patch("src.skills.researcher.scrapers.scrape_with_fallback", new_callable=AsyncMock)
async def test_scrape_urls_deduplicates(mock_scrape: AsyncMock) -> None:
    """测试 scrape_urls 保序去重 (dict.fromkeys)."""

    def mock_fn(url: str, **kwargs: object) -> dict:
        return {
            "url": url,
            "content": f"Content for {url}. " * 20,
            "title": "",
            "image_urls": [],
        }

    mock_scrape.side_effect = mock_fn

    urls = [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/a",  # 重复
    ]
    results = await scrape_urls(urls)

    assert len(results) == 2
    assert results[0]["url"] == "https://example.com/a"
    assert results[1]["url"] == "https://example.com/b"
    assert mock_scrape.await_count == 2  # 重复 URL 不重复抓取


# ========== WorkerPool ==========


@pytest.mark.asyncio
async def test_worker_pool_concurrency_limit() -> None:
    """测试 WorkerPool.semaphore 限制并发工作数.

    max_workers=2 时, 同时执行的 worker 数不超过 2.
    """
    pool = WorkerPool(max_workers=2, rate_limit_delay=0.0)

    current_concurrent = 0
    max_concurrent = 0
    lock = asyncio.Lock()

    async def worker(worker_id: int) -> int:
        nonlocal current_concurrent, max_concurrent
        async with pool.throttle():
            async with lock:
                current_concurrent += 1
                max_concurrent = max(max_concurrent, current_concurrent)
            await asyncio.sleep(0.05)  # 模拟 IO
            async with lock:
                current_concurrent -= 1
            return worker_id

    # 启动 6 个 worker, 但最多 2 个并发
    results = await asyncio.gather(*[worker(i) for i in range(6)])

    assert results == [0, 1, 2, 3, 4, 5]  # 全部完成
    assert max_concurrent <= 2  # 并发不超过 2
    assert max_concurrent == 2  # 确实达到过 2 (验证 semaphore 生效)


@pytest.mark.asyncio
async def test_worker_pool_throttle_yields_within_context() -> None:
    """测试 WorkerPool.throttle 作为 async context manager 正常 yield."""
    pool = WorkerPool(max_workers=1, rate_limit_delay=0.0)

    async with pool.throttle() as ctx:
        # throttle 应 yield None (不返回值, 仅做限流)
        assert ctx is None


# ========== GlobalRateLimiter ==========


@pytest.mark.asyncio
async def test_global_rate_limiter_enforces_delay() -> None:
    """测试 GlobalRateLimiter 在两次调用间强制延迟.

    configure(0.1) 后, 第一次调用无延迟 (elapsed >> delay),
    第二次调用需等待 ~0.1s (elapsed < delay → sleep).
    """
    limiter = GlobalRateLimiter()
    limiter.configure(0.1)
    limiter._last_request_time = 0.0  # 重置, 使首次调用 elapsed 巨大

    start = time.monotonic()
    await limiter.wait_if_needed()  # 首次: elapsed 巨大, 无延迟
    first_elapsed = time.monotonic() - start
    assert first_elapsed < 0.05  # 首次几乎无延迟

    await limiter.wait_if_needed()  # 第二次: elapsed < 0.1, 需等待
    total_elapsed = time.monotonic() - start
    assert total_elapsed >= 0.1  # 总耗时至少 0.1s


@pytest.mark.asyncio
async def test_global_rate_limiter_zero_delay_no_wait() -> None:
    """测试 rate_limit_delay=0 时不等待 (wait_if_needed 早返回)."""
    limiter = GlobalRateLimiter()
    limiter.configure(0.0)

    start = time.monotonic()
    await limiter.wait_if_needed()
    await limiter.wait_if_needed()
    await limiter.wait_if_needed()
    elapsed = time.monotonic() - start

    # 无延迟, 三次调用几乎瞬间完成
    assert elapsed < 0.05


@pytest.mark.asyncio
async def test_global_rate_limiter_is_singleton() -> None:
    """测试 GlobalRateLimiter 是单例 (多次实例化返回同一对象)."""
    limiter1 = GlobalRateLimiter()
    limiter2 = GlobalRateLimiter()
    limiter3 = get_global_rate_limiter()

    assert limiter1 is limiter2
    assert limiter2 is limiter3


# ========== scrape_with_fallback ==========


@pytest.mark.asyncio
async def test_scrape_with_fallback_bs_to_playwright() -> None:
    """测试 BS 抓取内容过短时降级到 Playwright.

    BS 返回 content="short" (len < min_content_length=100),
    scraper_mode="auto" → 触发 Playwright 降级.
    Playwright 返回更长内容 → 使用 Playwright 结果.
    """
    url = "https://example.com"
    bs_result = {
        "url": url,
        "content": "short",  # len=5 < 100, 触发降级
        "title": "",
        "image_urls": [],
    }
    pw_result = {
        "url": url,
        "content": "Playwright rendered full content. " * 10,  # len > 100
        "title": "PW Title",
        "image_urls": [],
    }

    mock_settings = Settings(_env_file=None, scraper_mode="auto")

    with (
        patch(
            "src.skills.researcher.scrapers.get_settings",
            return_value=mock_settings,
        ),
        patch(
            "src.skills.researcher.scrapers.beautiful_soup_scraper.BeautifulSoupScraper"
        ) as mock_bs_cls,
        patch("src.skills.researcher.scrapers.playwright_scraper.PlaywrightScraper") as mock_pw_cls,
    ):
        mock_bs_cls.return_value.scrape = AsyncMock(return_value=bs_result)
        mock_pw_cls.return_value.scrape = AsyncMock(return_value=pw_result)

        result = await scrape_with_fallback(url)

    # 返回 Playwright 结果 (内容更长)
    assert result == pw_result
    assert "Playwright rendered full content" in result["content"]
    # BS 被调用
    mock_bs_cls.return_value.scrape.assert_awaited_once()
    # Playwright 被调用 (降级触发)
    mock_pw_cls.return_value.scrape.assert_awaited_once()


@pytest.mark.asyncio
async def test_scrape_with_fallback_lightweight_mode() -> None:
    """测试 lightweight 模式不触发 Playwright 降级.

    scraper_mode="lightweight" → BS 内容过短时直接返回 BS 结果,
    不导入/调用 PlaywrightScraper (适合离线最小化部署).
    """
    url = "https://example.com"
    bs_result = {
        "url": url,
        "content": "short",  # len=5 < 100
        "title": "",
        "image_urls": [],
    }

    mock_settings = Settings(_env_file=None, scraper_mode="lightweight")

    with (
        patch(
            "src.skills.researcher.scrapers.get_settings",
            return_value=mock_settings,
        ),
        patch(
            "src.skills.researcher.scrapers.beautiful_soup_scraper.BeautifulSoupScraper"
        ) as mock_bs_cls,
        patch("src.skills.researcher.scrapers.playwright_scraper.PlaywrightScraper") as mock_pw_cls,
    ):
        mock_bs_cls.return_value.scrape = AsyncMock(return_value=bs_result)

        result = await scrape_with_fallback(url)

    # 返回 BS 结果 (未降级)
    assert result == bs_result
    assert result["content"] == "short"
    # BS 被调用
    mock_bs_cls.return_value.scrape.assert_awaited_once()
    # Playwright 未被调用 (lightweight 模式跳过降级)
    mock_pw_cls.return_value.scrape.assert_not_called()


@pytest.mark.asyncio
async def test_scrape_with_fallback_bs_sufficient_no_fallback() -> None:
    """测试 BS 抓取内容足够长时不触发 Playwright 降级.

    BS 返回 content len >= min_content_length (100) → 直接返回 BS 结果.
    """
    url = "https://example.com"
    bs_result = {
        "url": url,
        "content": "This is sufficiently long content from BeautifulSoup. " * 5,  # > 100
        "title": "BS Title",
        "image_urls": [],
    }

    mock_settings = Settings(_env_file=None, scraper_mode="auto")

    with (
        patch(
            "src.skills.researcher.scrapers.get_settings",
            return_value=mock_settings,
        ),
        patch(
            "src.skills.researcher.scrapers.beautiful_soup_scraper.BeautifulSoupScraper"
        ) as mock_bs_cls,
        patch("src.skills.researcher.scrapers.playwright_scraper.PlaywrightScraper") as mock_pw_cls,
    ):
        mock_bs_cls.return_value.scrape = AsyncMock(return_value=bs_result)

        result = await scrape_with_fallback(url)

    assert result == bs_result
    mock_bs_cls.return_value.scrape.assert_awaited_once()
    # BS 内容足够, 不触发降级
    mock_pw_cls.return_value.scrape.assert_not_called()


@pytest.mark.asyncio
async def test_scrape_with_fallback_pdf_url_uses_pymupdf() -> None:
    """测试 PDF URL 直接走 PyMuPDFScraper, 不触发 BS→Playwright 降级链."""
    url = "https://example.com/doc.pdf"
    pdf_result = {
        "url": url,
        "content": "PDF text content. " * 20,
        "title": "",
        "image_urls": [],
        "content_type": "pdf",
    }

    with (
        patch("src.skills.researcher.scrapers.pymupdf_scraper.PyMuPDFScraper") as mock_pdf_cls,
        patch(
            "src.skills.researcher.scrapers.beautiful_soup_scraper.BeautifulSoupScraper"
        ) as mock_bs_cls,
        patch("src.skills.researcher.scrapers.playwright_scraper.PlaywrightScraper") as mock_pw_cls,
    ):
        mock_pdf_cls.return_value.scrape = AsyncMock(return_value=pdf_result)

        result = await scrape_with_fallback(url)

    assert result == pdf_result
    mock_pdf_cls.return_value.scrape.assert_awaited_once()
    # PDF 不走 BS / Playwright 降级
    mock_bs_cls.return_value.scrape.assert_not_called()
    mock_pw_cls.return_value.scrape.assert_not_called()


@pytest.mark.asyncio
async def test_scrape_with_fallback_arxiv_url_uses_arxiv_scraper() -> None:
    """测试 arxiv.org URL 直接走 ArxivScraper, 不触发 BS→Playwright 降级链."""
    url = "https://arxiv.org/abs/2401.12345"
    arxiv_result = {
        "url": url,
        "content": "Title: Test Paper\nAuthors: Author A\nSummary: ...",
        "title": "Test Paper",
        "image_urls": [],
        "content_type": "arxiv",
    }

    with (
        patch("src.skills.researcher.scrapers.arxiv_scraper.ArxivScraper") as mock_arxiv_cls,
        patch(
            "src.skills.researcher.scrapers.beautiful_soup_scraper.BeautifulSoupScraper"
        ) as mock_bs_cls,
    ):
        mock_arxiv_cls.return_value.scrape = AsyncMock(return_value=arxiv_result)

        result = await scrape_with_fallback(url)

    assert result == arxiv_result
    mock_arxiv_cls.return_value.scrape.assert_awaited_once()
    # Arxiv 不走 BS 降级
    mock_bs_cls.return_value.scrape.assert_not_called()
