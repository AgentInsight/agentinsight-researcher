"""RAGAS 兼容性 shim (修复 langchain_community 0.4+ 移除 vertexai 模块问题).

RAGAS 0.2.15 的 ragas/llms/base.py 尝试从 langchain_community 导入:
    from langchain_community.chat_models.vertexai import ChatVertexAI
    from langchain_community.llms import VertexAI

但 langchain_community 0.4+ 已移除这些模块 (迁移到 langchain-google-vertexai).
本模块在 import ragas 前注入 fake 模块, 避免 ImportError.

用法: 在 import ragas 之前 import evals.rag._compat_shim
"""

from __future__ import annotations

import sys
import types


def _install_shim() -> None:
    """注入 fake vertexai 模块到 langchain_community.chat_models."""
    try:
        import langchain_community.chat_models as cm  # noqa: F401
    except ImportError:
        return  # langchain_community 未安装, 无需 shim

    # 检查是否已存在 vertexai 模块
    if hasattr(cm, "vertexai"):
        return  # 模块已存在, 无需 shim

    # 创建 fake vertexai 模块
    fake_module = types.ModuleType("langchain_community.chat_models.vertexai")
    fake_module.ChatVertexAI = type("ChatVertexAI", (), {})  # 占位类
    sys.modules["langchain_community.chat_models.vertexai"] = fake_module
    cm.vertexai = fake_module

    # 检查 langchain_community.llms.VertexAI
    try:
        from langchain_community import llms

        if not hasattr(llms, "VertexAI"):
            llms.VertexAI = type("VertexAI", (), {})  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        pass


_install_shim()
