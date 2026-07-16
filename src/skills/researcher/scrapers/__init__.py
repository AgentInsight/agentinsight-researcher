"""网页抓取器注册中心与调度.

抓取器清单:
- TrafilaturaScraper: L1 主路径 (LLM 友好 Markdown, 轻量级去噪)
- BSMarkdownifyScraper: L1 降级链 L2 (HTML→Markdown, 纯本地)
- BeautifulSoupScraper: 轻量, 速度快, 输出纯文本
- PlaywrightScraper: JS 渲染页面 (可选, 镜像内安装 chromium)
- PyMuPDFScraper: PDF 抓取
- ArxivScraper: Arxiv 论文 (含全文)
- FirecrawlScraper: Firecrawl 商业服务 (LLM 友好 Markdown 输出)
- TavilyExtractScraper: Tavily Extract API
  (LLM 友好纯文本输出, 复用 TAVILY_API_KEY)

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


# ========== 快速失败状态码集合 (403 快速失败, 省 BS/Playwright 内存) ==========
# 命中即跳过后续降级链, 直接返回空结果:
# - 401 Unauthorized / 403 Forbidden / 429 Too Many Requests
# 这些状态码不会因降级到 BS/Playwright 而成功 (服务器层拒绝),
# 继续降级只会徒增内存占用 (BS DOM 树 5-10x HTML / Playwright chromium ~400MB).
_FAST_FAIL_STATUS_CODES: frozenset[int] = frozenset({401, 403, 429})


def _is_fast_fail(result: dict[str, Any]) -> bool:
    """检测抓取结果是否携带快速失败状态码.

    scraper 在捕获 httpx.HTTPStatusError 时, 将状态码写入 result["_http_status"].
    命中 _FAST_FAIL_STATUS_CODES 时, 降级链应立即终止, 不再触发后续 BS/Playwright.
    """
    status = result.get("_http_status")
    return isinstance(status, int) and status in _FAST_FAIL_STATUS_CODES


# ========== 全局速率限制器 (单例, 跨所有 WorkerPool 实例) ==========


class GlobalRateLimiter:
    """全局速率限制器 (单例).

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


# ========== 域名级限流器 (单例) ==========

# 域名信号量空闲淘汰阈值 (30 分钟无获取请求则淘汰, 防止 _semaphores 字典无限增长)
_DOMAIN_IDLE_TIMEOUT_SECONDS: float = 1800.0


class DomainRateLimiter:
    """域名级限流器 (单例).

    每域名一个 asyncio.Semaphore(1), 同域名请求串行化, 避免单域名被封.
    被锁时随机延迟 0.6-1.2s (random.uniform(0.6, 1.2)).

    与 GlobalRateLimiter 区别:
    - GlobalRateLimiter: 全局速率 (跨所有域名, 控总 QPS)
    - DomainRateLimiter: 域名级串行 (同域名 1 并发, 避免被封)

    内存保护:
    - _semaphores 存 (semaphore, last_access_time) 二元组, 记录最后获取时间.
    - 惰性清理: 超过 _DOMAIN_IDLE_TIMEOUT_SECONDS (30 分钟) 未获取请求的域名
      在 _get_semaphore 创建新条目前被淘汰, 防止字典无限增长.
    - 被锁定 (sem.locked()) 的域名不淘汰, 防止删除正在持有的协程上下文.

    使用方式:
        limiter = get_domain_rate_limiter()
        async with limiter.throttle(url):
            # 同域名请求串行执行
            ...
    """

    _instance: DomainRateLimiter | None = None
    _semaphores: dict[str, tuple[asyncio.Semaphore, float]]
    _lock: asyncio.Lock

    def __new__(cls) -> DomainRateLimiter:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._semaphores = {}
            cls._instance._lock = asyncio.Lock()
        return cls._instance

    def _get_domain(self, url: str) -> str:
        """从 URL 提取二级域名."""
        from urllib.parse import urlparse

        domain = urlparse(url).netloc
        parts = domain.split(".")
        if len(parts) > 2:
            domain = ".".join(parts[-2:])
        return domain

    def _cleanup_idle(self) -> None:
        """惰性清理空闲域名信号量 (超过阈值未访问且未被锁定).

        在 _get_semaphore 创建新条目前调用, 避免后台任务.
        asyncio 单线程模型, 字典遍历+删除无需额外锁.
        """
        now = time.monotonic()
        idle_domains = [
            domain
            for domain, (sem, last_access) in self._semaphores.items()
            if now - last_access > _DOMAIN_IDLE_TIMEOUT_SECONDS and not sem.locked()
        ]
        for domain in idle_domains:
            del self._semaphores[domain]

    async def _get_semaphore(self, domain: str) -> asyncio.Semaphore:
        """获取域名 Semaphore (惰性创建 + 空闲清理).

        命中时更新最后访问时间; 未命中时加锁双重检查并触发惰性清理.
        """
        now = time.monotonic()
        entry = self._semaphores.get(domain)
        if entry is not None:
            sem, _ = entry
            # 更新最后访问时间 (tuple 不可变, 重新赋值)
            self._semaphores[domain] = (sem, now)
            return sem
        async with self._lock:
            # 双重检查, 防并发创建
            entry = self._semaphores.get(domain)
            if entry is not None:
                sem, _ = entry
                self._semaphores[domain] = (sem, now)
                return sem
            # 惰性清理: 创建新条目前淘汰空闲域名
            self._cleanup_idle()
            sem = asyncio.Semaphore(1)
            self._semaphores[domain] = (sem, now)
            return sem

    @asynccontextmanager
    async def throttle(self, url: str) -> AsyncIterator[None]:
        """域名级限流 (同域名串行化 + 随机延迟).

        Args:
            url: 请求 URL (用于提取域名)
        """
        import random

        domain = self._get_domain(url)
        sem = await self._get_semaphore(domain)
        was_locked = sem.locked()
        async with sem:
            # 被锁时随机延迟 0.6-1.2s (避免密集重试)
            if was_locked:
                await asyncio.sleep(random.uniform(0.6, 1.2))
            yield


def get_domain_rate_limiter() -> DomainRateLimiter:
    """获取域名级限流器单例."""
    return DomainRateLimiter()


# ========== WorkerPool 并发池 ==========


class WorkerPool:
    """并发工作池.

    asyncio.Semaphore 控并发, GlobalRateLimiter 控全局速率.
    域名级限流由 scraper 主动调用 DomainRateLimiter.throttle(url).
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
    async def throttle(self, url: str | None = None) -> AsyncIterator[None]:
        """获取并发槽 + 全局速率限制 + 域名级限流.

        Args:
            url: 可选 URL, 提供时启用域名级限流 (向后兼容)
        """
        async with self.semaphore:
            limiter = get_global_rate_limiter()
            await limiter.wait_if_needed()
            # 域名级限流 (同域名串行化 + 随机延迟)
            if url:
                domain_limiter = get_domain_rate_limiter()
                async with domain_limiter.throttle(url):
                    yield
            else:
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

    get_scraper 路由逻辑:
    - URL 以 .pdf 结尾 → PyMuPDFScraper
    - URL 含 arxiv.org → ArxivScraper
    - URL 以 Office 文档后缀结尾 → MarkItDownScraper
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
        # Tavily Extract API 抓取器
        from src.skills.researcher.scrapers.tavily_extract_scraper import (
            TavilyExtractScraper,
        )

        return TavilyExtractScraper(url, session)

    # 默认 BeautifulSoup
    from src.skills.researcher.scrapers.beautiful_soup_scraper import BeautifulSoupScraper

    return BeautifulSoupScraper(url, session)


async def _safe_scrape(scraper: BaseScraper) -> dict[str, Any]:
    """安全抓取 (异常返回空结果).

    scrape_with_fallback 的容错语义.
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
    """带降级链的抓取.

    L1 降级链:
      1. Trafilatura (LLM 友好 Markdown, 轻量级去噪, 15s timeout)
      2. BS+markdownify (HTML→Markdown, 纯本地, 15s timeout)
      3. Playwright (JS 渲染兜底, 30s timeout)

    PDF/Arxiv 不降级 (专用抓取器).
    user_agent 参数预留 (session 已含 UA, 由调用方在构建 session 时注入).
    方案 E: scraper_mode=lightweight 时跳过 Playwright 降级.

    内存优化 (3 项硬性要求):
    - 成功不降级: 各级成功 (>=min_content_length) 直接返回, 不做 max() 三级比较.
      原 max() 导致 tf/bsm/pw 三份内容同时驻留 (+1.5GB), 现仅驻留当前级.
    - 失败不驻留: 降级前 del 上一级结果 (tf_result/tf_content, bsm_result/bsm_content),
      CPython 引用计数立即释放, 避免累积. L3 失败返回空结果 (L1/L2 已 del, 无法回退).
    - 403 快速失败: L1/L2 命中 _FAST_FAIL_STATUS_CODES (401/403/429) 立即返回,
      不触发后续 BS/Playwright (服务器层拒绝, 降级必失败, 徒增 BS DOM 50MB / chromium 400MB).
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

    # 403 快速失败: 服务器层拒绝, 降级到 BS/Playwright 也无法成功
    if _is_fast_fail(tf_result):
        return tf_result

    tf_content = tf_result.get("content", "") or ""
    # 成功不降级 (硬性要求): 内容达标直接返回
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
    # 失败不驻留: 释放 L1 结果, 避免 L1+L2 结果同时驻留 (原 max() 三级比较的根因)
    del tf_result, tf_content

    from src.skills.researcher.scrapers.bs_markdownify_scraper import (
        BSMarkdownifyScraper,
    )

    bsm_result = await _safe_scrape(BSMarkdownifyScraper(url, session))

    # 403 快速失败: L2 命中也立即终止, 不触发 Playwright
    if _is_fast_fail(bsm_result):
        return bsm_result

    bsm_content = bsm_result.get("content", "") or ""
    # 成功不降级 (硬性要求): 内容达标直接返回
    if bsm_content and len(bsm_content) >= min_content_length:
        return bsm_result

    # 第三级: Playwright (JS 渲染兜底)
    logger.info(
        "BS+markdownify 抓取内容过短, 降级 Playwright: %s",
        url,
    )
    # 失败不驻留: 释放 L2 结果, 避免 L2+L3 结果同时驻留
    del bsm_result, bsm_content

    try:
        from src.skills.researcher.scrapers.playwright_scraper import PlaywrightScraper

        pw_result = await _safe_scrape(PlaywrightScraper(url, session))
        # 成功不降级 (硬性要求): 移除原 max() 三级比较,
        # Playwright 结果无论长度直接返回 (已是兜底, 无更优选项).
        # 原 max() 导致 tf_content/bsm_content/pw_content 三份同时驻留 (+1.5GB),
        # 现已通过 del 释放 L1/L2, 仅 pw_result 单份驻留.
        return pw_result
    except Exception as e:  # noqa: BLE001
        logger.warning("Playwright 降级失败 %s: %s", url, e)
        # L3 失败: 返回空结果 (L1/L2 已 del, 无法回退).
        # 这是"失败不驻留"的必然结果: 所有降级均已失败, 返回空符合语义.
        return {"url": url, "content": "", "title": "", "image_urls": []}


# ========== 共享 HTTP 客户端 (单例, 复用 TCP 连接) ==========

# 模块级单例 (惰性创建, 首次调用 get_shared_http_client 时初始化)
# httpx 类型注解由 TYPE_CHECKING 守卫 (文件顶部), 运行时在 get_shared_http_client 内惰性导入
_shared_http_client: httpx.AsyncClient | None = None
_shared_http_client_lock: asyncio.Lock | None = None


async def get_shared_http_client(user_agent: str = "") -> httpx.AsyncClient:
    """获取共享 httpx.AsyncClient 单例 (惰性创建).

    复用 TCP 连接池, 避免 scrape_urls 每次调用都新建客户端导致
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

    供 server.py lifespan 清理调用, 释放底层 TCP 连接池.
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

    返回 [{"url","content","title","image_urls","content_type"}].
    enable_fallback=True 时启用 BS → Playwright 降级链.
    scraper_type 仅在非降级路径 (enable_fallback=False) 生效.

    使用共享 httpx.AsyncClient 单例复用 TCP 连接, 不再每次新建客户端.
    """
    if not urls:
        return []

    # 保序去重
    unique_urls = list(dict.fromkeys(urls))
    worker_pool = WorkerPool(max_workers, rate_limit_delay)

    # 复用共享 HTTP 客户端 (单例, 惰性创建)
    session = await get_shared_http_client(user_agent=user_agent)

    async def _scrape_one(url: str) -> dict[str, Any]:
        # 传入 url 启用域名级限流 (同域名串行化, 避免被封)
        async with worker_pool.throttle(url):
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
                # 内容过短直接丢弃
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

    # P1-17: 用 as_completed 增量处理, 失败结果立即丢弃不驻留
    # (原 gather 全量收集所有结果再过滤, 大批量抓取时峰值内存高)
    # 通过 _scrape_with_idx 包装返回 (idx, result) 保序, 与原 gather 输出顺序一致.
    async def _scrape_with_idx(idx: int, url: str) -> tuple[int, dict[str, Any]]:
        return idx, await _scrape_one(url)

    results: list[dict[str, Any] | None] = [None] * len(unique_urls)
    aws = [_scrape_with_idx(i, u) for i, u in enumerate(unique_urls)]
    for completed in asyncio.as_completed(aws):
        idx, result = await completed
        # 失败结果 (content 为空/None) 立即丢弃, 不驻留到返回列表
        if result.get("content"):
            results[idx] = result

    # 过滤 None (失败项), 返回成功结果列表 (保持输入 URL 顺序)
    return [r for r in results if r is not None]
