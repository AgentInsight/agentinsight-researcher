"""Trafilatura 抓取器 (L1 主路径).

轻量级网页内容提取工具, 输出 LLM 友好的 Markdown.
对比 Crawl4AI: 无需浏览器渲染, 依赖更少, 速度更快.

降级链:
1. Trafilatura (LLM 友好 Markdown, 轻量级) ← 本抓取器
2. BS + markdownify (HTML→Markdown, 纯本地)
3. Playwright (JS 渲染兜底, 输出 HTML)

抓取栈未列入硬选型, 属 "Ask first" 范畴.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.skills.researcher.scrapers import BaseScraper

logger = logging.getLogger(__name__)


class TrafilaturaScraper(BaseScraper):
    """Trafilatura 抓取器 (L1 主路径).

    Trafilatura 是轻量级网页内容提取工具, 专门设计用于提取文章内容,
    去噪能力强, 输出质量接近 Crawl4AI 但无需浏览器依赖.

    用法:
        scraper = TrafilaturaScraper(url, session)
        result = await scraper.scrape()  # {"content": markdown, "content_type": "markdown"}
    """

    name = "trafilatura"

    def __init__(self, url: str, session: Any | None = None) -> None:
        super().__init__(url, session)

    async def scrape(self) -> dict[str, Any]:
        """抓取网页 (Trafilatura 提取 + 去噪)."""
        if self.session is None:
            return {"url": self.url, "content": "", "title": "", "image_urls": []}

        try:
            import trafilatura

            downloaded = await self.session.get(self.url, timeout=15.0)
            downloaded.raise_for_status()
            # P1-16: 用 bytes 截断避免全量 str 解码 (节省内存峰值)
            max_html_size = 5 * 1024 * 1024
            raw = downloaded.content  # bytes, 不触发解码
            if len(raw) > max_html_size:
                logger.warning(
                    "HTML 内容过大 (%.2fMB), 截断至 5MB: %s",
                    len(raw) / (1024 * 1024),
                    self.url,
                )
                raw = raw[:max_html_size]
            html = raw.decode(downloaded.encoding or "utf-8", errors="replace")

            result = trafilatura.extract(
                html,
                url=self.url,
                include_links=True,
                include_images=True,
                include_tables=True,
                favor_precision=True,
                output_format="markdown",
            )

            if not result:
                logger.debug("Trafilatura 未提取到内容: %s", self.url)
                return {"url": self.url, "content": "", "title": "", "image_urls": []}

            title = ""
            try:
                import trafilatura.metadata as tm

                metadata = tm.extract_metadata(html)
                if metadata and metadata.title:
                    title = metadata.title
            except Exception:
                pass

            # v3: 按尺寸/class 评分排序, 取 Top-4
            # trafilatura.extractors.extract_images 仅返回 URL 列表无尺寸信息,
            # 改用 get_relevant_images_from_html 从 HTML 评分排序.
            from src.skills.researcher.scrapers.utils import (
                get_relevant_images_from_html,
            )

            image_urls = get_relevant_images_from_html(html, self.url, top_k=4)

            while "\n\n\n" in result:
                result = result.replace("\n\n\n", "\n\n")

            return {
                "url": self.url,
                "content": result,
                "title": title,
                "image_urls": image_urls,
                "content_type": "markdown",
            }

        except ImportError:
            logger.info("trafilatura 未安装, 降级链兜底: %s", self.url)
            return {"url": self.url, "content": "", "title": "", "image_urls": []}
        except httpx.HTTPStatusError as e:
            # 403/401/429 快速失败: 服务器层拒绝, 降级到 BS/Playwright 也无法成功,
            # 仅徒增内存 (BS DOM 5-10x / Playwright chromium ~400MB).
            # 将状态码写入 _http_status, 供降级链 _is_fast_fail() 检测后终止.
            status_code = e.response.status_code
            logger.warning("Trafilatura HTTP %d (快速失败, 不降级): %s", status_code, self.url)
            return {
                "url": self.url,
                "content": "",
                "title": "",
                "image_urls": [],
                "_http_status": status_code,
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("Trafilatura 抓取失败 %s: %s", self.url, e)
            return {"url": self.url, "content": "", "title": "", "image_urls": []}
