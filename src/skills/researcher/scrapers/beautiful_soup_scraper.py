"""BeautifulSoup 抓取器 - 默认主力.

轻量, 速度快, 适用于大多数静态网页.
"""

from __future__ import annotations

import logging
from typing import Any

from src.skills.researcher.scrapers import BaseScraper

logger = logging.getLogger(__name__)


class BeautifulSoupScraper(BaseScraper):
    """BeautifulSoup 抓取器."""

    name = "bs"

    async def scrape(self) -> dict[str, Any]:
        """抓取网页内容."""
        try:
            from bs4 import BeautifulSoup

            # 异步抓取
            async def _async_scrape() -> dict[str, Any]:
                if self.session is None:
                    return {"url": self.url, "content": "", "title": "", "image_urls": []}

                response = await self.session.get(self.url, timeout=15.0)
                response.raise_for_status()
                # P1-16: 用 bytes 截断避免全量 str 解码 (节省内存峰值)
                max_html_size = 5 * 1024 * 1024  # 5MB
                raw = response.content  # bytes, 不触发解码
                if len(raw) > max_html_size:
                    logger.warning(
                        "HTML 内容过大 (%.2fMB), 截断至 5MB: %s",
                        len(raw) / (1024 * 1024),
                        self.url,
                    )
                    raw = raw[:max_html_size]
                html = raw.decode(response.encoding or "utf-8", errors="replace")

                # str (Unicode) 输入时不应传 from_encoding, 否则 bs4 发出 UserWarning
                soup = BeautifulSoup(html, "lxml")

                # 提取标题
                title = ""
                if soup.title:
                    title = soup.title.string or ""

                # 清理脚本/样式
                for tag in soup(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()

                # 提取正文
                content = soup.get_text(separator="\n", strip=True)

                # 提取图片
                image_urls: list[str] = []
                for img in soup.find_all("img", limit=20):
                    src = img.get("src") or img.get("data-src")
                    if isinstance(src, str) and src.startswith("http"):
                        image_urls.append(src)

                return {
                    "url": self.url,
                    "content": content,
                    "title": title,
                    "image_urls": image_urls[:4],  # top 4
                    "content_type": "html",
                }

            return await _async_scrape()
        except Exception as e:  # noqa: BLE001
            logger.warning("BeautifulSoup 抓取失败 %s: %s", self.url, e)
            return {"url": self.url, "content": "", "title": "", "image_urls": []}
