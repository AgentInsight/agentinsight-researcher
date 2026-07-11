"""Firecrawl 抓取器.

Firecrawl 是商业 LLM 友好抓取服务, 输出已为 LLM 优化的 Markdown/HTML.

依赖说明:
- firecrawl-py: 不在 requirements.txt, 需手动 pip install firecrawl-py.
  缺失时降级为直接 HTTP API 调用 (需 httpx, 已在 requirements.txt).
- API Key 从 settings.firecrawl_api_key 注入 (AGENTS.md 第 4/11 章: 禁硬编码).
- API URL 从 settings.firecrawl_api_url 注入 (默认 https://api.firecrawl.dev).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from src.config.settings import get_settings
from src.skills.researcher.scrapers import BaseScraper

logger = logging.getLogger(__name__)


class FirecrawlScraper(BaseScraper):
    """Firecrawl 抓取器 (LLM 友好 Markdown 输出).

    优先使用 firecrawl-py SDK, 未安装时降级走 HTTP API.
    需配置 FIRECRAWL_API_KEY 才生效.
    """

    name = "firecrawl"

    def __init__(self, url: str, session: Any | None = None) -> None:
        super().__init__(url, session)
        settings = get_settings()
        self.api_key: str | None = settings.firecrawl_api_key
        self.api_url: str = settings.firecrawl_api_url

    async def scrape(self) -> dict[str, Any]:
        """抓取网页 (走 Firecrawl 服务)."""
        if not self.api_key:
            logger.warning("FirecrawlScraper: firecrawl_api_key 未配置, 跳过")
            return {"url": self.url, "content": "", "title": "", "image_urls": []}

        # 优先用 firecrawl-py SDK
        try:
            return await self._scrape_with_sdk()
        except ImportError:
            logger.info("firecrawl-py 未安装, 降级走 HTTP API: %s", self.url)
        except Exception as e:  # noqa: BLE001
            logger.warning("Firecrawl SDK 抓取失败, 降级 HTTP API %s: %s", self.url, e)

        # 降级: 直接 HTTP API
        try:
            return await self._scrape_with_http()
        except Exception as e:  # noqa: BLE001
            logger.warning("Firecrawl HTTP API 抓取失败 %s: %s", self.url, e)
            return {"url": self.url, "content": "", "title": "", "image_urls": []}

    async def _scrape_with_sdk(self) -> dict[str, Any]:
        """使用 firecrawl-py SDK 抓取 (异步)."""
        from firecrawl import FirecrawlApp

        app = FirecrawlApp(api_key=self.api_key, api_url=self.api_url)

        # SDK 的 scrape_url 是同步接口, 用 asyncio.to_thread 包装
        def _sync_scrape() -> dict[str, Any]:
            result = app.scrape_url(
                self.url,
                params={"formats": ["markdown"]},
            )
            return self._normalize_result(result)

        return await asyncio.to_thread(_sync_scrape)

    async def _scrape_with_http(self) -> dict[str, Any]:
        """直接调用 Firecrawl REST API (POST /v1/scrape)."""
        async with httpx.AsyncClient(
            timeout=60.0,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        ) as client:
            resp = await client.post(
                f"{self.api_url.rstrip('/')}/v1/scrape",
                json={"url": self.url, "formats": ["markdown"]},
            )
            resp.raise_for_status()
            payload = resp.json()
            return self._normalize_result(payload)

    def _normalize_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        """归一化 Firecrawl 响应为统一 scrapers 规约.

        Firecrawl v1 响应结构:
        {
            "success": true,
            "data": {
                "markdown": "...",
                "html": "...",
                "metadata": {"title": "...", "sourceURL": "...", ...}
            }
        }
        """
        data = payload.get("data") or {}
        content = data.get("markdown") or data.get("html") or ""
        metadata = data.get("metadata") or {}
        title = metadata.get("title", "") or ""
        # Firecrawl metadata.ogImages / images 字段优先, 否则空
        image_urls: list[str] = list(metadata.get("ogImages") or metadata.get("images") or [])

        return {
            "url": self.url,
            "content": content,
            "title": title,
            "image_urls": image_urls[:4],
            "content_type": "markdown",
        }
