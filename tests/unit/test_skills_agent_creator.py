"""单元测试: AgentCreator LLM 动态角色生成器.

验证 AUTO_AGENT_INSTRUCTIONS 常量 (10 行业 few-shot + 三要素要求) 与
create_agent() 方法 (LLM 动态生成 / 兜底降级 / agent_role 优先级).

行业适配 4 层机制之 Prompt 层 (AGENTS.md 第 7 章):
- Prompt 层: AUTO_AGENT_INSTRUCTIONS few-shot → LLM 自主生成 persona
- Config 层: settings.agent_role / agent_role 参数注入 (优先级高于 LLM)

单元测试在构建期执行, 不依赖外部服务 (LLM 全部 mock).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings
from src.skills.researcher.agent_creator import AgentCreator

pytestmark = pytest.mark.unit


def _make_mock_llm(content: str) -> MagicMock:
    """构造 mock LLMClient, achat 返回含指定 content 的响应."""
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = content
    mock_llm.achat = AsyncMock(return_value=mock_response)
    return mock_llm


@pytest.fixture()
def settings() -> Settings:
    """构造无 env 文件的 Settings (避免读取 .env 配置)."""
    return Settings(_env_file=None)


@pytest.fixture()
def creator(settings: Settings) -> AgentCreator:
    """构造带 mock LLM 的 AgentCreator 实例."""
    mock_llm = _make_mock_llm(
        '{"server": "test_role", "agent_role_prompt": "测试 persona"}'
    )
    return AgentCreator(settings=settings, llm=mock_llm)


# ========== AUTO_AGENT_INSTRUCTIONS 常量 ==========


def test_auto_agent_instructions_exists_and_non_empty() -> None:
    """测试 AUTO_AGENT_INSTRUCTIONS 常量存在且非空."""
    assert hasattr(AgentCreator, "AUTO_AGENT_INSTRUCTIONS")
    assert isinstance(AgentCreator.AUTO_AGENT_INSTRUCTIONS, str)
    assert len(AgentCreator.AUTO_AGENT_INSTRUCTIONS.strip()) > 0


def test_auto_agent_instructions_contains_ten_few_shot_examples() -> None:
    """测试 AUTO_AGENT_INSTRUCTIONS 含 10 个行业 few-shot 例子 (按 'task:' 出现次数计数)."""
    count = AgentCreator.AUTO_AGENT_INSTRUCTIONS.count("task:")
    assert count == 10


def test_auto_agent_instructions_contains_ten_industries() -> None:
    """测试 AUTO_AGENT_INSTRUCTIONS 含 10 个行业关键词.

    源文件实际包含的行业: 金融/商业/旅行/医学/法律/技术/教育/科学/营销/环境.
    """
    industries = [
        "金融",
        "商业",
        "旅行",
        "医学",
        "法律",
        "技术",
        "教育",
        "科学",
        "营销",
        "环境",
    ]
    for industry in industries:
        assert industry in AgentCreator.AUTO_AGENT_INSTRUCTIONS, f"缺少行业: {industry}"


def test_auto_agent_instructions_contains_three_requirements() -> None:
    """测试 AUTO_AGENT_INSTRUCTIONS 含三要素要求 (研究方法论/输出规范/语言风格)."""
    prompt = AgentCreator.AUTO_AGENT_INSTRUCTIONS
    assert "研究方法论" in prompt
    assert "输出规范" in prompt
    assert "语言风格" in prompt


def test_default_agent_role_contains_three_elements() -> None:
    """测试兜底角色 _DEFAULT_AGENT_ROLE 含三要素."""
    prompt = AgentCreator._DEFAULT_AGENT_ROLE["agent_role_prompt"]
    assert "研究方法论" in prompt
    assert "输出规范" in prompt
    assert "语言风格" in prompt


# ========== AgentCreator 实例化 ==========


def test_agent_creator_can_instantiate(settings: Settings) -> None:
    """测试 AgentCreator 类可实例化."""
    mock_llm = MagicMock()
    creator_obj = AgentCreator(settings=settings, llm=mock_llm)
    assert creator_obj is not None
    assert creator_obj.settings is settings
    assert creator_obj._llm is mock_llm


def test_agent_creator_has_create_agent_method(creator: AgentCreator) -> None:
    """测试 create_agent() 方法存在且可调用."""
    assert hasattr(creator, "create_agent")
    assert callable(creator.create_agent)


# ========== create_agent() LLM 动态生成 ==========


@pytest.mark.asyncio
async def test_create_agent_llm_returns_persona(creator: AgentCreator) -> None:
    """测试 create_agent() LLM 返回有效 persona 时直接使用."""
    result = await creator.create_agent("分析新能源汽车市场")
    assert result["server"] == "test_role"
    assert result["agent_role_prompt"] == "测试 persona"


@pytest.mark.asyncio
async def test_create_agent_llm_returns_persona_with_three_elements(
    settings: Settings,
) -> None:
    """测试 LLM 返回的 persona 含三要素 (研究方法论/输出规范/语言风格)."""
    persona = (
        "你是一位医学研究专家。"
        "研究方法论: 采用系统综述与 meta 分析。"
        "输出规范: 报告需含数据支撑、明确引用来源、逻辑结构清晰。"
        "语言风格: 客观、专业、避免主观臆断。"
    )
    mock_llm = _make_mock_llm(
        '{"server": "medical_researcher", "agent_role_prompt": "' + persona + '"}'
    )
    creator_obj = AgentCreator(settings=settings, llm=mock_llm)
    result = await creator_obj.create_agent("查询涉及医学/医疗/健康/药物")
    assert "研究方法论" in result["agent_role_prompt"]
    assert "输出规范" in result["agent_role_prompt"]
    assert "语言风格" in result["agent_role_prompt"]


# ========== create_agent() 兜底降级 ==========


@pytest.mark.asyncio
async def test_create_agent_llm_empty_content_falls_back_to_default(
    settings: Settings,
) -> None:
    """测试 LLM 返回空内容时降级到默认 persona."""
    mock_llm = _make_mock_llm("")
    creator_obj = AgentCreator(settings=settings, llm=mock_llm)
    result = await creator_obj.create_agent("任意查询")
    assert result["server"] == AgentCreator._DEFAULT_AGENT_ROLE["server"]
    assert "研究方法论" in result["agent_role_prompt"]


@pytest.mark.asyncio
async def test_create_agent_llm_exception_falls_back_to_default(
    settings: Settings,
) -> None:
    """测试 LLM 抛异常时降级到默认 persona (不阻断主流程)."""
    mock_llm = MagicMock()
    mock_llm.achat = AsyncMock(side_effect=Exception("LLM 不可用"))
    creator_obj = AgentCreator(settings=settings, llm=mock_llm)
    result = await creator_obj.create_agent("任意查询")
    assert result["server"] == AgentCreator._DEFAULT_AGENT_ROLE["server"]
    assert "研究方法论" in result["agent_role_prompt"]


@pytest.mark.asyncio
async def test_create_agent_llm_invalid_json_falls_back_to_default(
    settings: Settings,
) -> None:
    """测试 LLM 返回非 JSON 字符串时降级到默认 persona."""
    mock_llm = _make_mock_llm("这不是一个有效的 JSON")
    creator_obj = AgentCreator(settings=settings, llm=mock_llm)
    result = await creator_obj.create_agent("任意查询")
    assert result["server"] == AgentCreator._DEFAULT_AGENT_ROLE["server"]


# ========== agent_role 注入优先级 (Config 层 > Prompt 层) ==========


@pytest.mark.asyncio
async def test_create_agent_preset_role_takes_priority_over_llm(
    settings: Settings,
) -> None:
    """测试 agent_role 注入优先级高于 LLM 自动生成 (跳过 LLM 调用)."""
    mock_llm = _make_mock_llm(
        '{"server": "llm_role", "agent_role_prompt": "LLM persona"}'
    )
    creator_obj = AgentCreator(settings=settings, llm=mock_llm)
    preset_role = "你是一位预设的金融分析师, 研究方法论: 定量分析."
    result = await creator_obj.create_agent("任意查询", agent_role=preset_role)
    # 应直接使用 preset_role, 不调 LLM
    assert result["server"] == "custom"
    assert result["agent_role_prompt"] == preset_role
    mock_llm.achat.assert_not_called()


@pytest.mark.asyncio
async def test_create_agent_no_preset_role_calls_llm(creator: AgentCreator) -> None:
    """测试无 agent_role 注入时调用 LLM 生成角色."""
    result = await creator.create_agent("分析量子计算技术")
    assert result["server"] == "test_role"
    creator._llm.achat.assert_called_once()
