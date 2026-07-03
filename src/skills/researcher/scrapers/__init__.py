"""网页抓取器注册中心与调度.

对标 GPT Researcher scraper/ 体系.
- BeautifulSoupScraper: 默认主力 (轻量, 速度快)
- PlaywrightScraper: JS 渲染页面 (可选, 镜像内安装 chromium)
- PyMuPDFScraper: PDF 抓取
- ArxivScraper: Arxiv 论文 (含全文)

WorkerPool 并发限流: asyncio.Semaphore + GlobalRateLimiter 单例.
所有 scraper 共享 scrape(url) -> dict 规约.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from src.config.settings import Settings as Settings
from src.config.settings import get_settings as get_settings

logger = logging.getLogger(__name__)


# ========== 全局速率限制器 (单例, 跨所有 WorkerPool 实例) ==========


class GlobalRateLimiter:
    """全局速率限制器 (单例).

    对标 GPT Researcher utils/rate_limiter.py.
    asyncio.Lock 确保 rate_limit_delay 跨所有 WorkerPool 实例全局生效.
    """

    _instance: GlobalRateLimiter | None = None
    _lock: asyncio.Lock
    _rate_limit_delay: float
    _last_request_time: float

    def __new__(cls) -> GlobalRateLimiter:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._lock = asyncio.Lock()
            cls._instance._rate_limit_delay = 0.0
            cls._instance._last_request_time = 0.0
        return cls._instance

    def configure(self, rate_limit_delay: float) -> None:
        """配置速率限制延迟 (秒)."""
        self._rate_limit_delay = max(0.0, rate_limit_delay)

    async def wait_if_needed(self) -> None:
        """如需等待则等待 (确保跨实例全局速率)."""
        if self._rate_limit_delay <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._rate_limit_delay:
                await asyncio.sleep(self._rate_limit_delay - elapsed)
            self._last_request_time = time.monotonic()


def get_global_rate_limiter() -> GlobalRateLimiter:
    """获取全局速率限制器单例."""
    return GlobalRateLimiter()


# ========== WorkerPool 并发池 ==========


class WorkerPool:
    """并发工作池.

    对标 GPT Researcher utils/workers.py.
    asyncio.Semaphore 控并发, GlobalRateLimiter 控速率.
    """

    def __init__(
        self,
        max_workers: int = 15,
        rate_limit_delay: float = 0.0,
    ) -> None:
        self.max_workers = max_workers
        self.semaphore = asyncio.Semaphore(max_workers)
        limiter = get_global_rate_limiter()
        limiter.configure(rate_limit_delay)

    @asynccontextmanager
    async def throttle(self) -> AsyncIterator[None]:
        """获取并发槽 + 速率限制."""
        async with self.semaphore:
            limiter = get_global_rate_limiter()
            await limiter.wait_if_needed()
            yield


# ========== Scraper 基类与注册 ==========


class BaseScraper:
    """抓取器基类."""

    name: str = "base"

    def __init__(self, url: str, session: Any | None = None) -> None:
        self.url = url
        self.session = session

    async def scrape(self) -> dict[str, Any]:
        """抓取, 返回 {"url","content","title","image_urls","content_type"}."""
        raise NotImplementedError


def get_scraper(
    url: str,
    scraper_type: str = "bs",
    session: Any | None = None,
) -> BaseScraper:
    """根据 URL 与类型选择抓取器.

    对标 GPT Researcher scraper/scraper.py 的 get_scraper 路由逻辑.
    - URL 以 .pdf 结尾 → PyMuPDFScraper
    - URL 含 arxiv.org → ArxivScraper
    - 否则 → 按配置 (bs/playwright)
    """
    url_lower = url.lower()

    if url_lower.endswith(".pdf"):
        from src.skills.researcher.scrapers.pymupdf_scraper import PyMuPDFScraper

        return PyMuPDFScraper(url, session)

    if "arxiv.org" in url_lower:
        from src.skills.researcher.scrapers.arxiv_scraper import ArxivScraper

        return ArxivScraper(url, session)

    if scraper_type == "playwright":
        from src.skills.researcher.scrapers.playwright_scraper import PlaywrightScraper

        return PlaywrightScraper(url, session)

    # 默认 BeautifulSoup
    from src.skills.researcher.scrapers.beautiful_soup_scraper import BeautifulSoupScraper

    return BeautifulSoupScraper(url, session)


async def scrape_urls(
    urls: list[str],
    *,
    scraper_type: str = "bs",
    max_workers: int = 15,
    rate_limit_delay: float = 0.0,
    user_agent: str = "",
) -> list[dict[str, Any]]:
    """并发抓取多个 URL.

    对标 GPT Researcher actions/web_scraping.py 的 scrape_urls.
    返回 [{"url","content","title","image_urls","content_type"}].
    """
    if not urls:
        return []

    # 保序去重
    unique_urls = list(dict.fromkeys(urls))
    worker_pool = WorkerPool(max_workers, rate_limit_delay)

    import httpx

    async with httpx.AsyncClient(
        timeout=30.0,
        headers={"User-Agent": user_agent or "agentinsight-researcher/0.1"},
        follow_redirects=True,
    ) as session:

        async def _scrape_one(url: str) -> dict[str, Any]:
            async with worker_pool.throttle():
                try:
                    scraper = get_scraper(url, scraper_type, session)
                    result = await scraper.scrape()
                    # 内容过短直接丢弃 (对标 GPT Researcher)
                    if len(result.get("content", "")) < 100:
                        return {"url": url, "content": None, "title": "", "image_urls": []}
                    return result
                except Exception as e:  # noqa: BLE001
                    logger.warning("抓取失败 %s: %s", url, e)
                    return {"url": url, "content": None, "title": "", "image_urls": []}

        results = await asyncio.gather(*[_scrape_one(u) for u in unique_urls])

    # 过滤失败结果
    return [r for r in results if r.get("content")]
