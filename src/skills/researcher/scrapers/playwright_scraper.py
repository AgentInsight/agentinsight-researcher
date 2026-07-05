"""Playwright 抓取器 - JS 渲染页面.

对标 GPT Researcher scraper/browser/browser.py (但用 Playwright 替代 Selenium).
适用于 JS 渲染的 SPA 页面.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from src.skills.researcher.scrapers import BaseScraper

logger = logging.getLogger(__name__)


class PlaywrightScraper(BaseScraper):
    """Playwright 抓取器 (JS 渲染).

    比 BeautifulSoup 重, 仅在配置 SCRAPER=playwright 时启用.
    镜像内需预装 chromium.
    """

    name = "playwright"

    async def scrape(self) -> dict[str, Any]:
        """用 Playwright 抓取 JS 渲染页面."""
        try:
            from playwright.async_api import async_playwright

            # 方案 E: 支持系统 chromium + 非 root 兼容
            from src.config.settings import get_settings

            settings = get_settings()
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
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = (
                    settings.playwright_browsers_path
                )
            # 系统 chromium 路径 (方案 B 兼容)
            if settings.playwright_chromium_executable_path:
                launch_kwargs["executable_path"] = (
                    settings.playwright_chromium_executable_path
                )
            else:
                # 方案 E: 自动检测已下载的完整 Chrome (而非 headless shell)
                # Playwright 1.61+ 默认查找 chromium_headless_shell-*/chrome-headless-shell
                # 但离线模式可能只下载了完整 Chrome (chromium-*/chrome-linux64/chrome)
                browsers_path = os.environ.get(
                    "PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"
                )
                chrome_path = f"{browsers_path}/chromium-1228/chrome-linux64/chrome"
                if os.path.exists(chrome_path):
                    launch_kwargs["executable_path"] = chrome_path
                    logger.debug("使用完整 Chrome: %s", chrome_path)

            async with async_playwright() as p:
                browser = await p.chromium.launch(**launch_kwargs)
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

                await browser.close()

                return {
                    "url": self.url,
                    "content": content,
                    "title": title,
                    "image_urls": image_urls,
                    "content_type": "html",
                }
        except ImportError:
            logger.warning("playwright 未安装, 降级为空内容")
            return {"url": self.url, "content": "", "title": "", "image_urls": []}
        except Exception as e:  # noqa: BLE001
            logger.warning("Playwright 抓取失败 %s: %s", self.url, e)
            return {"url": self.url, "content": "", "title": "", "image_urls": []}
