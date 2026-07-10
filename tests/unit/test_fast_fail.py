"""单元测试: 403 快速失败 + 降级链 del 释放 (P2-04 内存优化).

验证 src/skills/researcher/scrapers/__init__.py 的 3 项硬性要求:
1. 成功不降级 (各级成功直接返回, 不做 max() 三级比较)
2. 失败不驻留 (降级前 del 上一级结果)
3. 403 快速失败 (401/403/429 命中即终止, 不触发 BS/Playwright)

AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
所有 scraper 类与 get_settings 全部 mock, 不实际网络请求.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.config.settings import Settings

pytestmark = pytest.mark.unit


# ========== Helpers (复用 test_scraper_routing.py 模式) ==========


def _make_scraper_class_mock(
    *,
    scrape_return: dict | None = None,
    scrape_side_effect: Exception | None = None,
) -> MagicMock:
    """构造 scraper 类 mock, 实例化后 scrape() 返回指定结果或抛异常."""

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


def _make_settings(*, scraper_mode: str = "auto") -> Settings:
    """构造测试 Settings (跳过 .env 加载)."""
    s = Settings(_env_file=None)
    s.scraper_mode = scraper_mode
    return s


# ========== TestFastFail: 403 快速失败测试 ==========


class TestFastFail:
    """验证 401/403/429 快速失败: 命中即终止, 不触发后续降级."""

    @pytest.mark.parametrize("status_code", [401, 403, 429])
    async def test_tf_fast_fail_no_bs(self, status_code: int) -> None:
        """L1 Trafilatura 命中快速失败状态码 → 不触发 L2 BS."""
        from src.skills.researcher.scrapers import scrape_with_fallback

        tf_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/page",
                "content": "",
                "title": "",
                "image_urls": [],
                "_http_status": status_code,
            }
        )
        bs_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/page",
                "content": "x" * 500,
                "title": "",
                "image_urls": [],
            }
        )

        with (
            patch(
                "src.skills.researcher.scrapers.get_settings",
                return_value=_make_settings(),
            ),
            patch(
                "src.skills.researcher.scrapers.trafilatura_scraper.TrafilaturaScraper",
                tf_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.bs_markdownify_scraper.BSMarkdownifyScraper",
                bs_mock,
            ),
        ):
            result = await scrape_with_fallback(
                "https://example.com/page",
                enable_fallback=True,
                min_content_length=100,
            )

        # 命中快速失败, 直接返回 L1 结果 (含 _http_status)
        assert result["_http_status"] == status_code
        # BS 不应被实例化 (side_effect 调用次数应为 0)
        assert bs_mock.call_count == 0

    @pytest.mark.parametrize("status_code", [401, 403, 429])
    async def test_bs_fast_fail_no_playwright(self, status_code: int) -> None:
        """L2 BS 命中快速失败状态码 → 不触发 L3 Playwright."""
        from src.skills.researcher.scrapers import scrape_with_fallback

        tf_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/page",
                "content": "short",  # L1 内容过短, 触发降级
                "title": "",
                "image_urls": [],
            }
        )
        bs_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/page",
                "content": "",
                "title": "",
                "image_urls": [],
                "_http_status": status_code,
            }
        )
        pw_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/page",
                "content": "x" * 500,
                "title": "",
                "image_urls": [],
            }
        )

        with (
            patch(
                "src.skills.researcher.scrapers.get_settings",
                return_value=_make_settings(),
            ),
            patch(
                "src.skills.researcher.scrapers.trafilatura_scraper.TrafilaturaScraper",
                tf_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.bs_markdownify_scraper.BSMarkdownifyScraper",
                bs_mock,
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

        # 命中快速失败, 直接返回 L2 结果 (含 _http_status)
        assert result["_http_status"] == status_code
        # Playwright 不应被实例化
        assert pw_mock.call_count == 0

    async def test_500_not_fast_fail_triggers_bs(self) -> None:
        """L1 返回 500 (5xx 不在快速失败集合) → 触发 L2 BS 降级."""
        from src.skills.researcher.scrapers import scrape_with_fallback

        tf_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/page",
                "content": "",
                "title": "",
                "image_urls": [],
                "_http_status": 500,
            }
        )
        bs_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/page",
                "content": "x" * 500,
                "title": "",
                "image_urls": [],
            }
        )

        with (
            patch(
                "src.skills.researcher.scrapers.get_settings",
                return_value=_make_settings(),
            ),
            patch(
                "src.skills.researcher.scrapers.trafilatura_scraper.TrafilaturaScraper",
                tf_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.bs_markdownify_scraper.BSMarkdownifyScraper",
                bs_mock,
            ),
        ):
            result = await scrape_with_fallback(
                "https://example.com/page",
                enable_fallback=True,
                min_content_length=100,
            )

        # 5xx 不快速失败, 降级到 BS 成功
        assert result["content"] == "x" * 500
        assert "_http_status" not in result  # BS 成功路径不含 _http_status
        assert bs_mock.call_count == 1


# ========== TestDelRelease: del 释放与 max() 移除测试 ==========


class TestDelRelease:
    """验证失败不驻留: 降级前 del 上一级结果, 移除 max() 三级比较."""

    async def test_l1_fail_del_no_max_comparison(self) -> None:
        """L1 失败降级 L2 成功 → 直接返回 L2, 不做 max() 比较."""
        from src.skills.researcher.scrapers import scrape_with_fallback

        tf_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/page",
                "content": "short",  # L1 内容过短
                "title": "",
                "image_urls": [],
            }
        )
        bs_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/page",
                "content": "L2 content " * 50,  # L2 成功 (>=100)
                "title": "BS Title",
                "image_urls": [],
            }
        )

        with (
            patch(
                "src.skills.researcher.scrapers.get_settings",
                return_value=_make_settings(),
            ),
            patch(
                "src.skills.researcher.scrapers.trafilatura_scraper.TrafilaturaScraper",
                tf_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.bs_markdownify_scraper.BSMarkdownifyScraper",
                bs_mock,
            ),
        ):
            result = await scrape_with_fallback(
                "https://example.com/page",
                enable_fallback=True,
                min_content_length=100,
            )

        # L2 成功直接返回, 不做 max() 比较 (原 max() 会取 L1 的 "short" 与 L2 比较)
        assert result["content"] == "L2 content " * 50
        assert result["title"] == "BS Title"

    async def test_l3_success_no_max_comparison(self) -> None:
        """L3 Playwright 兜底成功 → 直接返回 L3, 不做 max() 三级比较."""
        from src.skills.researcher.scrapers import scrape_with_fallback

        tf_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/page",
                "content": "short",
                "title": "",
                "image_urls": [],
            }
        )
        bs_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/page",
                "content": "short",  # L2 也过短
                "title": "",
                "image_urls": [],
            }
        )
        pw_content = "Playwright content " * 20
        pw_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/page",
                "content": pw_content,
                "title": "PW Title",
                "image_urls": [],
            }
        )

        with (
            patch(
                "src.skills.researcher.scrapers.get_settings",
                return_value=_make_settings(),
            ),
            patch(
                "src.skills.researcher.scrapers.trafilatura_scraper.TrafilaturaScraper",
                tf_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.bs_markdownify_scraper.BSMarkdownifyScraper",
                bs_mock,
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

        # L3 直接返回, 不做 max() (原 max() 会比较 tf/bsm/pw 三份内容)
        assert result["content"] == pw_content
        assert result["title"] == "PW Title"

    async def test_l3_failure_returns_empty(self) -> None:
        """L3 Playwright 抛异常 → 返回空结果 (L1/L2 已 del, 无法回退)."""
        from src.skills.researcher.scrapers import scrape_with_fallback

        tf_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/page",
                "content": "short",
                "title": "",
                "image_urls": [],
            }
        )
        bs_mock = _make_scraper_class_mock(
            scrape_return={
                "url": "https://example.com/page",
                "content": "short",
                "title": "",
                "image_urls": [],
            }
        )
        pw_mock = _make_scraper_class_mock(scrape_side_effect=RuntimeError("chromium crash"))

        with (
            patch(
                "src.skills.researcher.scrapers.get_settings",
                return_value=_make_settings(),
            ),
            patch(
                "src.skills.researcher.scrapers.trafilatura_scraper.TrafilaturaScraper",
                tf_mock,
            ),
            patch(
                "src.skills.researcher.scrapers.bs_markdownify_scraper.BSMarkdownifyScraper",
                bs_mock,
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

        # L3 失败: _safe_scrape 捕获异常返回 content=None,
        # scrape_with_fallback 直接返回 pw_result (不再回退到 tf_result, 因已 del).
        # content 为 None 或空均符合 "失败" 语义 (falsy).
        assert not result["content"]
        assert result["title"] == ""
        assert result["image_urls"] == []


# ========== TestIsFastFail: _is_fast_fail 辅助函数测试 ==========


class TestIsFastFail:
    """验证 _is_fast_fail 辅助函数的边界条件."""

    def test_403_is_fast_fail(self) -> None:
        from src.skills.researcher.scrapers import _is_fast_fail

        assert _is_fast_fail({"_http_status": 403}) is True

    def test_401_is_fast_fail(self) -> None:
        from src.skills.researcher.scrapers import _is_fast_fail

        assert _is_fast_fail({"_http_status": 401}) is True

    def test_429_is_fast_fail(self) -> None:
        from src.skills.researcher.scrapers import _is_fast_fail

        assert _is_fast_fail({"_http_status": 429}) is True

    def test_500_not_fast_fail(self) -> None:
        from src.skills.researcher.scrapers import _is_fast_fail

        assert _is_fast_fail({"_http_status": 500}) is False

    def test_404_not_fast_fail(self) -> None:
        """404 不快速失败 (页面不存在, 但降级可能成功, 如 BS 解析缓存)."""
        from src.skills.researcher.scrapers import _is_fast_fail

        assert _is_fast_fail({"_http_status": 404}) is False

    def test_no_http_status_not_fast_fail(self) -> None:
        """无 _http_status 字段 → 不快速失败 (正常成功/失败路径)."""
        from src.skills.researcher.scrapers import _is_fast_fail

        assert _is_fast_fail({"content": "xxx"}) is False

    def test_string_http_status_not_fast_fail(self) -> None:
        """_http_status 为字符串 → 不快速失败 (类型校验)."""
        from src.skills.researcher.scrapers import _is_fast_fail

        assert _is_fast_fail({"_http_status": "403"}) is False
