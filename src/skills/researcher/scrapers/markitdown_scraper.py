"""MarkItDown 文档抓取器.

支持 DOCX/PPTX/XLSX 等 Office 文档转 Markdown.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Any
from urllib.parse import urlparse

import httpx

from src.skills.researcher.scrapers import BaseScraper

logger = logging.getLogger(__name__)


def _get_suffix(url: str) -> str:
    """从 URL 提取文件后缀."""
    path = urlparse(url).path.lower()
    for ext in (".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls"):
        if path.endswith(ext):
            return ext
    return ".docx"


class MarkItDownScraper(BaseScraper):
    """MarkItDown 文档抓取器 (DOCX/PPTX/XLSX 等)."""

    name = "markitdown"

    async def scrape(self) -> dict[str, Any]:
        """下载 Office 文档 → MarkItDown 转 Markdown."""
        try:
            from markitdown import MarkItDown
        except ImportError:
            logger.warning("markitdown 未安装, 无法抓取 Office 文档")
            return {"url": self.url, "content": None, "title": "", "image_urls": []}

        tmp_path: str | None = None
        try:
            # 下载文档到临时文件 (同步写入用 to_thread 包装避免 ASYNC230)
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                r = await client.get(self.url)
                r.raise_for_status()

                def _write_temp(content: bytes, suffix: str) -> str:
                    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                        f.write(content)
                        return f.name

                tmp_path = await asyncio.to_thread(_write_temp, r.content, _get_suffix(self.url))

            # MarkItDown 转换 (同步 CPU 密集, 用 to_thread 包装)
            def _convert(path: str) -> dict[str, Any]:
                md = MarkItDown()
                result = md.convert(path)
                return {
                    "url": self.url,
                    "content": result.text_content or "",
                    "title": result.title or "",
                    "image_urls": [],
                    "content_type": "document",
                }

            return await asyncio.to_thread(_convert, tmp_path)
        except Exception as e:  # noqa: BLE001
            logger.warning("MarkItDown 抓取失败 %s: %s", self.url, e)
            return {"url": self.url, "content": None, "title": "", "image_urls": []}
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
