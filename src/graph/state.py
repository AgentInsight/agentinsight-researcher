"""ResearcherState 定义.

对标 GPT Researcher multi_agents/memory/research.py 的 ResearchState TypedDict.
AGENTS.md 第 5 章: State 必须为 TypedDict; 跨节点共享字段用 Annotated[T, reducer].
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    # 仅类型检查时导入, 运行时由 langgraph 注入 reducer
    pass


class ResearcherState(TypedDict, total=False):
    """研究智能体状态.

    所有字段可选(total=False), 节点纯函数化只返回 update dict 由 reducer 合并.
    对标 GPT Researcher ResearchState + AgentInsightService insight/state.py 模式.

    注: messages 字段在阶段 2 接入 LangGraph 时启用 Annotated reducer,
    阶段 1 骨架阶段保持纯 dict 不依赖 langchain_core.
    """

    # ========== 请求上下文 (AGENTS.md 第 6/8 章) ==========
    query: str  # 用户原始研究请求
    session_id: str  # 即 thread_id, 会话隔离键
    agent_id: str  # agent_name, 数据隔离键
    user_id: str  # 用户隔离键, 第 8 章身份解析获得
    token: str  # JWT Bearer Token (不持久化, 不入日志)

    # ========== 报告配置 (用户需求 6) ==========
    report_type: str  # basic_report | detailed_report | deep_research
    report_format: str  # markdown | html | pdf
    tone: str  # objective | analytical | opinionated | casual
    total_words: int  # 报告字数下限

    # ========== 行业识别 (用户需求 4) ==========
    industry_code: str  # GICS 行业代码
    industry_name: str  # GICS 行业名称
    industry_sector: str  # GICS Sector
    industry_group: str  # GICS Industry Group
    industry_sub: str  # GICS Sub-Industry
    industry_prompt_family: dict[str, Any]  # 行业专家提示词族 (prompt_family)

    # ========== 研究流程 (对标 GPT Researcher) ==========
    # 阶段 2 接入 LangGraph 时改为: Annotated[list[BaseMessage], add_messages]
    messages: list[Any]  # 消息流 (阶段 2 启用 reducer)
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

    # ========== 输出 ==========
    report_md: str  # Markdown 报告
    report_html: str  # HTML 报告
    report_pdf_path: str  # PDF 文件路径
    error: str  # 错误信息
    status: str  # pending | running | completed | failed
