"""单元测试: PersonaConfig 闲聊 Persona 配置.

验证 src/config/researcher/persona.py:
- PersonaConfig 默认值 (identity/tone/boundaries/signature)
- PersonaConfig 自定义值覆盖
- pydantic BaseModel 校验行为
- 字段类型与可序列化

配置 SSOT, 业务代码禁止硬编码.
单元测试不依赖外部服务.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from src.config.researcher.persona import PersonaConfig

pytestmark = pytest.mark.unit


# ========== PersonaConfig 默认值 ==========


class TestPersonaDefaults:
    """PersonaConfig 默认值测试."""

    def test_default_identity(self) -> None:
        """默认 identity 应为研究助手定位."""
        persona = PersonaConfig()
        assert persona.identity == "你是一个专注于深度研究和分析的 AI 研究助手"

    def test_default_tone(self) -> None:
        """默认 tone 应含友好专业简洁 + emoji 描述."""
        persona = PersonaConfig()
        assert "友好" in persona.tone
        assert "专业" in persona.tone
        assert "emoji" in persona.tone

    def test_default_boundaries(self) -> None:
        """默认 boundaries 应含拒绝类型/引导策略/安全约束三段."""
        persona = PersonaConfig()
        assert "拒绝类型" in persona.boundaries
        assert "引导策略" in persona.boundaries
        assert "安全约束" in persona.boundaries

    def test_default_signature(self) -> None:
        """默认 signature 应为 AgentInsight Researcher."""
        persona = PersonaConfig()
        assert persona.signature == "AgentInsight Researcher"

    def test_default_boundaries_contains_sensitive_topics(self) -> None:
        """默认 boundaries 应明确拒绝涉政/涉黄/涉暴话题."""
        persona = PersonaConfig()
        assert "涉政" in persona.boundaries
        assert "涉黄" in persona.boundaries
        assert "涉暴" in persona.boundaries

    def test_default_boundaries_contains_medical_legal_disclaimer(self) -> None:
        """默认 boundaries 应拒绝医疗诊断/法律建议."""
        persona = PersonaConfig()
        assert "医疗诊断" in persona.boundaries
        assert "法律建议" in persona.boundaries

    def test_default_boundaries_mentions_pii(self) -> None:
        """默认 boundaries 应明确不输出 PII."""
        persona = PersonaConfig()
        assert "PII" in persona.boundaries


# ========== PersonaConfig 自定义值 ==========


class TestPersonaCustom:
    """PersonaConfig 自定义值覆盖测试."""

    def test_custom_identity(self) -> None:
        """显式传入 identity 应覆盖默认."""
        persona = PersonaConfig(identity="你是一个金融分析助手")
        assert persona.identity == "你是一个金融分析助手"

    def test_custom_tone(self) -> None:
        """显式传入 tone 应覆盖默认."""
        persona = PersonaConfig(tone="严肃正式")
        assert persona.tone == "严肃正式"

    def test_custom_boundaries(self) -> None:
        """显式传入 boundaries 应覆盖默认."""
        custom_boundaries = "仅回答金融相关问题"
        persona = PersonaConfig(boundaries=custom_boundaries)
        assert persona.boundaries == custom_boundaries

    def test_custom_signature(self) -> None:
        """显式传入 signature 应覆盖默认."""
        persona = PersonaConfig(signature="金融专家 Agent")
        assert persona.signature == "金融专家 Agent"

    def test_partial_custom_only_changes_specified_fields(self) -> None:
        """部分覆盖应保留未指定字段的默认值."""
        persona = PersonaConfig(identity="金融助手")
        # identity 已覆盖
        assert persona.identity == "金融助手"
        # 其他字段应保持默认
        assert persona.tone == "友好、专业、简洁, 适度使用 emoji (1-2 个/回复)"
        assert persona.signature == "AgentInsight Researcher"

    def test_all_fields_custom(self) -> None:
        """所有字段同时自定义."""
        persona = PersonaConfig(
            identity="法律咨询助手",
            tone="严谨、专业",
            boundaries="仅回答法律咨询, 不提供具体法律建议",
            signature="法律 Agent",
        )
        assert persona.identity == "法律咨询助手"
        assert persona.tone == "严谨、专业"
        assert persona.boundaries == "仅回答法律咨询, 不提供具体法律建议"
        assert persona.signature == "法律 Agent"


# ========== PersonaConfig pydantic 行为 ==========


class TestPersonaPydanticBehavior:
    """PersonaConfig pydantic BaseModel 行为测试."""

    def test_is_pydantic_model(self) -> None:
        """PersonaConfig 应继承 pydantic BaseModel."""
        assert issubclass(PersonaConfig, BaseModel)

    def test_model_fields_count(self) -> None:
        """应有 4 个字段 (identity/tone/boundaries/signature)."""
        fields = PersonaConfig.model_fields
        assert len(fields) == 4
        assert "identity" in fields
        assert "tone" in fields
        assert "boundaries" in fields
        assert "signature" in fields

    def test_model_dump_returns_dict(self) -> None:
        """model_dump() 应返回字段字典."""
        persona = PersonaConfig()
        dumped = persona.model_dump()
        assert isinstance(dumped, dict)
        assert set(dumped.keys()) == {"identity", "tone", "boundaries", "signature"}

    def test_model_dump_roundtrip(self) -> None:
        """model_dump + PersonaConfig(**dump) 应等价于原对象."""
        original = PersonaConfig(identity="金融助手", tone="严肃")
        dumped = original.model_dump()
        restored = PersonaConfig(**dumped)
        assert restored == original

    def test_equality_between_same_instances(self) -> None:
        """两个相同字段的 PersonaConfig 应相等."""
        p1 = PersonaConfig()
        p2 = PersonaConfig()
        assert p1 == p2

    def test_inequality_between_different_instances(self) -> None:
        """不同字段的 PersonaConfig 应不等."""
        p1 = PersonaConfig()
        p2 = PersonaConfig(identity="不同身份")
        assert p1 != p2

    def test_field_assignment_supported(self) -> None:
        """pydantic v2 默认支持字段赋值 (validate_assignment=False)."""
        persona = PersonaConfig()
        # 应可重新赋值
        persona.identity = "新身份"
        assert persona.identity == "新身份"

    def test_model_construct_bypasses_validation(self) -> None:
        """model_construct 应绕过校验直接构造 (用于性能敏感场景)."""
        persona = PersonaConfig.model_construct(
            identity="x",
            tone="y",
            boundaries="z",
            signature="w",
        )
        assert persona.identity == "x"
        assert persona.signature == "w"


# ========== PersonaConfig 字段类型 ==========


class TestPersonaFieldTypes:
    """PersonaConfig 字段类型测试."""

    def test_identity_is_str(self) -> None:
        """identity 应为 str 类型."""
        persona = PersonaConfig()
        assert isinstance(persona.identity, str)

    def test_tone_is_str(self) -> None:
        """tone 应为 str 类型."""
        persona = PersonaConfig()
        assert isinstance(persona.tone, str)

    def test_boundaries_is_str(self) -> None:
        """boundaries 应为 str 类型."""
        persona = PersonaConfig()
        assert isinstance(persona.boundaries, str)

    def test_signature_is_str(self) -> None:
        """signature 应为 str 类型."""
        persona = PersonaConfig()
        assert isinstance(persona.signature, str)

    def test_empty_string_identity_accepted(self) -> None:
        """空字符串 identity 应被接受 (无 min_length 约束)."""
        persona = PersonaConfig(identity="")
        assert persona.identity == ""

    def test_long_string_accepted(self) -> None:
        """长字符串应被接受 (无 max_length 约束)."""
        long_text = "A" * 10000
        persona = PersonaConfig(identity=long_text)
        assert persona.identity == long_text


# ========== PersonaConfig 序列化 ==========


class TestPersonaSerialization:
    """PersonaConfig 序列化测试."""

    def test_model_dump_json_returns_json_string(self) -> None:
        """model_dump_json() 应返回 JSON 字符串."""
        persona = PersonaConfig()
        json_str = persona.model_dump_json()
        assert isinstance(json_str, str)
        assert "identity" in json_str
        assert "AgentInsight Researcher" in json_str

    def test_model_validate_json_roundtrip(self) -> None:
        """JSON 序列化 → 反序列化应等价于原对象."""
        original = PersonaConfig(identity="金融助手", signature="金融 Agent")
        json_str = original.model_dump_json()
        restored = PersonaConfig.model_validate_json(json_str)
        assert restored == original

    def test_model_validate_dict(self) -> None:
        """model_validate 应接受 dict 输入."""
        data = {"identity": "医疗助手", "tone": "温和", "boundaries": "x", "signature": "y"}
        persona = PersonaConfig.model_validate(data)
        assert persona.identity == "医疗助手"
        assert persona.tone == "温和"


# ========== 实际使用场景模拟 ==========


class TestPersonaUsageScenarios:
    """PersonaConfig 实际使用场景测试."""

    def test_persona_for_finance_industry(self) -> None:
        """金融行业 persona 配置."""
        persona = PersonaConfig(
            identity="你是一个专注于金融市场的分析助手",
            tone="严谨、专业、数据导向",
            boundaries="不提供具体投资建议, 不预测股票涨跌",
            signature="金融研究 Agent",
        )
        assert "金融" in persona.identity
        assert "严谨" in persona.tone

    def test_persona_for_medical_research(self) -> None:
        """医学研究 persona 配置."""
        persona = PersonaConfig(
            identity="你是一个专注于医学文献研究的助手",
            boundaries="不提供医疗诊断, 不替代医生意见",
        )
        assert "医学" in persona.identity
        assert "诊断" in persona.boundaries

    def test_persona_for_legal_research(self) -> None:
        """法律研究 persona 配置."""
        persona = PersonaConfig(
            identity="你是一个法律文献检索助手",
            boundaries="不提供具体法律建议, 不替代律师",
        )
        assert "法律" in persona.identity
        assert "法律建议" in persona.boundaries

    def test_persona_preserves_default_when_passed_to_template(
        self,
    ) -> None:
        """默认 persona 可作为 Jinja2 模板变量 (含 4 字段)."""
        persona = PersonaConfig()
        # 模拟 Jinja2 模板渲染时访问字段
        template_vars = {
            "identity": persona.identity,
            "tone": persona.tone,
            "boundaries": persona.boundaries,
            "signature": persona.signature,
        }
        assert all(isinstance(v, str) for v in template_vars.values())
        assert template_vars["signature"] == "AgentInsight Researcher"
