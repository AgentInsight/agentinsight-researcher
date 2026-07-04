"""AG2 框架研究任务入口.

对标 GPT Researcher multi_agents_ag2/main.py.
创建 AG2Orchestrator 并执行研究任务.

AG2 默认关闭 (settings.ag2_enabled=False), 启用方式:
1. 安装可选依赖: pip install ag2 (或 pip install pyautogen)
2. 设置环境变量 AG2_ENABLED=true

启用后可用 AG2 替代 LangGraph 作为多 Agent 编排框架, 现有 LangGraph 代码不受影响.
"""

from __future__ import annotations

import logging
from typing import Any

from src.agents.ag2.orchestrator import _AG2_AVAILABLE, AG2Orchestrator
from src.config.settings import get_settings

logger = logging.getLogger(__name__)


async def run_research_task(query: str, **kwargs: Any) -> dict[str, Any]:
    """AG2 框架研究任务入口.

    对标 GPT Researcher multi_agents_ag2/main.py 的 run_research_task.

    创建 AG2Orchestrator, 用 GroupChat 编排 4 个角色 (Researcher / Writer / Reviewer /
    Publisher) 完成研究任务. 所有 LLM 调用经 LLMClient (LiteLLM), 复用现有 Skill 组件.

    Args:
        query: 研究查询字符串.
        **kwargs: 传递给 AG2Orchestrator.run 的关键字参数:
            - agent_role (str | None): 角色 persona (对标 GPTR AGENT_ROLE)
            - user_id (str | None): 用户 ID (隔离键, AGENTS.md 第 8 章)
            - session_id (str | None): 会话 ID (隔离键, AGENTS.md 第 6 章)
            - report_type (str): 报告类型, 默认 "basic_report"
            - report_format (str): 输出格式, 默认 "markdown"
            - tone (str): 语气, 默认 "objective"

    Returns:
        研究结果 dict, 含以下字段:
        - query (str): 研究查询
        - contexts (list[str]): 检索到的上下文列表
        - sources (list[dict]): 引用来源列表
        - report_md (str): Markdown 研究报告
        - review_decision (str): 审核决策 ("accept" | "revise")
        - review_feedback (str): 审核反馈
        - published (dict | None): 发布结果

    Raises:
        RuntimeError: ag2_enabled=False 时抛出 (需设置 AG2_ENABLED=true).
        ImportError: AG2 (autogen) 未安装时抛出 (需 pip install ag2).
    """
    settings = get_settings()

    if not settings.ag2_enabled:
        raise RuntimeError("AG2 框架未启用, 请设置 AG2_ENABLED=true 并安装 ag2: pip install ag2")

    if not _AG2_AVAILABLE:
        raise ImportError("AG2 (autogen) 未安装, 请执行 pip install ag2 或 pip install pyautogen")

    logger.info("AG2 框架启动研究任务: query=%s", query[:100])

    orchestrator = AG2Orchestrator(settings=settings)
    return await orchestrator.run(query, **kwargs)
