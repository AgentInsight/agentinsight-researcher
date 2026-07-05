"""查询意图分类器 (P0-Future-05/06, P1-Future-07).

AGENTS.md 第 5 章: 节点纯函数, 无副作用.
AGENTS.md 第 9 章: LLM 调用经 llm/ 的 LLMClient (LiteLLM), 禁厂商 SDK 直连.
AGENTS.md 第 10 章: 用 trace_chain 包裹 (禁 agentinsight.observe 装饰器).
AGENTS.md 第 7 章: Embeddings 经 rag/embeddings.py, Qdrant 经 rag/qdrant_manager.py, 禁直连.

三层分类逻辑 (P1-Future-07 升级为四分类, 对标 Rasa FallbackClassifier / Dify 失效回复 / NeMo topic rail):
- 第一层(规则): 长度<min_length / 纯数字 / 纯标点 / 闲聊正则 → SHORT_QUERY 或 OFF_TOPIC
- 第二层(Embeddings 语义): Qdrant short_query_patterns / off_topic_patterns namespace 语义匹配
  - SHORT_QUERY 命中阈值 0.85 (短句精确匹配)
  - OFF_TOPIC 命中阈值 0.75 (闲聊句子语义距离更大, 阈值放宽)
- 第三层(LLM FAST 层): 仅当前两层未命中时, 用 LLMTier.FAST 分类 RESEARCH / CHAT / OFF_TOPIC
- LLM 调用失败默认 OFF_TOPIC (业界标准: 走最轻路径, 避免误导向高成本研究流程)
- 语义层失败降级到 LLM 层 (不阻断主流程)

短查询(如"你好"/"1"/"天气")与离题闲聊(如"今天怎么样"/"讲个笑话"/"你多大了")直接返回
settings.short_query_reply / settings.off_topic_reply, 不走任何 graph, 零 LLM 成本.
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

# 离题/闲聊正则模式 (P1-Future-07)
# 用于在规则层快速拦截高频闲聊句式, 避免落入 Embeddings 语义层误判
# 命中即返回 OFF_TOPIC (零 LLM 成本, 直接返回 off_topic_reply)
# 设计原则: 仅匹配明显与研究/分析无关的句式, 模糊语义留给 Embeddings/LLM 层
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

# 离题/闲聊种子模式 (P1-Future-07, 懒初始化时预填充到 Qdrant off_topic_patterns namespace)
# 涵盖问候/身份/娱乐/常识/情感/天气/时间等闲聊, 用 Embeddings 语义匹配
# 按分类组织 (118 个不重复种子, 与 _SHORT_QUERY_SEED 互补:
#   - SHORT_QUERY_SEED 覆盖短词/短语 (长度通常 ≤6)
#   - _OFF_TOPIC_SEED 覆盖句子/完整问句 (语义匹配闲聊意图))
_OFF_TOPIC_SEED: list[str] = [
    # 询问 bot 身份/属性 (12)
    "你叫什么名字",
    "你叫啥",
    "你多大了",
    "你几岁了",
    "你是男是女",
    "你有女朋友吗",
    "你有男朋友吗",
    "你结婚了吗",
    "你在哪里",
    "你住哪里",
    "你是真人吗",
    "你有兄弟姐妹吗",
    # 询问 bot 心情/状态 (10)
    "今天怎么样",
    "你今天过得好吗",
    "你最近怎么样",
    "你开心吗",
    "你累吗",
    "你忙吗",
    "你心情怎么样",
    "你怎么了",
    "你在干嘛",
    "你在做什么",
    # 娱乐/创作请求 (12)
    "讲个笑话",
    "说个笑话",
    "讲个故事",
    "说个故事",
    "写首诗",
    "写首歌",
    "唱首歌",
    "出个谜语",
    "说个绕口令",
    "陪我玩个游戏",
    "给我讲个段子",
    "来个脑筋急转弯",
    # 常识/算术问题 (10)
    "1加1等于几",
    "1+1等于几",
    "2+2等于几",
    "天空为什么是蓝的",
    "太阳从哪边升起",
    "水为什么会沸腾",
    "地球是圆的吗",
    "地球有多大",
    "光速是多少",
    "人为什么要睡觉",
    # 时间/日期/天气 (10)
    "现在几点",
    "今天星期几",
    "今天天气怎么样",
    "明天天气如何",
    "今天会下雨吗",
    "今天冷吗",
    "今天热吗",
    "现在什么时间",
    "你家有几口人",
    "你会做饭吗",
    # 情绪/陪伴 (15)
    "我好开心",
    "我好难过",
    "我好累",
    "我好无聊",
    "我好孤独",
    "我心情不好",
    "我不开心",
    "我很难过",
    "我很郁闷",
    "我烦死了",
    "和我聊天",
    "陪我聊聊",
    "和我说说话",
    "我讨厌我自己",
    "我太开心了",
    # 评价/情感表达 (10)
    "你真聪明",
    "你真棒",
    "你真厉害",
    "你真笨",
    "你真傻",
    "我喜欢你",
    "我爱你",
    "我讨厌你",
    "我恨你",
    "你真可爱",
    # 转移话题/拒绝 (8)
    "我不想研究",
    "我不想用了",
    "算了",
    "不研究了",
    "不用了",
    "不需要了",
    "换个别的话题",
    "聊点别的",
    # 测试 bot 智能 (8)
    "你聪明吗",
    "你有意识吗",
    "你有感情吗",
    "你会思考吗",
    "你能听懂我说话吗",
    "你能理解我吗",
    "你明白我在说什么吗",
    "你是人工智能吗",
    # 英文闲聊补充 (13)
    "how are you today",
    "what's your name",
    "how old are you",
    "are you a robot",
    "are you human",
    "tell me a joke",
    "sing me a song",
    "what time is it",
    "what's the weather",
    "do you have feelings",
    "are you conscious",
    "i love you",
    "i'm bored",
    # 其他闲聊补充 (10, 替换与 _SHORT_QUERY_SEED 重复的项)
    "你最喜欢什么颜色",
    "你能记住我吗",
    "你有什么爱好",
    "你平时做什么",
    "你会唱歌吗",
    "你喜欢什么音乐",
    "你看过什么电影",
    "你有什么特长",
    "你喜欢读书吗",
    "你会做什么菜",
]

# 离题种子版本号, 用于增量更新 Qdrant (版本变化时触发重新写入)
_OFF_TOPIC_SEED_VERSION: str = "v1.0"


class QueryIntent(StrEnum):
    """查询意图类型."""

    RESEARCH = "research"  # 研究请求 → 走 researcher graph
    CHAT = "chat"  # 对话 (针对已有报告的追问) → 走 chat graph
    SHORT_QUERY = "short_query"  # 短查询 (问候/数字/标点/测试) → 直接返回回复语
    OFF_TOPIC = "off_topic"  # 离题/闲聊 (问候/身份/娱乐/常识/私人问题) → 直接返回离题回复语


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
    # 种子数据存在性缓存 (None=未知, True=有数据, False=无数据)
    # 避免每次 _semantic_match 都调用 embeddings (P1-04: embeddings 429 优化)
    _seed_has_data: bool | None
    _off_topic_seed_has_data: bool | None

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
        # P1-Future-07: 离题种子独立初始化状态 (与短查询种子分离, 互不阻断)
        self._off_topic_seed_initialized = False
        # P1-04: 种子数据存在性缓存 (避免无数据时调用 embeddings)
        self._seed_has_data = None
        self._off_topic_seed_has_data = None

    @property
    def _short_query_namespace(self) -> str:
        """短查询种子模式的 Qdrant namespace.

        CHITCHAT_FAST_LLM_OPTIMIZATION_PLAN.md: 拆分为 chat/data 两个 namespace 池.
        短查询种子归入 chat namespace: {agent_id}-chat:short_query.
        种子模式为全局通用问候/告别等, 不含 user_id (非用户私有数据).
        """
        return f"{self._qdrant.build_chat_namespace()}:short_query"

    @property
    def _off_topic_namespace(self) -> str:
        """离题/闲聊种子模式的 Qdrant namespace (P1-Future-07).

        CHITCHAT_FAST_LLM_OPTIMIZATION_PLAN.md: 拆分为 chat/data 两个 namespace 池.
        离题种子归入 chat namespace: {agent_id}-chat:off_topic.
        种子模式为全局通用闲聊模式, 不含 user_id (非用户私有数据).
        """
        return f"{self._qdrant.build_chat_namespace()}:off_topic"

    def _rule_classify(self, query: str) -> _RuleResult | None:
        """第一层规则分类 (快速).

        Returns:
            命中规则时返回 _RuleResult(SHORT_QUERY 或 OFF_TOPIC, reason);
            未命中返回 None (需进入第二层语义分类).
        """
        q = query.strip()

        # P1-Future-07: 离题/闲聊正则优先匹配 (在短查询规则之前)
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
        #    不含空格/数字/标点; 中文 2-6 字短词亦拦截 (避免 "你好" 落入语义层误判)
        #    P2: 配置化 (原硬编码 6, 现 settings.query_classify_single_word_max_chars)
        if len(q) <= self.settings.query_classify_single_word_max_chars and _SINGLE_WORD_RE.match(
            q
        ):
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

            # P1-04: 种子数据存在性检查 (避免无数据时调用 embeddings, 减少 429)
            # 缓存结果, 避免每次都调用 Qdrant count
            if self._seed_has_data is None:
                self._seed_has_data = await self._qdrant.namespace_has_data(
                    self._short_query_namespace
                )
                if not self._seed_has_data:
                    logger.warning(
                        "短查询种子 namespace 无数据, 语义层降级到 LLM 层 "
                        "(避免无意义 embeddings 调用)"
                    )
            if not self._seed_has_data:
                return False

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

    async def _ensure_off_topic_seed_patterns(self) -> None:
        """懒初始化离题种子模式到 Qdrant (P1-Future-07, 带版本校验, 支持增量更新).

        使用双重检查锁定避免并发重复写入.
        通过 _OFF_TOPIC_SEED_VERSION 做 payload 版本校验:
        - 版本匹配 → 跳过写入
        - 版本不匹配或不存在 → 删除旧数据, 重新写入全部种子
        预填充失败不阻断, 后续语义匹配会降级到 LLM 层.
        """
        if self._off_topic_seed_initialized:
            return

        async with self._seed_lock:
            if self._off_topic_seed_initialized:
                return
            try:
                await self._qdrant.ensure_collection()

                # 用首条种子向量检索, 检查是否已存在当前版本
                probe_vector = await self._embeddings.embed_query(_OFF_TOPIC_SEED[0])
                existing = await self._qdrant.search(
                    query_vector=probe_vector,
                    namespaces=[self._off_topic_namespace],
                    limit=1,
                )
                if existing:
                    top_meta = existing[0].get("metadata", {}) or {}
                    if top_meta.get("seed_version") == _OFF_TOPIC_SEED_VERSION:
                        self._off_topic_seed_initialized = True
                        logger.debug(
                            "离题种子已存在 (version=%s), 跳过写入",
                            _OFF_TOPIC_SEED_VERSION,
                        )
                        return

                # 版本不匹配或不存在, 重新写入
                await self._write_off_topic_seed_patterns()
                self._off_topic_seed_initialized = True
            except Exception as e:  # noqa: BLE001
                # 预填充失败不阻断, 标记已尝试避免反复失败重试
                logger.warning("离题种子初始化失败 (降级为仅规则+LLM 层): %s", e)
                self._off_topic_seed_initialized = True

    async def _write_off_topic_seed_patterns(self) -> None:
        """写入/更新离题种子到 Qdrant (P1-Future-07, 版本变化时先删后写).

        AGENTS.md 第 7 章: payload 必须含 content+metadata+namespace;
        种子版本号写入 metadata.seed_version 做增量更新校验.
        """
        # 版本变化时先清理旧数据 (避免残留旧版本种子)
        await self._qdrant.delete_by_namespace(self._off_topic_namespace)

        points: list[dict[str, Any]] = [
            {
                "content": pattern,
                "metadata": {
                    "type": "off_topic_seed",
                    "category": "chat",
                    "seed_version": _OFF_TOPIC_SEED_VERSION,
                },
            }
            for pattern in _OFF_TOPIC_SEED
        ]
        await self._qdrant.upsert_points(
            namespace=self._off_topic_namespace,
            points=points,
        )
        logger.info(
            "离题种子已写入 Qdrant (namespace=%s, version=%s, count=%d)",
            self._off_topic_namespace,
            _OFF_TOPIC_SEED_VERSION,
            len(points),
        )

    async def _semantic_match_off_topic(self, query: str) -> bool:
        """第二层 Embeddings 语义匹配离题/闲聊 (P1-Future-07).

        1. 将 query 用 EmbeddingsClient 向量化
        2. 在 Qdrant off_topic_patterns namespace 搜索
        3. top-1 score > off_topic_similarity_threshold → OFF_TOPIC

        任何异常都降级为 False (不阻断主流程, 进入 LLM 层).
        """
        try:
            await self._ensure_off_topic_seed_patterns()

            # P1-04: 种子数据存在性检查 (避免无数据时调用 embeddings, 减少 429)
            # 缓存结果, 避免每次都调用 Qdrant count
            if self._off_topic_seed_has_data is None:
                self._off_topic_seed_has_data = await self._qdrant.namespace_has_data(
                    self._off_topic_namespace
                )
                if not self._off_topic_seed_has_data:
                    logger.warning(
                        "离题种子 namespace 无数据, 离题语义层降级到 LLM 层 "
                        "(避免无意义 embeddings 调用)"
                    )
            if not self._off_topic_seed_has_data:
                return False

            query_vector = await self._embeddings.embed_query(query)
            if not query_vector:
                logger.warning("Embedding 返回空向量, 离题语义层降级")
                return False

            results = await self._qdrant.search(
                query_vector=query_vector,
                namespaces=[self._off_topic_namespace],
                limit=1,
            )

            if not results:
                return False

            top_score = float(results[0].get("score") or 0.0)
            matched = top_score > self.settings.off_topic_similarity_threshold
            if matched:
                logger.debug(
                    "语义匹配命中离题: query=%r top_score=%.4f threshold=%.2f",
                    query[:50],
                    top_score,
                    self.settings.off_topic_similarity_threshold,
                )
            return matched
        except Exception as e:  # noqa: BLE001
            logger.warning("离题语义匹配失败, 降级到 LLM 层: %s", e)
            return False

    async def _llm_classify(self, query: str, has_report: bool) -> QueryIntent:
        """第三层 LLM FAST 分类 (P1-Future-07 升级为三分类).

        用 LLMTier.FAST (deepseek-chat, temperature=0.0) 分类 RESEARCH / CHAT / OFF_TOPIC.
        失败时默认 settings.llm_classify_fallback (默认 OFF_TOPIC, 业界标准: 走最轻路径).

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

        system_prompt = (
            "你是查询意图分类器. 根据用户查询判断意图类别, 仅返回 JSON.\n\n"
            "类别定义:\n"
            '- "research": 需要深入研究并生成报告的主题 '
            '(如"分析新能源汽车市场"|"AI 在医疗的应用"|"比较 React 和 Vue")\n'
            '- "chat": 针对已有研究报告的追问/澄清/讨论 '
            '(如"这个数据来源是什么"|"展开讲讲第二点"|"总结一下")\n'
            '- "off_topic": 与研究/分析无关的闲聊/问候/身份询问/娱乐/常识/私人问题 '
            '(如"你好"|"今天怎么样"|"讲个笑话"|"你多大了"|"1+1等于几"|"天气如何")\n\n'
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
            # P1-Future-07: 业界标准 - LLM 失败走最轻路径 (默认 OFF_TOPIC, 避免误导向研究)
            logger.warning(
                "LLM 意图分类失败, 走 llm_classify_fallback=%s (业界默认最轻路径): %s",
                self.settings.llm_classify_fallback,
                e,
            )
            return self._fallback_intent()

    def _fallback_intent(self) -> QueryIntent:
        """返回配置的 LLM 失败兜底意图 (P1-Future-07).

        默认 OFF_TOPIC (业界标准: 走最轻路径, 避免误导向高成本研究流程).
        可通过 settings.llm_classify_fallback 配置为 "research" 覆盖.
        """
        if self.settings.llm_classify_fallback == "research":
            return QueryIntent.RESEARCH
        return QueryIntent.OFF_TOPIC

    async def classify(self, query: str, has_report: bool) -> QueryIntent:
        """分类查询意图 (P1-Future-07 升级为四分类).

        三层分类:
        1. 规则层优先 (短查询保护 + 闲聊正则)
        2. Embeddings 语义层 (短查询 namespace + 离题 namespace, 规则层未命中时)
        3. LLM FAST 层 (前两层未命中时, 三分类 RESEARCH/CHAT/OFF_TOPIC)

        Args:
            query: 用户原始查询
            has_report: 当前会话是否已有报告 (True 时 LLM 倾向 CHAT)

        Returns:
            QueryIntent 枚举 (RESEARCH / CHAT / SHORT_QUERY / OFF_TOPIC)
        """
        async with trace_chain(
            name="query-intent-classifier",
            input={
                "query": query[:200],
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

            # 第二层: Embeddings 语义匹配 (并行查询短查询 + 离题两个 namespace)
            # 并行执行避免串行延迟; 两个语义层独立, 任一命中即返回
            short_match, off_topic_match = await asyncio.gather(
                self._semantic_match(query),
                self._semantic_match_off_topic(query),
            )

            if short_match:
                span.update(
                    output={
                        "intent": QueryIntent.SHORT_QUERY.value,
                        "layer": "semantic",
                    },
                    metadata={
                        "threshold": self.settings.short_query_similarity_threshold,
                        "namespace": "short_query_patterns",
                    },
                )
                return QueryIntent.SHORT_QUERY

            if off_topic_match:
                span.update(
                    output={
                        "intent": QueryIntent.OFF_TOPIC.value,
                        "layer": "semantic",
                    },
                    metadata={
                        "threshold": self.settings.off_topic_similarity_threshold,
                        "namespace": "off_topic_patterns",
                    },
                )
                return QueryIntent.OFF_TOPIC

            # 第三层: LLM FAST 分类 (三分类, 失败兜底 OFF_TOPIC)
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
