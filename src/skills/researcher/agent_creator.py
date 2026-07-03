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
from src.llm.client import LLMClient, LLMTier
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
    AUTO_AGENT_INSTRUCTIONS = """你是一个研究助手角色选择专家。根据用户的研究查询,选择最合适的研究角色 persona。

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

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        prompt_family: PromptFamily | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or LLMClient(self.settings)
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
        """LLM 动态生成角色 persona (对标 GPTR actions/agent_creator.py:18-62)."""
        try:
            messages = [
                {"role": "system", "content": self._prompt_family.agent_creator_prompt(query)},
                {"role": "user", "content": f"研究查询: {query}"},
            ]
            response = await self._llm.achat(
                messages,
                tier=LLMTier.FAST,
                temperature=0.0,
                user_id=user_id,
                session_id=session_id,
                span_name="agent-creator-llm",
                step="agent_creator",
            )

            result = safe_json_parse(
                response.content,
                fallback={
                    "server": "researcher",
                    "agent_role_prompt": "你是一位资深研究分析专家, 擅长多领域综合研究, 研究重点是全面、客观地分析问题.",
                },
            )
            if not isinstance(result, dict):
                return {
                    "server": "researcher",
                    "agent_role_prompt": "你是一位资深研究分析专家, 擅长多领域综合研究, 研究重点是全面、客观地分析问题.",
                }

            # 字段校验与兜底
            server = str(result.get("server") or "researcher")
            agent_role_prompt = str(
                result.get("agent_role_prompt")
                or "你是一位资深研究分析专家, 擅长多领域综合研究, 研究重点是全面、客观地分析问题."
            )
            return {"server": server, "agent_role_prompt": agent_role_prompt}
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM 动态角色生成失败, 使用兜底角色: %s", e)
            return {
                "server": "researcher",
                "agent_role_prompt": "你是一位资深研究分析专家, 擅长多领域综合研究, 研究重点是全面、客观地分析问题.",
            }
