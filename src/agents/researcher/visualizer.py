"""Visualizer 可视化 Agent (P2-Future-01).

AGENTS.md 第 5 章: LangGraph StateGraph 唯一编排, 节点纯函数.
对标 GPT Researcher multi_agents/agents/visualizer.py.

Visualizer 职责:
- 根据报告内容生成 Mermaid 流程图/架构图/思维导图
- 提取 ```mermaid 围栏内的代码, 插入报告 H1 标题后
- 用 LLMClient tier=FAST 调用 (生成 Mermaid 代码不需要复杂推理)
- 用 trace_chain 包裹 (AGENTS.md 第 10 章, 禁 agentinsight.observe 装饰器)
- 用 PromptFamily.visualizer_prompt 注入 prompt (P1-Future-04 策略模式)

插入位置 (multi_agent_builder):
    reviewer accept → visualizer → publisher
这样可视化基于已通过评审的最终报告, 避免修订后图表过时.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.config.settings import Settings, get_settings
from src.graph.state import ResearcherState
from src.llm.client import LLMClient, LLMTier
from src.observability.tracing import trace_chain
from src.skills.researcher.prompts import PromptFamily, get_prompt_family

logger = logging.getLogger(__name__)

# 匹配 ```mermaid ... ``` 围栏 (DOTALL 使 . 跨行)
_MERMAID_FENCE_RE = re.compile(r"```mermaid\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)


class Visualizer:
    """可视化 Agent (P2-Future-01).

    用 fast_llm 生成 Mermaid 图表代码, 插入报告 H1 标题后.
    对标 GPT Researcher visualizer 角色 + Mermaid 围栏提取.
    """

    settings: Settings
    _llm: LLMClient
    _prompt_family: PromptFamily

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        prompt_family: PromptFamily | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or LLMClient(self.settings)
        self._prompt_family = prompt_family or get_prompt_family(self.settings.prompt_family)

    async def visualize(
        self,
        state: ResearcherState,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """生成 Mermaid 图表并插入报告.

        Args:
            state: 研究状态, 含 report_md / query
            user_id: 用户 ID (隔离键, AGENTS.md 第 8 章)
            session_id: 会话 ID (隔离键, AGENTS.md 第 6 章)

        Returns:
            {"report_md": str} 插入图表后的报告; 失败时返回原报告.
        """
        async with trace_chain(
            name="visualizer",
            input={
                "query": state.get("query", "")[:100],
                "report_len": len(state.get("report_md", "")),
            },
            user_id=user_id,
            session_id=session_id,
        ) as span:
            report_md = state.get("report_md", "")
            if not report_md:
                span.update(
                    output={"error": "empty_report"},
                    metadata={"error": "report_md 为空"},
                )
                return {"report_md": ""}

            query = state.get("query", "")

            # P1-Future-04: prompt 经 PromptFamily 策略注入
            prompt = self._prompt_family.visualizer_prompt(report_md=report_md, query=query)

            messages = [{"role": "user", "content": prompt}]
            try:
                response = await self._llm.achat(
                    messages,
                    tier=LLMTier.FAST,
                    temperature=0.3,
                    max_tokens=2000,
                    user_id=user_id,
                    session_id=session_id,
                    span_name="visualizer-llm",
                    step="visualizer",
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Visualizer LLM 调用失败, 报告保持不变: %s", e)
                span.update(
                    output={"visualized": False, "reason": "llm_error"},
                    metadata={"visualized": False, "error": str(e)},
                )
                return {"report_md": report_md}

            # 提取 ```mermaid 围栏内的代码
            mermaid_code = self._extract_mermaid(response.content)
            if not mermaid_code:
                logger.warning("Visualizer 未能提取 Mermaid 代码, 报告保持不变")
                span.update(
                    output={"visualized": False, "reason": "no_mermaid_code"},
                    metadata={"visualized": False, "llm_response_len": len(response.content)},
                )
                return {"report_md": report_md}

            # 插入报告 H1 标题后
            updated_report = self._insert_mermaid_into_report(report_md, mermaid_code)

            span.update(
                output={
                    "visualized": True,
                    "mermaid_len": len(mermaid_code),
                    "new_report_len": len(updated_report),
                },
                metadata={"visualized": True},
            )
            return {"report_md": updated_report}

    @staticmethod
    def _extract_mermaid(text: str) -> str | None:
        """从 LLM 输出中提取 ```mermaid 围栏内的代码.

        策略:
        1. 优先匹配 ```mermaid ... ``` 围栏
        2. 失败时尝试整段作为 Mermaid 代码 (LLM 可能未加围栏)
        3. 都失败返回 None
        """
        match = _MERMAID_FENCE_RE.search(text)
        if match:
            return match.group(1).strip()

        # 兜底: 若整段看起来像 Mermaid (以 graph/flowchart/sequenceDiagram 等开头)
        stripped = text.strip()
        mermaid_keywords = (
            "graph ",
            "flowchart ",
            "sequenceDiagram",
            "classDiagram",
            "stateDiagram",
            "erDiagram",
            "mindmap",
            "gantt",
            "pie ",
            "journey",
            "C4Context",
        )
        if any(stripped.startswith(kw) for kw in mermaid_keywords):
            return stripped

        return None

    @staticmethod
    def _insert_mermaid_into_report(report_md: str, mermaid_code: str) -> str:
        """在报告 Markdown 中插入 Mermaid 图表.

        策略: 在第一个 H1 标题后插入 (标题下方, 正文上方).
        若无 H1, 则在报告开头插入.
        图表用 ```mermaid 围栏包裹, 前后空行分隔.
        """
        mermaid_block = f"\n```mermaid\n{mermaid_code}\n```\n"

        lines = report_md.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("# "):
                return "\n".join(lines[: i + 1]) + mermaid_block + "\n" + "\n".join(lines[i + 1 :])

        # 无 H1, 在开头插入
        return mermaid_block + "\n" + report_md


async def visualizer_node(
    state: ResearcherState,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """Visualizer 可视化节点 (P2-Future-01).

    调用 Visualizer.visualize(), 返回 delta {"report_md": updated_report}.
    AGENTS.md 第 5 章: 节点为纯函数, 单一职责无副作用.
    AGENTS.md 第 10 章: 节点包裹在 trace span 内 (Visualizer.visualize 内部已包裹 trace_chain).
    """
    visualizer = Visualizer(settings)
    result = await visualizer.visualize(
        state,
        user_id=state.get("user_id"),
        session_id=state.get("session_id"),
    )
    return {"report_md": result["report_md"]}
