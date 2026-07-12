"""PromptFamily 策略模式.

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
            agent_role: 角色 persona (AGENT_ROLE)
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
        report_style: str = "academic",
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
            report_style: 报告风格预设 (academic/business/casual/news)

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

    @abstractmethod
    def get_tone_prompt(self, tone: str) -> str:
        """获取 Tone 语气提示词片段.

        Args:
            tone: 语气标识 (objective/analytical/formal/informative/
                  explanatory/critical/comparative/casual)

        Returns:
            Tone 提示词片段, 附加到主 prompt 末尾
        """

    # ========== detailed_report 专用 prompt ==========
    # 4 个 prompt 提取到 PromptFamily 统一管理.

    @abstractmethod
    def subtopics_prompt(
        self,
        query: str,
        context: str,
        role_persona: str,
        max_subtopics: int = 5,
    ) -> str:
        """detailed_report 子主题拆解 prompt.

        Args:
            query: 主研究问题
            context: 初始检索上下文
            role_persona: 角色 persona
            max_subtopics: 子主题数量上限

        Returns:
            完整 prompt 字符串
        """

    @abstractmethod
    def introduction_prompt(
        self,
        query: str,
        context: str,
        references: str,
        role_persona: str,
        tone: str,
        current_date: str,
        style_desc: str,
        word_min: int = 300,
        word_max: int = 500,
    ) -> str:
        """detailed_report 引言 prompt.

        Args:
            query: 主研究问题
            context: 初始检索上下文
            references: 参考文献文本
            role_persona: 角色 persona
            tone: 语气
            current_date: 当前日期
            style_desc: 报告风格描述
            word_min: 引言字数下限
            word_max: 引言字数上限

        Returns:
            完整 prompt 字符串
        """

    @abstractmethod
    def section_prompt(
        self,
        topic: str,
        context: str,
        references: str,
        role_persona: str,
        tone: str,
        style_desc: str,
        word_min: int = 800,
        word_max: int = 1200,
    ) -> str:
        """detailed_report 子主题章节 prompt.

        Args:
            topic: 子主题名称
            context: 子主题检索上下文
            references: 参考文献文本
            role_persona: 角色 persona
            tone: 语气
            style_desc: 报告风格描述
            word_min: 章节字数下限
            word_max: 章节字数上限

        Returns:
            完整 prompt 字符串
        """

    @abstractmethod
    def conclusion_prompt(
        self,
        query: str,
        sections_summary: str,
        role_persona: str,
        tone: str,
        style_desc: str,
        word_min: int = 300,
        word_max: int = 500,
    ) -> str:
        """detailed_report 结论 prompt.

        Args:
            query: 主研究问题
            sections_summary: 已写章节摘要
            role_persona: 角色 persona
            tone: 语气
            style_desc: 报告风格描述
            word_min: 结论字数下限
            word_max: 结论字数上限

        Returns:
            完整 prompt 字符串
        """


class DefaultPromptFamily(PromptFamily):
    """中文优先默认实现.

    从现有各 Skill 文件提取 prompt 文本, 保持现有行为不变.
    """

    # 报告风格预设描述 (4 种风格)
    _STYLE_PROMPTS: dict[str, str] = {
        "academic": (
            "学术风格: 严谨客观, 引用来源, 使用正式学术语言, "
            "段落间逻辑清晰, 论点需有数据或文献支撑, 避免口语化表达"
        ),
        "business": (
            "商业风格: 简洁明了, 结论先行, 使用商业术语, "
            "聚焦价值与决策建议, 突出关键指标与 ROI, 段落短小精悍"
        ),
        "casual": (
            "通俗风格: 易于理解, 避免专业术语, 适合大众阅读, "
            "多用类比与案例, 语言亲切自然, 降低认知门槛"
        ),
        "news": (
            "新闻风格: 倒金字塔结构, 5W1H, 客观报道, "
            "导语概括核心事实, 正文按重要性递减展开, 强调时效与现场感"
        ),
    }

    # Tone 描述 (精选 8 种适合中文研究场景)
    _TONE_DESCRIPTIONS: dict[str, str] = {
        "objective": "客观中立，基于事实陈述，不带个人观点",
        "analytical": "分析性强，深入剖析因果关系和数据背后的含义",
        "formal": "正式严谨，学术风格，使用专业术语",
        "informative": "信息丰富，重点传递实用知识，便于读者快速理解",
        "explanatory": "解释性，阐明复杂概念，适合科普读者",
        "critical": "批判性，审视多角度观点，指出局限与不足",
        "comparative": "比较性，横向纵向对比，突出差异与优劣",
        "casual": "通俗轻松，口语化表达，适合大众阅读",
    }

    def get_tone_prompt(self, tone: str) -> str:
        """获取 Tone 提示词片段 (精选 8 种).

        未注册 tone 降级为 objective, 保证健壮性.
        """
        desc = self._TONE_DESCRIPTIONS.get(tone, self._TONE_DESCRIPTIONS["objective"])
        return f"\n\n## 写作语气要求\n{desc}"

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
        report_style: str = "academic",
    ) -> str:
        # 注入报告风格预设描述, 未注册风格降级为 academic
        style_desc = self._STYLE_PROMPTS.get(report_style, self._STYLE_PROMPTS["academic"])
        # writer_prompt 精细之处
        # - MUST 含具体观点 (非泛泛描述)
        # - MUST markdown 表格呈现对比数据
        # - MUST 编号引用 [n] 对应 reference list (in-text citation)
        # - MUST 至少 {word_limit} 字 (硬下限)
        # - 来源可信度优先级 (官方 > 学术 > 行业 > 自媒体)
        return f"""{agent_role}

请基于以下检索到的上下文, 撰写一份关于「{query}」的研究报告.

【MUST 必须满足】:
1. **字数下限**: 报告不少于 {word_limit} 字 (硬要求, 不达标视为失败)
2. **具体观点**: 每个论点 MUST 含具体数据/案例/数字, 严禁泛泛描述 (如"市场很大"→"2025 年市场规模 1.2 万亿元, CAGR 18.5%")
3. **Markdown 表格**: 至少 1 个 markdown 表格呈现对比数据 (多维度对比/趋势数据/竞争格局等)
4. **编号引用**: 行内引用使用 `[n]` 编号格式 (对应末尾参考文献列表, 如 "市场规模 1.2 万亿 [1]"), 同时 Web 源附超链接 `([说明](url))`; 多个引用之间用空格分隔 (如 `[1] [2]`), 严禁紧贴
5. **结构化标题**: `#`/`##`/`###` 三级层级
6. **来源可信度优先**: 优先引用 官方机构 > 学术期刊 > 行业媒体 > 自媒体, 自媒体来源需标注"未经证实"

【Markdown 排版规范】(严格遵守, 确保可读性):
- **段落间距**: 段落之间必须空一行 (即两个 \\n), 严禁段落紧贴
- **标题前后**: 标题前后必须空一行; 严禁标题与正文紧贴
- **表格前后**: 表格前后必须空一行; 表格内部单元格不得有空行
- **列表前后**: 列表前后必须空一行; 列表项之间不空行
- **引用块前后**: 引用块 (>) 前后必须空一行
- **末尾清洁**: 报告末尾不得有多余空行

【软要求】:
- 语气: {tone} (objective=客观, analytical=分析性, opinionated=观点鲜明, casual=通俗)
- 末尾附参考文献列表 (APA 格式, 含 [n] 编号)
- 注入当前日期: {current_date}
- 不得编造未在上下文中出现的数据 (幻觉零容忍)
- 报告风格: {style_desc}

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
        # SourceCurator 评估要点
        # - 强调 "Quantitative Value" (出现 5 次)
        # - 5 维评估 (Relevance/Credibility/Currency/Objectivity/Quantitative Value)
        # - "Err on the side of inclusion" (宁多勿少)
        # prompt 精简, 仅输出 index+score, 不输出 reason
        return f"""{agent_role}

你的任务是: 评估以下搜索来源的相关性与可信度, 选出最值得引用的 {max_results} 条.

【5 维评估标准】:
1. **相关性 (Relevance)**: 与研究问题的相关程度 (0-10 分)
2. **可信度 (Credibility)**: 来源权威性 (官方机构 > 学术期刊 > 行业媒体 > 自媒体)
3. **时效性 (Currency)**: 信息新鲜度 (优先近 12 个月数据)
4. **客观性 (Objectivity)**: 是否存在明显立场偏见或商业推广
5. **数据丰富度 (Quantitative Value)**: 是否含具体数字/百分比/金额/统计指标 (含统计数据的来源优先级显著高于纯文字描述)

【原则】: Err on the side of inclusion (宁多勿少) — 数据丰富的来源即使相关性略低也优先保留.

研究问题: {query}

来源列表:
{sources_text}

请返回 JSON 数组, 每项仅含 index (1-based) 与 score (0-10), 不需要 reason:
[{{"index": 1, "score": 9}}, ...]

仅返回最相关的 {max_results} 条的 JSON 数组:"""

    def agent_creator_prompt(self, query: str) -> str:
        return """你是一个研究助手角色选择专家。根据用户的研究查询,选择最合适的研究角色 persona。

生成角色 persona 时必须满足以下三项要求:
1. 研究方法论: 明确采用的研究方法 (如系统综述、meta 分析、案例研究、对比分析、定量建模等)
2. 输出规范: 报告需含数据支撑、明确引用来源、逻辑结构清晰
3. 语言风格: 客观、专业、避免主观臆断

以下是一些示例 (格式: task → response):
task: "查询涉及金融/投资/股票/财务分析" → response: {"server": "financial_analyst", "agent_role_prompt": "你是一位资深的金融分析师, 擅长财务建模、估值、投资研究. 研究方法论: 采用定量分析与财务建模交叉验证多源数据. 输出规范: 报告需含数据支撑、明确引用来源、逻辑结构清晰. 语言风格: 客观、专业、避免主观臆断."}
task: "查询涉及商业/市场/战略/管理" → response: {"server": "business_analyst", "agent_role_prompt": "你是一位资深的商业分析师, 擅长市场分析、竞争战略、商业模式. 研究方法论: 采用案例研究与对比分析结合波特五力等框架. 输出规范: 报告需含数据支撑、明确引用来源、逻辑结构清晰. 语言风格: 客观、专业、避免主观臆断."}
task: "查询涉及旅行/旅游/酒店/行程" → response: {"server": "travel_agent", "agent_role_prompt": "你是一位资深的旅游顾问, 擅长目的地推荐、行程规划. 研究方法论: 采用多源信息聚合与用户偏好匹配. 输出规范: 报告需含数据支撑、明确引用来源、逻辑结构清晰. 语言风格: 客观、专业、避免主观臆断."}
task: "查询涉及医学/医疗/健康/药物" → response: {"server": "medical_researcher", "agent_role_prompt": "你是一位医学研究专家, 擅长临床试验分析、医学文献综述. 研究方法论: 采用系统综述与 meta 分析优先循证医学. 输出规范: 报告需含数据支撑、明确引用来源、逻辑结构清晰. 语言风格: 客观、专业、避免主观臆断."}
task: "查询涉及法律/合规/法规/判例" → response: {"server": "legal_researcher", "agent_role_prompt": "你是一位法律研究专家, 擅长法规解读、合规分析、判例研究. 研究方法论: 采用判例比对与条文文义解释结合. 输出规范: 报告需含数据支撑、明确引用来源、逻辑结构清晰. 语言风格: 客观、专业、避免主观臆断."}
task: "查询涉及技术/工程/IT/架构" → response: {"server": "technology_researcher", "agent_role_prompt": "你是一位技术研究专家, 擅长技术趋势、架构分析、工程实践. 研究方法论: 采用技术调研与对比实验评估. 输出规范: 报告需含数据支撑、明确引用来源、逻辑结构清晰. 语言风格: 客观、专业、避免主观臆断."}
task: "查询涉及教育/教学/课程/学习" → response: {"server": "education_researcher", "agent_role_prompt": "你是一位教育研究专家, 擅长课程设计、教学法、教育政策分析. 研究方法论: 采用文献综述与教育实验对照. 输出规范: 报告需含数据支撑、明确引用来源、逻辑结构清晰. 语言风格: 客观、专业、避免主观臆断."}
task: "查询涉及科学/物理/化学/生物/天文" → response: {"server": "science_researcher", "agent_role_prompt": "你是一位科学研究专家, 擅长跨学科文献综述、实验设计、科学推理. 研究方法论: 采用系统综述与可重复性验证. 输出规范: 报告需含数据支撑、明确引用来源、逻辑结构清晰. 语言风格: 客观、专业、避免主观臆断."}
task: "查询涉及营销/品牌/广告/用户增长" → response: {"server": "marketing_researcher", "agent_role_prompt": "你是一位市场营销研究专家, 擅长消费者行为、品牌策略、增长黑客. 研究方法论: 采用定量调研与 A/B 测试结合用户访谈. 输出规范: 报告需含数据支撑、明确引用来源、逻辑结构清晰. 语言风格: 客观、专业、避免主观臆断."}
task: "查询涉及环境/气候/可持续发展/生态" → response: {"server": "environment_researcher", "agent_role_prompt": "你是一位环境与可持续发展研究专家, 擅长气候变化、生态评估、ESG 分析. 研究方法论: 采用生命周期评估与情景建模. 输出规范: 报告需含数据支撑、明确引用来源、逻辑结构清晰. 语言风格: 客观、专业、避免主观臆断."}

请根据用户查询,返回 JSON:
{"server": "角色简称(英文, snake_case, 如 financial_analyst)", "agent_role_prompt": "完整的角色 persona 描述(中文), 必须含研究方法论、输出规范、语言风格三要素"}

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

    # ========== detailed_report 专用 prompt 实现 ==========

    def subtopics_prompt(
        self,
        query: str,
        context: str,
        role_persona: str,
        max_subtopics: int = 5,
    ) -> str:
        # generate_subtopics
        # 用 STRATEGIC LLM 拆解子主题, temperature=0.25
        return f"""{role_persona}

请基于以下研究问题与初始上下文, 拆解为 3-{max_subtopics} 个用于分章节深入研究的子主题.

要求:
1. 子主题应覆盖问题的不同维度 (如市场/技术/竞争/政策/趋势等)
2. 子主题应基于上下文中实际出现的内容, 不得编造
3. 每个子主题为简洁的中/英文短语
4. 子主题之间应互斥互补, 避免内容重叠
5. 返回 JSON 数组格式: ["子主题1", "子主题2", ...]

研究问题: {query}

初始上下文:
{context[:4000]}

请返回 3-{max_subtopics} 个子主题的 JSON 数组:"""

    def introduction_prompt(
        self,
        query: str,
        context: str,
        references: str,
        role_persona: str,
        tone: str,
        current_date: str,
        style_desc: str,
        word_min: int = 300,
        word_max: int = 500,
    ) -> str:
        # write_introduction
        # 用 SMART LLM 写引言, temperature=0.25
        return f"""{role_persona}

请基于以下上下文, 为「{query}」研究报告撰写引言部分.

要求:
1. 简述研究背景、目的与核心发现
2. 字数 {word_min}-{word_max} 字
3. 语气: {tone} (objective=客观, analytical=分析性, opinionated=观点鲜明, casual=通俗)
4. Web 源必须超链接引用: ([说明](url))
5. 不得编造未在上下文中出现的数据
6. 注入当前日期: {current_date}
7. 仅输出引言内容 (## 引言 标题下), 不含其他章节
8. 写作风格: {style_desc}
9. **严禁输出参考文献列表**: 引言内**不得**出现 `**参考文献**`/`**References**` 粗体块、`## 参考文献` 章节, 或 `[^xxx]:` Markdown 脚注定义 (如 `[^ref4]: 文献标题. 期刊. [链接](url)`). 参考文献列表由报告组装层在报告末尾统一追加, 引言内仅使用 `[n]` 行内编号引用或 `([说明](url))` 超链接引用.

上下文:
{context[:6000]}

参考文献来源:
{references}

请输出引言 (以 `## 引言` 开头):"""

    def section_prompt(
        self,
        topic: str,
        context: str,
        references: str,
        role_persona: str,
        tone: str,
        style_desc: str,
        word_min: int = 800,
        word_max: int = 1200,
    ) -> str:
        # write_section
        # 用 SMART LLM 写章节, temperature=0.35
        # 章节字数 500-1000 → 800-1200
        return f"""{role_persona}

请基于以下子主题上下文, 撰写「{topic}」章节内容.

【MUST 必须满足】:
1. **字数 {word_min}-{word_max} 字** (硬要求, 不达标视为失败)
2. **具体观点**: 每个论点 MUST 含具体数据/案例/数字, 严禁泛泛描述
3. **Markdown 表格**: 至少 1 个表格呈现对比数据 (多维度对比/趋势数据/竞争格局等)
4. **编号引用**: 行内引用使用 `[n]` 编号格式, 同时 Web 源附超链接 `([说明](url))`; 多个引用之间用空格分隔 (如 `[1] [2]`), 严禁紧贴
5. **结构化标题**: `##` 章节标题 + `###` 子小节. 子小节必须使用描述性标题或编号标题 (如 `### 1. 研究设计` 或 `### 研究设计与数据来源`), **严禁**使用"引言"/"总结"/"结论"等与报告级章节冲突的子标题 (引言和结论已由报告层级统一生成). 子小节编号格式统一为 `### 1. 标题` (数字+点+空格).
6. **来源可信度优先**: 优先引用 官方 > 学术 > 行业 > 自媒体
7. **严禁输出参考文献列表**: 章节内**不得**出现 `**参考文献**`/`**References**`/`**参考来源**` 粗体块、`## 参考文献`/`## 参考来源` 章节, 或 `[^xxx]:` Markdown 脚注定义 (如 `[^ref4]: 文献标题. 期刊. [链接](url)`). 参考文献列表由报告组装层在报告末尾统一追加 (`## 参考来源`), 章节内仅使用 `[n]` 行内编号引用, 不输出引用条目列表. 章节末尾不得出现 `---` 分隔线.

【Markdown 排版规范】(严格遵守, 确保可读性):
- **段落间距**: 段落之间必须空一行 (即两个 \\n), 严禁段落紧贴
- **标题前后**: 标题前后必须空一行; 严禁标题与正文紧贴
- **表格前后**: 表格前后必须空一行; 表格内部单元格不得有空行
- **列表前后**: 列表前后必须空一行; 列表项之间不空行
- **末尾清洁**: 章节末尾不得有多余空行

【软要求】:
- 语气: {tone}
- 不得编造未在上下文中出现的数据 (幻觉零容忍)
- 写作风格: {style_desc}
- 仅输出本章节内容 (`## {topic}` 下), 不含其他章节

子主题上下文:
{context[:6000]}

参考文献来源:
{references}

请输出本章节 (以 `## {topic}` 开头):"""

    def conclusion_prompt(
        self,
        query: str,
        sections_summary: str,
        role_persona: str,
        tone: str,
        style_desc: str,
        word_min: int = 300,
        word_max: int = 500,
    ) -> str:
        # write_conclusion
        # 用 SMART LLM 写结论, temperature=0.25
        return f"""{role_persona}

请基于以下已写章节内容, 为「{query}」研究报告撰写结论部分.

要求:
1. 总结核心发现与洞察 (含具体数据回引)
2. 提出未来展望与建议 (可操作性)
3. 字数 {word_min}-{word_max} 字
4. 语气: {tone} (objective=客观, analytical=分析性, opinionated=观点鲜明, casual=通俗)
5. 仅输出结论内容 (## 结论 标题下), 不含其他章节
6. 写作风格: {style_desc}

已写章节摘要:
{sections_summary[:6000]}

请输出结论 (以 `## 结论` 开头):"""


class EnglishPromptFamily(PromptFamily):
    """英文实现."""

    # Tone descriptions (adapted from industry practice 17 Tones, 8 selected for research scenarios)
    _TONE_DESCRIPTIONS: dict[str, str] = {
        "objective": "objective and neutral, fact-based, free of personal opinion",
        "analytical": "analytical, in-depth examination of causality and meaning behind data",
        "formal": "formal and rigorous, academic style, using technical terminology",
        "informative": "informative, focused on delivering practical knowledge for quick understanding",
        "explanatory": "explanatory, clarifying complex concepts, suitable for general audiences",
        "critical": "critical, examining multiple viewpoints, identifying limitations and gaps",
        "comparative": "comparative, horizontal and vertical comparisons, highlighting differences",
        "casual": "casual and relaxed, conversational tone, suitable for general readers",
    }

    def get_tone_prompt(self, tone: str) -> str:
        """Get Tone prompt fragment (adapted from industry practice 17 Tones, 8 selected).

        Unregistered tones fall back to objective for robustness.
        """
        desc = self._TONE_DESCRIPTIONS.get(tone, self._TONE_DESCRIPTIONS["objective"])
        return f"\n\n## Tone Requirement\n{desc}"

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
        report_style: str = "academic",
    ) -> str:
        # 英文版风格描述 (与 DefaultPromptFamily 对齐)
        style_map: dict[str, str] = {
            "academic": "Academic style: rigorous and objective, cite sources, formal language",
            "business": "Business style: concise, conclusion-first, business terminology",
            "casual": "Casual style: easy to understand, avoid jargon, suitable for general readers",
            "news": "News style: inverted pyramid, 5W1H, objective reporting",
        }
        style_desc = style_map.get(report_style, style_map["academic"])
        # align with industry practice writer_prompt (specific points + table + [n] citation)
        return f"""{agent_role}

Please write a research report on "{query}" based on the retrieved context below.

【MUST requirements】:
1. **Word count floor**: at least {word_limit} words (hard requirement, fail if not met)
2. **Specific points**: each argument MUST contain specific data/cases/numbers, vague descriptions forbidden (e.g., "market is large" → "2025 market size $1.2T, CAGR 18.5%")
3. **Markdown table**: at least 1 markdown table presenting comparison data
4. **Numbered citations**: in-text citations use `[n]` format (matching the reference list, e.g., "market size $1.2T[1]"), plus hyperlinks `([description](url))`
5. **Structured headings**: `#`/`##`/`###` three-level hierarchy
6. **Source credibility priority**: Official > Academic > Industry > Self-media; self-media sources must be marked "unverified"

【Soft requirements】:
- Tone: {tone} (objective/analytical/opinionated/casual)
- Reference list at the end (APA format, with [n] numbering)
- Current date: {current_date}
- Do not fabricate data not present in the context (zero hallucination)
- Report style: {style_desc}

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
        # align with industry practice SourceCurator (5 dimensions + Quantitative Value)
        # prompt 精简, 仅输出 index+score, 不输出 reason
        return f"""{agent_role}

Your task is: evaluate the relevance and credibility of the following search sources, and select the top {max_results} most worthy of citation.

【5-dimension evaluation criteria】 (aligned with industry practice SourceCurator):
1. **Relevance**: how related to the research question (0-10)
2. **Credibility**: source authority (official > academic journals > industry media > blogs)
3. **Currency**: information freshness (prioritize last 12 months)
4. **Objectivity**: whether there is obvious bias or commercial promotion
5. **Quantitative Value**: whether it contains specific numbers/percentages/amounts/statistics (sources with statistics are significantly prioritized over pure text descriptions)

【Principle】: Err on the side of inclusion — data-rich sources are preferred even if relevance is slightly lower.

Research question: {query}

Source list:
{sources_text}

Return a JSON array, each item containing only index (1-based) and score (0-10), no reason needed:
[{{"index": 1, "score": 9}}, ...]

Return only the JSON array of the top {max_results} most relevant sources:"""

    def agent_creator_prompt(self, query: str) -> str:
        return """You are a research assistant role selection expert. Choose the most appropriate research role persona based on the user's research query.

When generating a role persona, you must satisfy these three requirements:
1. Research methodology: clearly state the research method (e.g., systematic review, meta-analysis, case study, comparative analysis, quantitative modeling)
2. Output standards: reports must include data support, clear source citations, and logical structure
3. Language style: objective, professional, avoid subjective speculation

Here are some examples (format: task → response):
task: "query involves finance/investment/stocks/accounting" → response: {"server": "financial_analyst", "agent_role_prompt": "You are a senior financial analyst, expert in financial modeling, valuation, investment research. Research methodology: cross-validate multi-source data via quantitative analysis and financial modeling. Output standards: reports must include data support, clear source citations, and logical structure. Language style: objective, professional, avoid subjective speculation."}
task: "query involves business/market/strategy/management" → response: {"server": "business_analyst", "agent_role_prompt": "You are a senior business analyst, expert in market analysis, competitive strategy, business models. Research methodology: combine case studies and comparative analysis with frameworks like Porter's Five Forces. Output standards: reports must include data support, clear source citations, and logical structure. Language style: objective, professional, avoid subjective speculation."}
task: "query involves travel/tourism/hotels/itinerary" → response: {"server": "travel_agent", "agent_role_prompt": "You are a senior travel consultant, expert in destination recommendations, itinerary planning. Research methodology: aggregate multi-source information and match user preferences. Output standards: reports must include data support, clear source citations, and logical structure. Language style: objective, professional, avoid subjective speculation."}
task: "query involves medicine/healthcare/pharma/clinical" → response: {"server": "medical_researcher", "agent_role_prompt": "You are a medical research expert, expert in clinical trial analysis, medical literature review. Research methodology: prioritize evidence-based medicine via systematic review and meta-analysis. Output standards: reports must include data support, clear source citations, and logical structure. Language style: objective, professional, avoid subjective speculation."}
task: "query involves law/compliance/regulations/case law" → response: {"server": "legal_researcher", "agent_role_prompt": "You are a legal research expert, expert in regulatory interpretation, compliance analysis, case law research. Research methodology: combine case comparison with statutory textual interpretation. Output standards: reports must include data support, clear source citations, and logical structure. Language style: objective, professional, avoid subjective speculation."}
task: "query involves technology/engineering/IT/architecture" → response: {"server": "technology_researcher", "agent_role_prompt": "You are a technology research expert, expert in technology trends, architecture analysis, engineering practices. Research methodology: evaluate via technical research and comparative experiments. Output standards: reports must include data support, clear source citations, and logical structure. Language style: objective, professional, avoid subjective speculation."}
task: "query involves education/teaching/curriculum/learning" → response: {"server": "education_researcher", "agent_role_prompt": "You are an education research expert, expert in curriculum design, pedagogy, education policy analysis. Research methodology: combine literature review with controlled educational experiments. Output standards: reports must include data support, clear source citations, and logical structure. Language style: objective, professional, avoid subjective speculation."}
task: "query involves science/physics/chemistry/biology/astronomy" → response: {"server": "science_researcher", "agent_role_prompt": "You are a science research expert, expert in cross-disciplinary literature review, experimental design, scientific reasoning. Research methodology: systematic review with reproducibility verification. Output standards: reports must include data support, clear source citations, and logical structure. Language style: objective, professional, avoid subjective speculation."}
task: "query involves marketing/brand/advertising/user growth" → response: {"server": "marketing_researcher", "agent_role_prompt": "You are a marketing research expert, expert in consumer behavior, brand strategy, growth hacking. Research methodology: combine quantitative surveys and A/B testing with user interviews. Output standards: reports must include data support, clear source citations, and logical structure. Language style: objective, professional, avoid subjective speculation."}
task: "query involves environment/climate/sustainability/ecology" → response: {"server": "environment_researcher", "agent_role_prompt": "You are an environment and sustainability research expert, expert in climate change, ecological assessment, ESG analysis. Research methodology: life-cycle assessment combined with scenario modeling. Output standards: reports must include data support, clear source citations, and logical structure. Language style: objective, professional, avoid subjective speculation."}

Based on the user query, return JSON:
{"server": "role_short_name (English, snake_case, e.g. financial_analyst)", "agent_role_prompt": "Complete role persona description, must include research methodology, output standards, and language style"}

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

    # ========== detailed_report prompts (English) ==========

    def subtopics_prompt(
        self,
        query: str,
        context: str,
        role_persona: str,
        max_subtopics: int = 5,
    ) -> str:
        return f"""{role_persona}

Based on the following research question and initial context, decompose into 3-{max_subtopics} subtopics for in-depth chapter-by-chapter research.

Requirements:
1. Subtopics should cover different dimensions (e.g., market/technology/competition/policy/trends)
2. Subtopics must be based on content actually present in the context, no fabrication
3. Each subtopic should be a concise phrase
4. Subtopics should be mutually exclusive and collectively exhaustive
5. Return JSON array format: ["subtopic1", "subtopic2", ...]

Research question: {query}

Initial context:
{context[:4000]}

Return a JSON array of 3-{max_subtopics} subtopics:"""

    def introduction_prompt(
        self,
        query: str,
        context: str,
        references: str,
        role_persona: str,
        tone: str,
        current_date: str,
        style_desc: str,
        word_min: int = 300,
        word_max: int = 500,
    ) -> str:
        return f"""{role_persona}

Based on the following context, write the introduction section for the research report on "{query}".

Requirements:
1. Briefly describe research background, purpose, and core findings
2. Word count: {word_min}-{word_max} words
3. Tone: {tone}
4. Web sources must be hyperlinked: ([description](url))
5. Do not fabricate data not present in the context
6. Current date: {current_date}
7. Output only the introduction (under ## Introduction), no other sections
8. Writing style: {style_desc}
9. **NEVER output reference list**: The introduction MUST NOT contain `**References**`/`**Bibliography**` bold blocks, `## References` sections, or `[^xxx]:` Markdown footnote definitions (e.g., `[^ref4]: Title. Journal. [link](url)`). The reference list is appended by the report assembler at the end of the report. Within the introduction, only use `[n]` in-text numbered citations or `([description](url))` hyperlink citations.

Context:
{context[:6000]}

Reference sources:
{references}

Output the introduction (starting with `## Introduction`):"""

    def section_prompt(
        self,
        topic: str,
        context: str,
        references: str,
        role_persona: str,
        tone: str,
        style_desc: str,
        word_min: int = 800,
        word_max: int = 1200,
    ) -> str:
        return f"""{role_persona}

Based on the following subtopic context, write the "{topic}" section content.

【MUST requirements】:
1. **Word count: {word_min}-{word_max} words** (hard requirement)
2. **Specific points**: Each argument MUST contain specific data/cases/numbers, vague descriptions forbidden
3. **Markdown table**: At least 1 table presenting comparison data
4. **Numbered citations**: Use `[n]` format for in-text citations, plus hyperlinks `([description](url))`
5. **Structured headings**: `##` section title + `###` subsections. Subsections MUST use descriptive or numbered titles (e.g., `### 1. Research Design` or `### Research Design`). **NEVER** use "Introduction"/"Summary"/"Conclusion" as subsection titles (these are reserved for report-level sections). Use consistent numbering format `### 1. Title`.
6. **Source credibility priority**: Official > Academic > Industry > Self-media
7. **NEVER output reference list**: The section MUST NOT contain `**References**`/`**Bibliography**`/`**参考来源**` bold blocks, `## References`/`## Bibliography` sections, or `[^xxx]:` Markdown footnote definitions (e.g., `[^ref4]: Title. Journal. [link](url)`). The reference list is appended by the report assembler at the end of the report (`## References`). Within the section, only use `[n]` in-text numbered citations; do not output citation entry lists. The section MUST NOT end with a `---` separator.

【Soft requirements】:
- Tone: {tone}
- Do not fabricate data (zero hallucination)
- Writing style: {style_desc}
- Output only this section (under `## {topic}`), no other sections

Subtopic context:
{context[:6000]}

Reference sources:
{references}

Output this section (starting with `## {topic}`):"""

    def conclusion_prompt(
        self,
        query: str,
        sections_summary: str,
        role_persona: str,
        tone: str,
        style_desc: str,
        word_min: int = 300,
        word_max: int = 500,
    ) -> str:
        return f"""{role_persona}

Based on the following written section content, write the conclusion section for the research report on "{query}".

Requirements:
1. Summarize core findings and insights (with specific data references)
2. Propose future outlook and actionable recommendations
3. Word count: {word_min}-{word_max} words
4. Tone: {tone}
5. Output only the conclusion (under ## Conclusion), no other sections
6. Writing style: {style_desc}

Written section summaries:
{sections_summary[:6000]}

Output the conclusion (starting with `## Conclusion`):"""


# ========== 工厂路由 (注册表驱动, 与 scrapers/searchers 一致的模式) ==========

# 内置 PromptFamily 注册表: 项目内 family 静态注册, 第三方扩展通过
# register_prompt_family 动态注册. get_prompt_family 优先查询注册表.
_PROMPT_FAMILY_REGISTRY: dict[str, type[PromptFamily]] = {
    "default": DefaultPromptFamily,
    "english": EnglishPromptFamily,
}


def get_prompt_family(name: str = "default") -> PromptFamily:
    """工厂方法: 按 name 获取 PromptFamily 实例 (注册表驱动).

    优先查询 _PROMPT_FAMILY_REGISTRY 注册表; 未注册时降级为 DefaultPromptFamily
    (不抛异常, 保证研究流程不因配置错误中断).

    区域路由逻辑:
    - 用户明确指定 "english" → EnglishPromptFamily
    - 区域为 GLOBAL (无中文字符) → EnglishPromptFamily
    - 其他情况 → DefaultPromptFamily (中文)

    Args:
        name: 策略名称 ("default" | "english" | 自定义注册名)

    Returns:
        PromptFamily 实例
    """
    if name == "english":
        return EnglishPromptFamily()

    from src.config.settings import get_settings
    from src.skills.researcher.searchers import SearchRegion, detect_region

    settings = get_settings()
    if settings.prompt_family == "english":
        return EnglishPromptFamily()

    try:
        current_query = getattr(settings, "_current_query", "")
        if current_query:
            region = detect_region(current_query)
            if region == SearchRegion.GLOBAL:
                return EnglishPromptFamily()
    except Exception:
        pass

    cls = _PROMPT_FAMILY_REGISTRY.get(name)
    if cls is None:
        logger.warning("未注册的 PromptFamily '%s', 降级为 default", name)
        cls = DefaultPromptFamily
    return cls()
