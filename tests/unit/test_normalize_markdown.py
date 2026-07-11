"""ReportGenerator._normalize_markdown 单元测试.

原文件为手动脚本 (run_tests + __main__), 违反 "手动调试
脚本放在 tests/manual/" 约定. 重构为 pytest 参数化用例, 保留在 unit/ 下
作为正式测试, 同时支持 `python -m tests.unit.test_normalize_markdown` 独立
运行 (兼容旧用法).
"""

from __future__ import annotations

import sys
from collections.abc import Callable

import pytest

from src.skills.researcher.report_generator import ReportGenerator

# ========== 测试用例数据 ==========


def _case_table_internal_no_blank() -> tuple[str, str, Callable[[str], bool]]:
    """表格内部不应被插入空行."""
    inp = "## 表格测试\n\n| 名称 | 值 |\n| --- | --- |\n| A | 1 |\n| B | 2 |\n\n正文"
    desc = "表格内部不应有空行"

    def check(out: str) -> bool:
        return "|\n\n|" not in out

    return inp, desc, check


def _case_table_around_add_blank() -> tuple[str, str, Callable[[str], bool]]:
    """表格前后应有空行."""
    inp = "正文\n| 名称 | 值 |\n| --- | --- |\n| A | 1 |\n正文"
    desc = "表格前后应补空行"

    def check(out: str) -> bool:
        return ("正文\n|" not in out) and ("|\n正文" not in out)

    return inp, desc, check


def _case_heading_paragraph_add_blank() -> tuple[str, str, Callable[[str], bool]]:
    """段落紧贴标题应补空行."""
    inp = "## 标题\n正文内容"
    desc = "标题后应补空行"

    def check(out: str) -> bool:
        return "## 标题\n正文" not in out

    return inp, desc, check


def _case_citation_spacing() -> tuple[str, str, Callable[[str], bool]]:
    """引用紧贴应修复为带空格."""
    inp = "研究表明[1][2]显示"
    desc = "引用紧贴应修复为 [1] [2]"

    def check(out: str) -> bool:
        return "[1][2]" not in out

    return inp, desc, check


def _case_consecutive_blank_compress() -> tuple[str, str, Callable[[str], bool]]:
    """连续空行应压缩为 2 个."""
    inp = "段落1\n\n\n\n\n段落2"
    desc = "连续4空行应压缩为2"

    def check(out: str) -> bool:
        return "\n\n\n\n" not in out

    return inp, desc, check


def _case_list_internal_no_blank() -> tuple[str, str, Callable[[str], bool]]:
    """列表项之间不应有空行."""
    inp = "## 列表\n- 项目1\n- 项目2\n- 项目3\n\n正文"
    desc = "列表内部不应有空行"

    def check(out: str) -> bool:
        return "- 项目1\n\n- 项目2" not in out

    return inp, desc, check


def _case_list_around_add_blank() -> tuple[str, str, Callable[[str], bool]]:
    """列表前后应有空行."""
    inp = "正文\n- 项目1\n- 项目2\n正文"
    desc = "列表前后应补空行"

    def check(out: str) -> bool:
        return ("正文\n- 项目1" not in out) and ("- 项目2\n正文" not in out)

    return inp, desc, check


def _case_trailing_blank_cleanup() -> tuple[str, str, Callable[[str], bool]]:
    """末尾空行应清理为单个换行."""
    inp = "内容\n\n\n\n"
    desc = "末尾空行应清理"

    def check(out: str) -> bool:
        return out == "内容\n"

    return inp, desc, check


def _case_composite() -> tuple[str, str, Callable[[str], bool]]:
    """综合用例: 标题/段落/引用/表格混合."""
    inp = (
        "## 摘要\n这是摘要内容\n## 关键维度\n### 物理原理\n量子比特[1][2]是基本单元\n"
        "| 技术 | 优势 |\n| --- | --- |\n| 超导 | 高速 |\n| 离子阱 | 长相干 |\n"
        "## 结论\n量子计算前景广阔"
    )
    desc = "综合用例: 标题/段落/引用/表格混合"

    def check(out: str) -> bool:
        if "|\n\n|" in out:
            return False
        if "[1][2]" in out:
            return False
        if "## 摘要\n这是" in out:
            return False
        return True

    return inp, desc, check


# 所有用例集合 (顺序与原脚本一致)
_ALL_CASES = [
    _case_table_internal_no_blank(),
    _case_table_around_add_blank(),
    _case_heading_paragraph_add_blank(),
    _case_citation_spacing(),
    _case_consecutive_blank_compress(),
    _case_list_internal_no_blank(),
    _case_list_around_add_blank(),
    _case_trailing_blank_cleanup(),
    _case_composite(),
]


# ========== pytest 参数化测试 ==========


@pytest.mark.parametrize(
    ("input_md", "desc", "check_fn"),
    _ALL_CASES,
    ids=[c[1] for c in _ALL_CASES],
)
def test_normalize_markdown(input_md: str, desc: str, check_fn: Callable[[str], bool]) -> None:
    """_normalize_markdown 应正确处理各类 Markdown 结构."""
    result = ReportGenerator._normalize_markdown(input_md)
    assert check_fn(result), f"{desc} 失败, 输出={result!r}"


def test_normalize_markdown_empty_string() -> None:
    """空字符串应返回空字符串."""
    assert ReportGenerator._normalize_markdown("") == ""


def test_normalize_markdown_pure_text() -> None:
    """纯文本(无 Markdown 结构)应原样返回(末尾补换行)."""
    result = ReportGenerator._normalize_markdown("纯文本内容")
    assert "纯文本内容" in result


# ========== 兼容旧用法: python -m tests.unit.test_normalize_markdown ==========


def run_tests() -> bool:
    """兼容旧脚本用法: 直接运行本文件时执行所有用例并打印汇总."""
    print("=" * 80)
    print("_normalize_markdown 单元测试")
    print("=" * 80)
    passed = 0
    failed = 0
    for i, (input_md, desc, check_fn) in enumerate(_ALL_CASES, 1):
        result = ReportGenerator._normalize_markdown(input_md)
        print(f"\n--- 测试 {i}: {desc} ---")
        print(f"输入:  {repr(input_md[:80])}")
        print(f"输出:  {repr(result[:120])}")
        if check_fn(result):
            print("结果: PASS")
            passed += 1
        else:
            print("结果: FAIL")
            failed += 1

    print(f"\n{'=' * 80}")
    print(f"汇总: {passed} 通过, {failed} 失败, 共 {len(_ALL_CASES)} 个测试")
    print(f"{'=' * 80}")
    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
