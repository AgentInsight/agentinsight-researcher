"""单元测试: mcp_coordinator._make_cache_key 缓存键生成.

验证 _make_cache_key 输出 md5(query + tool_name + json(args)) 格式,
相同输入生成相同 key, 不同输入生成不同 key.
AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from src.skills.researcher.mcp_coordinator import _make_cache_key

pytestmark = pytest.mark.unit


# ========== 格式验证 ==========


def test_make_cache_key_returns_32_hex_string() -> None:
    """测试返回 32 位 hex 字符串 (md5 摘要长度)."""
    key = _make_cache_key("query", "tool", {"arg": "val"})
    assert isinstance(key, str)
    assert len(key) == 32
    # 全部为 hex 字符
    int(key, 16)  # 不抛异常即为合法 hex


def test_make_cache_key_matches_md5_format() -> None:
    """测试 key 等于 md5(query:tool_name:args_json) 的 hexdigest."""
    query = "my query"
    tool_name = "search_tool"
    args = {"q": "test", "limit": 5}
    args_str = json.dumps(args, sort_keys=True, ensure_ascii=False)
    raw = f"{query}:{tool_name}:{args_str}"
    expected = hashlib.md5(raw.encode()).hexdigest()
    assert _make_cache_key(query, tool_name, args) == expected


# ========== 相同输入 → 相同 key ==========


def test_make_cache_key_same_inputs_returns_same_key() -> None:
    """测试相同输入生成相同 key."""
    args = {"q": "test", "n": 3}
    key1 = _make_cache_key("query", "tool", args)
    key2 = _make_cache_key("query", "tool", args)
    assert key1 == key2


def test_make_cache_key_args_order_independent() -> None:
    """测试 args 字段顺序不影响 key (sort_keys=True)."""
    args_a = {"a": 1, "b": 2}
    args_b = {"b": 2, "a": 1}
    key_a = _make_cache_key("query", "tool", args_a)
    key_b = _make_cache_key("query", "tool", args_b)
    assert key_a == key_b


# ========== 不同输入 → 不同 key ==========


def test_make_cache_key_different_query_different_key() -> None:
    """测试不同 query 生成不同 key."""
    args = {"q": "x"}
    key1 = _make_cache_key("query1", "tool", args)
    key2 = _make_cache_key("query2", "tool", args)
    assert key1 != key2


def test_make_cache_key_different_tool_different_key() -> None:
    """测试不同 tool_name 生成不同 key."""
    args = {"q": "x"}
    key1 = _make_cache_key("query", "tool1", args)
    key2 = _make_cache_key("query", "tool2", args)
    assert key1 != key2


def test_make_cache_key_different_args_different_key() -> None:
    """测试不同 args 生成不同 key."""
    key1 = _make_cache_key("query", "tool", {"q": "a"})
    key2 = _make_cache_key("query", "tool", {"q": "b"})
    assert key1 != key2


def test_make_cache_key_empty_args_vs_nonempty_different() -> None:
    """测试空 args 与非空 args 生成不同 key."""
    key1 = _make_cache_key("query", "tool", {})
    key2 = _make_cache_key("query", "tool", {"q": "x"})
    assert key1 != key2


# ========== 边界 ==========


def test_make_cache_key_empty_args() -> None:
    """测试空 args dict 也能生成 key."""
    key = _make_cache_key("query", "tool", {})
    assert len(key) == 32


def test_make_cache_key_unicode_args() -> None:
    """测试含中文等 unicode 字符的 args (ensure_ascii=False)."""
    args = {"query": "中文查询"}
    key = _make_cache_key("查询", "工具", args)
    assert len(key) == 32
    # 相同中文输入应稳定
    assert _make_cache_key("查询", "工具", args) == key


def test_make_cache_key_non_serializable_args_uses_repr() -> None:
    """测试含不可序列化对象时降级为 repr (不抛异常)."""

    class NonSerializable:
        pass

    args = {"obj": NonSerializable()}
    # 不应抛异常, 降级为 repr
    key = _make_cache_key("query", "tool", args)
    assert len(key) == 32


def test_make_cache_key_nested_args() -> None:
    """测试嵌套 dict args 也能正常序列化."""
    args = {"filter": {"lang": "zh", "region": "cn"}, "limit": 10}
    key = _make_cache_key("query", "tool", args)
    assert len(key) == 32
    # 相同嵌套结构稳定
    assert _make_cache_key("query", "tool", args) == key


def test_make_cache_key_list_args_order_matters() -> None:
    """测试 list 元素顺序影响 key (list 序列化保持顺序)."""
    args_a = {"items": [1, 2, 3]}
    args_b = {"items": [3, 2, 1]}
    key_a = _make_cache_key("query", "tool", args_a)
    key_b = _make_cache_key("query", "tool", args_b)
    assert key_a != key_b
