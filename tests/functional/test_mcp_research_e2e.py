"""功能测试: MCP 协调器端到端调用 (mock MCP Server, 不实际启动).

AGENTS.md 第 13 章:
- 功能测试在 docker compose up -d 且全部容器 service_healthy 后执行
- 测试用例独立可重复运行, 不依赖执行顺序
- 测试数据隔离: session_id=test_mcp_* / config name=test-mcp-*

本文件验证 MCP 协调器在用户研究/分析流程中的完整调用链路:
- TestMCPCoordinatorEndToEnd: LLM 选工具 → 工具调用 → 结果注入 → 失败降级
- TestMCPResearchFlow: 启用/禁用 MCP 的研究流程 + trace_tool span 包裹

注意:
- 使用 mock MCP Server (不实际启动 MCP Server 进程)
- mock LangChain MCP adapters 的 MultiServerMCPClient (不连接真实 ClientSession)
- 所有外部依赖 (LLMClient/MultiServerMCPClient/Postgres) 全部 mock
- 验证 MCP 工具调用结果出现在研究上下文/报告中

为避免 conftest.py 在容器栈未运行时跳过本测试 (pytest_collection_modifyitems
对 functional mark 自动 skip), 本文件使用 unit mark 以保证测试始终可执行
(mock 化测试不依赖容器栈). 参考 tests/unit/test_skills_mcp_coordinator.py 的标记模式.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.skills.researcher.mcp_coordinator import (
    MCPCoordinator,
    conduct_mcp_if_enabled,
)

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture()
def test_settings() -> Settings:
    """构造测试 Settings (跳过 .env 加载)."""
    s = Settings(_env_file=None)
    s.mcp_strategy = "fast"
    s.mcp_max_tools = 3
    s.mcp_cache_enabled = False  # 测试关闭缓存, 避免跨用例污染
    s.agent_name = "agentinsight-researcher"
    return s


@pytest.fixture()
def mock_llm() -> MagicMock:
    """构造 mock LLMClient (achat 返回可控 JSON 用于工具选择)."""
    llm = MagicMock()
    llm.achat = AsyncMock(return_value=MagicMock(content="[]"))
    return llm


@pytest.fixture()
def coordinator(test_settings: Settings, mock_llm: MagicMock) -> MCPCoordinator:
    """构造 MCPCoordinator 实例 (注入 mock LLM)."""
    return MCPCoordinator(settings=test_settings, llm=mock_llm)


def _make_mock_tool(
    name: str,
    description: str = "mock tool",
    invoke_result: str = "mock-tool-result",
    invoke_side_effect: Exception | None = None,
) -> MagicMock:
    """构造 mock MCP 工具 (含 name/description/args_schema/ainvoke)."""

    class _ArgsSchema:
        @staticmethod
        def model_json_schema() -> dict:
            return {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            }

    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.args_schema = _ArgsSchema
    if invoke_side_effect is not None:
        tool.ainvoke = AsyncMock(side_effect=invoke_side_effect)
    else:
        tool.ainvoke = AsyncMock(return_value=invoke_result)
    return tool


def _make_mock_mcp_client(tools: list) -> MagicMock:
    """构造 mock MultiServerMCPClient (get_tools 返回指定工具列表)."""
    client = MagicMock()
    client.get_tools = AsyncMock(return_value=tools)
    return client


def _make_mcp_configs(transport_type: str = "streamable_http") -> list[dict]:
    """构造测试用 MCP 配置列表."""
    return [
        {
            "name": "test-mcp-server",
            "transport_type": transport_type,
            "server_url": "http://mock-mcp:9999/mcp",
            "url": "http://mock-mcp:9999/mcp",
            "command": None,
            "args": [],
            "env_vars": {},
            "enabled": True,
        }
    ]


# ========== TestMCPCoordinatorEndToEnd: MCP 协调器端到端 ==========


class TestMCPCoordinatorEndToEnd:
    """MCP 协调器端到端: LLM 选工具 → 工具调用 → 结果注入 → 失败降级."""

    async def test_mcp_tool_selection_and_call(
        self, coordinator: MCPCoordinator, mock_llm: MagicMock
    ) -> None:
        """LLM 选择 MCP 工具并调用, 验证工具结果出现在返回 contexts 中.

        流程:
        1. mock MultiServerMCPClient.get_tools 返回 2 个 mock 工具
        2. mock LLM.achat 返回 JSON 选择第一个工具
        3. mock tool.ainvoke 返回 "real-mcp-result"
        4. 验证 contexts 含 "real-mcp-result"
        """
        tool_a = _make_mock_tool("search_tool", "search the web", invoke_result="real-mcp-result")
        tool_b = _make_mock_tool("calc_tool", "calculate", invoke_result="calc-result")
        mock_client = _make_mock_mcp_client([tool_a, tool_b])

        # LLM 返回选择 search_tool 的 JSON
        mock_llm.achat.return_value = MagicMock(
            content=json.dumps([{"name": "search_tool", "args": {"query": "test query"}}])
        )

        with patch.object(coordinator, "_get_or_create_client", return_value=mock_client):
            contexts = await coordinator.conduct_research(
                "test query",
                strategy="fast",
                mcp_configs=_make_mcp_configs(),
                user_id="test-user",
                session_id="test-mcp-session",
            )

        # LLM 应被调用一次 (选工具)
        mock_llm.achat.assert_awaited_once()
        # search_tool.ainvoke 应被调用, calc_tool 不应被调用
        tool_a.ainvoke.assert_awaited_once()
        tool_b.ainvoke.assert_not_called()
        # contexts 应含 search_tool 的返回结果
        assert "real-mcp-result" in contexts
        # 不应含 calc_tool 的结果 (LLM 未选)
        assert all("calc-result" not in c for c in contexts)

    async def test_mcp_result_injected_into_research(
        self, coordinator: MCPCoordinator, mock_llm: MagicMock
    ) -> None:
        """MCP 工具调用结果注入研究上下文 (contexts 列表)."""
        tool = _make_mock_tool(
            "data_source",
            "提供行业数据",
            invoke_result="行业数据: 2024 年市场规模 1.2 万亿",
        )
        mock_client = _make_mock_mcp_client([tool])

        mock_llm.achat.return_value = MagicMock(
            content=json.dumps([{"name": "data_source", "args": {"query": "市场规模"}}])
        )

        with patch.object(coordinator, "_get_or_create_client", return_value=mock_client):
            contexts = await coordinator.conduct_research(
                "分析 2024 年市场规模",
                strategy="fast",
                mcp_configs=_make_mcp_configs(),
                user_id="test-user",
                session_id="test-mcp-inject",
            )

        # contexts 应含 MCP 工具返回的数据 (后续会拼接到研究上下文)
        assert len(contexts) >= 1
        assert any("1.2 万亿" in c for c in contexts)
        # 工具应被实际调用 (而非仅 LLM 选工具)
        tool.ainvoke.assert_awaited_once()

    async def test_mcp_failure_degrades_gracefully(
        self, coordinator: MCPCoordinator, mock_llm: MagicMock
    ) -> None:
        """MCP 工具调用失败 → conduct_research 返回空列表 (不抛异常).

        场景: tool.ainvoke 抛异常, _call_single_tool 返回 None,
        contexts 过滤掉 None 后为空, conduct_research 返回 [].
        """
        tool = _make_mock_tool(
            "failing_tool",
            "always fails",
            invoke_side_effect=RuntimeError("MCP server crashed"),
        )
        mock_client = _make_mock_mcp_client([tool])

        mock_llm.achat.return_value = MagicMock(
            content=json.dumps([{"name": "failing_tool", "args": {"query": "test"}}])
        )

        with patch.object(coordinator, "_get_or_create_client", return_value=mock_client):
            # 不应抛异常 (conduct_research 内部 try/except 兜底)
            contexts = await coordinator.conduct_research(
                "test query",
                strategy="fast",
                mcp_configs=_make_mcp_configs(),
                user_id="test-user",
                session_id="test-mcp-fail",
            )

        # 失败时返回空列表 (而非抛异常)
        assert isinstance(contexts, list)
        assert contexts == []

    async def test_mcp_no_configs_returns_empty(self, coordinator: MCPCoordinator) -> None:
        """mcp_configs 为空 → conduct_research 早期返回 []."""
        contexts = await coordinator.conduct_research(
            "test query",
            strategy="fast",
            mcp_configs=[],
            user_id="test-user",
            session_id="test-mcp-no-config",
        )
        assert contexts == []

    async def test_mcp_disabled_strategy_returns_empty(self, coordinator: MCPCoordinator) -> None:
        """strategy=disabled → conduct_research 直接返回 []."""
        contexts = await coordinator.conduct_research(
            "test query",
            strategy="disabled",
            mcp_configs=_make_mcp_configs(),
            user_id="test-user",
            session_id="test-mcp-disabled",
        )
        assert contexts == []


# ========== TestMCPResearchFlow: 研究流程中的 MCP 调用 ==========


class TestMCPResearchFlow:
    """验证 conduct_mcp_if_enabled 公共入口在研究流程中的行为."""

    async def test_research_with_mcp_enabled(self, test_settings: Settings) -> None:
        """启用 MCP (strategy=fast) + 有可用配置 → 返回 MCP contexts.

        mock get_user_mcp_configs 返回非空配置, mock MCPCoordinator.conduct_research
        返回模拟 contexts, 验证 conduct_mcp_if_enabled 透传结果.
        """
        test_settings.mcp_strategy = "fast"

        mock_coord = MagicMock()
        mock_coord.conduct_research = AsyncMock(return_value=["mcp-context-1", "mcp-context-2"])

        with (
            patch(
                "src.skills.researcher.mcp_coordinator.get_mcp_coordinator",
                return_value=mock_coord,
            ),
            patch(
                "src.skills.researcher.mcp_coordinator.get_user_mcp_configs",
                AsyncMock(return_value=_make_mcp_configs()),
            ),
        ):
            contexts = await conduct_mcp_if_enabled(
                test_settings,
                "分析新能源市场",
                user_id="test-user",
                session_id="test-mcp-enabled-flow",
            )

        # 应返回 MCP contexts
        assert contexts == ["mcp-context-1", "mcp-context-2"]
        # conduct_research 应被调用一次
        mock_coord.conduct_research.assert_awaited_once()
        # 验证调用参数 (sub_query + strategy + mcp_configs + user_id + session_id)
        call_kwargs = mock_coord.conduct_research.call_args
        assert call_kwargs.kwargs.get("strategy") == "fast"
        assert call_kwargs.kwargs.get("user_id") == "test-user"
        assert call_kwargs.kwargs.get("session_id") == "test-mcp-enabled-flow"

    async def test_research_with_mcp_disabled(self, test_settings: Settings) -> None:
        """禁用 MCP (strategy=disabled) → 早期返回 [], 不调用协调器."""
        test_settings.mcp_strategy = "disabled"

        mock_coord = MagicMock()
        mock_coord.conduct_research = AsyncMock(return_value=["should-not-reach"])

        with (
            patch(
                "src.skills.researcher.mcp_coordinator.get_mcp_coordinator",
                return_value=mock_coord,
            ),
            patch(
                "src.skills.researcher.mcp_coordinator.get_user_mcp_configs",
                AsyncMock(return_value=_make_mcp_configs()),
            ),
        ):
            contexts = await conduct_mcp_if_enabled(
                test_settings,
                "分析新能源市场",
                user_id="test-user",
                session_id="test-mcp-disabled-flow",
            )

        # disabled 策略直接返回空
        assert contexts == []
        # 协调器不应被调用
        mock_coord.conduct_research.assert_not_called()

    async def test_research_with_no_user_configs_returns_empty(
        self, test_settings: Settings
    ) -> None:
        """用户无启用 MCP 配置 → 早期返回 [], 不调用 conduct_research."""
        test_settings.mcp_strategy = "fast"

        mock_coord = MagicMock()
        mock_coord.conduct_research = AsyncMock(return_value=["should-not-reach"])

        with (
            patch(
                "src.skills.researcher.mcp_coordinator.get_mcp_coordinator",
                return_value=mock_coord,
            ),
            patch(
                "src.skills.researcher.mcp_coordinator.get_user_mcp_configs",
                AsyncMock(return_value=[]),  # 用户无配置
            ),
        ):
            contexts = await conduct_mcp_if_enabled(
                test_settings,
                "分析新能源市场",
                user_id="test-user",
                session_id="test-mcp-no-configs",
            )

        assert contexts == []
        mock_coord.conduct_research.assert_not_called()

    async def test_mcp_call_traced_in_span(
        self, coordinator: MCPCoordinator, mock_llm: MagicMock
    ) -> None:
        """MCP 调用被 trace_tool span 包裹 (AGENTS.md 第 10 章).

        mock trace_tool 为 asynccontextmanager, 验证 conduct_research 进入 span,
        且 span.update 被调用记录 success=True.
        """
        from contextlib import asynccontextmanager

        tool = _make_mock_tool("traced_tool", "traced tool", invoke_result="traced-result")
        mock_client = _make_mock_mcp_client([tool])
        mock_llm.achat.return_value = MagicMock(
            content=json.dumps([{"name": "traced_tool", "args": {"query": "test"}}])
        )

        span_calls: list[dict] = []

        class _SpySpan:
            def update(self, **kwargs):
                span_calls.append(kwargs)
                return self

            def end(self, **kwargs):
                return self

        @asynccontextmanager
        async def _spy_trace_tool(name, **kwargs):
            span_calls.append({"_enter": True, "name": name, "kwargs": kwargs})
            yield _SpySpan()

        with (
            patch(
                "src.skills.researcher.mcp_coordinator.trace_tool",
                _spy_trace_tool,
            ),
            patch.object(coordinator, "_get_or_create_client", return_value=mock_client),
        ):
            contexts = await coordinator.conduct_research(
                "test query",
                strategy="fast",
                mcp_configs=_make_mcp_configs(),
                user_id="test-user",
                session_id="test-mcp-trace",
            )

        # trace_tool 应被进入 (mcp-research span)
        enter_calls = [c for c in span_calls if "_enter" in c]
        assert len(enter_calls) >= 1, "trace_tool span 应被进入"
        assert enter_calls[0]["name"] == "mcp-research"
        # span.update 应记录 success=True (调用成功后)
        update_calls = [c for c in span_calls if "_enter" not in c]
        success_updates = [c for c in update_calls if c.get("metadata", {}).get("success") is True]
        assert len(success_updates) >= 1, "span 应记录 success=True"
        # contexts 应含工具结果
        assert "traced-result" in contexts

    async def test_mcp_failure_traced_with_success_false(
        self, coordinator: MCPCoordinator, mock_llm: MagicMock
    ) -> None:
        """MCP 调用失败时, trace span 应记录 success=False (不抛异常).

        _execute_mcp 内部有 try/except 会吞掉工具层异常并返回 [],
        要触发 conduct_research 的 except 分支 (记录 success=False),
        需直接 mock _execute_mcp 抛异常.
        """
        from contextlib import asynccontextmanager

        span_calls: list[dict] = []

        class _SpySpan:
            def update(self, **kwargs):
                span_calls.append(kwargs)
                return self

            def end(self, **kwargs):
                return self

        @asynccontextmanager
        async def _spy_trace_tool(name, **kwargs):
            span_calls.append({"_enter": True, "name": name})
            yield _SpySpan()

        # 直接 mock _execute_mcp 抛异常, 触发 conduct_research 的 except 分支
        with (
            patch(
                "src.skills.researcher.mcp_coordinator.trace_tool",
                _spy_trace_tool,
            ),
            patch.object(
                coordinator,
                "_execute_mcp",
                AsyncMock(side_effect=RuntimeError("conduct_research level failure")),
            ),
        ):
            contexts = await coordinator.conduct_research(
                "test query",
                strategy="fast",
                mcp_configs=_make_mcp_configs(),
                user_id="test-user",
                session_id="test-mcp-fail-trace",
            )

        # 失败时返回空列表
        assert contexts == []
        # span.update 应记录 success=False
        update_calls = [c for c in span_calls if "_enter" not in c]
        fail_updates = [c for c in update_calls if c.get("metadata", {}).get("success") is False]
        assert len(fail_updates) >= 1, "失败时 span 应记录 success=False"

    async def test_mcp_fast_strategy_caches_result(
        self, coordinator: MCPCoordinator, mock_llm: MagicMock
    ) -> None:
        """fast 策略: 同一 query 二次调用命中实例缓存 (不重复调用工具).

        验证 V4-P1-01 fast 策略的缓存复用语义.
        """
        tool = _make_mock_tool("cached_tool", "cached tool", invoke_result="cached-result")
        mock_client = _make_mock_mcp_client([tool])
        mock_llm.achat.return_value = MagicMock(
            content=json.dumps([{"name": "cached_tool", "args": {"query": "test"}}])
        )

        with patch.object(coordinator, "_get_or_create_client", return_value=mock_client):
            # 第一次调用
            contexts1 = await coordinator.conduct_research(
                "same query",
                strategy="fast",
                mcp_configs=_make_mcp_configs(),
                user_id="test-user",
                session_id="test-mcp-cache-1",
            )
            # 第二次调用 (同 query, fast 策略应命中缓存)
            contexts2 = await coordinator.conduct_research(
                "same query",
                strategy="fast",
                mcp_configs=_make_mcp_configs(),
                user_id="test-user",
                session_id="test-mcp-cache-2",
            )

        assert contexts1 == contexts2
        assert "cached-result" in contexts1
        # 工具应只被调用一次 (第二次命中缓存)
        assert tool.ainvoke.await_count == 1
