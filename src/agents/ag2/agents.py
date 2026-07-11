"""AG2 角色定义: system_prompt + 消息传递协议.

4 个角色对应 multi_agents 流程: Researcher → Writer → Reviewer → Publisher.

复用现有 Skill 组件 (本模块仅定义 prompt 与协议, 不创建 ConversableAgent):
- Researcher → src/skills/researcher/research_conductor.py
- Writer → src/skills/researcher/report_generator.py
- Reviewer → src/agents/researcher/reviewer.py
- Publisher → src/skills/researcher/publisher.py
"""

from __future__ import annotations

# ========== 4 个角色的 system_prompt ==========

RESEARCHER_SYSTEM_PROMPT = """你是一名研究分析专家 (Researcher), 在多 Agent 协作研究中负责信息检索与上下文聚合.

职责:
- 接收用户的研究查询
- 调用 ResearchConductor 进行多源并行检索 (搜索引擎 + 网页抓取 + 上下文压缩)
- 将研究结果 (上下文列表 + 来源列表) 传递给 Writer

你不负责撰写报告, 仅负责收集和整理研究材料.
收到研究请求后, 系统会自动调用 ResearchConductor 完成检索, 你只需确认任务已启动."""

WRITER_SYSTEM_PROMPT = """你是一名报告撰写专家 (Writer), 在多 Agent 协作研究中负责基于上下文合成研究报告.

职责:
- 接收 Researcher 提供的研究上下文与来源
- 调用 ReportGenerator 生成 Markdown 研究报告 (支持 basic_report / detailed_report)
- 将报告草稿传递给 Reviewer 审核

你不负责检索信息, 仅基于已有上下文撰写报告.
收到研究材料后, 系统会自动调用 ReportGenerator 完成写作, 你只需确认任务已启动."""

REVIEWER_SYSTEM_PROMPT = """你是一名报告评审专家 (Reviewer), 在多 Agent 协作研究中负责多维度审核报告质量.

职责:
- 接收 Writer 生成的报告草稿
- 调用 Reviewer 按 4 维度评分 (事实性 / 结构性 / 语言性 / 完整性)
- 将审核决策 (accept / revise) 与反馈传递给 Publisher

你不负责修改报告, 仅负责评审并给出反馈.
收到报告后, 系统会自动调用 Reviewer 完成审核, 你只需确认任务已启动."""

PUBLISHER_SYSTEM_PROMPT = """你是一名报告发布专家 (Publisher), 在多 Agent 协作研究中负责将审核通过的报告发布为指定格式.

职责:
- 接收审核完成的报告
- 调用 Publisher 将报告发布为 Markdown / HTML / PDF / DOCX / JSON 格式
- 返回最终发布结果

你不负责修改报告内容, 仅负责格式化与发布.
收到审核完成的报告后, 系统会自动调用 Publisher 完成发布, 你只需确认任务已启动."""


# ========== 角色顺序与 prompt 映射 ==========

# AG2 GroupChat 中 Agent 的发言顺序 (multi_agents 流程)
AGENTS_ORDER: list[str] = ["researcher", "writer", "reviewer", "publisher"]

# GroupChat 最大轮次 (初始消息 + 4 个 Agent 回复 + 余量)
MAX_ROUNDS: int = 6

# 角色 system_prompt 映射
ROLE_PROMPTS: dict[str, str] = {
    "researcher": RESEARCHER_SYSTEM_PROMPT,
    "writer": WRITER_SYSTEM_PROMPT,
    "reviewer": REVIEWER_SYSTEM_PROMPT,
    "publisher": PUBLISHER_SYSTEM_PROMPT,
}


# ========== 角色间消息传递协议 ==========

# 各角色完成后输出的状态消息模板 (用于 GroupChat 消息流)


def research_complete_msg(contexts_count: int, sources_count: int) -> str:
    """Researcher 完成研究后的消息."""
    return (
        f"研究完成。已收集 {contexts_count} 条上下文, {sources_count} 个来源。"
        "请 Writer 基于这些材料撰写报告。"
    )


def report_ready_msg(report_len: int) -> str:
    """Writer 完成报告后的消息."""
    return f"报告已生成。报告长度: {report_len} 字符。请 Reviewer 审核报告。"


def review_complete_msg(decision: str) -> str:
    """Reviewer 完成审核后的消息."""
    return f"审核完成。决策: {decision}。请 Publisher 发布报告。"


def publish_complete_msg(output_format: str) -> str:
    """Publisher 完成发布后的消息."""
    return f"报告已发布。输出格式: {output_format}。研究任务完成。"
