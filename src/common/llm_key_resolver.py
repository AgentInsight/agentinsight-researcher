"""LLM API Key 解析器.

AGENTS.md 第 3 章: 共享逻辑下沉到 common/, 不应重复实现.
AGENTS.md 第 9 章: LLM 调用经 llm/ 网关 (LiteLLM), 模型名以 LiteLLM 路由前缀声明.

抽取自 llm/client.py 和 skills/researcher/image_generator.py 中重复的
_get_api_key 逻辑 (按 LiteLLM 路由前缀查对应厂商 API Key).

LiteLLM 路由前缀 → Settings API Key 字段名映射, 新增 provider 只改本表.
"""

from __future__ import annotations

from typing import cast

from src.config.settings import Settings

# LiteLLM 路由前缀 → Settings API Key 字段名映射
# 新增 provider 只需在此追加一行, 无需改调用方
_PREFIX_KEY_MAP: dict[str, str] = {
    "deepseek/": "deepseek_api_key",
    "openai/": "openai_api_key",
    "anthropic/": "anthropic_api_key",
    # 智谱 AI: 项目配置用 zhipuai/ 前缀, litellm 1.83.7 原生支持 zai/ 路由
    # _adapt_zhipu 会将 zhipuai/ 适配为 zai/ (litellm 原生路由), resolve_api_key 用原始前缀
    "zhipu/": "zhipu_api_key",
    "zhipuai/": "zhipu_api_key",
    "zai/": "zhipu_api_key",  # litellm 原生 zai/ 路由前缀 (智谱 GLM)
}


def resolve_api_key(model: str, settings: Settings) -> str | None:
    """按 LiteLLM 路由前缀查对应厂商 API Key.

    取代 llm/client.py 和 image_generator.py 中重复的 _get_api_key 实现.

    Args:
        model: LiteLLM 路由前缀模型名 (如 "deepseek/deepseek-chat")
        settings: 全局配置 (SSOT)

    Returns:
        对应厂商的 API Key; 未匹配前缀返回 None
    """
    for prefix, key_field in _PREFIX_KEY_MAP.items():
        if model.startswith(prefix):
            return cast(str | None, getattr(settings, key_field))
    return None


__all__ = ["resolve_api_key"]
