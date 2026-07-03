"""PyMuPDF 抓取器 - PDF 文档.

对标 GPT Researcher scraper/pymupdf/pymupdf.py.
适用于 PDF URL 与本地路径.
"""

from __future__ import annotations

import logging
import tempfile
from typing import Any

from src.skills.researcher.scrapers import BaseScraper

logger = logging.getLogger(__name__)


class PyMuPDFScraper(BaseScraper):
    """PyMuPDF 抓取器 (PDF)."""

    name = "pdf"

    async def scrape(self) -> dict[str, Any]:
        """抓取 PDF 内容."""
        try:
            content = await self._extract_pdf_content()
            return {
                "url": self.url,
                "content": content,
                "title": "",  # PDF 标题需额外提取
                "image_urls": [],
                "content_type": "pdf",
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("PyMuPDF 抓取失败 %s: %s", self.url, e)
            return {"url": self.url, "content": "", "title": "", "image_urls": []}

    async def _extract_pdf_content(self) -> str:
        """提取 PDF 文本内容."""
        import asyncio
        import os

        # 判断是 URL 还是本地路径
        if self.url.startswith("http"):
            # URL: 下载到临时文件
            if self.session is None:
                return ""
            response = await self.session.get(self.url, timeout=30.0)
            response.raise_for_status()

            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(response.content)
                temp_path = f.name

            try:
                return await asyncio.to_thread(self._extract_from_file, temp_path)
            finally:
                os.unlink(temp_path)
        else:
            # 本地路径
            def _local_exists() -> bool:
                return os.path.exists(self.url)

            if not await asyncio.to_thread(_local_exists):
                return ""
            return await asyncio.to_thread(self._extract_from_file, self.url)

    @staticmethod
    def _extract_from_file(file_path: str) -> str:
        """从本地 PDF 文件提取文本 (同步, 用 asyncio.to_thread 包装)."""
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(file_path)
            text_parts: list[str] = []
            for page in doc:
                text_parts.append(page.get_text())
            doc.close()
            return "\n\n".join(text_parts)
        except ImportError:
            logger.warning("PyMuPDF (fitz) 未安装, 无法解析 PDF")
            return ""
        except Exception as e:  # noqa: BLE001
            logger.warning("PDF 解析失败 %s: %s", file_path, e)
            return ""
