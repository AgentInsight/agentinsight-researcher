"""Playwright 抓取器 - JS 渲染页面.

对标 GPT Researcher scraper/browser/browser.py (但用 Playwright 替代 Selenium).
适用于 JS 渲染的 SPA 页面.

P0-6: 全局 _PlaywrightPool 单例复用 browser, 每次 scrape 仅创建新 page,
避免每个 URL 启动新 chromium 进程 (1-3s 开销).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any, ClassVar

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
        # P0-1: 统一走 Settings SSOT, 不再绕过 settings 直接读 os.environ
        browsers_path = settings.playwright_browsers_path or "/opt/pw-browsers"
        chrome_path = f"{browsers_path}/chromium-1228/chrome-linux64/chrome"
        # ASYNC240: 异步函数内不应使用阻塞的 os.path.exists
        if await asyncio.to_thread(os.path.exists, chrome_path):
            launch_kwargs["executable_path"] = chrome_path
            logger.debug("使用完整 Chrome: %s", chrome_path)
    return launch_kwargs


class _PlaywrightPool:
    """全局 Playwright 浏览器池 (单例, P0-6 修复).

    复用 browser 实例, 每次 scrape 仅创建新 context+page (轻量),
    避免每个 URL 启动新 chromium 进程 (1-3s 开销).

    v2 修复:
    - _lock 改为懒加载, 避免模块导入时绑定错误事件循环
    - _ensure_browser 添加 30s 超时, 防止 chromium 启动挂起导致死锁
    - shutdown 先置 _instance=None 再清理, 防止重入; 异常时记录但不阻断
    """

    _instance: ClassVar[_PlaywrightPool | None] = None
    _lock: ClassVar[asyncio.Lock | None] = None
    _browser: Any = None
    _playwright: Any = None

    @classmethod
    async def get(cls, settings: Settings | None = None) -> Any:
        """获取 browser 实例 (首次调用启动 chromium, 后续复用)."""
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        async with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return await cls._instance._ensure_browser(settings)

    async def _ensure_browser(self, settings: Settings | None) -> Any:
        if self._browser is not None:
            return self._browser
        from src.config.settings import get_settings

        settings = settings or get_settings()
        launch_kwargs = await _build_launch_kwargs(settings)
        from playwright.async_api import async_playwright

        # v2: 30s 超时防止 chromium 启动挂起导致 _lock 无限持有
        self._playwright = await asyncio.wait_for(
            async_playwright().start(), timeout=30.0
        )
        self._browser = await asyncio.wait_for(
            self._playwright.chromium.launch(**launch_kwargs), timeout=30.0
        )
        logger.info("Playwright 浏览器池已就绪")
        return self._browser

    @classmethod
    async def shutdown(cls) -> None:
        """关闭浏览器池 (供 server.py lifespan 关闭时调用).

        v2: 先置 _instance=None 再清理, 防止重入; 异常时记录但不阻断.
        """
        instance = cls._instance
        cls._instance = None  # 先置 None 防止重入
        if instance is None:
            return
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
    """

    name = "playwright"

    async def scrape(self) -> dict[str, Any]:
        """用 Playwright 抓取 JS 渲染页面 (P0-6: 复用全局浏览器池).

        v2 修复:
        - 用 BrowserContext 隔离每次 scrape (cookies/storage 不残留, context.close 批量释放)
        - 降级模式 fallback_playwright 启动失败时清理已启动的 playwright 进程
        - finally 块优先关 context (一次性释放所有 page + storage)
        """
        try:
            from playwright.async_api import async_playwright

            from src.config.settings import get_settings

            settings = get_settings()
            browser: Any = None
            context: Any = None
            page: Any = None
            own_browser = False  # 降级模式: 自建 browser 需自行关闭
            fallback_playwright: Any = None

            try:
                # 优先复用全局浏览器池 (P0-6)
                try:
                    browser = await _PlaywrightPool.get(settings)
                except Exception as e:  # noqa: BLE001
                    # 浏览器池启动失败时降级到原同步模式 (每次启动新 browser)
                    logger.warning(
                        "Playwright 浏览器池启动失败, 降级同步模式 (每次新 browser): %s", e
                    )
                    launch_kwargs = await _build_launch_kwargs(settings)
                    # v2: fallback_playwright 启动失败时清理, 防止进程泄漏
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

                # v2: 用 BrowserContext 隔离, 避免默认 context 的 cookies/storage 残留
                # context.close() 一次性释放所有 page + storage, 比 page.close() 更可靠
                context = await browser.new_context()
                page = await context.new_page()
                # P1-3: 用 domcontentloaded 替代 networkidle (避免长网络请求拖到 30s timeout)
                await page.goto(self.url, wait_until="domcontentloaded", timeout=15000)

                # P1-3: 短等待 + 智能检测 (替代固定 2s 等待)
                try:
                    await page.wait_for_selector("body", timeout=5000)
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(500)
                except Exception as wait_e:  # noqa: BLE001
                    logger.debug(
                        "Playwright wait_for_selector/scroll 超时 (不阻断, 用已有 DOM): %s",
                        wait_e,
                    )

                content = await page.inner_text("body")
                title = await page.title()

                # 提取图片
                image_urls = await page.evaluate("""
                    () => Array.from(document.querySelectorAll('img'))
                        .map(img => img.src || img.dataset.src)
                        .filter(src => src && src.startsWith('http'))
                        .slice(0, 4)
                """)

                return {
                    "url": self.url,
                    "content": content,
                    "title": title,
                    "image_urls": image_urls,
                    "content_type": "html",
                }
            finally:
                # v2: 优先关 context (一次性释放所有 page + storage), 再关 browser (仅降级模式)
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
        except ImportError:
            logger.warning("playwright 未安装, 降级为空内容")
            return {"url": self.url, "content": "", "title": "", "image_urls": []}
        except Exception as e:  # noqa: BLE001
            logger.warning("Playwright 抓取失败 %s: %s", self.url, e)
            return {"url": self.url, "content": "", "title": "", "image_urls": []}
