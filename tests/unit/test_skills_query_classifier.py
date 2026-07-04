"""单元测试: QueryIntentClassifier 规则层分类.

验证 _rule_classify (长度/纯数字/纯标点) 与 QueryIntent 枚举,
以及 _SHORT_QUERY_SEED (178 个种子) + _SHORT_QUERY_SEED_VERSION.
AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
"""

from __future__ import annotations

import pytest

from src.config.settings import Settings
from src.skills.researcher.query_classifier import (
    _SHORT_QUERY_SEED,
    _SHORT_QUERY_SEED_VERSION,
    QueryIntent,
    QueryIntentClassifier,
)

pytestmark = pytest.mark.unit


@pytest.fixture()
def classifier() -> QueryIntentClassifier:
    """构造 Classifier 实例 (跳过 LLM/Embeddings/Qdrant 依赖).

    _rule_classify 是纯函数, 仅访问 settings.short_query_enabled / short_query_min_length,
    不调用 LLM/Embeddings/Qdrant. 通过 __new__ 跳过 __init__ 避免外部依赖初始化.
    """
    obj = QueryIntentClassifier.__new__(QueryIntentClassifier)
    obj.settings = Settings(_env_file=None)
    return obj


# ========== QueryIntent 枚举 ==========


def test_query_intent_research_value() -> None:
    """测试 RESEARCH 枚举值为 'research'."""
    assert QueryIntent.RESEARCH.value == "research"


def test_query_intent_chat_value() -> None:
    """测试 CHAT 枚举值为 'chat'."""
    assert QueryIntent.CHAT.value == "chat"


def test_query_intent_short_query_value() -> None:
    """测试 SHORT_QUERY 枚举值为 'short_query'."""
    assert QueryIntent.SHORT_QUERY.value == "short_query"


def test_query_intent_has_three_members() -> None:
    """测试 QueryIntent 仅含 RESEARCH / CHAT / SHORT_QUERY 三个成员."""
    members = list(QueryIntent)
    assert len(members) == 3
    assert QueryIntent.RESEARCH in members
    assert QueryIntent.CHAT in members
    assert QueryIntent.SHORT_QUERY in members


# ========== _SHORT_QUERY_SEED ==========


def test_short_query_seed_has_90_entries() -> None:
    """测试 _SHORT_QUERY_SEED 含 178 个不重复种子 (V6 短查询优化扩展)."""
    assert len(_SHORT_QUERY_SEED) == 178


def test_short_query_seed_unique() -> None:
    """测试所有种子不重复."""
    assert len(set(_SHORT_QUERY_SEED)) == len(_SHORT_QUERY_SEED)


def test_short_query_seed_all_non_empty_strings() -> None:
    """测试所有种子为非空字符串."""
    for seed in _SHORT_QUERY_SEED:
        assert isinstance(seed, str)
        assert len(seed) > 0


def test_short_query_seed_version() -> None:
    """测试 _SHORT_QUERY_SEED_VERSION == 'v6.0' (V6 短查询优化升级版本)."""
    assert _SHORT_QUERY_SEED_VERSION == "v6.0"


def test_short_query_seed_contains_greetings() -> None:
    """测试种子含问候类 ('你好' / 'hello')."""
    assert "你好" in _SHORT_QUERY_SEED
    assert "hello" in _SHORT_QUERY_SEED


def test_short_query_seed_contains_digits() -> None:
    """测试种子含纯数字 ('1' / '123')."""
    assert "1" in _SHORT_QUERY_SEED
    assert "123" in _SHORT_QUERY_SEED


# ========== _rule_classify 长查询 ==========


def test_rule_classify_long_query_returns_none(classifier: QueryIntentClassifier) -> None:
    """测试长查询 (>= short_query_min_length, 非纯数字/标点) 返回 None (进入下层)."""
    # short_query_min_length 默认 2, 此查询长度远大于
    result = classifier._rule_classify("研究中国新能源汽车行业发展现状")
    assert result is None


def test_rule_classify_normal_english_query_returns_none(
    classifier: QueryIntentClassifier,
) -> None:
    """测试正常英文查询返回 None."""
    result = classifier._rule_classify("analyze the AI market trends in 2024")
    assert result is None


# ========== _rule_classify 短查询 (长度) ==========


def test_rule_classify_short_query_below_min_length(
    classifier: QueryIntentClassifier,
) -> None:
    """测试长度 < short_query_min_length → SHORT_QUERY (reason=length_below_min)."""
    # short_query_min_length 默认 2, 单字符 "a" 长度 1 < 2
    result = classifier._rule_classify("a")
    assert result is not None
    assert result.intent == QueryIntent.SHORT_QUERY
    assert result.reason == "length_below_min"


def test_rule_classify_single_character_short(classifier: QueryIntentClassifier) -> None:
    """测试单字符返回 SHORT_QUERY."""
    result = classifier._rule_classify("x")
    assert result is not None
    assert result.intent == QueryIntent.SHORT_QUERY


# ========== _rule_classify 纯数字 ==========


def test_rule_classify_pure_digits_short(classifier: QueryIntentClassifier) -> None:
    """测试纯数字 → SHORT_QUERY (reason=pure_digits)."""
    # 数字长度 >= 2 (避免触发 length_below_min), 但纯数字仍应判 SHORT_QUERY
    result = classifier._rule_classify("12345")
    assert result is not None
    assert result.intent == QueryIntent.SHORT_QUERY
    assert result.reason == "pure_digits"


def test_rule_classify_single_digit_short(classifier: QueryIntentClassifier) -> None:
    """测试单数字 '1' (优先匹配 length_below_min)."""
    result = classifier._rule_classify("1")
    assert result is not None
    assert result.intent == QueryIntent.SHORT_QUERY
    # '1' 长度 1 < 2, 先命中 length_below_min
    assert result.reason == "length_below_min"


# ========== _rule_classify 纯标点 ==========


def test_rule_classify_pure_punctuation_short(classifier: QueryIntentClassifier) -> None:
    """测试纯标点 → SHORT_QUERY (reason=pure_punctuation)."""
    # 标点长度 >= 2, 不触发 length_below_min
    result = classifier._rule_classify("???")
    assert result is not None
    assert result.intent == QueryIntent.SHORT_QUERY
    assert result.reason == "pure_punctuation"


def test_rule_classify_ellipsis_short(classifier: QueryIntentClassifier) -> None:
    """测试省略号 '...' 判 SHORT_QUERY."""
    result = classifier._rule_classify("...")
    assert result is not None
    assert result.intent == QueryIntent.SHORT_QUERY


def test_rule_classify_mixed_alnum_not_short(classifier: QueryIntentClassifier) -> None:
    """测试字母+数字混合查询返回 None (非纯数字/标点)."""
    result = classifier._rule_classify("gpt4 model")
    assert result is None


# ========== _rule_classify disabled ==========


def test_rule_classify_disabled_returns_none(classifier: QueryIntentClassifier) -> None:
    """测试 short_query_enabled=False 时规则层禁用, 始终返回 None."""
    classifier.settings = Settings(_env_file=None, short_query_enabled=False)
    # 即使纯数字, 规则层禁用后也应返回 None
    result = classifier._rule_classify("123")
    assert result is None


# ========== _rule_classify 空查询 ==========


def test_rule_classify_empty_string_short(classifier: QueryIntentClassifier) -> None:
    """测试空串触发长度规则 → SHORT_QUERY."""
    result = classifier._rule_classify("")
    assert result is not None
    assert result.intent == QueryIntent.SHORT_QUERY


def test_rule_classify_whitespace_only_short(classifier: QueryIntentClassifier) -> None:
    """测试纯空白 → SHORT_QUERY (length_below_min, strip 后长度 0)."""
    result = classifier._rule_classify("   ")
    assert result is not None
    assert result.intent == QueryIntent.SHORT_QUERY
