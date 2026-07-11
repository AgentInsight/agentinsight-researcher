"""ResearcherState 定义.

State 必须为 TypedDict; 跨节点共享字段用 Annotated[T, reducer].
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class ResearcherState(TypedDict, total=False):
    """研究智能体状态.

    所有字段可选(total=False), 节点纯函数化只返回 update dict 由 reducer 合并.
    跨节点共享字段用 Annotated[T, reducer] (messages 用 add_messages).
    """

    # ========== 请求上下文 ==========
    query: str  # 用户原始研究请求
    session_id: str  # 即 thread_id, 会话隔离键
    agent_id: str  # agent_name, 数据隔离键
    user_id: str  # 用户隔离键, 第 8 章身份解析获得
    token: str  # JWT Bearer Token (不持久化, 不入日志)
    query_domains: list[str]  # 域名过滤白名单 (仅检索这些域名的结果)

    # ========== 报告配置 (用户需求 6) ==========
    report_type: str  # basic_report | detailed_report | deep_research
    report_format: str  # markdown | html | pdf | docx | json
    tone: str  # objective | analytical | opinionated | casual
    total_words: int  # 报告字数下限
    report_language: str  # 报告语言 (zh|en|ja|ko|fr), 默认 zh 中文

    # ========== 查询意图 ==========
    query_intent: str  # 查询意图: "research" | "chat" | "short_query"

    # ========== 动态角色 ==========
    # 行业适配采用 4 层机制 (无行业分类器):
    #   1. Prompt 层: AgentCreator.AUTO_AGENT_INSTRUCTIONS few-shot → LLM 动态生成角色
    #   2. Config 层: settings.agent_role 静态注入 (优先级高于 LLM)
    #   3. Retriever 层: searchers/ 含 arxiv/pubmed/semantic_scholar 等专业数据源
    #   4. MCP 层: MCP_SERVERS 注册行业专用工具服务器
    agent_role: str  # 动态生成的角色 persona
    agent_role_server: str  # 角色简称 (如 financial_analyst)

    # ========== 研究流程 ==========
    messages: Annotated[list[BaseMessage], add_messages]  # 消息流 (add_messages reducer)
    sub_queries: list[str]  # Planner 拆解的子查询
    contexts: list[str]  # 聚合的上下文 (来自 Researcher)
    sources: list[dict[str, Any]]  # 引用来源 [{"title","url","snippet","score"}]
    visited_urls: set[str]  # 已访问 URL, 去重
    curated_sources: list[dict[str, Any]]  # Reviewer 策展后的来源

    # ========== Token 优化 (用户需求 10) ==========
    total_cost_usd: float  # 累计成本
    total_tokens: int  # 累计 Token
    token_logs: list[dict[str, Any]]  # 各阶段 Token 明细

    # ========== 文件上传 (用户需求 8) ==========
    uploaded_files: list[dict[str, Any]]  # 上传文件元数据

    # ========== 深度研究 ==========
    research_mode: (
        str  # "basic" | "detailed" | "quick" | "sources" | "deep" | "summary" | "subtopics"
    )
    deep_research_breadth: int  # 递归广度
    deep_research_depth: int  # 递归深度
    iteration_count: Annotated[int, operator.add]  # 迭代计数器 (守卫用, 多分支节点累加)

    # ========== 人在回路 (Human-in-the-loop) ==========
    # human_review_enabled=True 时, agent_creator → human → (accept → supervisor | revise → agent_creator)
    # human_feedback: None 表示审核通过; 非 None 字符串表示用户修订意见 (回 agent_creator 重新生成角色)
    # revisions_count: 累计修订次数 (Annotated[int, operator.add] 累加), 达 max_plan_revisions 强制通过
    human_feedback: str | None  # 用户审核反馈 (None=通过, str=修订意见)
    revisions_count: Annotated[int, operator.add]  # 修订次数累加器
    human_review_enabled: bool  # 是否启用人在回路 (由 settings.human_review_enabled 注入)

    # ========== 事实核查 ==========
    fact_check_accepted: bool  # 事实核查是否通过
    fact_check_issues: list[str]  # 不一致的事实声明列表

    # ========== 评审与修订循环 (多维度评分) ==========
    review_decision: str  # "accept" | "revise" (Reviewer 决策)
    review_feedback: str  # Reviewer 评审反馈 (revise 时含具体修订建议)
    review_scores: dict[str, Any]  # 多维度评分 {维度: {score, issues}}
    revision_count: Annotated[
        int, operator.add
    ]  # 修订计数器 (reviser 节点累加, max_revisions 守卫)

    # ========== 输出 ==========
    # 报告格式字段合并为单一 dict, key 为格式名 (md/html/pdf/docx/json),
    # value 为内容 (md/html/docx/json) 或文件路径 (pdf).
    # report_md 保留一个发布周期 (deprecated), 新代码应使用 report_formats["md"].
    report_md: str  # deprecated: 使用 report_formats["md"], 兼容期保留
    report_formats: dict[str, str]  # {md|html|pdf|docx|json: 内容或路径}
    report_image_url: str  # 报告配图 URL (deepseek-v4-flash 生成)
    report_image_b64: str  # 报告配图 base64 (与 url 二选一)
    report_id: str  # 报告主键 UUID (publisher 写入, routes/cli 读取用于下载链接)
    status: str  # pending | running | completed | failed
