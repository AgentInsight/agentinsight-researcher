"""ResearcherState 定义.

对标 GPT Researcher multi_agents/memory/research.py 的 ResearchState TypedDict.
AGENTS.md 第 5 章: State 必须为 TypedDict; 跨节点共享字段用 Annotated[T, reducer].
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class ResearcherState(TypedDict, total=False):
    """研究智能体状态.

    所有字段可选(total=False), 节点纯函数化只返回 update dict 由 reducer 合并.
    对标 GPT Researcher ResearchState + AgentInsightService insight/state.py 模式.
    AGENTS.md 第 5 章: 跨节点共享字段用 Annotated[T, reducer] (messages 用 add_messages).
    """

    # ========== 请求上下文 (AGENTS.md 第 6/8 章) ==========
    query: str  # 用户原始研究请求
    session_id: str  # 即 thread_id, 会话隔离键
    agent_id: str  # agent_name, 数据隔离键
    user_id: str  # 用户隔离键, 第 8 章身份解析获得
    token: str  # JWT Bearer Token (不持久化, 不入日志)
    query_domains: list[str]  # P1-Future-02: 域名过滤白名单 (仅检索这些域名的结果)

    # ========== 报告配置 (用户需求 6) ==========
    report_type: str  # basic_report | detailed_report | deep_research
    report_format: str  # markdown | html | pdf | docx | json
    tone: str  # objective | analytical | opinionated | casual
    total_words: int  # 报告字数下限

    # ========== 动态角色 (对标 GPTR researcher.role, AGENTS.md 第 5 章) ==========
    # 行业适配采用 GPTR 风格 4 层机制, 不再使用行业分类器:
    #   1. Prompt 层: AgentCreator.AUTO_AGENT_INSTRUCTIONS few-shot → LLM 动态生成角色
    #   2. Config 层: settings.agent_role 静态注入 (优先级高于 LLM)
    #   3. Retriever 层: searchers/ 含 arxiv/pubmed/semantic_scholar 等专业数据源
    #   4. MCP 层: MCP_SERVERS 注册行业专用工具服务器
    agent_role: str  # 动态生成的角色 persona (对标 GPTR researcher.role)
    agent_role_server: str  # 角色简称 (对标 GPTR server, 如 financial_analyst)

    # ========== 研究流程 (对标 GPT Researcher) ==========
    messages: Annotated[list[BaseMessage], add_messages]  # 消息流 (P0-06: add_messages reducer)
    sub_queries: list[str]  # Planner 拆解的子查询
    contexts: list[str]  # 聚合的上下文 (来自 Researcher)
    sources: list[dict[str, Any]]  # 引用来源 [{"title","url","snippet","score"}]
    visited_urls: set[str]  # 已访问 URL, 去重
    curated_sources: list[dict[str, Any]]  # Reviewer 策展后的来源

    # ========== Token 优化 (用户需求 10) ==========
    context_compressed: bool  # 是否已压缩
    total_cost_usd: float  # 累计成本
    total_tokens: int  # 累计 Token
    token_logs: list[dict[str, Any]]  # 各阶段 Token 明细

    # ========== MCP (用户需求 9) ==========
    mcp_strategy: str  # fast | deep | disabled
    mcp_configs: list[dict[str, Any]]
    mcp_context: list[str]  # MCP 检索结果

    # ========== 文件上传 (用户需求 8) ==========
    uploaded_files: list[dict[str, Any]]  # 上传文件元数据

    # ========== 深度研究 (P0-01) ==========
    research_mode: (
        str  # "basic" | "detailed" | "quick" | "sources" | "deep" | "summary" | "subtopics"
    )
    deep_research_breadth: int  # 递归广度
    deep_research_depth: int  # 递归深度
    iteration_count: Annotated[int, operator.add]  # 迭代计数器 (P0-05 守卫用, 多分支节点累加)

    # ========== 人在回路 (P0-Future-03 Human-in-the-loop) ==========
    # human_review_enabled=True 时, agent_creator → human → (accept → supervisor | revise → agent_creator)
    # human_feedback: None 表示审核通过; 非 None 字符串表示用户修订意见 (回 agent_creator 重新生成角色)
    # revisions_count: 累计修订次数 (Annotated[int, operator.add] 累加), 达 max_plan_revisions 强制通过
    human_feedback: str | None  # 用户审核反馈 (None=通过, str=修订意见)
    revisions_count: Annotated[int, operator.add]  # 修订次数累加器
    human_review_enabled: bool  # 是否启用人在回路 (由 settings.human_review_enabled 注入)

    # ========== 事实核查 (P0-Future-02) ==========
    fact_check_accepted: bool  # 事实核查是否通过
    fact_check_issues: list[str]  # 不一致的事实声明列表

    # ========== 评审与修订循环 (P0-Future-01) ==========
    review_decision: str  # "accept" | "revise" (Reviewer 决策)
    review_feedback: str  # Reviewer 评审反馈 (revise 时含具体修订建议)
    revision_count: Annotated[
        int, operator.add
    ]  # 修订计数器 (reviser 节点累加, max_revisions 守卫)

    # ========== 输出 ==========
    report_md: str  # Markdown 报告
    report_html: str  # HTML 报告
    report_pdf_path: str  # PDF 文件路径
    report_docx: bytes  # DOCX 报告 (P1-05)
    report_json: str  # JSON 报告 (P1-05)
    report_image_url: str  # 报告配图 URL (P2-06, deepseek-v4-flash 生成)
    report_image_b64: str  # 报告配图 base64 (P2-06, 与 url 二选一)
    error: str  # 错误信息
    status: str  # pending | running | completed | failed
