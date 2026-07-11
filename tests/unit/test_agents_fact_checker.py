"""单元测试: FactChecker 事实核查 Agent.

验证 src/agents/researcher/fact_checker.py:
- check(): LLM 核查报告事实, 返回 fact_check_accepted/fact_check_issues
- LLM 判定准确 → accepted=True; 发现问题 → accepted=False + issues
- LLM 返回非法 JSON 时 safe_json_parse 兜底为 accepted=True (不阻断流程)
- 空报告短路返回 accepted=False (不调用 LLM)

AGENTS.md 第 13 章: 单元测试不依赖外部服务 (LLM 全部 mock).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.researcher.fact_checker import FactChecker
from src.config.settings import Settings
from src.graph.state import ResearcherState
from src.llm.client import LLMResponse

pytestmark = pytest.mark.unit


@pytest.fixture()
def settings() -> Settings:
    """构造最小 Settings (跳过 .env 加载, 使用默认 fact_check_enabled=True)."""
    return Settings(_env_file=None)


@pytest.fixture()
def mock_llm() -> MagicMock:
    """Mock LLMClient, 默认 achat 返回 accepted=true."""
    llm = MagicMock()
    llm.achat = AsyncMock(
        return_value=LLMResponse(
            content='{"accepted": true, "issues": []}',
            model="test-model",
        )
    )
    return llm


@pytest.fixture()
def base_state() -> ResearcherState:
    """基础研究状态 (含报告 + 上下文, 满足 LLM 调用前置条件)."""
    return {
        "query": "分析新能源汽车市场",
        "session_id": "test-session",
        "user_id": "test-user",
        "agent_id": "agentinsight-researcher",
        "report_md": "# 报告\n\n新能源汽车 2024 年销量增长 35%.",
        "contexts": ["新能源汽车 2024 年销量增长 35% (来源: 中国汽车工业协会)"],
    }


# ========== FactChecker.check ==========


@pytest.mark.asyncio
async def test_fact_checker_evaluate_accepts(
    settings: Settings,
    mock_llm: MagicMock,
    base_state: ResearcherState,
) -> None:
    """测试 LLM 判定事实准确时返回 accepted=True.

    Mock LLM 返回 {"accepted": true, "issues": []},
    check() 应返回 fact_check_accepted=True, fact_check_issues=[].
    """
    mock_llm.achat.return_value = LLMResponse(
        content='{"accepted": true, "issues": []}',
        model="test-model",
    )
    checker = FactChecker(settings=settings, llm=mock_llm)

    result = await checker.check(
        base_state,
        user_id="test-user",
        session_id="test-session",
    )

    assert result["fact_check_accepted"] is True
    assert result["fact_check_issues"] == []
    # 验证 LLM 调用参数 (隔离键透传)
    mock_llm.achat.assert_awaited_once()
    _, kwargs = mock_llm.achat.call_args
    assert kwargs["user_id"] == "test-user"
    assert kwargs["session_id"] == "test-session"


@pytest.mark.asyncio
async def test_fact_checker_evaluate_rejects(
    settings: Settings,
    mock_llm: MagicMock,
    base_state: ResearcherState,
) -> None:
    """测试 LLM 发现问题时返回 accepted=False + issues 列表.

    Mock LLM 返回 {"accepted": false, "issues": ["报告称增长 50%, 上下文无此数据"]},
    check() 应返回 fact_check_accepted=False, fact_check_issues 非空.
    """
    mock_llm.achat.return_value = LLMResponse(
        content='{"accepted": false, "issues": ["报告称增长 50%, 上下文无此数据"]}',
        model="test-model",
    )
    checker = FactChecker(settings=settings, llm=mock_llm)

    result = await checker.check(base_state)

    assert result["fact_check_accepted"] is False
    assert len(result["fact_check_issues"]) == 1
    assert "增长 50%" in result["fact_check_issues"][0]


@pytest.mark.asyncio
async def test_fact_checker_handles_llm_error(
    settings: Settings,
    mock_llm: MagicMock,
    base_state: ResearcherState,
) -> None:
    """测试 LLM 返回非法 JSON 时 safe_json_parse 兜底为 accepted=True.

    FactChecker.check 用 safe_json_parse(fallback={"accepted": True, "issues": []}),
    LLM 输出非 JSON 时降级为通过 (不阻断研究流程, 设计参考: fact_checker 容错).
    """
    mock_llm.achat.return_value = LLMResponse(
        content="这不是合法 JSON",
        model="test-model",
    )
    checker = FactChecker(settings=settings, llm=mock_llm)

    result = await checker.check(base_state)

    # 兜底: accepted=True, issues=[]
    assert result["fact_check_accepted"] is True
    assert result["fact_check_issues"] == []


@pytest.mark.asyncio
async def test_fact_checker_empty_report(
    settings: Settings,
    mock_llm: MagicMock,
    base_state: ResearcherState,
) -> None:
    """测试空报告短路返回 accepted=False + 错误提示.

    FactChecker.check 对空 report_md 直接返回 accepted=False,
    fact_check_issues=["报告内容为空"], 不调用 LLM (短路路径).
    """
    base_state["report_md"] = ""
    checker = FactChecker(settings=settings, llm=mock_llm)

    result = await checker.check(base_state)

    assert result["fact_check_accepted"] is False
    assert "报告内容为空" in result["fact_check_issues"]
    # 空报告短路, 不调用 LLM
    mock_llm.achat.assert_not_called()
