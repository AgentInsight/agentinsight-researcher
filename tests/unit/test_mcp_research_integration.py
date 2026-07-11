"""单元测试: MCP 服务在用户分析研究中的集成调用.

验证 src/skills/researcher/mcp_coordinator.py 与 research_conductor.py 的 MCP 调用链路:
- MCPCoordinator 在研究流程中的工具选择 (LLM 智能选工具 + 关键词降级)
- MCP 工具调用与 LLM 的交互 (Fast tier 调用 + JSON 解析)
- MCP 工具结果如何注入研究上下文 (conduct_mcp_if_enabled 公共入口)
- MCP 缓存机制 (新增/命中/LRU 淘汰/clear_cache 三层清理)
- MCP 策略开关 (disabled 跳过 / fast 缓存 / deep 每子查询调用)
- MCP 工具调用失败降级 (LLM 失败/工具异常/超时)
- MCP 工具调用 trace span 记录 (trace_tool 包裹)

单元测试不依赖外部服务 (LLM/MCP Server/Postgres 全部 mock).
MCP 工具配置按 agent_id + user_id 隔离.
"""

from __future__ import annotations

import asyncio
import sys
import time
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.llm.client import LLMResponse
from src.skills.researcher.mcp_coordinator import (
    _MCP_CACHE,
    _MCP_CACHE_MAX_SIZE,
    MCP_MAX_CONCURRENCY,
    MCP_TOOL_TIMEOUT_SECONDS,
    MCPCoordinator,
    _make_cache_key,
    conduct_mcp_if_enabled,
    get_mcp_coordinator,
)

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture()
def settings() -> Settings:
    """构造最小 Settings (mcp_strategy=fast, 缓存启用)."""
    return Settings(_env_file=None, mcp_strategy="fast", mcp_cache_enabled=True)


@pytest.fixture(autouse=True)
def reset_mcp_cache() -> Any:
    """每个用例前后清空模块级 _MCP_CACHE (TTL 缓存), 保证用例独立性."""
    _MCP_CACHE.clear()
    yield
    _MCP_CACHE.clear()


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


def _make_tool(
    name: str = "search_tool",
    description: str = "search documents",
    result: str = "tool result content",
) -> MagicMock:
    """构造 mock MCP 工具对象 (含 name/description/args_schema/ainvoke)."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.args_schema = None
    tool.args = {}
    tool.ainvoke = AsyncMock(return_value=result)
    return tool


def _make_mcp_config(
    name: str = "test-server",
    transport_type: str = "streamable_http",
    url: str = "http://mcp-server:8080/mcp",
) -> dict[str, Any]:
    """构造 MCP 配置 dict (远程模式)."""
    return {
        "name": name,
        "transport_type": transport_type,
        "server_url": url,
        "url": url,
        "command": None,
        "args": None,
        "env_vars": None,
    }


# ========== 策略开关 ==========


@pytest.mark.asyncio
async def test_conduct_research_disabled_strategy_returns_empty(
    coordinator: MCPCoordinator,
) -> None:
    """disabled 策略 → 直接返回空列表, 不调用任何工具."""
    coordinator.settings = Settings(_env_file=None, mcp_strategy="disabled")

    result = await coordinator.conduct_research(
        "test query",
        mcp_configs=[_make_mcp_config()],
    )

    assert result == []


@pytest.mark.asyncio
async def test_conduct_research_empty_configs_returns_empty(
    coordinator: MCPCoordinator,
) -> None:
    """无 MCP 配置 → 返回空列表, 不调用 LLM/MCP Server."""
    result = await coordinator.conduct_research("query", mcp_configs=[])

    assert result == []
    coordinator._llm.achat.assert_not_awaited()


@pytest.mark.asyncio
async def test_conduct_research_fast_strategy_caches_second_call(
    coordinator: MCPCoordinator,
    mock_llm: MagicMock,
) -> None:
    """fast 策略: 同一 query 第二次调用复用实例级缓存, 不重复调 LLM."""
    tool_result = "cached context"
    tool = _make_tool(result=tool_result)
    # LLM 选工具返回 [{"name":"search_tool","args":{"query":"q"}}]
    mock_llm.achat.return_value = LLMResponse(
        content='[{"name": "search_tool", "args": {"query": "test"}}]',
        model="test",
    )

    with (
        patch.object(
            coordinator, "_get_or_create_client", return_value=MagicMock()
        ) as mock_client_factory,
        patch.object(
            coordinator, "_select_tool_with_llm", return_value=[(tool, {"query": "test"})]
        ) as mock_select,
    ):
        mock_client = mock_client_factory.return_value
        mock_client.get_tools = AsyncMock(return_value=[tool])
        # 第一次调用
        result1 = await coordinator.conduct_research("same query", mcp_configs=[_make_mcp_config()])
        # 第二次相同 query
        result2 = await coordinator.conduct_research("same query", mcp_configs=[_make_mcp_config()])

    assert result1 == [tool_result]
    assert result2 == [tool_result]
    # fast 策略缓存: 第二次未再调 _select_tool_with_llm
    assert mock_select.await_count == 1


@pytest.mark.asyncio
async def test_conduct_research_deep_strategy_no_cache(
    coordinator: MCPCoordinator,
    mock_llm: MagicMock,
) -> None:
    """deep 策略: 不走 fast 缓存, 每次都重新调用."""
    coordinator.settings = Settings(_env_file=None, mcp_strategy="deep")
    tool = _make_tool(result="ctx")
    mock_llm.achat.return_value = LLMResponse(
        content='[{"name": "search_tool", "args": {"query": "q"}}]',
        model="test",
    )

    with (
        patch.object(coordinator, "_get_or_create_client") as mock_client_factory,
        patch.object(
            coordinator,
            "_select_tool_with_llm",
            return_value=[(tool, {"query": "q"})],
        ) as mock_select,
    ):
        mock_client = mock_client_factory.return_value
        mock_client.get_tools = AsyncMock(return_value=[tool])
        await coordinator.conduct_research("q", mcp_configs=[_make_mcp_config()])
        await coordinator.conduct_research("q", mcp_configs=[_make_mcp_config()])

    # deep 策略每次都调 _select_tool_with_llm (不走 fast 缓存)
    assert mock_select.await_count == 2


# ========== LLM 工具选择交互 ==========


@pytest.mark.asyncio
async def test_select_tool_with_llm_parses_json_response(
    coordinator: MCPCoordinator,
    mock_llm: MagicMock,
) -> None:
    """LLM 返回合法 JSON 数组 → 映射回 tool 对象 + 兜底 query 参数."""
    tool1 = _make_tool(name="search", description="search docs")
    tool2 = _make_tool(name="fetch", description="fetch url")
    tools = [tool1, tool2]
    mock_llm.achat.return_value = LLMResponse(
        content='[{"name": "search", "args": {"q": "value"}}]',
        model="test",
    )

    selected = await coordinator._select_tool_with_llm("query", tools, max_tools=3)

    assert len(selected) == 1
    assert selected[0][0] is tool1
    assert selected[0][1] == {"q": "value", "query": "query"}  # 兜底 query


@pytest.mark.asyncio
async def test_select_tool_with_llm_fallback_when_llm_fails(
    coordinator: MCPCoordinator,
    mock_llm: MagicMock,
) -> None:
    """LLM 调用抛异常 → 降级到关键词匹配 _select_tools."""
    tool1 = _make_tool(name="search", description="search docs")
    tool2 = _make_tool(name="weather", description="weather tool")
    mock_llm.achat.side_effect = RuntimeError("LLM down")

    selected = await coordinator._select_tool_with_llm("search query", [tool1, tool2], max_tools=2)

    # 关键词匹配: "search" 命中 tool1 的 name
    assert len(selected) >= 1
    assert selected[0][0] is tool1
    # 兜底参数含 query
    assert "query" in selected[0][1]


@pytest.mark.asyncio
async def test_select_tool_with_llm_fallback_when_llm_returns_empty(
    coordinator: MCPCoordinator,
    mock_llm: MagicMock,
) -> None:
    """LLM 返回空数组 → 降级到关键词匹配."""
    tool = _make_tool(name="search")
    mock_llm.achat.return_value = LLMResponse(content="[]", model="test")

    selected = await coordinator._select_tool_with_llm("search", [tool], max_tools=3)

    assert len(selected) == 1
    assert selected[0][0] is tool


@pytest.mark.asyncio
async def test_select_tool_with_llm_fallback_when_json_invalid(
    coordinator: MCPCoordinator,
    mock_llm: MagicMock,
) -> None:
    """LLM 返回非 JSON → safe_json_parse 兜底空 → 降级关键词匹配."""
    tool = _make_tool(name="search")
    mock_llm.achat.return_value = LLMResponse(content="not a json", model="test")

    selected = await coordinator._select_tool_with_llm("search", [tool], max_tools=3)

    assert len(selected) == 1


@pytest.mark.asyncio
async def test_select_tool_with_llm_max_tools_truncation(
    coordinator: MCPCoordinator,
    mock_llm: MagicMock,
) -> None:
    """LLM 选了超过 max_tools 个 → 截断到 max_tools."""
    tools = [_make_tool(name=f"t{i}") for i in range(5)]
    mock_llm.achat.return_value = LLMResponse(
        content='[{"name":"t0","args":{}},{"name":"t1","args":{}},{"name":"t2","args":{}},{"name":"t3","args":{}},{"name":"t4","args":{}}]',
        model="test",
    )

    selected = await coordinator._select_tool_with_llm("q", tools, max_tools=2)

    assert len(selected) == 2


# ========== 工具结果注入研究上下文 ==========


@pytest.mark.asyncio
async def test_conduct_mcp_if_enabled_disabled_returns_empty() -> None:
    """conduct_mcp_if_enabled: disabled 策略 → 返回空, 不查 DB."""
    settings = Settings(_env_file=None, mcp_strategy="disabled")

    with patch(
        "src.skills.researcher.mcp_coordinator.get_user_mcp_configs",
        new=AsyncMock(),
    ) as mock_get_configs:
        result = await conduct_mcp_if_enabled(settings, "sub_query", user_id="u1", session_id="s1")

    assert result == []
    mock_get_configs.assert_not_awaited()


@pytest.mark.asyncio
async def test_conduct_mcp_if_enabled_no_configs_returns_empty() -> None:
    """conduct_mcp_if_enabled: 用户无启用 MCP 配置 → 返回空."""
    settings = Settings(_env_file=None, mcp_strategy="fast")

    with patch(
        "src.skills.researcher.mcp_coordinator.get_user_mcp_configs",
        new=AsyncMock(return_value=[]),
    ) as mock_get_configs:
        result = await conduct_mcp_if_enabled(settings, "sub_query", user_id="u1", session_id="s1")

    assert result == []
    mock_get_configs.assert_awaited_once()


@pytest.mark.asyncio
async def test_conduct_mcp_if_enabled_injects_contexts_into_research() -> None:
    """conduct_mcp_if_enabled: 正常流程 → 返回 MCP 上下文列表, 注入研究."""
    settings = Settings(_env_file=None, mcp_strategy="fast")
    mcp_contexts = ["ctx from mcp tool 1", "ctx from mcp tool 2"]

    mock_mcp = MagicMock()
    mock_mcp.conduct_research = AsyncMock(return_value=mcp_contexts)

    with (
        patch(
            "src.skills.researcher.mcp_coordinator.get_mcp_coordinator",
            return_value=mock_mcp,
        ),
        patch(
            "src.skills.researcher.mcp_coordinator.get_user_mcp_configs",
            new=AsyncMock(return_value=[_make_mcp_config()]),
        ),
    ):
        result = await conduct_mcp_if_enabled(settings, "sub_query", user_id="u1", session_id="s1")

    assert result == mcp_contexts
    # 验证 MCP 调用参数透传
    mock_mcp.conduct_research.assert_awaited_once()
    call_kwargs = mock_mcp.conduct_research.call_args
    assert call_kwargs.args[0] == "sub_query"
    assert call_kwargs.kwargs["user_id"] == "u1"
    assert call_kwargs.kwargs["session_id"] == "s1"


@pytest.mark.asyncio
async def test_conduct_mcp_if_enabled_failure_degrades_to_empty() -> None:
    """conduct_mcp_if_enabled: MCP 调用异常 → 降级返回空列表, 不阻断研究."""
    settings = Settings(_env_file=None, mcp_strategy="fast")

    with (
        patch(
            "src.skills.researcher.mcp_coordinator.get_mcp_coordinator",
            side_effect=RuntimeError("MCP unavailable"),
        ),
        patch(
            "src.skills.researcher.mcp_coordinator.get_user_mcp_configs",
            new=AsyncMock(return_value=[_make_mcp_config()]),
        ),
    ):
        result = await conduct_mcp_if_enabled(settings, "sub_query", user_id="u1", session_id="s1")

    assert result == []


# ========== 缓存机制 (TTL + LRU) ==========


@pytest.mark.asyncio
async def test_call_single_tool_cache_hit_skips_invoke(
    coordinator: MCPCoordinator,
) -> None:
    """TTL 缓存命中 → 直接返回缓存结果, 不调用 tool.ainvoke."""
    tool = _make_tool(name="t", result="fresh")
    cache_key = _make_cache_key("query", "t", {"q": "x"})
    _MCP_CACHE[cache_key] = ("cached result", time.time() + 100)

    sem = asyncio.Semaphore(MCP_MAX_CONCURRENCY)
    result = await coordinator._call_single_tool(
        tool, {"q": "x"}, "query", cache_enabled=True, sem=sem
    )

    assert result == "cached result"
    tool.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_single_tool_cache_miss_invokes_and_writes(
    coordinator: MCPCoordinator,
) -> None:
    """TTL 缓存未命中 → 调用 tool.ainvoke, 成功后写入缓存."""
    tool = _make_tool(name="t", result="fresh result")
    sem = asyncio.Semaphore(MCP_MAX_CONCURRENCY)

    result = await coordinator._call_single_tool(
        tool, {"q": "x"}, "query", cache_enabled=True, sem=sem
    )

    assert result == "fresh result"
    tool.ainvoke.assert_awaited_once()
    # 缓存已写入
    cache_key = _make_cache_key("query", "t", {"q": "x"})
    assert cache_key in _MCP_CACHE


@pytest.mark.asyncio
async def test_call_single_tool_expired_cache_reinvokes(
    coordinator: MCPCoordinator,
) -> None:
    """缓存过期 → 删除旧条目, 重新调用工具."""
    tool = _make_tool(name="t", result="new")
    cache_key = _make_cache_key("query", "t", {"q": "x"})
    _MCP_CACHE[cache_key] = ("expired", time.time() - 1)  # 已过期

    sem = asyncio.Semaphore(MCP_MAX_CONCURRENCY)
    result = await coordinator._call_single_tool(
        tool, {"q": "x"}, "query", cache_enabled=True, sem=sem
    )

    assert result == "new"
    tool.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_single_tool_cache_disabled_no_write(
    coordinator: MCPCoordinator,
) -> None:
    """cache_enabled=False → 不读不写缓存."""
    tool = _make_tool(name="t", result="result")
    sem = asyncio.Semaphore(MCP_MAX_CONCURRENCY)

    await coordinator._call_single_tool(tool, {"q": "x"}, "query", cache_enabled=False, sem=sem)

    assert len(_MCP_CACHE) == 0


@pytest.mark.asyncio
async def test_mcp_cache_lru_eviction_when_exceeds_max_size(
    coordinator: MCPCoordinator,
) -> None:
    """LRU 淘汰: 缓存超过 _MCP_CACHE_MAX_SIZE 时弹出最旧项."""
    # 填充到上限
    for i in range(_MCP_CACHE_MAX_SIZE):
        _MCP_CACHE[f"key{i}"] = (f"val{i}", time.time() + 100)
        _MCP_CACHE.move_to_end(f"key{i}")

    assert len(_MCP_CACHE) == _MCP_CACHE_MAX_SIZE

    # 触发一次写入, 应淘汰最旧项
    tool = _make_tool(name="new_tool", result="new")
    sem = asyncio.Semaphore(MCP_MAX_CONCURRENCY)
    await coordinator._call_single_tool(tool, {"q": "x"}, "query", cache_enabled=True, sem=sem)

    assert len(_MCP_CACHE) == _MCP_CACHE_MAX_SIZE
    # 最旧项 key0 被淘汰
    assert "key0" not in _MCP_CACHE


def test_clear_cache_clears_all_three_layers(coordinator: MCPCoordinator) -> None:
    """clear_cache 清空三层缓存: 实例级 + client_cache + 模块级 TTL."""
    # 准备数据
    coordinator._cache = ["cached"]
    coordinator._cache_query = "q"
    coordinator._client_cache["k"] = MagicMock()
    _MCP_CACHE["ttl_key"] = ("val", time.time() + 100)

    coordinator.clear_cache()

    assert coordinator._cache is None
    assert coordinator._cache_query is None
    assert len(coordinator._client_cache) == 0
    assert len(_MCP_CACHE) == 0


def test_clear_cache_idempotent_when_empty(coordinator: MCPCoordinator) -> None:
    """clear_cache 在缓存已空时仍安全 (幂等)."""
    coordinator.clear_cache()
    coordinator.clear_cache()  # 二次调用不抛异常

    assert coordinator._cache is None
    assert len(_MCP_CACHE) == 0


# ========== 工具调用失败降级 ==========


@pytest.mark.asyncio
async def test_call_single_tool_invoke_failure_returns_none(
    coordinator: MCPCoordinator,
) -> None:
    """工具 ainvoke 抛异常 → 返回 None, 不影响其他工具 (由 gather 调用方过滤)."""
    tool = _make_tool(name="t")
    tool.ainvoke = AsyncMock(side_effect=RuntimeError("tool down"))
    sem = asyncio.Semaphore(MCP_MAX_CONCURRENCY)

    result = await coordinator._call_single_tool(
        tool, {"q": "x"}, "query", cache_enabled=True, sem=sem
    )

    assert result is None
    # 异常工具不写入缓存
    assert len(_MCP_CACHE) == 0


@pytest.mark.asyncio
async def test_call_single_tool_timeout_returns_none(
    coordinator: MCPCoordinator,
) -> None:
    """工具 ainvoke 超时 (>MCP_TOOL_TIMEOUT_SECONDS) → 返回 None."""

    async def _slow_invoke(_: Any) -> str:
        await asyncio.sleep(MCP_TOOL_TIMEOUT_SECONDS + 1)
        return "never"

    tool = _make_tool(name="t")
    tool.ainvoke = _slow_invoke
    sem = asyncio.Semaphore(MCP_MAX_CONCURRENCY)

    result = await coordinator._call_single_tool(
        tool, {"q": "x"}, "query", cache_enabled=True, sem=sem
    )

    assert result is None


@pytest.mark.asyncio
async def test_execute_mcp_no_tools_returns_empty(
    coordinator: MCPCoordinator,
) -> None:
    """MCP Server 返回空工具列表 → 返回空上下文."""
    mock_client = MagicMock()
    mock_client.get_tools = AsyncMock(return_value=[])

    with patch.object(coordinator, "_get_or_create_client", return_value=mock_client):
        result = await coordinator._execute_mcp("q", [_make_mcp_config()])

    assert result == []


@pytest.mark.asyncio
async def test_execute_mcp_invalid_config_skipped(
    coordinator: MCPCoordinator,
) -> None:
    """无效 MCP 配置 (stdio 缺 command / 远程缺 url) → 跳过, 不报错."""
    bad_configs = [
        {"name": "bad-stdio", "transport_type": "stdio", "command": None},
        {"name": "bad-remote", "transport_type": "sse", "server_url": None, "url": None},
    ]

    # 所有配置均无效 → 返回空, 不构建 client
    with patch.object(coordinator, "_get_or_create_client", return_value=None) as mock_factory:
        result = await coordinator._execute_mcp("q", bad_configs)

    assert result == []
    mock_factory.assert_not_called()


@pytest.mark.asyncio
async def test_execute_mcp_langchain_adapters_missing_returns_empty(
    coordinator: MCPCoordinator,
) -> None:
    """langchain-mcp-adapters 未安装 → _get_or_create_client 返回 None → 空列表."""
    with patch.object(coordinator, "_get_or_create_client", return_value=None):
        result = await coordinator._execute_mcp("q", [_make_mcp_config()])

    assert result == []


# ========== 并发调用 ==========


@pytest.mark.asyncio
async def test_conduct_research_multiple_tools_concurrent(
    coordinator: MCPCoordinator,
    mock_llm: MagicMock,
) -> None:
    """多个 MCP 工具 → asyncio.gather 并发执行, 单个失败不影响其他."""
    tool1 = _make_tool(name="t1", result="r1")
    tool2 = _make_tool(name="t2", result="r2")
    tool3 = _make_tool(name="t3", result=None)  # 空结果 → None
    tool4 = _make_tool(name="t4")
    tool4.ainvoke = AsyncMock(side_effect=RuntimeError("t4 failed"))

    mock_llm.achat.return_value = LLMResponse(
        content='[{"name":"t1","args":{}},{"name":"t2","args":{}},{"name":"t3","args":{}},{"name":"t4","args":{}}]',
        model="test",
    )
    mock_client = MagicMock()
    mock_client.get_tools = AsyncMock(return_value=[tool1, tool2, tool3, tool4])

    with patch.object(coordinator, "_get_or_create_client", return_value=mock_client):
        # 关闭缓存以观察工具实际调用
        coordinator.settings = Settings(
            _env_file=None, mcp_strategy="deep", mcp_cache_enabled=False
        )
        result = await coordinator.conduct_research("query", mcp_configs=[_make_mcp_config()])

    # t3 空结果被过滤, t4 异常被过滤, 仅 t1/t2 返回
    assert set(result) == {"r1", "r2"}


# ========== Trace span 记录 ==========


@pytest.mark.asyncio
async def test_conduct_research_wraps_trace_tool_span(
    coordinator: MCPCoordinator,
    mock_llm: MagicMock,
) -> None:
    """conduct_research 用 trace_tool span 包裹 (name=mcp-research)."""
    tool = _make_tool(result="ctx")
    mock_llm.achat.return_value = LLMResponse(
        content='[{"name":"search_tool","args":{"query":"q"}}]',
        model="test",
    )
    mock_client = MagicMock()
    mock_client.get_tools = AsyncMock(return_value=[tool])

    with (
        patch.object(coordinator, "_get_or_create_client", return_value=mock_client),
        patch("src.skills.researcher.mcp_coordinator.trace_tool") as mock_trace,
    ):
        mock_span = MagicMock()
        mock_trace.return_value.__aenter__ = AsyncMock(return_value=mock_span)
        mock_trace.return_value.__aexit__ = AsyncMock(return_value=None)

        await coordinator.conduct_research("q", mcp_configs=[_make_mcp_config()])

    mock_trace.assert_called_once()
    call_kwargs = mock_trace.call_args
    assert call_kwargs.kwargs["name"] == "mcp-research"
    # span.update 被调用记录成功结果
    assert mock_span.update.call_count >= 1


@pytest.mark.asyncio
async def test_conduct_research_span_records_failure_on_exception(
    coordinator: MCPCoordinator,
) -> None:
    """conduct_research 内部异常 → span.update 记录 success=False."""
    with (
        patch.object(
            coordinator,
            "_execute_mcp",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch("src.skills.researcher.mcp_coordinator.trace_tool") as mock_trace,
    ):
        mock_span = MagicMock()
        mock_trace.return_value.__aenter__ = AsyncMock(return_value=mock_span)
        mock_trace.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await coordinator.conduct_research("q", mcp_configs=[_make_mcp_config()])

    assert result == []
    # 至少一次 span.update 记录 failure
    update_calls = mock_span.update.call_args_list
    assert any(call.kwargs.get("metadata", {}).get("success") is False for call in update_calls)


# ========== 全局单例 ==========


def test_get_mcp_coordinator_returns_singleton() -> None:
    """get_mcp_coordinator 返回全局单例 (多次调用同一实例)."""
    # 重置单例
    import src.skills.researcher.mcp_coordinator as mod

    mod._mcp_coordinator_instance = None
    inst1 = get_mcp_coordinator()
    inst2 = get_mcp_coordinator()
    assert inst1 is inst2
    # 清理
    mod._mcp_coordinator_instance = None


# ========== MultiServerMCPClient 缓存 ==========


def test_get_or_create_client_caches_by_config_hash(
    coordinator: MCPCoordinator,
) -> None:
    """相同 server_configs 复用同一 MultiServerMCPClient (按 sha256 缓存)."""
    configs = {"server1": {"url": "http://x", "transport": "sse"}}
    fake_client_cls = MagicMock()

    # 注入 fake langchain_mcp_adapters 模块 (实际环境未安装该包)
    fake_pkg = types.ModuleType("langchain_mcp_adapters")
    fake_client_mod = types.ModuleType("langchain_mcp_adapters.client")
    fake_client_mod.MultiServerMCPClient = fake_client_cls
    fake_pkg.client = fake_client_mod

    with patch.dict(
        sys.modules,
        {
            "langchain_mcp_adapters": fake_pkg,
            "langchain_mcp_adapters.client": fake_client_mod,
        },
    ):
        client1 = coordinator._get_or_create_client(configs)
        client2 = coordinator._get_or_create_client(configs)

    assert client1 is client2
    fake_client_cls.assert_called_once_with(configs)


def test_get_or_create_client_different_configs_different_instance(
    coordinator: MCPCoordinator,
) -> None:
    """不同 server_configs → 不同 client 实例."""
    cfg1 = {"s1": {"url": "http://1", "transport": "sse"}}
    cfg2 = {"s2": {"url": "http://2", "transport": "sse"}}
    fake_client_cls = MagicMock(side_effect=lambda x: MagicMock())

    # 注入 fake langchain_mcp_adapters 模块 (实际环境未安装该包)
    fake_pkg = types.ModuleType("langchain_mcp_adapters")
    fake_client_mod = types.ModuleType("langchain_mcp_adapters.client")
    fake_client_mod.MultiServerMCPClient = fake_client_cls
    fake_pkg.client = fake_client_mod

    with patch.dict(
        sys.modules,
        {
            "langchain_mcp_adapters": fake_pkg,
            "langchain_mcp_adapters.client": fake_client_mod,
        },
    ):
        c1 = coordinator._get_or_create_client(cfg1)
        c2 = coordinator._get_or_create_client(cfg2)

    assert c1 is not c2
    assert fake_client_cls.call_count == 2
