"""单元测试: TokenBudgetAllocator (节点级 token 预算分配 + 成本归因).

验证 src/llm/token_budget.py:
- BudgetExceededError: 异常构造 + 消息格式
- StepCost.add: 累加 token + 模型级拆分
- TokenBudgetAllocator.allocate: 节点预算比例分配 (planner/researcher/writer/reviewer/reviser)
- TokenBudgetAllocator.add_cost: 累加 + 超支抛 BudgetExceededError + check_budget=False 不抛
- TokenBudgetAllocator.get_step_costs: 成本快照
- TokenBudgetAllocator.get_total_cost: 汇总
- 并发安全 (asyncio.Lock)

max_iterations 为硬上限, 在此基础上增加 token 预算硬上限.
成本归因通过 trace span 自动传播.
单元测试不依赖外部服务.

注: 本模块为 token 预算分配器, 非上下文窗口管理 (800K chars 压缩在 context_manager.py).
任务描述中 "上下文窗口阈值/压缩触发" 场景在此适配为预算阈值与 BudgetExceededError 触发.
"""

from __future__ import annotations

import asyncio

import pytest

from src.llm.token_budget import (
    BudgetExceededError,
    StepCost,
    TokenBudgetAllocator,
)

pytestmark = pytest.mark.unit


# ========== BudgetExceededError ==========


def test_budget_exceeded_error_message_contains_node_and_amounts() -> None:
    """BudgetExceededError 消息应含节点名/已用/预算."""
    err = BudgetExceededError(node="writer", used=1000, budget=800)
    assert "writer" in str(err)
    assert "1000" in str(err)
    assert "800" in str(err)


def test_budget_exceeded_error_attributes_exposed() -> None:
    """BudgetExceededError 应暴露 node/used/budget 属性供上层捕获处理."""
    err = BudgetExceededError(node="planner", used=500, budget=400)
    assert err.node == "planner"
    assert err.used == 500
    assert err.budget == 400


# ========== StepCost.add ==========


def test_step_cost_add_accumulates_tokens_and_calls() -> None:
    """StepCost.add 应累加 prompt/completion/total tokens 与 call_count."""
    sc = StepCost()
    sc.add(prompt_tokens=100, completion_tokens=50, model="deepseek/deepseek-chat")
    sc.add(prompt_tokens=200, completion_tokens=80, model="deepseek/deepseek-chat")

    assert sc.prompt_tokens == 300
    assert sc.completion_tokens == 130
    assert sc.total_tokens == 430
    assert sc.call_count == 2


def test_step_cost_add_tracks_model_breakdown() -> None:
    """StepCost.add 应按模型拆分 token 与调用次数."""
    sc = StepCost()
    sc.add(prompt_tokens=100, completion_tokens=50, model="model-a")
    sc.add(prompt_tokens=200, completion_tokens=80, model="model-b")
    sc.add(prompt_tokens=30, completion_tokens=10, model="model-a")

    assert sc.model_breakdown["model-a"]["prompt"] == 130
    assert sc.model_breakdown["model-a"]["completion"] == 60
    assert sc.model_breakdown["model-a"]["calls"] == 2
    assert sc.model_breakdown["model-b"]["prompt"] == 200
    assert sc.model_breakdown["model-b"]["calls"] == 1


def test_step_cost_add_accumulates_cost_usd() -> None:
    """StepCost.add 应累加 cost_usd."""
    sc = StepCost()
    sc.add(prompt_tokens=100, completion_tokens=50, model="m", cost_usd=0.01)
    sc.add(prompt_tokens=100, completion_tokens=50, model="m", cost_usd=0.02)

    assert sc.cost_usd == pytest.approx(0.03)


# ========== TokenBudgetAllocator.allocate ==========


def test_allocate_returns_correct_ratio_for_each_node() -> None:
    """allocate 应按 NODE_RATIOS 返回各节点预算 (planner=10%/researcher=20%/writer=50%/...)."""
    allocator = TokenBudgetAllocator(total_budget=10000)

    assert allocator.allocate("planner") == 1000  # 10%
    assert allocator.allocate("researcher") == 2000  # 20%
    assert allocator.allocate("writer") == 5000  # 50%
    assert allocator.allocate("reviewer") == 1000  # 10%
    assert allocator.allocate("reviser") == 1000  # 10%


def test_allocate_unknown_node_uses_default_ratio() -> None:
    """未知节点应使用 _default 比例 (5%)."""
    allocator = TokenBudgetAllocator(total_budget=10000)

    assert allocator.allocate("unknown_node") == 500  # 5%
    assert allocator.allocate("") == 500


def test_node_budgets_initialized_from_ratios() -> None:
    """_node_budgets 应在初始化时按比例预分配 (排除 _default)."""
    allocator = TokenBudgetAllocator(total_budget=20000)

    assert allocator._node_budgets["planner"] == 2000
    assert allocator._node_budgets["writer"] == 10000
    assert "_default" not in allocator._node_budgets


# ========== add_cost: 累加 + 超支 ==========


@pytest.mark.asyncio
async def test_add_cost_accumulates_per_node_without_exceeding() -> None:
    """未超支时累加正常, 不抛异常."""
    allocator = TokenBudgetAllocator(total_budget=10000)  # writer 预算 5000

    await allocator.add_cost(
        "writer",
        prompt_tokens=1000,
        completion_tokens=500,
        model="deepseek/deepseek-chat",
    )
    await allocator.add_cost(
        "writer",
        prompt_tokens=1000,
        completion_tokens=500,
        model="deepseek/deepseek-chat",
    )

    costs = await allocator.get_step_costs()
    assert costs["writer"]["total_tokens"] == 3000
    assert costs["writer"]["call_count"] == 2


@pytest.mark.asyncio
async def test_add_cost_raises_budget_exceeded_when_over_limit() -> None:
    """累计 token 超过节点预算 → 抛 BudgetExceededError (硬上限)."""
    allocator = TokenBudgetAllocator(total_budget=1000)  # writer 预算 500

    # 第一次: 300 token (未超)
    await allocator.add_cost(
        "writer",
        prompt_tokens=200,
        completion_tokens=100,
        model="m",
    )

    # 第二次: 再加 300 → 累计 600 > 500 → 抛异常
    with pytest.raises(BudgetExceededError) as exc_info:
        await allocator.add_cost(
            "writer",
            prompt_tokens=200,
            completion_tokens=100,
            model="m",
        )

    assert exc_info.value.node == "writer"
    assert exc_info.value.used == 600
    assert exc_info.value.budget == 500


@pytest.mark.asyncio
async def test_add_cost_check_budget_false_does_not_raise() -> None:
    """check_budget=False → 即使超支也不抛异常 (供 achat 内部仅记录场景)."""
    allocator = TokenBudgetAllocator(total_budget=100)  # writer 预算 50

    # 不抛异常 (check_budget=False)
    await allocator.add_cost(
        "writer",
        prompt_tokens=200,
        completion_tokens=100,
        model="m",
        check_budget=False,
    )

    costs = await allocator.get_step_costs()
    assert costs["writer"]["total_tokens"] == 300  # 累加成功


# ========== get_step_costs / get_total_cost ==========


@pytest.mark.asyncio
async def test_get_step_costs_returns_snapshot_with_model_breakdown() -> None:
    """get_step_costs 应返回所有节点的成本快照 (含 model_breakdown)."""
    allocator = TokenBudgetAllocator(total_budget=10000)

    await allocator.add_cost("planner", prompt_tokens=100, completion_tokens=50, model="m-a")
    await allocator.add_cost("writer", prompt_tokens=500, completion_tokens=200, model="m-b")

    costs = await allocator.get_step_costs()

    assert "planner" in costs
    assert "writer" in costs
    assert costs["planner"]["total_tokens"] == 150
    assert costs["writer"]["total_tokens"] == 700
    assert costs["writer"]["model_breakdown"]["m-b"]["calls"] == 1


@pytest.mark.asyncio
async def test_get_step_costs_empty_when_no_calls() -> None:
    """无任何 add_cost 调用时 → get_step_costs 返回空 dict."""
    allocator = TokenBudgetAllocator(total_budget=10000)
    costs = await allocator.get_step_costs()
    assert costs == {}


@pytest.mark.asyncio
async def test_get_total_cost_aggregates_all_nodes() -> None:
    """get_total_cost 应汇总所有节点的 prompt/completion/total tokens + cost."""
    allocator = TokenBudgetAllocator(total_budget=10000)

    await allocator.add_cost(
        "planner", prompt_tokens=100, completion_tokens=50, model="m", cost_usd=0.01
    )
    await allocator.add_cost(
        "writer", prompt_tokens=500, completion_tokens=200, model="m", cost_usd=0.05
    )

    total = await allocator.get_total_cost()

    assert total["total_prompt_tokens"] == 600
    assert total["total_completion_tokens"] == 250
    assert total["total_tokens"] == 850
    assert total["total_cost_usd"] == pytest.approx(0.06)
    assert total["step_count"] == 2


# ========== 并发安全 ==========


@pytest.mark.asyncio
async def test_concurrent_add_cost_is_lock_safe() -> None:
    """并发 add_cost → asyncio.Lock 保证累加线程安全 (无丢更新)."""
    allocator = TokenBudgetAllocator(total_budget=1_000_000)  # 大预算避免超支

    async def _add_once() -> None:
        await allocator.add_cost(
            "writer",
            prompt_tokens=100,
            completion_tokens=50,
            model="m",
        )

    # 并发 10 次, 每次加 150 token → 总 1500
    await asyncio.gather(*[_add_once() for _ in range(10)])

    costs = await allocator.get_step_costs()
    assert costs["writer"]["total_tokens"] == 1500
    assert costs["writer"]["call_count"] == 10
