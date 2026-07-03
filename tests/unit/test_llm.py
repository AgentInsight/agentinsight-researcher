"""单元测试: LLM 网关.

验证 LLMClient 三级模型路由、API Key 映射、token 上限.
不实际调用 LLM, 仅测试内部逻辑.
"""

from __future__ import annotations

import pytest

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
    """测试按 LiteLLM 路由前缀获取 API Key."""
    settings = Settings(
        deepseek_api_key="ds-key",
        openai_api_key="oa-key",
        anthropic_api_key="an-key",
        zhipu_api_key="zp-key",
        _env_file=None,
    )
    client = LLMClient(settings)
    assert client._get_api_key("deepseek/deepseek-chat") == "ds-key"
    assert client._get_api_key("openai/gpt-4o") == "oa-key"
    assert client._get_api_key("anthropic/claude-3") == "an-key"
    assert client._get_api_key("zhipu/glm-4") == "zp-key"
    assert client._get_api_key("unknown/model") is None


def test_cost_computation():
    """测试成本计算."""
    settings = Settings(_env_file=None)
    client = LLMClient(settings)
    # deepseek-chat: 0.0014/1k input + 0.0028/1k output
    cost = client._compute_cost("deepseek/deepseek-chat", 1000, 1000)
    assert cost == pytest.approx(0.0014 + 0.0028)

    # 未知模型用默认费率
    cost = client._compute_cost("unknown/model", 1000, 1000)
    assert cost == pytest.approx(0.001 + 0.002)
