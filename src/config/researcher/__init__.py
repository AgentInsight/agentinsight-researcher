"""闲聊响应配置层 (P1-Future-07).

AGENTS.md 第 1/3 章: config/<agent_name>/ 子智能体专属配置.
闲聊响应配置集中在本包内, 包括:
- PersonaConfig: 闲聊 Persona (身份/语气/边界/签名)
- ChitchatConfigBundle: YAML + Jinja2 统一加载器
- get_chitchat_config(): 全局单例入口

对标 Rasa FallbackClassifier / Dify 失效回复 / NeMo topic rail.
"""

from __future__ import annotations

from src.config.researcher.loader import ChitchatConfigBundle, get_chitchat_config
from src.config.researcher.persona import PersonaConfig

__all__ = [
    "ChitchatConfigBundle",
    "PersonaConfig",
    "get_chitchat_config",
]
