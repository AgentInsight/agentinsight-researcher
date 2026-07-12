"""单元测试: 三级 JSON 容错解析工具.

验证 src/common/json_utils.py:
- safe_json_parse: 三级解析链 (json.loads → json_repair → regex 提取)
  - 第一级: 标准 json.loads (正常 JSON)
  - 第二级: json_repair.loads (尾逗号/单引号/未引用键/markdown 代码块/嵌入文本)
  - 第三级: regex 提取 (json_repair 返回 None 时的兜底)
  - 边界: 空字符串/None/非字符串 → fallback
- safe_json_parse_dict: 安全解析为 dict
  - 正常 dict 返回
  - 非 dict (list/str/数字/布尔) → fallback
  - 默认 fallback = {}

单元测试不依赖外部服务. json_repair 为本地库 (非外部服务), 直接使用真实库;
regex 兜底路径通过 mock json_repair.loads 返回 None 触发.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.common.json_utils import safe_json_parse, safe_json_parse_dict

pytestmark = pytest.mark.unit


# ========== safe_json_parse: 第一级 (标准 json.loads) ==========


def test_safe_json_parse_standard_dict() -> None:
    """标准 JSON dict → 直接解析返回."""
    assert safe_json_parse('{"a": 1, "b": "hello"}') == {"a": 1, "b": "hello"}


def test_safe_json_parse_standard_list() -> None:
    """标准 JSON list → 直接解析返回."""
    assert safe_json_parse("[1, 2, 3]") == [1, 2, 3]


def test_safe_json_parse_standard_string() -> None:
    """标准 JSON 字符串 (带引号) → 解析为 Python str."""
    assert safe_json_parse('"hello world"') == "hello world"


def test_safe_json_parse_standard_number() -> None:
    """标准 JSON 数字 → 解析为 Python int."""
    assert safe_json_parse("42") == 42


def test_safe_json_parse_standard_boolean() -> None:
    """标准 JSON 布尔 → 解析为 Python bool."""
    assert safe_json_parse("true") is True
    assert safe_json_parse("false") is False


def test_safe_json_parse_standard_null() -> None:
    """标准 JSON null → 解析为 Python None (非 fallback)."""
    assert safe_json_parse("null") is None


def test_safe_json_parse_strips_whitespace() -> None:
    """前后空白应被 strip 后正常解析."""
    assert safe_json_parse('  {"a": 1}  \n') == {"a": 1}


# ========== safe_json_parse: 第二级 (json_repair 修复) ==========


def test_safe_json_parse_trailing_comma() -> None:
    """尾逗号 JSON → json_repair 修复为 dict."""
    assert safe_json_parse('{"a": 1, "b": 2,}') == {"a": 1, "b": 2}


def test_safe_json_parse_single_quotes() -> None:
    """单引号 JSON → json_repair 修复为 dict."""
    assert safe_json_parse("{'a': 1, 'b': 2}") == {"a": 1, "b": 2}


def test_safe_json_parse_unquoted_keys() -> None:
    """未引用键的 JSON → json_repair 修复为 dict."""
    assert safe_json_parse("{key: value}") == {"key": "value"}


def test_safe_json_parse_markdown_code_block_with_lang() -> None:
    """markdown 代码块 (```json) → json_repair 解析为 dict."""
    text = '```json\n{"a": 1, "b": 2}\n```'
    assert safe_json_parse(text) == {"a": 1, "b": 2}


def test_safe_json_parse_markdown_code_block_without_lang() -> None:
    """markdown 代码块 (无语言标记) → json_repair 解析为 dict."""
    text = '```\n{"a": 1}\n```'
    assert safe_json_parse(text) == {"a": 1}


def test_safe_json_parse_json_embedded_in_text() -> None:
    """JSON 嵌入多余文本 → json_repair 提取为 dict."""
    text = 'Here is the result: {"a": 1, "b": 2}'
    assert safe_json_parse(text) == {"a": 1, "b": 2}


def test_safe_json_parse_array_embedded_in_text() -> None:
    """JSON 数组嵌入多余文本 → json_repair 提取为 list."""
    text = "Results: [1, 2, 3]"
    assert safe_json_parse(text) == [1, 2, 3]


# ========== safe_json_parse: 第三级 (regex 兜底, mock json_repair) ==========


def test_safe_json_parse_regex_fallback_markdown_block() -> None:
    """json_repair 返回 None 时 → regex 从 markdown 代码块提取 dict."""
    text = '```json\n{"a": 1}\n```'
    with patch("json_repair.loads", return_value=None):
        result = safe_json_parse(text)
    assert result == {"a": 1}


def test_safe_json_parse_regex_fallback_object_in_text() -> None:
    """json_repair 返回 None 时 → regex 从多余文本提取 JSON 对象."""
    text = 'prefix text {"key": "value"} suffix text'
    with patch("json_repair.loads", return_value=None):
        result = safe_json_parse(text)
    assert result == {"key": "value"}


def test_safe_json_parse_regex_fallback_array_in_text() -> None:
    """json_repair 返回 None 时 → regex 从多余文本提取 JSON 数组."""
    text = "some text [1, 2, 3] more text"
    with patch("json_repair.loads", return_value=None):
        result = safe_json_parse(text)
    assert result == [1, 2, 3]


def test_safe_json_parse_regex_fallback_plain_code_block() -> None:
    """json_repair 返回 None 时 → regex 剥离无语言标记代码块后解析."""
    text = '```\n{"x": 10}\n```'
    with patch("json_repair.loads", return_value=None):
        result = safe_json_parse(text)
    assert result == {"x": 10}


# ========== safe_json_parse: 边界与 fallback ==========


def test_safe_json_parse_empty_string_returns_fallback() -> None:
    """空字符串 → 直接返回 fallback (短路)."""
    assert safe_json_parse("", fallback="default") == "default"


def test_safe_json_parse_none_returns_fallback() -> None:
    """None 输入 → 直接返回 fallback (短路)."""
    assert safe_json_parse(None, fallback="default") == "default"  # type: ignore[arg-type]


def test_safe_json_parse_non_string_int_returns_fallback() -> None:
    """非字符串 (int) → 直接返回 fallback (短路)."""
    assert safe_json_parse(123, fallback="default") == "default"  # type: ignore[arg-type]


def test_safe_json_parse_non_string_list_returns_fallback() -> None:
    """非字符串 (list) → 直接返回 fallback (短路)."""
    assert safe_json_parse([1, 2], fallback="default") == "default"  # type: ignore[arg-type]


def test_safe_json_parse_default_fallback_is_none() -> None:
    """未指定 fallback 时 → 默认返回 None."""
    assert safe_json_parse("") is None
    assert safe_json_parse(None) is None  # type: ignore[arg-type]


def test_safe_json_parse_custom_fallback_on_all_fail() -> None:
    """三级解析全部失败 → 返回自定义 fallback."""
    # json_repair 返回 None, 且文本中无 JSON 结构可提取
    with patch("json_repair.loads", return_value=None):
        result = safe_json_parse("plain text no json here", fallback={"default": True})
    assert result == {"default": True}


def test_safe_json_parse_json_repair_exception_falls_through() -> None:
    """json_repair 抛异常时 → 进入 regex 兜底路径."""
    text = '```json\n{"a": 1}\n```'
    with patch("json_repair.loads", side_effect=RuntimeError("mocked error")):
        result = safe_json_parse(text)
    # regex 兜底应成功提取
    assert result == {"a": 1}


def test_safe_json_parse_nested_structure() -> None:
    """嵌套 JSON 结构 → 正常解析."""
    text = '{"outer": {"inner": [1, 2, {"deep": true}]}}'
    assert safe_json_parse(text) == {"outer": {"inner": [1, 2, {"deep": True}]}}


# ========== safe_json_parse_dict: 正常 dict 返回 ==========


def test_safe_json_parse_dict_normal_dict() -> None:
    """正常 JSON dict → 返回 dict."""
    assert safe_json_parse_dict('{"a": 1}') == {"a": 1}


def test_safe_json_parse_dict_with_repair() -> None:
    """尾逗号 JSON → json_repair 修复后返回 dict."""
    assert safe_json_parse_dict('{"a": 1,}') == {"a": 1}


def test_safe_json_parse_dict_markdown_block() -> None:
    """markdown 代码块 → 解析后返回 dict."""
    assert safe_json_parse_dict('```json\n{"key": "value"}\n```') == {"key": "value"}


# ========== safe_json_parse_dict: 非 dict 返回 fallback ==========


def test_safe_json_parse_dict_list_returns_default_fallback() -> None:
    """解析结果为 list → 返回默认 fallback {}."""
    assert safe_json_parse_dict("[1, 2, 3]") == {}


def test_safe_json_parse_dict_string_returns_default_fallback() -> None:
    """解析结果为 str → 返回默认 fallback {}."""
    assert safe_json_parse_dict('"hello"') == {}


def test_safe_json_parse_dict_number_returns_default_fallback() -> None:
    """解析结果为 number → 返回默认 fallback {}."""
    assert safe_json_parse_dict("42") == {}


def test_safe_json_parse_dict_boolean_returns_default_fallback() -> None:
    """解析结果为 boolean → 返回默认 fallback {}."""
    assert safe_json_parse_dict("true") == {}


def test_safe_json_parse_dict_custom_fallback_when_non_dict() -> None:
    """解析结果为 list + 自定义 fallback → 返回自定义 fallback."""
    custom = {"default": True}
    assert safe_json_parse_dict("[1, 2]", fallback=custom) == custom


def test_safe_json_parse_dict_custom_fallback_on_empty_input() -> None:
    """空输入 + 自定义 fallback → 返回自定义 fallback."""
    custom = {"mode": "empty"}
    assert safe_json_parse_dict("", fallback=custom) == custom


def test_safe_json_parse_dict_none_input_returns_default() -> None:
    """None 输入 → 返回默认 fallback {}."""
    assert safe_json_parse_dict(None) == {}  # type: ignore[arg-type]


def test_safe_json_parse_dict_default_fallback_is_empty_dict() -> None:
    """未指定 fallback 时 → 默认返回 {}."""
    assert safe_json_parse_dict("invalid") == {}
