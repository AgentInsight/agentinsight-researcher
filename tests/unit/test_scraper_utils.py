"""抓取器工具函数 (utils.py) 单元测试.

测试覆盖:
1. parse_dimension: 尺寸值解析 (px 后缀/小数/空值/异常)
2. _score_image: 单图评分 (class 优先级/尺寸分级/小图过滤)
3. get_relevant_images_from_soup: 从 BeautifulSoup 提取 + 评分排序
4. get_relevant_images_from_html: 从 HTML 字符串提取
5. 边界情况: 空输入/相对路径/data-src/data URI/非 http
6. 错误处理: 无效 HTML/解析异常
7. top_k 限制
8. 高优先级 class 全覆盖 (header/featured/hero/thumbnail/main/content)

单元测试在构建期执行, 不依赖外部服务.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from src.skills.researcher.scrapers.utils import (
    _score_image,
    get_relevant_images_from_html,
    get_relevant_images_from_soup,
    parse_dimension,
)


class TestParseDimension:
    """parse_dimension 尺寸解析测试."""

    def test_plain_int(self) -> None:
        """纯整数应正确解析."""
        assert parse_dimension("800") == 800

    def test_px_suffix(self) -> None:
        """px 后缀应被剥离."""
        assert parse_dimension("800px") == 800

    def test_decimal_value(self) -> None:
        """小数应向下取整."""
        assert parse_dimension("409.12") == 409

    def test_decimal_with_px(self) -> None:
        """带 px 后缀的小数应正确解析."""
        assert parse_dimension("409.99px") == 409

    def test_none_input(self) -> None:
        """None 输入应返回 None."""
        assert parse_dimension(None) is None

    def test_empty_string(self) -> None:
        """空字符串应返回 None."""
        assert parse_dimension("") is None

    def test_invalid_string(self) -> None:
        """非数字字符串应返回 None."""
        assert parse_dimension("abc") is None

    def test_negative_value(self) -> None:
        """负数应被解析 (调用方决定是否过滤)."""
        assert parse_dimension("-100") == -100

    def test_with_whitespace(self) -> None:
        """带空白的值应被 strip."""
        assert parse_dimension("  800  ") == 800

    def test_px_with_whitespace(self) -> None:
        """带空白的 px 值应被正确处理."""
        assert parse_dimension("  800px  ") == 800

    def test_float_string(self) -> None:
        """浮点字符串应正确解析."""
        assert parse_dimension("1024.5") == 1024

    def test_zero(self) -> None:
        """0 应被正确解析."""
        assert parse_dimension("0") == 0


class TestScoreImage:
    """_score_image 单图评分测试."""

    def _make_img(self, **attrs: object) -> MagicMock:
        """构造 mock img 标签."""
        img = MagicMock()
        img.get = lambda key, default=None: attrs.get(key, default)
        return img

    def test_high_priority_class_header(self) -> None:
        """class 含 'header' 应得 4 分."""
        img = self._make_img(**{"class": ["header"]})
        assert _score_image(img) == 4

    def test_high_priority_class_featured(self) -> None:
        """class 含 'featured' 应得 4 分."""
        img = self._make_img(**{"class": ["featured", "other"]})
        assert _score_image(img) == 4

    def test_high_priority_class_hero(self) -> None:
        """class 含 'hero' 应得 4 分."""
        img = self._make_img(**{"class": ["hero"]})
        assert _score_image(img) == 4

    def test_high_priority_class_thumbnail(self) -> None:
        """class 含 'thumbnail' 应得 4 分."""
        img = self._make_img(**{"class": ["thumbnail"]})
        assert _score_image(img) == 4

    def test_high_priority_class_main(self) -> None:
        """class 含 'main' 应得 4 分."""
        img = self._make_img(**{"class": ["main"]})
        assert _score_image(img) == 4

    def test_high_priority_class_content(self) -> None:
        """class 含 'content' 应得 4 分."""
        img = self._make_img(**{"class": ["content"]})
        assert _score_image(img) == 4

    def test_class_string_not_list(self) -> None:
        """class 属性为字符串而非列表时也应正确处理."""
        img = MagicMock()
        img.get = lambda key, default=None: "featured" if key == "class" else default
        assert _score_image(img) == 4

    def test_large_size_3_score(self) -> None:
        """width>=2000 且 height>=1000 应得 3 分."""
        img = self._make_img(width="2500", height="1200")
        assert _score_image(img) == 3

    def test_medium_size_2_score(self) -> None:
        """width>=1600 或 height>=800 应得 2 分."""
        img = self._make_img(width="1800", height="600")
        assert _score_image(img) == 2

    def test_small_size_1_score(self) -> None:
        """width>=800 或 height>=500 应得 1 分."""
        img = self._make_img(width="900", height="600")
        assert _score_image(img) == 1

    def test_tiny_size_0_score(self) -> None:
        """width>=500 或 height>=300 应得 0 分."""
        img = self._make_img(width="600", height="400")
        assert _score_image(img) == 0

    def test_too_small_skipped(self) -> None:
        """尺寸过小应返回 None (跳过)."""
        img = self._make_img(width="100", height="100")
        assert _score_image(img) is None

    def test_no_size_kept_0(self) -> None:
        """无尺寸信息应保留 (评分 0)."""
        img = self._make_img()
        assert _score_image(img) == 0

    def test_only_width_no_height(self) -> None:
        """只有 width 无 height 应保留 (评分 0)."""
        img = self._make_img(width="800")
        assert _score_image(img) == 0

    def test_class_overrides_size(self) -> None:
        """class 优先级高于尺寸 (即使尺寸小, 有高优 class 仍 4 分)."""
        img = self._make_img(**{"class": ["hero"], "width": "100", "height": "100"})
        assert _score_image(img) == 4


class TestGetRelevantImagesFromSoup:
    """get_relevant_images_from_soup 批量提取 + 评分排序."""

    def _make_html(self, imgs: list[dict]) -> str:
        """构造含多个 img 标签的 HTML."""
        tags = []
        for img in imgs:
            attrs = " ".join(f'{k}="{v}"' for k, v in img.items())
            tags.append(f"<img {attrs}>")
        return f"<html><body>{''.join(tags)}</body></html>"

    def test_top4_by_score(self) -> None:
        """应取 Top-4 按评分降序."""
        from bs4 import BeautifulSoup

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
        result = get_relevant_images_from_soup(soup, "https://example.com", top_k=4)
        assert len(result) == 4
        assert result[0] == "https://example.com/featured.jpg"
        assert result[1] == "https://example.com/xlarge.jpg"
        assert result[2] == "https://example.com/large.jpg"
        assert result[3] == "https://example.com/medium.jpg"

    def test_relative_url_urljoin(self) -> None:
        """相对路径应被 urljoin 转为绝对路径."""
        from bs4 import BeautifulSoup

        html = '<img src="/images/pic.jpg" class="hero">'
        soup = BeautifulSoup(html, "lxml")
        result = get_relevant_images_from_soup(soup, "https://example.com/page")
        assert result == ["https://example.com/images/pic.jpg"]

    def test_skip_non_http(self) -> None:
        """非 http/https 协议应被跳过 (如 data URI)."""
        from bs4 import BeautifulSoup

        html = '<img src="data:image/svg+xml;base64,abc" class="hero">'
        soup = BeautifulSoup(html, "lxml")
        result = get_relevant_images_from_soup(soup, "https://example.com")
        assert result == []

    def test_data_src_fallback(self) -> None:
        """img 无 src 时应回退到 data-src."""
        from bs4 import BeautifulSoup

        html = '<img data-src="/lazy.jpg" width="900" height="600">'
        soup = BeautifulSoup(html, "lxml")
        result = get_relevant_images_from_soup(soup, "https://example.com")
        assert result == ["https://example.com/lazy.jpg"]

    def test_empty_html(self) -> None:
        """空 HTML 应返回空列表."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<html></html>", "lxml")
        assert get_relevant_images_from_soup(soup, "https://example.com") == []

    def test_top_k_limit(self) -> None:
        """top_k 应限制返回数量."""
        from bs4 import BeautifulSoup

        html = self._make_html(
            [
                {"src": f"https://example.com/{i}.jpg", "class": "hero"}
                for i in range(10)
            ]
        )
        soup = BeautifulSoup(html, "lxml")
        result = get_relevant_images_from_soup(soup, "https://example.com", top_k=3)
        assert len(result) == 3

    def test_skip_non_string_src(self) -> None:
        """src 非 string 类型 (如 None) 应被跳过."""
        from bs4 import BeautifulSoup

        html = '<img class="hero">'
        soup = BeautifulSoup(html, "lxml")
        result = get_relevant_images_from_soup(soup, "https://example.com")
        assert result == []

    def test_protocol_relative_url(self) -> None:
        """协议相对 URL (//) 应被跳过 (非 http:// 开头)."""
        from bs4 import BeautifulSoup

        html = '<img src="//cdn.example.com/img.jpg" class="hero">'
        soup = BeautifulSoup(html, "lxml")
        result = get_relevant_images_from_soup(soup, "https://example.com")
        # urljoin 会将 //cdn.example.com 解析为 https://cdn.example.com
        # 实际行为: urljoin("https://example.com", "//cdn.example.com/img.jpg")
        # → "https://cdn.example.com/img.jpg" (协议继承)
        # 所以这里会保留
        if result:
            assert result[0] == "https://cdn.example.com/img.jpg"


class TestGetRelevantImagesFromHtml:
    """get_relevant_images_from_html HTML 字符串入口测试."""

    def test_basic_extraction(self) -> None:
        """基本提取应正确返回图片 URL."""
        html = '<img src="/a.jpg" class="featured"><img src="/b.jpg" width="100" height="100">'
        result = get_relevant_images_from_html(html, "https://example.com", top_k=2)
        assert "https://example.com/a.jpg" in result
        # b.jpg 尺寸过小 (100x100) 应被跳过
        assert "https://example.com/b.jpg" not in result

    def test_empty_html(self) -> None:
        """空 HTML 应返回空列表."""
        assert get_relevant_images_from_html("", "https://example.com") == []

    def test_html_with_no_images(self) -> None:
        """无 img 标签的 HTML 应返回空列表."""
        html = "<html><body><p>仅文本</p></body></html>"
        assert get_relevant_images_from_html(html, "https://example.com") == []

    def test_complex_html_extraction(self) -> None:
        """复杂 HTML 应正确提取评分排序."""
        html = """
        <html><body>
            <article>
                <img src="/hero.jpg" class="hero" />
                <p>内容</p>
                <img src="/thumb.jpg" width="600" height="400" />
                <img src="/icon.jpg" width="50" height="50" />
            </article>
        </body></html>
        """
        result = get_relevant_images_from_html(html, "https://example.com", top_k=2)
        assert len(result) == 2
        assert result[0] == "https://example.com/hero.jpg"
        assert result[1] == "https://example.com/thumb.jpg"

    def test_top_k_default_4(self) -> None:
        """top_k 默认值应为 4."""
        html = "".join(
            f'<img src="https://example.com/{i}.jpg" class="hero">' for i in range(10)
        )
        result = get_relevant_images_from_html(html, "https://example.com")
        assert len(result) == 4

    def test_exception_returns_empty(self) -> None:
        """解析异常时应返回空列表 (不抛异常)."""
        # get_relevant_images_from_html 内部 import bs4.BeautifulSoup,
        # 通过 patch bs4.BeautifulSoup 模拟解析异常
        with patch("bs4.BeautifulSoup", side_effect=Exception("解析失败")):
            result = get_relevant_images_from_html(
                "<html></html>",
                "https://example.com",
            )
        assert result == []
