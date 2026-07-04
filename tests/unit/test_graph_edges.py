"""单元测试: LangGraph 条件边守卫工厂.

验证 src/graph/edges.py 的三个守卫工厂函数:
- create_iteration_guard: 迭代上限守卫 (iteration_count >= max → publisher)
- create_revision_guard: 修订循环守卫 (revision_count >= max → accept)
- create_fact_check_guard: 事实核查守卫 (iteration_count >= max → accept)

AGENTS.md 第 5 章: max_iterations 为硬上限, 达到上限时强制终止.
AGENTS.md 第 13 章: 单元测试不依赖外部服务.
"""

from __future__ import annotations

import pytest

from src.graph.edges import (
    create_fact_check_guard,
    create_iteration_guard,
    create_revision_guard,
)
from src.graph.state import ResearcherState

pytestmark = pytest.mark.unit


class TestCreateIterationGuard:
    """create_iteration_guard 测试: 迭代上限守卫."""

    def test_below_max_returns_continue(self) -> None:
        """iteration_count < max → continue."""
        guard = create_iteration_guard(max_iterations=10)
        state: ResearcherState = {"iteration_count": 5}
        assert guard(state) == "continue"

    def test_reaches_max_returns_publisher(self) -> None:
        """iteration_count == max → publisher (边界)."""
        guard = create_iteration_guard(max_iterations=10)
        state: ResearcherState = {"iteration_count": 10}
        assert guard(state) == "publisher"

    def test_exceeds_max_returns_publisher(self) -> None:
        """iteration_count > max → publisher."""
        guard = create_iteration_guard(max_iterations=10)
        state: ResearcherState = {"iteration_count": 15}
        assert guard(state) == "publisher"

    def test_missing_iteration_count_defaults_to_zero(self) -> None:
        """state 无 iteration_count 字段 → 默认 0 → continue."""
        guard = create_iteration_guard(max_iterations=10)
        state: ResearcherState = {}
        assert guard(state) == "continue"

    def test_max_zero_with_zero_count_returns_publisher(self) -> None:
        """max=0, iteration_count=0: 0 >= 0 → publisher (边界)."""
        guard = create_iteration_guard(max_iterations=0)
        state: ResearcherState = {"iteration_count": 0}
        assert guard(state) == "publisher"

    def test_max_zero_missing_field_returns_publisher(self) -> None:
        """max=0 且 state 无字段: 默认 0 >= 0 → publisher (边界)."""
        guard = create_iteration_guard(max_iterations=0)
        state: ResearcherState = {}
        assert guard(state) == "publisher"

    def test_default_max_iterations_is_ten(self) -> None:
        """默认 max_iterations=10."""
        guard = create_iteration_guard()
        assert guard({"iteration_count": 9}) == "continue"
        assert guard({"iteration_count": 10}) == "publisher"


class TestCreateRevisionGuard:
    """create_revision_guard 测试: 修订循环守卫."""

    def test_accept_decision_returns_accept(self) -> None:
        """review_decision=accept → accept."""
        guard = create_revision_guard(max_revisions=3)
        state: ResearcherState = {"review_decision": "accept"}
        assert guard(state) == "accept"

    def test_revise_below_max_returns_revise(self) -> None:
        """review_decision=revise, revision_count < max → revise."""
        guard = create_revision_guard(max_revisions=3)
        state: ResearcherState = {"review_decision": "revise", "revision_count": 2}
        assert guard(state) == "revise"

    def test_revise_reaches_max_returns_accept(self) -> None:
        """review_decision=revise, revision_count == max → accept (守卫强制终止)."""
        guard = create_revision_guard(max_revisions=3)
        state: ResearcherState = {"review_decision": "revise", "revision_count": 3}
        assert guard(state) == "accept"

    def test_revise_exceeds_max_returns_accept(self) -> None:
        """review_decision=revise, revision_count > max → accept."""
        guard = create_revision_guard(max_revisions=3)
        state: ResearcherState = {"review_decision": "revise", "revision_count": 5}
        assert guard(state) == "accept"

    def test_missing_review_decision_defaults_to_accept(self) -> None:
        """state 无 review_decision → 默认 accept → accept."""
        guard = create_revision_guard(max_revisions=3)
        state: ResearcherState = {}
        assert guard(state) == "accept"

    def test_missing_revision_count_defaults_to_zero(self) -> None:
        """review_decision=revise, 无 revision_count → 默认 0 → revise."""
        guard = create_revision_guard(max_revisions=3)
        state: ResearcherState = {"review_decision": "revise"}
        assert guard(state) == "revise"

    def test_max_zero_with_revise_returns_accept(self) -> None:
        """max=0, revise, revision_count=0: 0 >= 0 → accept (边界)."""
        guard = create_revision_guard(max_revisions=0)
        state: ResearcherState = {"review_decision": "revise", "revision_count": 0}
        assert guard(state) == "accept"

    def test_default_max_revisions_is_three(self) -> None:
        """默认 max_revisions=3."""
        guard = create_revision_guard()
        assert guard({"review_decision": "revise", "revision_count": 2}) == "revise"
        assert guard({"review_decision": "revise", "revision_count": 3}) == "accept"


class TestCreateFactCheckGuard:
    """create_fact_check_guard 测试: 事实核查守卫."""

    def test_accepted_returns_accept(self) -> None:
        """fact_check_accepted=True → accept."""
        guard = create_fact_check_guard(max_iterations=10)
        state: ResearcherState = {"fact_check_accepted": True}
        assert guard(state) == "accept"

    def test_not_accepted_below_max_returns_revise(self) -> None:
        """fact_check_accepted=False, iteration_count < max → revise."""
        guard = create_fact_check_guard(max_iterations=10)
        state: ResearcherState = {"fact_check_accepted": False, "iteration_count": 5}
        assert guard(state) == "revise"

    def test_not_accepted_reaches_max_returns_accept(self) -> None:
        """fact_check_accepted=False, iteration_count == max → accept (守卫)."""
        guard = create_fact_check_guard(max_iterations=10)
        state: ResearcherState = {"fact_check_accepted": False, "iteration_count": 10}
        assert guard(state) == "accept"

    def test_not_accepted_exceeds_max_returns_accept(self) -> None:
        """fact_check_accepted=False, iteration_count > max → accept."""
        guard = create_fact_check_guard(max_iterations=10)
        state: ResearcherState = {"fact_check_accepted": False, "iteration_count": 15}
        assert guard(state) == "accept"

    def test_missing_fact_check_accepted_defaults_to_true(self) -> None:
        """state 无 fact_check_accepted → 默认 True → accept."""
        guard = create_fact_check_guard(max_iterations=10)
        state: ResearcherState = {}
        assert guard(state) == "accept"

    def test_missing_iteration_count_defaults_to_zero(self) -> None:
        """fact_check_accepted=False, 无 iteration_count → 默认 0 → revise."""
        guard = create_fact_check_guard(max_iterations=10)
        state: ResearcherState = {"fact_check_accepted": False}
        assert guard(state) == "revise"

    def test_max_zero_with_not_accepted_returns_accept(self) -> None:
        """max=0, not accepted, iteration_count=0: 0 >= 0 → accept (边界)."""
        guard = create_fact_check_guard(max_iterations=0)
        state: ResearcherState = {"fact_check_accepted": False, "iteration_count": 0}
        assert guard(state) == "accept"

    def test_default_max_iterations_is_ten(self) -> None:
        """默认 max_iterations=10."""
        guard = create_fact_check_guard()
        assert guard({"fact_check_accepted": False, "iteration_count": 9}) == "revise"
        assert guard({"fact_check_accepted": False, "iteration_count": 10}) == "accept"
