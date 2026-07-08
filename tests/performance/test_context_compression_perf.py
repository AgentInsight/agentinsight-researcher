"""性能测试: 上下文压缩性能 (消息压缩延迟 / 内存占用).

AGENTS.md 第 6 章硬约束:
- ContextManager: 滑动窗口 + LLM 摘要压缩消息列表
- 保留最近 25% 消息为原文, 其余 LLM 摘要化
- CONTEXT_MAX_CHARS: 上下文总字符数上限

执行方式:
    pytest tests/performance/test_context_compression_perf.py -v -m performance -s
"""

from __future__ import annotations

import time

import pytest

from src.config.settings import get_settings

pytestmark = pytest.mark.unit


def _generate_test_messages(char_count: int) -> list[dict[str, str]]:
    """生成指定字符数的测试消息列表."""
    base_message = {
        "role": "user",
        "content": (
            "人工智能在医疗领域的应用前景非常广阔。近年来，机器学习和深度学习技术"
            "取得了重大突破，为医疗诊断、药物研发、健康管理等多个方面带来了革命性"
            "的变化。"
        ),
    }
    messages = []
    current_chars = 0
    while current_chars < char_count:
        messages.append(base_message.copy())
        current_chars += len(base_message["content"])
    return messages


# ========== 消息压缩延迟测试 ==========


async def test_compress_messages_100k_chars_latency() -> None:
    """验证 100K 字符消息压缩延迟.

    AGENTS.md 第 6 章: ContextManager.compress_messages 负责长会话压缩.
    100K 字符约 25K tokens, 应在合理时间内完成压缩.

    阈值: 100K 字符压缩延迟 < 30s.
    """
    from unittest.mock import AsyncMock, MagicMock

    settings = get_settings()
    original_threshold = settings.compression_threshold
    try:
        settings.compression_threshold = 10000

        messages = _generate_test_messages(100000)
        total_chars = sum(len(m.get("content", "")) for m in messages)

        mock_llm = MagicMock()
        mock_llm.achat = AsyncMock(return_value=MagicMock(content="测试摘要内容"))

        from src.skills.researcher.context_manager import ContextManager

        cm = ContextManager(settings)
        cm._llm = mock_llm

        start = time.perf_counter()
        compressed = await cm.compress_messages(messages)
        elapsed = time.perf_counter() - start

        assert len(compressed) > 0, "压缩后消息列表为空"
        assert elapsed < 30.0, (
            f"100K 字符消息压缩延迟 {elapsed:.3f}s 超过阈值 30s (原始字符数 {total_chars})"
        )
        print(f"\n[compress_messages_100k] {total_chars} chars in {elapsed:.3f}s (阈值 30s)")
    finally:
        settings.compression_threshold = original_threshold


async def test_compress_messages_500k_chars_latency() -> None:
    """验证 500K 字符消息压缩延迟.

    500K 字符约 125K tokens, 接近模型上下文上限 (128K).
    此场景验证系统在高负载下的压缩性能.

    阈值: 500K 字符压缩延迟 < 60s.
    """
    from unittest.mock import AsyncMock, MagicMock

    settings = get_settings()
    original_threshold = settings.compression_threshold
    try:
        settings.compression_threshold = 10000

        messages = _generate_test_messages(500000)
        total_chars = sum(len(m.get("content", "")) for m in messages)

        mock_llm = MagicMock()
        mock_llm.achat = AsyncMock(return_value=MagicMock(content="测试摘要内容"))

        from src.skills.researcher.context_manager import ContextManager

        cm = ContextManager(settings)
        cm._llm = mock_llm

        start = time.perf_counter()
        compressed = await cm.compress_messages(messages)
        elapsed = time.perf_counter() - start

        assert len(compressed) > 0, "压缩后消息列表为空"
        assert elapsed < 60.0, (
            f"500K 字符消息压缩延迟 {elapsed:.3f}s 超过阈值 60s (原始字符数 {total_chars})"
        )
        print(f"\n[compress_messages_500k] {total_chars} chars in {elapsed:.3f}s (阈值 60s)")
    finally:
        settings.compression_threshold = original_threshold


# ========== 上下文压缩内存占用测试 ==========


async def test_context_compression_memory_usage() -> None:
    """验证上下文压缩内存占用.

    AGENTS.md 第 6 章: 上下文压缩应控制内存占用, 避免 OOM.
    本测试测量压缩过程中的内存变化, 验证内存增长在可控范围内.

    注意: psutil 未安装时跳过本测试.
    """
    pytest.importorskip("psutil")
    import psutil

    process = psutil.Process()

    from unittest.mock import AsyncMock, MagicMock

    settings = get_settings()
    original_threshold = settings.compression_threshold
    try:
        settings.compression_threshold = 10000

        messages = _generate_test_messages(100000)

        mock_llm = MagicMock()
        mock_llm.achat = AsyncMock(return_value=MagicMock(content="测试摘要内容"))

        from src.skills.researcher.context_manager import ContextManager

        cm = ContextManager(settings)
        cm._llm = mock_llm

        before_memory = process.memory_info().rss / (1024 * 1024)

        start = time.perf_counter()
        compressed = await cm.compress_messages(messages)
        elapsed = time.perf_counter() - start

        after_memory = process.memory_info().rss / (1024 * 1024)
        memory_increase = after_memory - before_memory

        assert len(compressed) > 0, "压缩后消息列表为空"
        assert memory_increase < 100.0, (
            f"上下文压缩内存增长 {memory_increase:.1f}MB 超过阈值 100MB "
            f"(前={before_memory:.1f}MB 后={after_memory:.1f}MB)"
        )
        print(
            f"\n[context_compression_memory] 内存增长={memory_increase:.1f}MB "
            f"(前={before_memory:.1f}MB 后={after_memory:.1f}MB) 耗时={elapsed:.3f}s"
        )
    finally:
        settings.compression_threshold = original_threshold
