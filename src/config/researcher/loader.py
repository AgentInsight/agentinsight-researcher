"""闲聊响应配置加载器.

配置 SSOT, 业务代码禁止硬编码.
闲聊响应所需的正则模式/种子/短语/模板/兜底话术统一从 YAML + Jinja2 加载.

Rasa FallbackClassifier / Dify 失效回复 / NeMo topic rail 的配置化思路:
- patterns: 闲聊正则 (规则层快速拦截)
- phrases: 高频短查询短语 (精确匹配)
- seeds: 短查询/离题种子 (Embeddings 语义匹配预填充)
- prompts: Jinja2 模板 (LLM 生成式回复)
- replies: 兜底话术 (零 LLM 成本直接返回)

文件布局 (config_dir 默认为本模块所在目录):
    config_dir/
    ├── chitchat_patterns.yaml      # 闲聊正则
    ├── short_query_phrases.yaml    # 高频短语
    ├── short_query_seeds.yaml      # 短查询种子
    ├── off_topic_seeds.yaml        # 离题种子 (含 category)
    ├── prompts/
    │   └── chitchat/
    │       ├── short_query.j2
    │       ├── off_topic_greeting.j2
    │       ├── off_topic_identity.j2
    │       ├── off_topic_emotion.j2
    │       ├── off_topic_entertainment.j2
    │       ├── off_topic_common_sense.j2
    │       ├── off_topic_capability.j2
    │       └── off_topic_topic_switch.j2
    └── replies/
        ├── short_query.yaml         # 短查询兜底话术
        └── off_topic.yaml           # 离题兜底话术 (按分类)
"""

from __future__ import annotations

import logging
import random
import re
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.config.researcher.persona import PersonaConfig

logger = logging.getLogger(__name__)


class ChitchatConfigBundle:
    """闲聊响应配置包 (YAML + Jinja2 统一加载).

    一次构造即加载全部静态资源 (YAML 数据 + Jinja2 Environment),
    后续查询均为内存读取, 线程安全 (只读访问).

    Attributes:
        config_dir: 配置根目录 (默认为 src/config/researcher/).
        persona: 默认 PersonaConfig, 可被外部覆盖.
    """

    # 默认配置目录: 本模块所在目录 (src/config/researcher/)
    _DEFAULT_CONFIG_DIR: Path = Path(__file__).parent

    def __init__(self, config_dir: Path | None = None) -> None:
        """初始化配置包, 加载全部 YAML 并构建 Jinja2 Environment.

        Args:
            config_dir: 配置根目录, 留空则使用默认目录 (本模块所在目录).
        """
        self.config_dir: Path = config_dir or self._DEFAULT_CONFIG_DIR

        # 加载 YAML 数据文件 (yaml.safe_load, UTF-8)
        self._patterns_raw: dict[str, Any] = self._load_yaml("chitchat_patterns.yaml")
        self._phrases_raw: dict[str, Any] = self._load_yaml("short_query_phrases.yaml")
        self._short_query_seeds_raw: dict[str, Any] = self._load_yaml("short_query_seeds.yaml")
        self._off_topic_seeds_raw: dict[str, Any] = self._load_yaml("off_topic_seeds.yaml")
        self._short_query_replies: dict[str, Any] = self._load_yaml("replies/short_query.yaml")
        self._off_topic_replies: dict[str, Any] = self._load_yaml("replies/off_topic.yaml")

        # Jinja2 Environment (trim_blocks/lstrip_blocks 去除模板块标记行首尾空白)
        prompts_dir: Path = self.config_dir / "prompts"
        self._jinja_env: Environment = Environment(
            loader=FileSystemLoader(str(prompts_dir)),
            autoescape=select_autoescape(),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )

        # 默认 persona (可被外部覆盖)
        self.persona: PersonaConfig = PersonaConfig()

        # 编译后的正则缓存 (懒编译, 首次调用 get_patterns 时填充)
        self._compiled_patterns: tuple[re.Pattern[str], ...] | None = None

        logger.debug(
            "ChitchatConfigBundle 已加载 (config_dir=%s, patterns=%d, phrases=%d, "
            "short_query_seeds=%d, off_topic_seeds=%d)",
            self.config_dir,
            len(self._patterns_raw.get("patterns", [])),
            len(self._phrases_raw.get("phrases", [])),
            len(self._short_query_seeds_raw.get("seeds", [])),
            len(self._off_topic_seeds_raw.get("seeds", [])),
        )

    # ========== 内部加载方法 ==========

    def _load_yaml(self, relative_path: str) -> dict[str, Any]:
        """加载 YAML 文件 (yaml.safe_load, UTF-8 编码).

        Args:
            relative_path: 相对 config_dir 的路径 (如 "chitchat_patterns.yaml"
                或 "replies/short_query.yaml").

        Returns:
            解析后的字典; 文件为空时返回空字典.
        """
        path: Path = self.config_dir / relative_path
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    # ========== Jinja2 模板渲染 ==========

    def render_prompt(self, template_name: str, **kwargs: object) -> str:
        """渲染 Jinja2 模板.

        Args:
            template_name: 模板相对路径 (相对 prompts/ 目录),
                如 "chitchat/short_query.j2".
            **kwargs: 模板变量, 通常含 persona (PersonaConfig) 和 query (str).

        Returns:
            渲染后的字符串.

        Raises:
            jinja2.TemplateNotFound: 模板不存在时抛出.
        """
        template = self._jinja_env.get_template(template_name)
        return str(template.render(**kwargs))

    # ========== 兜底话术 ==========

    def random_reply(self, category: str, subcategory: str | None = None) -> str:
        """从兜底话术 YAML 随机取一条.

        短查询话术结构 (replies/short_query.yaml):
            short_query: [str, ...]

        离题话术结构 (replies/off_topic.yaml):
            off_topic:
              greeting: [str, ...]
              identity: [str, ...]
              ...

        Args:
            category: 话术分类, "short_query" 或 "off_topic".
            subcategory: 子分类, 仅 off_topic 需要
                (greeting/identity/emotion/entertainment/common_sense/
                 capability_check/topic_switch/evaluation).
                off_topic 且 subcategory=None 时从所有子分类汇总随机取.

        Returns:
            随机话术字符串.

        Raises:
            KeyError: 分类或子分类不存在, 或对应话术列表为空.
        """
        if category == "short_query":
            replies: list[str] = list(self._short_query_replies.get("short_query", []))
        elif category == "off_topic":
            off_topic: dict[str, Any] = self._off_topic_replies.get("off_topic", {})
            if subcategory is None:
                # 未指定子分类时, 从所有子分类汇总随机取
                replies = []
                for v in off_topic.values():
                    if isinstance(v, list):
                        replies.extend(v)
            else:
                replies = list(off_topic.get(subcategory, []))
        else:
            raise KeyError(f"未知话术分类: {category}")

        if not replies:
            sub_hint = f"/{subcategory}" if subcategory else ""
            raise KeyError(f"话术分类 {category}{sub_hint} 无可用兜底回复")

        return random.choice(replies)

    # ========== 正则模式 ==========

    def get_patterns(self) -> tuple[re.Pattern[str], ...]:
        """获取闲聊正则模式 (编译并缓存).

        正则统一使用 re.UNICODE 标志, 中文匹配 \u4e00-\u9fa5.
        编译结果缓存, 多次调用返回同一 tuple.

        Returns:
            编译后的正则 Pattern 元组.
        """
        if self._compiled_patterns is None:
            raw_patterns: list[str] = self._patterns_raw.get("patterns", [])
            self._compiled_patterns = tuple(re.compile(p, re.UNICODE) for p in raw_patterns)
        return self._compiled_patterns

    # ========== 种子列表 ==========

    def get_short_query_seeds(self) -> list[str]:
        """获取短查询种子列表 (用于 Qdrant 语义匹配预填充).

        Returns:
            短查询种子字符串列表 (副本, 外部修改不影响内部缓存).
        """
        return list(self._short_query_seeds_raw.get("seeds", []))

    def get_off_topic_seeds(self) -> list[dict[str, Any]]:
        """获取离题种子列表 (每条含 text + category 字段).

        Returns:
            离题种子字典列表 (副本), 每条形如 {"text": "...", "category": "..."}.
        """
        return list(self._off_topic_seeds_raw.get("seeds", []))

    def get_short_query_phrases(self) -> frozenset[str]:
        """获取高频短查询短语 (frozenset, 英文小写).

        用于规则层精确匹配 (query.lower() in phrases).

        Returns:
            不可变短语集合.
        """
        return frozenset(self._phrases_raw.get("phrases", []))


# ========== 全局单例 ==========

_bundle: ChitchatConfigBundle | None = None


def get_chitchat_config() -> ChitchatConfigBundle:
    """获取全局 ChitchatConfigBundle 单例.

    首次调用时构造 (加载全部 YAML + Jinja2 Environment),
    后续调用返回同一实例 (只读, 线程安全).
    """
    global _bundle
    if _bundle is None:
        _bundle = ChitchatConfigBundle()
    return _bundle
