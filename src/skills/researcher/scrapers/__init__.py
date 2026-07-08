"""网页抓取器注册中心与调度.

对标 GPT Researcher scraper/ 体系.
- TrafilaturaScraper: L1 主路径 (LLM 友好 Markdown, 轻量级去噪)
- BSMarkdownifyScraper: L1 降级链 L2 (HTML→Markdown, 纯本地)
- BeautifulSoupScraper: 旧版默认 (轻量, 速度快, 输出纯文本)
- PlaywrightScraper: JS 渲染页面 (可选, 镜像内安装 chromium)
- PyMuPDFScraper: PDF 抓取
- ArxivScraper: Arxiv 论文 (含全文)
- FirecrawlScraper: Firecrawl 商业服务 (P1-Future-08, LLM 友好 Markdown 输出)
- TavilyExtractScraper: Tavily Extract API (对标 GPTR scraper/tavily_extract,
  LLM 友好纯文本输出, 复用 TAVILY_API_KEY)

L1 降级链:
  Trafilatura (LLM 友好 Markdown) → BS+markdownify (HTML→Markdown) → Playwright (兜底)

WorkerPool 并发限流: asyncio.Semaphore + GlobalRateLimiter 单例.
所有 scraper 共享 scrape(url) -> dict 规约.
项目内 scraper 走 get_scraper 函数式工厂路由 (按 URL 后缀 + scraper_type 参数).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from src.config.settings import Settings as Settings
from src.config.settings import get_settings as get_settings

if TYPE_CHECKING:
    import httpx

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


# ========== Scraper 工厂路由 (函数式 if-else, 项目内 scraper 走此路径) ==========


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
    - 否则 → 按配置 (bs/playwright/firecrawl/nodriver/tavily_extract)
    """
    url_lower = url.lower()

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

    if scraper_type == "trafilatura":
        from src.skills.researcher.scrapers.trafilatura_scraper import TrafilaturaScraper

        return TrafilaturaScraper(url, session)

    if scraper_type == "bs_markdownify":
        from src.skills.researcher.scrapers.bs_markdownify_scraper import (
            BSMarkdownifyScraper,
        )

        return BSMarkdownifyScraper(url, session)

    if scraper_type == "firecrawl":
        from src.skills.researcher.scrapers.firecrawl_scraper import FirecrawlScraper

        return FirecrawlScraper(url, session)

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
    """带降级链的抓取 (对标 GPT Researcher).

    L1 降级链:
      1. Trafilatura (LLM 友好 Markdown, 轻量级去噪, 15s timeout)
      2. BS+markdownify (HTML→Markdown, 纯本地, 15s timeout)
      3. Playwright (JS 渲染兜底, 30s timeout)

    PDF/Arxiv 不降级 (专用抓取器).
    user_agent 参数预留 (session 已含 UA, 由调用方在构建 session 时注入).
    方案 E: scraper_mode=lightweight 时跳过 Playwright 降级.
    """
    settings = get_settings()
    scraper_mode = settings.scraper_mode

    url_lower = url.lower()

    if url_lower.endswith(".pdf"):
        from src.skills.researcher.scrapers.pymupdf_scraper import PyMuPDFScraper

        return await _safe_scrape(PyMuPDFScraper(url, session))

    if "arxiv.org" in url_lower:
        from src.skills.researcher.scrapers.arxiv_scraper import ArxivScraper

        return await _safe_scrape(ArxivScraper(url, session))

    if scraper_mode == "playwright":
        from src.skills.researcher.scrapers.playwright_scraper import PlaywrightScraper

        return await _safe_scrape(PlaywrightScraper(url, session))

    # ━━━━━━━━━━━ L1 降级链 ━━━━━━━━━━━
    # 第一级: Trafilatura (LLM 友好 Markdown)
    from src.skills.researcher.scrapers.trafilatura_scraper import TrafilaturaScraper

    tf_result = await _safe_scrape(TrafilaturaScraper(url, session))
    tf_content = tf_result.get("content", "") or ""
    if tf_content and len(tf_content) >= min_content_length:
        return tf_result

    if not enable_fallback:
        return tf_result

    if scraper_mode == "lightweight":
        return tf_result

    # 第二级: BS+markdownify (HTML→Markdown, 纯本地)
    logger.info(
        "Trafilatura 抓取内容过短(%d), 降级 BS+markdownify: %s",
        len(tf_content),
        url,
    )
    from src.skills.researcher.scrapers.bs_markdownify_scraper import (
        BSMarkdownifyScraper,
    )

    bsm_result = await _safe_scrape(BSMarkdownifyScraper(url, session))
    bsm_content = bsm_result.get("content", "") or ""
    if bsm_content and len(bsm_content) >= min_content_length:
        return bsm_result

    # 第三级: Playwright (JS 渲染兜底)
    logger.info(
        "BS+markdownify 抓取内容过短, 降级 Playwright: %s",
        url,
    )
    try:
        from src.skills.researcher.scrapers.playwright_scraper import PlaywrightScraper

        pw_result = await _safe_scrape(PlaywrightScraper(url, session))
        best_content = max(
            [tf_content, bsm_content, pw_result.get("content", "") or ""],
            key=len,
        )
        if len(pw_result.get("content", "") or "") >= len(best_content):
            return pw_result
        for r in [tf_result, bsm_result, pw_result]:
            if r.get("content"):
                return r
        return tf_result
    except Exception as e:  # noqa: BLE001
        logger.warning("Playwright 降级失败 %s: %s", url, e)
        return tf_result


# ========== 共享 HTTP 客户端 (单例, 复用 TCP 连接, P1-3) ==========

# 模块级单例 (惰性创建, 首次调用 get_shared_http_client 时初始化)
# httpx 类型注解由 TYPE_CHECKING 守卫 (文件顶部), 运行时在 get_shared_http_client 内惰性导入
_shared_http_client: httpx.AsyncClient | None = None
_shared_http_client_lock: asyncio.Lock | None = None


async def get_shared_http_client(user_agent: str = "") -> httpx.AsyncClient:
    """获取共享 httpx.AsyncClient 单例 (惰性创建).

    P1-3: 复用 TCP 连接池, 避免 scrape_urls 每次调用都新建客户端导致
    重复 TCP 握手与 RTT 开销. 首次调用创建, 后续复用同一实例.

    配置:
    - timeout: 30s (连接/读取/写入/池整体)
    - limits: max_connections=20, max_keepalive_connections=10
    - headers: 默认 User-Agent
    - follow_redirects: True

    Args:
        user_agent: 可选 User-Agent (仅在首次创建时生效, 已存在实例时忽略).

    Returns:
        共享的 httpx.AsyncClient 实例.
    """
    global _shared_http_client, _shared_http_client_lock

    if _shared_http_client is not None:
        return _shared_http_client

    # 锁惰性初始化 (避免模块导入时创建事件循环绑定)
    if _shared_http_client_lock is None:
        _shared_http_client_lock = asyncio.Lock()

    async with _shared_http_client_lock:
        # 双重检查锁定, 防止并发首次创建
        if _shared_http_client is not None:
            return _shared_http_client

        import httpx

        _shared_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
            ),
            headers={
                "User-Agent": user_agent or "agentinsight-researcher/0.1",
            },
            follow_redirects=True,
        )
    return _shared_http_client


async def close_shared_http_client() -> None:
    """关闭共享 httpx.AsyncClient (应用关闭时调用).

    P1-3: 供 server.py lifespan 清理调用, 释放底层 TCP 连接池.
    幂等: 多次调用安全 (无实例时直接返回).
    """
    global _shared_http_client
    if _shared_http_client is not None:
        await _shared_http_client.aclose()
        _shared_http_client = None


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

    P1-3: 使用共享 httpx.AsyncClient 单例复用 TCP 连接, 不再每次新建客户端.
    """
    if not urls:
        return []

    # 保序去重
    unique_urls = list(dict.fromkeys(urls))
    worker_pool = WorkerPool(max_workers, rate_limit_delay)

    # P1-3: 复用共享 HTTP 客户端 (单例, 惰性创建)
    session = await get_shared_http_client(user_agent=user_agent)

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
