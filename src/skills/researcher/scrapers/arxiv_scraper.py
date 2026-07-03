"""Arxiv 抓取器 - 学术论文 (含全文).

对标 GPT Researcher scraper/arxiv/arxiv.py.
适用于 arxiv.org URL, 直接获取论文摘要与全文.
"""

from __future__ import annotations

import logging
from typing import Any

from src.skills.researcher.scrapers import BaseScraper

logger = logging.getLogger(__name__)


class ArxivScraper(BaseScraper):
    """Arxiv 抓取器 (学术论文)."""

    name = "arxiv"

    async def scrape(self) -> dict[str, Any]:
        """抓取 Arxiv 论文."""
        try:
            import asyncio

            import arxiv

            # 从 URL 提取论文 ID
            # https://arxiv.org/abs/2401.12345 → 2401.12345
            arxiv_id = self.url.split("/")[-1]
            if arxiv_id.endswith(".pdf"):
                arxiv_id = arxiv_id[:-4]

            def _sync_scrape() -> dict[str, Any]:
                client = arxiv.Client()
                search = arxiv.Search(id_list=[arxiv_id])
                results = list(client.results(search))
                if not results:
                    return {"url": self.url, "content": "", "title": "", "image_urls": []}

                paper = results[0]
                # APA 风格格式化
                content = (
                    f"Title: {paper.title}\n\n"
                    f"Authors: {', '.join(str(a) for a in paper.authors)}\n\n"
                    f"Published: {paper.published.strftime('%Y-%m-%d')}\n\n"
                    f"Summary: {paper.summary}\n\n"
                )

                # 尝试获取全文 (PDF 下载)
                try:
                    pdf_path = paper.download_pdf(dirpath="/tmp", filename=f"{arxiv_id}.pdf")
                    # 用 PyMuPDF 提取全文
                    import fitz

                    doc = fitz.open(pdf_path)
                    full_text = "\n\n".join(page.get_text() for page in doc)
                    doc.close()
                    content += f"Full Content:\n{full_text}"
                except Exception:  # noqa: BLE001
                    pass  # 全文获取失败仅用摘要

                return {
                    "url": self.url,
                    "content": content,
                    "title": paper.title,
                    "image_urls": [],
                    "content_type": "arxiv",
                }

            return await asyncio.to_thread(_sync_scrape)
        except ImportError:
            logger.warning("arxiv 库未安装, 跳过 Arxiv 抓取")
            return {"url": self.url, "content": "", "title": "", "image_urls": []}
        except Exception as e:  # noqa: BLE001
            logger.warning("Arxiv 抓取失败 %s: %s", self.url, e)
            return {"url": self.url, "content": "", "title": "", "image_urls": []}
