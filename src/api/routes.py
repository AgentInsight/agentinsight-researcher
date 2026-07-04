"""API 路由: OpenAI 兼容端点 + 文件上传.

AGENTS.md 第 13/14 章硬约束:
- 统一调用 OpenAI 兼容端点 POST /v1/chat/completions, 请求体带 stream: true
- 测试页面只能走对外 OpenAI 兼容接口, 禁止调用后端私有端点
- API 测试必须覆盖流式 SSE + 非流式 + 错误码
- 包含携带 Bearer JWT Token 与不携带两种场景

阶段 3/4 实现: 接入 LangGraph 研究流水线 + 文件上传 (用户需求 8).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    File,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field

from src.api.middleware import (
    get_request_agent_id,
    get_request_session_id,
    get_request_user_id,
)
from src.config.settings import get_settings
from src.observability.tracing import trace_agent
from src.skills.researcher.query_classifier import (
    QueryIntent,
    get_query_intent_classifier,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["openai-compatible"])


# ========== LangGraph 图单例 (延迟构建, 复用) ==========
_compiled_graph: Any | None = None
_multi_agent_graph: Any | None = None
_chat_graph: Any | None = None  # P2-Future-03: 对话追问图单例


async def _get_graph(multi_agent: bool = False) -> Any:
    """获取/构建已编译的 LangGraph 单例.

    AGENTS.md 第 5 章: 生产 StateGraph 必须挂 PostgresSaver.
    首次调用时构建, 后续复用 (单例).
    P0-02: multi_agent=True 时构建多 Agent Supervisor 图.
    """
    global _compiled_graph, _multi_agent_graph
    if multi_agent:
        if _multi_agent_graph is None:
            from src.graph.multi_agent_builder import build_multi_agent_graph

            _multi_agent_graph = await build_multi_agent_graph(use_checkpointer=True)
        return _multi_agent_graph
    if _compiled_graph is None:
        from src.graph.builder import build_researcher_graph

        _compiled_graph = await build_researcher_graph(use_checkpointer=True)
    return _compiled_graph


async def _get_chat_graph() -> Any:
    """获取/构建对话追问图单例 (P2-Future-03).

    AGENTS.md 第 5 章: 生产 StateGraph 必须挂 PostgresSaver.
    复用同一 thread_id 隔离, 支持多会话并发.
    单节点 chat 图, 依赖 checkpointer 自动加载会话历史 (report_md / messages).
    """
    global _chat_graph
    if _chat_graph is None:
        from src.graph.chat_builder import build_chat_graph

        _chat_graph = await build_chat_graph(use_checkpointer=True)
    return _chat_graph


async def _has_report(session_id: str) -> bool:
    """检查会话是否已有报告 (P0-Future-05/06).

    从 checkpointer 读取会话状态, 判断是否已有 report_md.
    AGENTS.md 第 6 章: thread_id 做会话隔离, checkpointer 自动持久化.

    Args:
        session_id: 会话 ID (thread_id)

    Returns:
        True 表示会话已有报告 (用于意图分类 has_report 参数).
    """
    try:
        graph = await _get_graph()
        config: dict[str, Any] = {"configurable": {"thread_id": session_id}}
        state_snapshot = await graph.aget_state(config)
        if state_snapshot and state_snapshot.values:
            report_md = state_snapshot.values.get("report_md", "")
            return bool(report_md)
    except Exception as e:  # noqa: BLE001
        logger.warning("检查会话报告失败 (session=%s): %s", session_id, e)
    return False


# ========== 请求/响应模型 (OpenAI 兼容) ==========


class ChatMessage(BaseModel):
    """OpenAI 兼容消息格式."""

    role: str = "user"
    content: str = ""


class ChatCompletionRequest(BaseModel):
    """OpenAI 兼容 /v1/chat/completions 请求."""

    model: str = "agentinsight-researcher"
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    # 扩展字段 (非 OpenAI 标准, 用于研究配置)
    report_type: str | None = Field(
        None, description="报告类型: basic_report | detailed_report | deep_research"
    )
    report_format: str | None = Field(None, description="输出格式: markdown | html | pdf")
    tone: str | None = Field(
        None, description="语气: objective | analytical | opinionated | casual"
    )
    session_id: str | None = Field(None, description="会话 ID (thread_id), 不传则自动生成")
    uploaded_files: list[str] | None = Field(
        None, description="已上传文件 ID 列表 (来自 POST /v1/files), 作为研究数据源"
    )
    multi_agent: bool = Field(
        False,
        description="P0-02: 是否启用多 Agent Supervisor 模式 (默认单图流水线)",
    )
    agent_role: str | None = Field(
        None,
        description=(
            "对标 GPTR AGENT_ROLE 配置: 用户可注入行业 persona 字符串, "
            "优先级高于 LLM 动态生成 (AgentCreator). "
            "行业适配采用 GPTR 风格 4 层机制, 不再使用行业分类器."
        ),
    )
    query_domains: list[str] | None = Field(
        None,
        description="P1-Future-02: 域名过滤白名单, 仅检索这些域名的结果",
    )


class ChatCompletionResponse(BaseModel):
    """OpenAI 兼容非流式响应 (P0-01: 增加 sources 结构化字段)."""

    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[dict[str, Any]]
    # P1-04: usage 含 cost_usd (float), 放宽为 dict[str, Any] 兼容真实成本
    usage: dict[str, Any]
    # P0-01: 显式返回 sources 结构化列表 (对标 GPTR add_references)
    # 含 title/url/snippet/score 字段, 测试页面与下游消费者可直接渲染
    sources: list[dict[str, Any]] = []


# ========== 端点实现 ==========


@router.post("/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    req: Request,
    authorization: str | None = Header(None),
) -> Any:
    """OpenAI 兼容研究端点.

    AGENTS.md 第 14 章: 测试页面统一调用此端点, 请求体带 stream: true.
    阶段 3: 接入 LangGraph 研究流水线.
    """
    settings = get_settings()

    # 提取最后一条 user 消息作为研究查询
    user_messages = [m for m in request.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="messages 必须包含至少一条 user 消息")

    query = user_messages[-1].content
    if not query.strip():
        raise HTTPException(status_code=400, detail="查询内容不能为空")

    # 会话 ID (thread_id): 优先请求体, 其次中间件, 最后生成
    session_id = request.session_id or get_request_session_id() or str(uuid.uuid4())

    # user_id / agent_id 由中间件注入
    user_id = get_request_user_id()
    agent_id = get_request_agent_id()

    # P0-Future-05/06 + P1-Future-07: 查询意图分类 (短查询 + 离题闲聊 + 对话/研究路由)
    # 先检查会话是否已有报告 (用于分类器 has_report 参数)
    has_report = await _has_report(session_id)
    intent = await get_query_intent_classifier().classify(query, has_report)

    # P1-Future-07: CHAT 首轮保护 — 无已有报告时降级 OFF_TOPIC
    # 避免首轮闲聊走 chat graph 消耗 SMART LLM; 显式 report_type 时强制走 researcher graph
    if (
        intent == QueryIntent.CHAT
        and request.report_type is None
        and settings.chat_requires_report
        and not has_report
    ):
        logger.debug(
            "CHAT 首轮无报告, 降级 OFF_TOPIC (session_id=%s)",
            session_id,
        )
        intent = QueryIntent.OFF_TOPIC

    # P0-Future-06 + P1-Future-07: 短查询/离题闲聊 — 直接返回回复语, 不走任何 graph
    if intent in (QueryIntent.SHORT_QUERY, QueryIntent.OFF_TOPIC):
        reply = (
            settings.short_query_reply
            if intent == QueryIntent.SHORT_QUERY
            else settings.off_topic_reply
        )
        if request.stream:
            return StreamingResponse(
                _stream_short_query(reply, request, session_id, intent=intent.value),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Session-Id": session_id,
                },
            )
        return await _run_short_query(reply, request, session_id, intent=intent.value)

    # P1-Future-06: CHAT 意图走 chat graph (仅追问场景, 首轮已被降级到 OFF_TOPIC)
    # 显式指定 report_type 时强制走 researcher graph (用户明确要新研究)
    if intent == QueryIntent.CHAT and request.report_type is None:
        # 走 chat graph (复用会话历史 + report_md 上下文, 首轮时 report_md 为空)
        # 注意: initial_state 不含 report_md (由 checkpointer 自动加载, 避免覆盖)
        chat_state: dict[str, Any] = {
            "query": query,
            "session_id": session_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "query_intent": intent.value,
            "messages": [],
        }
        chat_config: dict[str, Any] = {"configurable": {"thread_id": session_id}}
        if request.stream:
            return StreamingResponse(
                _stream_chat(chat_state, chat_config, request, session_id),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Session-Id": session_id,
                },
            )
        else:
            return await _run_chat(chat_state, chat_config, request, session_id)

    # RESEARCH 意图 (或 CHAT + 显式 report_type) → researcher graph
    # 报告配置 (新研究模式)
    report_type = request.report_type or settings.default_report_type
    report_format = request.report_format or settings.default_report_format
    tone = request.tone or "objective"
    # P0-01: report_type == "deep_research" 时启用递归深度研究
    research_mode = "deep" if report_type == "deep_research" else "basic"

    # 加载已上传文件上下文 (用户需求 8)
    uploaded_files_context: list[str] = []
    if request.uploaded_files:
        uploaded_files_context = _load_uploaded_files_context(
            request.uploaded_files, user_id, agent_id
        )

    # 初始 State (AGENTS.md 第 5 章: TypedDict, 节点返回 delta)
    initial_state: dict[str, Any] = {
        "query": query,
        "session_id": session_id,
        "user_id": user_id,
        "agent_id": agent_id,
        "query_intent": intent.value,
        "report_type": report_type,
        "report_format": report_format,
        "tone": tone,
        "uploaded_files_context": uploaded_files_context,
        "messages": [],
        # 对标 GPTR: agent_role (来自 ChatRequest 或 settings) 优先级高于 LLM 动态生成
        "agent_role": request.agent_role or settings.agent_role or "",
        "agent_role_server": "",
        # P1-Future-02: 域名过滤白名单
        "query_domains": request.query_domains or [],
        "sub_queries": [],
        "contexts": [],
        "sources": [],
        "visited_urls": [],
        "curated_sources": [],
        "report_md": "",
        "report_html": "",
        "report_pdf_path": "",
        "status": "pending",
        # P0-01: 深度研究配置
        "research_mode": research_mode,
        "deep_research_breadth": settings.deep_research_breadth,
        "deep_research_depth": settings.deep_research_depth,
        # P0-02: 多 Agent 迭代计数器 (Annotated[int, operator.add] 累加)
        "iteration_count": 0,
    }

    # LangGraph 配置: thread_id 做会话隔离 (AGENTS.md 第 6 章)
    graph_config = {"configurable": {"thread_id": session_id}}

    if request.stream:
        return StreamingResponse(
            _stream_research(initial_state, graph_config, request, session_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Session-Id": session_id,
            },
        )
    else:
        # 非流式: 完整执行后返回
        return await _run_research(initial_state, graph_config, request, session_id)


async def _stream_research(
    initial_state: dict[str, Any],
    graph_config: dict[str, Any],
    request: ChatCompletionRequest,
    session_id: str,
) -> Any:
    """流式 SSE 响应生成器.

    阶段 3: 接入 LangGraph astream, 逐节点 yield 进度 + 最终报告.
    AGENTS.md 第 10 章: 用 trace_agent 包裹 graph.ainvoke 作为根 span.
    """
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    def _sse_chunk(delta: dict[str, Any], finish_reason: str | None = None) -> str:
        """构造 SSE 数据帧."""
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }
            ],
        }
        return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    # SSE 首块 (role)
    yield _sse_chunk({"role": "assistant"})

    # 根 span 包裹整次研究 (AGENTS.md 第 10 章)
    async with trace_agent(
        name="agentinsight-researcher",
        input={"query": initial_state["query"][:200], "session_id": session_id},
        metadata={
            "session_id": session_id,
            "intent": "research",
            "user_id": initial_state.get("user_id"),
        },
        session_id=session_id,
        user_id=initial_state.get("user_id"),
    ):
        try:
            graph = await _get_graph(multi_agent=request.multi_agent)

            # 节点名称中文映射 (用于流式进度提示)
            node_label = {
                "agent_creator": "生成研究角色",
                "deep_research": "深度研究",
                "research_conductor": "并行检索研究",
                "source_curator": "来源策展",
                "report_generator": "生成报告",
                "publisher": "格式化输出",
                # P0-02 多 Agent 节点
                "supervisor": "调度决策",
                "reviewer": "评估来源",
                "writer": "撰写报告",
            }

            final_state: dict[str, Any] = {}

            # astream 流式输出节点进度
            async for event in graph.astream(
                initial_state, config=graph_config, stream_mode="updates"
            ):
                # event 格式: {node_name: delta_dict}
                for node_name, delta in event.items():
                    if not isinstance(delta, dict):
                        continue
                    final_state.update(delta)

                    # 节点开始进度提示
                    label = node_label.get(node_name, node_name)
                    progress = f"\n\n> **[{label}]** "
                    if node_name == "agent_creator" and delta.get("agent_role_server"):
                        progress += f"已生成研究角色: {delta['agent_role_server']}\n"
                    elif node_name == "research_conductor":
                        sq_count = len(delta.get("sub_queries", []))
                        ctx_count = len(delta.get("contexts", []))
                        src_count = len(delta.get("sources", []))
                        progress += (
                            f"已生成 {sq_count} 子查询, 采集 {ctx_count} 上下文, {src_count} 来源\n"
                        )
                    elif node_name == "source_curator":
                        if delta.get("curated_sources"):
                            progress += f"已策展 {len(delta['curated_sources'])} 来源\n"
                    elif node_name == "report_generator" and delta.get("report_md"):
                        # 报告生成完成, 流式输出报告正文
                        report_md = delta["report_md"]
                        # 分块输出报告 (按段落)
                        paragraphs = report_md.split("\n\n")
                        for para in paragraphs:
                            yield _sse_chunk({"content": para + "\n\n"})
                            # P0-01 背压: 让出控制权, 允许消费者 (SSE writer) 刷出缓冲
                            await asyncio.sleep(0)
                        continue  # 跳过下面的进度提示
                    elif node_name == "publisher":
                        fmt = delta.get("report_format", "markdown")
                        progress += f"已发布 ({fmt})\n"

                    yield _sse_chunk({"content": progress})
                    # P0-01 背压: 让出控制权, 允许消费者 (SSE writer) 刷出缓冲
                    await asyncio.sleep(0)

            # 输出最终报告元信息
            final_md = final_state.get("report_md", "")
            if not final_md:
                yield _sse_chunk({"content": "\n\n*未生成报告内容 (可能上下文为空)*"})

            # P0-01: 流式推送 sources 元信息帧 (在 finish 之前)
            # 客户端可通过 delta.sources 获取结构化来源列表
            final_sources = final_state.get("curated_sources") or final_state.get("sources", [])
            normalized_sources: list[dict[str, Any]] = []
            for src in final_sources[:20]:
                if isinstance(src, dict):
                    normalized_sources.append(
                        {
                            "title": src.get("title", "") or "",
                            "url": src.get("url", "") or src.get("href", "") or "",
                            "snippet": src.get("snippet", "") or "",
                            "score": src.get("score", 0.0) or 0.0,
                        }
                    )
            if normalized_sources:
                yield _sse_chunk({"sources": normalized_sources})

        except Exception as e:
            logger.exception("研究流水线执行失败")
            yield _sse_chunk({"content": f"\n\n**研究执行失败**: {str(e)[:200]}"})

    # SSE 末块 (finish_reason)
    yield _sse_chunk({}, finish_reason="stop")
    yield "data: [DONE]\n\n"


async def _run_research(
    initial_state: dict[str, Any],
    graph_config: dict[str, Any],
    request: ChatCompletionRequest,
    session_id: str,
) -> ChatCompletionResponse:
    """非流式研究执行.

    阶段 3: 接入 LangGraph ainvoke.
    AGENTS.md 第 10 章: trace_agent 根 span.
    """
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    async with trace_agent(
        name="agentinsight-researcher",
        input={"query": initial_state["query"][:200], "session_id": session_id},
        metadata={
            "session_id": session_id,
            "intent": "research",
            "user_id": initial_state.get("user_id"),
        },
        session_id=session_id,
        user_id=initial_state.get("user_id"),
    ):
        try:
            graph = await _get_graph(multi_agent=request.multi_agent)
            final_state = await graph.ainvoke(initial_state, config=graph_config)
            content = final_state.get("report_md", "")
            if not content:
                content = "未生成报告内容 (可能上下文为空)"

            # P0-01: sources 作为结构化字段返回 (优先 curated_sources, 回退 sources)
            sources = final_state.get("curated_sources") or final_state.get("sources", [])
            # 规范化: 仅保留 title/url/snippet/score 四字段, 便于客户端渲染
            normalized_sources: list[dict[str, Any]] = []
            for src in sources[:20]:
                if isinstance(src, dict):
                    normalized_sources.append(
                        {
                            "title": src.get("title", "") or "",
                            "url": src.get("url", "") or src.get("href", "") or "",
                            "snippet": src.get("snippet", "") or "",
                            "score": src.get("score", 0.0) or 0.0,
                        }
                    )

            # P1-04: 读取 LLMClient 真实成本 (优先于字符估算)
            total_cost_usd = final_state.get("total_cost_usd", 0.0) or 0.0
            total_tokens = final_state.get("total_tokens", 0) or 0
            token_logs = final_state.get("token_logs", []) or []
            if total_tokens > 0:
                prompt_tokens = sum(
                    int(log.get("prompt_tokens", 0)) for log in token_logs if isinstance(log, dict)
                )
                completion_tokens = sum(
                    int(log.get("completion_tokens", 0))
                    for log in token_logs
                    if isinstance(log, dict)
                )
                usage = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "cost_usd": round(total_cost_usd, 6),
                }
            else:
                # 降级: 字符估算 (向后兼容)
                prompt_tokens = len(initial_state["query"]) // 4
                completion_tokens = len(content) // 4
                usage = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                }

        except Exception as e:
            logger.exception("研究流水线执行失败")
            content = f"研究执行失败: {str(e)[:500]}"
            normalized_sources = []
            usage = {
                "prompt_tokens": len(initial_state["query"]) // 4,
                "completion_tokens": len(content) // 4,
                "total_tokens": (len(initial_state["query"]) + len(content)) // 4,
            }

    return ChatCompletionResponse(
        id=completion_id,
        created=created,
        model=request.model,
        choices=[
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        usage=usage,
        sources=normalized_sources,
    )


# ========== 短查询保护 (P0-Future-06, 不走任何 graph) + 离题闲聊保护 (P1-Future-07) ==========


async def _stream_short_query(
    reply: str,
    request: ChatCompletionRequest,
    session_id: str,
    *,
    intent: str = "short_query",
) -> Any:
    """流式 SSE 短查询/离题回复生成器 (P0-Future-06 + P1-Future-07).

    直接返回 settings.short_query_reply / settings.off_topic_reply, 不走任何 graph.
    AGENTS.md 第 10 章: 用 trace_agent 包裹作为根 span.

    Args:
        reply: 回复内容 (短查询回复语或离题回复语)
        request: OpenAI 兼容请求
        session_id: 会话 ID
        intent: 意图类型 ("short_query" 或 "off_topic"), 用于 trace 区分
    """
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    def _sse_chunk(delta: dict[str, Any], finish_reason: str | None = None) -> str:
        """构造 SSE 数据帧."""
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }
            ],
        }
        return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    # SSE 首块 (role)
    yield _sse_chunk({"role": "assistant"})

    span_name = f"agentinsight-researcher-{intent}"
    async with trace_agent(
        name=span_name,
        input={"session_id": session_id},
        metadata={
            "session_id": session_id,
            "intent": intent,
        },
        session_id=session_id,
    ):
        yield _sse_chunk({"content": reply})

    # SSE 末块 (finish_reason)
    yield _sse_chunk({}, finish_reason="stop")
    yield "data: [DONE]\n\n"


async def _run_short_query(
    reply: str,
    request: ChatCompletionRequest,
    session_id: str,
    *,
    intent: str = "short_query",
) -> ChatCompletionResponse:
    """非流式短查询/离题回复 (P0-Future-06 + P1-Future-07).

    直接返回 settings.short_query_reply / settings.off_topic_reply, 不走任何 graph.
    AGENTS.md 第 10 章: trace_agent 根 span.

    Args:
        reply: 回复内容 (短查询回复语或离题回复语)
        request: OpenAI 兼容请求
        session_id: 会话 ID
        intent: 意图类型 ("short_query" 或 "off_topic"), 用于 trace 区分
    """
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    span_name = f"agentinsight-researcher-{intent}"
    async with trace_agent(
        name=span_name,
        input={"session_id": session_id},
        metadata={
            "session_id": session_id,
            "intent": intent,
        },
        session_id=session_id,
    ):
        content = reply

    prompt_tokens = 0
    completion_tokens = len(content) // 4

    return ChatCompletionResponse(
        id=completion_id,
        created=created,
        model=request.model,
        choices=[
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        usage={
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    )


# ========== 对话追问 (P2-Future-03 ChatAgentWithMemory) ==========


async def _stream_chat(
    initial_state: dict[str, Any],
    graph_config: dict[str, Any],
    request: ChatCompletionRequest,
    session_id: str,
) -> Any:
    """流式 SSE 对话追问响应生成器 (P2-Future-03).

    走 chat graph (单节点), 流式输出 AI 回答.
    AGENTS.md 第 10 章: 用 trace_agent 包裹 graph.ainvoke 作为根 span.
    """
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    def _sse_chunk(delta: dict[str, Any], finish_reason: str | None = None) -> str:
        """构造 SSE 数据帧."""
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason,
                }
            ],
        }
        return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    # SSE 首块 (role)
    yield _sse_chunk({"role": "assistant"})

    # 根 span 包裹对话追问 (AGENTS.md 第 10 章)
    async with trace_agent(
        name="agentinsight-researcher-chat",
        input={"query": initial_state["query"][:200], "session_id": session_id},
        metadata={
            "session_id": session_id,
            "intent": "chat",
            "user_id": initial_state.get("user_id"),
        },
        session_id=session_id,
        user_id=initial_state.get("user_id"),
    ):
        try:
            graph = await _get_chat_graph()

            async for event in graph.astream(
                initial_state, config=graph_config, stream_mode="updates"
            ):
                # event 格式: {node_name: delta_dict}
                for node_name, delta in event.items():
                    if not isinstance(delta, dict):
                        continue
                    if node_name == "chat":
                        # 提取 AIMessage 内容, 流式输出
                        msgs = delta.get("messages", [])
                        for msg in msgs:
                            if isinstance(msg, AIMessage) and msg.content:
                                msg_text = (
                                    msg.content
                                    if isinstance(msg.content, str)
                                    else str(msg.content)
                                )
                                paragraphs = msg_text.split("\n\n")
                                for para in paragraphs:
                                    yield _sse_chunk({"content": para + "\n\n"})
                                    # P0-01 背压: 让出控制权, 允许消费者 (SSE writer) 刷出缓冲
                                    await asyncio.sleep(0)

        except Exception as e:
            logger.exception("对话追问流式执行失败")
            yield _sse_chunk({"content": f"\n\n**对话执行失败**: {str(e)[:200]}"})

    # SSE 末块 (finish_reason)
    yield _sse_chunk({}, finish_reason="stop")
    yield "data: [DONE]\n\n"


async def _run_chat(
    initial_state: dict[str, Any],
    graph_config: dict[str, Any],
    request: ChatCompletionRequest,
    session_id: str,
) -> ChatCompletionResponse:
    """非流式对话追问执行 (P2-Future-03).

    走 chat graph (单节点), 返回完整 AI 回答.
    AGENTS.md 第 10 章: trace_agent 根 span.
    """
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    async with trace_agent(
        name="agentinsight-researcher-chat",
        input={"query": initial_state["query"][:200], "session_id": session_id},
        metadata={
            "session_id": session_id,
            "intent": "chat",
            "user_id": initial_state.get("user_id"),
        },
        session_id=session_id,
        user_id=initial_state.get("user_id"),
    ):
        try:
            graph = await _get_chat_graph()
            final_state = await graph.ainvoke(initial_state, config=graph_config)

            # 从 messages 中提取最新 AIMessage 作为回答
            content = ""
            messages = final_state.get("messages", [])
            for msg in reversed(messages):
                if isinstance(msg, AIMessage) and msg.content:
                    content = msg.content if isinstance(msg.content, str) else str(msg.content)
                    break

            if not content:
                content = "(对话未生成响应)"

        except Exception as e:
            logger.exception("对话追问执行失败")
            content = f"对话执行失败: {str(e)[:500]}"

    prompt_tokens = len(initial_state["query"]) // 4
    completion_tokens = len(content) // 4

    return ChatCompletionResponse(
        id=completion_id,
        created=created,
        model=request.model,
        choices=[
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        usage={
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    )


# ========== 文件上传 (用户需求 8) ==========


@router.post("/files")
async def upload_file(
    file: UploadFile = File(...),  # noqa: B008 - FastAPI 标准模式
    authorization: str | None = Header(None),
) -> Any:
    """文件上传端点 (用户需求 8).

    上传文件作为研究数据源, 文件 ID 可在 /v1/chat/completions 的
    uploaded_files 字段引用.

    AGENTS.md 第 7 章: 用户私有数据按 agent_id + user_id 隔离.
    AGENTS.md 第 11 章: 安全约束 (大小/扩展名白名单).
    """
    settings = get_settings()
    user_id = get_request_user_id()
    agent_id = get_request_agent_id()

    # 校验文件大小
    contents = await file.read()
    size_mb = len(contents) / (1024 * 1024)
    if size_mb > settings.max_upload_size_mb:
        raise HTTPException(
            status_code=413,
            detail=f"文件大小 {size_mb:.2f}MB 超过限制 {settings.max_upload_size_mb}MB",
        )

    # 校验扩展名 (AGENTS.md 第 11 章: 白名单)
    ext = Path(file.filename or "").suffix.lstrip(".").lower()
    if ext not in settings.allowed_extensions_list:
        raise HTTPException(
            status_code=415,
            detail=f"不支持的文件类型: .{ext}, 允许: {', '.join(settings.allowed_extensions_list)}",
        )

    # 生成文件 ID (agent_id:user_id:uuid 三级分键)
    file_id = f"{agent_id}:{user_id}:{uuid.uuid4().hex[:16]}"

    # 存储路径 (按 agent_id + user_id 隔离)
    upload_dir = Path(settings.upload_dir) / agent_id / user_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    save_path = upload_dir / f"{file_id.split(':')[-2]}_{file_id.split(':')[-1]}.{ext}"

    # 写入文件
    save_path.write_bytes(contents)

    logger.info(
        "文件上传成功: file_id=%s, filename=%s, size=%.2fMB, user=%s",
        file_id,
        file.filename,
        size_mb,
        user_id,
    )

    return JSONResponse(
        status_code=201,
        content={
            "file_id": file_id,
            "filename": file.filename,
            "size_bytes": len(contents),
            "size_mb": round(size_mb, 4),
            "extension": ext,
            "uploaded_at": int(time.time()),
        },
    )


def _load_uploaded_files_context(file_ids: list[str], user_id: str, agent_id: str) -> list[str]:
    """加载已上传文件内容作为研究上下文.

    AGENTS.md 第 7 章: 按 agent_id + user_id 隔离, 禁止跨用户访问.
    """
    settings = get_settings()
    contexts: list[str] = []

    for file_id in file_ids:
        try:
            # 校验 file_id 前缀归属当前 agent+user (安全: 禁止跨用户)
            parts = file_id.split(":")
            if len(parts) != 3:
                continue
            fid_agent, fid_user, fid_uuid = parts
            if fid_agent != agent_id or fid_user != user_id:
                logger.warning(
                    "拒绝跨用户文件访问: file_id=%s, agent=%s, user=%s",
                    file_id,
                    agent_id,
                    user_id,
                )
                continue

            # 查找文件 (按 uuid 前缀匹配)
            upload_dir = Path(settings.upload_dir) / agent_id / user_id
            if not upload_dir.exists():
                continue
            matches = list(upload_dir.glob(f"{fid_uuid}_*"))
            if not matches:
                continue

            file_path = matches[0]
            content = _extract_file_content(file_path, file_path.suffix.lstrip(".").lower())
            if content:
                contexts.append(f"=== 用户上传文件: {file_path.name} ===\n{content[:8000]}")
        except Exception as e:  # noqa: BLE001
            logger.warning("加载文件 %s 失败: %s", file_id, e)

    return contexts


def _extract_file_content(file_path: Path, ext: str) -> str:
    """提取文件文本内容 (按扩展名路由).

    支持: pdf, docx, md, txt, html, csv (用户需求 8).
    """
    try:
        if ext in ("txt", "md", "csv"):
            return str(file_path.read_text(encoding="utf-8", errors="ignore"))

        if ext == "pdf":
            try:
                import fitz  # PyMuPDF

                doc = fitz.open(str(file_path))
                text = "\n".join(page.get_text() for page in doc)
                doc.close()
                return str(text)
            except ImportError:
                logger.warning("PyMuPDF 未安装, 跳过 PDF 文本提取")
                return ""

        if ext == "docx":
            try:
                from docx import Document

                doc = Document(str(file_path))
                return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            except ImportError:
                logger.warning("python-docx 未安装, 跳过 DOCX 文本提取")
                return ""

        if ext == "html":
            try:
                from bs4 import BeautifulSoup

                html = file_path.read_text(encoding="utf-8", errors="ignore")
                soup = BeautifulSoup(html, "html.parser")
                return str(soup.get_text(separator="\n", strip=True))
            except ImportError:
                return str(file_path.read_text(encoding="utf-8", errors="ignore"))

        if ext == "xlsx":
            try:
                from openpyxl import load_workbook

                wb = load_workbook(str(file_path), read_only=True)
                text_parts: list[str] = []
                for ws in wb.worksheets:
                    for row in ws.iter_rows(values_only=True):
                        text_parts.append(",".join(str(c) if c is not None else "" for c in row))
                wb.close()
                return "\n".join(text_parts)
            except ImportError:
                logger.warning("openpyxl 未安装, 跳过 XLSX 文本提取")
                return ""

        if ext == "pptx":
            try:
                from pptx import Presentation

                prs = Presentation(str(file_path))
                pptx_parts: list[str] = []
                for slide in prs.slides:
                    for shape in slide.shapes:
                        if hasattr(shape, "text") and shape.text:
                            pptx_parts.append(shape.text)
                return "\n".join(pptx_parts)
            except ImportError:
                logger.warning("python-pptx 未安装, 跳过 PPTX 文本提取")
                return ""

    except Exception as e:  # noqa: BLE001
        logger.warning("提取文件 %s 内容失败: %s", file_path.name, e)

    return ""


@router.get("/models")
async def list_models() -> Any:
    """OpenAI 兼容 /v1/models 端点."""
    return {
        "object": "list",
        "data": [
            {
                "id": "agentinsight-researcher",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "agentinsight",
            }
        ],
    }


# ========== 人在回路反馈 (P0-Future-03 Human-in-the-loop) ==========


class FeedbackRequest(BaseModel):
    """人在回路反馈请求 (P0-Future-03).

    AGENTS.md 第 14 章: /v1/feedback 为允许调用的端点 (人在回路反馈通道).
    用户通过此端点提交对研究计划/大纲的审核反馈, 解决 HumanAgent 等待的 Future.
    """

    session_id: str = Field(..., description="会话 ID (thread_id), 与研究请求一致")
    feedback: str = Field(
        ...,
        description=(
            "审核反馈内容; 空字符串或 approve/accept/通过 等关键词表示接受, "
            "其他内容视为修订意见 (回 agent_creator 重新生成角色)."
        ),
    )


@router.post("/feedback")
async def submit_feedback(request: FeedbackRequest) -> Any:
    """提交人在回路审核反馈 (P0-Future-03).

    AGENTS.md 第 14 章: /v1/feedback 为允许调用的端点.
    HumanAgent 在 human 节点通过 FeedbackQueue.wait_feedback() 阻塞等待,
    此端点调用 FeedbackQueue.put_feedback() 提交反馈, 解决等待.

    Returns:
        200: 反馈已提交
        404: 无待处理的反馈请求 (HumanAgent 未在等待)
    """
    from src.api.feedback_queue import get_feedback_queue

    feedback_queue = get_feedback_queue()
    ok = feedback_queue.put_feedback(request.session_id, request.feedback)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="无待处理的反馈请求 (session_id 可能无效或反馈已提交)",
        )
    return JSONResponse(
        status_code=200,
        content={
            "session_id": request.session_id,
            "submitted": True,
            "submitted_at": int(time.time()),
        },
    )
