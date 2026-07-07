"""单元测试: Reviser 报告修订 Agent.

验证 src/agents/researcher/reviser.py:
- revise(): 根据 review_feedback 修订报告, 返回新的 report_md
- LLM 返回空内容时保留原报告 (不返回空报告)
- 无反馈时返回原报告 (不修订)
- revise() 返回修订后报告, revision_count 累加由 reviser_node 节点包装负责
  (见 test_graph_nodes.py::test_reviser_node_increments_count)

AGENTS.md 第 13 章: 单元测试不依赖外部服务 (LLM 全部 mock).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.researcher.reviser import Reviser
from src.config.settings import Settings
from src.graph.state import ResearcherState
from src.llm.client import LLMResponse

pytestmark = pytest.mark.unit


@pytest.fixture()
def settings() -> Settings:
    """构造最小 Settings (跳过 .env 加载)."""
    return Settings(_env_file=None)


@pytest.fixture()
def mock_llm() -> MagicMock:
    """Mock LLMClient, 默认 achat 返回修订后报告."""
    llm = MagicMock()
    llm.achat = AsyncMock(
        return_value=LLMResponse(
            content="# 修订后报告\n\n已补充数据来源, 修正幻觉问题.",
            model="test-model",
        )
    )
    return llm


@pytest.fixture()
def base_state() -> ResearcherState:
    """基础研究状态 (含原报告 + 评审反馈, 满足修订前置条件)."""
    return {
        "query": "分析新能源汽车市场",
        "session_id": "test-session",
        "user_id": "test-user",
        "agent_id": "agentinsight-researcher",
        "report_md": "# 原报告\n\n新能源汽车 2024 年销量增长 50%.",
        "review_feedback": "事实性: 4/10 — 数据无来源, 存在幻觉. 请核实 50% 增长数据.",
        "contexts": ["新能源汽车 2024 年销量增长 35% (来源: 中国汽车工业协会)"],
    }


# ========== Reviser.revise ==========


@pytest.mark.asyncio
async def test_reviser_applies_revisions(
    settings: Settings,
    mock_llm: MagicMock,
    base_state: ResearcherState,
) -> None:
    """测试 Reviser 应用 LLM 修订结果到报告.

    Mock LLM 返回修订后报告, revise() 应返回 {"report_md": 修订后内容}.
    原报告与修订后报告应不同.
    """
    revised_content = "# 修订后报告\n\n已补充数据来源, 修正幻觉问题."
    mock_llm.achat.return_value = LLMResponse(content=revised_content, model="test-model")
    reviser = Reviser(settings=settings, llm=mock_llm)

    result = await reviser.revise(
        base_state,
        user_id="test-user",
        session_id="test-session",
    )

    assert result["report_md"] == revised_content
    assert result["report_md"] != base_state["report_md"]
    # 验证 LLM 调用参数 (隔离键透传)
    mock_llm.achat.assert_awaited_once()
    _, kwargs = mock_llm.achat.call_args
    assert kwargs["user_id"] == "test-user"
    assert kwargs["session_id"] == "test-session"


@pytest.mark.asyncio
async def test_reviser_handles_llm_error(
    settings: Settings,
    mock_llm: MagicMock,
    base_state: ResearcherState,
) -> None:
    """测试 LLM 返回空内容时保留原报告不变.

    Reviser.revise 对 LLM 空输出降级: 返回原 report_md (不返回空报告,
    避免丢失内容). 这是 Reviser 的容错路径.
    """
    mock_llm.achat.return_value = LLMResponse(content="", model="test-model")
    reviser = Reviser(settings=settings, llm=mock_llm)

    result = await reviser.revise(base_state)

    # LLM 空输出 → 返回原报告
    assert result["report_md"] == base_state["report_md"]


@pytest.mark.asyncio
async def test_reviser_increments_count(
    settings: Settings,
    mock_llm: MagicMock,
    base_state: ResearcherState,
) -> None:
    """测试 Reviser.revise 返回修订后报告 (revision_count 累加由节点负责).

    Reviser.revise 自身不返回 revision_count (仅返回 {"report_md": str}).
    revision_count 累加由 reviser_node 节点包装负责 (Annotated[int, operator.add]
    reducer, 见 test_graph_nodes.py::test_reviser_node_increments_count).

    本测试验证 revise() 成功修订报告, 这是 revision_count 累加的前置条件:
    仅当 revise() 返回新报告时, 节点才会 revision_count += 1.
    """
    revised_content = "# 修订后报告\n\n已修正幻觉问题."
    mock_llm.achat.return_value = LLMResponse(content=revised_content, model="test-model")
    reviser = Reviser(settings=settings, llm=mock_llm)

    result = await reviser.revise(base_state)

    # revise() 返回修订后报告 (节点据此累加 revision_count)
    assert result["report_md"] == revised_content
    assert result["report_md"] != base_state["report_md"]
    # revise() 不返回 revision_count (由节点包装负责)
    assert "revision_count" not in result
    # LLM 被调用一次 (修订发生)
    mock_llm.achat.assert_awaited_once()


@pytest.mark.asyncio
async def test_reviser_no_feedback_returns_original(
    settings: Settings,
    mock_llm: MagicMock,
    base_state: ResearcherState,
) -> None:
    """测试无评审反馈时返回原报告 (不修订, 不调用 LLM).

    Reviser.revise 对空 feedback 短路: 返回原 report_md, 不调用 LLM.
    """
    base_state["review_feedback"] = ""
    reviser = Reviser(settings=settings, llm=mock_llm)

    result = await reviser.revise(base_state)

    assert result["report_md"] == base_state["report_md"]
    # 无反馈短路, 不调用 LLM
    mock_llm.achat.assert_not_called()
