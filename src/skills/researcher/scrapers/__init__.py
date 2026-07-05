"""网页抓取器注册中心与调度.

对标 GPT Researcher scraper/ 体系.
- BeautifulSoupScraper: 默认主力 (轻量, 速度快)
- PlaywrightScraper: JS 渲染页面 (可选, 镜像内安装 chromium)
- PyMuPDFScraper: PDF 抓取
- ArxivScraper: Arxiv 论文 (含全文)
- FirecrawlScraper: Firecrawl 商业服务 (P1-Future-08, LLM 友好 Markdown 输出)
- NodriverScraper: nodriver 无头浏览器 (P1-Future-08, 反反爬绕过 Cloudflare)
- TavilyExtractScraper: Tavily Extract API (对标 GPTR scraper/tavily_extract,
  LLM 友好纯文本输出, 复用 TAVILY_API_KEY)

WorkerPool 并发限流: asyncio.Semaphore + GlobalRateLimiter 单例.
所有 scraper 共享 scrape(url) -> dict 规约.
- register_scraper 装饰器 (P0-01, 对称 register_searcher, 第三方扩展自注册)
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


# ========== 插件注册表 (对称 register_searcher, P0-01) ==========
# 用装饰器注册 scraper, 不破坏现有 get_scraper() 函数式工厂.
# 第三方扩展可通过 @register_scraper("xxx") 自注册, 再由
# get_registered_scrapers() 查询, 未来可逐步迁移工厂到注册表驱动.
_SCRAPER_REGISTRY: dict[str, type[BaseScraper]] = {}


def register_scraper(name: str):
    """抓取器注册装饰器 (对称 register_searcher, P0-01).

    Args:
        name: 注册键名 (如 "tavily_extract").

    Returns:
        类装饰器, 将 cls 注册到 _SCRAPER_REGISTRY 后原样返回.
    """

    def decorator(cls: type[BaseScraper]) -> type[BaseScraper]:
        _SCRAPER_REGISTRY[name] = cls
        return cls

    return decorator


def get_registered_scrapers() -> dict[str, type[BaseScraper]]:
    """返回已注册的抓取器字典 (浅拷贝, 防外部篡改)."""
    return dict(_SCRAPER_REGISTRY)


def get_scraper(
    url: str,
    scraper_type: str = "bs",
    session: Any | None = None,
) -> BaseScraper:
    """根据 URL 与类型选择抓取器.

    对标 GPT Researcher scraper/scraper.py 的 get_scraper 路由逻辑.
    - URL 以 .pdf 结尾 → PyMuPDFScraper
    - URL 含 arxiv.org → ArxivScraper
    - URL 以 Office 文档后缀结尾 → MarkItDownScraper (P2-03)
    - 否则 → 按配置 (bs/playwright/firecrawl/nodriver)
    """
    url_lower = url.lower()

    # 优先查询注册表 (P0-01: 支持 @register_scraper 自注册的第三方扩展)
    if scraper_type and scraper_type in _SCRAPER_REGISTRY:
        return _SCRAPER_REGISTRY[scraper_type](url, session)

    if url_lower.endswith(".pdf"):
        from src.skills.researcher.scrapers.pymupdf_scraper import PyMuPDFScraper

        return PyMuPDFScraper(url, session)

    if "arxiv.org" in url_lower:
        from src.skills.researcher.scrapers.arxiv_scraper import ArxivScraper

        return ArxivScraper(url, session)

    if url_lower.endswith((".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls")):
        from src.skills.researcher.scrapers.markitdown_scraper import MarkItDownScraper

        return MarkItDownScraper(url, session)

    if scraper_type == "playwright":
        from src.skills.researcher.scrapers.playwright_scraper import PlaywrightScraper

        return PlaywrightScraper(url, session)

    if scraper_type == "firecrawl":
        from src.skills.researcher.scrapers.firecrawl_scraper import FirecrawlScraper

        return FirecrawlScraper(url, session)

    if scraper_type == "nodriver":
        from src.skills.researcher.scrapers.nodriver_scraper import NodriverScraper

        return NodriverScraper(url, session)

    if scraper_type == "tavily_extract":
        # Tavily Extract API 抓取器 (对标 GPTR scraper/tavily_extract)
        from src.skills.researcher.scrapers.tavily_extract_scraper import (
            TavilyExtractScraper,
        )

        return TavilyExtractScraper(url, session)

    # 默认 BeautifulSoup
    from src.skills.researcher.scrapers.beautiful_soup_scraper import BeautifulSoupScraper

    return BeautifulSoupScraper(url, session)


async def _safe_scrape(scraper: BaseScraper) -> dict[str, Any]:
    """安全抓取 (异常返回空结果).

    对标 GPT Researcher scrape_with_fallback 的容错语义.
    """
    try:
        return await scraper.scrape()
    except Exception as e:  # noqa: BLE001
        logger.warning("抓取失败 %s: %s", scraper.url, e)
        return {"url": scraper.url, "content": None, "title": "", "image_urls": []}


async def scrape_with_fallback(
    url: str,
    *,
    session: Any | None = None,
    enable_fallback: bool = True,
    min_content_length: int = 100,
    user_agent: str = "",
) -> dict[str, Any]:
    """带降级链的抓取 (P1-04, 对标 GPT Researcher).

    降级链: BS → Playwright → 失败.
    PDF/Arxiv 不降级 (专用抓取器).
    user_agent 参数预留 (session 已含 UA, 由调用方在构建 session 时注入).
    方案 E: scraper_mode=lightweight 时跳过 Playwright 降级.
    """
    # 读取配置
    settings = get_settings()
    scraper_mode = settings.scraper_mode

    url_lower = url.lower()

    # PDF / Arxiv 不降级 (专用抓取器)
    if url_lower.endswith(".pdf"):
        from src.skills.researcher.scrapers.pymupdf_scraper import PyMuPDFScraper

        return await _safe_scrape(PyMuPDFScraper(url, session))

    if "arxiv.org" in url_lower:
        from src.skills.researcher.scrapers.arxiv_scraper import ArxivScraper

        return await _safe_scrape(ArxivScraper(url, session))

    # 方案 E: playwright 模式直接走 Playwright (调试用)
    if scraper_mode == "playwright":
        from src.skills.researcher.scrapers.playwright_scraper import PlaywrightScraper

        return await _safe_scrape(PlaywrightScraper(url, session))

    # 第一级: BeautifulSoup
    from src.skills.researcher.scrapers.beautiful_soup_scraper import BeautifulSoupScraper

    bs_result = await _safe_scrape(BeautifulSoupScraper(url, session))
    content = bs_result.get("content", "") or ""
    if content and len(content) >= min_content_length:
        return bs_result

    if not enable_fallback:
        return bs_result

    # 方案 E: lightweight 模式跳过 Playwright 降级 (适合离线最小化部署)
    if scraper_mode == "lightweight":
        return bs_result

    # 第二级: Playwright 降级 (auto 模式)
    logger.info("BS 抓取内容过短(%d), 降级 Playwright: %s", len(content), url)
    try:
        from src.skills.researcher.scrapers.playwright_scraper import PlaywrightScraper

        pw_result = await _safe_scrape(PlaywrightScraper(url, session))
        # Playwright 结果更好则使用
        if len(pw_result.get("content", "") or "") > len(content):
            return pw_result
        return bs_result  # 否则保留 BS 结果
    except Exception as e:  # noqa: BLE001
        logger.warning("Playwright 降级失败 %s: %s", url, e)
        return bs_result


async def scrape_urls(
    urls: list[str],
    *,
    scraper_type: str = "bs",
    max_workers: int = 15,
    rate_limit_delay: float = 0.0,
    user_agent: str = "",
    enable_fallback: bool = True,
) -> list[dict[str, Any]]:
    """并发抓取多个 URL.

    对标 GPT Researcher actions/web_scraping.py 的 scrape_urls.
    返回 [{"url","content","title","image_urls","content_type"}].
    enable_fallback=True 时启用 BS → Playwright 降级链 (P1-04).
    scraper_type 仅在非降级路径 (enable_fallback=False) 生效.
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
                if enable_fallback:
                    return await scrape_with_fallback(
                        url,
                        session=session,
                        enable_fallback=True,
                        min_content_length=100,
                        user_agent=user_agent,
                    )
                try:
                    scraper = get_scraper(url, scraper_type, session)
                    result = await scraper.scrape()
                    # 内容过短直接丢弃 (对标 GPT Researcher)
                    if len(result.get("content", "")) < 100:
                        return {
                            "url": url,
                            "content": None,
                            "title": "",
                            "image_urls": [],
                        }
                    return result
                except Exception as e:  # noqa: BLE001
                    logger.warning("抓取失败 %s: %s", url, e)
                    return {"url": url, "content": None, "title": "", "image_urls": []}

        results = await asyncio.gather(*[_scrape_one(u) for u in unique_urls])

    # 过滤失败结果
    return [r for r in results if r.get("content")]
