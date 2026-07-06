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
        browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
        chrome_path = f"{browsers_path}/chromium-1228/chrome-linux64/chrome"
        # ASYNC240: 异步函数内不应使用阻塞的 os.path.exists
        if await asyncio.to_thread(os.path.exists, chrome_path):
            launch_kwargs["executable_path"] = chrome_path
            logger.debug("使用完整 Chrome: %s", chrome_path)
    return launch_kwargs


class _PlaywrightPool:
    """全局 Playwright 浏览器池 (单例, P0-6 修复).

    复用 browser 实例, 每次 scrape 仅创建新 page (轻量),
    避免每个 URL 启动新 chromium 进程 (1-3s 开销).
    """

    _instance: ClassVar[_PlaywrightPool | None] = None
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    _browser: Any = None
    _playwright: Any = None

    @classmethod
    async def get(cls, settings: Settings | None = None) -> Any:
        """获取 browser 实例 (首次调用启动 chromium, 后续复用)."""
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

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        logger.info("Playwright 浏览器池已就绪")
        return self._browser

    @classmethod
    async def shutdown(cls) -> None:
        """关闭浏览器池 (供 server.py lifespan 关闭时调用)."""
        if cls._instance is not None:
            if cls._instance._browser is not None:
                try:
                    await cls._instance._browser.close()
                except Exception as e:  # noqa: BLE001
                    logger.warning("Playwright browser 关闭失败: %s", e)
            if cls._instance._playwright is not None:
                try:
                    await cls._instance._playwright.stop()
                except Exception as e:  # noqa: BLE001
                    logger.warning("Playwright playwright 关闭失败: %s", e)
            cls._instance = None


class PlaywrightScraper(BaseScraper):
    """Playwright 抓取器 (JS 渲染).

    比 BeautifulSoup 重, 仅在配置 SCRAPER=playwright 时启用.
    镜像内需预装 chromium.
    """

    name = "playwright"

    async def scrape(self) -> dict[str, Any]:
        """用 Playwright 抓取 JS 渲染页面 (P0-6: 复用全局浏览器池)."""
        try:
            from playwright.async_api import async_playwright

            from src.config.settings import get_settings

            settings = get_settings()
            browser: Any = None
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
                    fallback_playwright = await async_playwright().start()
                    browser = await fallback_playwright.chromium.launch(**launch_kwargs)
                    own_browser = True

                page = await browser.new_page()
                await page.goto(self.url, wait_until="networkidle", timeout=30000)

                # 滚动到底部触发懒加载
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2000)

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
                # page.close() 失败不阻断流程
                if page is not None:
                    try:
                        await page.close()
                    except Exception as e:  # noqa: BLE001
                        logger.warning("page.close 失败 (不阻断): %s", e)
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
