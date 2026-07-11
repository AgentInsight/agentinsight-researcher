"""BeautifulSoup + markdownify 抓取器 (L1 降级链 L2).

Trafilatura 降级后的第二级抓取器, 输出 LLM 友好 Markdown 而非纯文本.
遵循 firecrawl_scraper.py 的 Markdown 输出规约, 但完全本地化 (零 API 调用).

优势 (对比原 BeautifulSoupScraper):
- 输出 Markdown (保留标题/列表/链接结构, BS 输出纯文本丢失结构)
- 复用 BeautifulSoup + lxml 解析 (零新依赖, markdownify 已在 requirements.txt)
- 纯本地计算, 零网络调用 (对比 Firecrawl 付费 SaaS)

降级链 (L1):
1. Trafilatura (LLM 友好 Markdown, 轻量级去噪)
2. BS + markdownify (HTML→Markdown, 纯本地) ← 本抓取器
3. Playwright (JS 渲染兜底, 输出 HTML)

AGENTS.md 第 4 章: 抓取栈未列入硬选型, 属 "Ask first" 范畴.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.skills.researcher.scrapers import BaseScraper

logger = logging.getLogger(__name__)


class BSMarkdownifyScraper(BaseScraper):
    """BeautifulSoup + markdownify 抓取器 (L1 降级链 L2).

    用法:
        scraper = BSMarkdownifyScraper(url, session)
        result = await scraper.scrape()  # {"content": markdown, "content_type": "markdown"}
    """

    name = "bs_markdownify"

    async def scrape(self) -> dict[str, Any]:
        """抓取网页 (BS 解析 + markdownify 转 Markdown)."""

        if self.session is None:
            return {"url": self.url, "content": "", "title": "", "image_urls": []}

        try:
            from bs4 import BeautifulSoup
            from markdownify import markdownify as md

            # 复用 BeautifulSoupScraper 的 HTTP 抓取 + 清理逻辑
            response = await self.session.get(self.url, timeout=15.0)
            response.raise_for_status()
            html = response.text

            # HTML 大小上限检查 (参考 BeautifulSoupScraper, 5MB)
            max_html_size = 5 * 1024 * 1024
            if len(html) > max_html_size:
                logger.warning(
                    "HTML 内容过大 (%.2fMB), 截断至 5MB: %s",
                    len(html) / (1024 * 1024),
                    self.url,
                )
                html = html[:max_html_size]

            # str (Unicode) 输入时不应传 from_encoding, 否则 bs4 发出 UserWarning
            # 仅当 html 为 bytes 时 from_encoding 才有意义 (本路径 html 已是 str)
            soup = BeautifulSoup(html, "lxml")

            # 提取标题
            title = ""
            if soup.title:
                title = soup.title.string or ""

            # 清理脚本/样式/导航/页脚/页眉 (参考 BeautifulSoupScraper)
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()

            # 提取正文 HTML (优先 article/main/body)
            content_html = ""
            for selector in ["article", "main", "body"]:
                element = soup.find(selector)
                if element:
                    content_html = str(element)
                    break

            if not content_html:
                content_html = str(soup)

            # HTML → Markdown 转换 (markdownify 核心能力)
            markdown = md(
                content_html,
                heading_style="ATX",  # # 风格标题
                bullets="-",  # 列表符号
                strip=["img"],  # 暂不保留图片 (避免 Markdown 图片噪声)
            )

            # 清理多余空行 (markdownify 可能产生连续空行)
            lines = [line.rstrip() for line in markdown.split("\n")]
            markdown = "\n".join(lines)
            # 合并连续 3+ 空行为 2 空行
            while "\n\n\n" in markdown:
                markdown = markdown.replace("\n\n\n", "\n\n")

            # 提取图片 (v3: 按尺寸/class 评分排序, 取 Top-4)
            from src.skills.researcher.scrapers.utils import (
                get_relevant_images_from_soup,
            )

            image_urls = get_relevant_images_from_soup(soup, self.url, top_k=4)

            # 显式释放大对象 (省 500MB-1GB):
            # - html: 原始 HTML 字符串 (上限 5MB)
            # - soup: BeautifulSoup DOM 树 (5-10x HTML, 可达 50MB)
            # - content_html: 正文 HTML 字符串 (article/main/body 的 str 表示)
            # markdown 已是最终产物, 保留返回; image_urls/title 为小对象, 无需 del.
            # CPython 引用计数: del 立即触发析构, 无需等 GC; 此处变量均已定义, 无 NameError 风险.
            del html, soup, content_html

            return {
                "url": self.url,
                "content": markdown,
                "title": title,
                "image_urls": image_urls[:4],
                "content_type": "markdown",
            }

        except ImportError:
            logger.info("markdownify 未安装, 降级链兜底: %s", self.url)
            return {"url": self.url, "content": "", "title": "", "image_urls": []}
        except httpx.HTTPStatusError as e:
            # 403/401/429 快速失败: 服务器层拒绝, 降级到 Playwright 也无法成功,
            # 仅徒增内存 (Playwright chromium ~400MB).
            # 将状态码写入 _http_status, 供降级链 _is_fast_fail() 检测后终止.
            status_code = e.response.status_code
            logger.warning("BS+markdownify HTTP %d (快速失败, 不降级): %s", status_code, self.url)
            return {
                "url": self.url,
                "content": "",
                "title": "",
                "image_urls": [],
                "_http_status": status_code,
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("BS+markdownify 抓取失败 %s: %s", self.url, e)
            return {"url": self.url, "content": "", "title": "", "image_urls": []}
