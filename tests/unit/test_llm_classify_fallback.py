"""单元测试: LLM 分类失败兜底字典化 (分支优化).

验证 src/skills/researcher/query_classifier.py:
- _FALLBACK_INTENT_MAP: 字典查表取代 if-else (仅 'research' 显式映射)
- _fallback_intent: 配置 llm_classify_fallback 返回对应 QueryIntent
- 默认 OFF_TOPIC (业界标准: 走最轻路径, 避免误导向高成本研究)
- 未知字符串/空字符串/chat → 兜底 OFF_TOPIC
- _llm_classify 失败路径走 _fallback_intent
- _llm_classify 未知意图字符串走 _fallback_intent
- classify() 集成: LLM 失败时返回 fallback

节点纯函数, 无副作用.
单元测试不依赖外部服务 (mock LLM/Redis).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings
from src.skills.researcher.query_classifier import (
    _FALLBACK_INTENT_MAP,
    QueryIntent,
    QueryIntentClassifier,
)

pytestmark = pytest.mark.unit


@pytest.fixture()
def classifier() -> QueryIntentClassifier:
    """构造 Classifier 实例 (跳过 LLM 初始化, 仅测试 fallback 逻辑).

    _fallback_intent 仅访问 settings.llm_classify_fallback, 不调 LLM/Redis.
    通过 __new__ 跳过 __init__ 避免外部依赖初始化.
    """
    obj = QueryIntentClassifier.__new__(QueryIntentClassifier)
    obj.settings = Settings(_env_file=None)
    obj._llm = MagicMock()  # type: ignore[assignment]
    obj._redis = None
    obj._redis_initialized = False
    obj._inflight_locks = {}
    return obj


# ========== _FALLBACK_INTENT_MAP 内容契约 ==========


def test_fallback_intent_map_contains_only_research() -> None:
    """_FALLBACK_INTENT_MAP 仅含 'research' 键 (其他值兜底 OFF_TOPIC)."""
    assert "research" in _FALLBACK_INTENT_MAP
    assert _FALLBACK_INTENT_MAP["research"] == QueryIntent.RESEARCH


def test_fallback_intent_map_has_single_entry() -> None:
    """_FALLBACK_INTENT_MAP 应只有 1 个条目 (仅 research 显式映射)."""
    assert len(_FALLBACK_INTENT_MAP) == 1


def test_fallback_intent_map_does_not_contain_chat() -> None:
    """_FALLBACK_INTENT_MAP 不含 'chat' (chat 走 LLM 显式判断, 失败不兜底 chat).

    业界标准: LLM 失败走最轻路径 (OFF_TOPIC), 避免误导向 chat 触发对话成本.
    """
    assert "chat" not in _FALLBACK_INTENT_MAP


def test_fallback_intent_map_does_not_contain_off_topic() -> None:
    """_FALLBACK_INTENT_MAP 不显式含 'off_topic' (字典 .get 默认值即 OFF_TOPIC)."""
    assert "off_topic" not in _FALLBACK_INTENT_MAP


# ========== _fallback_intent 默认行为 ==========


def test_fallback_intent_default_returns_off_topic(classifier: QueryIntentClassifier) -> None:
    """_fallback_intent: settings.llm_classify_fallback 默认值 → OFF_TOPIC.

    业界标准: LLM 失败走最轻路径, 避免误导向高成本研究流程.
    """
    # Settings 默认 llm_classify_fallback 应为 'off_topic' (或未设置走默认)
    assert classifier.settings.llm_classify_fallback in ("off_topic", "", None)
    assert classifier._fallback_intent() == QueryIntent.OFF_TOPIC


def test_fallback_intent_research_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_fallback_intent: settings.llm_classify_fallback='research' → RESEARCH.

    可通过配置覆盖默认 OFF_TOPIC, 适合研究导向场景 (宁可走研究也不漏).
    """
    obj = QueryIntentClassifier.__new__(QueryIntentClassifier)
    obj.settings = Settings(llm_classify_fallback="research", _env_file=None)
    assert obj._fallback_intent() == QueryIntent.RESEARCH


# ========== _fallback_intent 未知值兜底 OFF_TOPIC ==========


def test_fallback_intent_unknown_string_returns_off_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_fallback_intent: 未知字符串 (如 'unknown') → 兜底 OFF_TOPIC (字典 .get 默认).

    注: Settings.llm_classify_fallback 类型为 Literal["research", "off_topic"],
    pydantic 会拒绝非法值; 此处用 model_construct 绕过校验, 模拟运行时
    防御性兜底逻辑 (配置被外部直接修改/JSON 注入等极端场景).
    """
    obj = QueryIntentClassifier.__new__(QueryIntentClassifier)
    obj.settings = Settings.model_construct(llm_classify_fallback="unknown_intent")
    assert obj._fallback_intent() == QueryIntent.OFF_TOPIC


def test_fallback_intent_empty_string_returns_off_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_fallback_intent: 空字符串 → 兜底 OFF_TOPIC.

    注: 用 model_construct 绕过 pydantic Literal 校验, 测试防御性兜底逻辑.
    """
    obj = QueryIntentClassifier.__new__(QueryIntentClassifier)
    obj.settings = Settings.model_construct(llm_classify_fallback="")
    assert obj._fallback_intent() == QueryIntent.OFF_TOPIC


def test_fallback_intent_chat_string_returns_off_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_fallback_intent: 'chat' 字符串不在 map 中 → 兜底 OFF_TOPIC.

    设计: chat 必须由 LLM 显式判断, 失败不兜底 chat (避免误判触发对话成本).
    注: 用 model_construct 绕过 pydantic Literal 校验, 测试防御性兜底逻辑.
    """
    obj = QueryIntentClassifier.__new__(QueryIntentClassifier)
    obj.settings = Settings.model_construct(llm_classify_fallback="chat")
    assert obj._fallback_intent() == QueryIntent.OFF_TOPIC


def test_fallback_intent_case_sensitive(classifier: QueryIntentClassifier) -> None:
    """_fallback_intent: 大小写敏感 ('Research' ≠ 'research', 兜底 OFF_TOPIC).

    注: 用 model_construct 绕过 pydantic Literal 校验, 测试防御性兜底逻辑.
    """
    obj = QueryIntentClassifier.__new__(QueryIntentClassifier)
    obj.settings = Settings.model_construct(llm_classify_fallback="Research")  # 大写
    assert obj._fallback_intent() == QueryIntent.OFF_TOPIC


# ========== _llm_classify 失败路径走 _fallback_intent ==========


async def test_llm_classify_failure_returns_fallback_off_topic(
    classifier: QueryIntentClassifier,
) -> None:
    """_llm_classify: LLM 调用抛异常 → 走 _fallback_intent (默认 OFF_TOPIC)."""
    classifier._llm.achat = AsyncMock(side_effect=RuntimeError("LLM timeout"))
    result = await classifier._llm_classify("分析量子计算", has_report=False)
    assert result == QueryIntent.OFF_TOPIC


async def test_llm_classify_failure_returns_fallback_research_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_llm_classify: LLM 失败 + llm_classify_fallback='research' → RESEARCH."""
    obj = QueryIntentClassifier.__new__(QueryIntentClassifier)
    obj.settings = Settings(llm_classify_fallback="research", _env_file=None)
    obj._llm = MagicMock()
    obj._llm.achat = AsyncMock(side_effect=RuntimeError("LLM timeout"))
    result = await obj._llm_classify("分析量子计算", has_report=False)
    assert result == QueryIntent.RESEARCH


async def test_llm_classify_json_parse_failure_returns_fallback(
    classifier: QueryIntentClassifier,
) -> None:
    """_llm_classify: LLM 返回非 JSON → 走 _fallback_intent."""
    mock_response = MagicMock()
    mock_response.content = "这不是JSON"
    classifier._llm.achat = AsyncMock(return_value=mock_response)
    result = await classifier._llm_classify("分析量子计算", has_report=False)
    assert result == QueryIntent.OFF_TOPIC


async def test_llm_classify_unknown_intent_string_returns_fallback(
    classifier: QueryIntentClassifier,
) -> None:
    """_llm_classify: LLM 返回未知 intent 字符串 → 走 _fallback_intent.

    场景: LLM 返回 {"intent": "unknown"} (不在 research/chat/off_topic 之列).
    """
    mock_response = MagicMock()
    mock_response.content = '{"intent": "unknown_value"}'
    classifier._llm.achat = AsyncMock(return_value=mock_response)
    result = await classifier._llm_classify("分析量子计算", has_report=False)
    assert result == QueryIntent.OFF_TOPIC


# ========== _llm_classify 正常路径 (不走 fallback) ==========


async def test_llm_classify_research_intent_returns_research(
    classifier: QueryIntentClassifier,
) -> None:
    """_llm_classify: LLM 返回 research → RESEARCH (不走 fallback)."""
    mock_response = MagicMock()
    mock_response.content = '{"intent": "research"}'
    classifier._llm.achat = AsyncMock(return_value=mock_response)
    result = await classifier._llm_classify("分析量子计算市场", has_report=False)
    assert result == QueryIntent.RESEARCH


async def test_llm_classify_chat_intent_returns_chat(
    classifier: QueryIntentClassifier,
) -> None:
    """_llm_classify: LLM 返回 chat → CHAT (不走 fallback)."""
    mock_response = MagicMock()
    mock_response.content = '{"intent": "chat"}'
    classifier._llm.achat = AsyncMock(return_value=mock_response)
    result = await classifier._llm_classify("展开讲讲第二点", has_report=True)
    assert result == QueryIntent.CHAT


async def test_llm_classify_off_topic_intent_returns_off_topic(
    classifier: QueryIntentClassifier,
) -> None:
    """_llm_classify: LLM 返回 off_topic → OFF_TOPIC (不走 fallback)."""
    mock_response = MagicMock()
    mock_response.content = '{"intent": "off_topic"}'
    classifier._llm.achat = AsyncMock(return_value=mock_response)
    result = await classifier._llm_classify("讲个笑话", has_report=False)
    assert result == QueryIntent.OFF_TOPIC


# ========== _llm_classify markdown 围栏兼容 ==========


async def test_llm_classify_markdown_fence_json(classifier: QueryIntentClassifier) -> None:
    """_llm_classify: LLM 返回带 markdown 围栏的 JSON → 正确解析.

    兼容 LLM 可能返回 ```json\n{"intent":"research"}\n``` 格式.
    """
    mock_response = MagicMock()
    mock_response.content = '```json\n{"intent": "research"}\n```'
    classifier._llm.achat = AsyncMock(return_value=mock_response)
    result = await classifier._llm_classify("分析量子计算", has_report=False)
    assert result == QueryIntent.RESEARCH


async def test_llm_classify_markdown_fence_without_json_prefix(
    classifier: QueryIntentClassifier,
) -> None:
    """_llm_classify: LLM 返回 ``` 围栏无 json 前缀 → 正确解析."""
    mock_response = MagicMock()
    mock_response.content = '```\n{"intent": "chat"}\n```'
    classifier._llm.achat = AsyncMock(return_value=mock_response)
    result = await classifier._llm_classify("展开讲讲", has_report=True)
    assert result == QueryIntent.CHAT


# ========== classify() 集成: LLM 失败时返回 fallback ==========


async def test_classify_llm_failure_returns_fallback_off_topic(
    classifier: QueryIntentClassifier,
) -> None:
    """classify() 集成: 规则层未命中 + LLM 失败 → fallback OFF_TOPIC.

    场景: query 不命中规则层 (长度足, 非纯数字/标点/闲聊), 进 LLM 层;
    LLM 调用失败, 走 _fallback_intent (默认 OFF_TOPIC).
    """
    classifier._llm.achat = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
    # mock _get_redis 返回 None (不启用缓存, 直接走 LLM)
    classifier._get_redis = AsyncMock(return_value=None)  # type: ignore[assignment]
    result = await classifier.classify("分析量子计算在金融的应用", has_report=False)
    assert result == QueryIntent.OFF_TOPIC


async def test_classify_rule_hit_skips_llm(classifier: QueryIntentClassifier) -> None:
    """classify() 集成: 规则层命中 (短查询/闲聊) → 直接返回, 不调 LLM.

    场景: query='你好' 命中 _COMMON_SHORT_PHRASES → SHORT_QUERY, LLM 不应被调用.
    """
    classifier._llm.achat = AsyncMock(side_effect=AssertionError("规则命中不应调 LLM"))
    result = await classifier.classify("你好", has_report=False)
    assert result == QueryIntent.SHORT_QUERY


async def test_classify_chitchat_pattern_returns_off_topic(
    classifier: QueryIntentClassifier,
) -> None:
    """classify() 集成: 闲聊正则命中 → OFF_TOPIC, 不调 LLM."""
    classifier._llm.achat = AsyncMock(side_effect=AssertionError("闲聊正则命中不应调 LLM"))
    result = await classifier.classify("讲个笑话", has_report=False)
    assert result == QueryIntent.OFF_TOPIC


# ========== has_report 影响 prompt (但不影响 fallback) ==========


async def test_llm_classify_has_report_true_includes_hint(
    classifier: QueryIntentClassifier,
) -> None:
    """_llm_classify: has_report=True 时 prompt 含 '已有研究报告' 提示.

    验证 prompt 内容根据 has_report 切换 (但不影响 fallback 逻辑).
    """
    mock_response = MagicMock()
    mock_response.content = '{"intent": "chat"}'
    classifier._llm.achat = AsyncMock(return_value=mock_response)

    await classifier._llm_classify("展开讲讲", has_report=True)

    # 验证 achat 被调用, 且 messages 含 has_report 提示
    call_args = classifier._llm.achat.call_args
    messages = call_args.args[0]
    system_content = messages[0]["content"]
    assert "已有研究报告" in system_content


async def test_llm_classify_has_report_false_includes_hint(
    classifier: QueryIntentClassifier,
) -> None:
    """_llm_classify: has_report=False 时 prompt 含 '无研究报告' 提示."""
    mock_response = MagicMock()
    mock_response.content = '{"intent": "off_topic"}'
    classifier._llm.achat = AsyncMock(return_value=mock_response)

    await classifier._llm_classify("讲个笑话", has_report=False)

    call_args = classifier._llm.achat.call_args
    messages = call_args.args[0]
    system_content = messages[0]["content"]
    assert "无研究报告" in system_content


# ========== 字典查表取代 if-else 验证 ==========


def test_fallback_uses_dict_lookup_not_if_else() -> None:
    """_fallback_intent 应使用字典查表 (无 if-else 分支).

    共享逻辑下沉, 避免重复 if-else.
    验证源码不含 if-else 链 (允许 .get() 默认值).
    """
    import inspect

    from src.skills.researcher import query_classifier

    source = inspect.getsource(query_classifier.QueryIntentClassifier._fallback_intent)
    # 不应含 if intent == 'xxx' 的分支判断
    assert "if self.settings.llm_classify_fallback ==" not in source, (
        "_fallback_intent 应用字典查表, 不应含 if-else 分支"
    )
    # 应使用 _FALLBACK_INTENT_MAP.get
    assert "_FALLBACK_INTENT_MAP.get" in source


def test_fallback_intent_map_is_module_level_constant() -> None:
    """_FALLBACK_INTENT_MAP 应为模块级常量 (非实例属性), 避免重复创建."""
    import inspect

    from src.skills.researcher import query_classifier

    source = inspect.getsource(query_classifier)
    # 应在模块级定义 (非 class 内)
    assert "_FALLBACK_INTENT_MAP" in source
    # 找到定义行, 应在 class 外 (无缩进)
    for line in source.splitlines():
        if line.startswith("_FALLBACK_INTENT_MAP"):
            return  # 模块级定义 (无前导空格)
    pytest.fail("_FALLBACK_INTENT_MAP 应在模块级定义 (无缩进)")
