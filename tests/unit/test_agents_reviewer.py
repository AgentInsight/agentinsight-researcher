"""单元测试: Reviewer 报告评审 Agent.

验证 src/agents/researcher/reviewer.py:
- review(): LLM 多维度评分 (factual/structural/language/completeness), 返回 accept|revise
- 全维度 >=6 → accept; 任一维度 <6 → revise
- LLM 返回非法 JSON 时 _fallback_scores 兜底 (全 6 分 → accept)
- 评分缓存命中时跳过 LLM 调用 (跨实例共享, TTL 30 分钟)

单元测试不依赖外部服务 (LLM 全部 mock).
Reviewer 不强制 max_iterations (由图级守卫负责),
  Reviewer 自身的"强制"机制是评分缓存 (避免重复评审).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.researcher import reviewer as reviewer_module
from src.agents.researcher.reviewer import Reviewer
from src.config.settings import Settings
from src.graph.state import ResearcherState
from src.llm.client import LLMResponse

pytestmark = pytest.mark.unit


@pytest.fixture()
def settings() -> Settings:
    """构造最小 Settings (跳过 .env 加载)."""
    return Settings(_env_file=None)


@pytest.fixture(autouse=True)
def clear_review_cache() -> None:
    """每个用例前清空模块级 _REVIEW_CACHE, 避免跨用例污染."""
    reviewer_module._REVIEW_CACHE.clear()


@pytest.fixture()
def mock_llm() -> MagicMock:
    """Mock LLMClient, 默认 achat 返回全维度高分 (accept)."""
    llm = MagicMock()
    llm.achat = AsyncMock(
        return_value=LLMResponse(
            content=(
                '{"factual": {"score": 8, "issues": []}, '
                '"structural": {"score": 7, "issues": []}, '
                '"language": {"score": 9, "issues": []}, '
                '"completeness": {"score": 8, "issues": []}, '
                '"overall_decision": "accept", '
                '"revision_instructions": ""}'
            ),
            model="test-model",
        )
    )
    return llm


@pytest.fixture()
def base_state() -> ResearcherState:
    """基础研究状态 (含 report_md, 满足评审前置条件)."""
    return {
        "query": "分析新能源汽车市场",
        "session_id": "test-session",
        "user_id": "test-user",
        "agent_id": "agentinsight-researcher",
        "report_md": "# 新能源汽车市场报告\n\n2024 年销量增长 35%.",
        "contexts": ["新能源汽车 2024 年销量增长 35%"],
    }


# ========== Reviewer.review ==========


@pytest.mark.asyncio
async def test_reviewer_accepts_report(
    settings: Settings,
    mock_llm: MagicMock,
    base_state: ResearcherState,
) -> None:
    """测试 LLM 全维度 >=6 时返回 decision="accept".

    Mock LLM 返回 4 维度均 >=6 分, review() 应返回 review_decision="accept".
    """
    reviewer = Reviewer(settings=settings, llm=mock_llm)

    result = await reviewer.review(
        base_state,
        user_id="test-user",
        session_id="test-session",
    )

    assert result["review_decision"] == "accept"
    assert "review_feedback" in result
    assert "review_scores" in result
    # 验证 LLM 调用参数
    mock_llm.achat.assert_awaited_once()
    _, kwargs = mock_llm.achat.call_args
    assert kwargs["user_id"] == "test-user"
    assert kwargs["session_id"] == "test-session"


@pytest.mark.asyncio
async def test_reviewer_requests_revision(
    settings: Settings,
    mock_llm: MagicMock,
    base_state: ResearcherState,
) -> None:
    """测试 LLM 任一维度 <6 时返回 decision="revise" + feedback.

    Mock LLM 返回 factual=4 (<6), 其余维度 >=6,
    review() 应返回 review_decision="revise", review_feedback 非空.
    """
    mock_llm.achat.return_value = LLMResponse(
        content=(
            '{"factual": {"score": 4, "issues": ["存在幻觉, 数据无上下文支持"]}, '
            '"structural": {"score": 7, "issues": []}, '
            '"language": {"score": 8, "issues": []}, '
            '"completeness": {"score": 7, "issues": []}, '
            '"overall_decision": "revise", '
            '"revision_instructions": "请核实 35% 数据来源"}'
        ),
        model="test-model",
    )
    reviewer = Reviewer(settings=settings, llm=mock_llm)

    result = await reviewer.review(base_state)

    assert result["review_decision"] == "revise"
    assert len(result["review_feedback"]) > 0
    # 反馈应含修订建议或低分维度问题
    assert "事实性" in result["review_feedback"] or "修订" in result["review_feedback"]
    # 评分结构正确
    scores = result["review_scores"]
    assert scores["factual"]["score"] == 4
    assert scores["structural"]["score"] == 7


@pytest.mark.asyncio
async def test_reviewer_handles_llm_error(
    settings: Settings,
    mock_llm: MagicMock,
    base_state: ResearcherState,
) -> None:
    """测试 LLM 返回非法 JSON 时 _fallback_scores 兜底为 accept.

    Reviewer.review 用 safe_json_parse(fallback=_fallback_scores()),
    _fallback_scores 返回全 6 分 (阈值边界), 决策为 accept (不阻断流程).
    """
    mock_llm.achat.return_value = LLMResponse(
        content="这不是合法 JSON",
        model="test-model",
    )
    reviewer = Reviewer(settings=settings, llm=mock_llm)

    result = await reviewer.review(base_state)

    # 兜底: 全 6 分 → accept
    assert result["review_decision"] == "accept"
    for dim in ("factual", "structural", "language", "completeness"):
        assert result["review_scores"][dim]["score"] == 6


@pytest.mark.asyncio
async def test_reviewer_respects_max_iterations(
    settings: Settings,
    mock_llm: MagicMock,
    base_state: ResearcherState,
) -> None:
    """测试评分缓存命中时跳过 LLM 调用 (Reviewer 的"强制"机制).

    Reviewer 自身不检查 iteration_count (max_iterations 守卫由图级负责,
    见 multi_agent_builder). Reviewer 的"强制"机制是评分缓存:
    同一报告重复评审时, 缓存命中直接返回上次决策, 跳过 LLM 调用,
    避免冗余评审 (类似 max_iterations 防止无限循环).
    """
    # 第一次评审: LLM 判定 revise, 写入缓存
    mock_llm.achat.return_value = LLMResponse(
        content=(
            '{"factual": {"score": 4, "issues": ["幻觉"]}, '
            '"structural": {"score": 7, "issues": []}, '
            '"language": {"score": 8, "issues": []}, '
            '"completeness": {"score": 7, "issues": []}, '
            '"overall_decision": "revise", '
            '"revision_instructions": "修订建议"}'
        ),
        model="test-model",
    )
    reviewer = Reviewer(settings=settings, llm=mock_llm)

    first_result = await reviewer.review(base_state)
    assert first_result["review_decision"] == "revise"
    assert mock_llm.achat.await_count == 1

    # 第二次评审同一报告: 缓存命中, 不再调用 LLM
    second_result = await reviewer.review(base_state)

    assert second_result["review_decision"] == "revise"
    # LLM 调用次数仍为 1 (缓存命中跳过)
    assert mock_llm.achat.await_count == 1
    # 缓存命中应返回与首次相同的结果
    assert (
        second_result["review_scores"]["factual"]["score"]
        == first_result["review_scores"]["factual"]["score"]
    )
