"""Token 预算分配器与成本归因 (P1-02).

对标 GPTR add_costs() 按步骤归因成本的设计, 升级为:
- 并发安全 (asyncio.Lock)
- 节点级预算上限 (BudgetExceededError)
- 模型级成本拆分 (LLM + Embedding)
- US 区域倍率 (对标 GPTR 1.1x)

AGENTS.md 第 5 章: max_iterations 为硬上限, 由节点计数器 + 条件边强制.
P1-02 在此基础上增加 token 预算硬上限, 避免单节点超支导致整体失败.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """预算超支异常 (P1-02).

    当某节点累计 token 超过分配的预算时抛出, 由上层捕获降级处理.
    """

    def __init__(self, node: str, used: int, budget: int) -> None:
        self.node = node
        self.used = used
        self.budget = budget
        super().__init__(f"节点 {node} token 预算超支: 已用 {used} > 预算 {budget}")


@dataclass
class StepCost:
    """单步骤成本记录 (对标 GPTR step_costs 字典的 value)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    call_count: int = 0
    cost_usd: float = 0.0
    # 模型级拆分 (升级点: GPTR 无)
    model_breakdown: dict[str, dict[str, int]] = field(default_factory=dict)

    def add(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        model: str,
        cost_usd: float = 0.0,
    ) -> None:
        """累加一次调用."""
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_tokens += prompt_tokens + completion_tokens
        self.call_count += 1
        self.cost_usd += cost_usd
        # 模型级拆分
        if model not in self.model_breakdown:
            self.model_breakdown[model] = {
                "prompt": 0,
                "completion": 0,
                "calls": 0,
            }
        self.model_breakdown[model]["prompt"] += prompt_tokens
        self.model_breakdown[model]["completion"] += completion_tokens
        self.model_breakdown[model]["calls"] += 1


class TokenBudgetAllocator:
    """节点级 token 预算分配器 (P1-02).

    根据 settings.max_total_tokens 按比例分配给各节点:
    - planner: 10%
    - researcher (子查询): 20%
    - writer: 50%
    - reviewer: 10%
    - reviser: 10%

    对标 GPTR add_costs() 的分步归因, 升级为预算上限管控.
    """

    # 节点预算比例 (对标 GPTR step_costs 的步骤定义, 升级为比例分配)
    NODE_RATIOS: dict[str, float] = {
        "planner": 0.10,
        "researcher": 0.20,
        "writer": 0.50,
        "reviewer": 0.10,
        "reviser": 0.10,
        # 默认/未知节点
        "_default": 0.05,
    }

    # US 区域倍率 (对标 GPTR costs.py 的 1.1x)
    US_REGION_MULTIPLIER: float = 1.1

    def __init__(self, total_budget: int) -> None:
        """初始化.

        Args:
            total_budget: 总 token 预算 (通常 = settings.max_total_tokens).
        """
        self.total_budget = total_budget
        self._lock = asyncio.Lock()
        # 步骤成本归因 (对标 GPTR step_costs, 升级为 StepCost 对象)
        self._step_costs: dict[str, StepCost] = {}
        # 节点预算上限 (按比例分配)
        self._node_budgets: dict[str, int] = {
            node: int(total_budget * ratio)
            for node, ratio in self.NODE_RATIOS.items()
            if node != "_default"
        }

    def allocate(self, node: str) -> int:
        """返回该节点的 token 预算上限.

        Args:
            node: 节点名 (planner/researcher/writer/reviewer/reviser).

        Returns:
            预算上限 (token 数). 未知节点返回 _default 比例.
        """
        ratio = self.NODE_RATIOS.get(node, self.NODE_RATIOS["_default"])
        return int(self.total_budget * ratio)

    async def add_cost(
        self,
        node: str,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        model: str,
        cost_usd: float = 0.0,
        check_budget: bool = True,
    ) -> None:
        """累加一次调用的成本到指定节点 (对标 GPTR add_costs).

        AGENTS.md 第 10 章: 成本归因通过 trace span 自动传播, 不需手动传递.

        Args:
            node: 节点名.
            prompt_tokens: 输入 token 数.
            completion_tokens: 输出 token 数.
            model: 模型名 (如 "deepseek/deepseek-chat").
            cost_usd: 本次调用成本 (USD).
            check_budget: 是否检查预算上限 (默认 True, 超支抛 BudgetExceededError).

        Raises:
            BudgetExceededError: 累计 token 超过节点预算.
        """
        async with self._lock:
            if node not in self._step_costs:
                self._step_costs[node] = StepCost()
            self._step_costs[node].add(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                model=model,
                cost_usd=cost_usd,
            )

            used = self._step_costs[node].total_tokens
            if check_budget:
                budget = self._node_budgets.get(node, self.allocate("_default"))
                if used > budget:
                    logger.warning(
                        "节点 %s token 预算超支: 已用 %d > 预算 %d (model=%s)",
                        node,
                        used,
                        budget,
                        model,
                    )
                    raise BudgetExceededError(node, used, budget)

    async def get_step_costs(self) -> dict[str, dict[str, Any]]:
        """返回所有步骤的成本快照 (对标 GPTR step_costs 属性).

        Returns:
            {node: {prompt_tokens, completion_tokens, total_tokens, call_count,
                    cost_usd, model_breakdown}} 字典.
        """
        async with self._lock:
            return {
                node: {
                    "prompt_tokens": sc.prompt_tokens,
                    "completion_tokens": sc.completion_tokens,
                    "total_tokens": sc.total_tokens,
                    "call_count": sc.call_count,
                    "cost_usd": sc.cost_usd,
                    "model_breakdown": dict(sc.model_breakdown),
                }
                for node, sc in self._step_costs.items()
            }

    async def get_total_cost(self) -> dict[str, Any]:
        """返回总成本汇总 (对标 GPTR get_total_cost)."""
        async with self._lock:
            total_prompt = sum(sc.prompt_tokens for sc in self._step_costs.values())
            total_completion = sum(sc.completion_tokens for sc in self._step_costs.values())
            total_tokens = total_prompt + total_completion
            total_cost = sum(sc.cost_usd for sc in self._step_costs.values())
            return {
                "total_prompt_tokens": total_prompt,
                "total_completion_tokens": total_completion,
                "total_tokens": total_tokens,
                "total_cost_usd": total_cost,
                "step_count": len(self._step_costs),
            }


# ========== 全局单例 ==========
_allocator: TokenBudgetAllocator | None = None
_allocator_lock = asyncio.Lock()


async def get_token_budget_allocator() -> TokenBudgetAllocator:
    """获取全局 TokenBudgetAllocator 单例.

    从 settings.max_total_tokens 读取总预算.
    """
    global _allocator
    if _allocator is None:
        async with _allocator_lock:
            if _allocator is None:
                from src.config.settings import get_settings

                settings = get_settings()
                _allocator = TokenBudgetAllocator(settings.max_total_tokens)
    return _allocator



