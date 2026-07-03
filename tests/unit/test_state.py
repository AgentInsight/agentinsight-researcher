"""单元测试: ResearcherState 状态定义.

验证 TypedDict 字段与 reducer 声明.
"""

from __future__ import annotations

from src.graph.state import ResearcherState


def test_state_is_typed_dict():
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


def test_state_optional_fields():
    """测试 State 所有字段可选 (total=False)."""
    state: ResearcherState = {}  # 空字典也应合法
    assert state == {}
