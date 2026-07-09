"""单元测试: GPTR 借鉴点 (P2-05) - 池化 + 域名限流 + 图片评分.

验证 3 项 GPTR 借鉴实现:
1. 图片评分 (scrapers/utils.py): parse_dimension / _score_image /
   get_relevant_images_from_soup / get_relevant_images_from_html
2. 域名级限流 (scrapers/__init__.py DomainRateLimiter):
   _get_domain / _get_semaphore / throttle 同域名串行化
3. 浏览器池化 (scrapers/playwright_scraper.py _PlaywrightPool):
   max_browsers=5 + 负载均衡 (min processing_count) + acquire/release 语义

AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
所有 Playwright/httpx 全部 mock, 不启动真实 chromium.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ========== TestImageScoring: 图片评分测试 (utils.py) ==========


class TestParseDimension:
    """parse_dimension 尺寸解析."""

    def test_basic_int(self) -> None:
        from src.skills.researcher.scrapers.utils import parse_dimension

        assert parse_dimension("800") == 800

    def test_px_suffix(self) -> None:
        from src.skills.researcher.scrapers.utils import parse_dimension

        assert parse_dimension("800px") == 800

    def test_decimal(self) -> None:
        from src.skills.researcher.scrapers.utils import parse_dimension

        assert parse_dimension("409.12") == 409

    def test_none(self) -> None:
        from src.skills.researcher.scrapers.utils import parse_dimension

        assert parse_dimension(None) is None

    def test_empty(self) -> None:
        from src.skills.researcher.scrapers.utils import parse_dimension

        assert parse_dimension("") is None

    def test_invalid(self) -> None:
        from src.skills.researcher.scrapers.utils import parse_dimension

        assert parse_dimension("abc") is None


class TestScoreImage:
    """_score_image 单图评分 (对标 GPTR 评分规则)."""

    def _make_img(self, **attrs: object) -> MagicMock:
        img = MagicMock()
        img.get = lambda key, default=None: attrs.get(key, default)
        return img

    def test_high_priority_class_featured(self) -> None:
        from src.skills.researcher.scrapers.utils import _score_image

        img = self._make_img(**{"class": ["featured", "other"]})
        assert _score_image(img) == 4

    def test_high_priority_class_hero(self) -> None:
        from src.skills.researcher.scrapers.utils import _score_image

        img = self._make_img(**{"class": ["hero"]})
        assert _score_image(img) == 4

    def test_large_size_3_score(self) -> None:
        from src.skills.researcher.scrapers.utils import _score_image

        img = self._make_img(width="2500", height="1200")
        assert _score_image(img) == 3

    def test_medium_size_2_score(self) -> None:
        from src.skills.researcher.scrapers.utils import _score_image

        img = self._make_img(width="1800", height="600")
        assert _score_image(img) == 2

    def test_small_size_1_score(self) -> None:
        from src.skills.researcher.scrapers.utils import _score_image

        img = self._make_img(width="900", height="600")
        assert _score_image(img) == 1

    def test_tiny_size_0_score(self) -> None:
        from src.skills.researcher.scrapers.utils import _score_image

        img = self._make_img(width="600", height="400")
        assert _score_image(img) == 0

    def test_too_small_skipped(self) -> None:
        from src.skills.researcher.scrapers.utils import _score_image

        img = self._make_img(width="100", height="100")
        assert _score_image(img) is None

    def test_no_size_kept_0(self) -> None:
        from src.skills.researcher.scrapers.utils import _score_image

        img = self._make_img()
        assert _score_image(img) == 0

    def test_class_string_not_list(self) -> None:
        from src.skills.researcher.scrapers.utils import _score_image

        # class 属性为字符串而非列表时也应正确处理
        img = MagicMock()
        img.get = lambda key, default=None: "featured" if key == "class" else default
        assert _score_image(img) == 4


class TestGetRelevantImagesFromSoup:
    """get_relevant_images_from_soup 批量提取 + 评分排序."""

    def _make_html(self, imgs: list[dict]) -> str:
        tags = []
        for img in imgs:
            attrs = " ".join(f'{k}="{v}"' for k, v in img.items())
            tags.append(f"<img {attrs}>")
        return f"<html><body>{''.join(tags)}</body></html>"

    def test_top4_by_score(self) -> None:
        from bs4 import BeautifulSoup

        from src.skills.researcher.scrapers.utils import get_relevant_images_from_soup

        html = self._make_html(
            [
                {"src": "/small.jpg", "width": "100", "height": "100"},  # None 跳过
                {"src": "/tiny.jpg", "width": "600", "height": "400"},  # 0 分
                {"src": "/medium.jpg", "width": "900", "height": "600"},  # 1 分
                {"src": "/large.jpg", "width": "1800", "height": "600"},  # 2 分
                {"src": "/xlarge.jpg", "width": "2500", "height": "1200"},  # 3 分
                {"src": "/featured.jpg", "class": "featured"},  # 4 分
            ]
        )
        soup = BeautifulSoup(html, "lxml")
        result = get_relevant_images_from_soup(
            soup, "https://example.com", top_k=4
        )
        # 5 张保留 (1 张跳过), 取 Top-4 按评分降序
        assert len(result) == 4
        assert result[0] == "https://example.com/featured.jpg"  # 4 分
        assert result[1] == "https://example.com/xlarge.jpg"  # 3 分
        assert result[2] == "https://example.com/large.jpg"  # 2 分
        assert result[3] == "https://example.com/medium.jpg"  # 1 分

    def test_relative_url_urljoin(self) -> None:
        from bs4 import BeautifulSoup

        from src.skills.researcher.scrapers.utils import get_relevant_images_from_soup

        html = '<img src="/images/pic.jpg" class="hero">'
        soup = BeautifulSoup(html, "lxml")
        result = get_relevant_images_from_soup(soup, "https://example.com/page")
        assert result == ["https://example.com/images/pic.jpg"]

    def test_skip_non_http(self) -> None:
        from bs4 import BeautifulSoup

        from src.skills.researcher.scrapers.utils import get_relevant_images_from_soup

        html = '<img src="data:image/svg+xml;base64,abc" class="hero">'
        soup = BeautifulSoup(html, "lxml")
        result = get_relevant_images_from_soup(soup, "https://example.com")
        assert result == []

    def test_data_src_fallback(self) -> None:
        from bs4 import BeautifulSoup

        from src.skills.researcher.scrapers.utils import get_relevant_images_from_soup

        html = '<img data-src="/lazy.jpg" width="900" height="600">'
        soup = BeautifulSoup(html, "lxml")
        result = get_relevant_images_from_soup(soup, "https://example.com")
        assert result == ["https://example.com/lazy.jpg"]

    def test_empty_html(self) -> None:
        from bs4 import BeautifulSoup

        from src.skills.researcher.scrapers.utils import get_relevant_images_from_soup

        soup = BeautifulSoup("<html></html>", "lxml")
        assert get_relevant_images_from_soup(soup, "https://example.com") == []


class TestGetRelevantImagesFromHtml:
    """get_relevant_images_from_html HTML 字符串入口."""

    def test_basic_extraction(self) -> None:
        from src.skills.researcher.scrapers.utils import (
            get_relevant_images_from_html,
        )

        html = '<img src="/a.jpg" class="featured"><img src="/b.jpg" width="100">'
        result = get_relevant_images_from_html(html, "https://example.com", top_k=2)
        assert "https://example.com/a.jpg" in result

    def test_empty_html(self) -> None:
        from src.skills.researcher.scrapers.utils import (
            get_relevant_images_from_html,
        )

        assert get_relevant_images_from_html("", "https://example.com") == []


# ========== TestDomainRateLimiter: 域名级限流测试 ==========


class TestDomainRateLimiter:
    """DomainRateLimiter 域名级限流 (借鉴 GPTR NoDriverScraper)."""

    def test_get_domain_basic(self) -> None:
        from src.skills.researcher.scrapers import DomainRateLimiter

        limiter = DomainRateLimiter()
        assert limiter._get_domain("https://www.example.com/path") == "example.com"

    def test_get_domain_two_parts(self) -> None:
        from src.skills.researcher.scrapers import DomainRateLimiter

        limiter = DomainRateLimiter()
        assert limiter._get_domain("https://example.com") == "example.com"

    def test_get_domain_deep_subdomain(self) -> None:
        from src.skills.researcher.scrapers import DomainRateLimiter

        limiter = DomainRateLimiter()
        assert limiter._get_domain("https://a.b.c.example.com") == "example.com"

    @pytest.mark.asyncio
    async def test_same_domain_shares_semaphore(self) -> None:
        from src.skills.researcher.scrapers import DomainRateLimiter

        limiter = DomainRateLimiter()
        # 清理单例状态
        limiter._semaphores.clear()
        sem1 = await limiter._get_semaphore("example.com")
        sem2 = await limiter._get_semaphore("example.com")
        assert sem1 is sem2

    @pytest.mark.asyncio
    async def test_diff_domain_separate_semaphore(self) -> None:
        from src.skills.researcher.scrapers import DomainRateLimiter

        limiter = DomainRateLimiter()
        limiter._semaphores.clear()
        sem1 = await limiter._get_semaphore("example.com")
        sem2 = await limiter._get_semaphore("other.com")
        assert sem1 is not sem2

    @pytest.mark.asyncio
    async def test_throttle_serializes_same_domain(self) -> None:
        """同域名请求应串行执行 (不重叠)."""
        from src.skills.researcher.scrapers import DomainRateLimiter

        limiter = DomainRateLimiter()
        limiter._semaphores.clear()

        active = 0
        max_active = 0
        lock = asyncio.Lock()

        async def task() -> None:
            nonlocal active, max_active
            async with limiter.throttle("https://example.com"):
                async with lock:
                    active += 1
                    max_active = max(max_active, active)
                await asyncio.sleep(0.05)
                async with lock:
                    active -= 1

        await asyncio.gather(*[task() for _ in range(3)])
        # 同域名串行化: 任意时刻最多 1 个在执行
        assert max_active == 1

    @pytest.mark.asyncio
    async def test_throttle_parallel_diff_domain(self) -> None:
        """不同域名请求应并行执行 (不互相阻塞)."""
        from src.skills.researcher.scrapers import DomainRateLimiter

        limiter = DomainRateLimiter()
        limiter._semaphores.clear()

        active = 0
        max_active = 0
        lock = asyncio.Lock()

        async def task(url: str) -> None:
            nonlocal active, max_active
            async with limiter.throttle(url):
                async with lock:
                    active += 1
                    max_active = max(max_active, active)
                await asyncio.sleep(0.05)
                async with lock:
                    active -= 1

        # 3 个不同域名应并行
        await asyncio.gather(
            task("https://a.com"),
            task("https://b.com"),
            task("https://c.com"),
        )
        assert max_active == 3


# ========== TestPlaywrightPool: 浏览器池化负载均衡测试 ==========


@pytest.fixture
def reset_pool() -> None:
    """每个测试前重置 _PlaywrightPool 单例状态 (ClassVar)."""
    from src.skills.researcher.scrapers.playwright_scraper import _PlaywrightPool

    _PlaywrightPool._instance = None
    _PlaywrightPool._lock = None
    _PlaywrightPool._pooled_browsers.clear()
    yield
    # 测试后清理
    _PlaywrightPool._instance = None
    _PlaywrightPool._lock = None
    _PlaywrightPool._pooled_browsers.clear()


def _make_mock_pooled_browser(processing_count: int = 0) -> MagicMock:
    """构造 mock _PooledBrowser (不启动真实 chromium)."""
    pooled = MagicMock()
    pooled.browser = MagicMock()
    pooled.playwright = MagicMock()
    pooled.processing_count = processing_count
    pooled.domain_semaphores = {}
    pooled.stopping = False
    pooled.acquire_domain = AsyncMock(return_value=None)
    pooled.stop = AsyncMock()
    return pooled


class TestPlaywrightPoolLoadBalancing:
    """_PlaywrightPool 池化负载均衡 (借鉴 GPTR NoDriverScraper)."""

    @pytest.mark.asyncio
    async def test_first_browser_created(self, reset_pool: None) -> None:
        """空池首次调用应创建新 browser."""
        from src.skills.researcher.scrapers.playwright_scraper import (
            _PlaywrightPool,
        )

        mock_pooled = _make_mock_pooled_browser()
        create_mock = AsyncMock(return_value=mock_pooled)
        with patch.object(_PlaywrightPool, "_create_pooled_browser", new=create_mock):
            pooled = await _PlaywrightPool._get_pooled_browser(None)
            assert pooled is mock_pooled
            create_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_load_balancing_min_count(self, reset_pool: None) -> None:
        """多 browser 时应选 processing_count 最低的."""
        from src.skills.researcher.scrapers.playwright_scraper import (
            _PlaywrightPool,
        )

        low = _make_mock_pooled_browser(processing_count=1)
        high = _make_mock_pooled_browser(processing_count=5)
        _PlaywrightPool._pooled_browsers.add(low)
        _PlaywrightPool._pooled_browsers.add(high)
        _PlaywrightPool._instance = _PlaywrightPool()  # 触发单例

        create_mock = AsyncMock()
        with patch.object(_PlaywrightPool, "_create_pooled_browser", new=create_mock):
            pooled = await _PlaywrightPool._get_pooled_browser(None)
            assert pooled is low  # 选 processing_count=1 的
            create_mock.assert_not_called()  # 不新建

    @pytest.mark.asyncio
    async def test_max_browsers_limit(self, reset_pool: None) -> None:
        """池满后不再新建, 复用负载最低 (即使超阈值)."""
        from src.skills.researcher.scrapers.playwright_scraper import (
            _PlaywrightPool,
        )

        # 填满池 (max_browsers=5)
        for _ in range(_PlaywrightPool._max_browsers):
            _PlaywrightPool._pooled_browsers.add(
                _make_mock_pooled_browser(processing_count=10)
            )
        _PlaywrightPool._instance = _PlaywrightPool()

        create_mock = AsyncMock()
        with patch.object(_PlaywrightPool, "_create_pooled_browser", new=create_mock):
            pooled = await _PlaywrightPool._get_pooled_browser(None)
            # 池满, 不新建, 复用最低 (任意一个 processing_count=10)
            assert pooled in _PlaywrightPool._pooled_browsers
            create_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_when_threshold_exceeded(self, reset_pool: None) -> None:
        """单 browser 超 threshold 且池未满 → 新建."""
        from src.skills.researcher.scrapers.playwright_scraper import (
            _PlaywrightPool,
        )

        # 1 个 browser, processing_count = threshold (8)
        overloaded = _make_mock_pooled_browser(
            processing_count=_PlaywrightPool._browser_load_threshold
        )
        _PlaywrightPool._pooled_browsers.add(overloaded)
        _PlaywrightPool._instance = _PlaywrightPool()

        new_pooled = _make_mock_pooled_browser()
        create_mock = AsyncMock(return_value=new_pooled)
        with patch.object(_PlaywrightPool, "_create_pooled_browser", new=create_mock):
            pooled = await _PlaywrightPool._get_pooled_browser(None)
            assert pooled is new_pooled  # 新建
            create_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_create_when_below_threshold(self, reset_pool: None) -> None:
        """单 browser 未超 threshold → 复用, 不新建."""
        from src.skills.researcher.scrapers.playwright_scraper import (
            _PlaywrightPool,
        )

        idle = _make_mock_pooled_browser(
            processing_count=_PlaywrightPool._browser_load_threshold - 1
        )
        _PlaywrightPool._pooled_browsers.add(idle)
        _PlaywrightPool._instance = _PlaywrightPool()

        create_mock = AsyncMock()
        with patch.object(_PlaywrightPool, "_create_pooled_browser", new=create_mock):
            pooled = await _PlaywrightPool._get_pooled_browser(None)
            assert pooled is idle
            create_mock.assert_not_called()


class TestPlaywrightPoolAcquireRelease:
    """acquire/release 语义测试."""

    @pytest.mark.asyncio
    async def test_acquire_increments_count(self, reset_pool: None) -> None:
        from src.skills.researcher.scrapers.playwright_scraper import (
            _PlaywrightPool,
        )

        mock_pooled = _make_mock_pooled_browser(processing_count=0)
        with patch.object(
            _PlaywrightPool, "_get_pooled_browser", new=AsyncMock(return_value=mock_pooled)
        ):
            browser, sem, pooled = await _PlaywrightPool.acquire("https://example.com")
            assert pooled is mock_pooled
            assert pooled.processing_count == 1
            assert browser is mock_pooled.browser

    @pytest.mark.asyncio
    async def test_release_decrements_count(self, reset_pool: None) -> None:
        from src.skills.researcher.scrapers.playwright_scraper import (
            _PlaywrightPool,
        )

        mock_pooled = _make_mock_pooled_browser(processing_count=3)
        await _PlaywrightPool.release(mock_pooled)
        assert mock_pooled.processing_count == 2

    @pytest.mark.asyncio
    async def test_release_floor_zero(self, reset_pool: None) -> None:
        """release 不应让 processing_count 为负."""
        from src.skills.researcher.scrapers.playwright_scraper import (
            _PlaywrightPool,
        )

        mock_pooled = _make_mock_pooled_browser(processing_count=0)
        await _PlaywrightPool.release(mock_pooled)
        assert mock_pooled.processing_count == 0

    @pytest.mark.asyncio
    async def test_acquire_returns_domain_sem(self, reset_pool: None) -> None:
        """acquire 应返回域名 Semaphore (非 None)."""
        from src.skills.researcher.scrapers.playwright_scraper import (
            _PlaywrightPool,
        )

        mock_pooled = _make_mock_pooled_browser()
        domain_sem = asyncio.Semaphore(1)
        mock_pooled.acquire_domain = AsyncMock(return_value=domain_sem)
        with patch.object(
            _PlaywrightPool, "_get_pooled_browser", new=AsyncMock(return_value=mock_pooled)
        ):
            _, sem, _ = await _PlaywrightPool.acquire("https://example.com")
            assert sem is domain_sem

    @pytest.mark.asyncio
    async def test_acquire_failure_decrements_count(self, reset_pool: None) -> None:
        """acquire_domain 抛异常时应回滚 processing_count."""
        from src.skills.researcher.scrapers.playwright_scraper import (
            _PlaywrightPool,
        )

        mock_pooled = _make_mock_pooled_browser(processing_count=0)
        mock_pooled.acquire_domain = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.object(
            _PlaywrightPool, "_get_pooled_browser", new=AsyncMock(return_value=mock_pooled)
        ):
            with pytest.raises(RuntimeError, match="boom"):
                await _PlaywrightPool.acquire("https://example.com")
            # 异常回滚: processing_count 应为 0
            assert mock_pooled.processing_count == 0


class TestPlaywrightPoolShutdown:
    """shutdown 清理测试."""

    @pytest.mark.asyncio
    async def test_shutdown_closes_all_browsers(self, reset_pool: None) -> None:
        from src.skills.researcher.scrapers.playwright_scraper import (
            _PlaywrightPool,
        )

        pooled1 = _make_mock_pooled_browser()
        pooled2 = _make_mock_pooled_browser()
        _PlaywrightPool._pooled_browsers.add(pooled1)
        _PlaywrightPool._pooled_browsers.add(pooled2)
        _PlaywrightPool._instance = _PlaywrightPool()

        await _PlaywrightPool.shutdown()
        assert len(_PlaywrightPool._pooled_browsers) == 0
        pooled1.stop.assert_awaited_once()
        pooled2.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_idempotent(self, reset_pool: None) -> None:
        """shutdown 幂等: 多次调用不报错."""
        from src.skills.researcher.scrapers.playwright_scraper import (
            _PlaywrightPool,
        )

        _PlaywrightPool._instance = _PlaywrightPool()
        await _PlaywrightPool.shutdown()
        await _PlaywrightPool.shutdown()  # 不应抛异常
        assert _PlaywrightPool._instance is None


# ========== TestGetDomainHelper: _get_domain 辅助函数 ==========


class TestGetDomainHelper:
    """playwright_scraper._get_domain 域名提取 (对标 GPTR)."""

    def test_basic(self) -> None:
        from src.skills.researcher.scrapers.playwright_scraper import _get_domain

        assert _get_domain("https://www.example.com/path") == "example.com"

    def test_no_subdomain(self) -> None:
        from src.skills.researcher.scrapers.playwright_scraper import _get_domain

        assert _get_domain("https://example.com") == "example.com"

    def test_deep_subdomain(self) -> None:
        from src.skills.researcher.scrapers.playwright_scraper import _get_domain

        assert _get_domain("https://a.b.c.example.com/x") == "example.com"

    def test_with_port(self) -> None:
        from src.skills.researcher.scrapers.playwright_scraper import _get_domain

        assert _get_domain("https://example.com:8080") == "example.com:8080"
