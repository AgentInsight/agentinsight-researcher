"""nodriver 抓取器 - 反反爬无头浏览器 (P1-Future-08).

对标 GPT Researcher scraper/browser/nodriver_scraper.py (260 行).
nodriver 是 undetected-chromedriver 的继任者, 通过 CDP 直连 Chrome,
无需 chromedriver, 可绕过 Cloudflare/Datadome/PerimeterX 等反爬.

依赖说明:
- nodriver: 不在 requirements.txt, 需手动 pip install nodriver.
  缺失时返回空结果并告警, 由调用方走降级链.
- 本地需安装 Chrome / Chromium 浏览器 (镜像内预装或宿主机提供).
- 默认 settings.nodriver_enabled=False, 需手动启用避免误用.

AGENTS.md 第 9 章: 工具调用必须经 trace_xxx span 包裹; 本抓取器属 scrapers,
由调用方 (scrape_urls) 在 chain span 内统一包裹, 不强制 trace_tool.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from src.config.settings import get_settings
from src.skills.researcher.scrapers import BaseScraper

logger = logging.getLogger(__name__)


class NodriverScraper(BaseScraper):
    """nodriver 抓取器 (无头 Chrome + 反反爬).

    适用于:
    - Cloudflare/Datadome 等反爬保护的页面
    - JS 渲染的 SPA 页面 (Playwright 抓不到时降级用)
    - 滚动懒加载内容

    不适用于:
    - 静态页面 (用 BeautifulSoup 更轻量)
    - PDF/Office 文档 (用专用 scraper)
    """

    name = "nodriver"

    # 默认等待页面加载超时 (秒)
    DEFAULT_TIMEOUT: float = 30.0
    # 滚动到底部后的等待时长 (秒, 触发懒加载)
    SCROLL_WAIT: float = 2.0
    # 提取图片的最大数量 (对标其他 scraper 的 top 4)
    MAX_IMAGES: int = 4

    def __init__(
        self,
        url: str,
        session: Any | None = None,
        *,
        wait_for: str | None = None,
        timeout: float | None = None,
    ) -> None:
        super().__init__(url, session)
        self.wait_for = wait_for  # CSS 选择器, 等待该元素出现
        self.timeout = timeout or self.DEFAULT_TIMEOUT

    async def scrape(self) -> dict[str, Any]:
        """用 nodriver 启动 Chrome 抓取页面."""
        settings = get_settings()
        if not settings.nodriver_enabled:
            logger.debug("NodriverScraper: nodriver_enabled=False, 跳过")
            return {"url": self.url, "content": "", "title": "", "image_urls": []}

        try:
            import nodriver
        except ImportError:
            logger.warning(
                "nodriver 未安装, NodriverScraper 不可用; 请 pip install nodriver 并预装 Chrome"
            )
            return {"url": self.url, "content": "", "title": "", "image_urls": []}

        try:
            return await self._scrape_with_browser(nodriver)
        except Exception as e:  # noqa: BLE001
            logger.warning("nodriver 抓取失败 %s: %s", self.url, e)
            return {"url": self.url, "content": "", "title": "", "image_urls": []}

    async def _scrape_with_browser(self, nodriver: Any) -> dict[str, Any]:
        """启动浏览器执行抓取.

        Args:
            nodriver: 已 import 的 nodriver 模块 (避免重复 import).
        """
        browser = await nodriver.start(
            headless=True,
            browser_args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )

        try:
            tab = await browser.get(self.url)

            # 等待页面加载 (nodriver 自带 networkidle 语义)
            await asyncio.sleep(self.SCROLL_WAIT)

            # 等待指定元素 (如配置 wait_for)
            if self.wait_for:
                try:
                    await tab.wait_for_selector(self.wait_for, timeout=self.timeout)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "nodriver 等待元素 %s 超时 %s: %s",
                        self.wait_for,
                        self.url,
                        e,
                    )

            # 滚动到底部触发懒加载 (对标 Playwright 行为)
            await tab.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(self.SCROLL_WAIT)

            # 提取内容 (对标 Playwright: inner_text("body"))
            content = await self._extract_text(tab)
            title = await self._extract_title(tab)
            image_urls = await self._extract_images(tab)

            return {
                "url": self.url,
                "content": content,
                "title": title,
                "image_urls": image_urls,
                "content_type": "html",
            }
        finally:
            try:
                await browser.close()
            except Exception as e:  # noqa: BLE001
                logger.debug("nodriver browser.close 异常 (忽略): %s", e)

    async def _extract_text(self, tab: Any) -> str:
        """提取页面正文文本."""
        try:
            # nodriver tab.evaluate 返回 JS Promise 的 resolved 值
            return cast(
                "str",
                await tab.evaluate(
                    "document.body.innerText",
                    return_by_value=True,
                ),
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("nodriver 提取 innerText 失败, 尝试 textContent: %s", e)
            try:
                return cast(
                    "str",
                    await tab.evaluate(
                        "document.body.textContent",
                        return_by_value=True,
                    ),
                )
            except Exception as e2:  # noqa: BLE001
                logger.warning("nodriver 提取文本完全失败: %s", e2)
                return ""

    async def _extract_title(self, tab: Any) -> str:
        """提取页面标题."""
        try:
            return cast(
                "str",
                await tab.evaluate(
                    "document.title",
                    return_by_value=True,
                ),
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("nodriver 提取 title 失败: %s", e)
            return ""

    async def _extract_images(self, tab: Any) -> list[str]:
        """提取页面图片 URL (top N)."""
        try:
            raw = await tab.evaluate(
                """
                () => Array.from(document.querySelectorAll('img'))
                    .map(img => img.src || img.dataset.src)
                    .filter(src => src && src.startsWith('http'))
                """,
                return_by_value=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("nodriver 提取图片失败: %s", e)
            return []

        if not isinstance(raw, list):
            return []
        # 过滤非字符串元素
        return [str(u) for u in raw if isinstance(u, str)][: self.MAX_IMAGES]
