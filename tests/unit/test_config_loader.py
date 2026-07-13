"""单元测试: ChitchatConfigBundle 闲聊配置加载器.

验证 src/config/researcher/loader.py:
- __init__: 加载 YAML + 构建 Jinja2 Environment
- _load_yaml: YAML 文件读取 (UTF-8 / safe_load / 空文件兜底)
- render_prompt: Jinja2 模板渲染 (含 persona 变量)
- random_reply: 兜底话术随机取 (short_query / off_topic 分类)
- get_patterns: 闲聊正则编译 + 缓存
- get_short_query_seeds / get_off_topic_seeds / get_short_query_phrases
- get_chitchat_config: 全局单例

配置 SSOT, 业务代码禁止硬编码.
单元测试使用真实配置目录 (src/config/researcher/) 验证集成.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

import pytest
from jinja2 import TemplateNotFound

from src.config.researcher.loader import (
    ChitchatConfigBundle,
    get_chitchat_config,
)
from src.config.researcher.persona import PersonaConfig

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture()
def config_dir() -> Path:
    """获取真实配置目录路径."""
    return Path(__file__).parent.parent.parent / "src" / "config" / "researcher"


@pytest.fixture()
def bundle(config_dir: Path) -> ChitchatConfigBundle:
    """构造 ChitchatConfigBundle (加载真实 YAML)."""
    return ChitchatConfigBundle(config_dir=config_dir)


@pytest.fixture(autouse=True)
def reset_global_bundle() -> None:
    """每个用例前重置全局单例 _bundle, 避免跨用例污染."""
    import src.config.researcher.loader as loader_module

    loader_module._bundle = None


# ========== __init__: 加载 YAML ==========


class TestBundleInit:
    """ChitchatConfigBundle 初始化测试."""

    def test_init_loads_all_yaml_files(self, bundle: ChitchatConfigBundle) -> None:
        """初始化应加载所有 6 个 YAML 文件."""
        # 6 个 YAML: chitchat_patterns / short_query_phrases / short_query_seeds /
        # off_topic_seeds / replies/short_query / replies/off_topic
        assert bundle._patterns_raw is not None
        assert bundle._phrases_raw is not None
        assert bundle._short_query_seeds_raw is not None
        assert bundle._off_topic_seeds_raw is not None
        assert bundle._short_query_replies is not None
        assert bundle._off_topic_replies is not None

    def test_init_default_persona(self, bundle: ChitchatConfigBundle) -> None:
        """初始化应创建默认 PersonaConfig."""
        assert isinstance(bundle.persona, PersonaConfig)
        assert bundle.persona.signature == "AgentInsight Researcher"

    def test_init_config_dir_attribute(self, bundle: ChitchatConfigBundle, config_dir: Path) -> None:
        """config_dir 属性应保存配置目录路径."""
        assert bundle.config_dir == config_dir

    def test_init_default_config_dir_when_none(self) -> None:
        """无 config_dir 参数 → 使用默认目录 (本模块所在目录)."""
        bundle = ChitchatConfigBundle()
        expected_dir = Path(__file__).parent.parent.parent / "src" / "config" / "researcher"
        assert bundle.config_dir == expected_dir

    def test_init_compiled_patterns_none_before_first_call(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """初始化后 _compiled_patterns 应为 None (懒编译)."""
        assert bundle._compiled_patterns is None

    def test_init_patterns_raw_contains_patterns_key(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """patterns YAML 应含 'patterns' 键."""
        assert "patterns" in bundle._patterns_raw
        assert isinstance(bundle._patterns_raw["patterns"], list)
        assert len(bundle._patterns_raw["patterns"]) > 0

    def test_init_phrases_raw_contains_phrases_key(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """phrases YAML 应含 'phrases' 键."""
        assert "phrases" in bundle._phrases_raw
        assert isinstance(bundle._phrases_raw["phrases"], list)
        assert len(bundle._phrases_raw["phrases"]) > 0

    def test_init_short_query_seeds_contains_seeds_key(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """short_query_seeds YAML 应含 'seeds' 键."""
        assert "seeds" in bundle._short_query_seeds_raw
        assert len(bundle._short_query_seeds_raw["seeds"]) > 0

    def test_init_off_topic_seeds_contains_seeds_key(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """off_topic_seeds YAML 应含 'seeds' 键."""
        assert "seeds" in bundle._off_topic_seeds_raw
        assert len(bundle._off_topic_seeds_raw["seeds"]) > 0


# ========== _load_yaml: YAML 文件加载 ==========


class TestLoadYaml:
    """_load_yaml 方法测试."""

    def test_load_yaml_returns_dict(self, bundle: ChitchatConfigBundle) -> None:
        """_load_yaml 应返回字典."""
        result = bundle._load_yaml("chitchat_patterns.yaml")
        assert isinstance(result, dict)

    def test_load_yaml_unicode_content(self, bundle: ChitchatConfigBundle) -> None:
        """_load_yaml 应正确读取 UTF-8 中文内容."""
        result = bundle._load_yaml("chitchat_patterns.yaml")
        # 中文 pattern 应被正确解析
        patterns = result.get("patterns", [])
        assert any("你好" in p for p in patterns)

    def test_load_yaml_nested_path(self, bundle: ChitchatConfigBundle) -> None:
        """_load_yaml 应支持嵌套路径 (replies/short_query.yaml)."""
        result = bundle._load_yaml("replies/short_query.yaml")
        assert "short_query" in result
        assert isinstance(result["short_query"], list)

    def test_load_yaml_missing_file_raises(self, tmp_path: Path) -> None:
        """文件不存在时应抛 FileNotFoundError (构造时即加载)."""
        # 构造时已加载所有 YAML, 缺失文件应抛 FileNotFoundError
        # tmp_path 无所需 YAML, 构造时应抛 FileNotFoundError
        with pytest.raises(FileNotFoundError):
            ChitchatConfigBundle(config_dir=tmp_path)

    def test_load_yaml_empty_file_returns_empty_dict(
        self, tmp_path: Path
    ) -> None:
        """空 YAML 文件应返回空字典 (yaml.safe_load 返回 None → or {})."""
        # 构造最小配置目录 (空 YAML 文件)
        (tmp_path / "chitchat_patterns.yaml").write_text("", encoding="utf-8")
        (tmp_path / "short_query_phrases.yaml").write_text("", encoding="utf-8")
        (tmp_path / "short_query_seeds.yaml").write_text("", encoding="utf-8")
        (tmp_path / "off_topic_seeds.yaml").write_text("", encoding="utf-8")
        (tmp_path / "replies").mkdir()
        (tmp_path / "replies" / "short_query.yaml").write_text("", encoding="utf-8")
        (tmp_path / "replies" / "off_topic.yaml").write_text("", encoding="utf-8")
        (tmp_path / "prompts").mkdir()

        bundle = ChitchatConfigBundle(config_dir=tmp_path)
        assert bundle._patterns_raw == {}
        assert bundle._phrases_raw == {}


# ========== render_prompt: Jinja2 模板渲染 ==========


class TestRenderPrompt:
    """render_prompt 方法测试."""

    def test_render_short_query_template(self, bundle: ChitchatConfigBundle) -> None:
        """渲染 short_query.j2 模板."""
        result = bundle.render_prompt(
            "chitchat/short_query.j2",
            persona=bundle.persona,
            query="你好",
        )
        assert isinstance(result, str)
        assert len(result) > 0

    def test_render_off_topic_greeting_template(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """渲染 off_topic_greeting.j2 模板."""
        result = bundle.render_prompt(
            "chitchat/off_topic_greeting.j2",
            persona=bundle.persona,
            query="你好啊",
        )
        assert isinstance(result, str)
        assert len(result) > 0

    def test_render_template_includes_persona_identity(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """渲染结果应包含 persona.identity 内容."""
        custom_persona = PersonaConfig(identity="你是一个测试助手")
        result = bundle.render_prompt(
            "chitchat/short_query.j2",
            persona=custom_persona,
            query="你好",
        )
        assert "测试助手" in result

    def test_render_template_includes_query(self, bundle: ChitchatConfigBundle) -> None:
        """渲染结果应包含 query 内容."""
        result = bundle.render_prompt(
            "chitchat/short_query.j2",
            persona=bundle.persona,
            query="特殊查询标记",
        )
        assert "特殊查询标记" in result

    def test_render_template_not_found_raises(self, bundle: ChitchatConfigBundle) -> None:
        """模板不存在应抛 TemplateNotFound."""
        with pytest.raises(TemplateNotFound):
            bundle.render_prompt("chitchat/nonexistent_template.j2", query="x")

    def test_render_template_with_custom_persona(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """自定义 persona 应被注入模板."""
        custom_persona = PersonaConfig(
            identity="金融分析助手",
            tone="严谨专业",
            signature="金融 Agent",
        )
        result = bundle.render_prompt(
            "chitchat/short_query.j2",
            persona=custom_persona,
            query="分析",
        )
        assert "金融分析助手" in result or "金融 Agent" in result

    def test_render_returns_string_type(self, bundle: ChitchatConfigBundle) -> None:
        """render_prompt 应返回 str 类型 (即使模板返回非 str)."""
        result = bundle.render_prompt(
            "chitchat/short_query.j2",
            persona=bundle.persona,
            query="x",
        )
        assert type(result) is str


# ========== random_reply: 兜底话术 ==========


class TestRandomReply:
    """random_reply 方法测试."""

    def test_random_reply_short_query_returns_string(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """random_reply('short_query') 应返回字符串."""
        result = bundle.random_reply("short_query")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_random_reply_short_query_in_known_replies(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """short_query 随机话术应来自 replies/short_query.yaml 列表."""
        known_replies = bundle._short_query_replies.get("short_query", [])
        result = bundle.random_reply("short_query")
        assert result in known_replies

    def test_random_reply_off_topic_with_subcategory(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """random_reply('off_topic', subcategory) 应返回对应分类话术."""
        result = bundle.random_reply("off_topic", "greeting")
        assert isinstance(result, str)
        known_greetings = bundle._off_topic_replies.get("off_topic", {}).get("greeting", [])
        assert result in known_greetings

    def test_random_reply_off_topic_no_subcategory_picks_from_all(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """random_reply('off_topic', None) 应从所有子分类汇总随机取."""
        result = bundle.random_reply("off_topic")
        assert isinstance(result, str)
        # 汇总所有子分类话术
        all_replies: list[str] = []
        for v in bundle._off_topic_replies.get("off_topic", {}).values():
            if isinstance(v, list):
                all_replies.extend(v)
        assert result in all_replies

    @pytest.mark.parametrize(
        "subcategory",
        [
            "greeting",
            "identity",
            "emotion",
            "entertainment",
            "common_sense",
            "capability_check",
            "topic_switch",
            "evaluation",
        ],
    )
    def test_random_reply_off_topic_all_subcategories(
        self, bundle: ChitchatConfigBundle, subcategory: str
    ) -> None:
        """off_topic 所有 8 个子分类应都能取到话术."""
        result = bundle.random_reply("off_topic", subcategory)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_random_reply_unknown_category_raises_keyerror(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """未知分类应抛 KeyError."""
        with pytest.raises(KeyError, match="未知话术分类"):
            bundle.random_reply("invalid_category")

    def test_random_reply_unknown_subcategory_raises_keyerror(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """未知子分类 + 空列表应抛 KeyError (无可用兜底回复)."""
        with pytest.raises(KeyError, match="无可用兜底回复"):
            bundle.random_reply("off_topic", "nonexistent_subcategory")

    def test_random_reply_with_seed_reproducible(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """相同 random.seed 应产生相同话术."""
        random.seed(42)
        result1 = bundle.random_reply("short_query")
        random.seed(42)
        result2 = bundle.random_reply("short_query")
        assert result1 == result2

    def test_random_reply_off_topic_distributes_across_subcategories(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """多次取 off_topic (无 subcategory) 应从不同子分类取 (验证汇总逻辑)."""
        random.seed(0)
        results = {bundle.random_reply("off_topic") for _ in range(20)}
        # 应至少能取到 2 种不同话术 (汇总了多个子分类)
        assert len(results) >= 2


# ========== get_patterns: 正则模式编译 ==========


class TestGetPatterns:
    """get_patterns 方法测试."""

    def test_get_patterns_returns_tuple(self, bundle: ChitchatConfigBundle) -> None:
        """get_patterns 应返回 tuple."""
        patterns = bundle.get_patterns()
        assert isinstance(patterns, tuple)
        assert len(patterns) > 0

    def test_get_patterns_elements_are_compiled_regex(self, bundle: ChitchatConfigBundle) -> None:
        """每个 pattern 应为编译后的 re.Pattern."""
        patterns = bundle.get_patterns()
        for p in patterns:
            assert isinstance(p, re.Pattern)

    def test_get_patterns_caches_result(self, bundle: ChitchatConfigBundle) -> None:
        """多次调用应返回同一 tuple (缓存)."""
        first = bundle.get_patterns()
        second = bundle.get_patterns()
        assert first is second  # 同一对象引用

    def test_get_patterns_compiled_with_unicode_flag(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """正则应使用 re.UNICODE 标志编译."""
        patterns = bundle.get_patterns()
        # re.UNICODE 在 Python 3 str 模式下默认开启, flags 应含 UNICODE
        assert patterns[0].flags & re.UNICODE

    def test_get_patterns_can_match_chinese(self, bundle: ChitchatConfigBundle) -> None:
        """编译后的正则应能匹配中文 (你好.*)."""
        patterns = bundle.get_patterns()
        # 找到 "你好.*" 模式
        matched = False
        for p in patterns:
            if p.match("你好啊"):
                matched = True
                break
        assert matched

    def test_get_patterns_count_matches_yaml(self, bundle: ChitchatConfigBundle) -> None:
        """编译后的 pattern 数量应等于 YAML 中的 pattern 数量."""
        patterns = bundle.get_patterns()
        raw_count = len(bundle._patterns_raw.get("patterns", []))
        assert len(patterns) == raw_count


# ========== get_short_query_seeds / get_off_topic_seeds / get_short_query_phrases ==========


class TestSeedGetters:
    """种子/短语 getter 方法测试."""

    def test_get_short_query_seeds_returns_list(self, bundle: ChitchatConfigBundle) -> None:
        """get_short_query_seeds 应返回 list."""
        seeds = bundle.get_short_query_seeds()
        assert isinstance(seeds, list)
        assert len(seeds) > 0

    def test_get_short_query_seeds_elements_are_strings(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """短查询种子元素应为 str."""
        seeds = bundle.get_short_query_seeds()
        for s in seeds:
            assert isinstance(s, str)

    def test_get_short_query_seeds_returns_copy(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """get_short_query_seeds 应返回副本 (外部修改不影响内部)."""
        seeds1 = bundle.get_short_query_seeds()
        original_len = len(seeds1)
        seeds1.append("外部新增种子")
        seeds2 = bundle.get_short_query_seeds()
        assert len(seeds2) == original_len

    def test_get_off_topic_seeds_returns_list(self, bundle: ChitchatConfigBundle) -> None:
        """get_off_topic_seeds 应返回 list."""
        seeds = bundle.get_off_topic_seeds()
        assert isinstance(seeds, list)
        assert len(seeds) > 0

    def test_get_off_topic_seeds_elements_are_dicts(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """离题种子元素应为 dict (含 text + category)."""
        seeds = bundle.get_off_topic_seeds()
        for s in seeds:
            assert isinstance(s, dict)
            assert "text" in s
            assert "category" in s

    def test_get_off_topic_seeds_returns_copy(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """get_off_topic_seeds 应返回副本."""
        seeds1 = bundle.get_off_topic_seeds()
        original_len = len(seeds1)
        seeds1.append({"text": "外部新增", "category": "test"})
        seeds2 = bundle.get_off_topic_seeds()
        assert len(seeds2) == original_len

    def test_get_short_query_phrases_returns_frozenset(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """get_short_query_phrases 应返回 frozenset."""
        phrases = bundle.get_short_query_phrases()
        assert isinstance(phrases, frozenset)
        assert len(phrases) > 0

    def test_get_short_query_phrases_elements_are_strings(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """短语元素应为 str."""
        phrases = bundle.get_short_query_phrases()
        for p in phrases:
            assert isinstance(p, str)

    def test_get_short_query_phrases_contains_chinese(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """短语集合应含中文短语 (你好)."""
        phrases = bundle.get_short_query_phrases()
        assert "你好" in phrases

    def test_get_short_query_phrases_contains_english(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """短语集合应含英文短语 (hello)."""
        phrases = bundle.get_short_query_phrases()
        assert "hello" in phrases

    def test_get_short_query_phrases_immutable(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """frozenset 应不可变."""
        phrases = bundle.get_short_query_phrases()
        with pytest.raises(AttributeError):
            phrases.add("new phrase")  # type: ignore[attr-defined]


# ========== get_chitchat_config: 全局单例 ==========


class TestGetChitchatConfig:
    """get_chitchat_config 全局单例测试."""

    def test_get_chitchat_config_returns_bundle(self) -> None:
        """get_chitchat_config 应返回 ChitchatConfigBundle 实例."""
        bundle = get_chitchat_config()
        assert isinstance(bundle, ChitchatConfigBundle)

    def test_get_chitchat_config_returns_same_instance(self) -> None:
        """多次调用应返回同一实例 (单例)."""
        bundle1 = get_chitchat_config()
        bundle2 = get_chitchat_config()
        assert bundle1 is bundle2

    def test_get_chitchat_config_initializes_global_bundle(self) -> None:
        """首次调用应初始化全局 _bundle."""
        import src.config.researcher.loader as loader_module

        # 重置全局单例
        loader_module._bundle = None
        assert loader_module._bundle is None

        bundle = get_chitchat_config()
        assert loader_module._bundle is not None
        assert loader_module._bundle is bundle


# ========== 集成验证: 真实配置文件 ==========


class TestRealConfigIntegration:
    """真实配置文件集成验证测试."""

    def test_patterns_yaml_contains_expected_categories(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """patterns.yaml 应含问候/身份/能力/娱乐等类别正则."""
        patterns_text = " ".join(bundle._patterns_raw.get("patterns", []))
        assert "你好" in patterns_text  # 问候类
        assert "你是谁" in patterns_text  # 身份询问类
        assert "你能做什么" in patterns_text  # 能力询问类

    def test_short_query_phrases_yaml_contains_common_greetings(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """short_query_phrases.yaml 应含常见问候短语."""
        phrases = bundle._phrases_raw.get("phrases", [])
        assert "你好" in phrases
        assert "hi" in phrases
        assert "hello" in phrases

    def test_off_topic_replies_yaml_contains_8_categories(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """off_topic.yaml 应含 8 个子分类."""
        off_topic = bundle._off_topic_replies.get("off_topic", {})
        expected_subcategories = {
            "greeting",
            "identity",
            "emotion",
            "entertainment",
            "common_sense",
            "capability_check",
            "topic_switch",
            "evaluation",
        }
        assert expected_subcategories.issubset(off_topic.keys())

    def test_short_query_seeds_count_matches_comment(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """short_query_seeds 数量应 > 100 (注释称 178 条)."""
        seeds = bundle._short_query_seeds_raw.get("seeds", [])
        assert len(seeds) > 100

    def test_off_topic_seeds_count_matches_comment(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """off_topic_seeds 数量应 > 50 (注释称 118 条)."""
        seeds = bundle._off_topic_seeds_raw.get("seeds", [])
        assert len(seeds) > 50

    def test_persona_can_be_overridden_after_init(
        self, bundle: ChitchatConfigBundle
    ) -> None:
        """构造后应可覆盖 persona (外部注入)."""
        custom_persona = PersonaConfig(identity="自定义身份")
        bundle.persona = custom_persona
        assert bundle.persona is custom_persona
        assert bundle.persona.identity == "自定义身份"
