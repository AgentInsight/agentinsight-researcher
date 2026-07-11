"""单元测试: PromptFamily 策略模式.

验证 DefaultPromptFamily / EnglishPromptFamily 所有方法返回非空字符串,
get_prompt_family 工厂路由正确, register_prompt_family 自定义注册.
单元测试在构建期执行, 不依赖外部服务.
"""

from __future__ import annotations

import pytest

from src.skills.researcher.prompts import (
    DefaultPromptFamily,
    EnglishPromptFamily,
    get_prompt_family,
)

pytestmark = pytest.mark.unit


# ========== DefaultPromptFamily ==========


@pytest.fixture()
def default_family() -> DefaultPromptFamily:
    return DefaultPromptFamily()


def test_default_planner_prompt_non_empty(default_family: DefaultPromptFamily) -> None:
    """测试 planner_prompt 返回非空字符串."""
    prompt = default_family.planner_prompt("研究 AI", "你是分析师", 4)
    assert isinstance(prompt, str)
    assert len(prompt) > 0
    assert "研究 AI" in prompt
    assert "你是分析师" in prompt
    assert "4" in prompt


def test_default_writer_prompt_non_empty(default_family: DefaultPromptFamily) -> None:
    """测试 writer_prompt 返回非空字符串."""
    prompt = default_family.writer_prompt(
        query="新能源",
        contexts="一些上下文",
        agent_role="分析师",
        tone="objective",
        word_limit=1000,
        report_type="basic_report",
        current_date="2025年1月1日",
        references="[1] xxx",
        structure_hint="# 结构",
        report_style="academic",
    )
    assert isinstance(prompt, str)
    assert len(prompt) > 0
    assert "新能源" in prompt
    assert "1000" in prompt


def test_default_curator_prompt_non_empty(default_family: DefaultPromptFamily) -> None:
    """测试 curator_prompt 返回非空字符串."""
    prompt = default_family.curator_prompt("研究问题", "来源列表", "分析师", 5)
    assert isinstance(prompt, str)
    assert len(prompt) > 0
    assert "5" in prompt


def test_default_agent_creator_prompt_non_empty(default_family: DefaultPromptFamily) -> None:
    """测试 agent_creator_prompt 返回非空字符串."""
    prompt = default_family.agent_creator_prompt("查询")
    assert isinstance(prompt, str)
    assert len(prompt) > 0


def test_default_reviewer_prompt_non_empty(default_family: DefaultPromptFamily) -> None:
    """测试 reviewer_prompt 返回非空字符串."""
    prompt = default_family.reviewer_prompt("# 报告", "上下文", "分析师")
    assert isinstance(prompt, str)
    assert len(prompt) > 0


def test_default_fact_checker_prompt_non_empty(default_family: DefaultPromptFamily) -> None:
    """测试 fact_checker_prompt 返回非空字符串."""
    prompt = default_family.fact_checker_prompt("# 报告", "上下文", "来源")
    assert isinstance(prompt, str)
    assert len(prompt) > 0


def test_default_mcp_tool_selection_prompt_non_empty(
    default_family: DefaultPromptFamily,
) -> None:
    """测试 mcp_tool_selection_prompt 返回非空字符串."""
    prompt = default_family.mcp_tool_selection_prompt("查询", "[{}]", 3)
    assert isinstance(prompt, str)
    assert len(prompt) > 0
    assert "3" in prompt


def test_default_chat_prompt_non_empty(default_family: DefaultPromptFamily) -> None:
    """测试 chat_prompt 返回非空字符串."""
    prompt = default_family.chat_prompt("追问", "# 报告", "分析师")
    assert isinstance(prompt, str)
    assert len(prompt) > 0


# ========== get_tone_prompt (DefaultPromptFamily) ==========


@pytest.mark.parametrize(
    "tone",
    [
        "objective",
        "analytical",
        "formal",
        "informative",
        "explanatory",
        "critical",
        "comparative",
        "casual",
    ],
)
def test_default_get_tone_prompt_known_tones(
    default_family: DefaultPromptFamily,
    tone: str,
) -> None:
    """测试 8 种已注册 tone 返回非空提示词."""
    result = default_family.get_tone_prompt(tone)
    assert isinstance(result, str)
    assert len(result) > 0
    assert "## 写作语气要求" in result


def test_default_get_tone_prompt_unknown_tone_falls_back(
    default_family: DefaultPromptFamily,
) -> None:
    """测试未知 tone 降级为 objective."""
    result = default_family.get_tone_prompt("unknown_tone")
    assert isinstance(result, str)
    assert len(result) > 0
    # 应包含 objective 的描述
    assert "客观" in result


# ========== EnglishPromptFamily ==========


@pytest.fixture()
def english_family() -> EnglishPromptFamily:
    return EnglishPromptFamily()


def test_english_planner_prompt_non_empty(english_family: EnglishPromptFamily) -> None:
    prompt = english_family.planner_prompt("AI research", "You are analyst", 4)
    assert isinstance(prompt, str)
    assert len(prompt) > 0
    assert "AI research" in prompt


def test_english_writer_prompt_non_empty(english_family: EnglishPromptFamily) -> None:
    prompt = english_family.writer_prompt(
        query="EV market",
        contexts="context",
        agent_role="analyst",
        tone="objective",
        word_limit=1000,
        report_type="basic_report",
        current_date="2025-01-01",
        references="[1] xxx",
        structure_hint="# Structure",
        report_style="academic",
    )
    assert isinstance(prompt, str)
    assert len(prompt) > 0
    assert "EV market" in prompt


def test_english_curator_prompt_non_empty(english_family: EnglishPromptFamily) -> None:
    prompt = english_family.curator_prompt("question", "sources", "role", 5)
    assert isinstance(prompt, str)
    assert len(prompt) > 0


def test_english_agent_creator_prompt_non_empty(english_family: EnglishPromptFamily) -> None:
    prompt = english_family.agent_creator_prompt("query")
    assert isinstance(prompt, str)
    assert len(prompt) > 0


def test_english_reviewer_prompt_non_empty(english_family: EnglishPromptFamily) -> None:
    prompt = english_family.reviewer_prompt("# Report", "context", "role")
    assert isinstance(prompt, str)
    assert len(prompt) > 0


def test_english_fact_checker_prompt_non_empty(english_family: EnglishPromptFamily) -> None:
    prompt = english_family.fact_checker_prompt("# Report", "context", "sources")
    assert isinstance(prompt, str)
    assert len(prompt) > 0


def test_english_mcp_tool_selection_prompt_non_empty(
    english_family: EnglishPromptFamily,
) -> None:
    prompt = english_family.mcp_tool_selection_prompt("query", "[{}]", 3)
    assert isinstance(prompt, str)
    assert len(prompt) > 0


def test_english_chat_prompt_non_empty(english_family: EnglishPromptFamily) -> None:
    prompt = english_family.chat_prompt("followup", "# Report", "role")
    assert isinstance(prompt, str)
    assert len(prompt) > 0


def test_english_get_tone_prompt_known_tones(english_family: EnglishPromptFamily) -> None:
    """测试英文版 tone 提示词."""
    result = english_family.get_tone_prompt("objective")
    assert isinstance(result, str)
    assert len(result) > 0
    assert "## Tone Requirement" in result


# ========== get_prompt_family 工厂路由 ==========


def test_get_prompt_family_default_returns_default() -> None:
    """测试 'default' 返回 DefaultPromptFamily 实例."""
    family = get_prompt_family("default")
    assert isinstance(family, DefaultPromptFamily)


def test_get_prompt_family_english_returns_english() -> None:
    """测试 'english' 返回 EnglishPromptFamily 实例."""
    family = get_prompt_family("english")
    assert isinstance(family, EnglishPromptFamily)


def test_get_prompt_family_unknown_falls_back_to_default() -> None:
    """测试未知 name 降级为 DefaultPromptFamily."""
    family = get_prompt_family("unknown")
    assert isinstance(family, DefaultPromptFamily)


def test_get_prompt_family_default_when_no_arg() -> None:
    """测试无参数时默认返回 DefaultPromptFamily."""
    family = get_prompt_family()
    assert isinstance(family, DefaultPromptFamily)


# ========== curator_prompt 格式精简 (P2 优化: 仅 index+score, 移除 reason) ==========


def test_default_curator_prompt_contains_index_and_score(
    default_family: DefaultPromptFamily,
) -> None:
    """测试中文版 curator_prompt 要求输出 index 与 score 字段.

    P2 优化 (trace 4ad14970): prompt 仅要求 index+score, 减少 LLM 输出 token.
    """
    prompt = default_family.curator_prompt("研究问题", "来源列表", "分析师", 5)
    assert "index" in prompt
    assert "score" in prompt


def test_default_curator_prompt_no_reason_required(
    default_family: DefaultPromptFamily,
) -> None:
    """测试中文版 curator_prompt 明确声明不需要 reason 字段.

    P2 优化: prompt 含 "不需要 reason" 明确声明, 对应 max_tokens 4000→2000.
    """
    prompt = default_family.curator_prompt("研究问题", "来源列表", "分析师", 5)
    assert "reason" in prompt
    assert "不需要" in prompt


def test_default_curator_prompt_example_format(
    default_family: DefaultPromptFamily,
) -> None:
    """测试中文版 curator_prompt 示例格式仅含 index+score (无 reason).

    示例 JSON 应为 {"index": 1, "score": 9} 格式, 不含 reason 字段.
    """
    prompt = default_family.curator_prompt("研究问题", "来源列表", "分析师", 5)
    # 验证示例格式含 index 与 score
    assert '"index": 1' in prompt or "'index': 1" in prompt
    assert '"score": 9' in prompt or "'score': 9" in prompt
    # 验证示例不含 reason
    assert '"reason"' not in prompt
    assert "'reason'" not in prompt


def test_default_curator_prompt_includes_max_results(
    default_family: DefaultPromptFamily,
) -> None:
    """测试中文版 curator_prompt 含 max_results 参数."""
    prompt = default_family.curator_prompt("研究问题", "来源列表", "分析师", 7)
    assert "7" in prompt


def test_default_curator_prompt_includes_query_and_sources(
    default_family: DefaultPromptFamily,
) -> None:
    """测试中文版 curator_prompt 含查询与来源文本."""
    prompt = default_family.curator_prompt("新能源市场", "[1] 来源A", "分析师", 5)
    assert "新能源市场" in prompt
    assert "[1] 来源A" in prompt


def test_english_curator_prompt_contains_index_and_score(
    english_family: EnglishPromptFamily,
) -> None:
    """测试英文版 curator_prompt 要求输出 index 与 score 字段."""
    prompt = english_family.curator_prompt("question", "sources", "role", 5)
    assert "index" in prompt
    assert "score" in prompt


def test_english_curator_prompt_no_reason_required(
    english_family: EnglishPromptFamily,
) -> None:
    """测试英文版 curator_prompt 明确声明不需要 reason 字段.

    P2 优化: prompt 含 "no reason needed" 明确声明.
    """
    prompt = english_family.curator_prompt("question", "sources", "role", 5)
    assert "reason" in prompt
    assert "no reason" in prompt.lower()


def test_english_curator_prompt_example_format(
    english_family: EnglishPromptFamily,
) -> None:
    """测试英文版 curator_prompt 示例格式仅含 index+score (无 reason)."""
    prompt = english_family.curator_prompt("question", "sources", "role", 5)
    assert '"index": 1' in prompt or "'index': 1" in prompt
    assert '"score": 9' in prompt or "'score': 9" in prompt
    assert '"reason"' not in prompt
    assert "'reason'" not in prompt


# ========== 中文版与英文版 curator_prompt 一致性 ==========


def test_curator_prompt_zh_en_both_contain_index_score(
    default_family: DefaultPromptFamily,
    english_family: EnglishPromptFamily,
) -> None:
    """测试中英版 curator_prompt 均含 index 与 score 字段要求."""
    zh_prompt = default_family.curator_prompt("查询", "来源", "角色", 5)
    en_prompt = english_family.curator_prompt("query", "sources", "role", 5)
    assert "index" in zh_prompt
    assert "index" in en_prompt
    assert "score" in zh_prompt
    assert "score" in en_prompt


def test_curator_prompt_zh_en_both_declare_no_reason(
    default_family: DefaultPromptFamily,
    english_family: EnglishPromptFamily,
) -> None:
    """测试中英版 curator_prompt 均明确声明不需要 reason (P2 优化一致性)."""
    zh_prompt = default_family.curator_prompt("查询", "来源", "角色", 5)
    en_prompt = english_family.curator_prompt("query", "sources", "role", 5)
    # 中文版含 "不需要 reason"
    assert "不需要" in zh_prompt
    assert "reason" in zh_prompt
    # 英文版含 "no reason"
    assert "no reason" in en_prompt.lower()


def test_curator_prompt_zh_en_both_example_no_reason(
    default_family: DefaultPromptFamily,
    english_family: EnglishPromptFamily,
) -> None:
    """测试中英版 curator_prompt 示例 JSON 均不含 reason 字段."""
    zh_prompt = default_family.curator_prompt("查询", "来源", "角色", 5)
    en_prompt = english_family.curator_prompt("query", "sources", "role", 5)
    # 两版示例均不含 reason
    assert '"reason"' not in zh_prompt
    assert '"reason"' not in en_prompt


def test_curator_prompt_zh_en_both_contain_max_results(
    default_family: DefaultPromptFamily,
    english_family: EnglishPromptFamily,
) -> None:
    """测试中英版 curator_prompt 均含 max_results 数值."""
    zh_prompt = default_family.curator_prompt("查询", "来源", "角色", 8)
    en_prompt = english_family.curator_prompt("query", "sources", "role", 8)
    assert "8" in zh_prompt
    assert "8" in en_prompt


def test_curator_prompt_zh_en_both_contain_query_and_sources(
    default_family: DefaultPromptFamily,
    english_family: EnglishPromptFamily,
) -> None:
    """测试中英版 curator_prompt 均含查询与来源文本."""
    zh_prompt = default_family.curator_prompt("新能源", "[1] 来源", "角色", 5)
    en_prompt = english_family.curator_prompt("新能源", "[1] 来源", "role", 5)
    assert "新能源" in zh_prompt
    assert "新能源" in en_prompt
    assert "[1] 来源" in zh_prompt
    assert "[1] 来源" in en_prompt


def test_curator_prompt_zh_en_both_contain_quantitative_value(
    default_family: DefaultPromptFamily,
    english_family: EnglishPromptFamily,
) -> None:
    """测试中英版 curator_prompt 均含 Quantitative Value 评估维度."""
    zh_prompt = default_family.curator_prompt("查询", "来源", "角色", 5)
    en_prompt = english_family.curator_prompt("query", "sources", "role", 5)
    assert "Quantitative Value" in zh_prompt
    assert "Quantitative Value" in en_prompt
