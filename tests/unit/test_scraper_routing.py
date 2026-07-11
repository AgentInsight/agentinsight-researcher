"""单元测试: scrape_with_fallback 抓取路由分支.

验证 src/skills/researcher/scrapers/__init__.py 的 scrape_with_fallback 函数路由:
- Trafilatura → BS+markdownify → Playwright 降级链
- PDF / Arxiv 专用抓取器不降级
- scraper_mode 直接路径 (playwright)
- enable_fallback / min_content_length 边界控制
- best_content 回退 (三级全部内容不足时取最长)

单元测试在构建期执行, 不依赖外部服务.
所有 scraper 类与 get_settings 全部 mock, 不实际网络请求.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.config.settings import Settings

pytestmark = pytest.mark.unit


# ========== Helpers ==========


def _make_scraper_class_mock(
    *,
    scrape_return: dict | None = None,
    scrape_side_effect: Exception | None = None,
) -> MagicMock:
    """构造 scraper 类 mock, 实例化后 scrape() 返回指定结果或抛异常.

    scrape_return 与 scrape_side_effect 互斥; 同时给出时 side_effect 优先.
    """

    class _MockInstance:
        def __init__(self, url: str = "", session: object | None = None, *args, **kwargs) -> None:
            self.url = url
            self.session = session

        async def scrape(self) -> dict:
            if scrape_side_effect is not None:
                raise scrape_side_effect
            return scrape_return or {}

    cls_mock = MagicMock()
    cls_mock.side_effect = _MockInstance
    return cls_mock


def _make_settings(
    *,
    scraper_mode: str = "auto",
) -> Settings:
    """构造测试 Settings (跳过 .env 加载)."""
    s = Settings(_env_file=None)
    s.scraper_mode = scraper_mode
    return s


# ========== TestScraperRouting: 路由分支测试 ==========


class TestScraperRouting:
    """验证 scrape_with_fallback 的路由分支与降级控制."""

    async def test_tf_success_no_fallback(self) -> None:
        """Trafilatura 成功返回足够内容 → 不触发 BS/Playwright 降级."""
        from src.skills.researcher.scrapers import scrape_with_fallback

        tf_content = "x" * 500
        tf_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/page",
                "content": tf_content,
                "title": "TF Title",
                "image_urls": [],
            }
        )
        bsm_mock = _make_scraper_class_mock(scrape_return={"content": "should-not-reach"})
        pw_mock = _make_scraper_class_mock(scrape_return={"content": "should-not-reach"})

        custom_settings = _make_settings(scraper_mode="auto")

        with (
            patch(
                "src.skills.researcher.scrapers.get_settings",
                return_value=custom_settings,
            ),
            patch(
                "src.skills.researcher.scrapers.trafilatura_scraper.TrafilaturaScraper",
                tf_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.bs_markdownify_scraper.BSMarkdownifyScraper",
                bsm_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.playwright_scraper.PlaywrightScraper",
                pw_mock,
            ),
        ):
            result = await scrape_with_fallback(
                "https://example.com/page",
                enable_fallback=True,
                min_content_length=100,
            )

        assert tf_mock.called, "Trafilatura 应作为首路径被实例化"
        assert not bsm_mock.called, "Trafilatura 成功后不应触发 BS 降级"
        assert not pw_mock.called, "Trafilatura 成功后不应触发 Playwright 降级"
        assert result.get("content") == tf_content

    async def test_tf_fallback_to_bs_markdownify(self) -> None:
        """Trafilatura 内容过短 → 降级 BS+markdownify 成功."""
        from src.skills.researcher.scrapers import scrape_with_fallback

        tf_mock = _make_scraper_class_mock(
            scrape_return={"content": "short", "title": "", "image_urls": []}
        )
        bsm_content = "y" * 500
        bsm_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/page",
                "content": bsm_content,
                "title": "BS Title",
                "image_urls": [],
            }
        )
        pw_mock = _make_scraper_class_mock(scrape_return={"content": "should-not-reach"})

        custom_settings = _make_settings(scraper_mode="auto")

        with (
            patch(
                "src.skills.researcher.scrapers.get_settings",
                return_value=custom_settings,
            ),
            patch(
                "src.skills.researcher.scrapers.trafilatura_scraper.TrafilaturaScraper",
                tf_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.bs_markdownify_scraper.BSMarkdownifyScraper",
                bsm_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.playwright_scraper.PlaywrightScraper",
                pw_mock,
            ),
        ):
            result = await scrape_with_fallback(
                "https://example.com/page",
                enable_fallback=True,
                min_content_length=100,
            )

        assert tf_mock.called, "Trafilatura 应作为首路径被实例化"
        assert bsm_mock.called, "Trafilatura 内容过短后应降级到 BS+markdownify"
        assert not pw_mock.called, "BS+markdownify 成功后不应触发 Playwright"
        assert result.get("content") == bsm_content

    async def test_tf_and_bs_fallback_to_playwright(self) -> None:
        """Trafilatura 内容过短 → BS+markdownify 内容过短 → Playwright 兜底成功."""
        from src.skills.researcher.scrapers import scrape_with_fallback

        tf_mock = _make_scraper_class_mock(
            scrape_return={"content": "short", "title": "", "image_urls": []}
        )
        bsm_mock = _make_scraper_class_mock(
            scrape_return={"content": "short", "title": "", "image_urls": []}
        )
        pw_content = "z" * 500
        pw_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/page",
                "content": pw_content,
                "title": "PW Title",
                "image_urls": [],
            }
        )

        custom_settings = _make_settings(scraper_mode="auto")

        with (
            patch(
                "src.skills.researcher.scrapers.get_settings",
                return_value=custom_settings,
            ),
            patch(
                "src.skills.researcher.scrapers.trafilatura_scraper.TrafilaturaScraper",
                tf_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.bs_markdownify_scraper.BSMarkdownifyScraper",
                bsm_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.playwright_scraper.PlaywrightScraper",
                pw_mock,
            ),
        ):
            result = await scrape_with_fallback(
                "https://example.com/page",
                enable_fallback=True,
                min_content_length=100,
            )

        assert tf_mock.called
        assert bsm_mock.called
        assert pw_mock.called, "BS+markdownify 内容过短后应降级到 Playwright"
        assert result.get("content") == pw_content

    async def test_pdf_url_no_fallback(self) -> None:
        """PDF URL 直接走 PyMuPDFScraper 不降级."""
        from src.skills.researcher.scrapers import scrape_with_fallback

        pdf_content = "PDF text content " * 20
        pdf_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/doc.pdf",
                "content": pdf_content,
                "title": "PDF Doc",
                "image_urls": [],
            }
        )
        tf_mock = _make_scraper_class_mock(scrape_return={"content": "should-not-reach"})

        custom_settings = _make_settings(scraper_mode="auto")

        with (
            patch(
                "src.skills.researcher.scrapers.get_settings",
                return_value=custom_settings,
            ),
            patch(
                "src.skills.researcher.scrapers.pymupdf_scraper.PyMuPDFScraper",
                pdf_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.trafilatura_scraper.TrafilaturaScraper",
                tf_mock,
            ),
        ):
            result = await scrape_with_fallback(
                "https://example.com/doc.pdf",
                enable_fallback=True,
                min_content_length=100,
            )

        assert pdf_mock.called, "PDF URL 应直接走 PyMuPDFScraper"
        assert not tf_mock.called, "PDF 不应触发 Trafilatura"
        assert result.get("content") == pdf_content

    async def test_arxiv_url_no_fallback(self) -> None:
        """Arxiv URL 直接走 ArxivScraper 不降级."""
        from src.skills.researcher.scrapers import scrape_with_fallback

        arxiv_content = "Arxiv paper abstract " * 20
        arxiv_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://arxiv.org/abs/1234.5678",
                "content": arxiv_content,
                "title": "Arxiv Paper",
                "image_urls": [],
            }
        )
        tf_mock = _make_scraper_class_mock(scrape_return={"content": "should-not-reach"})

        custom_settings = _make_settings(scraper_mode="auto")

        with (
            patch(
                "src.skills.researcher.scrapers.get_settings",
                return_value=custom_settings,
            ),
            patch(
                "src.skills.researcher.scrapers.arxiv_scraper.ArxivScraper",
                arxiv_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.trafilatura_scraper.TrafilaturaScraper",
                tf_mock,
            ),
        ):
            result = await scrape_with_fallback(
                "https://arxiv.org/abs/1234.5678",
                enable_fallback=True,
                min_content_length=100,
            )

        assert arxiv_mock.called, "Arxiv URL 应直接走 ArxivScraper"
        assert not tf_mock.called, "Arxiv 不应触发 Trafilatura"
        assert result.get("content") == arxiv_content

    async def test_scraper_mode_playwright_direct(self) -> None:
        """scraper_mode='playwright' 直接走 Playwright (调试用, 不走降级链)."""
        from src.skills.researcher.scrapers import scrape_with_fallback

        pw_content = "x" * 500
        pw_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/page",
                "content": pw_content,
                "title": "PW",
                "image_urls": [],
            }
        )
        tf_mock = _make_scraper_class_mock(scrape_return={"content": "should-not-reach"})
        bsm_mock = _make_scraper_class_mock(scrape_return={"content": "should-not-reach"})

        custom_settings = _make_settings(scraper_mode="playwright")

        with (
            patch(
                "src.skills.researcher.scrapers.get_settings",
                return_value=custom_settings,
            ),
            patch(
                "src.skills.researcher.scrapers.playwright_scraper.PlaywrightScraper",
                pw_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.trafilatura_scraper.TrafilaturaScraper",
                tf_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.bs_markdownify_scraper.BSMarkdownifyScraper",
                bsm_mock,
            ),
        ):
            result = await scrape_with_fallback(
                "https://example.com/page",
                enable_fallback=True,
                min_content_length=100,
            )

        assert pw_mock.called, "scraper_mode=playwright 应直接走 Playwright"
        assert not tf_mock.called, "playwright 模式不应触发 Trafilatura"
        assert not bsm_mock.called, "playwright 模式不应触发 BS"
        assert result.get("content") == pw_content

    async def test_all_three_scrapers_fail_best_content_fallback(self) -> None:
        """三级全部内容过短 → best_content 回退 (取最长内容)."""
        from src.skills.researcher.scrapers import scrape_with_fallback

        tf_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com",
                "content": "x" * 10,
                "title": "",
                "image_urls": [],
            }
        )
        bsm_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com",
                "content": "y" * 20,
                "title": "",
                "image_urls": [],
            }
        )
        pw_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com",
                "content": "z" * 50,
                "title": "PW Best",
                "image_urls": [],
            }
        )

        custom_settings = _make_settings(scraper_mode="auto")

        with (
            patch(
                "src.skills.researcher.scrapers.get_settings",
                return_value=custom_settings,
            ),
            patch(
                "src.skills.researcher.scrapers.trafilatura_scraper.TrafilaturaScraper",
                tf_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.bs_markdownify_scraper.BSMarkdownifyScraper",
                bsm_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.playwright_scraper.PlaywrightScraper",
                pw_mock,
            ),
        ):
            result = await scrape_with_fallback(
                "https://example.com/page",
                enable_fallback=True,
                min_content_length=100,
            )

        assert tf_mock.called
        assert bsm_mock.called
        assert pw_mock.called
        assert result.get("content") == "z" * 50

    async def test_enable_fallback_false_no_degradation(self) -> None:
        """enable_fallback=False 时不触发降级 (Trafilatura 内容过短也直接返回)."""
        from src.skills.researcher.scrapers import scrape_with_fallback

        tf_short = "short content"
        tf_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com",
                "content": tf_short,
                "title": "",
                "image_urls": [],
            }
        )
        bsm_mock = _make_scraper_class_mock(scrape_return={"content": "should-not-reach"})
        pw_mock = _make_scraper_class_mock(scrape_return={"content": "should-not-reach"})

        custom_settings = _make_settings(scraper_mode="auto")

        with (
            patch(
                "src.skills.researcher.scrapers.get_settings",
                return_value=custom_settings,
            ),
            patch(
                "src.skills.researcher.scrapers.trafilatura_scraper.TrafilaturaScraper",
                tf_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.bs_markdownify_scraper.BSMarkdownifyScraper",
                bsm_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.playwright_scraper.PlaywrightScraper",
                pw_mock,
            ),
        ):
            result = await scrape_with_fallback(
                "https://example.com/page",
                enable_fallback=False,
                min_content_length=100,
            )

        assert tf_mock.called, "Trafilatura 应作为首路径"
        assert not bsm_mock.called, "enable_fallback=False 不应触发 BS 降级"
        assert not pw_mock.called, "enable_fallback=False 不应触发 Playwright 降级"
        assert result.get("content") == tf_short

    async def test_min_content_length_boundary(self) -> None:
        """min_content_length 边界: 内容长度 == min_content_length 不触发降级."""
        from src.skills.researcher.scrapers import scrape_with_fallback

        boundary_content = "x" * 100
        tf_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com",
                "content": boundary_content,
                "title": "Boundary",
                "image_urls": [],
            }
        )
        bsm_mock = _make_scraper_class_mock(scrape_return={"content": "should-not-reach"})

        custom_settings = _make_settings(scraper_mode="auto")

        with (
            patch(
                "src.skills.researcher.scrapers.get_settings",
                return_value=custom_settings,
            ),
            patch(
                "src.skills.researcher.scrapers.trafilatura_scraper.TrafilaturaScraper",
                tf_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.bs_markdownify_scraper.BSMarkdownifyScraper",
                bsm_mock,
            ),
        ):
            result = await scrape_with_fallback(
                "https://example.com/page",
                enable_fallback=True,
                min_content_length=100,
            )

        assert tf_mock.called
        assert not bsm_mock.called, "内容长度 == min_content_length (>=) 不应触发降级"
        assert result.get("content") == boundary_content

    async def test_lightweight_mode_skips_all_fallback(self) -> None:
        """lightweight 模式: Trafilatura 内容过短 → 直接返回 (跳过 BS+markdownify 和 Playwright)."""
        from src.skills.researcher.scrapers import scrape_with_fallback

        tf_mock = _make_scraper_class_mock(
            scrape_return={"content": "short", "title": "", "image_urls": []}
        )
        bsm_mock = _make_scraper_class_mock(scrape_return={"content": "should-not-reach"})
        pw_mock = _make_scraper_class_mock(scrape_return={"content": "should-not-reach"})

        custom_settings = _make_settings(scraper_mode="lightweight")

        with (
            patch(
                "src.skills.researcher.scrapers.get_settings",
                return_value=custom_settings,
            ),
            patch(
                "src.skills.researcher.scrapers.trafilatura_scraper.TrafilaturaScraper",
                tf_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.bs_markdownify_scraper.BSMarkdownifyScraper",
                bsm_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.playwright_scraper.PlaywrightScraper",
                pw_mock,
            ),
        ):
            result = await scrape_with_fallback(
                "https://example.com/page",
                enable_fallback=True,
                min_content_length=100,
            )

        assert tf_mock.called, "Trafilatura 应作为首路径被实例化"
        assert not bsm_mock.called, "lightweight 模式应跳过 BS+markdownify"
        assert not pw_mock.called, "lightweight 模式应跳过 Playwright"
        assert not result.get("content") or len(result.get("content", "")) < 100
