"""单元测试: QueryIntentClassifier 规则层分类 + Redis 缓存层.

验证 _rule_classify (长度/纯数字/纯标点/闲聊正则) 与 QueryIntent 枚举,
以及 P1 Redis 缓存层 (_classify_with_cache) 与 P2 LLM prompt 强化.

QUERY_CLASSIFIER_FAST_LLM_OPTIMIZATION_PLAN.md P0/P1/P2 已实施:
- P0: 移除原第二层 Embeddings+Qdrant 语义匹配
- P1: 引入 Redis 分类结果缓存 (TTL 24h)
- P2: 强化 LLM prompt few-shot + 删除种子数据

单元测试在构建期执行, 不依赖外部服务 (Redis/Qdrant/LLM 全部 mock).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.skills.researcher.query_classifier import (
    _CHITCHAT_PATTERNS,
    _COMMON_SHORT_PHRASES,
    QueryIntent,
    QueryIntentClassifier,
    cleanup_legacy_chat_seeds,
)

pytestmark = pytest.mark.unit


@pytest.fixture()
def classifier() -> QueryIntentClassifier:
    """构造 Classifier 实例 (跳过 LLM 初始化, 仅测试规则层).

    _rule_classify 是纯函数, 仅访问 settings.short_query_enabled / short_query_min_length,
    不调用 LLM/Redis. 通过 __new__ 跳过 __init__ 避免外部依赖初始化.
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
    """测试 QueryIntent 含 RESEARCH / CHAT / SHORT_QUERY / OFF_TOPIC 四个成员."""
    members = list(QueryIntent)
    assert len(members) == 4
    assert QueryIntent.RESEARCH in members
    assert QueryIntent.CHAT in members
    assert QueryIntent.SHORT_QUERY in members
    assert QueryIntent.OFF_TOPIC in members


# ========== _COMMON_SHORT_PHRASES ==========


def test_common_short_phrases_non_empty() -> None:
    """测试 _COMMON_SHORT_PHRASES 非空."""
    assert len(_COMMON_SHORT_PHRASES) > 0


def test_common_short_phrases_contains_greetings() -> None:
    """测试常见短语含问候 ('你好' / 'hello')."""
    assert "你好" in _COMMON_SHORT_PHRASES
    assert "hello" in _COMMON_SHORT_PHRASES


def test_common_short_phrases_all_lowercase_for_english() -> None:
    """测试英文短语全部小写 (匹配时 query.lower())."""
    for phrase in _COMMON_SHORT_PHRASES:
        if any(c.isalpha() and ord(c) < 128 for c in phrase):
            # 含英文字母的短语应为小写
            assert phrase == phrase.lower(), f"英文短语未小写: {phrase}"


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


# ========== _CHITCHAT_PATTERNS ==========


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


# ========== _cache_key (P1) ==========


def test_cache_key_contains_agent_id(classifier: QueryIntentClassifier) -> None:
    """测试缓存 key 含 agent_id 前缀 (Redis 约定)."""
    key = classifier._cache_key("你好", has_report=False)
    assert classifier.settings.agent_name in key


def test_cache_key_contains_has_report_dimension(classifier: QueryIntentClassifier) -> None:
    """测试缓存 key 含 has_report 维度 (同一 query 在有/无报告上下文下分类可能不同)."""
    key_no_report = classifier._cache_key("分析量子计算", has_report=False)
    key_with_report = classifier._cache_key("分析量子计算", has_report=True)
    assert key_no_report != key_with_report


def test_cache_key_idempotent_for_same_input(classifier: QueryIntentClassifier) -> None:
    """测试相同输入生成相同缓存 key (幂等)."""
    key1 = classifier._cache_key("你好", has_report=False)
    key2 = classifier._cache_key("你好", has_report=False)
    assert key1 == key2


def test_cache_key_differs_by_query(classifier: QueryIntentClassifier) -> None:
    """测试不同 query 生成不同缓存 key."""
    key1 = classifier._cache_key("你好", has_report=False)
    key2 = classifier._cache_key("再见", has_report=False)
    assert key1 != key2


# ========== _classify_with_cache (P1, mock Redis + LLM) ==========


@pytest.fixture()
def classifier_with_mock_deps() -> QueryIntentClassifier:
    """构造带 mock LLM 的 Classifier (用于缓存层测试)."""
    settings = Settings(_env_file=None, query_classify_cache_enabled=True)
    llm = MagicMock()
    obj = QueryIntentClassifier(settings=settings, llm=llm)  # type: ignore[arg-type]
    return obj


@pytest.mark.asyncio
async def test_classify_with_cache_hit_returns_cached_intent(
    classifier_with_mock_deps: QueryIntentClassifier,
) -> None:
    """测试缓存命中时直接返回缓存结果, 不调 LLM (P1)."""
    # Mock Redis: 返回缓存的 "research"
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value="research")
    classifier_with_mock_deps._redis = mock_redis
    classifier_with_mock_deps._redis_initialized = True

    # Mock LLM (不应被调用)
    classifier_with_mock_deps._llm.achat = AsyncMock()

    intent, source = await classifier_with_mock_deps._classify_with_cache(
        "分析量子计算", has_report=False
    )

    assert intent == QueryIntent.RESEARCH
    assert source == "cache_hit"
    # LLM 不应被调用
    classifier_with_mock_deps._llm.achat.assert_not_called()


@pytest.mark.asyncio
async def test_classify_with_cache_miss_calls_llm_and_writes_cache(
    classifier_with_mock_deps: QueryIntentClassifier,
) -> None:
    """测试缓存未命中时调 LLM 并写回缓存 (P1)."""
    # Mock Redis: 缓存未命中
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.setex = AsyncMock()
    classifier_with_mock_deps._redis = mock_redis
    classifier_with_mock_deps._redis_initialized = True

    # Mock LLM 返回 "research"
    mock_response = MagicMock()
    mock_response.content = '{"intent": "research"}'
    classifier_with_mock_deps._llm.achat = AsyncMock(return_value=mock_response)

    intent, source = await classifier_with_mock_deps._classify_with_cache(
        "分析量子计算", has_report=False
    )

    assert intent == QueryIntent.RESEARCH
    assert source == "llm"
    # LLM 应被调用一次
    classifier_with_mock_deps._llm.achat.assert_called_once()
    # 缓存应被写入
    mock_redis.setex.assert_called_once()
    # 检查写入的 TTL 与 value
    call_args = mock_redis.setex.call_args
    assert call_args.args[1] == classifier_with_mock_deps.settings.query_classify_cache_ttl
    assert call_args.args[2] == "research"


@pytest.mark.asyncio
async def test_classify_with_cache_disabled_skips_redis(
    classifier_with_mock_deps: QueryIntentClassifier,
) -> None:
    """测试 query_classify_cache_enabled=False 时跳过 Redis, 直接走 LLM (P1).

    用 "research" 意图避免与默认 fallback (OFF_TOPIC) 混淆, 才能正确断言 source="llm".
    """
    classifier_with_mock_deps.settings = Settings(
        _env_file=None, query_classify_cache_enabled=False
    )

    # Mock LLM 返回 "research" (非默认 fallback, 才能断言 source="llm")
    mock_response = MagicMock()
    mock_response.content = '{"intent": "research"}'
    classifier_with_mock_deps._llm.achat = AsyncMock(return_value=mock_response)

    intent, source = await classifier_with_mock_deps._classify_with_cache(
        "分析量子计算", has_report=False
    )

    assert intent == QueryIntent.RESEARCH
    assert source == "llm"


@pytest.mark.asyncio
async def test_classify_with_cache_llm_failure_returns_fallback_no_cache_write(
    classifier_with_mock_deps: QueryIntentClassifier,
) -> None:
    """测试 LLM 失败时返回兜底意图但不写入缓存 (避免缓存污染, P1)."""
    # Mock Redis
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.setex = AsyncMock()
    classifier_with_mock_deps._redis = mock_redis
    classifier_with_mock_deps._redis_initialized = True

    # Mock LLM 抛异常
    classifier_with_mock_deps._llm.achat = AsyncMock(side_effect=Exception("LLM 不可用"))

    intent, source = await classifier_with_mock_deps._classify_with_cache(
        "随便一个查询", has_report=False
    )

    # 默认兜底 OFF_TOPIC
    assert intent == QueryIntent.OFF_TOPIC
    assert source == "llm_fallback"
    # 缓存不应被写入 (避免缓存 fallback 结果)
    mock_redis.setex.assert_not_called()


@pytest.mark.asyncio
async def test_classify_with_cache_redis_failure_degrades_to_llm(
    classifier_with_mock_deps: QueryIntentClassifier,
) -> None:
    """测试 Redis 不可用时降级为直接走 LLM (不阻断主流程, P1)."""
    # Mock Redis: get 抛异常 (连接失败/读失败), 模拟 Redis 不可用
    # 直接设置 _redis + _redis_initialized, 跳过 get_redis_client 调用
    # (源码已从 aioredis.from_url 重构为 src.common.redis_client.get_redis_client)
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(side_effect=Exception("Redis 连接失败"))
    mock_redis.setex = AsyncMock()
    classifier_with_mock_deps._redis = mock_redis
    classifier_with_mock_deps._redis_initialized = True

    # Mock LLM 返回 "research"
    mock_response = MagicMock()
    mock_response.content = '{"intent": "research"}'
    classifier_with_mock_deps._llm.achat = AsyncMock(return_value=mock_response)

    intent, source = await classifier_with_mock_deps._classify_with_cache(
        "分析量子计算", has_report=False
    )

    assert intent == QueryIntent.RESEARCH
    assert source == "llm"


# ========== classify (集成规则层 + LLM 层) ==========


@pytest.mark.asyncio
async def test_classify_rule_hit_skips_llm(
    classifier_with_mock_deps: QueryIntentClassifier,
) -> None:
    """测试规则层命中时跳过 LLM 调用 (零 LLM 成本)."""
    classifier_with_mock_deps._llm.achat = AsyncMock()

    # "你好" 应命中 _COMMON_SHORT_PHRASES
    intent = await classifier_with_mock_deps.classify("你好", has_report=False)

    assert intent == QueryIntent.SHORT_QUERY
    # LLM 不应被调用
    classifier_with_mock_deps._llm.achat.assert_not_called()


@pytest.mark.asyncio
async def test_classify_chitchat_pattern_hit_returns_off_topic(
    classifier_with_mock_deps: QueryIntentClassifier,
) -> None:
    """测试闲聊正则命中时返回 OFF_TOPIC (零 LLM 成本)."""
    classifier_with_mock_deps._llm.achat = AsyncMock()

    # "你多大了" 应命中 _CHITCHAT_PATTERNS
    intent = await classifier_with_mock_deps.classify("你多大了", has_report=False)

    assert intent == QueryIntent.OFF_TOPIC
    classifier_with_mock_deps._llm.achat.assert_not_called()


# ========== cleanup_legacy_chat_seeds (P2 Qdrant 清理) ==========


@pytest.mark.asyncio
async def test_cleanup_legacy_chat_seeds_idempotent_no_data() -> None:
    """测试 P2 清理函数幂等: namespace 无数据时跳过, 不抛异常."""
    # 注意: cleanup_legacy_chat_seeds() 在函数体内 import get_qdrant_manager,
    # 必须在源模块上 patch (而非 query_classifier 模块)
    with patch("src.rag.qdrant_manager.get_qdrant_manager") as mock_get_mgr:
        mock_mgr = AsyncMock()
        mock_mgr.ensure_collection = AsyncMock()
        mock_mgr.count_points_in_namespace = AsyncMock(return_value=0)
        mock_mgr.delete_by_namespace = AsyncMock()
        mock_mgr.settings = Settings(_env_file=None)
        mock_get_mgr.return_value = mock_mgr

        # 不应抛异常
        await cleanup_legacy_chat_seeds()

        # 应检查两个 namespace
        assert mock_mgr.count_points_in_namespace.call_count == 2
        # 无数据, 不应调用 delete
        mock_mgr.delete_by_namespace.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_legacy_chat_seeds_deletes_when_data_exists() -> None:
    """测试 P2 清理函数: namespace 有数据时调用 delete_by_namespace."""
    with patch("src.rag.qdrant_manager.get_qdrant_manager") as mock_get_mgr:
        mock_mgr = AsyncMock()
        mock_mgr.ensure_collection = AsyncMock()
        # 第一次调用返回 178 (short_query), 第二次返回 118 (off_topic)
        mock_mgr.count_points_in_namespace = AsyncMock(side_effect=[178, 118])
        mock_mgr.delete_by_namespace = AsyncMock()
        mock_mgr.settings = Settings(_env_file=None)
        mock_get_mgr.return_value = mock_mgr

        await cleanup_legacy_chat_seeds()

        # 应删除两个 namespace
        assert mock_mgr.delete_by_namespace.call_count == 2


@pytest.mark.asyncio
async def test_cleanup_legacy_chat_seeds_qdrant_unavailable_no_raise() -> None:
    """测试 P2 清理函数: Qdrant 不可用时仅告警不抛异常 (不阻断启动)."""
    with patch("src.rag.qdrant_manager.get_qdrant_manager") as mock_get_mgr:
        mock_get_mgr.side_effect = Exception("Qdrant 不可用")

        # 不应抛异常
        await cleanup_legacy_chat_seeds()
