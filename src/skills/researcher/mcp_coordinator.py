"""MCP Coordinator MCP 协调器.

支持用户配置 MCP 作为数据源.

三策略:
- fast (默认): 仅对原始查询运行一次, 缓存复用
- deep: 每子查询都运行
- disabled: 完全跳过

工具调用结果 TTL 缓存, 缓存 key = hash(agent_id + user_id + query + tool_name + tool_args),
含 agent_id + user_id 隔离键 (多 Agent 共享模块级缓存, 避免跨 Agent / 跨用户串扰),
命中直接返回, 未命中调用 MCP Server 后写入缓存.

多工具并发调用 (asyncio.gather + 信号量), 默认并发上限 3,
单个工具失败不影响其他工具. 保留 TTL 缓存逻辑.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import Any

from src.config.settings import Settings, get_settings
from src.llm.client import LLMClient, LLMTier, get_llm_client
from src.observability.tracing import trace_tool

logger = logging.getLogger(__name__)

# 模块级 TTL 缓存: key -> (result, expire_time)
# 用 OrderedDict 实现 LRU 淘汰, max 256 项
_MCP_CACHE: OrderedDict[str, tuple[Any, float]] = OrderedDict()
_MCP_CACHE_MAX_SIZE = 256

# MCP 工具调用并发上限 (信号量)
MCP_MAX_CONCURRENCY = 3

# 单个 MCP 工具调用超时上限 (秒)
MCP_TOOL_TIMEOUT_SECONDS = 30.0


def _make_cache_key(
    agent_id: str | None,
    user_id: str | None,
    query: str,
    tool_name: str,
    tool_args: dict[str, Any],
) -> str:
    """生成 MCP 工具调用缓存 key.

    key = sha256(agent_id + user_id + query + tool_name + tool_args 序列化),
    含 agent_id + user_id 隔离键, 避免跨 Agent / 跨用户缓存串扰
    (多 Agent 共享模块级 _MCP_CACHE, 必须按 agent_id + user_id 隔离).

    Args:
        agent_id: Agent ID (即 agent_name, 多 Agent 数据隔离键)
        user_id: 用户 ID
        query: 用户查询
        tool_name: 工具名
        tool_args: 工具调用参数

    Returns:
        32 位 hex 摘要字符串
    """
    try:
        args_str = json.dumps(tool_args, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        # 含不可序列化对象时降级为 repr
        args_str = repr(tool_args)
    raw = f"{agent_id or ''}:{user_id or ''}:{query}:{tool_name}:{args_str}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def get_user_mcp_configs(user_id: str, agent_id: str) -> list[dict[str, Any]]:
    """从 postgres 获取用户的启用 MCP 配置 (用户专属 + 系统共享).

    数据隔离键 agent_id = agent_name, 用户私有数据按 user_id 区分.
    Agent 初始化时调用, 合并到 MCP_SERVERS (动态工具注册).

    查询范围 (S-13 共享改造):
    - 用户专属 MCP: agent_id 精确匹配 + user_id 精确匹配 (is_system 可为 TRUE/FALSE)
    - 系统共享 MCP: agent_id IS NULL AND is_system=TRUE (全局共享, 所有 Agent 可用)

    排序: 系统共享 MCP 优先 (is_system DESC), 同类按 name ASC.

    Args:
        user_id: 用户 ID (从请求上下文注入).
        agent_id: Agent ID (即 agent_name).

    Returns:
        启用的 MCP 配置列表 (dict 含 name/server_url/transport_type/command/args/env_vars/is_system).
        查询失败时返回空列表 (降级, 不阻断研究流程).
    """
    try:
        from src.memory.db_initializer import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            # 用户专属 MCP (agent_id+user_id 精确匹配) OR 系统共享 MCP (agent_id IS NULL AND is_system=TRUE)
            # ORDER BY is_system DESC: 系统共享 MCP 排前 (便于日志/调试区分), name ASC 同类按字母序
            rows = await conn.fetch(
                "SELECT name, server_url, transport_type, command, args, env_vars, is_system "
                "FROM mcp_configs "
                "WHERE enabled=TRUE AND "
                "((agent_id=$1 AND user_id=$2) OR (agent_id IS NULL AND is_system=TRUE)) "
                "ORDER BY is_system DESC, name ASC",
                agent_id,
                user_id,
            )
        # 兼容 MCPCoordinator._execute_mcp 期望的 url 字段: 同时保留 server_url 与 url 别名
        configs: list[dict[str, Any]] = []
        for row in rows:
            cfg = dict(row)
            cfg["url"] = cfg.get("server_url", "")
            configs.append(cfg)
        return configs
    except Exception as e:  # noqa: BLE001
        logger.warning("获取用户 MCP 配置失败 (降级为空列表): %s", e)
        return []


async def conduct_mcp_if_enabled(
    settings: Settings,
    sub_query: str,
    user_id: str | None,
    session_id: str | None,
) -> list[str]:
    """MCP 工具调用公共入口 (DRY 收敛).

    取代 deep_research.py:373 和 research_conductor.py:649 的重复 28 行 MCP 调用块.
    两处原逻辑均为: if mcp_strategy != "disabled": try get_mcp + get_user_mcp_configs +
    conduct_research + 拼接 context, except 降级 warn.

    Args:
        settings: 全局配置 (含 mcp_strategy / agent_name)
        sub_query: 子查询 (MCP 工具调用的输入)
        user_id: 用户 ID (None 时降级空字符串)
        session_id: 会话 ID (用于 trace)

    Returns:
        MCP 上下文列表 (空列表表示未启用/失败/无配置). 调用方可直接 if mcp_contexts: 拼接.
    """
    if settings.mcp_strategy == "disabled":
        return []

    try:
        mcp = get_mcp_coordinator()
        mcp_configs = await get_user_mcp_configs(user_id or "", settings.agent_name)
        if not mcp_configs:
            return []

        contexts = await mcp.conduct_research(
            sub_query,
            strategy=settings.mcp_strategy,
            mcp_configs=mcp_configs,
            agent_id=settings.agent_name,
            user_id=user_id,
            session_id=session_id,
        )
        return contexts or []
    except Exception as e:  # noqa: BLE001
        logger.warning("MCP 工具调用失败 (不阻断): %s", e)
        return []


class MCPCoordinator:
    """MCP 协调器.

    管理用户配置的 MCP Server 作为数据源.
    """

    settings: Settings
    _llm: LLMClient
    _cache: list[str] | None
    _cache_query: str | None
    # MultiServerMCPClient 缓存, key = hash(server_configs)
    _client_cache: dict[str, Any]

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or get_llm_client()
        self._cache = None
        self._cache_query = None
        self._client_cache = {}

    def _get_or_create_client(self, server_configs: dict[str, Any]) -> Any | None:
        """缓存并复用 MultiServerMCPClient.

        避免每次 conduct_research 都重新构建客户端 (含连接初始化).
        key = hash(server_configs JSON 序列化), 相同配置复用客户端.

        Args:
            server_configs: MCP Server 配置字典 (name -> config)

        Returns:
            MultiServerMCPClient 实例, langchain-mcp-adapters 未安装时返回 None.
        """
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
        except ImportError:
            return None

        try:
            key = hashlib.sha256(
                json.dumps(server_configs, sort_keys=True, default=str).encode()
            ).hexdigest()
        except (TypeError, ValueError):
            # 序列化失败时直接构建 (无缓存)
            return MultiServerMCPClient(server_configs)

        if key not in self._client_cache:
            self._client_cache[key] = MultiServerMCPClient(server_configs)
        return self._client_cache[key]

    async def conduct_research(
        self,
        query: str,
        *,
        strategy: str | None = None,
        mcp_configs: list[dict[str, Any]] | None = None,
        agent_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[str]:
        """MCP 研究 (三策略).

        返回 MCP 检索到的上下文列表.
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
                # 透传 agent_id 用于缓存 key 隔离 (避免跨 Agent 缓存串扰)
                context = await self._execute_mcp(
                    query,
                    mcp_configs,
                    agent_id=agent_id,
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
        agent_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[str]:
        """执行 MCP 工具调用.

        支持两种传输模式 (MCP 协议):
        - stdio (本地模式): 通过 command/args/env 启动本地进程, 经 stdin/stdout 通信
        - sse / streamable_http (远程模式): 通过 server_url 连接远程 HTTP 服务器
        """
        try:
            # 转换配置格式
            # 根据数据库 transport_type 字段构建配置 (不再从 URL 推断)
            server_configs = {}
            for cfg in mcp_configs:
                name = cfg.get("name", "default")
                transport_type = cfg.get("transport_type", "stdio")
                url = cfg.get("url") or cfg.get("server_url") or ""

                if transport_type == "stdio":
                    # 本地模式: 通过 command/args/env 启动本地进程
                    command = cfg.get("command")
                    if not command:
                        logger.warning("MCP 配置 %s: stdio 模式缺少 command, 跳过", name)
                        continue
                    # args/env_vars 可能是 JSONB 字符串或已解析的 list/dict
                    # env_vars 接入 safe_json_parse_dict (含 json_repair + regex 兜底)
                    from src.common.json_utils import safe_json_parse, safe_json_parse_dict

                    args = cfg.get("args")
                    if isinstance(args, str):
                        args = safe_json_parse(args, fallback=None)
                    env_vars = cfg.get("env_vars")
                    if isinstance(env_vars, str):
                        env_vars = safe_json_parse_dict(env_vars)
                    server_configs[name] = {
                        "command": command,
                        "args": args or [],
                        "env": env_vars or {},
                        "transport": "stdio",
                    }
                else:
                    # 远程模式 (sse / streamable_http): 通过 server_url 连接
                    if not url:
                        logger.warning(
                            "MCP 配置 %s: %s 模式缺少 server_url, 跳过",
                            name,
                            transport_type,
                        )
                        continue
                    server_configs[name] = {
                        "url": url,
                        "transport": transport_type,
                    }

            if not server_configs:
                logger.warning("MCP 无可用配置 (所有配置均无效)")
                return []

            # 复用缓存的 MultiServerMCPClient (相同配置不重复构建)
            client = self._get_or_create_client(server_configs)
            if client is None:
                logger.warning("langchain-mcp-adapters 未安装, MCP 数据源不可用")
                return []
            tools = await client.get_tools()

            if not tools:
                logger.warning("MCP 未返回任何工具")
                return []

            # LLM 智能选工具 + 生成参数
            max_tools = self.settings.mcp_max_tools
            selected = await self._select_tool_with_llm(
                query,
                tools,
                max_tools,
                user_id=user_id,
                session_id=session_id,
            )

            # 并发执行工具调用 (asyncio.gather + 信号量, 默认并发 3)
            # 单个工具失败返回 None 不影响其他工具; 保留 TTL 缓存逻辑
            # 透传 agent_id + user_id 用于缓存 key 隔离 (避免跨 Agent / 跨用户缓存串扰)
            cache_enabled = self.settings.mcp_cache_enabled
            sem = asyncio.Semaphore(MCP_MAX_CONCURRENCY)
            results = await asyncio.gather(
                *[
                    self._call_single_tool(
                        tool,
                        args,
                        query,
                        cache_enabled,
                        sem,
                        agent_id=agent_id,
                        user_id=user_id,
                    )
                    for tool, args in selected
                ],
                return_exceptions=False,
            )
            contexts: list[str] = [r for r in results if r is not None]

            return contexts
        except Exception as e:  # noqa: BLE001
            logger.warning("MCP 执行失败: %s", e)
            return []

    async def _call_single_tool(
        self,
        tool: Any,
        tool_args: dict[str, Any],
        query: str,
        cache_enabled: bool,
        sem: asyncio.Semaphore,
        *,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> str | None:
        """执行单个 MCP 工具调用 (并发 + TTL 缓存).

        缓存命中直接返回 (不消耗信号量); 缓存未命中在信号量内调用工具.
        单个工具失败返回 None, 不影响其他工具 (由 gather 调用方过滤).

        Args:
            tool: MCP 工具对象 (含 ainvoke / name 属性)
            tool_args: 工具调用参数
            query: 用户查询 (用于缓存 key)
            cache_enabled: 是否启用 TTL 缓存
            sem: 并发信号量
            agent_id: Agent ID (即 agent_name, 缓存 key 隔离键)
            user_id: 用户 ID (缓存 key 隔离键)

        Returns:
            工具结果字符串, 失败/空结果返回 None
        """
        tool_name = getattr(tool, "name", "")
        # TTL 缓存检查 (缓存命中不消耗信号量)
        # cache key 含 agent_id + user_id, 避免跨 Agent / 跨用户缓存串扰
        cache_key: str | None = None
        if cache_enabled:
            cache_key = _make_cache_key(agent_id, user_id, query, tool_name, tool_args)
            if cache_key in _MCP_CACHE:
                cached_result, expire = _MCP_CACHE[cache_key]
                if time.time() < expire:
                    # 命中时移动到末尾 (LRU 最近使用)
                    _MCP_CACHE.move_to_end(cache_key)
                    logger.debug("MCP 缓存命中: tool=%s", tool_name)
                    return str(cached_result)
                else:
                    # 过期: 删除
                    _MCP_CACHE.pop(cache_key, None)
        # 信号量限制并发
        async with sem:
            try:
                # 单工具调用超时 (30s), 避免长时间阻塞
                result = await asyncio.wait_for(
                    tool.ainvoke(tool_args),
                    timeout=MCP_TOOL_TIMEOUT_SECONDS,
                )
                if result:
                    # 调用成功写入缓存
                    if cache_key is not None:
                        # LRU 淘汰, 超过 max_size 时弹出最旧项
                        _MCP_CACHE[cache_key] = (
                            result,
                            time.time() + self.settings.mcp_cache_ttl,
                        )
                        _MCP_CACHE.move_to_end(cache_key)
                        while len(_MCP_CACHE) > _MCP_CACHE_MAX_SIZE:
                            _MCP_CACHE.popitem(last=False)
                    return str(result)
            except TimeoutError:
                logger.warning(
                    "MCP 工具 %s 调用超时 (>%ss)",
                    tool_name or "?",
                    MCP_TOOL_TIMEOUT_SECONDS,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("MCP 工具 %s 调用失败: %s", tool_name or "?", e)
            return None

    async def _select_tool_with_llm(
        self,
        query: str,
        available_tools: list[Any],
        max_tools: int,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[tuple[Any, dict[str, Any]]]:
        """LLM 智能选工具 + 生成参数.

        返回 [(tool, tool_args), ...], 最多 max_tools 个.
        LLM 不可用或失败时降级到关键词匹配 (_select_tools).
        """
        if not available_tools:
            return []

        # 1. 构造工具描述 (含 name/description/参数 schema)
        tools_desc: list[dict[str, Any]] = []
        for t in available_tools:
            name = getattr(t, "name", "")
            desc = getattr(t, "description", "")
            # args_schema 可能是 pydantic model 或 dict
            args_schema = getattr(t, "args_schema", None)
            if args_schema is None:
                args: Any = getattr(t, "args", {})
            else:
                try:
                    args = (
                        args_schema.model_json_schema()
                        if hasattr(args_schema, "model_json_schema")
                        else dict(args_schema)
                    )
                except Exception:  # noqa: BLE001
                    args = {}
            tools_desc.append(
                {
                    "name": name,
                    "description": desc,
                    "parameters": args,
                }
            )

        tools_json = json.dumps(tools_desc, ensure_ascii=False, indent=2)

        prompt = f"""你是 MCP 工具选择专家. 根据用户查询选择最合适的 {max_tools} 个 MCP 工具并生成调用参数.

可用工具:
{tools_json}

用户查询: {query}

请返回 JSON 数组, 每项含 name 与 args:
[
  {{"name": "tool_name", "args": {{"param1": "value1"}}}},
  ...
]

仅返回 JSON, 不要其他内容:"""

        messages = [{"role": "user", "content": prompt}]
        try:
            response = await self._llm.achat(
                messages,
                tier=LLMTier.FAST,
                temperature=0.0,
                max_tokens=2000,
                user_id=user_id,
                session_id=session_id,
                span_name="mcp-tool-select",
                step="mcp",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("MCP LLM 选工具失败, 降级关键词匹配: %s", e)
            fallback_tools = await self._select_tools(query, available_tools, max_tools)
            return [(t, {"query": query}) for t in fallback_tools[:max_tools]]

        # 三级 JSON 容错解析 (json_utils 由并行 sub-agent 创建, 兜底本地解析)
        def _fallback_parse(text: str, fallback: Any = None) -> Any:
            try:
                return json.loads(text)
            except Exception:  # noqa: BLE001
                return fallback

        try:
            from src.common.json_utils import safe_json_parse
        except ImportError:
            safe_json_parse = _fallback_parse

        parsed: Any = safe_json_parse(response.content, fallback=[])
        if not isinstance(parsed, list):
            parsed = []

        # 映射回 tool 对象
        tool_map = {getattr(t, "name", ""): t for t in available_tools}
        selected: list[tuple[Any, dict[str, Any]]] = []
        for item in parsed[:max_tools]:
            if not isinstance(item, dict):
                continue
            name = item.get("name", "")
            args = item.get("args", {})
            if not isinstance(args, dict):
                args = {"query": query}
            # 确保 args 含 query (兜底)
            if "query" not in args:
                args["query"] = query
            tool = tool_map.get(name)
            if tool is not None:
                selected.append((tool, args))

        # 若 LLM 选了 0 个, 降级关键词匹配
        if not selected:
            fallback_tools = await self._select_tools(query, available_tools, max_tools)
            return [(t, {"query": query}) for t in fallback_tools[:max_tools]]

        return selected

    async def _select_tools(
        self,
        query: str,
        tools: list[Any],
        max_tools: int,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[Any]:
        """LLM 智能选工具.

        完整实现, 此处用简单匹配.
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
        """清空 MCPCoordinator 实例级缓存 (fast 策略复用缓存).

        MCP 配置 CRUD (create/update/delete/clone) 后应调用此方法失效缓存,
        避免用户修改 MCP 配置后命中过期缓存. 同时清空模块级 _MCP_CACHE (TTL 缓存)
        与 _client_cache (MultiServerMCPClient 缓存), 确保下次 conduct_research 重新加载.

        调用时机:
        - mcp_routes.create_mcp_config 后
        - mcp_routes.update_mcp_config 后 (含 enabled 切换)
        - mcp_routes.delete_mcp_config 后
        - mcp_routes.clone_system_mcp_config 后

        注: 模块级 _MCP_CACHE 按 (agent_id, user_id, query, tool_name, tool_args) 哈希,
        无法按单 user_id 精细清理, 全量清空以失效所有 Agent / 用户的工具调用结果缓存
        (TTL 兜底, 影响有限).
        """
        # 实例级缓存 (fast 策略复用)
        self._cache = None
        self._cache_query = None
        # MultiServerMCPClient 缓存 (避免配置变更后复用旧客户端)
        self._client_cache.clear()
        # 模块级 TTL 缓存 (工具调用结果, 全量清空)
        _MCP_CACHE.clear()


# ========== 全局单例 (供 conduct_mcp_if_enabled 公共方法使用) ==========
_mcp_coordinator_instance: MCPCoordinator | None = None


def get_mcp_coordinator() -> MCPCoordinator:
    """获取全局 MCPCoordinator 单例.

    复用全局 LLMClient 单例, 避免重复构造导致 step_costs 累计丢失.
    deep_research / research_conductor 通过 conduct_mcp_if_enabled 共享同一实例,
    fast 策略缓存跨调用方复用 (同一 query 不重复调用 MCP 工具).
    """
    global _mcp_coordinator_instance
    if _mcp_coordinator_instance is None:
        _mcp_coordinator_instance = MCPCoordinator()
    return _mcp_coordinator_instance
