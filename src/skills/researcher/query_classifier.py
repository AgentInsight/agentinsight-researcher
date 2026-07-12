"""查询意图分类器.

节点纯函数, 无副作用.
LLM 调用经 llm/ 的 LLMClient (LiteLLM), 禁厂商 SDK 直连.
用 trace_chain 包裹 (禁 agentinsight.observe 装饰器).
skills/ 不应依赖 rag/ (已解除 embeddings/qdrant 依赖).

两层分类逻辑 (已移除原第二层 Embeddings+Qdrant 语义匹配, 改用 FAST_LLM + Redis 缓存):
- 第一层(规则): 长度<min_length / 纯数字 / 纯标点 / 闲聊正则 → SHORT_QUERY 或 OFF_TOPIC
- 第二层(LLM FAST 层): 规则层未命中时, 用 LLMTier.FAST 分类 RESEARCH / CHAT / OFF_TOPIC
  - 命中结果写入 Redis 缓存 (TTL 24h), 高频重复 query 零 LLM 调用
  - LLM 调用失败默认 OFF_TOPIC (走最轻路径, 避免误导向高成本研究流程)

短查询(如"你好"/"1"/"天气")与离题闲聊(如"今天怎么样"/"讲个笑话"/"你多大了")直接返回
settings.short_query_reply / settings.off_topic_reply, 不走任何 graph, 零 LLM 成本.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.common.redis_client import get_redis_client
from src.config.settings import Settings, get_settings
from src.llm.client import LLMClient, LLMTier, get_llm_client
from src.observability.tracing import trace_chain

logger = logging.getLogger(__name__)

# 纯标点/符号正则: 全部由非字母数字字符组成 (re.UNICODE 下中文字符为 word char, 不会被匹配)
_PURE_PUNCT_RE = re.compile(r"^[\s\W]+$", re.UNICODE)

# 重复字符模式正则: 全部由同一字符重复组成 (如 "哈哈哈哈"/"aaaa"/"1111")
_REPEAT_PATTERN_RE = re.compile(r"^(.)\1+$")

# 单单词正则: 仅含字母或中文 (无空格/数字/标点)
_SINGLE_WORD_RE = re.compile(r"^[a-zA-Z\u4e00-\u9fa5]+$")

# 常见短查询短语 (精确匹配, 英文小写; 匹配时 query.lower() 比对)
# 用于在规则层快速拦截高频短查询
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

# 离题/闲聊正则模式
# 用于在规则层快速拦截高频闲聊句式, 命中即返回 OFF_TOPIC (零 LLM 成本)
# 设计原则: 仅匹配明显与研究/分析无关的句式, 模糊语义留给 LLM 层
_CHITCHAT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 询问 bot 身份/属性
    re.compile(r"^(?:你叫什么|你叫啥|你的名字|你叫什么名字|你叫啥名字)[？?]?"),
    re.compile(r"^(?:你多大了|你几岁|你几岁了|你的年龄|你多大)[？?]?"),
    re.compile(r"^(?:你是男是女|你是男的还是女的|你有性别吗)[？?]?"),
    re.compile(r"^(?:你有女朋友吗|你有男朋友吗|你结婚了吗|你单身吗)[？?]?"),
    re.compile(r"^(?:你在哪里|你在哪|你的位置|你住哪里)[？?]?"),
    re.compile(r"^(?:你是谁啊|你是什么东西|你是什么人)[？?]?"),
    # 询问 bot 心情/状态
    re.compile(r"^(?:今天|今儿).{0,5}(?:怎么样|如何|心情|开心吗|过得)[？?]?"),
    re.compile(r"^(?:你(?:最近|今天|现在)?).{0,3}(?:怎么样|如何|好吗|开心吗|累吗|忙吗)[？?]?"),
    re.compile(r"^(?:你好(?:吗|么|不))", re.UNICODE),
    re.compile(r"^(?:你在干嘛|你在做什么|你在干啥|你在忙什么)[？?]?"),
    re.compile(r"^(?:你怎么了|你不开心吗|你心情不好吗)[？?]?"),
    # 娱乐/创作请求
    re.compile(r"^(?:讲个|说个|来个|给我讲个|给我说个).{0,4}(?:笑话|故事|段子|谜语|绕口令)[？?]?"),
    re.compile(r"^(?:写首|作首|来首|给我写首).{0,4}(?:诗|词|歌|曲子)[？?]?"),
    re.compile(r"^(?:唱首歌|唱个歌|来首歌|给我唱)[？?]?"),
    re.compile(r"^(?:陪我玩|和我玩|来玩个|玩个游戏)[？?]?"),
    # 常识/算术问题
    re.compile(r"^(?:1\s*\+\s*1|2\s*\+\s*2|1加1|1\+1|一加一).{0,5}(?:等于|是|=?)[？?]?"),
    re.compile(r"^(?:天空为什么是蓝的|天空为什么是蓝色|为什么天空是蓝)[？?]?"),
    re.compile(r"^(?:太阳从哪边升起|太阳从哪升起|太阳东升西落)[？?]?"),
    re.compile(r"^(?:水为什么会沸腾|水为什么烧开|水为什么结冰)[？?]?"),
    re.compile(r"^(?:地球是圆的吗|地球是球形吗|地球有多大)[？?]?"),
    # 时间/日期/天气 (句子形式, 单词形式已在 _COMMON_SHORT_PHRASES 拦截)
    re.compile(r"^(?:几点了|现在几点|现在几点了|几点钟了)[？?]?"),
    re.compile(r"^(?:今天星期几|今天周几|今天几号|今天多少号)[？?]?"),
    re.compile(
        r"^(?:今天|明天|后天|大后天).{0,3}(?:天气怎么样|天气如何|天气好吗|会下雨吗|冷吗|热吗)[？?]?"
    ),
    re.compile(r"^(?:现在|今天).{0,3}(?:什么时间|什么时辰)[？?]?"),
    # 情绪/陪伴
    re.compile(r"^(?:我好开心|我好难过|我好累|我好无聊|我好孤独)[啊呀]?[。.!！?？]*$"),
    re.compile(r"^(?:我心情不好|我不开心|我很难过|我很郁闷|我烦死了)[。.!！?？]*$"),
    re.compile(r"^(?:陪我聊天|和我聊天|聊聊天|陪我聊聊|说说话)[吧]?[。.!！?？]*$"),
    re.compile(r"^(?:你真(?:聪明|棒|厉害|笨|傻|蠢))(?:啊|呀|哦)?[。.!！?？]*$"),
    re.compile(r"^(?:我喜欢你|我爱你|我讨厌你|我恨你)(?:啊|呀|哦)?[。.!！?？]*$"),
    # 转移话题/拒绝
    re.compile(r"^(?:我不想研究|我不想用了|算了|不研究了|不用了|不需要了)[。.!！?？]*$"),
    re.compile(r"^(?:换个别的话题|聊点别的|说点别的|不聊这个了)[。.!！?？]*$"),
    # 测试 bot 智能
    re.compile(r"^(?:你能(?:听懂|理解|明白).{0,5}吗)[？?]?"),
    re.compile(r"^(?:你聪明吗|你有意识吗|你有感情吗|你会思考吗)[？?]?"),
)


class QueryIntent(StrEnum):
    """查询意图类型."""

    RESEARCH = "research"  # 研究请求 → 走 researcher graph
    CHAT = "chat"  # 对话 (针对已有报告的追问) → 走 chat graph
    SHORT_QUERY = "short_query"  # 短查询 (问候/数字/标点/测试) → 直接返回回复语
    OFF_TOPIC = "off_topic"  # 离题/闲聊 (问候/身份/娱乐/常识/私人问题) → 直接返回离题回复语


# llm_classify_fallback 字符串 → QueryIntent 映射 (字典查表取代 if-else)
# 默认 OFF_TOPIC (走最轻路径); 仅 "research" 显式映射, 其他值兜底 OFF_TOPIC
_FALLBACK_INTENT_MAP: dict[str, QueryIntent] = {
    "research": QueryIntent.RESEARCH,
}


@dataclass
class _RuleResult:
    """规则层分类结果(内部)."""

    intent: QueryIntent
    reason: str  # 命中原因(用于 trace)


class QueryIntentClassifier:
    """查询意图分类器.

    两层分类 (已移除原第二层 Embeddings+Qdrant 语义匹配):
    1. 规则层: 长度<min_length / 纯数字 / 纯标点 / 闲聊正则 → SHORT_QUERY 或 OFF_TOPIC
    2. LLM FAST 层: 规则层未命中时, 用 LLMTier.FAST 分类 RESEARCH / CHAT / OFF_TOPIC
       - 命中结果写入 Redis 缓存 (TTL 24h)
       - 失败时默认 settings.llm_classify_fallback (默认 OFF_TOPIC, 走最轻路径)

    设计原则 (级联降级策略):
    - FAST_LLM 优先 (glm-4-flash, 免费层)
    - Redis 缓存高频 query (24h TTL, 零 LLM 调用)
    - 不依赖 Embeddings/Qdrant (符合 skills/ 不依赖 rag/ 边界)
    """

    settings: Settings
    _llm: LLMClient
    _redis: Any | None
    _redis_initialized: bool
    # singleflight 互斥锁 (按 query hash 分锁, 防止缓存击穿并发重复 LLM 调用)
    _inflight_locks: dict[str, asyncio.Lock]

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or get_llm_client()
        self._redis = None
        self._redis_initialized = False
        # singleflight 锁字典初始化
        self._inflight_locks = {}

    async def _get_redis(self) -> Any | None:
        """惰性初始化 Redis 连接 (复用 common.redis_client 全局单例).

        Redis 不可用时降级为不缓存 (每次走 LLM), 不阻断主流程.
        遵循 Redis 约定: key 加 {agent_id} 前缀.
        """
        if not self.settings.query_classify_cache_enabled:
            return None
        if self._redis_initialized:
            return self._redis
        # 复用全局单例 (双重检查锁 + ping 检查 + 降级 None 由 get_redis_client 内部保证)
        self._redis = await get_redis_client(self.settings)
        self._redis_initialized = True
        return self._redis

    def _cache_key(self, query: str, has_report: bool) -> str:
        """生成分类结果缓存 key.

        Redis 约定: {agent_id}:{user_id}:{module}:{type}:{id}
        此处为全局级分类缓存 (不区分用户), 使用 _global 占位.
        key 含 has_report 维度 (同一 query 在有/无报告上下文下分类可能不同).
        """
        query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
        return f"{self.settings.agent_name}:_global:query_classify:intent:{has_report}:{query_hash}"

    def _rule_classify(self, query: str) -> _RuleResult | None:
        """第一层规则分类 (快速).

        Returns:
            命中规则时返回 _RuleResult(SHORT_QUERY 或 OFF_TOPIC, reason);
            未命中返回 None (需进入第二层 LLM 分类).
        """
        q = query.strip()

        # 离题/闲聊正则优先匹配 (在短查询规则之前)
        # 仅当 off_topic_enabled 时启用, 避免与短查询保护冲突
        if self.settings.off_topic_enabled:
            for pattern in _CHITCHAT_PATTERNS:
                if pattern.search(q):
                    return _RuleResult(QueryIntent.OFF_TOPIC, "chitchat_pattern")

        if not self.settings.short_query_enabled:
            return None

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

        # 6. 纯字母/中文单单词且长度≤query_classify_single_word_max_chars (如 "Hello"/"Hi"/"test"/"ok"/"你好")
        #    不含空格/数字/标点; 中文 2-6 字短词亦拦截
        if len(q) <= self.settings.query_classify_single_word_max_chars and _SINGLE_WORD_RE.match(
            q
        ):
            return _RuleResult(QueryIntent.SHORT_QUERY, "single_word_short")

        # 7. 常见短语精确匹配 (大小写不敏感; 含 "你好"/"hello"/"test"/"谢谢" 等 30+ 高频短语)
        if q.lower() in _COMMON_SHORT_PHRASES:
            return _RuleResult(QueryIntent.SHORT_QUERY, "exact_match_common")

        return None

    async def _llm_classify(self, query: str, has_report: bool) -> QueryIntent:
        """第二层 LLM FAST 分类 (强化 prompt + few-shot 例子).

        用 LLMTier.FAST (glm-4-flash, temperature=0.0) 分类 RESEARCH / CHAT / OFF_TOPIC.
        失败时默认 settings.llm_classify_fallback (默认 OFF_TOPIC, 走最轻路径).

        Args:
            query: 用户原始查询
            has_report: 当前会话是否已有报告 (True 时倾向 CHAT)

        Returns:
            QueryIntent.RESEARCH / CHAT / OFF_TOPIC
        """
        hint = (
            "注意: 用户当前会话已有研究报告, 针对报告的追问/澄清倾向判定为 chat."
            if has_report
            else "注意: 用户当前会话无研究报告, 闲聊/问候/常识问题倾向判定为 off_topic."
        )

        # 强化 prompt: 增加分类定义细节 + few-shot 例子 (覆盖复合意图/方言/多语言)
        system_prompt = (
            "你是查询意图分类器. 根据用户查询判断意图类别, 仅返回 JSON.\n\n"
            "类别定义:\n"
            '- "research": 需要深入研究并生成报告的主题 '
            '(如"分析新能源汽车市场"|"AI 在医疗的应用"|"比较 React 和 Vue")\n'
            '- "chat": 针对已有研究报告的追问/澄清/讨论 '
            '(如"这个数据来源是什么"|"展开讲讲第二点"|"总结一下")\n'
            '- "off_topic": 与研究/分析无关的闲聊/问候/身份询问/娱乐/常识/私人问题 '
            '(如"你好"|"今天怎么样"|"讲个笑话"|"你多大了"|"1+1等于几"|"天气如何"|"今儿天气咋样")\n\n'
            "few-shot 例子 (用于校准复合意图与边界用例):\n"
            'query: "你好" → {"intent": "off_topic"}\n'
            'query: "你能做什么" → {"intent": "off_topic"}\n'
            'query: "1+1等于几" → {"intent": "off_topic"}\n'
            'query: "帮我研究一下量子计算" → {"intent": "research"}\n'
            'query: "上面报告里提到的第三个风险能详细说说吗" → {"intent": "chat"}\n'
            'query: "你好, 我想研究量子计算" → {"intent": "research"} '
            "(复合意图以研究为主, 不要被问候诱导为 off_topic)\n"
            'query: "hi there" → {"intent": "off_topic"}\n'
            'query: "今儿天气咋样" → {"intent": "off_topic"} (方言应识别为闲聊)\n'
            'query: "分析 Apple 2024 财报" → {"intent": "research"}\n'
            'query: "继续聊吧" → {"intent": "off_topic"}\n\n'
            f"{hint}\n\n"
            '返回 JSON: {"intent": "research" | "chat" | "off_topic"}, '
            "仅返回 JSON, 不要其他内容."
        )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query[: self.settings.query_classify_llm_query_truncate]},
        ]

        try:
            response = await self._llm.achat(
                messages,
                tier=LLMTier.FAST,
                temperature=0.0,
                max_tokens=self.settings.query_classify_llm_max_tokens,
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
            intent_str = str(data.get("intent", "")).lower().strip()
            if intent_str == "chat":
                return QueryIntent.CHAT
            if intent_str == "off_topic":
                return QueryIntent.OFF_TOPIC
            if intent_str == "research":
                return QueryIntent.RESEARCH
            # 未知意图字符串 → 走配置兜底 (默认 OFF_TOPIC)
            logger.warning(
                "LLM 返回未知意图字符串 %r, 走 llm_classify_fallback=%s",
                intent_str,
                self.settings.llm_classify_fallback,
            )
            return self._fallback_intent()
        except Exception as e:  # noqa: BLE001
            # LLM 失败走最轻路径 (默认 OFF_TOPIC, 避免误导向研究)
            logger.warning(
                "LLM 意图分类失败, 走 llm_classify_fallback=%s (默认最轻路径): %s",
                self.settings.llm_classify_fallback,
                e,
            )
            return self._fallback_intent()

    def _fallback_intent(self) -> QueryIntent:
        """返回配置的 LLM 失败兜底意图.

        默认 OFF_TOPIC (走最轻路径, 避免误导向高成本研究流程).
        可通过 settings.llm_classify_fallback 配置为 "research" 覆盖.

        字典查表取代 if-else (分支优化方案).
        """
        return _FALLBACK_INTENT_MAP.get(self.settings.llm_classify_fallback, QueryIntent.OFF_TOPIC)

    async def _classify_with_cache(self, query: str, has_report: bool) -> tuple[QueryIntent, str]:
        """带 Redis 缓存的 LLM 分类.

        singleflight 互斥锁防止缓存击穿 — 同一 query 并发请求只允许一个
        调用 LLM, 其他等待结果后从缓存读取 (避免并发重复 LLM 调用浪费成本).

        Returns:
            (intent, source) 元组; source ∈ {"cache_hit", "llm", "llm_fallback"}.
        """
        cache_key = self._cache_key(query, has_report)

        # 缓存命中检查
        r = await self._get_redis()
        if r is not None:
            try:
                cached = await r.get(cache_key)
                if cached is not None:
                    cached_str = str(cached).lower().strip()
                    for intent in QueryIntent:
                        if intent.value == cached_str:
                            return intent, "cache_hit"
            except Exception as e:  # noqa: BLE001
                logger.warning("QueryClassifier 缓存读取失败, 走 LLM: %s", e)

        # singleflight 互斥锁 — 缓存未命中时按 query hash 加锁
        # 同一 query 并发只允许一个调用 LLM, 其他等待后从缓存读取
        lock = self._inflight_locks.get(cache_key)
        if lock is None:
            lock = asyncio.Lock()
            self._inflight_locks[cache_key] = lock
        async with lock:
            # 双重检查: 持有锁后再次查缓存, 可能在等待期间已被其他协程填充
            if r is not None:
                try:
                    cached = await r.get(cache_key)
                    if cached is not None:
                        cached_str = str(cached).lower().strip()
                        for intent in QueryIntent:
                            if intent.value == cached_str:
                                return intent, "cache_hit"
                except Exception as e:  # noqa: BLE001
                    logger.warning("QueryClassifier 缓存读取失败 (singleflight), 走 LLM: %s", e)

            # 缓存未命中或不可用 → 调 LLM
            intent = await self._llm_classify(query, has_report)
            source = "llm_fallback" if intent == self._fallback_intent() else "llm"

            # 写入缓存 (仅缓存 LLM 真实分类结果, 不缓存 fallback, 避免缓存污染)
            if r is not None and source == "llm":
                try:
                    await r.setex(
                        cache_key,
                        self.settings.query_classify_cache_ttl,
                        intent.value,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("QueryClassifier 缓存写入失败 (不阻断): %s", e)

            return intent, source

    async def classify(self, query: str, has_report: bool) -> QueryIntent:
        """分类查询意图 (已移除原第二层 Embeddings+Qdrant 语义匹配).

        两层分类:
        1. 规则层 (短查询保护 + 闲聊正则) → SHORT_QUERY / OFF_TOPIC
        2. LLM FAST 层 + Redis 缓存 → RESEARCH / CHAT / OFF_TOPIC

        Args:
            query: 用户原始查询
            has_report: 当前会话是否已有报告 (True 时 LLM 倾向 CHAT)

        Returns:
            QueryIntent 枚举 (RESEARCH / CHAT / SHORT_QUERY / OFF_TOPIC)
        """
        async with trace_chain(
            name="query-intent-classifier",
            input={
                "query": query[: self.settings.query_classify_trace_input_truncate],
                "has_report": has_report,
            },
        ) as span:
            # 第一层: 规则分类 (含闲聊正则)
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

            # 第二层: LLM FAST 分类 (带 Redis 缓存)
            intent, source = await self._classify_with_cache(query, has_report)
            span.update(
                output={"intent": intent.value, "layer": "llm"},
                metadata={
                    "has_report": has_report,
                    "source": source,  # cache_hit / llm / llm_fallback
                },
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


async def cleanup_legacy_chat_seeds() -> None:
    """一次性清理 Qdrant 上遗留的短查询/离题种子命名空间数据.

    第二层 Embeddings+Qdrant 语义匹配已移除, 原种子数据 (short_query/off_topic namespace)
    不再使用, 启动时清理一次避免残留.

    幂等: 多次调用安全; Qdrant 不可用时仅告警不阻断启动.
    """
    try:
        from src.rag.qdrant_manager import get_qdrant_manager

        mgr = get_qdrant_manager()
        await mgr.ensure_collection()
        # legacy namespace: {agent_id}-chat:short_query / {agent_id}-chat:off_topic
        chat_ns = f"{mgr.settings.agent_name}-chat"
        legacy_namespaces = [f"{chat_ns}:short_query", f"{chat_ns}:off_topic"]
        for ns in legacy_namespaces:
            count = await mgr.count_points_in_namespace(ns)
            if count > 0:
                await mgr.delete_by_namespace(ns)
                logger.info("P2 清理: 已删除 Qdrant 旧种子 namespace=%s (count=%d)", ns, count)
            else:
                logger.debug("P2 清理: namespace=%s 无数据, 跳过", ns)
    except Exception as e:  # noqa: BLE001
        logger.warning("P2 清理 Qdrant 旧种子失败 (不阻断启动): %s", e)
