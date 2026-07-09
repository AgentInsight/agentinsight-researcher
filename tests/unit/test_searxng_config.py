"""单元测试: SearXNG keep_only 模式配置验证.

验证 SearXNG 元搜索引擎配置的正确性:
1. use_default_settings.engines.keep_only 模式已启用
2. 指定 23 个引擎全部在 keep_only 列表中 (国内 14 + 国外 9)
3. google / duckduckgo 不在 keep_only 列表 (从根源排除)
4. bing 系列强制使用 cn.bing.com (中国可访问镜像)
5. secret_key 已配置 (容器间通信鉴权)
6. server.port == 8099 (项目硬约束, 非 8080)
7. limiter: false (无 redis, 不开启限流)

AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
项目硬约束: SearXNG 服务端口 8099 (非 8080), SEARXNG_PORT 环境变量覆盖 settings.yml.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SEARXNG_SETTINGS = _PROJECT_ROOT / "config" / "searxng" / "settings.yml"

# 用户指定的 23 个保留引擎 (国内 14 + 国外 9)
_EXPECTED_ENGINES = {
    # 国内引擎 (14 个)
    "baidu",
    "baidu images",
    "bing",
    "bing images",
    "bing videos",
    "sogou",
    "sogou images",
    "sogou videos",
    "sogou wechat",
    "360search",
    "360search videos",
    "quark",
    "quark images",
    # 国外可用引擎 (9 个, 与国内 14 个合计 23 个)
    "arxiv",
    "pubmed",
    "crossref",
    "mwmbl",
    "github",
    "yandex",
    "mojeek",
    "stackoverflow",
    "npm",
    "crates.io",
}

# 必须被排除的引擎 (keep_only 模式下不应出现)
_FORBIDDEN_ENGINES = {
    "google",
    "google images",
    "google news",
    "google videos",
    "duckduckgo",
    "brave",
    "startpage",
    "wikipedia",
}


def _load_searxng_settings() -> dict:
    """加载 SearXNG settings.yml, 文件不存在时跳过测试."""
    if not _SEARXNG_SETTINGS.exists():
        pytest.skip("config/searxng/settings.yml 不存在")
    try:
        import yaml
    except ImportError:
        pytest.skip("PyYAML 未安装, 跳过 YAML 结构化检查")
    with _SEARXNG_SETTINGS.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


# ========== keep_only 模式验证 ==========


def test_keep_only_mode_enabled() -> None:
    """use_default_settings.engines.keep_only 模式已启用."""
    settings = _load_searxng_settings()
    use_default = settings.get("use_default_settings", {})
    engines_config = use_default.get("engines", {})
    assert "keep_only" in engines_config, (
        "use_default_settings.engines 应含 keep_only 列表 "
        "(从根源排除 google/duckduckgo 等不可用引擎)"
    )
    keep_only = engines_config["keep_only"]
    assert isinstance(keep_only, list), "keep_only 应为列表"
    assert len(keep_only) >= 20, f"keep_only 应至少含 20 个引擎, 实际: {len(keep_only)}"


def test_all_expected_engines_in_keep_only() -> None:
    """用户指定的 23 个引擎全部在 keep_only 列表中."""
    settings = _load_searxng_settings()
    keep_only = settings["use_default_settings"]["engines"]["keep_only"]
    # keep_only 可能是嵌套列表 (YAML 多行列表), 展平后比较
    flat_keep_only: set[str] = set()
    for item in keep_only:
        if isinstance(item, str):
            flat_keep_only.add(item.strip())
        elif isinstance(item, list):
            for sub_item in item:
                if isinstance(sub_item, str):
                    flat_keep_only.add(sub_item.strip())
    missing = _EXPECTED_ENGINES - flat_keep_only
    assert not missing, (
        f"keep_only 缺少 {len(missing)} 个引擎: {sorted(missing)}"
    )


def test_google_not_in_keep_only() -> None:
    """google 系列引擎不在 keep_only 列表 (从根源排除).

    用户需求: '最终确保 google 不会被启用, 现在 google 都会被调用'.
    keep_only 模式下, 未列入的引擎不会加载, google 自然不会被调用.
    """
    settings = _load_searxng_settings()
    keep_only = settings["use_default_settings"]["engines"]["keep_only"]
    flat_keep_only: set[str] = set()
    for item in keep_only:
        if isinstance(item, str):
            flat_keep_only.add(item.strip().lower())
        elif isinstance(item, list):
            for sub_item in item:
                if isinstance(sub_item, str):
                    flat_keep_only.add(sub_item.strip().lower())
    google_engines = {e for e in _FORBIDDEN_ENGINES if "google" in e}
    found_google = google_engines & flat_keep_only
    assert not found_google, (
        f"keep_only 不应含 google 系列引擎 (用户硬约束), 发现: {found_google}"
    )


def test_duckduckgo_not_in_keep_only() -> None:
    """duckduckgo 不在 keep_only 列表 (已被 SearXNG 替代)."""
    settings = _load_searxng_settings()
    keep_only = settings["use_default_settings"]["engines"]["keep_only"]
    flat_keep_only: set[str] = set()
    for item in keep_only:
        if isinstance(item, str):
            flat_keep_only.add(item.strip().lower())
        elif isinstance(item, list):
            for sub_item in item:
                if isinstance(sub_item, str):
                    flat_keep_only.add(sub_item.strip().lower())
    assert "duckduckgo" not in flat_keep_only, (
        "keep_only 不应含 duckduckgo (已被 SearXNG 替代, 项目已移除调用)"
    )


def test_forbidden_engines_not_in_keep_only() -> None:
    """不可用引擎 (google/duckduckgo/brave/startpage/wikipedia) 均不在 keep_only."""
    settings = _load_searxng_settings()
    keep_only = settings["use_default_settings"]["engines"]["keep_only"]
    flat_keep_only: set[str] = set()
    for item in keep_only:
        if isinstance(item, str):
            flat_keep_only.add(item.strip().lower())
        elif isinstance(item, list):
            for sub_item in item:
                if isinstance(sub_item, str):
                    flat_keep_only.add(sub_item.strip().lower())
    found_forbidden = _FORBIDDEN_ENGINES & flat_keep_only
    assert not found_forbidden, (
        f"keep_only 不应含不可用引擎, 发现: {sorted(found_forbidden)}"
    )


# ========== bing cn.bing.com 镜像验证 ==========


def test_bing_uses_cn_bing_com() -> None:
    """bing 引擎强制使用 cn.bing.com (中国可访问镜像).

    项目硬约束: SearXNG bing 引擎必须使用 base_url: https://cn.bing.com
    (默认国际 bing.com 在中国不可访问).
    """
    settings = _load_searxng_settings()
    engines = settings.get("engines", [])
    bing_engines = [
        e for e in engines if e.get("name", "").lower().startswith("bing")
    ]
    assert len(bing_engines) >= 3, (
        f"应至少含 3 个 bing 系列引擎 (bing/bing images/bing videos), "
        f"实际: {len(bing_engines)}"
    )
    for bing in bing_engines:
        base_url = bing.get("base_url", "")
        assert "cn.bing.com" in base_url, (
            f"bing 引擎 '{bing.get('name')}' 应使用 cn.bing.com, "
            f"实际 base_url: {base_url}"
        )


def test_bing_engines_enabled() -> None:
    """bing/bing images/bing videos 三个引擎均 disabled: false."""
    settings = _load_searxng_settings()
    engines = settings.get("engines", [])
    for name in ("bing", "bing images", "bing videos"):
        bing_engine = next((e for e in engines if e.get("name") == name), None)
        assert bing_engine is not None, f"应含 '{name}' 引擎配置"
        assert bing_engine.get("disabled") is False, (
            f"'{name}' 应 disabled: false (国内可用引擎)"
        )


# ========== server 配置验证 ==========


def test_server_port_is_8099() -> None:
    """server.port == 8099 (项目硬约束, 非 8080).

    项目记忆: SearXNG 服务端口 8099 (非 8080); SEARXNG_PORT 环境变量
    覆盖 settings.yml 的 server.port.
    """
    settings = _load_searxng_settings()
    server = settings.get("server", {})
    assert server.get("port") == 8099, (
        f"server.port 应为 8099 (项目硬约束), 实际: {server.get('port')}"
    )


def test_secret_key_configured() -> None:
    """server.secret_key 已配置 (非空, 容器间通信鉴权)."""
    settings = _load_searxng_settings()
    server = settings.get("server", {})
    secret_key = server.get("secret_key", "")
    assert secret_key, "server.secret_key 不应为空 (容器间通信鉴权)"
    assert len(secret_key) >= 32, (
        f"server.secret_key 长度应 >= 32 (安全强度), 实际: {len(secret_key)}"
    )


def test_limiter_disabled() -> None:
    """server.limiter: false (无 redis, 不开启限流).

    项目配置: 无 redis (limiter=false, 无限流数据需持久化).
    """
    settings = _load_searxng_settings()
    server = settings.get("server", {})
    assert server.get("limiter") is False, (
        f"server.limiter 应为 false (无 redis), 实际: {server.get('limiter')}"
    )


# ========== 引擎显式启用验证 ==========


def test_all_expected_engines_explicitly_enabled() -> None:
    """keep_only 中的 23 个引擎在 engines 段均显式 disabled: false."""
    settings = _load_searxng_settings()
    engines = settings.get("engines", [])
    engines_by_name = {e.get("name"): e for e in engines if isinstance(e, dict)}
    missing_explicit = []
    disabled_engines = []
    for name in _EXPECTED_ENGINES:
        if name not in engines_by_name:
            missing_explicit.append(name)
        elif engines_by_name[name].get("disabled") is not False:
            disabled_engines.append(name)
    assert not missing_explicit, (
        f"engines 段缺少 {len(missing_explicit)} 个引擎的显式配置: "
        f"{sorted(missing_explicit)}"
    )
    assert not disabled_engines, (
        f"engines 段 {len(disabled_engines)} 个引擎未显式 disabled: false: "
        f"{sorted(disabled_engines)}"
    )


def test_no_google_in_engines_section() -> None:
    """engines 段不含 google 引擎配置 (keep_only 已排除)."""
    settings = _load_searxng_settings()
    engines = settings.get("engines", [])
    google_engines = [
        e for e in engines if isinstance(e, dict) and "google" in e.get("name", "").lower()
    ]
    assert not google_engines, (
        f"engines 段不应含 google 引擎 (keep_only 已排除), 发现: "
        f"{[e.get('name') for e in google_engines]}"
    )


def test_no_duckduckgo_in_engines_section() -> None:
    """engines 段不含 duckduckgo 引擎配置."""
    settings = _load_searxng_settings()
    engines = settings.get("engines", [])
    ddg_engines = [
        e
        for e in engines
        if isinstance(e, dict) and "duckduckgo" in e.get("name", "").lower()
    ]
    assert not ddg_engines, (
        f"engines 段不应含 duckduckgo 引擎, 发现: "
        f"{[e.get('name') for e in ddg_engines]}"
    )


# ========== search 配置验证 ==========


def test_search_default_lang_zh_cn() -> None:
    """search.default_lang == 'zh-CN' (中文优先)."""
    settings = _load_searxng_settings()
    search = settings.get("search", {})
    assert search.get("default_lang") == "zh-CN", (
        f"search.default_lang 应为 'zh-CN', 实际: {search.get('default_lang')}"
    )


def test_search_formats_includes_json() -> None:
    """search.formats 含 json (API 调用需要)."""
    settings = _load_searxng_settings()
    search = settings.get("search", {})
    formats = search.get("formats", [])
    assert "json" in formats, (
        f"search.formats 应含 'json' (API 调用), 实际: {formats}"
    )


# ========== 引擎总数验证 ==========


def test_keep_only_engine_count_matches() -> None:
    """keep_only 列表引擎总数与用户指定一致 (23 个)."""
    settings = _load_searxng_settings()
    keep_only = settings["use_default_settings"]["engines"]["keep_only"]
    flat_keep_only: set[str] = set()
    for item in keep_only:
        if isinstance(item, str):
            flat_keep_only.add(item.strip())
        elif isinstance(item, list):
            for sub_item in item:
                if isinstance(sub_item, str):
                    flat_keep_only.add(sub_item.strip())
    assert len(flat_keep_only) >= 23, (
        f"keep_only 引擎总数应 >= 23 (用户指定), 实际: {len(flat_keep_only)}"
    )
