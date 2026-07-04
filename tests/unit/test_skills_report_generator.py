"""单元测试: ReportGenerator 静态纯函数.

验证 _slugify / _generate_toc / _insert_image_into_report /
_basic_report_structure / _detailed_report_structure /
_build_references / _format_sources / _get_language_instruction,
不依赖 LLM / 文件系统.
AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
"""

from __future__ import annotations

import pytest

from src.skills.researcher.report_generator import ReportGenerator

pytestmark = pytest.mark.unit


# ========== _slugify ==========


def test_slugify_english_lowercase() -> None:
    """测试英文 slug 转小写 + 空格转连字符."""
    assert ReportGenerator._slugify("Hello World") == "hello-world"


def test_slugify_chinese_preserved() -> None:
    """测试中文字符保留."""
    assert ReportGenerator._slugify("中文标题") == "中文标题"


def test_slugify_mixed_chinese_english() -> None:
    """测试中英混合."""
    assert ReportGenerator._slugify("AI 在 医疗 应用") == "ai-在-医疗-应用"


def test_slugify_special_characters_removed() -> None:
    """测试特殊字符移除 (仅保留中文/字母/数字/连字符)."""
    slug = ReportGenerator._slugify("Hello, World! 你好?")
    # 标点应被移除
    assert "," not in slug
    assert "!" not in slug
    assert "?" not in slug
    assert "hello" in slug
    assert "world" in slug
    assert "你好" in slug


def test_slugify_consecutive_hyphens_merged() -> None:
    """测试连续连字符合并为单个."""
    assert ReportGenerator._slugify("Hello   World") == "hello-world"


def test_slugify_strips_leading_trailing_hyphens() -> None:
    """测试首尾连字符被剥离."""
    assert ReportGenerator._slugify("  Hello  ") == "hello"


def test_slugify_empty_string() -> None:
    """测试空串返回空."""
    assert ReportGenerator._slugify("") == ""


def test_slugify_digits_preserved() -> None:
    """测试数字保留."""
    assert ReportGenerator._slugify("Section 1.2 Title") == "section-12-title"


# ========== _generate_toc ==========


def test_generate_toc_basic() -> None:
    """测试 TOC 生成含标题 + 锚点链接."""
    topics = ["引言", "方法", "结论"]
    toc = ReportGenerator._generate_toc(topics)
    assert "## 目录" in toc
    assert "[引言](#" in toc
    assert "[方法](#" in toc
    assert "[结论](#" in toc


def test_generate_toc_anchors_match_slug() -> None:
    """测试 TOC 锚点链接与 _slugify 一致."""
    topics = ["Hello World", "中文标题"]
    toc = ReportGenerator._generate_toc(topics)
    assert "(#hello-world)" in toc
    assert "(#中文标题)" in toc


def test_generate_toc_numbering() -> None:
    """测试 TOC 含 1-based 序号."""
    topics = ["a", "b", "c"]
    toc = ReportGenerator._generate_toc(topics)
    assert "1. [a]" in toc
    assert "2. [b]" in toc
    assert "3. [c]" in toc


def test_generate_toc_empty_returns_empty() -> None:
    """测试空子主题列表返回空串."""
    assert ReportGenerator._generate_toc([]) == ""


def test_generate_toc_ends_with_horizontal_rule() -> None:
    """测试 TOC 末尾含 --- 分隔线."""
    toc = ReportGenerator._generate_toc(["x"])
    assert toc.rstrip().endswith("---")


# ========== _insert_image_into_report ==========


def test_insert_image_after_h1_with_url() -> None:
    """测试 image_url 在第一个 H1 后插入."""
    report = "# 标题\n\n正文"
    result = ReportGenerator._insert_image_into_report(report, "https://x.com/y.png", None)
    lines = result.split("\n")
    # 第一行是 H1, 第二行应该是图片
    assert lines[0] == "# 标题"
    assert "![报告配图](https://x.com/y.png)" in lines[1]


def test_insert_image_after_h1_with_b64() -> None:
    """测试 image_b64 转 data URL 插入."""
    report = "# 标题\n\n正文"
    result = ReportGenerator._insert_image_into_report(report, None, "abc123")
    assert "data:image/png;base64,abc123" in result
    assert "![报告配图](data:image/png;base64,abc123)" in result


def test_insert_image_no_h1_inserts_at_start() -> None:
    """测试无 H1 时在报告开头插入图片 (在正文之前)."""
    report = "正文无标题"
    result = ReportGenerator._insert_image_into_report(report, "https://x.com/y.png", None)
    # 图片应在正文之前 (image_md 以 \n 开头)
    image_pos = result.find("![报告配图]")
    report_pos = result.find("正文无标题")
    assert image_pos != -1
    assert report_pos != -1
    assert image_pos < report_pos


def test_insert_image_no_image_returns_unchanged() -> None:
    """测试无 url 与 b64 时返回原报告."""
    report = "# 标题\n正文"
    result = ReportGenerator._insert_image_into_report(report, None, None)
    assert result == report


# ========== _basic_report_structure ==========


def test_basic_report_structure_contains_sections() -> None:
    """测试基础结构含关键章节."""
    structure = ReportGenerator._basic_report_structure()
    assert "# {标题}" in structure
    assert "## 摘要" in structure
    assert "## 关键维度" in structure
    assert "## 分析与洞察" in structure
    assert "## 结论与展望" in structure
    assert "## 参考文献" in structure


# ========== _detailed_report_structure ==========


def test_detailed_report_structure_contains_sections() -> None:
    """测试详细结构含关键章节."""
    structure = ReportGenerator._detailed_report_structure()
    assert "# {标题}" in structure
    assert "## 摘要" in structure
    assert "## 行业背景" in structure
    assert "## 深度分析" in structure
    assert "## 竞争格局" in structure
    assert "## 趋势与展望" in structure
    assert "## 风险与挑战" in structure
    assert "## 结论" in structure
    assert "## 参考文献" in structure


# ========== _build_references ==========


def test_build_references_apa_format() -> None:
    """测试 APA 格式参考文献."""
    sources = [
        {"title": "论文 A", "url": "https://example.com/a"},
        {"title": "论文 B", "url": "https://example.com/b"},
    ]
    refs = ReportGenerator._build_references(sources)
    assert "[1] 论文 A. Retrieved from https://example.com/a" in refs
    assert "[2] 论文 B. Retrieved from https://example.com/b" in refs


def test_build_references_no_url() -> None:
    """测试无 URL 时不含 'Retrieved from'."""
    sources = [{"title": "无URL论文"}]
    refs = ReportGenerator._build_references(sources)
    assert "[1] 无URL论文." in refs
    assert "Retrieved from" not in refs


def test_build_references_empty_returns_placeholder() -> None:
    """测试空来源返回占位文本."""
    assert ReportGenerator._build_references([]) == "(无可用来源)"


def test_build_references_max_20() -> None:
    """测试最多返回 20 条引用."""
    sources = [{"title": f"标题{i}", "url": f"https://x.com/{i}"} for i in range(25)]
    refs = ReportGenerator._build_references(sources)
    # 应只包含 [1] 到 [20]
    assert "[20]" in refs
    assert "[21]" not in refs


# ========== _format_sources ==========


def test_format_sources_basic(generator: ReportGenerator) -> None:
    """测试引用来源列表格式化 (_format_sources 为实例方法)."""
    sources = [
        {"title": "src1", "url": "https://example.com/1"},
        {"title": "src2", "href": "https://example.com/2"},
    ]
    result = generator._format_sources(sources)
    assert "## 参考来源" in result
    assert "1. src1. https://example.com/1" in result
    assert "2. src2. https://example.com/2" in result


def test_format_sources_empty_returns_empty(generator: ReportGenerator) -> None:
    """测试空来源返回空串."""
    assert generator._format_sources([]) == ""


def test_format_sources_uses_href_fallback(generator: ReportGenerator) -> None:
    """测试 url 缺失时回退到 href 字段."""
    sources = [{"title": "t", "href": "https://x.com/h"}]
    result = generator._format_sources(sources)
    assert "https://x.com/h" in result


# ========== _get_language_instruction ==========


@pytest.fixture()
def generator() -> ReportGenerator:
    """构造 ReportGenerator 实例 (跳过 LLM 依赖, 仅用静态方法)."""
    # ReportGenerator.__init__ 会创建 LLMClient, 但 _get_language_instruction
    # 仅访问 _LANGUAGE_INSTRUCTIONS 类属性, 不调用 LLM.
    # 通过 __new__ 跳过 __init__, 避免 LLMClient 初始化.
    obj = ReportGenerator.__new__(ReportGenerator)
    return obj


def test_language_instruction_zh_returns_empty(generator: ReportGenerator) -> None:
    """测试中文返回空串 (默认无需额外指令)."""
    assert generator._get_language_instruction("zh") == ""


def test_language_instruction_none_returns_empty(generator: ReportGenerator) -> None:
    """测试 None 视为中文返回空串."""
    assert generator._get_language_instruction(None) == ""


def test_language_instruction_en(generator: ReportGenerator) -> None:
    """测试英文指令."""
    result = generator._get_language_instruction("en")
    assert "English" in result
    assert len(result) > 0


def test_language_instruction_ja(generator: ReportGenerator) -> None:
    """测试日文指令."""
    result = generator._get_language_instruction("ja")
    assert "日本語" in result


def test_language_instruction_ko(generator: ReportGenerator) -> None:
    """测试韩文指令."""
    result = generator._get_language_instruction("ko")
    assert "한국어" in result


def test_language_instruction_fr(generator: ReportGenerator) -> None:
    """测试法文指令."""
    result = generator._get_language_instruction("fr")
    assert "français" in result


def test_language_instruction_unknown_returns_empty(generator: ReportGenerator) -> None:
    """测试未知语言降级为空串 (视为 zh)."""
    assert generator._get_language_instruction("de") == ""
    assert generator._get_language_instruction("xxx") == ""
