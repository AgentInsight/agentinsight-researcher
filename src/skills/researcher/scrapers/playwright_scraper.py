"""Playwright 抓取器 - JS 渲染页面.

对标 GPT Researcher scraper/browser/browser.py (但用 Playwright 替代 Selenium).
适用于 JS 渲染的 SPA 页面.
"""

from __future__ import annotations

import logging
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

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
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
