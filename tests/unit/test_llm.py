"""单元测试: LLM 网关.

验证 LLMClient 三级模型路由、API Key 映射、token 上限、分步成本追踪、降级链.
不实际调用 LLM, 仅测试内部逻辑 (降级链测试用伪造 litellm 模块).
"""

from __future__ import annotations

import types
from typing import Any

import pytest

from src.common.llm_key_resolver import resolve_api_key
from src.config.settings import Settings
from src.llm.client import LLMClient, LLMTier


def test_llm_tier_model_mapping():
    """测试三级 LLM 模型映射."""
    settings = Settings(
        fast_llm="deepseek/deepseek-chat",
        smart_llm="deepseek/deepseek-chat",
        strategic_llm="deepseek/deepseek-reasoner",
        _env_file=None,
    )
    client = LLMClient(settings)
    assert client._get_model(LLMTier.FAST) == "deepseek/deepseek-chat"
    assert client._get_model(LLMTier.SMART) == "deepseek/deepseek-chat"
    assert client._get_model(LLMTier.STRATEGIC) == "deepseek/deepseek-reasoner"


def test_llm_tier_token_limit():
    """测试三级 LLM token 上限."""
    settings = Settings(
        fast_token_limit=3000,
        smart_token_limit=6000,
        strategic_token_limit=4000,
        _env_file=None,
    )
    client = LLMClient(settings)
    assert client._get_token_limit(LLMTier.FAST) == 3000
    assert client._get_token_limit(LLMTier.SMART) == 6000
    assert client._get_token_limit(LLMTier.STRATEGIC) == 4000


def test_api_key_mapping_by_prefix():
    """测试按 LiteLLM 路由前缀获取 API Key (P1-3: 抽取到 common/llm_key_resolver)."""
    settings = Settings(
        deepseek_api_key="ds-key",
        openai_api_key="oa-key",
        anthropic_api_key="an-key",
        zhipu_api_key="zp-key",
        _env_file=None,
    )
    client = LLMClient(settings)
    assert resolve_api_key("deepseek/deepseek-chat", client.settings) == "ds-key"
    assert resolve_api_key("openai/gpt-4o", client.settings) == "oa-key"
    assert resolve_api_key("anthropic/claude-3", client.settings) == "an-key"
    assert resolve_api_key("zhipu/glm-4", client.settings) == "zp-key"
    assert resolve_api_key("unknown/model", client.settings) is None


def test_cost_computation():
    """测试成本计算."""
    settings = Settings(_env_file=None)
    client = LLMClient(settings)
    # deepseek-chat: 0.0014/1k input + 0.0028/1k output
    breakdown = client._compute_cost("deepseek/deepseek-chat", 1000, 1000)
    assert breakdown["input_cost"] == pytest.approx(0.0014)
    assert breakdown["output_cost"] == pytest.approx(0.0028)
    assert breakdown["total_cost"] == pytest.approx(0.0014 + 0.0028)

    # 模型名前缀匹配: deepseek-chat-2026-01-01 命中 deepseek-chat
    breakdown = client._compute_cost("deepseek/deepseek-chat-2026-01-01", 1000, 1000)
    assert breakdown["total_cost"] == pytest.approx(0.0014 + 0.0028)

    # 未知模型: 命中失败返回 0.0 (不再用兜底 0.001/0.002, 避免误算)
    breakdown = client._compute_cost("unknown/model", 1000, 1000)
    assert breakdown["total_cost"] == 0.0
    assert breakdown["input_cost"] == 0.0
    assert breakdown["output_cost"] == 0.0


def test_get_session_cost_accumulation():
    """测试会话级累计成本统计 + P1-Future-01 step_costs 分步分布."""
    settings = Settings(_env_file=None)
    client = LLMClient(settings)
    # 初始状态 (含 step_costs 空字典)
    assert client.get_session_cost("test-session") == {
        "call_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "step_costs": {},
    }
    # 手动累加 (模拟 achat 成功后调用, 按 step 分步累计)
    client._accumulate("test-session", "planner", 1000, 500, 0.0028)
    client._accumulate("test-session", "researcher", 2000, 800, 0.0056)
    client._accumulate("test-session", "planner", 500, 200, 0.0014)
    stats = client.get_session_cost("test-session")
    assert stats["call_count"] == 3
    assert stats["input_tokens"] == 3500
    assert stats["output_tokens"] == 1500
    assert stats["cost_usd"] == pytest.approx(0.0098)
    # step_costs 分布: planner 累加两次, researcher 一次
    assert stats["step_costs"]["planner"] == pytest.approx(0.0042)
    assert stats["step_costs"]["researcher"] == pytest.approx(0.0056)


def test_step_costs_rounding():
    """P1-Future-01: step_costs 保留 6 位小数, 多次累加不溢出精度."""
    settings = Settings(_env_file=None)
    client = LLMClient(settings)
    # 累加会产生浮点误差的值
    client._accumulate("test-session", "step", 0, 0, 0.0001)
    client._accumulate("test-session", "step", 0, 0, 0.0002)
    client._accumulate("test-session", "step", 0, 0, 0.0003)
    stats = client.get_session_cost("test-session")
    assert stats["step_costs"]["step"] == pytest.approx(0.0006)


# ========== P1-Future-05 LLM 降级链测试 (伪造 litellm) ==========


class _FakeUsage:
    """伪造 litellm usage 对象."""

    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeResponse:
    """伪造 litellm 非流式响应."""

    def __init__(self, content: str, input_tokens: int, output_tokens: int) -> None:
        self.usage = _FakeUsage(input_tokens, output_tokens)
        self.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=content))]


class _FakeChunk:
    """伪造 litellm 流式 chunk."""

    def __init__(self, delta_content: str | None, usage: _FakeUsage | None = None) -> None:
        if delta_content is not None:
            self.choices = [
                types.SimpleNamespace(delta=types.SimpleNamespace(content=delta_content))
            ]
        else:
            self.choices = []
        self.usage = usage


class _FakeStream:
    """伪造 litellm 流式响应 (async iterable)."""

    def __init__(self, chunks: list[_FakeChunk]) -> None:
        self._chunks = list(chunks)
        self._idx = 0

    def __aiter__(self) -> _FakeStream:
        return self

    async def __anext__(self) -> _FakeChunk:
        if self._idx >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._idx]
        self._idx += 1
        return chunk


def _install_fake_litellm(
    monkeypatch: pytest.MonkeyPatch,
    side_effect: Any,
) -> list[dict[str, Any]]:
    """注入伪造 litellm 模块到 src.llm.client 的命名空间.

    直接 patch src.llm.client.litellm 引用 (而非 sys.modules["litellm"]),
    因为 client.py 在 import 时已绑定 litellm 模块对象, 替换 sys.modules
    不会影响已绑定的引用. 真实 litellm 在某些版本缺少 use_litellm_proxy 属性,
    会触发 GetLLMProvider 异常, 故必须替换 client 模块内的 litellm 引用.

    side_effect(model, call_index) -> Exception (抛出) | _FakeResponse | _FakeStream.
    返回调用记录列表 (kwargs).
    """
    calls: list[dict[str, Any]] = []

    async def _fake_acompletion(**kwargs: Any) -> Any:
        calls.append(kwargs)
        result = side_effect(kwargs.get("model"), len(calls))
        if isinstance(result, Exception):
            raise result
        return result

    fake = types.ModuleType("litellm")
    fake.acompletion = _fake_acompletion
    # 直接 patch client 模块内的 litellm 引用 (而非 sys.modules)
    monkeypatch.setattr("src.llm.client.litellm", fake)
    return calls


def _tiered_settings() -> Settings:
    """三级 LLM 配置 (strategic/smart 用不同模型以便区分降级)."""
    return Settings(
        strategic_llm="deepseek/deepseek-reasoner",
        smart_llm="deepseek/deepseek-chat",
        fast_llm="deepseek/deepseek-chat",
        _env_file=None,
    )


@pytest.mark.asyncio
async def test_achat_fallback_strategic_to_smart(monkeypatch: pytest.MonkeyPatch):
    """P1-Future-05: achat strategic 失败 → 降级到 smart 成功."""
    client = LLMClient(_tiered_settings())

    def side_effect(model: str, n: int) -> Any:
        if n == 1:
            return RuntimeError("strategic tier down")
        return _FakeResponse("ok", 100, 50)

    calls = _install_fake_litellm(monkeypatch, side_effect)

    response = await client.achat(
        [{"role": "user", "content": "hi"}],
        tier=LLMTier.STRATEGIC,
        step="test_step",
    )
    assert response.content == "ok"
    assert response.model == "deepseek/deepseek-chat"  # 降级到 smart
    assert len(calls) == 2  # strategic + smart
    # step_costs 记录到 test_step
    stats = client.get_session_cost()
    assert stats["call_count"] == 1
    assert stats["step_costs"] == {"test_step": response.cost_usd}


@pytest.mark.asyncio
async def test_achat_fallback_exhausted(monkeypatch: pytest.MonkeyPatch):
    """P1-Future-05: 降级链耗尽 (FAST 也失败) 时抛出异常, 不计入成本."""
    client = LLMClient(_tiered_settings())

    def side_effect(model: str, n: int) -> Any:
        return RuntimeError("all tiers down")

    calls = _install_fake_litellm(monkeypatch, side_effect)

    with pytest.raises(RuntimeError, match="all tiers down"):
        await client.achat(
            [{"role": "user", "content": "hi"}],
            tier=LLMTier.STRATEGIC,
            step="test_step",
        )
    assert len(calls) == 3  # strategic + smart + fast 全部尝试
    # 失败不计入成本
    stats = client.get_session_cost()
    assert stats["call_count"] == 0
    assert stats["step_costs"] == {}


@pytest.mark.asyncio
async def test_achat_no_fallback_on_success(monkeypatch: pytest.MonkeyPatch):
    """P1-Future-05: SMART 直接成功时不降级."""
    client = LLMClient(_tiered_settings())

    def side_effect(model: str, n: int) -> Any:
        return _FakeResponse("ok", 100, 50)

    calls = _install_fake_litellm(monkeypatch, side_effect)

    response = await client.achat(
        [{"role": "user", "content": "hi"}],
        tier=LLMTier.SMART,
        step="writer",
    )
    assert response.model == "deepseek/deepseek-chat"
    assert len(calls) == 1  # 只调用一次, 未降级
    assert client.get_session_cost()["step_costs"] == {"writer": response.cost_usd}


@pytest.mark.asyncio
async def test_achat_stream_fallback_strategic_to_smart(monkeypatch: pytest.MonkeyPatch):
    """P1-Future-05: achat_stream 流式连接 strategic 失败 → 降级到 smart."""
    client = LLMClient(_tiered_settings())

    def side_effect(model: str, n: int) -> Any:
        if n == 1:
            return RuntimeError("strategic stream down")
        return _FakeStream(
            [
                _FakeChunk("Hello"),
                _FakeChunk(" world"),
                _FakeChunk(None, _FakeUsage(50, 10)),
            ]
        )

    calls = _install_fake_litellm(monkeypatch, side_effect)

    chunks = [
        delta
        async for delta in client.achat_stream(
            [{"role": "user", "content": "hi"}],
            tier=LLMTier.STRATEGIC,
            step="stream_step",
        )
    ]
    assert chunks == ["Hello", " world"]
    assert len(calls) == 2  # 降级一次
    stats = client.get_session_cost()
    assert stats["call_count"] == 1
    assert stats["step_costs"] == {"stream_step": stats["cost_usd"]}


@pytest.mark.asyncio
async def test_achat_stream_no_fallback_on_success(monkeypatch: pytest.MonkeyPatch):
    """P1-Future-05: achat_stream SMART 直接成功时不降级."""
    client = LLMClient(_tiered_settings())

    def side_effect(model: str, n: int) -> Any:
        return _FakeStream(
            [
                _FakeChunk("ok"),
                _FakeChunk(None, _FakeUsage(10, 5)),
            ]
        )

    calls = _install_fake_litellm(monkeypatch, side_effect)

    chunks = [
        delta
        async for delta in client.achat_stream(
            [{"role": "user", "content": "hi"}],
            tier=LLMTier.SMART,
            step="stream_writer",
        )
    ]
    assert chunks == ["ok"]
    assert len(calls) == 1
    stats = client.get_session_cost()
    # deepseek-chat: 10 input + 5 output → 0.000014 + 0.000014 = 2.8e-05
    assert stats["step_costs"] == {"stream_writer": stats["cost_usd"]}
    assert stats["cost_usd"] == pytest.approx(2.8e-05)
