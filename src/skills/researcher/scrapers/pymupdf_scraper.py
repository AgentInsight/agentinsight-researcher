"""PyMuPDF 抓取器 - PDF 文档.

对标 GPT Researcher scraper/pymupdf/pymupdf.py.
适用于 PDF URL 与本地路径.

P2-10 修复: 所有 fitz 同步调用经 asyncio.to_thread; 流式下载避免 OOM;
同步文件写入/删除经 asyncio.to_thread 包裹.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

from src.skills.researcher.scrapers import BaseScraper

logger = logging.getLogger(__name__)

# 流式下载 chunk 大小
_CHUNK_SIZE = 65536
# 下载超时(秒)
_DOWNLOAD_TIMEOUT = 30.0


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

        # 判断是 URL 还是本地路径
        if self.url.startswith("http"):
            # URL: 流式下载到临时文件, 避免 OOM
            if self.session is None:
                return ""

            # 同步创建临时文件 (打开操作极短, 可接受)
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                temp_path = f.name

            try:
                await self._stream_download_to_file(self.session, self.url, temp_path)
                return await asyncio.to_thread(self._extract_from_file, temp_path)
            finally:
                # 同步删除经 to_thread 包裹
                if await asyncio.to_thread(os.path.exists, temp_path):
                    await asyncio.to_thread(os.unlink, temp_path)
        else:
            # 本地路径
            def _local_exists() -> bool:
                return os.path.exists(self.url)

            if not await asyncio.to_thread(_local_exists):
                return ""
            return await asyncio.to_thread(self._extract_from_file, self.url)

    @staticmethod
    async def _stream_download_to_file(
        session: Any,
        url: str,
        dest_path: str,
    ) -> None:
        """流式下载 PDF 到文件 (chunk write 用 asyncio.to_thread 包裹).

        用 httpx.AsyncClient.stream 流式接收, 避免 response.content 一次性读入
        内存导致大文件 OOM; 每个 chunk 的同步 f.write 经 asyncio.to_thread
        包裹, 不阻塞事件循环.
        """
        import asyncio

        with open(dest_path, "wb") as f:  # noqa: ASYNC230  open() 极短可接受
            async with session.stream(
                "GET", url, timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=_CHUNK_SIZE):
                    # 同步 f.write 经 to_thread 包裹, 避免阻塞事件循环
                    await asyncio.to_thread(f.write, chunk)

    @staticmethod
    def _extract_from_file(file_path: str) -> str:
        """从本地 PDF 文件提取文本 (同步, 用 asyncio.to_thread 包装).

        PyMuPDF (fitz.Document) 不是线程安全, 整个解析逻辑作为单一同步
        函数在线程中执行, 避免跨线程共享 Document 对象.
        """
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
