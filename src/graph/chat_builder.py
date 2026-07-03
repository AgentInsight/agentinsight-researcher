"""Chat Graph 构建器 (P2-Future-03).

AGENTS.md 第 5 章: LangGraph StateGraph 唯一编排, 节点纯函数, 显式条件边.
单节点 chat 图, 复用同一 PostgresSaver (同 thread_id 隔离).

图结构:
    START → chat → END

集成:
    routes.py 检测追问 vs 新研究:
    - has_report 且无 report_type → 走 chat graph (追问模式)
    - 否则 → 走 researcher graph (新研究)

复用同一 PostgresSaver (同 thread_id 隔离), 支持多会话并发.
追问模式依赖 checkpointer 自动加载会话历史 (含 report_md / messages / agent_role),
禁止客户端自造 thread_id (AGENTS.md 第 6 章).
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

from langgraph.graph import END, StateGraph

from src.config.settings import Settings, get_settings
from src.graph.state import ResearcherState

logger = logging.getLogger(__name__)


async def build_chat_graph(
    settings: Settings | None = None,
    *,
    use_checkpointer: bool = True,
) -> Any:
    """构建单节点对话追问图 (P2-Future-03).

    图结构:
        START → chat → END

    AGENTS.md 第 5 章: 生产 StateGraph 必须挂 PostgresSaver (同 thread_id 隔离).
    AGENTS.md 第 6 章: 会话级数据通过 Checkpointer 隔离, thread_id 从请求上下文注入.

    Args:
        settings: 全局配置 (None 时用 get_settings())
        use_checkpointer: 是否挂 Checkpointer (生产必须 True)

    Returns:
        已编译的 StateGraph 单例
    """
    settings = settings or get_settings()

    # 延迟导入节点实现 (避免循环导入)
    from src.agents.researcher.chat_agent import chat_node

    graph = StateGraph(ResearcherState)

    # 单节点: chat (ChatAgent 对话追问)
    graph.add_node("chat", partial(chat_node, settings=settings))

    # 入口与终止
    graph.set_entry_point("chat")
    graph.add_edge("chat", END)

    if use_checkpointer:
        from src.memory.checkpointer import get_checkpointer

        checkpointer = await get_checkpointer(settings)
        compiled = graph.compile(checkpointer=checkpointer)
    else:
        compiled = graph.compile()

    logger.info("Chat graph 已构建 (单节点, P2-Future-03)")
    return compiled
