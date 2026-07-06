"""AgentCreator LLM 动态角色生成器.

对标 GPTR actions/agent_creator.py + prompts.py auto_agent_instructions().
GPTR 设计哲学: 行业角色是运行时 LLM 推理产物, 无 if-else 行业分支.
LLM 根据查询语义自主选择最合适的角色.

GPTR 的 4 层隐形机制之一 (Prompt 层):
- Prompt 层: 本模块的 auto_agent_instructions() few-shot 例子让 LLM 自主生成 persona
- Config 层: settings.agent_role / ChatRequest.agent_role 注入 (优先级高于 LLM)
- Retriever 层: searchers/ 下含 arxiv/pubmed/semantic_scholar 等专业数据源
- MCP 层: MCP_SERVERS 注册行业专用工具服务器 (mcp_coordinator)

P1-Future-04: prompt 文本经 PromptFamily 策略注入 (支持中英多语言切换).
"""

from __future__ import annotations

import logging
from typing import Any

from src.common.json_utils import safe_json_parse
from src.config.settings import Settings, get_settings
from src.llm.client import LLMClient, LLMTier, get_llm_client
from src.observability.tracing import trace_chain
from src.skills.researcher.prompts import PromptFamily, get_prompt_family

logger = logging.getLogger(__name__)


class AgentCreator:
    """LLM 动态角色生成器 (对标 GPTR choose_agent).

    优先级 (对标 GPTR AGENT_ROLE 配置):
    1. 调用方传入 agent_role (settings.agent_role 或 ChatRequest.agent_role) → 直接使用
    2. 否则 LLM 根据查询语义动态生成行业 persona
    """

    settings: Settings
    _llm: LLMClient
    _prompt_family: PromptFamily

    # few-shot 例子 (对标 GPTR prompts.py:486-511 auto_agent_instructions)
    # LLM 根据这些例子自主推理, 不存在 if-else 行业分支
    # P1-Future-04: 保留为类属性供向后兼容, 实际使用 PromptFamily.agent_creator_prompt()
    # 任务 9 (对比 GPTR vs AIR 角色机制): 扩展到 10 个行业 + task/response 风格 + 三要素要求
    AUTO_AGENT_INSTRUCTIONS = """你是一个研究助手角色选择专家。根据用户的研究查询,选择最合适的研究角色 persona。

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

    # 兜底角色: LLM 生成失败且无 agent_role 配置时使用 (任务 9 优化 4)
    # 增强默认角色, 含研究方法论/输出规范/语言风格三要素
    _DEFAULT_AGENT_ROLE: dict[str, str] = {
        "server": "🔬 Research Agent",
        "agent_role_prompt": (
            "你是一位严谨的通用研究助手。你的职责是基于检索到的资料，"
            "生成客观、准确、有来源支撑的研究报告。"
            "研究方法论: 采用系统综述方法，交叉验证多源信息。"
            "输出规范: 报告需含数据支撑、明确引用来源、逻辑结构清晰。"
            "语言风格: 客观、专业、避免主观臆断。"
        ),
    }

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        prompt_family: PromptFamily | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or get_llm_client()
        self._prompt_family = prompt_family or get_prompt_family(self.settings.prompt_family)

    async def create_agent(
        self,
        query: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
        agent_role: str | None = None,
    ) -> dict[str, Any]:
        """LLM 动态生成研究角色 (对标 GPTR choose_agent).

        Args:
            query: 用户研究查询
            user_id: 用户 ID (隔离键, AGENTS.md 第 8 章)
            session_id: 会话 ID (隔离键, AGENTS.md 第 6 章)
            agent_role: 调用方注入的角色 persona (对标 GPTR AGENT_ROLE 配置),
                        优先级高于 LLM 自动生成, 非空时直接使用, 跳过 LLM 调用.

        Returns:
            {"server": str, "agent_role_prompt": str}
            - server: 角色简称 (对标 GPTR server 字段)
            - agent_role_prompt: 完整角色 persona 描述 (对标 GPTR agent_role_prompt)
        """
        async with trace_chain(
            name="agent-creator",
            input={"query": query[:100], "has_preset_role": bool(agent_role)},
            user_id=user_id,
            session_id=session_id,
        ) as span:
            # 对标 GPTR: AGENT_ROLE 配置优先级高于 LLM 自动生成
            if agent_role:
                span.update(
                    output={"server": "custom", "source": "preset"},
                    metadata={"role_source": "preset"},
                )
                return {"server": "custom", "agent_role_prompt": agent_role}

            # LLM 动态角色生成
            result = await self._generate_via_llm(
                query,
                user_id=user_id,
                session_id=session_id,
            )

            span.update(
                output={"server": result.get("server", "researcher"), "source": "llm"},
                metadata={"role_source": "llm"},
            )
            return result

    async def _generate_via_llm(
        self,
        query: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """LLM 动态生成角色 persona (对标 GPTR actions/agent_creator.py:18-62).

        V2-P1 优化 (对标 GPTR choose_agent):
        - tier: FAST → SMART (GPTR 用 smart_llm_model, 角色生成需更精准)
        - temperature: 0.0 → 0.15 (GPTR 用 0.15, 略带随机性生成多样化角色)
        """
        try:
            messages = [
                {"role": "system", "content": self._prompt_family.agent_creator_prompt(query)},
                {"role": "user", "content": f"研究查询: {query}"},
            ]
            response = await self._llm.achat(
                messages,
                tier=LLMTier.SMART,  # V2-P1: FAST → SMART (对标 GPTR smart_llm_model)
                temperature=0.15,  # V2-P1: 0.0 → 0.15 (对标 GPTR choose_agent temp)
                user_id=user_id,
                session_id=session_id,
                span_name="agent-creator-llm",
                step="agent_creator",
            )

            result = safe_json_parse(
                response.content,
                fallback=dict(self._DEFAULT_AGENT_ROLE),
            )
            if not isinstance(result, dict):
                return dict(self._DEFAULT_AGENT_ROLE)

            # 字段校验与兜底 (缺失字段用 _DEFAULT_AGENT_ROLE 对应值补齐)
            server = str(result.get("server") or self._DEFAULT_AGENT_ROLE["server"])
            agent_role_prompt = str(
                result.get("agent_role_prompt") or self._DEFAULT_AGENT_ROLE["agent_role_prompt"]
            )
            return {"server": server, "agent_role_prompt": agent_role_prompt}
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM 动态角色生成失败, 使用兜底角色: %s", e)
            return dict(self._DEFAULT_AGENT_ROLE)
