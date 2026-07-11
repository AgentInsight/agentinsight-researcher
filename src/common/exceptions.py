"""统一异常层级.

AGENTS.md 第 4 章不推荐清单: 异常散落在多个模块, 继承树不一致.
本模块提供统一异常基类, 各模块自定义异常应继承对应层级.

使用方式:
    from src.common.exceptions import AgentError, LLMError, RetrievalError, ToolError

    raise LLMError("模型调用失败", code="llm_call_failed")

异常处理器 (server.py) 捕获 AgentError 返回结构化 JSON 错误响应.
"""

from __future__ import annotations


class AgentError(Exception):
    """Agent 系统统一异常基类.

    所有自定义异常应继承此类或其子类, 确保 server.py 全局异常处理器统一捕获.

    Attributes:
        code: 错误码 (机器可读, 如 "llm_call_failed")
        http_status: HTTP 状态码 (默认 500)
    """

    code: str = "agent_error"
    http_status: int = 500

    def __init__(
        self, message: str = "", *, code: str | None = None, http_status: int | None = None
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status

    @property
    def message(self) -> str:
        """错误消息 (str(self) 的别名, 便于序列化)."""
        return str(self.args[0]) if self.args else ""


class LLMError(AgentError):
    """LLM 调用异常 (网关层 / 模型调用失败 / 降级链耗尽)."""

    code = "llm_error"
    http_status = 503


class RetrievalError(AgentError):
    """检索异常 (RAG / 向量库 / BM25 检索失败)."""

    code = "retrieval_error"
    http_status = 503


class ToolError(AgentError):
    """工具调用异常 (MCP 工具执行失败)."""

    code = "tool_error"
    http_status = 502


class BudgetExceededError(AgentError):
    """Token 预算超支异常 (节点累计 token 超过分配预算).

    保留向后兼容: 原 src.llm.token_budget.BudgetExceededError 的别名.
    """

    code = "budget_exceeded"
    http_status = 429

    def __init__(self, node: str, used: int, budget: int) -> None:
        self.node = node
        self.used = used
        self.budget = budget
        super().__init__(f"节点 {node} token 预算超支: 已用 {used} > 预算 {budget}")
