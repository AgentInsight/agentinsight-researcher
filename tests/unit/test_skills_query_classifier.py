"""单元测试: QueryIntentClassifier 规则层分类.

验证 _rule_classify (长度/纯数字/纯标点) 与 QueryIntent 枚举,
以及 _SHORT_QUERY_SEED (178 个种子) + _SHORT_QUERY_SEED_VERSION.
P1-Future-07: 新增 OFF_TOPIC 意图 + _CHITCHAT_PATTERNS + _OFF_TOPIC_SEED (108 个种子).
AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
"""

from __future__ import annotations

import pytest

from src.config.settings import Settings
from src.skills.researcher.query_classifier import (
    _CHITCHAT_PATTERNS,
    _OFF_TOPIC_SEED,
    _OFF_TOPIC_SEED_VERSION,
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


def test_query_intent_off_topic_value() -> None:
    """测试 OFF_TOPIC 枚举值为 'off_topic'."""
    assert QueryIntent.OFF_TOPIC.value == "off_topic"


def test_query_intent_has_four_members() -> None:
    """测试 QueryIntent 含 RESEARCH / CHAT / SHORT_QUERY / OFF_TOPIC 四个成员 (P1-Future-07)."""
    members = list(QueryIntent)
    assert len(members) == 4
    assert QueryIntent.RESEARCH in members
    assert QueryIntent.CHAT in members
    assert QueryIntent.SHORT_QUERY in members
    assert QueryIntent.OFF_TOPIC in members


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


# ========== _OFF_TOPIC_SEED (P1-Future-07) ==========


def test_off_topic_seed_has_108_entries() -> None:
    """测试 _OFF_TOPIC_SEED 含 118 个不重复种子 (P1-Future-07, V1 含 10 项其他闲聊补充)."""
    assert len(_OFF_TOPIC_SEED) == 118


def test_off_topic_seed_unique() -> None:
    """测试所有离题种子不重复."""
    assert len(set(_OFF_TOPIC_SEED)) == len(_OFF_TOPIC_SEED)


def test_off_topic_seed_all_non_empty_strings() -> None:
    """测试所有离题种子为非空字符串."""
    for seed in _OFF_TOPIC_SEED:
        assert isinstance(seed, str)
        assert len(seed) > 0


def test_off_topic_seed_version() -> None:
    """测试 _OFF_TOPIC_SEED_VERSION == 'v1.0'."""
    assert _OFF_TOPIC_SEED_VERSION == "v1.0"


def test_off_topic_seed_contains_identity_queries() -> None:
    """测试离题种子含身份询问 ('你叫什么名字' / 'how old are you')."""
    assert "你叫什么名字" in _OFF_TOPIC_SEED
    assert "how old are you" in _OFF_TOPIC_SEED


def test_off_topic_seed_contains_emotional_queries() -> None:
    """测试离题种子含情绪表达 ('我好开心' / 'i'm bored')."""
    assert "我好开心" in _OFF_TOPIC_SEED
    assert "i'm bored" in _OFF_TOPIC_SEED


def test_off_topic_seed_no_overlap_with_short_query_seed() -> None:
    """测试离题种子与短查询种子无重复 (互补设计)."""
    short_set = set(_SHORT_QUERY_SEED)
    off_topic_set = set(_OFF_TOPIC_SEED)
    overlap = short_set & off_topic_set
    assert overlap == set(), f"种子重复: {overlap}"


# ========== _CHITCHAT_PATTERNS (P1-Future-07) ==========


def test_chitchat_patterns_non_empty() -> None:
    """测试 _CHITCHAT_PATTERNS 非空."""
    assert len(_CHITCHAT_PATTERNS) > 0


def test_chitchat_patterns_all_compiled() -> None:
    """测试 _CHITCHAT_PATTERNS 全部为编译后的正则对象."""
    import re

    for pattern in _CHITCHAT_PATTERNS:
        assert isinstance(pattern, re.Pattern)


# ========== _rule_classify OFF_TOPIC (闲聊正则) ==========


def test_rule_classify_identity_question_off_topic(classifier: QueryIntentClassifier) -> None:
    """测试身份询问 → OFF_TOPIC (reason=chitchat_pattern)."""
    result = classifier._rule_classify("你叫什么名字")
    assert result is not None
    assert result.intent == QueryIntent.OFF_TOPIC
    assert result.reason == "chitchat_pattern"


def test_rule_classify_age_question_off_topic(classifier: QueryIntentClassifier) -> None:
    """测试年龄询问 → OFF_TOPIC."""
    result = classifier._rule_classify("你多大了")
    assert result is not None
    assert result.intent == QueryIntent.OFF_TOPIC


def test_rule_classify_joke_request_off_topic(classifier: QueryIntentClassifier) -> None:
    """测试讲笑话请求 → OFF_TOPIC."""
    result = classifier._rule_classify("讲个笑话")
    assert result is not None
    assert result.intent == QueryIntent.OFF_TOPIC


def test_rule_classify_arithmetic_question_off_topic(classifier: QueryIntentClassifier) -> None:
    """测试算术问题 → OFF_TOPIC."""
    result = classifier._rule_classify("1+1等于几")
    assert result is not None
    assert result.intent == QueryIntent.OFF_TOPIC


def test_rule_classify_weather_question_off_topic(classifier: QueryIntentClassifier) -> None:
    """测试天气询问 (句子形式) → OFF_TOPIC."""
    result = classifier._rule_classify("今天天气怎么样")
    assert result is not None
    assert result.intent == QueryIntent.OFF_TOPIC


def test_rule_classify_emotional_expression_off_topic(classifier: QueryIntentClassifier) -> None:
    """测试情绪表达 → OFF_TOPIC."""
    result = classifier._rule_classify("我好开心")
    assert result is not None
    assert result.intent == QueryIntent.OFF_TOPIC


def test_rule_classify_compliment_off_topic(classifier: QueryIntentClassifier) -> None:
    """测试夸奖 → OFF_TOPIC."""
    result = classifier._rule_classify("你真聪明")
    assert result is not None
    assert result.intent == QueryIntent.OFF_TOPIC


def test_rule_classify_chat_companion_off_topic(classifier: QueryIntentClassifier) -> None:
    """测试陪伴聊天 → OFF_TOPIC."""
    result = classifier._rule_classify("陪我聊天")
    assert result is not None
    assert result.intent == QueryIntent.OFF_TOPIC


def test_rule_classify_off_topic_disabled_returns_none(
    classifier: QueryIntentClassifier,
) -> None:
    """测试 off_topic_enabled=False 时闲聊正则禁用, 闲聊查询不被判为 OFF_TOPIC.

    注: 短查询规则仍可能命中 (如 "讲个笑话" 长度 4 纯中文, 触发 single_word_short).
    测试用更长查询 "请给我讲一个笑话吧" (长度 9), 既不触发闲聊正则也不触发短查询规则.
    """
    classifier.settings = Settings(_env_file=None, off_topic_enabled=False)
    # 闲聊正则禁用后, 长查询不应被规则层拦截
    result = classifier._rule_classify("请给我讲一个笑话吧")
    assert result is None


# ========== _rule_classify 研究查询不被误判 ==========


def test_rule_classify_research_query_not_off_topic(classifier: QueryIntentClassifier) -> None:
    """测试研究查询不被误判为 OFF_TOPIC."""
    result = classifier._rule_classify("分析新能源汽车市场的发展趋势")
    assert result is None


def test_rule_classify_tech_query_not_off_topic(classifier: QueryIntentClassifier) -> None:
    """测试技术查询不被误判为 OFF_TOPIC."""
    result = classifier._rule_classify("比较 React 和 Vue 的性能差异")
    assert result is None
