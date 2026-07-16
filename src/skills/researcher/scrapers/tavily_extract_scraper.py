"""Tavily Extract 抓取器 - 商用内容提取 API.

用 Tavily Extract API 作为内容抓取器, 输出已为 LLM 优化的纯文本
(raw_content), 适用于反爬严格或 JS 渲染重的页面.

实现差异:
- AIR 统一走 httpx.AsyncClient (异步实现).
- API Key 从 settings.tavily_api_key 注入 (与 TavilySearcher 复用同一 Key).
- 返回 AIR 统一 scrapers 规约: {"url","content","title","image_urls","content_type"}
  (原生 API 返回 raw_content + url + images, 由本类映射到 AIR 字段).

API 文档: https://docs.tavily.com/documentation/api-reference/endpoint/extract
- POST https://api.tavily.com/extract
- Body: {"api_key": "<key>", "urls": ["<url>"]}
- Resp: {"results": [{"url":"...", "raw_content":"...", "images":[...]}]}
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.settings import get_settings
from src.skills.researcher.scrapers import BaseScraper, get_ssl_context

logger = logging.getLogger(__name__)


class TavilyExtractScraper(BaseScraper):
    """Tavily Extract API 抓取器 (LLM 友好纯文本输出).

    需配置 TAVILY_API_KEY 才生效 (与 TavilySearcher 复用).
    """

    name = "tavily_extract"

    _api_url: str = "https://api.tavily.com/extract"

    def __init__(self, url: str, session: Any | None = None) -> None:
        super().__init__(url, session)
        settings = get_settings()
        self.api_key: str | None = settings.tavily_api_key

    async def scrape(self) -> dict[str, Any]:
        """通过 Tavily Extract API 提取网页内容.

        优先复用传入的 httpx session (与其他 scraper 共享连接池),
        session 为 None 时自建临时 client.
        """
        if not self.api_key:
            logger.warning("TavilyExtractScraper: tavily_api_key 未配置, 跳过")
            return {
                "url": self.url,
                "content": "",
                "title": "",
                "image_urls": [],
            }

        try:
            payload: dict[str, Any] = {
                "api_key": self.api_key,
                "urls": [self.url],
            }

            client_owner = False
            client: httpx.AsyncClient
            if isinstance(self.session, httpx.AsyncClient):
                client = self.session
            else:
                client = httpx.AsyncClient(timeout=60.0, verify=get_ssl_context())
                client_owner = True

            try:
                resp = await client.post(self._api_url, json=payload)
                resp.raise_for_status()
                data = resp.json()
            finally:
                if client_owner:
                    await client.aclose()

            return self._normalize_result(data)
        except Exception as e:  # noqa: BLE001
            logger.warning("Tavily Extract 抓取失败 %s: %s", self.url, e)
            return {
                "url": self.url,
                "content": "",
                "title": "",
                "image_urls": [],
            }

    def _normalize_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        """归一化 Tavily Extract 响应为统一 scrapers 规约.

        Tavily Extract 响应结构:
        {
            "results": [
                {"url":"...", "raw_content":"...", "images":["..."]}
            ],
            "failed_results": [{"url":"...", "error_code": "...", "error_message":"..."}]
        }
        """
        results = payload.get("results") or []
        if not results:
            failed = payload.get("failed_results") or []
            if failed:
                err = failed[0]
                logger.warning(
                    "Tavily Extract 失败 %s: %s",
                    err.get("url", self.url),
                    err.get("error_message", "unknown"),
                )
            return {
                "url": self.url,
                "content": "",
                "title": "",
                "image_urls": [],
            }

        item = results[0]
        content = item.get("raw_content") or ""
        image_urls: list[str] = list(item.get("images") or [])

        # Tavily Extract 不直接返回 title, 从 raw_content 首行启发式提取 (可选)
        title = ""
        if content:
            first_line = content.lstrip().split("\n", 1)[0]
            if first_line and len(first_line) <= 200:
                title = first_line.lstrip("# ").strip()

        return {
            "url": self.url,
            "content": content,
            "title": title,
            "image_urls": image_urls[:4],
            "content_type": "text",
        }
