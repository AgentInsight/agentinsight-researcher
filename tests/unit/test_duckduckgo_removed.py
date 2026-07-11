"""单元测试: DuckDuckGo 移除调用验证.

验证 DuckDuckGo 已从搜索引擎注册表中移除, 但代码保留以备将来恢复:
1. _SEARCHER_REGISTRY 不含 "duckduckgo" 键 (注册块已注释)
2. duckduckgo.py 文件仍存在 (代码保留)
3. FREE_QUOTA_MAP 仍含 "duckduckgo" 条目 (代码保留, 不影响功能)
4. __init__.py 中 duckduckgo 的 import 与注册块已注释
5. SearXNG 已替代 DuckDuckGo 在 CN/GLOBAL/AUTO 三区域注册

AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
项目记忆: DuckDuckGo 移除策略 - 注释而非删除, 保留代码和 FREE_QUOTA_MAP 条目.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SEARCHERS_INIT = _PROJECT_ROOT / "src" / "skills" / "researcher" / "searchers" / "__init__.py"
_DUCKDUCKGO_FILE = _PROJECT_ROOT / "src" / "skills" / "researcher" / "searchers" / "duckduckgo.py"


# ========== 注册表移除验证 ==========


def test_duckduckgo_not_in_registry() -> None:
    """_SEARCHER_REGISTRY 不含 "duckduckgo" 键 (注册块已注释).

    DuckDuckGo 已被 SearXNG 替代, 注册块注释, 不应在注册表中.
    """
    from src.skills.researcher.searchers import _SEARCHER_REGISTRY, _register_all_searchers

    _register_all_searchers()
    assert "duckduckgo" not in _SEARCHER_REGISTRY, (
        "_SEARCHER_REGISTRY 不应含 'duckduckgo' (已被 SearXNG 替代, 注册块注释)"
    )


def test_searxng_replaces_duckduckgo_in_cn_region() -> None:
    """SearXNG 已替代 DuckDuckGo 在 CN 区域注册 (免费引擎).

    SearXNG 注册到 CN/GLOBAL/AUTO 三区域, 替代 DuckDuckGo 的 CN 区域角色.
    """
    from src.skills.researcher.searchers import _SEARCHER_REGISTRY, _register_all_searchers

    _register_all_searchers()
    assert "searxng" in _SEARCHER_REGISTRY, "_SEARCHER_REGISTRY 应含 'searxng' (替代 DuckDuckGo)"
    from src.skills.researcher.searchers import SearchRegion

    searxng_regions = _SEARCHER_REGISTRY["searxng"]["regions"]
    assert SearchRegion.CN in searxng_regions, "SearXNG 应注册到 CN 区域 (替代 DuckDuckGo)"


def test_duckduckgo_not_in_cn_searchers() -> None:
    """CN 区域搜索引擎列表不含 DuckDuckGo (运行时不调用)."""
    from src.config.settings import Settings
    from src.skills.researcher.searchers import (
        SearchRegion,
        _register_all_searchers,
        get_searchers,
    )

    _register_all_searchers()
    settings = Settings(_env_file=None)
    searchers = get_searchers(SearchRegion.CN, settings)
    names = {s.name for s in searchers}
    assert "duckduckgo" not in names, "CN 区域搜索结果不应含 DuckDuckGo (已被 SearXNG 替代)"


def test_duckduckgo_not_in_global_searchers() -> None:
    """GLOBAL 区域搜索引擎列表不含 DuckDuckGo."""
    from src.config.settings import Settings
    from src.skills.researcher.searchers import (
        SearchRegion,
        _register_all_searchers,
        get_searchers,
    )

    _register_all_searchers()
    settings = Settings(_env_file=None)
    searchers = get_searchers(SearchRegion.GLOBAL, settings)
    names = {s.name for s in searchers}
    assert "duckduckgo" not in names


def test_duckduckgo_not_in_auto_searchers() -> None:
    """AUTO 区域搜索引擎列表不含 DuckDuckGo."""
    from src.config.settings import Settings
    from src.skills.researcher.searchers import (
        SearchRegion,
        _register_all_searchers,
        get_searchers,
    )

    _register_all_searchers()
    settings = Settings(_env_file=None)
    searchers = get_searchers(SearchRegion.AUTO, settings)
    names = {s.name for s in searchers}
    assert "duckduckgo" not in names


# ========== 代码保留验证 (不删除) ==========


def test_duckduckgo_file_still_exists() -> None:
    """duckduckgo.py 文件仍存在 (代码保留, 不删除).

    策略: 注释注册而非删除文件, 保留代码以备将来恢复.
    """
    assert _DUCKDUCKGO_FILE.exists(), (
        f"duckduckgo.py 应仍存在 (代码保留策略), 路径: {_DUCKDUCKGO_FILE}"
    )


def test_duckduckgo_file_contains_class_definition() -> None:
    """duckduckgo.py 仍含 DuckDuckGoSearcher 类定义 (代码完整保留)."""
    if not _DUCKDUCKGO_FILE.exists():
        pytest.skip("duckduckgo.py 不存在")
    content = _DUCKDUCKGO_FILE.read_text(encoding="utf-8")
    assert "class DuckDuckGoSearcher" in content, (
        "duckduckgo.py 应仍含 'class DuckDuckGoSearcher' 定义 (代码完整保留)"
    )


def test_free_quota_map_retains_duckduckgo() -> None:
    """FREE_QUOTA_MAP 仍含 "duckduckgo" 条目 (代码保留, 不影响功能).

    FREE_QUOTA_MAP 是配置常量, 保留 duckduckgo 条目不影响运行时
    (因 _SEARCHER_REGISTRY 已不含 duckduckgo, 不会被查询到).
    """
    from src.skills.researcher.searchers import FREE_QUOTA_MAP

    assert "duckduckgo" in FREE_QUOTA_MAP, "FREE_QUOTA_MAP 应仍含 'duckduckgo' 条目 (代码保留策略)"
    assert FREE_QUOTA_MAP["duckduckgo"] == "unlimited"


# ========== __init__.py 注释验证 ==========


def test_duckduckgo_import_commented_out() -> None:
    """__init__.py 中 DuckDuckGoSearcher import 已注释 (不执行 import)."""
    if not _SEARCHERS_INIT.exists():
        pytest.skip("searchers/__init__.py 不存在")
    content = _SEARCHERS_INIT.read_text(encoding="utf-8")
    # 查找注释的 import 行 (以 # 开头, 含 DuckDuckGoSearcher)
    import_lines = [
        line.strip()
        for line in content.splitlines()
        if "DuckDuckGoSearcher" in line and "import" in line
    ]
    assert len(import_lines) >= 1, "__init__.py 应含 DuckDuckGoSearcher import 行 (已注释)"
    # 所有含 import 的行都应是注释 (# 开头)
    for line in import_lines:
        assert line.startswith("#"), f"DuckDuckGoSearcher import 行应已注释, 实际: {line}"


def test_duckduckgo_registry_block_commented_out() -> None:
    """__init__.py 中 duckduckgo 注册块已注释 (_SEARCHER_REGISTRY["duckduckgo"] = ...)."""
    if not _SEARCHERS_INIT.exists():
        pytest.skip("searchers/__init__.py 不存在")
    content = _SEARCHERS_INIT.read_text(encoding="utf-8")
    # 查找注册行 (含 _SEARCHER_REGISTRY["duckduckgo"])
    registry_lines = [
        line.strip() for line in content.splitlines() if '_SEARCHER_REGISTRY["duckduckgo"]' in line
    ]
    assert len(registry_lines) >= 1, (
        "__init__.py 应含 _SEARCHER_REGISTRY['duckduckgo'] 注册行 (已注释)"
    )
    # 所有注册行都应是注释 (# 开头)
    for line in registry_lines:
        assert line.startswith("#"), f"duckduckgo 注册行应已注释, 实际: {line}"


def test_searchers_init_contains_replacement_comment() -> None:
    """__init__.py 含 DuckDuckGo 已被 SearXNG 替代的说明注释."""
    if not _SEARCHERS_INIT.exists():
        pytest.skip("searchers/__init__.py 不存在")
    content = _SEARCHERS_INIT.read_text(encoding="utf-8")
    # 应含替代说明 (中文或英文)
    assert ("SearXNG" in content and "替代" in content) or "replaced" in content.lower(), (
        "__init__.py 应含 'DuckDuckGo 已被 SearXNG 替代' 的说明注释"
    )


# ========== 端到端契约验证 ==========


def test_no_active_duckduckgo_calls_in_source() -> None:
    """src/ 目录下无活跃的 DuckDuckGo 调用 (仅注释或字符串字面量).

    检查所有 .py 文件, 确保没有 DuckDuckGoSearcher() 实例化或
    duckduckgo searcher 的活跃调用 (注释行除外).
    """
    src_dir = _PROJECT_ROOT / "src"
    if not src_dir.exists():
        pytest.skip("src/ 目录不存在")
    active_calls: list[str] = []
    for py_file in src_dir.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_num, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            # 跳过注释行
            if stripped.startswith("#"):
                continue
            # 跳过字符串字面量 (docstring/string)
            if stripped.startswith('"') or stripped.startswith("'"):
                continue
            # 检查活跃的 DuckDuckGoSearcher 实例化
            if "DuckDuckGoSearcher()" in stripped:
                active_calls.append(f"{py_file.relative_to(_PROJECT_ROOT)}:{line_num}: {stripped}")
    assert not active_calls, (
        f"src/ 目录下发现 {len(active_calls)} 处活跃 DuckDuckGoSearcher 调用 "
        f"(应已全部注释): {active_calls}"
    )
