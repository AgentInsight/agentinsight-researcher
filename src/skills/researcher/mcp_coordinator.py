"""MCP Coordinator MCP 协调器.

对标 GPT Researcher mcp/ 模块.
AGENTS.md 用户需求 9: 支持用户配置 MCP 作为数据源.

三策略 (对标 GPT Researcher):
- fast (默认): 仅对原始查询运行一次, 缓存复用
- deep: 每子查询都运行
- disabled: 完全跳过
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.settings import Settings, get_settings
from src.llm.client import LLMClient
from src.observability.tracing import trace_tool

logger = logging.getLogger(__name__)


class MCPCoordinator:
    """MCP 协调器.

    对标 GPT Researcher MCPResearchSkill.
    管理用户配置的 MCP Server 作为数据源.
    """

    settings: Settings
    _llm: LLMClient
    _cache: list[str] | None
    _cache_query: str | None

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or LLMClient(self.settings)
        self._cache = None
        self._cache_query = None

    async def conduct_research(
        self,
        query: str,
        *,
        strategy: str | None = None,
        mcp_configs: list[dict[str, Any]] | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[str]:
        """MCP 研究 (三策略).

        返回 MCP 检索到的上下文列表.
        对标 GPT Researcher conduct_research_with_tools.
        """
        strat = strategy or self.settings.mcp_strategy
        if strat == "disabled":
            return []

        if not mcp_configs:
            return []

        # fast 策略: 缓存复用
        if strat == "fast" and self._cache is not None and self._cache_query == query:
            return self._cache.copy()

        async with trace_tool(
            name="mcp-research",
            input={"query": query[:100], "strategy": strat, "configs_count": len(mcp_configs)},
            metadata={"tool_name": "mcp", "strategy": strat},
        ) as span:
            try:
                # 用 langchain-mcp-adapters 连接 MCP Server
                context = await self._execute_mcp(
                    query,
                    mcp_configs,
                    user_id=user_id,
                    session_id=session_id,
                )

                # fast 策略: 缓存
                if strat == "fast":
                    self._cache = context.copy()
                    self._cache_query = query

                span.update(
                    output={"context_count": len(context)},
                    metadata={"tool_name": "mcp", "success": True, "strategy": strat},
                )
                return context
            except Exception as e:  # noqa: BLE001
                logger.warning("MCP 研究失败: %s", e)
                span.update(metadata={"tool_name": "mcp", "success": False, "error": str(e)})
                return []

    async def _execute_mcp(
        self,
        query: str,
        mcp_configs: list[dict[str, Any]],
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[str]:
        """执行 MCP 工具调用.

        对标 GPT Researcher MCPClientManager + MCPToolSelector.
        阶段 4 完整实现, 此处为骨架.
        """
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient

            # 转换配置格式 (对标 GPT Researcher convert_configs_to_langchain_format)
            server_configs = {}
            for cfg in mcp_configs:
                name = cfg.get("name", "default")
                url = cfg.get("url", "")
                if url.startswith(("wss://", "ws://")):
                    transport = "websocket"
                elif url.startswith(("https://", "http://")):
                    transport = "streamable_http"
                else:
                    transport = "stdio"
                server_configs[name] = {
                    "url": url,
                    "transport": transport,
                }

            client = MultiServerMCPClient(server_configs)
            tools = await client.get_tools()

            if not tools:
                logger.warning("MCP 未返回任何工具")
                return []

            # LLM 智能选工具 (对标 GPT Researcher MCPToolSelector)
            max_tools = self.settings.mcp_max_tools
            selected_tools = await self._select_tools(
                query,
                tools,
                max_tools,
                user_id=user_id,
                session_id=session_id,
            )

            # 执行工具调用
            contexts: list[str] = []
            for tool in selected_tools:
                try:
                    result = await tool.ainvoke({"query": query})
                    if result:
                        contexts.append(str(result))
                except Exception as e:  # noqa: BLE001
                    logger.warning("MCP 工具 %s 调用失败: %s", tool.name, e)

            return contexts
        except ImportError:
            logger.warning("langchain-mcp-adapters 未安装, MCP 数据源不可用")
            return []
        except Exception as e:  # noqa: BLE001
            logger.warning("MCP 执行失败: %s", e)
            return []

    async def _select_tools(
        self,
        query: str,
        tools: list[Any],
        max_tools: int,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[Any]:
        """LLM 智能选工具 (对标 GPT Researcher MCPToolSelector).

        阶段 4 完整实现, 此处用简单匹配.
        """
        if not tools:
            return []

        # 简单匹配: 名称/描述含查询关键词的工具优先
        query_lower = query.lower()
        scored: list[tuple[int, Any]] = []
        for tool in tools:
            name = getattr(tool, "name", "").lower()
            desc = getattr(tool, "description", "").lower()
            score = 0
            for word in query_lower.split():
                if word in name:
                    score += 3
                if word in desc:
                    score += 1
            scored.append((score, tool))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [tool for _, tool in scored[:max_tools]]

    def clear_cache(self) -> None:
        """清空缓存."""
        self._cache = None
        self._cache_query = None
