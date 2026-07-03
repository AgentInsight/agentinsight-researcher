"""AgentInsight SDK 可观测性封装.

AGENTS.md 第 10 章硬约束:
- 统一使用 agentinsight-sdk (pip 名 agentinsight-sdk, 导入名 agentinsight, ≥0.1.5)
- 追踪调用方式唯一: 异步上下文管理器 async with trace_xxx(...) as span
- 禁用观察者模式 (无 Subject/Observer, 无 attach/notify)
- @agentinsight.observe 装饰器已弃用
- 业务代码禁止直接调用 agentinsight.init()/get_client()/client.flush()
- 业务代码禁止直接使用 opentelemetry-sdk 原生 API
- SDK 初始化失败或运行时异常时, 所有 trace_xxx yield _NoopSpan (Null Object 模式)
- 业务代码禁止判断 SDK 是否可用, span.update() 调用永远安全

对标 AgentInsightService common/tracing.py, 提供 6 类 trace span.
"""

from __future__ import annotations

import logging
import random
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from src.config.settings import get_settings

logger = logging.getLogger(__name__)

# ========== 模块导入时一次性初始化 SDK ==========
_sdk_available: bool = False

try:
    import agentinsight

    _settings = get_settings()
    agentinsight.init(
        public_key=_settings.agentinsight_public_key,
        secret_key=_settings.agentinsight_secret_key,
        base_url=_settings.agentinsight_host,
    )
    _sdk_available = True
    logger.info("AgentInsight SDK 初始化成功")
except Exception as e:  # noqa: BLE001
    logger.warning("AgentInsight SDK 初始化失败, 链路追踪将降级为无操作: %s", e)
    _sdk_available = False


# ========== _NoopSpan Null Object 降级模式 ==========
class _NoopSpan:
    """SDK 不可用时的空操作 span, 支持链式调用.

    所有方法返回 self, trace_id/id 为 None.
    span.update() 调用永远安全, 业务代码无需判断 SDK 是否可用.
    """

    def update(self, **kwargs: Any) -> _NoopSpan:
        return self

    def end(self, **kwargs: Any) -> _NoopSpan:
        return self

    def score(self, **kwargs: Any) -> _NoopSpan:
        return self

    @property
    def trace_id(self) -> str | None:
        return None

    @property
    def id(self) -> str | None:
        return None


def _get_client() -> Any:
    """获取 SDK client, 不可用时返回 None."""
    if not _sdk_available:
        return None
    try:
        return agentinsight.get_client()
    except Exception:  # noqa: BLE001
        return None


# ========== metadata 合并工具 ==========
def _build_propagate_metadata(
    user_id: str | None = None,
    session_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建 propagate_attributes 所需的元数据字典.

    将 user_id / session_id / 自定义 metadata 合并为一个字典,
    用于传递给 start_as_current_observation 的 metadata 参数.

    合并顺序: user_id → session_id → metadata (业务 metadata 可覆盖前两者).
    """
    result: dict[str, Any] = {}
    if user_id:
        result["user_id"] = user_id
    if session_id:
        result["session_id"] = session_id
    if metadata:
        result.update(metadata)
    return result


# ========== 6 类 trace_xxx 异步上下文管理器 ==========


@asynccontextmanager
async def trace_agent(
    name: str,
    *,
    input: Any | None = None,
    metadata: dict[str, Any] | None = None,
    version: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
) -> AsyncGenerator[Any, None]:
    """Agent 级根 span, 包裹 graph.ainvoke().

    as_type=agent, 必带: name/input/metadata(含 session_id/user_id)/session_id/user_id.
    AGENTS.md 第 10 章: 编排器入口建立根 span, LangGraph 节点内子 span 自动关联.
    """
    client = _get_client()
    if client is None:
        yield _NoopSpan()
        return

    merged_metadata = _build_propagate_metadata(
        user_id=user_id,
        session_id=session_id,
        metadata=metadata,
    )

    ctx = client.start_as_current_observation(
        name=name,
        as_type="agent",
        input=input,
        metadata=merged_metadata or None,
        version=version,
    )
    try:
        with ctx as span:
            yield span
    except Exception as e:  # noqa: BLE001
        logger.debug("trace_agent 异常: %s", e)
        raise


@asynccontextmanager
async def trace_generation(
    name: str,
    *,
    input: Any | None = None,
    model: str | None = None,
    model_parameters: dict[str, Any] | None = None,
    usage_details: dict[str, Any] | None = None,
    cost_details: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    version: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
) -> AsyncGenerator[Any, None]:
    """LLM 调用 span, 仅在 llm/ 网关层使用, 业务节点层不重复包裹.

    as_type=generation, 必带: name/model/model_parameters/usage_details/cost_details.
    """
    client = _get_client()
    if client is None:
        yield _NoopSpan()
        return

    merged_metadata = _build_propagate_metadata(
        user_id=user_id,
        session_id=session_id,
        metadata=metadata,
    )

    ctx = client.start_as_current_observation(
        name=name,
        as_type="generation",
        input=input,
        model=model,
        model_parameters=model_parameters,
        usage_details=usage_details,
        cost_details=cost_details,
        metadata=merged_metadata or None,
        version=version,
    )
    try:
        with ctx as span:
            yield span
    except Exception as e:  # noqa: BLE001
        logger.debug("trace_generation 异常: %s", e)
        raise


@asynccontextmanager
async def trace_tool(
    name: str,
    *,
    input: Any | None = None,
    metadata: dict[str, Any] | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
) -> AsyncGenerator[Any, None]:
    """MCP 工具调用 span.

    as_type=tool, 必带: name/input/output(span.update)/metadata(含 tool_name/success).
    output 不在创建时传入, 业务在 with 块内通过 span.update(output=...) 增量写入.
    """
    client = _get_client()
    if client is None:
        yield _NoopSpan()
        return

    merged_metadata = _build_propagate_metadata(
        user_id=user_id,
        session_id=session_id,
        metadata=metadata,
    )

    ctx = client.start_as_current_observation(
        name=name,
        as_type="tool",
        input=input,
        metadata=merged_metadata or None,
    )
    try:
        with ctx as span:
            yield span
    except Exception as e:  # noqa: BLE001
        logger.debug("trace_tool 异常: %s", e)
        raise


@asynccontextmanager
async def trace_retriever(
    name: str,
    *,
    input: Any | None = None,
    metadata: dict[str, Any] | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
) -> AsyncGenerator[Any, None]:
    """RAG 检索 span (BM25/Vector/Qdrant search).

    as_type=retriever, 必带: name/input/output/metadata(含 matched/candidate_count/retriever_type/top_score).
    """
    client = _get_client()
    if client is None:
        yield _NoopSpan()
        return

    merged_metadata = _build_propagate_metadata(
        user_id=user_id,
        session_id=session_id,
        metadata=metadata,
    )

    ctx = client.start_as_current_observation(
        name=name,
        as_type="retriever",
        input=input,
        metadata=merged_metadata or None,
    )
    try:
        with ctx as span:
            yield span
    except Exception as e:  # noqa: BLE001
        logger.debug("trace_retriever 异常: %s", e)
        raise


@asynccontextmanager
async def trace_chain(
    name: str,
    *,
    input: Any | None = None,
    metadata: dict[str, Any] | None = None,
    version: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
) -> AsyncGenerator[Any, None]:
    """多步骤链式调用 span (RAG 管道、子图编排).

    as_type=chain, 必带: name/input/output.
    """
    client = _get_client()
    if client is None:
        yield _NoopSpan()
        return

    merged_metadata = _build_propagate_metadata(
        user_id=user_id,
        session_id=session_id,
        metadata=metadata,
    )

    ctx = client.start_as_current_observation(
        name=name,
        as_type="chain",
        input=input,
        metadata=merged_metadata or None,
        version=version,
    )
    try:
        with ctx as span:
            yield span
    except Exception as e:  # noqa: BLE001
        logger.debug("trace_chain 异常: %s", e)
        raise


@asynccontextmanager
async def trace_embedding(
    name: str,
    *,
    input: Any | None = None,
    model: str | None = None,
    usage_details: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
) -> AsyncGenerator[Any, None]:
    """Embedding 调用 span (高频, head-based 采样).

    as_type=embedding, 必带: name/model/usage_details(含 token_count).
    AGENTS.md 第 10 章: head-based 采样, 默认 tracing_embedding_sample_rate=0.5.
    """
    client = _get_client()
    if client is None:
        yield _NoopSpan()
        return

    # head-based 采样: 高频 embed 调用按配置降采样, 减少存储压力
    try:
        sample_rate = float(get_settings().tracing_embedding_sample_rate)
    except Exception:  # noqa: BLE001
        sample_rate = 0.5
    if sample_rate < 1.0 and random.random() > sample_rate:
        yield _NoopSpan()
        return

    merged_metadata = _build_propagate_metadata(
        user_id=user_id,
        session_id=session_id,
        metadata=metadata,
    )

    ctx = client.start_as_current_observation(
        name=name,
        as_type="embedding",
        input=input,
        model=model,
        usage_details=usage_details,
        metadata=merged_metadata or None,
    )
    try:
        with ctx as span:
            yield span
    except Exception as e:  # noqa: BLE001
        logger.debug("trace_embedding 异常: %s", e)
        raise


__all__ = [
    "trace_agent",
    "trace_generation",
    "trace_tool",
    "trace_retriever",
    "trace_chain",
    "trace_embedding",
]
