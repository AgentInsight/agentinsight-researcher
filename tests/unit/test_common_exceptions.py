"""单元测试: 统一异常层级.

验证 src/common/exceptions.py:
- AgentError: 基类
  - 默认 code="agent_error", http_status=500
  - 自定义 code 和 http_status
  - message 属性 (str(self) 别名)
  - 无参数时 message=""
- LLMError: code="llm_error", http_status=503
- RetrievalError: code="retrieval_error", http_status=503
- ToolError: code="tool_error", http_status=502
- BudgetExceededError: code="budget_exceeded", http_status=429
  - 构造函数接收 node/used/budget 参数
  - node/used/budget 属性暴露
  - 消息含节点名/已用/预算
- 继承关系: 所有子类 isinstance(AgentError) == True, isinstance(Exception) == True

单元测试不依赖外部服务.
"""

from __future__ import annotations

import pytest

from src.common.exceptions import (
    AgentError,
    BudgetExceededError,
    LLMError,
    RetrievalError,
    ToolError,
)

pytestmark = pytest.mark.unit


# ========== AgentError: 基类默认值 ==========


def test_agent_error_default_code() -> None:
    """AgentError 默认 code 应为 'agent_error'."""
    err = AgentError("something went wrong")
    assert err.code == "agent_error"


def test_agent_error_default_http_status() -> None:
    """AgentError 默认 http_status 应为 500."""
    err = AgentError("something went wrong")
    assert err.http_status == 500


def test_agent_error_message_property() -> None:
    """AgentError.message 属性应等于构造时传入的消息."""
    err = AgentError("custom message")
    assert err.message == "custom message"
    assert str(err) == "custom message"


def test_agent_error_empty_message() -> None:
    """无参数构造时 → message 应为空字符串."""
    err = AgentError()
    assert err.message == ""
    assert str(err) == ""


# ========== AgentError: 自定义 code/http_status ==========


def test_agent_error_custom_code() -> None:
    """自定义 code → 覆盖默认 'agent_error'."""
    err = AgentError("msg", code="custom_code")
    assert err.code == "custom_code"


def test_agent_error_custom_http_status() -> None:
    """自定义 http_status → 覆盖默认 500."""
    err = AgentError("msg", http_status=418)
    assert err.http_status == 418


def test_agent_error_custom_code_and_http_status() -> None:
    """同时自定义 code 和 http_status."""
    err = AgentError("msg", code="not_found", http_status=404)
    assert err.code == "not_found"
    assert err.http_status == 404


# ========== AgentError: 继承关系 ==========


def test_agent_error_is_exception() -> None:
    """AgentError 应继承 Exception."""
    err = AgentError("msg")
    assert isinstance(err, Exception)


def test_agent_error_raised_and_caught() -> None:
    """AgentError 可被 raise 并被 except AgentError 捕获."""
    with pytest.raises(AgentError) as exc_info:
        raise AgentError("test error")
    assert "test error" in str(exc_info.value)


# ========== LLMError ==========


def test_llm_error_code() -> None:
    """LLMError 默认 code 应为 'llm_error'."""
    err = LLMError("model call failed")
    assert err.code == "llm_error"


def test_llm_error_http_status() -> None:
    """LLMError 默认 http_status 应为 503."""
    err = LLMError("model call failed")
    assert err.http_status == 503


def test_llm_error_inherits_agent_error() -> None:
    """LLMError 应继承 AgentError."""
    err = LLMError("msg")
    assert isinstance(err, AgentError)
    assert isinstance(err, Exception)


# ========== RetrievalError ==========


def test_retrieval_error_code_and_http_status() -> None:
    """RetrievalError code='retrieval_error', http_status=503."""
    err = RetrievalError("vector search failed")
    assert err.code == "retrieval_error"
    assert err.http_status == 503


def test_retrieval_error_inherits_agent_error() -> None:
    """RetrievalError 应继承 AgentError."""
    err = RetrievalError("msg")
    assert isinstance(err, AgentError)


# ========== ToolError ==========


def test_tool_error_code_and_http_status() -> None:
    """ToolError code='tool_error', http_status=502."""
    err = ToolError("mcp tool execution failed")
    assert err.code == "tool_error"
    assert err.http_status == 502


def test_tool_error_inherits_agent_error() -> None:
    """ToolError 应继承 AgentError."""
    err = ToolError("msg")
    assert isinstance(err, AgentError)


# ========== BudgetExceededError ==========


def test_budget_exceeded_error_code_and_http_status() -> None:
    """BudgetExceededError code='budget_exceeded', http_status=429."""
    err = BudgetExceededError(node="writer", used=1000, budget=800)
    assert err.code == "budget_exceeded"
    assert err.http_status == 429


def test_budget_exceeded_error_attributes() -> None:
    """BudgetExceededError 应暴露 node/used/budget 属性."""
    err = BudgetExceededError(node="planner", used=500, budget=400)
    assert err.node == "planner"
    assert err.used == 500
    assert err.budget == 400


def test_budget_exceeded_error_message_contains_details() -> None:
    """BudgetExceededError 消息应含节点名/已用/预算."""
    err = BudgetExceededError(node="writer", used=1000, budget=800)
    msg = str(err)
    assert "writer" in msg
    assert "1000" in msg
    assert "800" in msg


def test_budget_exceeded_error_inherits_agent_error() -> None:
    """BudgetExceededError 应继承 AgentError."""
    err = BudgetExceededError(node="writer", used=100, budget=50)
    assert isinstance(err, AgentError)
    assert isinstance(err, Exception)


def test_budget_exceeded_error_raised_and_caught_as_agent_error() -> None:
    """BudgetExceededError 可被 except AgentError 捕获 (多态)."""
    with pytest.raises(AgentError) as exc_info:
        raise BudgetExceededError(node="writer", used=200, budget=100)
    assert isinstance(exc_info.value, BudgetExceededError)


# ========== 所有子类统一继承关系校验 ==========


def test_all_subclasses_are_agent_error() -> None:
    """所有自定义异常子类 isinstance(AgentError) == True."""
    instances = [
        LLMError("msg"),
        RetrievalError("msg"),
        ToolError("msg"),
        BudgetExceededError(node="n", used=1, budget=1),
    ]
    for err in instances:
        assert isinstance(err, AgentError), f"{type(err).__name__} 未继承 AgentError"
        assert isinstance(err, Exception), f"{type(err).__name__} 未继承 Exception"
