"""Playwright 抓取器 - JS 渲染页面.

用 Playwright 替代 Selenium.
适用于 JS 渲染的 SPA 页面.

全局 _PlaywrightPool 单例复用 browser, 每次 scrape 仅创建新 page,
避免每个 URL 启动新 chromium 进程 (1-3s 开销).

优化:
- 浏览器池化: max_browsers=5 + 负载均衡 (min processing_count), 支持高并发
- 域名级限流: Semaphore(1) per domain + 随机延迟, 避免单域名被封
- 图片评分: get_relevant_images() 按尺寸/class 评分排序
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlparse

from src.skills.researcher.scrapers import BaseScraper

if TYPE_CHECKING:
    from src.config.settings import Settings

logger = logging.getLogger(__name__)


async def _build_launch_kwargs(settings: Settings) -> dict[str, Any]:
    """构建 Playwright chromium launch 参数 (池与降级模式复用).

    保留: 离线模式 PLAYWRIGHT_BROWSERS_PATH / 非 root 兼容 --no-sandbox /
    asyncio.to_thread 异步检测 chrome 路径.
    """
    launch_kwargs: dict[str, Any] = {
        "headless": True,
        "args": [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ],
    }
    # Playwright 浏览器目录 (离线模式预下载, 默认 ~/.cache/ms-playwright)
    if settings.playwright_browsers_path:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = settings.playwright_browsers_path
    # 系统 chromium 路径 (方案 B 兼容)
    if settings.playwright_chromium_executable_path:
        launch_kwargs["executable_path"] = settings.playwright_chromium_executable_path
    else:
        # 方案 E: 自动检测已下载的完整 Chrome (而非 headless shell)
        # Playwright 1.61+ 默认查找 chromium_headless_shell-*/chrome-headless-shell
        # 但离线模式可能只下载了完整 Chrome (chromium-*/chrome-linux64/chrome)
        # 统一走 Settings SSOT, 不再绕过 settings 直接读 os.environ
        browsers_path = settings.playwright_browsers_path or "/opt/pw-browsers"
        chrome_path = f"{browsers_path}/chromium-1228/chrome-linux64/chrome"
        # ASYNC240: 异步函数内不应使用阻塞的 os.path.exists
        if await asyncio.to_thread(os.path.exists, chrome_path):
            launch_kwargs["executable_path"] = chrome_path
            logger.debug("使用完整 Chrome: %s", chrome_path)
    return launch_kwargs


def _get_domain(url: str) -> str:
    """从 URL 提取二级域名.

    示例: https://www.example.com/path → example.com
    用于域名级限流 (同域名请求串行化, 避免被封).
    """
    domain = urlparse(url).netloc
    parts = domain.split(".")
    if len(parts) > 2:
        domain = ".".join(parts[-2:])
    return domain


# 域名信号量空闲淘汰阈值 (30 分钟无获取请求则淘汰, 防止 domain_semaphores 字典无限增长)
_DOMAIN_IDLE_TIMEOUT_SECONDS: float = 1800.0


class _PooledBrowser:
    """池化浏览器包装.

    封装 Playwright browser 实例 + 负载计数 + 域名级 Semaphore.
    - processing_count: 当前并发处理数, 用于负载均衡 (min 选择)
    - domain_semaphores: 每域名一个 Semaphore(1), 同域名串行化

    内存保护:
    - domain_semaphores 存 (semaphore, last_access_time) 二元组, 记录最后获取时间.
    - 惰性清理: 超过 _DOMAIN_IDLE_TIMEOUT_SECONDS (30 分钟) 未获取请求的域名
      在 acquire_domain 创建新条目前被淘汰, 防止字典无限增长.
    - 被锁定 (sem.locked()) 的域名不淘汰, 防止删除正在持有的协程上下文.
    """

    def __init__(self, browser: Any, playwright: Any) -> None:
        self.browser = browser
        self.playwright = playwright
        self.processing_count: int = 0
        self.domain_semaphores: dict[str, tuple[asyncio.Semaphore, float]] = {}
        self.stopping: bool = False

    def _cleanup_idle_domains(self) -> None:
        """惰性清理空闲域名信号量 (超过阈值未访问且未被锁定).

        在 acquire_domain 创建新条目前调用, 避免后台任务.
        asyncio 单线程模型, 字典遍历+删除无需额外锁.
        """
        now = time.monotonic()
        idle_domains = [
            domain
            for domain, (sem, last_access) in self.domain_semaphores.items()
            if now - last_access > _DOMAIN_IDLE_TIMEOUT_SECONDS and not sem.locked()
        ]
        for domain in idle_domains:
            del self.domain_semaphores[domain]

    async def acquire_domain(self, url: str) -> asyncio.Semaphore | None:
        """获取域名级 Semaphore (同域名串行化, 加锁时随机延迟 0.6-1.2s).

        返回 Semaphore 供调用方 async with 使用; None 表示无 URL (不限流).
        命中时更新最后访问时间; 未命中时创建并触发惰性清理.
        """
        if not url:
            return None
        domain = _get_domain(url)
        now = time.monotonic()
        entry = self.domain_semaphores.get(domain)
        if entry is not None:
            sem, _ = entry
            # 更新最后访问时间 (tuple 不可变, 重新赋值)
            self.domain_semaphores[domain] = (sem, now)
            return sem
        # 惰性清理: 创建新条目前淘汰空闲域名
        self._cleanup_idle_domains()
        sem = asyncio.Semaphore(1)
        self.domain_semaphores[domain] = (sem, now)
        return sem

    async def stop(self) -> None:
        """关闭浏览器 + playwright (幂等)."""
        if self.stopping:
            return
        self.stopping = True
        if self.browser is not None:
            try:
                await self.browser.close()
            except Exception as e:  # noqa: BLE001
                logger.warning("池化 browser.close 失败: %s", e)
        if self.playwright is not None:
            try:
                await self.playwright.stop()
            except Exception as e:  # noqa: BLE001
                logger.warning("池化 playwright.stop 失败: %s", e)


class _PlaywrightPool:
    """全局 Playwright 浏览器池 (单例, 池化优化).

    v3 优化:
    - max_browsers=5: 池上限, 超过则复用负载最低的
    - browser_load_threshold=8: 单 browser 并发阈值, 超过则新建
    - 负载均衡: get_browser 选 min(processing_count)
    - release_browser: processing_count 归零时可选关闭 (本实现保留池化, 不主动关闭)

    v2 修复保留:
    - _lock 懒加载, 避免模块导入时绑定错误事件循环
    - _ensure_browser 添加 30s 超时, 防止 chromium 启动挂起
    - shutdown 先置 _instance=None 再清理, 防止重入
    """

    _instance: ClassVar[_PlaywrightPool | None] = None
    _lock: ClassVar[asyncio.Lock | None] = None
    # v3: 池化 (max_browsers=5)
    _pooled_browsers: ClassVar[set[_PooledBrowser]] = set()
    _max_browsers: ClassVar[int] = 5
    _browser_load_threshold: ClassVar[int] = 8
    # 兼容旧字段 (降级模式检测用)
    _browser: Any = None
    _playwright: Any = None

    @classmethod
    async def get(cls, settings: Settings | None = None) -> Any:
        """获取 browser 实例 (v3: 从池中选负载最低, 必要时新建).

        返回 Playwright browser 实例 (调用方需配合 acquire/release 计数).
        """
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        async with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return await cls._instance._get_or_create_browser(settings)

    @classmethod
    async def acquire(
        cls, url: str, settings: Settings | None = None
    ) -> tuple[Any, asyncio.Semaphore | None, _PooledBrowser]:
        """获取 browser + 域名 Semaphore + 池包装 (v3 新增).

        调用方需在 finally 中调用 release(pooled).
        返回 (browser, domain_semaphore, pooled_browser).
        """
        pooled = await cls._get_pooled_browser(settings)
        pooled.processing_count += 1
        try:
            sem = await pooled.acquire_domain(url)
        except Exception:
            pooled.processing_count -= 1
            raise
        return pooled.browser, sem, pooled

    @classmethod
    async def _get_pooled_browser(cls, settings: Settings | None = None) -> _PooledBrowser:
        """内部: 获取池化 browser (负载均衡 + 必要时新建)."""
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        async with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return await cls._instance._get_or_create_pooled(settings)

    async def _get_or_create_pooled(self, settings: Settings | None) -> _PooledBrowser:
        """v3 核心逻辑: 负载均衡选最低, 超阈值则新建."""
        # 池为空: 新建
        if not self._pooled_browsers:
            return await self._create_pooled_browser(settings)

        # 负载均衡: 选 processing_count 最低的 (min key)
        browser = min(self._pooled_browsers, key=lambda b: b.processing_count)

        # 所有 browser 都超阈值且池未满: 新建
        if (
            browser.processing_count >= self._browser_load_threshold
            and len(self._pooled_browsers) < self._max_browsers
        ):
            return await self._create_pooled_browser(settings)

        return browser

    async def _create_pooled_browser(self, settings: Settings | None) -> _PooledBrowser:
        """新建池化 browser (30s 超时防挂起)."""
        from src.config.settings import get_settings

        settings = settings or get_settings()
        launch_kwargs = await _build_launch_kwargs(settings)
        from playwright.async_api import async_playwright

        # v2: 30s 超时防止 chromium 启动挂起导致 _lock 无限持有
        playwright = await asyncio.wait_for(async_playwright().start(), timeout=30.0)
        browser = await asyncio.wait_for(playwright.chromium.launch(**launch_kwargs), timeout=30.0)
        pooled = _PooledBrowser(browser, playwright)
        self._pooled_browsers.add(pooled)
        logger.info(
            "Playwright 池化 browser 已创建 (池大小=%d/%d)",
            len(self._pooled_browsers),
            self._max_browsers,
        )
        return pooled

    async def _get_or_create_browser(self, settings: Settings | None) -> Any:
        """v2 兼容入口: 返回 browser 实例 (供旧调用方使用, 不做计数)."""
        pooled = await self._get_or_create_pooled(settings)
        return pooled.browser

    @classmethod
    async def release(cls, pooled: _PooledBrowser) -> None:
        """释放 browser 槽位 (v3 新增).

        processing_count 归零时不关闭 (保留池化复用);
        仅 shutdown 时统一关闭.
        """
        pooled.processing_count = max(0, pooled.processing_count - 1)

    @classmethod
    async def shutdown(cls) -> None:
        """关闭浏览器池 (供 server.py lifespan 关闭时调用).

        v3: 遍历池中所有 _PooledBrowser 逐一关闭.
        v2: 先置 _instance=None 再清理, 防止重入; 异常时记录但不阻断.
        """
        instance = cls._instance
        cls._instance = None  # 先置 None 防止重入
        if instance is None:
            return
        # v3: 关闭池中所有 browser
        for pooled in list(cls._pooled_browsers):
            try:
                await pooled.stop()
            except Exception as e:  # noqa: BLE001
                logger.warning("池化 browser 关闭失败: %s", e)
        cls._pooled_browsers.clear()
        # v2 兼容: 旧字段清理
        if instance._browser is not None:
            try:
                await instance._browser.close()
            except Exception as e:  # noqa: BLE001
                logger.warning("Playwright browser 关闭失败 (可能遗留 zombie 进程): %s", e)
        if instance._playwright is not None:
            try:
                await instance._playwright.stop()
            except Exception as e:  # noqa: BLE001
                logger.warning("Playwright playwright 关闭失败: %s", e)


class PlaywrightScraper(BaseScraper):
    """Playwright 抓取器 (JS 渲染).

    比 BeautifulSoup 重, 仅在配置 SCRAPER=playwright 时启用.
    镜像内需预装 chromium.

    优化:
    - 浏览器池化: acquire/release 语义, 负载均衡
    - 域名限流: acquire 返回 domain_semaphore, async with 加锁
    - 图片评分: 调用 utils.get_relevant_images_from_html 评分排序
    """

    name = "playwright"

    async def scrape(self) -> dict[str, Any]:
        """用 Playwright 抓取 JS 渲染页面 (复用全局浏览器池).

        v3 修复:
        - acquire/release 语义: 配合池化负载均衡 + 域名限流
        - 域名 Semaphore: 同域名串行化 + 随机延迟 0.6-1.2s
        - 图片提取改用 get_relevant_images_from_html 评分排序
        v2 修复保留:
        - BrowserContext 隔离 + context.close 批量释放
        - 降级模式 fallback_playwright 启动失败时清理
        """
        try:
            from playwright.async_api import async_playwright

            from src.config.settings import get_settings
            from src.skills.researcher.scrapers.utils import (
                get_relevant_images_from_html,
            )

            settings = get_settings()
            browser: Any = None
            context: Any = None
            page: Any = None
            own_browser = False  # 降级模式: 自建 browser 需自行关闭
            fallback_playwright: Any = None
            pooled: _PooledBrowser | None = None
            domain_sem: asyncio.Semaphore | None = None

            try:
                # v3: 池化 acquire (负载均衡 + 域名 Semaphore)
                try:
                    browser, domain_sem, pooled = await _PlaywrightPool.acquire(self.url, settings)
                except Exception as e:  # noqa: BLE001
                    # 浏览器池启动失败时降级到原同步模式 (每次启动新 browser)
                    logger.warning(
                        "Playwright 浏览器池启动失败, 降级同步模式 (每次新 browser): %s", e
                    )
                    launch_kwargs = await _build_launch_kwargs(settings)
                    try:
                        fallback_playwright = await async_playwright().start()
                        browser = await fallback_playwright.chromium.launch(**launch_kwargs)
                    except Exception:
                        if fallback_playwright is not None:
                            try:
                                await fallback_playwright.stop()
                            except Exception as stop_e:  # noqa: BLE001
                                logger.warning("降级 playwright.stop 清理失败: %s", stop_e)
                        raise
                    own_browser = True

                # v3: 域名级限流 (同域名串行化 + 随机延迟)
                async def _do_scrape() -> dict[str, Any]:
                    nonlocal context, page
                    # v2: BrowserContext 隔离, context.close 一次性释放 page+storage
                    context = await browser.new_context()
                    page = await context.new_page()
                    # domcontentloaded 替代 networkidle
                    await page.goto(self.url, wait_until="domcontentloaded", timeout=15000)

                    # 短等待 + 智能检测
                    try:
                        await page.wait_for_selector("body", timeout=5000)
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await page.wait_for_timeout(500)
                    except Exception as wait_e:  # noqa: BLE001
                        logger.debug(
                            "Playwright wait_for_selector/scroll 超时 (不阻断): %s",
                            wait_e,
                        )

                    content = await page.inner_text("body")
                    title = await page.title()

                    # v3: 提取页面 HTML 用于图片评分 (get_relevant_images)
                    html_content = await page.content()
                    # 图片评分排序 (按尺寸/class 评分, 取 Top-4)
                    image_urls = get_relevant_images_from_html(html_content, self.url, top_k=4)

                    return {
                        "url": self.url,
                        "content": content,
                        "title": title,
                        "image_urls": image_urls,
                        "content_type": "html",
                    }

                if domain_sem is not None:
                    # v3: 域名 Semaphore 加锁 (被锁时随机延迟)
                    was_locked = domain_sem.locked()
                    async with domain_sem:
                        if was_locked:
                            await asyncio.sleep(random.uniform(0.6, 1.2))
                        return await _do_scrape()
                else:
                    return await _do_scrape()
            finally:
                # v2: 优先关 context (一次性释放所有 page + storage)
                if context is not None:
                    try:
                        await context.close()
                    except Exception as e:  # noqa: BLE001
                        logger.warning("context.close 失败 (不阻断): %s", e)
                # 仅降级模式关闭 browser (池模式由 _PlaywrightPool.shutdown 统一关闭)
                if own_browser:
                    if browser is not None:
                        try:
                            await browser.close()
                        except Exception as e:  # noqa: BLE001
                            logger.warning("降级 browser.close 失败: %s", e)
                    if fallback_playwright is not None:
                        try:
                            await fallback_playwright.stop()
                        except Exception as e:  # noqa: BLE001
                            logger.warning("降级 playwright.stop 失败: %s", e)
                # v3: 释放池化槽位 (processing_count -1)
                if pooled is not None:
                    try:
                        await _PlaywrightPool.release(pooled)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("池化 release 失败 (不阻断): %s", e)
        except ImportError:
            logger.warning("playwright 未安装, 降级为空内容")
            return {"url": self.url, "content": "", "title": "", "image_urls": []}
        except Exception as e:  # noqa: BLE001
            logger.warning("Playwright 抓取失败 %s: %s", self.url, e)
            return {"url": self.url, "content": "", "title": "", "image_urls": []}
