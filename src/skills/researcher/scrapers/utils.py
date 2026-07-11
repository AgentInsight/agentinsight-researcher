"""scraper 公共工具函数 (设计参考 scraper/utils.py).

P2-05:
- get_relevant_images_from_soup: 从 BeautifulSoup 提取图片并评分排序
- get_relevant_images_from_html: 从 HTML 字符串提取图片并评分排序
- parse_dimension: 解析尺寸值 (支持 px 后缀)

评分规则 (设计参考):
- class 含 header/featured/hero/thumbnail/main/content → 4 分 (最高)
- width>=2000 且 height>=1000 → 3 分 (大图)
- width>=1600 或 height>=800 → 2 分
- width>=800 或 height>=500 → 1 分
- width>=500 或 height>=300 → 0 分 (最低保留)
- 更小 → 跳过 (过滤缩略图/图标)

返回: 排序后的 URL 字符串列表 (Top-K, 默认 4).
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


def parse_dimension(value: str | None) -> int | None:
    """解析尺寸值, 支持 px 后缀 (设计参考 parse_dimension).

    Args:
        value: 尺寸字符串, 如 "800", "800px", "409.12"
    Returns:
        int 或 None (解析失败)
    """
    if not value:
        return None
    try:
        v = str(value).strip()
        if v.lower().endswith("px"):
            v = v[:-2]
        return int(float(v))
    except (ValueError, TypeError):
        return None


def _score_image(img: Any) -> int | None:
    """为单个 img 标签评分 (设计参考 get_relevant_images 评分逻辑).

    Returns:
        0-4 分, 或 None (跳过小图).
    """
    # 优先检查 class (最高分)
    classes = img.get("class") or []
    if isinstance(classes, str):
        classes = [classes]
    high_priority_classes = {
        "header",
        "featured",
        "hero",
        "thumbnail",
        "main",
        "content",
    }
    if any(cls in high_priority_classes for cls in classes):
        return 4

    # 检查尺寸属性
    width = parse_dimension(img.get("width"))
    height = parse_dimension(img.get("height"))
    if width and height:
        if width >= 2000 and height >= 1000:
            return 3
        if width >= 1600 or height >= 800:
            return 2
        if width >= 800 or height >= 500:
            return 1
        if width >= 500 or height >= 300:
            return 0
        # 更小 → 跳过 (过滤缩略图/图标)
        return None

    # 无尺寸信息 → 保留 (评分 0, 不主动丢弃)
    return 0


def get_relevant_images_from_soup(soup: Any, url: str, top_k: int = 4) -> list[str]:
    """从 BeautifulSoup 对象提取相关图片并评分排序 (设计参考 get_relevant_images).

    Args:
        soup: BeautifulSoup 对象
        url: 页面 URL (用于 urljoin 相对路径)
        top_k: 返回前 K 张 (默认 4)
    Returns:
        排序后的图片 URL 字符串列表 (按评分降序)
    """
    try:
        scored_images: list[tuple[str, int]] = []
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src")
            if not isinstance(src, str):
                continue
            # urljoin 处理相对路径
            full_url = urljoin(url, src)
            if not full_url.startswith(("http://", "https://")):
                continue
            score = _score_image(img)
            if score is None:
                continue  # 跳过小图
            scored_images.append((full_url, score))

        # 按评分降序排序 (稳定排序, 同分保持原顺序)
        scored_images.sort(key=lambda x: x[1], reverse=True)
        return [url for url, _ in scored_images[:top_k]]
    except Exception as e:  # noqa: BLE001
        logger.warning("图片评分提取失败: %s", e)
        return []


def get_relevant_images_from_html(html: str, url: str, top_k: int = 4) -> list[str]:
    """从 HTML 字符串提取相关图片并评分排序 (Playwright 适配).

    Args:
        html: HTML 字符串
        url: 页面 URL (用于 urljoin 相对路径)
        top_k: 返回前 K 张 (默认 4)
    Returns:
        排序后的图片 URL 字符串列表 (按评分降序)
    """
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        return get_relevant_images_from_soup(soup, url, top_k=top_k)
    except ImportError:
        logger.debug("bs4 未安装, 无法评分图片")
        return []
    except Exception as e:  # noqa: BLE001
        logger.warning("HTML 图片评分提取失败: %s", e)
        return []
