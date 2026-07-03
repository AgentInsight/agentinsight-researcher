"""PromptFamily 策略模式 (P1-Future-04).

将各 Skill 中内联的 prompt 文本提取为 PromptFamily 方法,
支持多语言/多风格 prompt 切换, 由 settings.prompt_family 路由.

设计原则:
- DefaultPromptFamily: 中文优先默认实现 (从现有各 Skill 文件提取 prompt 文本)
- EnglishPromptFamily: 英文实现
- 工厂路由: _PROMPT_FAMILY_REGISTRY + get_prompt_family(name) -> PromptFamily
- 各 Skill 接收 prompt_family: PromptFamily | None = None, None 时用 get_prompt_family(settings.prompt_family)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class PromptFamily(ABC):
    """Prompt 策略基类.

    每个方法返回完整的 prompt 字符串, 含角色 persona + 任务指令 + 输出格式.
    """

    @abstractmethod
    def planner_prompt(
        self,
        query: str,
        agent_role: str,
        max_iterations: int,
    ) -> str:
        """Planner 拆解子查询 prompt.

        Args:
            query: 用户研究查询
            agent_role: 角色 persona (对标 GPTR AGENT_ROLE)
            max_iterations: 子查询数量上限

        Returns:
            完整 prompt 字符串
        """

    @abstractmethod
    def writer_prompt(
        self,
        query: str,
        contexts: str,
        agent_role: str,
        tone: str,
        word_limit: int,
        report_type: str,
        current_date: str,
        references: str,
        structure_hint: str,
    ) -> str:
        """Writer 报告生成 prompt.

        Args:
            query: 研究主题
            contexts: 合并后的上下文文本
            agent_role: 角色 persona
            tone: 语气 (objective/analytical/opinionated/casual)
            word_limit: 字数下限
            report_type: 报告类型 (basic_report/detailed_report)
            current_date: 当前日期字符串
            references: 参考文献列表文本
            structure_hint: 报告结构模板文本

        Returns:
            完整 prompt 字符串
        """

    @abstractmethod
    def curator_prompt(
        self,
        query: str,
        sources_text: str,
        agent_role: str,
        max_results: int,
    ) -> str:
        """Curator 来源策展 prompt.

        Args:
            query: 研究问题
            sources_text: 来源列表文本
            agent_role: 角色 persona
            max_results: 最多返回条数

        Returns:
            完整 prompt 字符串
        """

    @abstractmethod
    def agent_creator_prompt(self, query: str) -> str:
        """AgentCreator 动态角色生成 prompt (auto_agent_instructions).

        Args:
            query: 用户研究查询

        Returns:
            完整 prompt 字符串 (系统提示)
        """

    @abstractmethod
    def reviewer_prompt(
        self,
        report_md: str,
        contexts: str,
        agent_role: str,
    ) -> str:
        """Reviewer 报告审查 prompt.

        Args:
            report_md: 待审查的报告 Markdown
            contexts: 报告所依据的上下文
            agent_role: 角色 persona

        Returns:
            完整 prompt 字符串
        """

    @abstractmethod
    def fact_checker_prompt(
        self,
        report_md: str,
        contexts: str,
        sources: str,
    ) -> str:
        """FactChecker 事实核查 prompt.

        Args:
            report_md: 待核查的报告 Markdown
            contexts: 报告所依据的上下文
            sources: 来源列表文本

        Returns:
            完整 prompt 字符串
        """

    @abstractmethod
    def mcp_tool_selection_prompt(
        self,
        query: str,
        tools_json: str,
        max_tools: int,
    ) -> str:
        """MCP 工具选择 prompt.

        Args:
            query: 用户查询
            tools_json: 可用工具 JSON 描述
            max_tools: 最多选几个工具

        Returns:
            完整 prompt 字符串
        """

    @abstractmethod
    def visualizer_prompt(self, report_md: str, query: str) -> str:
        """Visualizer Mermaid 图表生成 prompt.

        Args:
            report_md: 报告内容
            query: 研究主题

        Returns:
            完整 prompt 字符串
        """

    @abstractmethod
    def chat_prompt(
        self,
        query: str,
        report_md: str,
        agent_role: str,
    ) -> str:
        """ChatAgent 对话式追问系统提示.

        Args:
            query: 用户追问
            report_md: 已有报告内容 (截断后)
            agent_role: 角色 persona

        Returns:
            系统提示字符串
        """


class DefaultPromptFamily(PromptFamily):
    """中文优先默认实现.

    从现有各 Skill 文件提取 prompt 文本, 保持现有行为不变.
    """

    def planner_prompt(
        self,
        query: str,
        agent_role: str,
        max_iterations: int,
    ) -> str:
        return f"""{agent_role}

你的任务是: 将用户的研究问题拆解为 {max_iterations} 个具体的子查询, 用于搜索引擎检索.

要求:
1. 子查询应覆盖问题的不同维度 (市场/技术/竞争/政策/趋势等)
2. 子查询应为搜索引擎友好的关键词组合
3. 子查询应中文优先 (中文问题用中文子查询, 英文问题用英文子查询)
4. 返回 JSON 数组格式: ["子查询1", "子查询2", ...]

用户问题: {query}

请返回 {max_iterations} 个子查询的 JSON 数组:"""

    def writer_prompt(
        self,
        query: str,
        contexts: str,
        agent_role: str,
        tone: str,
        word_limit: int,
        report_type: str,
        current_date: str,
        references: str,
        structure_hint: str,
    ) -> str:
        return f"""{agent_role}

请基于以下检索到的上下文, 撰写一份关于「{query}」的研究报告.

要求:
1. 报告字数不少于 {word_limit} 字
2. 语气: {tone} (objective=客观, analytical=分析性, opinionated=观点鲜明, casual=通俗)
3. 结构化标题: # ## ### 层级
4. Web 源必须超链接引用: ([说明](url))
5. 末尾附参考文献列表 (APA 格式)
6. 注入当前日期: {current_date}
7. 不得编造未在上下文中出现的数据

报告结构:
{structure_hint}

上下文:
{contexts}

参考文献来源:
{references}

请生成完整的研究报告 (Markdown 格式):"""

    def curator_prompt(
        self,
        query: str,
        sources_text: str,
        agent_role: str,
        max_results: int,
    ) -> str:
        return f"""{agent_role}

你的任务是: 评估以下搜索来源的相关性与可信度, 选出最值得引用的 {max_results} 条.

评估标准:
1. 相关性: 与研究问题的相关程度 (0-10 分)
2. 可信度: 来源权威性 (官方机构 > 学术期刊 > 行业媒体 > 自媒体)
3. 时效性: 信息新鲜度
4. 深度: 内容详实程度

研究问题: {query}

来源列表:
{sources_text}

请返回 JSON 数组, 每项含 index (1-based) 与 score (0-10):
[{{"index": 1, "score": 9, "reason": "官方权威数据"}}, ...]

仅返回最相关的 {max_results} 条的 JSON 数组:"""

    def agent_creator_prompt(self, query: str) -> str:
        return """你是一个研究助手角色选择专家。根据用户的研究查询,选择最合适的研究角色 persona。

以下是几个示例:
- 查询涉及金融/投资/股票/财务 -> "Financial Analyst Agent": 资深金融分析师,擅长财务建模、估值、投资研究
- 查询涉及商业/市场/战略/管理 -> "Business Analyst Agent": 资深商业分析师,擅长市场分析、竞争战略、商业模式
- 查询涉及旅行/旅游/酒店 -> "Travel Agent": 资深旅游顾问,擅长目的地推荐、行程规划
- 查询涉及医学/医疗/健康/药物 -> "Medical Research Agent": 医学研究专家,擅长临床试验分析、医学文献综述
- 查询涉及法律/合规/法规 -> "Legal Research Agent": 法律研究专家,擅长法规解读、合规分析、判例研究
- 查询涉及技术/工程/IT -> "Technical Research Agent": 技术研究专家,擅长技术趋势、架构分析、工程实践

请根据用户查询,返回 JSON:
{"server": "角色简称(英文, snake_case, 如 financial_analyst)", "agent_role_prompt": "完整的角色 persona 描述(中文), 格式: 你是一位资深的XXX, 擅长YYY, 研究重点是ZZZ"}

仅返回 JSON, 不要其他内容:"""

    def reviewer_prompt(
        self,
        report_md: str,
        contexts: str,
        agent_role: str,
    ) -> str:
        return f"""{agent_role}

你的任务是: 审查以下研究报告的质量, 检查是否存在问题.

审查维度:
1. 准确性: 报告中的事实是否与上下文一致
2. 完整性: 是否覆盖了研究主题的关键维度
3. 逻辑性: 论证是否严密, 结论是否合理
4. 来源引用: 是否正确引用了来源, 有无编造

上下文:
{contexts[:8000]}

报告:
{report_md[:8000]}

请返回 JSON:
{{"verdict": "pass|revise", "issues": ["问题1", "问题2"], "suggestions": ["建议1", "建议2"]}}

仅返回 JSON:"""

    def fact_checker_prompt(
        self,
        report_md: str,
        contexts: str,
        sources: str,
    ) -> str:
        return f"""你是事实核查专家. 请核查以下报告中的关键事实是否在上下文或来源中有依据.

上下文:
{contexts[:6000]}

来源:
{sources[:3000]}

报告:
{report_md[:6000]}

请返回 JSON:
{{"facts_checked": 5, "facts_correct": 4, "facts_incorrect": 1, "incorrect_facts": ["具体错误1"], "verdict": "pass|fail"}}

仅返回 JSON:"""

    def mcp_tool_selection_prompt(
        self,
        query: str,
        tools_json: str,
        max_tools: int,
    ) -> str:
        return f"""你是 MCP 工具选择专家. 根据用户查询选择最合适的 {max_tools} 个 MCP 工具并生成调用参数.

可用工具:
{tools_json}

用户查询: {query}

请返回 JSON 数组, 每项含 name 与 args:
[
  {{"name": "tool_name", "args": {{"param1": "value1"}}}},
  ...
]

仅返回 JSON, 不要其他内容:"""

    def visualizer_prompt(self, report_md: str, query: str) -> str:
        return f"""你是可视化专家. 请根据以下研究报告内容, 生成一张 Mermaid 流程图或架构图, 用于直观展示报告的核心逻辑.

要求:
1. 使用 Mermaid 语法 (flowchart/graph/sequenceDiagram/mindmap 等)
2. 图表应概括报告的核心结构、关键发现或逻辑关系
3. 节点标签应简洁 (不超过 15 字符)
4. 图表复杂度适中 (10-20 个节点)
5. 仅输出 ```mermaid 围栏内的代码, 不要其他内容

研究主题: {query}

报告内容:
{report_md[:6000]}

请生成 Mermaid 图表:"""

    def chat_prompt(
        self,
        query: str,
        report_md: str,
        agent_role: str,
    ) -> str:
        return f"""{agent_role}

你是一个研究助手的对话模式. 用户已有一份研究报告, 正在对报告内容进行追问.

你的职责:
1. 基于已有报告内容回答用户追问
2. 如追问超出报告范围, 可结合常识回答并声明"此内容超出原报告范围"
3. 保持回答简洁专业, 直接回答问题
4. 引用报告中的具体内容时标注来源章节

已有研究报告:
{report_md}

请回答用户追问:"""


class EnglishPromptFamily(PromptFamily):
    """英文实现."""

    def planner_prompt(
        self,
        query: str,
        agent_role: str,
        max_iterations: int,
    ) -> str:
        return f"""{agent_role}

Your task is: break down the user's research question into {max_iterations} specific sub-queries for search engine retrieval.

Requirements:
1. Sub-queries should cover different dimensions (market/technology/competition/policy/trends etc.)
2. Sub-queries should be search-engine-friendly keyword combinations
3. Sub-queries should be in the same language as the original query
4. Return JSON array format: ["sub-query 1", "sub-query 2", ...]

User question: {query}

Return a JSON array of {max_iterations} sub-queries:"""

    def writer_prompt(
        self,
        query: str,
        contexts: str,
        agent_role: str,
        tone: str,
        word_limit: int,
        report_type: str,
        current_date: str,
        references: str,
        structure_hint: str,
    ) -> str:
        return f"""{agent_role}

Please write a research report on "{query}" based on the retrieved context below.

Requirements:
1. Report should be at least {word_limit} words
2. Tone: {tone} (objective/analytical/opinionated/casual)
3. Structured headings: # ## ### hierarchy
4. Web sources must be hyperlinked: ([description](url))
5. Include a reference list at the end (APA format)
6. Current date: {current_date}
7. Do not fabricate data not present in the context

Report structure:
{structure_hint}

Context:
{contexts}

Reference sources:
{references}

Generate the complete research report (Markdown format):"""

    def curator_prompt(
        self,
        query: str,
        sources_text: str,
        agent_role: str,
        max_results: int,
    ) -> str:
        return f"""{agent_role}

Your task is: evaluate the relevance and credibility of the following search sources, and select the top {max_results} most worthy of citation.

Evaluation criteria:
1. Relevance: how related to the research question (0-10)
2. Credibility: source authority (official > academic journals > industry media > blogs)
3. Timeliness: information freshness
4. Depth: content detail level

Research question: {query}

Source list:
{sources_text}

Return a JSON array, each item containing index (1-based) and score (0-10):
[{{"index": 1, "score": 9, "reason": "authoritative official data"}}, ...]

Return only the JSON array of the top {max_results} most relevant sources:"""

    def agent_creator_prompt(self, query: str) -> str:
        return """You are a research assistant role selection expert. Choose the most appropriate research role persona based on the user's research query.

Here are some examples:
- Query involves finance/investment/stocks/accounting -> "Financial Analyst Agent": Senior financial analyst, expert in financial modeling, valuation, investment research
- Query involves business/market/strategy/management -> "Business Analyst Agent": Senior business analyst, expert in market analysis, competitive strategy, business models
- Query involves travel/tourism/hotels -> "Travel Agent": Senior travel consultant, expert in destination recommendations, itinerary planning
- Query involves medicine/healthcare/pharma -> "Medical Research Agent": Medical research expert, expert in clinical trial analysis, medical literature review
- Query involves law/compliance/regulations -> "Legal Research Agent": Legal research expert, expert in regulatory interpretation, compliance analysis, case law research
- Query involves technology/engineering/IT -> "Technical Research Agent": Technical research expert, expert in technology trends, architecture analysis, engineering practices

Based on the user query, return JSON:
{"server": "role_short_name (English, snake_case, e.g. financial_analyst)", "agent_role_prompt": "Complete role persona description, format: You are a senior XXX, expert in YYY, research focus on ZZZ"}

Return JSON only, nothing else:"""

    def reviewer_prompt(
        self,
        report_md: str,
        contexts: str,
        agent_role: str,
    ) -> str:
        return f"""{agent_role}

Your task is: review the quality of the following research report and check for issues.

Review dimensions:
1. Accuracy: whether facts in the report are consistent with the context
2. Completeness: whether key dimensions of the research topic are covered
3. Logic: whether arguments are rigorous and conclusions reasonable
4. Source citations: whether sources are correctly cited without fabrication

Context:
{contexts[:8000]}

Report:
{report_md[:8000]}

Return JSON:
{{"verdict": "pass|revise", "issues": ["issue1", "issue2"], "suggestions": ["suggestion1", "suggestion2"]}}

Return JSON only:"""

    def fact_checker_prompt(
        self,
        report_md: str,
        contexts: str,
        sources: str,
    ) -> str:
        return f"""You are a fact-checking expert. Verify whether the key facts in the following report are supported by the context or sources.

Context:
{contexts[:6000]}

Sources:
{sources[:3000]}

Report:
{report_md[:6000]}

Return JSON:
{{"facts_checked": 5, "facts_correct": 4, "facts_incorrect": 1, "incorrect_facts": ["specific error 1"], "verdict": "pass|fail"}}

Return JSON only:"""

    def mcp_tool_selection_prompt(
        self,
        query: str,
        tools_json: str,
        max_tools: int,
    ) -> str:
        return f"""You are an MCP tool selection expert. Select the most appropriate {max_tools} MCP tools based on the user query and generate call parameters.

Available tools:
{tools_json}

User query: {query}

Return a JSON array, each item containing name and args:
[
  {{"name": "tool_name", "args": {{"param1": "value1"}}}},
  ...
]

Return JSON only, nothing else:"""

    def visualizer_prompt(self, report_md: str, query: str) -> str:
        return f"""You are a visualization expert. Generate a Mermaid flowchart, architecture diagram, or mindmap based on the following research report to visually present its core logic.

Requirements:
1. Use Mermaid syntax (flowchart/graph/sequenceDiagram/mindmap etc.)
2. The diagram should summarize the core structure, key findings, or logical relationships of the report
3. Node labels should be concise (no more than 15 characters)
4. Moderate diagram complexity (10-20 nodes)
5. Output only the code within a ```mermaid fence, nothing else

Research topic: {query}

Report content:
{report_md[:6000]}

Generate Mermaid diagram:"""

    def chat_prompt(
        self,
        query: str,
        report_md: str,
        agent_role: str,
    ) -> str:
        return f"""{agent_role}

You are a research assistant in conversation mode. The user has a research report and is asking follow-up questions about it.

Your responsibilities:
1. Answer follow-up questions based on the existing report content
2. If a question goes beyond the report scope, answer with common knowledge and state "This content is beyond the original report scope"
3. Keep answers concise and professional
4. When citing specific content from the report, indicate the source section

Existing research report:
{report_md}

Answer the user's follow-up question:"""


# ========== 工厂路由 ==========

_PROMPT_FAMILY_REGISTRY: dict[str, type[PromptFamily]] = {
    "default": DefaultPromptFamily,
    "english": EnglishPromptFamily,
}


def get_prompt_family(name: str = "default") -> PromptFamily:
    """工厂方法: 按 name 获取 PromptFamily 实例.

    Args:
        name: 策略名称 ("default" | "english")

    Returns:
        PromptFamily 实例

    Raises:
        ValueError: name 不在注册表中
    """
    cls = _PROMPT_FAMILY_REGISTRY.get(name)
    if cls is None:
        logger.warning("未注册的 PromptFamily '%s', 降级为 default", name)
        cls = DefaultPromptFamily
    return cls()


def register_prompt_family(name: str, family_cls: type[PromptFamily]) -> None:
    """注册自定义 PromptFamily.

    Args:
        name: 策略名称
        family_cls: PromptFamily 子类
    """
    _PROMPT_FAMILY_REGISTRY[name] = family_cls
