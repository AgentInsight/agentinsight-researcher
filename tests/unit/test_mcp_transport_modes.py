"""单元测试: MCP 传输模式 (stdio / sse) 与 args_schema 分支.

验证 src/skills/researcher/mcp_coordinator.py 的 _execute_mcp 传输模式路由:
- stdio 模式: 通过 command/args/env_vars 启动本地进程 (含 JSON 字符串解析)
- sse / streamable_http 模式: 通过 server_url 连接远程 HTTP 服务器
- stdio 缺少 command 时跳过该配置
- _select_tool_with_llm 的 args_schema pydantic model vs dict 两种分支

单元测试在构建期执行, 不依赖外部服务.
所有 MCP Server 连接 / LLM 调用 / 数据库全部 mock.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from src.config.settings import Settings
from src.llm.client import LLMResponse
from src.skills.researcher.mcp_coordinator import MCPCoordinator

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture()
def settings() -> Settings:
    """构造最小 Settings (mcp_strategy=fast, 缓存启用)."""
    return Settings(_env_file=None, mcp_strategy="fast", mcp_cache_enabled=True)


@pytest.fixture()
def mock_llm() -> MagicMock:
    """Mock LLMClient (achat 为 AsyncMock)."""
    llm = MagicMock()
    llm.achat = AsyncMock()
    return llm


@pytest.fixture()
def coordinator(settings: Settings, mock_llm: MagicMock) -> MCPCoordinator:
    """构造 MCPCoordinator (依赖 mock)."""
    return MCPCoordinator(settings=settings, llm=mock_llm)


def _capture_client_factory() -> tuple[MagicMock, dict[str, Any]]:
    """构造 _get_or_create_client mock, 捕获传入的 server_configs.

    Returns:
        (mock_factory, captured_dict): mock_factory 用作 patch side_effect,
        captured_dict 在调用后填充实际传入的 server_configs.
    """
    captured: dict[str, Any] = {}

    def _factory(server_configs: dict[str, Any]) -> Any:
        captured.clear()
        captured.update(server_configs)
        mock_client = MagicMock()
        mock_client.get_tools = AsyncMock(return_value=[])
        return mock_client

    mock_factory = MagicMock(side_effect=_factory)
    return mock_factory, captured


# ========== stdio 传输模式 ==========


class TestMCPStdioTransport:
    """验证 stdio 传输模式的配置构建."""

    async def test_stdio_transport_with_command(
        self,
        coordinator: MCPCoordinator,
    ) -> None:
        """stdio 传输模式 + command + args + env_vars → 正确构建 server_configs.

        _execute_mcp 将 stdio 配置转为 {command, args, env, transport: "stdio"} 格式.
        """
        config = {
            "name": "local-server",
            "transport_type": "stdio",
            "command": "python",
            "args": ["-m", "mcp_server"],
            "env_vars": {"API_KEY": "secret", "DEBUG": "true"},
        }

        mock_factory, captured = _capture_client_factory()

        with patch.object(coordinator, "_get_or_create_client", mock_factory):
            result = await coordinator._execute_mcp("query", [config])

        # 结果为空 (get_tools 返回空列表)
        assert result == []
        # server_configs 应正确构建
        assert "local-server" in captured
        server_cfg = captured["local-server"]
        assert server_cfg["transport"] == "stdio"
        assert server_cfg["command"] == "python"
        assert server_cfg["args"] == ["-m", "mcp_server"]
        assert server_cfg["env"] == {"API_KEY": "secret", "DEBUG": "true"}

    async def test_stdio_transport_missing_command_skipped(
        self,
        coordinator: MCPCoordinator,
    ) -> None:
        """stdio 模式缺少 command 时跳过该配置 (不构建 client)."""
        config = {
            "name": "bad-stdio",
            "transport_type": "stdio",
            "command": None,  # 缺少 command
        }

        with patch.object(coordinator, "_get_or_create_client", return_value=None) as mock_factory:
            result = await coordinator._execute_mcp("query", [config])

        # 所有配置均无效 → 返回空, 不构建 client
        assert result == []
        mock_factory.assert_not_called()

    async def test_stdio_transport_args_json_parsing(
        self,
        coordinator: MCPCoordinator,
    ) -> None:
        """stdio 模式 args/env_vars 为 JSON 字符串时自动解析.

        args='["-m","server"]' → list, env_vars='{"KEY":"val"}' → dict.
        """
        config = {
            "name": "local-server",
            "transport_type": "stdio",
            "command": "node",
            "args": '["server.js", "--port", "3000"]',  # JSON 字符串
            "env_vars": '{"NODE_ENV": "production", "LOG_LEVEL": "info"}',  # JSON 字符串
        }

        mock_factory, captured = _capture_client_factory()

        with patch.object(coordinator, "_get_or_create_client", mock_factory):
            result = await coordinator._execute_mcp("query", [config])

        assert result == []
        assert "local-server" in captured
        server_cfg = captured["local-server"]
        # JSON 字符串应被解析为 list / dict
        assert server_cfg["args"] == ["server.js", "--port", "3000"]
        assert server_cfg["env"] == {"NODE_ENV": "production", "LOG_LEVEL": "info"}
        assert server_cfg["transport"] == "stdio"
        assert server_cfg["command"] == "node"


# ========== sse 传输模式 ==========


class TestMCPSseTransport:
    """验证 sse / streamable_http 远程传输模式的配置构建."""

    async def test_sse_transport_mode(
        self,
        coordinator: MCPCoordinator,
    ) -> None:
        """sse 传输模式 (transport_type='sse') → 构建 {url, transport: 'sse'}.

        远程模式不解析 command/args/env_vars, 仅需 server_url.
        """
        config = {
            "name": "remote-sse",
            "transport_type": "sse",
            "url": "http://mcp-server:8080/sse",
            "server_url": "http://mcp-server:8080/sse",
        }

        mock_factory, captured = _capture_client_factory()

        with patch.object(coordinator, "_get_or_create_client", mock_factory):
            result = await coordinator._execute_mcp("query", [config])

        assert result == []
        assert "remote-sse" in captured
        server_cfg = captured["remote-sse"]
        assert server_cfg["transport"] == "sse"
        assert server_cfg["url"] == "http://mcp-server:8080/sse"
        # 远程模式不应含 command/args/env 字段
        assert "command" not in server_cfg
        assert "args" not in server_cfg
        assert "env" not in server_cfg


# ========== args_schema 分支 ==========


class TestMCPArgsSchema:
    """验证 _select_tool_with_llm 的 args_schema pydantic model vs dict 分支."""

    async def test_args_schema_pydantic_model_vs_dict(
        self,
        coordinator: MCPCoordinator,
        mock_llm: MagicMock,
    ) -> None:
        """args_schema pydantic model vs dict vs None 三种分支.

        1. pydantic model (含 model_json_schema) → 调用 model_json_schema()
        2. dict (无 model_json_schema) → dict(args_schema)
        3. None → 使用 tool.args 属性

        三种分支均不应抛异常, LLM 应被调用且选回正确工具.
        """

        class FakePydanticSchema(BaseModel):
            """模拟 pydantic model (含 model_json_schema 方法)."""

            query: str = ""
            limit: int = 10

        # Tool 1: pydantic model args_schema (走 model_json_schema 分支)
        tool1 = MagicMock()
        tool1.name = "pydantic_tool"
        tool1.description = "tool with pydantic schema"
        tool1.args_schema = FakePydanticSchema
        tool1.ainvoke = AsyncMock(return_value="r1")

        # Tool 2: dict args_schema (走 dict(args_schema) 分支)
        tool2 = MagicMock()
        tool2.name = "dict_tool"
        tool2.description = "tool with dict schema"
        tool2.args_schema = {"param": "value", "type": "string"}
        tool2.ainvoke = AsyncMock(return_value="r2")

        # Tool 3: None args_schema (走 getattr(t, "args", {}) 分支)
        tool3 = MagicMock()
        tool3.name = "none_tool"
        tool3.description = "tool with no schema"
        tool3.args_schema = None
        tool3.args = {"default_param": "default_value"}
        tool3.ainvoke = AsyncMock(return_value="r3")

        tools = [tool1, tool2, tool3]

        # LLM 返回选择全部三个工具
        mock_llm.achat.return_value = LLMResponse(
            content=(
                '[{"name": "pydantic_tool", "args": {"query": "q"}}, '
                '{"name": "dict_tool", "args": {"query": "q"}}, '
                '{"name": "none_tool", "args": {"query": "q"}}]'
            ),
            model="test",
        )

        selected = await coordinator._select_tool_with_llm("query", tools, max_tools=3)

        # 三个工具均应被选回 (无异常)
        assert len(selected) == 3
        selected_names = {getattr(t, "name", "") for t, _ in selected}
        assert selected_names == {"pydantic_tool", "dict_tool", "none_tool"}

        # LLM 应被调用一次
        mock_llm.achat.assert_awaited_once()

        # 验证 prompt 中包含三种 schema 的参数描述
        call_args = mock_llm.achat.call_args
        prompt_content = call_args.args[0][0]["content"]
        # pydantic model → model_json_schema() 输出 (含 "properties" 键)
        assert "properties" in prompt_content, "pydantic schema 应含 model_json_schema 输出"
        # dict → dict(args_schema) 原样输出 (含 "param" 键)
        assert "param" in prompt_content, "dict schema 应含原始 dict 内容"
        # None → tool.args 属性 (含 "default_param" 键)
        assert "default_param" in prompt_content, "None schema 应使用 tool.args 属性"
