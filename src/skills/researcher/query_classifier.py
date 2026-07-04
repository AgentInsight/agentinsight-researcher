"""查询意图分类器 (P0-Future-05/06).

AGENTS.md 第 5 章: 节点纯函数, 无副作用.
AGENTS.md 第 9 章: LLM 调用经 llm/ 的 LLMClient (LiteLLM), 禁厂商 SDK 直连.
AGENTS.md 第 10 章: 用 trace_chain 包裹 (禁 agentinsight.observe 装饰器).
AGENTS.md 第 7 章: Embeddings 经 rag/embeddings.py, Qdrant 经 rag/qdrant_manager.py, 禁直连.

三层分类逻辑:
- 第一层(规则): 长度<min_length / 纯数字 / 纯标点 → SHORT_QUERY
- 第二层(Embeddings 语义): Qdrant short_query_patterns namespace 语义匹配 → SHORT_QUERY
- 第三层(LLM FAST 层): 仅当前两层未命中时, 用 LLMTier.FAST 分类 RESEARCH / CHAT
- LLM 调用失败默认 RESEARCH (宁可走研究流程也不误判)
- 语义层失败降级到 LLM 层 (不阻断主流程)

短查询(如"你好"/"1"/"天气")直接返回 settings.short_query_reply, 不走任何 graph.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.config.settings import Settings, get_settings
from src.llm.client import LLMClient, LLMTier
from src.observability.tracing import trace_chain
from src.rag.embeddings import EmbeddingsClient, get_embeddings_client
from src.rag.qdrant_manager import QdrantManager, get_qdrant_manager

logger = logging.getLogger(__name__)

# 纯标点/符号正则: 全部由非字母数字字符组成 (re.UNICODE 下中文字符为 word char, 不会被匹配)
_PURE_PUNCT_RE = re.compile(r"^[\s\W]+$", re.UNICODE)

# 重复字符模式正则: 全部由同一字符重复组成 (如 "哈哈哈哈"/"aaaa"/"1111")
_REPEAT_PATTERN_RE = re.compile(r"^(.)\1+$")

# 单单词正则: 仅含字母或中文 (无空格/数字/标点)
_SINGLE_WORD_RE = re.compile(r"^[a-zA-Z\u4e00-\u9fa5]+$")

# 常见短查询短语 (精确匹配, 英文小写; 匹配时 query.lower() 比对)
# 用于在规则层快速拦截高频短查询, 避免落入 Embeddings 语义层误判
_COMMON_SHORT_PHRASES: frozenset[str] = frozenset(
    {
        # 中文常见短语
        "你好",
        "您好",
        "嗨",
        "哈喽",
        "测试",
        "谢谢",
        "感谢",
        "再见",
        "拜拜",
        "帮助",
        "在吗",
        "你是谁",
        "你能做什么",
        "怎么用",
        "介绍",
        "功能",
        "菜单",
        "是",
        "否",
        "对",
        "收到",
        "明白",
        "好的",
        "嗯",
        "哦",
        "天气",
        "时间",
        "日期",
        "无聊",
        "聊天",
        # 英文常见短语 (小写, 匹配时 query.lower())
        "hi",
        "hello",
        "hey",
        "ok",
        "yes",
        "no",
        "bye",
        "test",
        "help",
        "menu",
        "intro",
        "about",
        "weather",
        "time",
        "date",
        "thanks",
        "please",
        "sorry",
        "sure",
        "cool",
        "wow",
        "lol",
        "hmm",
        "hey there",
        "hi there",
        "hello there",
    }
)

# 短查询种子模式 (懒初始化时预填充到 Qdrant short_query_patterns namespace)
# 涵盖问候/告别/确认/数字/测试等常见短查询, 用 Embeddings 语义匹配替代硬编码字符串匹配
# 基于网络常见短查询模式整理, 按分类组织 (178 个不重复种子)
_SHORT_QUERY_SEED: list[str] = [
    # 问候类 (30)
    "你好",
    "您好",
    "嗨",
    "哈喽",
    "hi",
    "hello",
    "hey",
    "早上好",
    "下午好",
    "晚上好",
    "晚安",
    "hey there",
    "hi there",
    "hello there",
    "大家好",
    "哈喽啊",
    "嗨嗨",
    "喂",
    "耶",
    "wow",
    "sup",
    "yo",
    "hola",
    "bonjour",
    "你好啊",
    "你好呀",
    "哈喽哈喽",
    "嗨呀",
    "哎呀",
    "诶",
    # 确认/回应类 (26)
    "好的",
    "ok",
    "嗯",
    "哦",
    "yes",
    "no",
    "是",
    "否",
    "对",
    "不对",
    "收到",
    "明白",
    "嗯嗯",
    "嗯哼",
    "ok啦",
    "okay",
    "okey",
    "sure",
    "of course",
    "没问题",
    "行",
    "行吧",
    "好",
    "好的呀",
    "好滴",
    "好嘞",
    # 感谢类 (17)
    "谢谢",
    "感谢",
    "thanks",
    "thank you",
    "多谢",
    "辛苦了",
    "谢啦",
    "3q",
    "thx",
    "ty",
    "thankss",
    "多谢啦",
    "感谢感谢",
    "谢了",
    "辛苦",
    "劳烦了",
    "麻烦你了",
    # 告别类 (19)
    "再见",
    "bye",
    "goodbye",
    "拜拜",
    "88",
    "下次见",
    "回头见",
    "see you",
    "byebye",
    "byeee",
    "see ya",
    "later",
    "cya",
    "peace",
    "拜",
    "拜啦",
    "回见",
    "明天见",
    "下次聊",
    # 测试/无意义类 (20)
    "测试",
    "test",
    "试试",
    "试一下",
    "1",
    "2",
    "3",
    "123",
    "aaa",
    "bbb",
    "111",
    "222",
    "333",
    "aaa111",
    "asdf",
    "qwerty",
    "foo",
    "bar",
    "baz",
    "测试中",
    # 询问 bot 能力类 (25)
    "你是谁",
    "你叫什么",
    "你能做什么",
    "帮助",
    "help",
    "菜单",
    "menu",
    "功能",
    "你是ai吗",
    "你是机器人吗",
    "怎么用",
    "使用说明",
    "介绍",
    "intro",
    "about",
    "你能帮我什么",
    "你能干啥",
    "你有啥功能",
    "如何使用",
    "怎么用你",
    "你是什么",
    "what is this",
    "你是ai",
    "你是机器人",
    "你是啥",
    # 闲聊/情绪类 (22)
    "在吗",
    "在不在",
    "有人吗",
    "天气",
    "今天天气",
    "时间",
    "几点了",
    "日期",
    "今天几号",
    "星期几",
    "无聊",
    "聊天",
    "在吗在吗",
    "在不",
    "有人",
    "陪我聊天",
    "聊聊天",
    "无聊啊",
    "好无聊",
    "开心",
    "难过",
    "累",
    # 英文短查询补充 (19)
    "weather",
    "time",
    "date",
    "help me",
    "can you",
    "who are you",
    "what can you do",
    "good morning",
    "good night",
    "how are you",
    "hey man",
    "good evening",
    "good afternoon",
    "howdy",
    "greetings",
    "what's up",
    "sup man",
    "how's it going",
    "long time",
]

# 短查询种子版本号, 用于增量更新 Qdrant (版本变化时触发重新写入)
_SHORT_QUERY_SEED_VERSION: str = "v6.0"


class QueryIntent(StrEnum):
    """查询意图类型."""

    RESEARCH = "research"  # 研究请求 → 走 researcher graph
    CHAT = "chat"  # 对话 → 走 chat graph
    SHORT_QUERY = "short_query"  # 短查询 → 直接返回回复语


@dataclass
class _RuleResult:
    """规则层分类结果(内部)."""

    intent: QueryIntent
    reason: str  # 命中原因(用于 trace)


class QueryIntentClassifier:
    """查询意图分类器 (P0-Future-05/06).

    三层分类:
    1. 规则层: 长度<min_length / 纯数字 / 纯标点 → SHORT_QUERY
    2. Embeddings 语义层: Qdrant short_query_patterns namespace 语义匹配 → SHORT_QUERY
    3. LLM FAST 层: 仅当前两层未命中时, 用 LLMTier.FAST 分类 RESEARCH / CHAT
       失败时默认 RESEARCH (宁可走研究流程也不误判)

    语义层失败时降级到 LLM 层 (不阻断主流程).
    """

    settings: Settings
    _llm: LLMClient
    _embeddings: EmbeddingsClient
    _qdrant: QdrantManager
    _seed_lock: asyncio.Lock
    _seed_initialized: bool

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        embeddings: EmbeddingsClient | None = None,
        qdrant: QdrantManager | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or LLMClient(self.settings)
        self._embeddings = embeddings or get_embeddings_client()
        self._qdrant = qdrant or get_qdrant_manager()
        self._seed_lock = asyncio.Lock()
        self._seed_initialized = False

    @property
    def _short_query_namespace(self) -> str:
        """短查询种子模式的 Qdrant namespace.

        AGENTS.md 第 7 章数据隔离: 按 agent_id 区分, 故 namespace = {agent_id}:short_query_patterns.
        种子模式为全局通用问候/告别等, 不含 user_id (非用户私有数据).
        """
        return f"{self.settings.agent_name}:short_query_patterns"

    def _rule_classify(self, query: str) -> _RuleResult | None:
        """第一层规则分类 (快速).

        Returns:
            命中规则时返回 _RuleResult(SHORT_QUERY, reason);
            未命中返回 None (需进入第二层语义分类).
        """
        if not self.settings.short_query_enabled:
            return None

        q = query.strip()

        # 1. 长度过短
        if len(q) < self.settings.short_query_min_length:
            return _RuleResult(QueryIntent.SHORT_QUERY, "length_below_min")

        # 2. 纯数字 (如 "1", "123")
        if q.isdigit():
            return _RuleResult(QueryIntent.SHORT_QUERY, "pure_digits")

        # 3. 纯标点/符号 (如 "??", "...")
        if _PURE_PUNCT_RE.match(q):
            return _RuleResult(QueryIntent.SHORT_QUERY, "pure_punctuation")

        # 4. 单字符 (中文/英文单个字符, 如 "嗨"/"a"/"1" 未被上面规则命中时)
        if len(q) == 1:
            return _RuleResult(QueryIntent.SHORT_QUERY, "single_char")

        # 5. 重复字符模式 (如 "哈哈哈哈"/"aaaa"/"1111"/"???")
        if len(q) >= 2 and _REPEAT_PATTERN_RE.match(q):
            return _RuleResult(QueryIntent.SHORT_QUERY, "repeated_pattern")

        # 6. 纯字母/中文单单词且长度≤6 (如 "Hello"/"Hi"/"test"/"ok"/"你好")
        #    不含空格/数字/标点; 中文 2-6 字短词亦拦截 (避免 "你好" 落入语义层误判)
        if len(q) <= 6 and _SINGLE_WORD_RE.match(q):
            return _RuleResult(QueryIntent.SHORT_QUERY, "single_word_short")

        # 7. 常见短语精确匹配 (大小写不敏感; 含 "你好"/"hello"/"test"/"谢谢" 等 30+ 高频短语)
        if q.lower() in _COMMON_SHORT_PHRASES:
            return _RuleResult(QueryIntent.SHORT_QUERY, "exact_match_common")

        return None

    async def _ensure_seed_patterns(self) -> None:
        """懒初始化种子模式到 Qdrant (带版本校验, 支持增量更新).

        使用双重检查锁定避免并发重复写入.
        通过 _SHORT_QUERY_SEED_VERSION 做 payload 版本校验:
        - 版本匹配 → 跳过写入
        - 版本不匹配或不存在 → 删除旧数据, 重新写入全部种子
        预填充失败不阻断, 后续语义匹配会降级到 LLM 层.
        """
        if self._seed_initialized:
            return

        async with self._seed_lock:
            if self._seed_initialized:
                return
            try:
                await self._qdrant.ensure_collection()

                # 用首条种子向量检索, 检查是否已存在当前版本
                probe_vector = await self._embeddings.embed_query(_SHORT_QUERY_SEED[0])
                existing = await self._qdrant.search(
                    query_vector=probe_vector,
                    namespaces=[self._short_query_namespace],
                    limit=1,
                )
                if existing:
                    top_meta = existing[0].get("metadata", {}) or {}
                    if top_meta.get("seed_version") == _SHORT_QUERY_SEED_VERSION:
                        self._seed_initialized = True
                        logger.debug(
                            "短查询种子已存在 (version=%s), 跳过写入",
                            _SHORT_QUERY_SEED_VERSION,
                        )
                        return

                # 版本不匹配或不存在, 重新写入
                await self._write_seed_patterns()
                self._seed_initialized = True
            except Exception as e:  # noqa: BLE001
                # 预填充失败不阻断, 标记已尝试避免反复失败重试
                logger.warning("短查询种子初始化失败 (降级为仅规则层): %s", e)
                self._seed_initialized = True

    async def _write_seed_patterns(self) -> None:
        """写入/更新短查询种子到 Qdrant (版本变化时先删后写).

        AGENTS.md 第 7 章: payload 必须含 content+metadata+namespace;
        种子版本号写入 metadata.seed_version 做增量更新校验.
        """
        # 版本变化时先清理旧数据 (避免残留旧版本种子)
        await self._qdrant.delete_by_namespace(self._short_query_namespace)

        points: list[dict[str, Any]] = [
            {
                "content": pattern,
                "metadata": {
                    "type": "short_query_seed",
                    "category": "chat",
                    "seed_version": _SHORT_QUERY_SEED_VERSION,
                },
            }
            for pattern in _SHORT_QUERY_SEED
        ]
        await self._qdrant.upsert_points(
            namespace=self._short_query_namespace,
            points=points,
        )
        logger.info(
            "短查询种子已写入 Qdrant (namespace=%s, version=%s, count=%d)",
            self._short_query_namespace,
            _SHORT_QUERY_SEED_VERSION,
            len(points),
        )

    async def _semantic_match(self, query: str) -> bool:
        """第二层 Embeddings 语义匹配短查询.

        1. 将 query 用 EmbeddingsClient 向量化
        2. 在 Qdrant short_query_patterns namespace 搜索
        3. top-1 score > short_query_similarity_threshold → SHORT_QUERY

        任何异常都降级为 False (不阻断主流程, 进入 LLM 层).
        """
        try:
            await self._ensure_seed_patterns()

            query_vector = await self._embeddings.embed_query(query)
            if not query_vector:
                logger.warning("Embedding 返回空向量, 语义层降级")
                return False

            results = await self._qdrant.search(
                query_vector=query_vector,
                namespaces=[self._short_query_namespace],
                limit=1,
            )

            if not results:
                return False

            top_score = float(results[0].get("score") or 0.0)
            matched = top_score > self.settings.short_query_similarity_threshold
            if matched:
                logger.debug(
                    "语义匹配命中短查询: query=%r top_score=%.4f threshold=%.2f",
                    query[:50],
                    top_score,
                    self.settings.short_query_similarity_threshold,
                )
            return matched
        except Exception as e:  # noqa: BLE001
            logger.warning("语义匹配失败, 降级到 LLM 层: %s", e)
            return False

    async def _llm_classify(self, query: str, has_report: bool) -> QueryIntent:
        """第三层 LLM FAST 分类.

        用 LLMTier.FAST (deepseek-chat, temperature=0.0) 分类 RESEARCH / CHAT.
        失败时默认 RESEARCH (宁可走研究流程也不误判).

        Args:
            query: 用户原始查询
            has_report: 当前会话是否已有报告 (True 时倾向 CHAT)

        Returns:
            QueryIntent.RESEARCH 或 QueryIntent.CHAT
        """
        hint = (
            "注意: 用户当前会话已有研究报告, 倾向判定为 chat."
            if has_report
            else "注意: 用户当前会话无研究报告, 倾向判定为 research."
        )

        system_prompt = (
            "你是查询意图分类器. 根据用户查询判断意图类别, 仅返回 JSON.\n\n"
            "类别定义:\n"
            '- "research": 需要深入研究并生成报告的主题 (如"分析新能源汽车市场"|"AI 在医疗的应用")\n'
            '- "chat": 简短对话/问候/询问/闲聊 (如"今天怎么样"|"你能做什么"|"解释一下")\n\n'
            f"{hint}\n\n"
            '返回 JSON: {"intent": "research" | "chat"}, 仅返回 JSON, 不要其他内容.'
        )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query[:1000]},
        ]

        try:
            response = await self._llm.achat(
                messages,
                tier=LLMTier.FAST,
                temperature=0.0,
                max_tokens=64,
                step="query_intent_classify",
                span_name="query-intent-classifier",
            )
            content = response.content.strip()
            # 兼容 LLM 可能返回的 markdown 围栏
            if content.startswith("```"):
                content = content.strip("`")
                if content.lower().startswith("json"):
                    content = content[4:]
                content = content.strip()
            data: dict[str, Any] = json.loads(content)
            intent_str = str(data.get("intent", "research")).lower().strip()
            if intent_str == "chat":
                return QueryIntent.CHAT
            return QueryIntent.RESEARCH
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM 意图分类失败, 降级为 RESEARCH: %s", e)
            return QueryIntent.RESEARCH

    async def classify(self, query: str, has_report: bool) -> QueryIntent:
        """分类查询意图.

        三层分类:
        1. 规则层优先 (短查询保护)
        2. Embeddings 语义层 (规则层未命中时)
        3. LLM FAST 层 (前两层未命中时)

        Args:
            query: 用户原始查询
            has_report: 当前会话是否已有报告 (True 时 LLM 倾向 CHAT)

        Returns:
            QueryIntent 枚举 (RESEARCH / CHAT / SHORT_QUERY)
        """
        async with trace_chain(
            name="query-intent-classifier",
            input={
                "query": query[:200],
                "has_report": has_report,
            },
        ) as span:
            # 第一层: 规则分类
            rule_result = self._rule_classify(query)
            if rule_result is not None:
                span.update(
                    output={
                        "intent": rule_result.intent.value,
                        "layer": "rule",
                    },
                    metadata={"reason": rule_result.reason},
                )
                return rule_result.intent

            # 第二层: Embeddings 语义匹配
            if await self._semantic_match(query):
                span.update(
                    output={
                        "intent": QueryIntent.SHORT_QUERY.value,
                        "layer": "semantic",
                    },
                    metadata={
                        "threshold": self.settings.short_query_similarity_threshold,
                    },
                )
                return QueryIntent.SHORT_QUERY

            # 第三层: LLM FAST 分类
            intent = await self._llm_classify(query, has_report)
            span.update(
                output={"intent": intent.value, "layer": "llm"},
                metadata={"has_report": has_report},
            )
            return intent


# ========== 全局单例 ==========
_classifier: QueryIntentClassifier | None = None


def get_query_intent_classifier() -> QueryIntentClassifier:
    """获取全局 QueryIntentClassifier 单例."""
    global _classifier
    if _classifier is None:
        _classifier = QueryIntentClassifier()
    return _classifier
