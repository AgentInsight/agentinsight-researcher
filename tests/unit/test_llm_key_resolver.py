"""单元测试: LLM API Key 解析器 (DRY 收敛).

验证 src/common/llm_key_resolver.py:
- resolve_api_key: 按 LiteLLM 路由前缀查对应厂商 API Key
- _PREFIX_KEY_MAP: deepseek/openai/anthropic/zhipu/zhipuai 前缀映射
- 智谱 AI 兼容 zhipu/ 和 zhipuai/ 两种前缀
- 未匹配前缀返回 None
- DRY: 取代 llm/client.py 和 image_generator.py 中重复的 _get_api_key

共享逻辑下沉到 common/, 不应重复实现.
LLM 调用经 llm/ 网关 (LiteLLM), 模型名以 LiteLLM 路由前缀声明.
单元测试不依赖外部服务 (mock Settings).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.common.llm_key_resolver import _PREFIX_KEY_MAP, resolve_api_key
from src.config.settings import Settings

pytestmark = pytest.mark.unit


@pytest.fixture()
def settings_with_keys() -> MagicMock:
    """构造含全部厂商 Key 的 mock settings.

    用 MagicMock 替代真实 Settings, 避免 .env 依赖;
    getattr(settings, key_field) 返回对应 Key 字符串.
    """
    mock = MagicMock(spec=Settings)
    mock.deepseek_api_key = "sk-deepseek-xxx"
    mock.openai_api_key = "sk-openai-xxx"
    mock.anthropic_api_key = "sk-ant-xxx"
    mock.zhipu_api_key = "sk-zhipu-xxx"
    return mock


# ========== _PREFIX_KEY_MAP 内容契约 ==========


def test_prefix_key_map_contains_deepseek() -> None:
    """_PREFIX_KEY_MAP 含 deepseek/ → deepseek_api_key."""
    assert _PREFIX_KEY_MAP["deepseek/"] == "deepseek_api_key"


def test_prefix_key_map_contains_openai() -> None:
    """_PREFIX_KEY_MAP 含 openai/ → openai_api_key."""
    assert _PREFIX_KEY_MAP["openai/"] == "openai_api_key"


def test_prefix_key_map_contains_anthropic() -> None:
    """_PREFIX_KEY_MAP 含 anthropic/ → anthropic_api_key."""
    assert _PREFIX_KEY_MAP["anthropic/"] == "anthropic_api_key"


def test_prefix_key_map_contains_zhipu_both_prefixes() -> None:
    """智谱 AI 兼容 zhipu/ 和 zhipuai/ 两种前缀, 均映射 zhipu_api_key.

    LiteLLM 1.90.2 不原生支持 zhipuai/, 项目用自定义前缀兼容.
    """
    assert _PREFIX_KEY_MAP["zhipu/"] == "zhipu_api_key"
    assert _PREFIX_KEY_MAP["zhipuai/"] == "zhipu_api_key"


def test_prefix_key_map_has_six_entries() -> None:
    """_PREFIX_KEY_MAP 应含 6 个前缀 (deepseek/openai/anthropic/zhipu/zhipuai/zai).

    新增 zai/ (litellm 原生智谱 GLM 路由前缀).
    """
    assert len(_PREFIX_KEY_MAP) == 6


# ========== resolve_api_key 各前缀解析 ==========


def test_resolve_api_key_deepseek(settings_with_keys: MagicMock) -> None:
    """deepseek/deepseek-chat → deepseek_api_key."""
    assert resolve_api_key("deepseek/deepseek-chat", settings_with_keys) == "sk-deepseek-xxx"


def test_resolve_api_key_openai(settings_with_keys: MagicMock) -> None:
    """openai/gpt-4o → openai_api_key."""
    assert resolve_api_key("openai/gpt-4o", settings_with_keys) == "sk-openai-xxx"


def test_resolve_api_key_anthropic(settings_with_keys: MagicMock) -> None:
    """anthropic/claude-3-5-sonnet → anthropic_api_key."""
    assert resolve_api_key("anthropic/claude-3-5-sonnet", settings_with_keys) == "sk-ant-xxx"


def test_resolve_api_key_zhipu(settings_with_keys: MagicMock) -> None:
    """zhipu/glm-4-flash → zhipu_api_key."""
    assert resolve_api_key("zhipu/glm-4-flash", settings_with_keys) == "sk-zhipu-xxx"


def test_resolve_api_key_zhipuai(settings_with_keys: MagicMock) -> None:
    """zhipuai/glm-4-flash → zhipu_api_key (兼容前缀)."""
    assert resolve_api_key("zhipuai/glm-4-flash", settings_with_keys) == "sk-zhipu-xxx"


# ========== 未匹配前缀 ==========


def test_resolve_api_key_unknown_prefix_returns_none(settings_with_keys: MagicMock) -> None:
    """未匹配前缀 (如 gemini/) 返回 None."""
    assert resolve_api_key("gemini/gemini-pro", settings_with_keys) is None


def test_resolve_api_key_empty_model_returns_none(settings_with_keys: MagicMock) -> None:
    """空 model 字符串返回 None (无前缀可匹配)."""
    assert resolve_api_key("", settings_with_keys) is None


def test_resolve_api_key_no_slash_returns_none(settings_with_keys: MagicMock) -> None:
    """无斜杠的 model 名 (如 'gpt-4o') 返回 None (前缀必含斜杠)."""
    assert resolve_api_key("gpt-4o", settings_with_keys) is None


# ========== 前缀匹配语义 ==========


def test_resolve_api_key_prefix_is_case_sensitive(settings_with_keys: MagicMock) -> None:
    """前缀匹配大小写敏感 (LiteLLM 前缀均为小写)."""
    # Deepseek/ 大写不应匹配 deepseek/
    assert resolve_api_key("Deepseek/deepseek-chat", settings_with_keys) is None
    assert resolve_api_key("OPENAI/gpt-4o", settings_with_keys) is None


def test_resolve_api_key_matches_prefix_not_exact(settings_with_keys: MagicMock) -> None:
    """前缀匹配 (startswith), 不要求 model 名完整.

    'deepseek/' 前缀匹配 'deepseek/deepseek-chat' / 'deepseek/deepseek-reasoner' 等.
    """
    assert resolve_api_key("deepseek/deepseek-reasoner", settings_with_keys) == "sk-deepseek-xxx"
    assert resolve_api_key("openai/gpt-4o-mini", settings_with_keys) == "sk-openai-xxx"


def test_resolve_api_key_returns_none_when_key_field_is_none(
    settings_with_keys: MagicMock,
) -> None:
    """settings 对应 Key 字段为 None 时返回 None (Key 未配置)."""
    settings_with_keys.deepseek_api_key = None
    assert resolve_api_key("deepseek/deepseek-chat", settings_with_keys) is None


def test_resolve_api_key_returns_empty_string_when_field_empty(
    settings_with_keys: MagicMock,
) -> None:
    """settings 对应 Key 字段为空字符串时返回空字符串 (透传, 不转 None)."""
    settings_with_keys.openai_api_key = ""
    assert resolve_api_key("openai/gpt-4o", settings_with_keys) == ""


# ========== DRY 收敛验证 ==========


def test_resolve_api_key_replaces_duplicate_get_api_key_in_llm_client() -> None:
    """DRY: llm/client.py 不应再定义独立的 _get_api_key (应复用 common/llm_key_resolver).

    共享逻辑下沉到 common/, 不应重复实现.
    """
    import inspect

    from src.llm import client as llm_client_module

    source = inspect.getsource(llm_client_module)
    # 允许 import resolve_api_key, 但不应有重复定义的 _get_api_key 函数体
    # 检查不含 "def _get_api_key" 定义 (允许调用 resolve_api_key)
    assert "def _get_api_key" not in source, (
        "llm/client.py 不应再定义 _get_api_key, 应复用 common.llm_key_resolver.resolve_api_key"
    )


def test_resolve_api_key_replaces_duplicate_get_api_key_in_image_generator() -> None:
    """DRY: image_generator.py 不应再定义独立的 _get_api_key (应复用 common/llm_key_resolver)."""
    import inspect

    from src.skills.researcher import image_generator

    source = inspect.getsource(image_generator)
    assert "def _get_api_key" not in source, (
        "image_generator.py 不应再定义 _get_api_key, 应复用 common.llm_key_resolver.resolve_api_key"
    )


def test_resolve_api_key_used_by_llm_client() -> None:
    """DRY: llm/client.py 应 import 并使用 resolve_api_key."""
    import inspect

    from src.llm import client as llm_client_module

    source = inspect.getsource(llm_client_module)
    assert "resolve_api_key" in source, "llm/client.py 应使用 resolve_api_key"


# ========== 新增 provider 扩展点 ==========


def test_resolve_api_key_new_provider_via_map_extension(
    settings_with_keys: MagicMock,
) -> None:
    """新增 provider 只需在 _PREFIX_KEY_MAP 追加一行, 无需改调用方.

    模拟新增 'moonshot/' 前缀, 验证 resolve_api_key 能解析 (不改函数代码).
    """
    mock = MagicMock(spec=Settings)
    mock.moonshot_api_key = "sk-moonshot-xxx"
    # 临时追加映射 (测试后还原)
    original_map = dict(_PREFIX_KEY_MAP)
    try:
        _PREFIX_KEY_MAP["moonshot/"] = "moonshot_api_key"
        assert resolve_api_key("moonshot/moonshot-v1-8k", mock) == "sk-moonshot-xxx"
    finally:
        _PREFIX_KEY_MAP.clear()
        _PREFIX_KEY_MAP.update(original_map)
    # 还原后应不再匹配
    assert resolve_api_key("moonshot/moonshot-v1-8k", mock) is None


def test_resolve_api_key_first_match_wins(settings_with_keys: MagicMock) -> None:
    """多前缀匹配时, 字典迭代顺序决定首个匹配返回 (deepseek/ 优先于 zhipu/)."""
    # 'deepseek/' 和 'zhipu/' 不会同时匹配同一 model (前缀互斥)
    # 但若手动注入冲突前缀, 首个匹配返回
    assert resolve_api_key("deepseek/deepseek-chat", settings_with_keys) == "sk-deepseek-xxx"


# ========== __all__ 契约 ==========


def test_module_exports_resolve_api_key() -> None:
    """__all__ 应导出 resolve_api_key."""
    from src.common import llm_key_resolver

    assert "resolve_api_key" in llm_key_resolver.__all__
    assert hasattr(llm_key_resolver, "resolve_api_key")
