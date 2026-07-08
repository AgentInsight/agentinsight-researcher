"""单元测试: ChitchatResponder (闲聊响应器 FAST_LLM + multi-template 降级).

验证 src/skills/researcher/chitchat_responder.py:
- respond_short_query: FAST_LLM 成功 / FAST_LLM 失败降级 / 空响应降级 / 流式
- respond_off_topic: FAST_LLM 成功 / 失败降级 / category 路由 / 未知 category 兜底 greeting
- _fallback_reply: chitchat_fallback_to_template 开关 (YAML 模板 vs settings 固定话术)

AGENTS.md 第 5 章: 节点为纯函数, 单一职责.
AGENTS.md 第 9 章: LLM 调用经 llm/ 网关 (LiteLLM).
AGENTS.md 第 10 章: trace_chain span 包裹.
AGENTS.md 第 13 章: 单元测试不依赖外部服务 (LLM/Config 全部 mock).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.researcher.persona import PersonaConfig
from src.config.settings import Settings
from src.llm.client import LLMResponse, LLMTier
from src.skills.researcher.chitchat_responder import ChitchatResponder

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture()
def settings() -> Settings:
    """构造闲聊测试 Settings."""
    return Settings(
        _env_file=None,
        chitchat_temperature=0.7,
        chitchat_max_tokens=200,
        chitchat_fallback_to_template=True,
        short_query_reply="短查询兜底话术",
        off_topic_reply="离题兜底话术",
    )


@pytest.fixture()
def persona() -> PersonaConfig:
    """构造 PersonaConfig."""
    return PersonaConfig()


@pytest.fixture()
def mock_config() -> MagicMock:
    """Mock ChitchatConfigBundle (render_prompt + random_reply + persona)."""
    config = MagicMock()
    config.render_prompt.return_value = "rendered system prompt"
    config.random_reply.return_value = "template fallback reply"
    config.persona = PersonaConfig()
    return config


@pytest.fixture()
def mock_llm() -> MagicMock:
    """Mock LLMClient (achat + achat_stream)."""
    llm = MagicMock()
    llm.achat = AsyncMock()
    return llm


@pytest.fixture()
def responder(
    settings: Settings,
    persona: PersonaConfig,
    mock_config: MagicMock,
    mock_llm: MagicMock,
) -> ChitchatResponder:
    """构造 ChitchatResponder (依赖全部 mock)."""
    return ChitchatResponder(
        settings=settings,
        persona=persona,
        config_bundle=mock_config,
        llm=mock_llm,
    )


def _make_llm_response(content: str = "FAST_LLM reply") -> LLMResponse:
    """构造 LLMResponse."""
    return LLMResponse(
        content=content,
        model="zhipuai/glm-4-flash",
        input_tokens=10,
        output_tokens=20,
        cost_usd=0.0001,
    )


# ========== respond_short_query: FAST_LLM 成功 ==========


@pytest.mark.asyncio
async def test_short_query_returns_fast_llm_response(
    responder: ChitchatResponder,
    mock_llm: MagicMock,
    mock_config: MagicMock,
) -> None:
    """short_query + FAST_LLM 成功 → 返回 LLM 内容."""
    mock_llm.achat.return_value = _make_llm_response("你好, 我是研究助手")

    result = await responder.respond_short_query(
        "你好", user_id="u1", session_id="s1"
    )

    assert result == "你好, 我是研究助手"
    # 应调用 achat 且 tier=FAST
    mock_llm.achat.assert_awaited_once()
    call_kwargs = mock_llm.achat.call_args.kwargs
    assert call_kwargs["tier"] == LLMTier.FAST
    # 应渲染 short_query.j2 模板
    mock_config.render_prompt.assert_called_once_with(
        "chitchat/short_query.j2",
        persona=responder._persona,
        query="你好",
    )


@pytest.mark.asyncio
async def test_short_query_passes_temperature_and_max_tokens(
    responder: ChitchatResponder,
    mock_llm: MagicMock,
    settings: Settings,
) -> None:
    """short_query 应使用 settings.chitchat_temperature / chitchat_max_tokens."""
    mock_llm.achat.return_value = _make_llm_response("reply")

    await responder.respond_short_query("hi")

    call_kwargs = mock_llm.achat.call_args.kwargs
    assert call_kwargs["temperature"] == settings.chitchat_temperature
    assert call_kwargs["max_tokens"] == settings.chitchat_max_tokens


# ========== respond_short_query: FAST_LLM 失败降级 ==========


@pytest.mark.asyncio
async def test_short_query_falls_back_on_llm_exception(
    responder: ChitchatResponder,
    mock_llm: MagicMock,
    mock_config: MagicMock,
) -> None:
    """FAST_LLM 抛异常 → 降级 multi-template (random_reply)."""
    mock_llm.achat.side_effect = RuntimeError("LLM 服务不可用")

    result = await responder.respond_short_query("你好")

    # 降级到 random_reply("short_query", None)
    assert result == "template fallback reply"
    mock_config.random_reply.assert_called_once_with("short_query", None)


@pytest.mark.asyncio
async def test_short_query_falls_back_on_empty_response(
    responder: ChitchatResponder,
    mock_llm: MagicMock,
    mock_config: MagicMock,
) -> None:
    """FAST_LLM 返回空内容 → 触发 ValueError → 降级 multi-template."""
    mock_llm.achat.return_value = _make_llm_response("   ")  # 空白内容

    result = await responder.respond_short_query("你好")

    assert result == "template fallback reply"
    mock_config.random_reply.assert_called_once_with("short_query", None)


# ========== respond_off_topic ==========


@pytest.mark.asyncio
async def test_off_topic_returns_fast_llm_response(
    responder: ChitchatResponder,
    mock_llm: MagicMock,
    mock_config: MagicMock,
) -> None:
    """off_topic + FAST_LLM 成功 → 返回 LLM 内容."""
    mock_llm.achat.return_value = _make_llm_response("你好呀")

    result = await responder.respond_off_topic(
        "你好啊", category="greeting", user_id="u1", session_id="s1"
    )

    assert result == "你好呀"
    mock_llm.achat.assert_awaited_once()
    # greeting → off_topic_greeting.j2
    mock_config.render_prompt.assert_called_once_with(
        "chitchat/off_topic_greeting.j2",
        persona=responder._persona,
        query="你好啊",
    )


@pytest.mark.asyncio
async def test_off_topic_falls_back_on_llm_failure(
    responder: ChitchatResponder,
    mock_llm: MagicMock,
    mock_config: MagicMock,
) -> None:
    """off_topic FAST_LLM 失败 → 降级 multi-template (category + subcategory)."""
    mock_llm.achat.side_effect = RuntimeError("LLM down")

    result = await responder.respond_off_topic("讲个笑话", category="entertainment")

    assert result == "template fallback reply"
    mock_config.random_reply.assert_called_once_with("off_topic", "entertainment")


# ========== off_topic category 路由 ==========


@pytest.mark.parametrize(
    ("category", "expected_template"),
    [
        ("greeting", "chitchat/off_topic_greeting.j2"),
        ("identity", "chitchat/off_topic_identity.j2"),
        ("emotion", "chitchat/off_topic_emotion.j2"),
        ("entertainment", "chitchat/off_topic_entertainment.j2"),
        ("common_sense", "chitchat/off_topic_common_sense.j2"),
        ("capability_check", "chitchat/off_topic_capability.j2"),
        ("topic_switch", "chitchat/off_topic_topic_switch.j2"),
        # evaluation 兜底用 greeting 模板
        ("evaluation", "chitchat/off_topic_greeting.j2"),
    ],
)
@pytest.mark.asyncio
async def test_off_topic_category_routes_to_correct_template(
    responder: ChitchatResponder,
    mock_llm: MagicMock,
    mock_config: MagicMock,
    category: str,
    expected_template: str,
) -> None:
    """off_topic category 应路由到对应 Jinja2 模板文件."""
    mock_llm.achat.return_value = _make_llm_response("ok")

    await responder.respond_off_topic("query", category=category)

    mock_config.render_prompt.assert_called_once_with(
        expected_template,
        persona=responder._persona,
        query="query",
    )


@pytest.mark.asyncio
async def test_off_topic_unknown_category_falls_back_to_greeting(
    responder: ChitchatResponder,
    mock_llm: MagicMock,
    mock_config: MagicMock,
) -> None:
    """未知 category → 兜底用 greeting 模板."""
    mock_llm.achat.return_value = _make_llm_response("hi")

    await responder.respond_off_topic("q", category="unknown_xyz")

    mock_config.render_prompt.assert_called_once_with(
        "chitchat/off_topic_greeting.j2",
        persona=responder._persona,
        query="q",
    )


# ========== _fallback_reply: chitchat_fallback_to_template 开关 ==========


@pytest.mark.asyncio
async def test_fallback_uses_settings_when_template_disabled(
    mock_llm: MagicMock,
    mock_config: MagicMock,
    persona: PersonaConfig,
) -> None:
    """chitchat_fallback_to_template=False → 返回 settings 固定话术 (旧版兼容)."""
    settings = Settings(
        _env_file=None,
        chitchat_fallback_to_template=False,
        short_query_reply="settings 短查询话术",
        off_topic_reply="settings 离题话术",
    )
    responder = ChitchatResponder(
        settings=settings,
        persona=persona,
        config_bundle=mock_config,
        llm=mock_llm,
    )
    mock_llm.achat.side_effect = RuntimeError("fail")

    short_result = await responder.respond_short_query("hi")
    off_result = await responder.respond_off_topic("hi", category="greeting")

    assert short_result == "settings 短查询话术"
    assert off_result == "settings 离题话术"
    # 不应调用 random_reply
    mock_config.random_reply.assert_not_called()


@pytest.mark.asyncio
async def test_fallback_yaml_missing_key_degrades_to_settings(
    mock_llm: MagicMock,
    persona: PersonaConfig,
) -> None:
    """YAML 兜底话术缺失 (KeyError) → 再降级到 settings 固定话术."""
    settings = Settings(
        _env_file=None,
        chitchat_fallback_to_template=True,
        short_query_reply="settings 短查询话术",
    )
    mock_config = MagicMock()
    mock_config.render_prompt.return_value = "system prompt"
    mock_config.random_reply.side_effect = KeyError("missing key")
    mock_config.persona = PersonaConfig()

    responder = ChitchatResponder(
        settings=settings,
        persona=persona,
        config_bundle=mock_config,
        llm=mock_llm,
    )
    mock_llm.achat.side_effect = RuntimeError("fail")

    result = await responder.respond_short_query("hi")

    assert result == "settings 短查询话术"


# ========== 流式响应 ==========


@pytest.mark.asyncio
async def test_short_query_stream_yields_chunks(
    responder: ChitchatResponder,
    mock_llm: MagicMock,
) -> None:
    """stream=True → 逐块 yield FAST_LLM 流式输出."""

    async def _fake_stream(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        for chunk in ["你好", "，", "我是助手"]:
            yield chunk

    mock_llm.achat_stream = _fake_stream

    stream = responder.respond_short_query("你好", stream=True)
    chunks = [chunk async for chunk in stream]

    assert chunks == ["你好", "，", "我是助手"]


@pytest.mark.asyncio
async def test_short_query_stream_falls_back_on_llm_failure(
    responder: ChitchatResponder,
    mock_llm: MagicMock,
    mock_config: MagicMock,
) -> None:
    """流式 FAST_LLM 失败 → 一次性 yield 完整兜底话术."""

    async def _failing_stream(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        yield "部分内容"
        raise RuntimeError("stream broken")
        yield  # pragma: no cover  # noqa: YIELD  # 让 mypy 识别为 async generator

    mock_llm.achat_stream = _failing_stream

    stream = responder.respond_short_query("hi", stream=True)
    chunks = [chunk async for chunk in stream]

    # 失败前已 yield 的 "部分内容" + 兜底话术
    assert "部分内容" in chunks
    assert "template fallback reply" in chunks
