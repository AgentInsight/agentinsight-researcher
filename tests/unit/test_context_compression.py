"""单元测试: 上下文压缩策略 (AGENTS.md 第 6 章 P1-01/V4-P1-04).

验证 src/skills/researcher/context_manager.py:
- compress_messages: 低于 compression_threshold 不触发 _hybrid_compress
- compress_messages: 超阈值触发 _hybrid_compress (混合压缩策略)
- _hybrid_compress: 保留最近 N 条原文 (context_sliding_window=5)
- _hybrid_compress: 远期消息 LLM 摘要化
- _hybrid_compress: 摘要失败降级仅返回近期原文
- _hybrid_compress: 消息数不足窗口大小直接返回原文

AGENTS.md 第 6 章:
- 单会话上下文上限 CONTEXT_MAX_CHARS = 800_000 (约 200K token)
- 写入会话前应调用 compress_if_needed() 检查阈值
- 上下文压缩策略: 滑动窗口 + LLM 摘要, 保留最近 25% 消息为原文, 其余摘要化

AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
所有外部依赖 (LLMClient/EmbeddingsClient) 全部 mock.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.skills.researcher.context_manager import ContextManager

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture()
def test_settings() -> Settings:
    """测试用 Settings (低 compression_threshold 便于触发压缩, 滑动窗口=5)."""
    return Settings(
        compression_threshold=8000,
        context_sliding_window=5,
        context_compressed_target=200_000,
        _env_file=None,
    )


@pytest.fixture()
def mock_llm() -> MagicMock:
    """构造 mock LLMClient (achat 返回固定摘要)."""
    llm = MagicMock()
    llm.achat = AsyncMock(return_value=MagicMock(content="远期消息摘要内容"))
    return llm


@pytest.fixture()
def mock_embeddings() -> MagicMock:
    """构造 mock EmbeddingsClient."""
    emb = MagicMock()
    emb.is_circuit_open = MagicMock(return_value=False)
    return emb


@pytest.fixture()
def context_manager(
    test_settings: Settings,
    mock_llm: MagicMock,
    mock_embeddings: MagicMock,
) -> ContextManager:
    """构造 ContextManager (注入 mock 依赖, 替换 _compressor 与 _written_compressor)."""
    with patch(
        "src.skills.researcher.context_manager.get_embeddings_client",
        return_value=mock_embeddings,
    ), patch(
        "src.skills.researcher.context_manager.get_llm_client",
        return_value=mock_llm,
    ):
        cm = ContextManager(test_settings)
    # 替换 _compressor 与 _written_compressor 为 mock, 避免触发真实 LLM/embedding 调用
    cm._compressor = MagicMock()
    cm._compressor.compress = AsyncMock(side_effect=lambda msgs: msgs)
    cm._written_compressor = MagicMock()
    cm._written_compressor.should_keep = AsyncMock(return_value=True)
    cm._written_compressor.reset = MagicMock()
    return cm


def _make_messages(count: int, chars_per_msg: int = 100) -> list[dict[str, Any]]:
    """构造 count 条消息, 每条 content 长度约 chars_per_msg."""
    messages: list[dict[str, Any]] = []
    for i in range(count):
        content = f"msg-{i}-" + ("x" * max(1, chars_per_msg - 7))
        messages.append({"role": "user", "content": content})
    return messages


# ========== compress_messages: 阈值边界 ==========


async def test_compress_messages_below_threshold_no_compress(
    context_manager: ContextManager,
) -> None:
    """低于 compression_threshold 不触发 _hybrid_compress, 走 _compressor.compress."""
    # 总字符 < 8000 (compression_threshold), 走 _compressor.compress (SlidingWindowCompressor)
    messages = _make_messages(count=3, chars_per_msg=100)  # 总字符 ≈ 300 < 8000

    # 监视 _hybrid_compress, 确保不被调用
    with patch.object(
        context_manager, "_hybrid_compress", new=AsyncMock()
    ) as mock_hybrid:
        result = await context_manager.compress_messages(messages)

    mock_hybrid.assert_not_called()
    # _compressor.compress 应被调用 (走 SlidingWindowCompressor 路径)
    context_manager._compressor.compress.assert_awaited_once()
    # 返回结果应与输入一致 (mock 透传)
    assert result == messages


async def test_compress_messages_above_threshold_triggers_hybrid(
    context_manager: ContextManager,
) -> None:
    """超阈值 (total_chars > compression_threshold) 触发 _hybrid_compress."""
    # 总字符 > 8000: 10 条 × 1000 字符 = 10000 > 8000
    messages = _make_messages(count=10, chars_per_msg=1000)

    hybrid_called = False

    async def fake_hybrid(msgs: list[dict[str, Any]], target: int) -> list[dict[str, Any]]:
        nonlocal hybrid_called
        hybrid_called = True
        return msgs  # 透传, 测试只关心是否被调用

    context_manager._hybrid_compress = fake_hybrid  # type: ignore[assignment]

    await context_manager.compress_messages(messages)

    assert hybrid_called, "超阈值应触发 _hybrid_compress"
    # _compressor.compress 不应被调用 (走了 hybrid 路径)
    context_manager._compressor.compress.assert_not_awaited()


# ========== _hybrid_compress: 滑动窗口 + 摘要 ==========


async def test_hybrid_compress_retains_recent_25_percent(
    context_manager: ContextManager,
) -> None:
    """保留最近 N 条原文 (context_sliding_window=5).

    构造 10 条消息 (10 > 窗口 5), mock _summarize_old_messages 返回非空摘要,
    验证返回结果末尾 5 条与原始最后 5 条一致.
    """
    messages = _make_messages(count=10, chars_per_msg=50)

    # mock 摘要返回非空字符串 (避免降级路径)
    context_manager._summarize_old_messages = AsyncMock(return_value="远期摘要")

    result = await context_manager._hybrid_compress(
        messages, target_tokens=context_manager.settings.context_compressed_target
    )

    # 期望: [summary_msg] + recent_messages (最近 5 条)
    assert len(result) == 6  # 1 摘要 + 5 原文
    # 第一条为摘要 (role=system, content 含 [历史摘要] 前缀)
    assert result[0]["role"] == "system"
    assert "[历史摘要]" in result[0]["content"]
    # 末尾 5 条应与原始最后 5 条完全一致
    assert result[1:] == messages[-5:]


async def test_hybrid_compress_summarizes_old_messages(
    context_manager: ContextManager,
) -> None:
    """远期消息 LLM 摘要化 (old_messages 经 _summarize_old_messages 处理)."""
    # 8 条消息, 窗口=5, 故前 3 条为 old_messages
    messages = _make_messages(count=8, chars_per_msg=50)

    captured_text: list[str] = []

    async def capture_summarize(text: str, target_tokens: int) -> str:
        captured_text.append(text)
        return "合成摘要"

    context_manager._summarize_old_messages = capture_summarize  # type: ignore[assignment]

    result = await context_manager._hybrid_compress(
        messages, target_tokens=context_manager.settings.context_compressed_target
    )

    # _summarize_old_messages 应被调用一次, 入参 text 含所有远期消息内容
    assert len(captured_text) == 1
    # 远期消息为 messages[:-5] = messages[0:3]
    for old_msg in messages[:-5]:
        assert old_msg["content"] in captured_text[0]
    # 近期消息不应出现在摘要输入中
    for recent_msg in messages[-5:]:
        # 近期消息内容不应作为摘要输入 (但内容前缀可能巧合, 故检查完整 content)
        assert recent_msg["content"] not in captured_text[0]

    # 返回首条为摘要消息
    assert result[0]["role"] == "system"
    assert "合成摘要" in result[0]["content"]


async def test_hybrid_compress_summary_failure_degrades_to_recent_only(
    context_manager: ContextManager,
) -> None:
    """摘要失败 (返回空字符串) 降级仅返回近期原文.

    AGENTS.md 第 6 章: 压缩应不阻塞用户响应, 摘要失败时降级保留近期原文,
    避免远期噪声 (源码 117-119 行: `if not summary: return recent_messages`).
    """
    messages = _make_messages(count=10, chars_per_msg=50)

    # mock 摘要返回空字符串 (模拟 LLM 调用失败降级)
    context_manager._summarize_old_messages = AsyncMock(return_value="")

    result = await context_manager._hybrid_compress(
        messages, target_tokens=context_manager.settings.context_compressed_target
    )

    # 降级: 仅返回 recent_messages (5 条), 不含摘要消息
    assert len(result) == 5
    assert result == messages[-5:]
    # 不应包含 [历史摘要] 系统消息
    for msg in result:
        assert msg["role"] != "system" or "[历史摘要]" not in msg.get("content", "")


async def test_hybrid_compress_messages_below_window_size_returns_original(
    context_manager: ContextManager,
) -> None:
    """消息数不足窗口大小 (len <= context_sliding_window) 直接返回原文.

    源码 103-104 行: `if len(messages) <= n: return messages`
    """
    # 3 条消息 < 窗口 5, 直接返回原文
    messages = _make_messages(count=3, chars_per_msg=50)

    # 监视 _summarize_old_messages, 确保不被调用
    context_manager._summarize_old_messages = AsyncMock(
        side_effect=AssertionError("消息数不足窗口不应触发摘要")
    )

    result = await context_manager._hybrid_compress(
        messages, target_tokens=context_manager.settings.context_compressed_target
    )

    # 直接返回原文 (引用相同对象)
    assert result is messages
    assert len(result) == 3
