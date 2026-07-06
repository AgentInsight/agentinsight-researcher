"""AG2 编排器.

对标 GPT Researcher multi_agents_ag2/agents/orchestrator.py.
用 autogen GroupChat + GroupChatManager 编排 4 个角色:
Researcher → Writer → Reviewer → Publisher.

设计要点 (AGENTS.md 合规):
- AG2 仅作为编排层, ConversableAgent 的 llm_config=False, 禁用 AG2 内置 LLM.
- 所有 LLM 调用经 LLMClient (LiteLLM), 复用现有 Skill 组件.
- 每个 Agent 的 reply 函数包裹在 trace_chain span 内 (AGENTS.md 第 10 章).
- AG2 (autogen) 是可选依赖, try/except import, 未安装时 _AG2_AVAILABLE=False.
"""

from __future__ import annotations

import logging
from typing import Any

from src.agents.ag2.agents import (
    AGENTS_ORDER,
    MAX_ROUNDS,
    PUBLISHER_SYSTEM_PROMPT,
    RESEARCHER_SYSTEM_PROMPT,
    REVIEWER_SYSTEM_PROMPT,
    WRITER_SYSTEM_PROMPT,
    publish_complete_msg,
    report_ready_msg,
    research_complete_msg,
    review_complete_msg,
)
from src.agents.researcher.reviewer import Reviewer
from src.config.settings import Settings, get_settings
from src.llm.client import LLMClient
from src.observability.tracing import trace_agent, trace_chain
from src.skills.researcher.publisher import Publisher
from src.skills.researcher.report_generator import ReportGenerator
from src.skills.researcher.research_conductor import ResearchConductor

logger = logging.getLogger(__name__)

# ========== AG2 (autogen) 可选依赖, try/except import ==========
# 支持两种包名: ag2 (新版) / autogen (旧版 pyautogen)
# 任一导入成功即 _AG2_AVAILABLE=True; 均失败则降级为不可用, 调用 run() 时抛 ImportError.
_AG2_AVAILABLE: bool = False

try:
    from autogen import (
        ConversableAgent,
        GroupChat,
        GroupChatManager,
    )

    _AG2_AVAILABLE = True
except ImportError:
    try:
        from ag2 import (
            ConversableAgent,
            GroupChat,
            GroupChatManager,
        )

        _AG2_AVAILABLE = True
    except ImportError:
        # 均未安装: 设为 None 以便模块可导入, 运行时检查 _AG2_AVAILABLE
        ConversableAgent = None
        GroupChat = None
        GroupChatManager = None


class AG2Orchestrator:
    """AG2 编排器: 用 GroupChat + GroupChatManager 编排 4 个角色.

    对标 GPT Researcher multi_agents_ag2/orchestrator.py.

    角色与 Skill 组件映射:
    - Researcher → ResearchConductor (多源检索 + 上下文聚合)
    - Writer → ReportGenerator (报告合成)
    - Reviewer → Reviewer (多维度审核)
    - Publisher → Publisher (格式化发布)

    AG2 仅负责编排对话顺序, 所有 LLM 调用经 LLMClient (LiteLLM).
    ConversableAgent 的 llm_config=False, 禁用 AG2 内置 LLM.
    """

    settings: Settings
    _llm: LLMClient
    _research_conductor: ResearchConductor
    _report_generator: ReportGenerator
    _reviewer: Reviewer
    _publisher: Publisher
    _state: dict[str, Any]

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        research_conductor: ResearchConductor | None = None,
        report_generator: ReportGenerator | None = None,
        reviewer: Reviewer | None = None,
        publisher: Publisher | None = None,
    ) -> None:
        """初始化 AG2 编排器.

        Args:
            settings: 全局配置, 默认 get_settings()
            llm: LLM 客户端, 默认 LLMClient(settings)
            research_conductor: 研究执行器, 默认自动创建
            report_generator: 报告生成器, 默认自动创建
            reviewer: 报告审核器, 默认自动创建
            publisher: 报告发布器, 默认自动创建
        """
        self.settings = settings or get_settings()
        self._llm = llm or LLMClient(self.settings)
        self._research_conductor = research_conductor or ResearchConductor(
            settings=self.settings, llm=self._llm
        )
        self._report_generator = report_generator or ReportGenerator(
            settings=self.settings, llm=self._llm
        )
        self._reviewer = reviewer or Reviewer(settings=self.settings, llm=self._llm)
        self._publisher = publisher or Publisher(settings=self.settings)
        self._state: dict[str, Any] = {}

    async def run(
        self,
        query: str,
        *,
        agent_role: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        report_type: str = "basic_report",
        report_format: str = "markdown",
        tone: str = "objective",
    ) -> dict[str, Any]:
        """编排 4 个 AG2 Agent 完成研究任务.

        流程: user_proxy 发送查询 → Researcher 检索 → Writer 写报告 → Reviewer 审核 → Publisher 发布.

        Args:
            query: 研究查询
            agent_role: 角色 persona (对标 GPTR AGENT_ROLE, 可选)
            user_id: 用户 ID (隔离键, AGENTS.md 第 8 章)
            session_id: 会话 ID (隔离键, AGENTS.md 第 6 章)
            report_type: 报告类型 (basic_report / detailed_report)
            report_format: 输出格式 (markdown / html / pdf / docx / json)
            tone: 语气 (objective / analytical / opinionated / casual)

        Returns:
            研究结果 dict, 含 query / contexts / sources / report_md /
            review_decision / review_feedback / published 等字段.

        Raises:
            ImportError: AG2 (autogen) 未安装时抛出.
        """
        if not _AG2_AVAILABLE:
            raise ImportError(
                "AG2 (autogen) 未安装, 请执行 pip install ag2 或 pip install pyautogen"
            )

        # 初始化共享状态 (各 Agent reply 函数读写此 dict, 避免通过消息传递大块数据)
        self._state = {
            "query": query,
            "agent_role": agent_role,
            "user_id": user_id,
            "session_id": session_id,
            "report_type": report_type,
            "report_format": report_format,
            "tone": tone,
            "contexts": [],
            "sources": [],
            "report_md": "",
            "review_decision": "",
            "review_feedback": "",
            "published": None,
        }

        async with trace_agent(
            name="ag2-orchestrator",
            input={"query": query[:100], "agent_role": agent_role or ""},
            metadata={
                "session_id": session_id or "",
                "intent": "research",
                "framework": "ag2",
            },
            session_id=session_id,
            user_id=user_id,
        ) as span:
            # 1. 创建 4 个 AG2 Agent
            researcher_agent = self._create_researcher_agent()
            writer_agent = self._create_writer_agent()
            reviewer_agent = self._create_reviewer_agent()
            publisher_agent = self._create_publisher_agent()

            # 2. 创建 user_proxy (发送初始消息, 不参与后续对话)
            # user_proxy 不在 GroupChat agents 列表内, 仅用于触发对话
            user_proxy = self._create_user_proxy()

            # 3. 创建 GroupChat + GroupChatManager
            group_chat = GroupChat(
                agents=[researcher_agent, writer_agent, reviewer_agent, publisher_agent],
                messages=[],
                max_round=MAX_ROUNDS,
                speaker_selection_method=self._speaker_selection,
            )
            manager = GroupChatManager(
                groupchat=group_chat,
                llm_config=False,
                name="ag2_manager",
            )

            # 4. 启动 GroupChat (user_proxy 发送初始查询, 触发 4 个 Agent 依次发言)
            await user_proxy.a_initiate_chat(
                manager,
                message=f"研究请求: {query}",
                clear_history=True,
            )

            span.update(
                output={
                    "contexts_count": len(self._state["contexts"]),
                    "sources_count": len(self._state["sources"]),
                    "report_len": len(self._state["report_md"]),
                    "review_decision": self._state["review_decision"],
                    "has_published": self._state["published"] is not None,
                }
            )

        return self._state

    # ========== Agent 创建方法 ==========

    def _create_user_proxy(self) -> Any:
        """创建 user_proxy Agent (发送初始查询, 不参与后续对话)."""
        return ConversableAgent(
            name="user_proxy",
            system_message="你是用户代理, 只负责发送初始研究请求, 不参与后续对话.",
            llm_config=False,
            human_input_mode="NEVER",
            max_consecutive_auto_reply=0,
        )

    def _create_researcher_agent(self) -> Any:
        """创建 Researcher Agent (调用 ResearchConductor 检索)."""
        agent = ConversableAgent(
            name="researcher",
            system_message=RESEARCHER_SYSTEM_PROMPT,
            llm_config=False,
            human_input_mode="NEVER",
        )
        agent.register_reply(
            trigger=ConversableAgent,
            reply_func=self._researcher_reply,
            position=0,
        )
        return agent

    def _create_writer_agent(self) -> Any:
        """创建 Writer Agent (调用 ReportGenerator 写报告)."""
        agent = ConversableAgent(
            name="writer",
            system_message=WRITER_SYSTEM_PROMPT,
            llm_config=False,
            human_input_mode="NEVER",
        )
        agent.register_reply(
            trigger=ConversableAgent,
            reply_func=self._writer_reply,
            position=0,
        )
        return agent

    def _create_reviewer_agent(self) -> Any:
        """创建 Reviewer Agent (调用 Reviewer 审核报告)."""
        agent = ConversableAgent(
            name="reviewer",
            system_message=REVIEWER_SYSTEM_PROMPT,
            llm_config=False,
            human_input_mode="NEVER",
        )
        agent.register_reply(
            trigger=ConversableAgent,
            reply_func=self._reviewer_reply,
            position=0,
        )
        return agent

    def _create_publisher_agent(self) -> Any:
        """创建 Publisher Agent (调用 Publisher 发布报告)."""
        agent = ConversableAgent(
            name="publisher",
            system_message=PUBLISHER_SYSTEM_PROMPT,
            llm_config=False,
            human_input_mode="NEVER",
        )
        agent.register_reply(
            trigger=ConversableAgent,
            reply_func=self._publisher_reply,
            position=0,
        )
        return agent

    # ========== Agent reply 函数 (异步, 调用现有 Skill 组件) ==========

    async def _researcher_reply(
        self,
        recipient: Any,
        messages: list[dict[str, Any]] | str | None,  # noqa: ARG002 (autogen 协议参数, 未使用)
        sender: Any,  # noqa: ARG002 (autogen 协议参数, 未使用)
        config: Any | None,  # noqa: ARG002 (autogen 协议参数, 未使用)
    ) -> tuple[bool, str | None]:
        """Researcher 回复: 调用 ResearchConductor 进行多源检索与上下文聚合.

        复用现有 ResearchConductor, 所有 LLM / 搜索 / 抓取调用经现有管道.
        """
        async with trace_chain(
            name="ag2-researcher",
            input={"query": self._state["query"][:100]},
            user_id=self._state.get("user_id"),
            session_id=self._state.get("session_id"),
        ) as span:
            try:
                result = await self._research_conductor.conduct_research(
                    self._state["query"],
                    agent_role=self._state.get("agent_role"),
                    user_id=self._state.get("user_id"),
                    session_id=self._state.get("session_id"),
                )
                self._state["contexts"] = result.get("contexts", [])
                self._state["sources"] = result.get("sources", [])

                msg = research_complete_msg(
                    len(self._state["contexts"]),
                    len(self._state["sources"]),
                )
                span.update(
                    output={
                        "contexts_count": len(self._state["contexts"]),
                        "sources_count": len(self._state["sources"]),
                    }
                )
                return True, msg
            except Exception as e:  # noqa: BLE001
                logger.exception("AG2 Researcher 执行失败: %s", e)
                span.update(output={"error": str(e)})
                return True, f"研究失败: {e}"

    async def _writer_reply(
        self,
        recipient: Any,  # noqa: ARG002
        messages: list[dict[str, Any]] | str | None,  # noqa: ARG002
        sender: Any,  # noqa: ARG002
        config: Any | None,  # noqa: ARG002
    ) -> tuple[bool, str | None]:
        """Writer 回复: 调用 ReportGenerator 基于上下文生成报告.

        复用现有 ReportGenerator, 所有 LLM 调用经 LLMClient (LiteLLM).
        """
        async with trace_chain(
            name="ag2-writer",
            input={
                "query": self._state["query"][:100],
                "contexts_count": len(self._state["contexts"]),
            },
            user_id=self._state.get("user_id"),
            session_id=self._state.get("session_id"),
        ) as span:
            try:
                result = await self._report_generator.generate_report(
                    query=self._state["query"],
                    contexts=self._state["contexts"],
                    sources=self._state["sources"],
                    report_type=self._state["report_type"],
                    tone=self._state["tone"],
                    agent_role=self._state.get("agent_role"),
                    user_id=self._state.get("user_id"),
                    session_id=self._state.get("session_id"),
                )
                self._state["report_md"] = result.get("report_md", "")
                self._state["report_image_url"] = result.get("image_url")
                self._state["report_image_b64"] = result.get("image_b64")

                msg = report_ready_msg(len(self._state["report_md"]))
                span.update(
                    output={
                        "report_len": len(self._state["report_md"]),
                    }
                )
                return True, msg
            except Exception as e:  # noqa: BLE001
                logger.exception("AG2 Writer 执行失败: %s", e)
                span.update(output={"error": str(e)})
                return True, f"报告生成失败: {e}"

    async def _reviewer_reply(
        self,
        recipient: Any,  # noqa: ARG002
        messages: list[dict[str, Any]] | str | None,  # noqa: ARG002
        sender: Any,  # noqa: ARG002
        config: Any | None,  # noqa: ARG002
    ) -> tuple[bool, str | None]:
        """Reviewer 回复: 调用 Reviewer 对报告进行多维度审核.

        复用现有 Reviewer, 构造 ResearcherState 传入.
        """
        async with trace_chain(
            name="ag2-reviewer",
            input={
                "query": self._state["query"][:100],
                "report_len": len(self._state["report_md"]),
            },
            user_id=self._state.get("user_id"),
            session_id=self._state.get("session_id"),
        ) as span:
            try:
                # 构造 ResearcherState (Reviewer.review 需要, TypedDict total=False)
                state: dict[str, Any] = {
                    "query": self._state["query"],
                    "report_md": self._state["report_md"],
                    "contexts": self._state["contexts"],
                    "agent_role": self._state.get("agent_role") or "",
                }
                result = await self._reviewer.review(
                    state,  # type: ignore[arg-type]
                    user_id=self._state.get("user_id"),
                    session_id=self._state.get("session_id"),
                )
                self._state["review_decision"] = result.get("review_decision", "")
                self._state["review_feedback"] = result.get("review_feedback", "")

                msg = review_complete_msg(self._state["review_decision"])
                span.update(
                    output={
                        "review_decision": self._state["review_decision"],
                    }
                )
                return True, msg
            except Exception as e:  # noqa: BLE001
                logger.exception("AG2 Reviewer 执行失败: %s", e)
                span.update(output={"error": str(e)})
                return True, f"审核失败: {e}"

    async def _publisher_reply(
        self,
        recipient: Any,  # noqa: ARG002
        messages: list[dict[str, Any]] | str | None,  # noqa: ARG002
        sender: Any,  # noqa: ARG002
        config: Any | None,  # noqa: ARG002
    ) -> tuple[bool, str | None]:
        """Publisher 回复: 调用 Publisher 将报告发布为指定格式.

        复用现有 Publisher, 支持 Markdown / HTML / PDF / DOCX / JSON 输出.
        """
        async with trace_chain(
            name="ag2-publisher",
            input={
                "report_len": len(self._state["report_md"]),
                "format": self._state["report_format"],
            },
            user_id=self._state.get("user_id"),
            session_id=self._state.get("session_id"),
        ) as span:
            try:
                result = await self._publisher.publish(
                    self._state["report_md"],
                    output_format=self._state["report_format"],
                    title=self._state["query"],
                    sources=self._state["sources"],
                    agent_role_server="",
                    research_mode="basic",
                    user_id=self._state.get("user_id"),
                    session_id=self._state.get("session_id"),
                )
                self._state["published"] = result

                msg = publish_complete_msg(result.get("format", "unknown"))
                span.update(
                    output={
                        "format": result.get("format", "unknown"),
                    }
                )
                return True, msg
            except Exception as e:  # noqa: BLE001
                logger.exception("AG2 Publisher 执行失败: %s", e)
                span.update(output={"error": str(e)})
                return True, f"发布失败: {e}"

    # ========== GroupChat 发言者选择 (固定顺序) ==========

    @staticmethod
    def _speaker_selection(last_speaker: Any, groupchat: Any) -> Any:
        """按固定顺序选择下一个发言者: researcher → writer → reviewer → publisher → None.

        所有 Agent 发言完毕后返回 None, 终止 GroupChat.
        last_speaker 为 None 或不在 AGENTS_ORDER 中时 (如 user_proxy), 选择 researcher 开场.

        Args:
            last_speaker: 上一个发言的 Agent
            groupchat: GroupChat 实例

        Returns:
            下一个发言的 Agent, 或 None 终止对话.
        """
        if last_speaker is None or last_speaker.name not in AGENTS_ORDER:
            return groupchat.agent_by_name("researcher")

        idx = AGENTS_ORDER.index(last_speaker.name)
        if idx + 1 >= len(AGENTS_ORDER):
            # 所有 Agent 已发言, 结束对话
            return None
        return groupchat.agent_by_name(AGENTS_ORDER[idx + 1])
