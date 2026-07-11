"""单元测试: LangGraph 图构建器路由函数.

验证 src/graph/builder.py 的 _route_after_agent_creator 路由逻辑:
- research_mode == "deep" → "deep_research" (递归深度研究)
- 其他值/缺失 → "research_conductor" (常规并行检索)

路由必须显式 add_conditional_edges, 禁止隐式跳转.
单元测试不依赖外部服务.
"""

from __future__ import annotations

import pytest

from src.graph.builder import _route_after_agent_creator
from src.graph.state import ResearcherState

pytestmark = pytest.mark.unit


class TestRouteAfterAgentCreator:
    """_route_after_agent_creator 路由函数测试."""

    def test_deep_mode_routes_to_deep_research(self) -> None:
        """research_mode=deep → deep_research."""
        state: ResearcherState = {"research_mode": "deep"}
        assert _route_after_agent_creator(state) == "deep_research"

    def test_basic_mode_routes_to_research_conductor(self) -> None:
        """research_mode=basic → research_conductor."""
        state: ResearcherState = {"research_mode": "basic"}
        assert _route_after_agent_creator(state) == "research_conductor"

    def test_detailed_mode_routes_to_research_conductor(self) -> None:
        """research_mode=detailed → research_conductor (非 deep 均走常规)."""
        state: ResearcherState = {"research_mode": "detailed"}
        assert _route_after_agent_creator(state) == "research_conductor"

    def test_quick_mode_routes_to_research_conductor(self) -> None:
        """research_mode=quick → research_conductor."""
        state: ResearcherState = {"research_mode": "quick"}
        assert _route_after_agent_creator(state) == "research_conductor"

    def test_summary_mode_routes_to_research_conductor(self) -> None:
        """research_mode=summary → research_conductor."""
        state: ResearcherState = {"research_mode": "summary"}
        assert _route_after_agent_creator(state) == "research_conductor"

    def test_sources_mode_routes_to_research_conductor(self) -> None:
        """research_mode=sources → research_conductor."""
        state: ResearcherState = {"research_mode": "sources"}
        assert _route_after_agent_creator(state) == "research_conductor"

    def test_subtopics_mode_routes_to_research_conductor(self) -> None:
        """research_mode=subtopics → research_conductor (非 deep)."""
        state: ResearcherState = {"research_mode": "subtopics"}
        assert _route_after_agent_creator(state) == "research_conductor"

    def test_missing_research_mode_defaults_to_research_conductor(self) -> None:
        """state 无 research_mode 字段 → 默认 research_conductor."""
        state: ResearcherState = {}
        assert _route_after_agent_creator(state) == "research_conductor"

    def test_empty_string_research_mode(self) -> None:
        """research_mode="" → research_conductor (空字符串非 deep)."""
        state: ResearcherState = {"research_mode": ""}
        assert _route_after_agent_creator(state) == "research_conductor"

    def test_case_sensitive_deep_match(self) -> None:
        """research_mode=Deep (大写) → research_conductor (大小写敏感)."""
        state: ResearcherState = {"research_mode": "Deep"}
        assert _route_after_agent_creator(state) == "research_conductor"
