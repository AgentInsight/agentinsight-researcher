"""单元测试: PromptFamily 策略模式.

验证 DefaultPromptFamily / EnglishPromptFamily 所有方法返回非空字符串,
get_prompt_family 工厂路由正确, register_prompt_family 自定义注册.
AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
"""

from __future__ import annotations

import pytest

from src.skills.researcher.prompts import (
    DefaultPromptFamily,
    EnglishPromptFamily,
    PromptFamily,
    get_prompt_family,
    register_prompt_family,
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


def test_default_visualizer_prompt_non_empty(default_family: DefaultPromptFamily) -> None:
    """测试 visualizer_prompt 返回非空字符串."""
    prompt = default_family.visualizer_prompt("# 报告", "查询")
    assert isinstance(prompt, str)
    assert len(prompt) > 0


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


def test_english_visualizer_prompt_non_empty(english_family: EnglishPromptFamily) -> None:
    prompt = english_family.visualizer_prompt("# Report", "query")
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


# ========== register_prompt_family 自定义注册 ==========


class _CustomPromptFamily(PromptFamily):
    """自定义 PromptFamily 用于测试注册."""

    def planner_prompt(self, query: str, agent_role: str, max_iterations: int) -> str:
        return "custom-planner"

    def writer_prompt(
        self,
        query: str,
        contexts: str,
        agent_role: str,
        tone: str,
        word_limit: int,
        report_type: str,
        current_date: str,
        references: str,
        structure_hint: str,
        report_style: str = "academic",
    ) -> str:
        return "custom-writer"

    def curator_prompt(
        self, query: str, sources_text: str, agent_role: str, max_results: int
    ) -> str:
        return "custom-curator"

    def agent_creator_prompt(self, query: str) -> str:
        return "custom-agent-creator"

    def reviewer_prompt(self, report_md: str, contexts: str, agent_role: str) -> str:
        return "custom-reviewer"

    def fact_checker_prompt(self, report_md: str, contexts: str, sources: str) -> str:
        return "custom-fact-checker"

    def mcp_tool_selection_prompt(self, query: str, tools_json: str, max_tools: int) -> str:
        return "custom-mcp"

    def visualizer_prompt(self, report_md: str, query: str) -> str:
        return "custom-visualizer"

    def chat_prompt(self, query: str, report_md: str, agent_role: str) -> str:
        return "custom-chat"

    def get_tone_prompt(self, tone: str) -> str:
        return "custom-tone"

    # V2-P1: detailed_report 专用 prompt 实现 (新增 4 个抽象方法)
    def subtopics_prompt(
        self,
        query: str,
        context: str,
        role_persona: str,
        max_subtopics: int = 5,
    ) -> str:
        return "custom-subtopics"

    def introduction_prompt(
        self,
        query: str,
        context: str,
        references: str,
        role_persona: str,
        tone: str,
        current_date: str,
        style_desc: str,
        word_min: int = 300,
        word_max: int = 500,
    ) -> str:
        return "custom-introduction"

    def section_prompt(
        self,
        topic: str,
        context: str,
        references: str,
        role_persona: str,
        tone: str,
        style_desc: str,
        word_min: int = 800,
        word_max: int = 1200,
    ) -> str:
        return "custom-section"

    def conclusion_prompt(
        self,
        query: str,
        sections_summary: str,
        role_persona: str,
        tone: str,
        style_desc: str,
        word_min: int = 300,
        word_max: int = 500,
    ) -> str:
        return "custom-conclusion"


def test_register_prompt_family_custom() -> None:
    """测试注册自定义 family 后可被 get_prompt_family 取出."""
    register_prompt_family("test_custom", _CustomPromptFamily)
    family = get_prompt_family("test_custom")
    assert isinstance(family, _CustomPromptFamily)
    assert family.planner_prompt("q", "r", 1) == "custom-planner"
