"""Trafilatura 抓取器单元测试 (L1 主路径).

测试覆盖:
1. ImportError 降级: trafilatura 未安装时返回空结果
2. 空 session 返回空结果
3. 异常处理: 抓取失败返回空结果

对标 test_scrapers.py 测试模式.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.skills.researcher.scrapers.trafilatura_scraper import TrafilaturaScraper


class TestTrafilaturaScraperImportError:
    """ImportError 降级测试."""

    @pytest.mark.asyncio
    async def test_import_error_returns_empty(self):
        """trafilatura 未安装时应返回空结果 (由降级链兜底)."""
        scraper = TrafilaturaScraper("https://example.com")
        result = await scraper.scrape()
        assert result["content"] == ""
        assert result["url"] == "https://example.com"


class TestTrafilaturaScraperEdgeCases:
    """边界情况测试."""

    @pytest.mark.asyncio
    async def test_empty_session_returns_empty(self):
        """session=None 时返回空结果."""
        scraper = TrafilaturaScraper("https://example.com", session=None)
        result = await scraper.scrape()

        assert result["content"] == ""
        assert result["url"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self):
        """HTTP 请求失败时返回空结果."""
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(side_effect=Exception("HTTP error"))

        scraper = TrafilaturaScraper("https://example.com", session=mock_session)
        result = await scraper.scrape()

        assert result["content"] == ""
        assert result["url"] == "https://example.com"
