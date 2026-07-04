"""单元测试: ResearcherState 状态定义.

验证 TypedDict 字段与 reducer 声明, 以及 reducer 行为:
- messages 字段 add_messages reducer (消息追加)
- iteration_count / revisions_count 的 operator.add reducer
- 从 Annotated 声明提取 reducer 元数据 (typing.get_type_hints)
"""

from __future__ import annotations

import operator
from typing import cast, get_args, get_type_hints

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph.message import add_messages

from src.graph.state import ResearcherState

pytestmark = pytest.mark.unit


def test_state_is_typed_dict() -> None:
    """测试 State 是 TypedDict."""
    # TypedDict 在运行时是 dict 的子类
    state: ResearcherState = {
        "query": "研究中国新能源汽车行业",
        "session_id": "test-session",
        "agent_id": "agentinsight-researcher",
        "user_id": "test-user",
        "report_type": "basic_report",
        "report_format": "markdown",
    }
    assert state["query"] == "研究中国新能源汽车行业"
    assert state["report_type"] == "basic_report"


def test_state_optional_fields() -> None:
    """测试 State 所有字段可选 (total=False)."""
    state: ResearcherState = {}  # 空字典也应合法
    assert state == {}


# ========== Reducer 行为测试 ==========


def test_messages_reducer_appends_messages() -> None:
    """测试 messages 字段的 add_messages reducer 追加消息."""
    left: list[BaseMessage] = [HumanMessage(content="hello")]
    right: list[BaseMessage] = [AIMessage(content="hi there")]
    result = cast(list[BaseMessage], add_messages(left, right))  # type: ignore[arg-type]
    assert len(result) == 2
    assert result[0].content == "hello"
    assert result[1].content == "hi there"


def test_messages_reducer_appends_multiple() -> None:
    """测试 add_messages 一次追加多条消息."""
    existing: list[BaseMessage] = [HumanMessage(content="msg1")]
    new: list[BaseMessage] = [HumanMessage(content="msg2"), HumanMessage(content="msg3")]
    result = cast(list[BaseMessage], add_messages(existing, new))  # type: ignore[arg-type]
    assert len(result) == 3
    assert result[0].content == "msg1"
    assert result[1].content == "msg2"
    assert result[2].content == "msg3"


def test_messages_reducer_empty_list() -> None:
    """测试 add_messages 与空列表合并 (保持原列表)."""
    existing: list[BaseMessage] = [HumanMessage(content="msg1")]
    result = cast(list[BaseMessage], add_messages(existing, []))  # type: ignore[arg-type]
    assert len(result) == 1
    assert result[0].content == "msg1"


def test_messages_reducer_empty_to_empty() -> None:
    """测试 add_messages 两个空列表合并."""
    result = add_messages([], [])
    assert result == []


def test_state_reducer_metadata_extracted() -> None:
    """测试从 Annotated 声明提取 reducer (typing.get_type_hints include_extras).

    ResearcherState 是 TypedDict, reducer 在 Annotated[T, reducer] 声明中,
    需用 get_type_hints(..., include_extras=True) 保留 Annotated 元数据.
    """
    hints = get_type_hints(ResearcherState, include_extras=True)

    # messages: Annotated[list[BaseMessage], add_messages]
    msg_args = get_args(hints["messages"])
    assert len(msg_args) == 2
    assert msg_args[1] is add_messages

    # iteration_count: Annotated[int, operator.add]
    iter_args = get_args(hints["iteration_count"])
    assert len(iter_args) == 2
    assert iter_args[1] is operator.add

    # revisions_count: Annotated[int, operator.add]
    rev_args = get_args(hints["revisions_count"])
    assert len(rev_args) == 2
    assert rev_args[1] is operator.add

    # revision_count: Annotated[int, operator.add] (Reviewer 修订循环)
    rev_cnt_args = get_args(hints["revision_count"])
    assert len(rev_cnt_args) == 2
    assert rev_cnt_args[1] is operator.add


def test_state_non_reducer_fields_no_annotated() -> None:
    """测试非 reducer 字段 (如 query) 不带 Annotated 元数据."""
    hints = get_type_hints(ResearcherState, include_extras=True)
    # query: str (无 Annotated)
    query_args = get_args(hints["query"])
    # str 类型 get_args 返回空元组 (非 Annotated)
    assert query_args == ()


def test_iteration_count_reducer_simulation() -> None:
    """测试模拟节点返回 delta 后 operator.add 合并 iteration_count.

    LangGraph 节点返回 {"iteration_count": 1}, reducer 用 operator.add 合并到 state.
    """
    current_state_count = 5
    node_delta = 1
    merged = operator.add(current_state_count, node_delta)
    assert merged == 6


def test_revisions_count_reducer_simulation() -> None:
    """测试模拟节点返回 delta 后 operator.add 合并 revisions_count."""
    current_state_count = 2
    node_delta = 1
    merged = operator.add(current_state_count, node_delta)
    assert merged == 3


def test_operator_add_zero_identity() -> None:
    """测试 operator.add 的零元特性 (0 为单位元)."""
    assert operator.add(5, 0) == 5
    assert operator.add(0, 5) == 5
