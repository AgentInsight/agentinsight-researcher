"""单元测试: ChatAgent 对话式追问 Agent.

验证 src/agents/researcher/chat_agent.py:
- _assess_chat_complexity: cascade 路由 (短查询→FAST / 复杂关键词→SMART)
- chat: 基于报告 + 历史消息回答追问 (含 system prompt 路径分支)
- chat: SMART 失败降级 FAST (FrugalGPT cascade)
- chat: 双层失败兜底 (FAST 也失败 → 返回错误 AIMessage)
- _convert_messages: langchain BaseMessage → OpenAI dict 格式
- chat_node: 节点纯函数包装

节点为纯函数, 单一职责无副作用.
LLM 调用经 llm/ 网关 (LiteLLM).
trace_chain span 包裹.
单元测试不依赖外部服务 (LLM/PromptFamily 全部 mock).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.agents.researcher.chat_agent import ChatAgent, chat_node
from src.config.settings import Settings
from src.llm.client import LLMResponse, LLMTier

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture()
def settings() -> Settings:
    """构造 ChatAgent 测试 Settings (覆盖 cascade 阈值)."""
    return Settings(
        _env_file=None,
        chat_temperature=0.5,
        chat_max_tokens=4000,
        chat_history_limit=10,
        chat_report_truncate_chars=50_000,
        chat_simple_query_threshold_chars=20,
        chat_complex_keywords=("对比", "分析", "为什么", "展开", "评估", "论证"),
    )


@pytest.fixture()
def mock_llm() -> MagicMock:
    """Mock LLMClient (achat)."""
    llm = MagicMock()
    llm.achat = AsyncMock()
    return llm


@pytest.fixture()
def mock_prompt_family() -> MagicMock:
    """Mock PromptFamily.chat_prompt."""
    pf = MagicMock()
    pf.chat_prompt.return_value = "rendered chat system prompt"
    return pf


@pytest.fixture()
def agent(
    settings: Settings,
    mock_llm: MagicMock,
    mock_prompt_family: MagicMock,
) -> ChatAgent:
    """构造 ChatAgent (依赖全部 mock)."""
    return ChatAgent(
        settings=settings,
        llm=mock_llm,
        prompt_family=mock_prompt_family,
    )


def _make_llm_response(content: str = "AI 回复") -> LLMResponse:
    """构造 LLMResponse."""
    return LLMResponse(
        content=content,
        model="zhipuai/glm-4-flash",
        input_tokens=15,
        output_tokens=30,
        cost_usd=0.0002,
    )


# ========== _assess_chat_complexity: cascade 路由 ==========


class TestAssessChatComplexity:
    """_assess_chat_complexity cascade 路由测试."""

    def test_short_query_returns_fast(self, agent: ChatAgent) -> None:
        """短查询 (< chat_simple_query_threshold_chars) → FAST."""
        # 阈值 20, 5 字符查询应判定 FAST
        tier = agent._assess_chat_complexity("你好")
        assert tier == LLMTier.FAST

    def test_short_query_at_threshold_boundary(self, agent: ChatAgent) -> None:
        """长度等于阈值 (不小于阈值) 时不走 FAST 路径."""
        # len(query) < threshold 才走 FAST, == threshold 时不走
        # 阈值 20, 长度 20 + 无复杂关键词 → FAST (默认)
        tier = agent._assess_chat_complexity("a" * 20)
        assert tier == LLMTier.FAST

    def test_long_query_without_complex_keywords_returns_fast(
        self, agent: ChatAgent
    ) -> None:
        """长查询但无复杂关键词 → 默认 FAST (闲聊/简单追问)."""
        # 长查询 + 无复杂关键词 → 默认 FAST
        tier = agent._assess_chat_complexity("这是一个普通的较长问题但不含复杂关键词所以走fast")
        assert tier == LLMTier.FAST

    @pytest.mark.parametrize(
        "keyword",
        ["对比", "分析", "为什么", "展开", "评估", "论证"],
    )
    def test_complex_keyword_triggers_smart(self, agent: ChatAgent, keyword: str) -> None:
        """长查询命中复杂关键词 → SMART."""
        query = f"这是一个较长的查询包含{keyword}关键词所以走smart"
        # 确保长度 >= 阈值 (20)
        assert len(query) >= 20
        tier = agent._assess_chat_complexity(query)
        assert tier == LLMTier.SMART

    def test_short_query_with_complex_keyword_still_fast(self, agent: ChatAgent) -> None:
        """短查询 (< 阈值) 即使含复杂关键词也走 FAST (长度优先)."""
        # 短查询 < 20 字符先判定 FAST, 不检查复杂关键词
        tier = agent._assess_chat_complexity("分析")
        assert tier == LLMTier.FAST


# ========== chat: 基础场景 ==========


class TestChatBasic:
    """chat() 基础场景测试."""

    @pytest.mark.asyncio
    async def test_chat_returns_messages_with_human_and_ai(
        self,
        agent: ChatAgent,
        mock_llm: MagicMock,
    ) -> None:
        """chat() 应返回 [HumanMessage, AIMessage] 列表."""
        mock_llm.achat.return_value = _make_llm_response("AI 回答")
        state: dict[str, Any] = {
            "query": "你好",
            "report_md": "# 报告",
            "messages": [],
        }

        result = await agent.chat(state, user_id="u1", session_id="s1")

        assert "messages" in result
        messages = result["messages"]
        assert len(messages) == 2
        assert isinstance(messages[0], HumanMessage)
        assert isinstance(messages[1], AIMessage)
        assert messages[0].content == "你好"
        assert messages[1].content == "AI 回答"

    @pytest.mark.asyncio
    async def test_chat_uses_prompt_family_when_report_present(
        self,
        agent: ChatAgent,
        mock_llm: MagicMock,
        mock_prompt_family: MagicMock,
    ) -> None:
        """report_md 非空 → 使用 PromptFamily.chat_prompt 生成 system prompt."""
        mock_llm.achat.return_value = _make_llm_response("ok")
        state: dict[str, Any] = {
            "query": "请总结一下",
            "report_md": "# 完整报告内容",
            "messages": [],
            "agent_role": "金融分析师",
        }

        await agent.chat(state)

        mock_prompt_family.chat_prompt.assert_called_once()
        call_kwargs = mock_prompt_family.chat_prompt.call_args.kwargs
        assert call_kwargs["query"] == "请总结一下"
        assert call_kwargs["report_md"] == "# 完整报告内容"
        assert call_kwargs["agent_role"] == "金融分析师"

    @pytest.mark.asyncio
    async def test_chat_uses_generic_prompt_when_report_empty(
        self,
        agent: ChatAgent,
        mock_llm: MagicMock,
        mock_prompt_family: MagicMock,
    ) -> None:
        """report_md 为空 (首轮 chat) → 使用通用 system prompt, 不调 chat_prompt."""
        mock_llm.achat.return_value = _make_llm_response("ok")
        state: dict[str, Any] = {
            "query": "你好",
            "report_md": "",  # 首轮, 报告为空
            "messages": [],
        }

        await agent.chat(state)

        # 不应调用 chat_prompt (报告为空, 用通用提示)
        mock_prompt_family.chat_prompt.assert_not_called()

    @pytest.mark.asyncio
    async def test_chat_passes_temperature_and_max_tokens(
        self,
        agent: ChatAgent,
        mock_llm: MagicMock,
        settings: Settings,
    ) -> None:
        """chat() 应使用 settings.chat_temperature / chat_max_tokens."""
        mock_llm.achat.return_value = _make_llm_response("ok")
        state: dict[str, Any] = {"query": "你好", "report_md": "", "messages": []}

        await agent.chat(state)

        call_kwargs = mock_llm.achat.call_args.kwargs
        assert call_kwargs["temperature"] == settings.chat_temperature
        assert call_kwargs["max_tokens"] == settings.chat_max_tokens

    @pytest.mark.asyncio
    async def test_chat_passes_user_and_session_to_llm(
        self,
        agent: ChatAgent,
        mock_llm: MagicMock,
    ) -> None:
        """chat() 应将 user_id/session_id 透传给 LLM 调用."""
        mock_llm.achat.return_value = _make_llm_response("ok")
        state: dict[str, Any] = {"query": "你好", "report_md": "", "messages": []}

        await agent.chat(state, user_id="user-123", session_id="sess-456")

        call_kwargs = mock_llm.achat.call_args.kwargs
        assert call_kwargs["user_id"] == "user-123"
        assert call_kwargs["session_id"] == "sess-456"

    @pytest.mark.asyncio
    async def test_chat_truncates_report_md(
        self,
        mock_llm: MagicMock,
        mock_prompt_family: MagicMock,
    ) -> None:
        """report_md 超过 chat_report_truncate_chars 应截断."""
        # 截断阈值设小
        custom_settings = Settings(
            _env_file=None,
            chat_report_truncate_chars=10,
        )
        agent = ChatAgent(
            settings=custom_settings,
            llm=mock_llm,
            prompt_family=mock_prompt_family,
        )
        mock_llm.achat.return_value = _make_llm_response("ok")
        long_report = "A" * 100  # 远超 10
        state: dict[str, Any] = {
            "query": "总结",
            "report_md": long_report,
            "messages": [],
        }

        await agent.chat(state)

        call_kwargs = mock_prompt_family.chat_prompt.call_args.kwargs
        # 应截断到 10 字符
        assert len(call_kwargs["report_md"]) == 10
        assert call_kwargs["report_md"] == "A" * 10

    @pytest.mark.asyncio
    async def test_chat_history_truncated_to_limit(
        self,
        mock_llm: MagicMock,
        mock_prompt_family: MagicMock,
    ) -> None:
        """历史 messages 应取最近 chat_history_limit 条."""
        custom_settings = Settings(
            _env_file=None,
            chat_history_limit=3,
        )
        agent = ChatAgent(
            settings=custom_settings,
            llm=mock_llm,
            prompt_family=mock_prompt_family,
        )
        mock_llm.achat.return_value = _make_llm_response("ok")
        # 构造 10 条历史消息
        history = [
            HumanMessage(content=f"msg {i}") if i % 2 == 0 else AIMessage(content=f"reply {i}")
            for i in range(10)
        ]
        state: dict[str, Any] = {
            "query": "你好",
            "report_md": "# 报告",
            "messages": history,
        }

        await agent.chat(state)

        # achat 调用的 messages 列表: 1 system + history(3) + 1 user = 5
        call_args = mock_llm.achat.call_args.args
        messages_list = call_args[0]
        assert len(messages_list) == 5  # 1 system + 3 history + 1 user
        # 最近 3 条应为 history 末尾 3 条
        assert messages_list[1]["content"] == "reply 7"
        assert messages_list[2]["content"] == "msg 8"
        assert messages_list[3]["content"] == "reply 9"

    @pytest.mark.asyncio
    async def test_chat_empty_response_uses_placeholder(
        self,
        agent: ChatAgent,
        mock_llm: MagicMock,
    ) -> None:
        """LLM 返回空内容 (strip 后) → 用 '(无响应)' 占位."""
        mock_llm.achat.return_value = _make_llm_response("   ")  # 空白
        state: dict[str, Any] = {"query": "你好", "report_md": "", "messages": []}

        result = await agent.chat(state)

        ai_msg = result["messages"][1]
        assert ai_msg.content == "(无响应)"

    @pytest.mark.asyncio
    async def test_chat_passes_correct_tier_for_short_query(
        self,
        agent: ChatAgent,
        mock_llm: MagicMock,
    ) -> None:
        """短查询 → achat 应使用 tier=FAST."""
        mock_llm.achat.return_value = _make_llm_response("ok")
        state: dict[str, Any] = {"query": "你好", "report_md": "", "messages": []}

        await agent.chat(state)

        call_kwargs = mock_llm.achat.call_args.kwargs
        assert call_kwargs["tier"] == LLMTier.FAST

    @pytest.mark.asyncio
    async def test_chat_passes_smart_tier_for_complex_query(
        self,
        agent: ChatAgent,
        mock_llm: MagicMock,
    ) -> None:
        """复杂查询 (含关键词) → achat 应使用 tier=SMART."""
        mock_llm.achat.return_value = _make_llm_response("ok")
        # 长查询 (>20 字符) + 含 "对比" 关键词
        state: dict[str, Any] = {
            "query": "请对比分析这两个方案的优缺点并给出详细的实施建议报告",
            "report_md": "# 报告",
            "messages": [],
        }

        await agent.chat(state)

        call_kwargs = mock_llm.achat.call_args.kwargs
        assert call_kwargs["tier"] == LLMTier.SMART


# ========== chat: cascade 降级 (SMART → FAST) ==========


class TestChatCascadeFallback:
    """chat() cascade 降级测试."""

    @pytest.mark.asyncio
    async def test_smart_failure_falls_back_to_fast(
        self,
        agent: ChatAgent,
        mock_llm: MagicMock,
    ) -> None:
        """SMART LLM 失败 → 降级到 FAST."""
        # 第一次 (SMART) 失败, 第二次 (FAST) 成功
        mock_llm.achat.side_effect = [
            RuntimeError("SMART LLM down"),
            _make_llm_response("FAST 降级回复"),
        ]
        # 长查询 (>20 字符) + 含 "对比" 关键词 → 触发 SMART
        state: dict[str, Any] = {
            "query": "请对比分析新能源汽车和燃油车的优缺点并给出建议",
            "report_md": "# 报告",
            "messages": [],
        }

        result = await agent.chat(state)

        # 应调用 2 次: SMART + FAST
        assert mock_llm.achat.await_count == 2
        # 第一次 tier=SMART, 第二次 tier=FAST
        first_call = mock_llm.achat.call_args_list[0].kwargs
        second_call = mock_llm.achat.call_args_list[1].kwargs
        assert first_call["tier"] == LLMTier.SMART
        assert second_call["tier"] == LLMTier.FAST
        # 最终返回 FAST 降级回复
        assert result["messages"][1].content == "FAST 降级回复"

    @pytest.mark.asyncio
    async def test_fast_failure_returns_error_message(
        self,
        agent: ChatAgent,
        mock_llm: MagicMock,
    ) -> None:
        """FAST LLM 直接失败 → 返回错误 AIMessage (不降级, FAST 无 fallback)."""
        mock_llm.achat.side_effect = RuntimeError("FAST LLM down")
        state: dict[str, Any] = {
            "query": "你好",  # 短查询走 FAST
            "report_md": "",
            "messages": [],
        }

        result = await agent.chat(state)

        # 应仅调用 1 次 (FAST 失败无降级)
        assert mock_llm.achat.await_count == 1
        messages = result["messages"]
        assert isinstance(messages[0], HumanMessage)
        assert isinstance(messages[1], AIMessage)
        # AIMessage 应含错误信息
        assert "对话服务暂时不可用" in messages[1].content
        assert "FAST LLM down" in messages[1].content

    @pytest.mark.asyncio
    async def test_smart_and_fast_both_fail_returns_error(
        self,
        agent: ChatAgent,
        mock_llm: MagicMock,
    ) -> None:
        """SMART + FAST 双层失败 → 返回错误 AIMessage."""
        mock_llm.achat.side_effect = [
            RuntimeError("SMART down"),
            RuntimeError("FAST also down"),
        ]
        # 长查询 (>20 字符) + 含 "对比" 关键词 → 触发 SMART
        state: dict[str, Any] = {
            "query": "请对比分析这两个方案的优缺点并给出详细的实施建议",
            "report_md": "# 报告",
            "messages": [],
        }

        result = await agent.chat(state)

        # 应调用 2 次: SMART + FAST
        assert mock_llm.achat.await_count == 2
        messages = result["messages"]
        assert isinstance(messages[0], HumanMessage)
        assert isinstance(messages[1], AIMessage)
        # 第二次失败的异常信息应在错误消息中
        assert "对话服务暂时不可用" in messages[1].content
        assert "FAST also down" in messages[1].content

    @pytest.mark.asyncio
    async def test_fallback_uses_different_span_name(
        self,
        agent: ChatAgent,
        mock_llm: MagicMock,
    ) -> None:
        """SMART 降级 FAST 时应使用不同 span_name 区分."""
        mock_llm.achat.side_effect = [
            RuntimeError("SMART down"),
            _make_llm_response("FAST ok"),
        ]
        # 长查询 (>20 字符) + 含 "对比" 关键词 → 触发 SMART
        state: dict[str, Any] = {
            "query": "请对比分析方案的优缺点并给出详细的实施建议报告",
            "report_md": "# 报告",
            "messages": [],
        }

        await agent.chat(state)

        first_call = mock_llm.achat.call_args_list[0].kwargs
        second_call = mock_llm.achat.call_args_list[1].kwargs
        assert first_call["span_name"] == "chat-agent-llm"
        assert second_call["span_name"] == "chat-agent-llm-fallback"


# ========== chat: 边界条件 ==========


class TestChatEdgeCases:
    """chat() 边界条件测试."""

    @pytest.mark.asyncio
    async def test_chat_handles_missing_query(
        self,
        agent: ChatAgent,
        mock_llm: MagicMock,
    ) -> None:
        """state 缺 query 字段 → 不抛异常 (使用空字符串)."""
        mock_llm.achat.return_value = _make_llm_response("ok")
        state: dict[str, Any] = {"report_md": "", "messages": []}

        result = await agent.chat(state)

        # 应正常返回, HumanMessage 内容为空
        assert result["messages"][0].content == ""

    @pytest.mark.asyncio
    async def test_chat_handles_missing_messages(
        self,
        agent: ChatAgent,
        mock_llm: MagicMock,
    ) -> None:
        """state 缺 messages 字段 → 不抛异常 (空列表)."""
        mock_llm.achat.return_value = _make_llm_response("ok")
        state: dict[str, Any] = {"query": "你好", "report_md": ""}

        result = await agent.chat(state)

        # 应正常返回, 仅含 HumanMessage + AIMessage (无历史)
        assert len(result["messages"]) == 2

    @pytest.mark.asyncio
    async def test_chat_uses_default_role_when_no_agent_role(
        self,
        agent: ChatAgent,
        mock_llm: MagicMock,
        mock_prompt_family: MagicMock,
    ) -> None:
        """无 agent_role → 使用默认研究分析专家 persona."""
        mock_llm.achat.return_value = _make_llm_response("ok")
        state: dict[str, Any] = {
            "query": "请总结",
            "report_md": "# 报告",
            "messages": [],
            # 无 agent_role
        }

        await agent.chat(state)

        call_kwargs = mock_prompt_family.chat_prompt.call_args.kwargs
        # 应使用默认研究分析专家 persona
        assert "研究分析专家" in call_kwargs["agent_role"]

    @pytest.mark.asyncio
    async def test_chat_passes_step_param(
        self,
        agent: ChatAgent,
        mock_llm: MagicMock,
    ) -> None:
        """chat() 调用 achat 应传 step='chat'."""
        mock_llm.achat.return_value = _make_llm_response("ok")
        state: dict[str, Any] = {"query": "你好", "report_md": "", "messages": []}

        await agent.chat(state)

        call_kwargs = mock_llm.achat.call_args.kwargs
        assert call_kwargs["step"] == "chat"


# ========== _convert_messages: 消息格式转换 ==========


class TestConvertMessages:
    """_convert_messages 消息格式转换测试."""

    def test_convert_human_message_to_user_role(self) -> None:
        """HumanMessage → role=user."""
        result = ChatAgent._convert_messages([HumanMessage(content="hi")])
        assert result == [{"role": "user", "content": "hi"}]

    def test_convert_ai_message_to_assistant_role(self) -> None:
        """AIMessage → role=assistant."""
        result = ChatAgent._convert_messages([AIMessage(content="hello")])
        assert result == [{"role": "assistant", "content": "hello"}]

    def test_convert_system_message_to_system_role(self) -> None:
        """SystemMessage → role=system."""
        result = ChatAgent._convert_messages([SystemMessage(content="system prompt")])
        assert result == [{"role": "system", "content": "system prompt"}]

    def test_convert_tool_message_to_tool_role(self) -> None:
        """ToolMessage → role=tool."""
        result = ChatAgent._convert_messages(
            [ToolMessage(content="tool output", tool_call_id="tc1")]
        )
        assert result == [{"role": "tool", "content": "tool output"}]

    def test_convert_multiple_messages_preserves_order(self) -> None:
        """多条消息应保持顺序."""
        msgs = [
            HumanMessage(content="用户1"),
            AIMessage(content="助手1"),
            HumanMessage(content="用户2"),
        ]
        result = ChatAgent._convert_messages(msgs)
        assert len(result) == 3
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[2]["role"] == "user"

    def test_convert_empty_list_returns_empty(self) -> None:
        """空列表 → 空列表."""
        assert ChatAgent._convert_messages([]) == []

    def test_convert_non_string_content_to_string(self) -> None:
        """非字符串 content 应转字符串."""
        # langchain content 可以是 list (multimodal)
        msg = HumanMessage(content=["part1", "part2"])  # type: ignore[arg-type]
        result = ChatAgent._convert_messages([msg])
        assert len(result) == 1
        assert isinstance(result[0]["content"], str)
        assert "part1" in result[0]["content"]


# ========== chat_node: 节点纯函数包装 ==========


class TestChatNode:
    """chat_node 节点包装测试."""

    @pytest.mark.asyncio
    async def test_chat_node_delegates_to_agent(self) -> None:
        """chat_node 应委托给 ChatAgent.chat."""
        settings = Settings(_env_file=None)
        state: dict[str, Any] = {
            "query": "你好",
            "report_md": "",
            "messages": [],
            "user_id": "u1",
            "session_id": "s1",
        }

        with patch("src.agents.researcher.chat_agent.ChatAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.chat = AsyncMock(
                return_value={"messages": [HumanMessage(content="你好"), AIMessage(content="hi")]}
            )
            mock_agent_cls.return_value = mock_agent

            result = await chat_node(state, settings=settings)

        mock_agent_cls.assert_called_once_with(settings)
        mock_agent.chat.assert_awaited_once()
        call_kwargs = mock_agent.chat.call_args.kwargs
        assert call_kwargs["user_id"] == "u1"
        assert call_kwargs["session_id"] == "s1"
        assert "messages" in result

    @pytest.mark.asyncio
    async def test_chat_node_passes_state_user_and_session(self) -> None:
        """chat_node 应从 state 提取 user_id/session_id 传给 chat."""
        settings = Settings(_env_file=None)
        state: dict[str, Any] = {
            "query": "你好",
            "report_md": "",
            "messages": [],
            "user_id": "user-from-state",
            "session_id": "session-from-state",
        }

        with patch("src.agents.researcher.chat_agent.ChatAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.chat = AsyncMock(return_value={"messages": []})
            mock_agent_cls.return_value = mock_agent

            await chat_node(state, settings=settings)

        call_args = mock_agent.chat.call_args
        # 第一个位置参数应为 state
        assert call_args.args[0] is state
        assert call_args.kwargs["user_id"] == "user-from-state"
        assert call_args.kwargs["session_id"] == "session-from-state"


# ========== ChatAgent 构造函数 ==========


class TestChatAgentInit:
    """ChatAgent 初始化测试."""

    def test_init_with_explicit_dependencies(self) -> None:
        """显式传入 settings/llm/prompt_family 应被使用."""
        settings = Settings(_env_file=None)
        llm = MagicMock()
        pf = MagicMock()
        agent = ChatAgent(settings=settings, llm=llm, prompt_family=pf)
        assert agent.settings is settings
        assert agent._llm is llm
        assert agent._prompt_family is pf

    def test_init_defaults_when_no_args(self) -> None:
        """无参构造应使用默认 settings/llm/prompt_family."""
        # 不应抛异常 (会调用 get_settings/get_llm_client/get_prompt_family)
        agent = ChatAgent()
        assert agent.settings is not None
        assert agent._llm is not None
        assert agent._prompt_family is not None
