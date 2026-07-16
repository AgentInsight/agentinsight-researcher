"""Arxiv 抓取器 - 学术论文 (含全文).

适用于 arxiv.org URL, 直接获取论文摘要与全文.

httpx 流式下载 + 超时/重试 + tempfile 替代 /tmp.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Any

import httpx

from src.skills.researcher.scrapers import BaseScraper, get_ssl_context

logger = logging.getLogger(__name__)

# 超时与重试默认值
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 1.0  # 指数退避基数(秒)


async def _download_pdf_with_retry(
    pdf_url: str,
    dest_path: str,
    *,
    request_timeout: float = _DEFAULT_TIMEOUT,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> None:
    """流式下载 PDF 到指定路径 (httpx.AsyncClient + 指数退避重试)."""
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(
                timeout=request_timeout,
                follow_redirects=True,
                verify=get_ssl_context(),
            ) as client:
                async with client.stream("GET", pdf_url) as resp:
                    resp.raise_for_status()
                    with open(dest_path, "wb") as f:  # noqa: ASYNC230
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            f.write(chunk)
            return  # 下载成功
        except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException) as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = _RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                logger.debug(
                    "PDF 下载第 %d 次失败, %0.1fs 后重试: %s",
                    attempt,
                    wait,
                    exc,
                )
                await asyncio.sleep(wait)
    # 全部重试耗尽, 抛出最后一次异常
    raise last_exc  # type: ignore[misc]


class ArxivScraper(BaseScraper):
    """Arxiv 抓取器 (学术论文)."""

    name = "arxiv"

    async def scrape(self) -> dict[str, Any]:
        """抓取 Arxiv 论文."""
        try:
            import arxiv

            # 从 URL 提取论文 ID
            # https://arxiv.org/abs/2401.12345 → 2401.12345
            arxiv_id = self.url.split("/")[-1]
            if arxiv_id.endswith(".pdf"):
                arxiv_id = arxiv_id[:-4]

            def _fetch_metadata() -> dict[str, Any] | None:
                """同步获取论文元数据 (arxiv 库)."""
                client = arxiv.Client()
                search = arxiv.Search(id_list=[arxiv_id])
                results = list(client.results(search))
                if not results:
                    return None
                return {
                    "title": results[0].title,
                    "authors": [str(a) for a in results[0].authors],
                    "published": results[0].published.strftime("%Y-%m-%d"),
                    "summary": results[0].summary,
                    "pdf_url": results[0].pdf_url,
                }

            # 元数据获取放 to_thread (arxiv 库是同步的)
            paper_info = await asyncio.to_thread(_fetch_metadata)
            if paper_info is None:
                return {"url": self.url, "content": "", "title": "", "image_urls": []}

            content = (
                f"Title: {paper_info['title']}\n\n"
                f"Authors: {', '.join(paper_info['authors'])}\n\n"
                f"Published: {paper_info['published']}\n\n"
                f"Summary: {paper_info['summary']}\n\n"
            )

            # 尝试获取全文 (PDF 流式下载 + pypdf 提取)
            try:
                pdf_url = paper_info.get("pdf_url", "")
                if not pdf_url:
                    raise ValueError("无 PDF URL")

                tmp_dir = tempfile.gettempdir()
                pdf_path = os.path.join(tmp_dir, f"{arxiv_id}.pdf")

                await _download_pdf_with_retry(pdf_url, pdf_path)
                try:
                    full_text = await asyncio.to_thread(self._extract_pdf_text, pdf_path)
                    if full_text:
                        content += f"Full Content:\n{full_text}"
                finally:
                    if await asyncio.to_thread(os.path.exists, pdf_path):
                        await asyncio.to_thread(os.unlink, pdf_path)
            except Exception:  # noqa: BLE001
                pass  # 全文获取失败仅用摘要

            return {
                "url": self.url,
                "content": content,
                "title": paper_info["title"],
                "image_urls": [],
                "content_type": "arxiv",
            }
        except ImportError:
            logger.warning("arxiv 库未安装, 跳过 Arxiv 抓取")
            return {"url": self.url, "content": "", "title": "", "image_urls": []}
        except Exception as e:  # noqa: BLE001
            logger.warning("Arxiv 抓取失败 %s: %s", self.url, e)
            return {"url": self.url, "content": "", "title": "", "image_urls": []}

    @staticmethod
    def _extract_pdf_text(pdf_path: str) -> str:
        """从 PDF 文件提取全文 (同步, 供 asyncio.to_thread 包裹).

        使用 pypdf.PdfReader 逐页提取文本, 多页文本以 "\\n\\n" 拼接.
        """
        try:
            from pypdf import PdfReader

            reader = PdfReader(pdf_path)
            text_parts: list[str] = []
            for page in reader.pages:
                text_parts.append(page.extract_text() or "")
            return "\n\n".join(text_parts)
        except ImportError:
            logger.warning("pypdf 未安装, 无法提取 PDF 全文")
            return ""
        except Exception as e:  # noqa: BLE001
            logger.warning("PDF 全文提取失败 %s: %s", pdf_path, e)
            return ""
