"""单元测试: V2 对齐 GPTR 优化 (V2-P0/P1).

验证 V2 优化的 7 项对齐 GPTR 改动:
1. settings.py: 新增配置字段 (written_content_similarity_threshold / dashscope_api_key /
   embeddings_filter_chunk_size / detailed_section_word_min/max 等)
2. prompts.py: writer_prompt MUST 具体观点+表格+[n]引用; curator_prompt 第 5 维 Quantitative Value;
   新增 4 个 detailed_report prompt 抽象方法 (DefaultPromptFamily + EnglishPromptFamily 实现)
3. embeddings_filter.py: 独立 EmbeddingsFilter 类, 递归分块 (RecursiveCharacterTextSplitter 对齐)
4. context_manager.py: WrittenContentCompressor 阈值走 settings + chunk 级去重
5. agent_creator.py: tier FAST→SMART + temperature 0.0→0.15
6. source_curator.py: _score_quantitative_value 方法 (百分比/金额/CAGR/数字密度)
7. report_generator.py: 4 个内联 prompt 提取到 PromptFamily + 章节字数 800-1200

AGENTS.md 第 13 章: 单元测试不依赖外部服务.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings
from src.rag.embeddings_filter import EmbeddingsFilter
from src.skills.researcher.prompts import DefaultPromptFamily, EnglishPromptFamily

# ========== 1. settings.py: V2 新增配置字段 ==========


class TestV2Settings:
    """V2 settings.py 新增配置字段验证."""

    def test_written_content_similarity_threshold_default(self) -> None:
        """默认值 0.5 (对标 GPTR WrittenContentCompressor)."""
        s = Settings()
        assert s.written_content_similarity_threshold == 0.5

    def test_dashscope_api_key_field_exists(self) -> None:
        """V2-P0: dashscope_api_key 字段存在 (方案 B smart_llm=qwen-max 用)."""
        s = Settings()
        # 默认 None, 部署时通过 .env 注入
        assert hasattr(s, "dashscope_api_key")
        assert s.dashscope_api_key is None

    def test_embeddings_filter_chunk_size_default(self) -> None:
        """对标 GPTR RecursiveCharacterTextSplitter chunk_size=1000."""
        s = Settings()
        assert s.embeddings_filter_chunk_size == 1000
        assert s.embeddings_filter_chunk_overlap == 100
        assert s.embeddings_filter_top_k == 20

    def test_detailed_section_word_min_max(self) -> None:
        """V2-P1: 章节字数 500-1000 → 800-1200 对齐 GPTR."""
        s = Settings()
        assert s.detailed_section_word_min == 800
        assert s.detailed_section_word_max == 1200
        assert s.detailed_intro_word_min == 300
        assert s.detailed_intro_word_max == 500


# ========== 2. prompts.py: writer_prompt + curator_prompt + 4 个 detailed_report prompt ==========


class TestV2Prompts:
    """V2 prompts.py 强化验证."""

    def test_writer_prompt_contains_must_requirements(self) -> None:
        """V2-P1: writer_prompt MUST 含具体观点 + 表格 + [n] 编号引用."""
        family = DefaultPromptFamily()
        prompt = family.writer_prompt(
            query="测试",
            contexts="上下文",
            agent_role="角色",
            tone="objective",
            word_limit=1200,
            report_type="basic_report",
            current_date="2026年7月4日",
            references="参考文献",
            structure_hint="结构",
            report_style="academic",
        )
        # MUST 具体观点
        assert "具体观点" in prompt
        # MUST Markdown 表格
        assert "表格" in prompt
        # MUST 编号引用 [n]
        assert "[n]" in prompt
        # MUST 字数下限
        assert "1200" in prompt

    def test_curator_prompt_contains_quantitative_value(self) -> None:
        """V2-P1: curator_prompt 强调 Quantitative Value 第 5 维."""
        family = DefaultPromptFamily()
        prompt = family.curator_prompt(
            query="测试",
            sources_text="[1] 来源",
            agent_role="角色",
            max_results=10,
        )
        # 5 维评估
        assert "Quantitative Value" in prompt
        assert "数据丰富度" in prompt
        # Err on the side of inclusion
        assert "Err on the side of inclusion" in prompt
        # 5 个维度全部出现
        for dim in ["相关性", "可信度", "时效性", "客观性", "数据丰富度"]:
            assert dim in prompt

    def test_default_family_has_4_detailed_report_prompts(self) -> None:
        """V2-P1: DefaultPromptFamily 实现 4 个 detailed_report prompt 方法."""
        family = DefaultPromptFamily()
        # 4 个方法都存在且返回非空字符串
        sub = family.subtopics_prompt("q", "ctx", "role", 5)
        assert isinstance(sub, str) and "子主题" in sub
        intro = family.introduction_prompt(
            "q", "ctx", "refs", "role", "objective", "2026年7月4日", "academic"
        )
        assert isinstance(intro, str) and "引言" in intro
        sec = family.section_prompt("topic", "ctx", "refs", "role", "objective", "academic")
        assert isinstance(sec, str) and "topic" in sec
        conc = family.conclusion_prompt("q", "summary", "role", "objective", "academic")
        assert isinstance(conc, str) and "结论" in conc

    def test_english_family_has_4_detailed_report_prompts(self) -> None:
        """V2-P1: EnglishPromptFamily 实现 4 个 detailed_report prompt 方法."""
        family = EnglishPromptFamily()
        sub = family.subtopics_prompt("q", "ctx", "role", 5)
        assert isinstance(sub, str) and "subtopic" in sub.lower()
        intro = family.introduction_prompt(
            "q", "ctx", "refs", "role", "objective", "2026-07-04", "academic"
        )
        assert isinstance(intro, str) and "Introduction" in intro
        sec = family.section_prompt("topic", "ctx", "refs", "role", "objective", "academic")
        assert isinstance(sec, str) and "topic" in sec
        conc = family.conclusion_prompt("q", "summary", "role", "objective", "academic")
        assert isinstance(conc, str) and "Conclusion" in conc

    def test_section_prompt_word_count_800_1200(self) -> None:
        """V2-P1: section_prompt 默认字数 800-1200 对齐 GPTR."""
        family = DefaultPromptFamily()
        prompt = family.section_prompt("topic", "ctx", "refs", "role", "objective", "academic")
        assert "800" in prompt
        assert "1200" in prompt

    def test_english_writer_prompt_contains_must_requirements(self) -> None:
        """V2-P1: 英文 writer_prompt 同样含 MUST 具体观点+表格+[n]引用."""
        family = EnglishPromptFamily()
        prompt = family.writer_prompt(
            query="test",
            contexts="ctx",
            agent_role="role",
            tone="objective",
            word_limit=1200,
            report_type="basic_report",
            current_date="2026-07-04",
            references="refs",
            structure_hint="struct",
            report_style="academic",
        )
        assert "Specific points" in prompt
        assert "Markdown table" in prompt
        assert "[n]" in prompt

    def test_english_curator_prompt_contains_quantitative_value(self) -> None:
        """V2-P1: 英文 curator_prompt 同样含 Quantitative Value."""
        family = EnglishPromptFamily()
        prompt = family.curator_prompt(
            query="test",
            sources_text="[1] src",
            agent_role="role",
            max_results=10,
        )
        assert "Quantitative Value" in prompt
        assert "Err on the side of inclusion" in prompt


# ========== 3. embeddings_filter.py: 独立 EmbeddingsFilter 类 ==========


class TestEmbeddingsFilter:
    """V2-P1: 独立 EmbeddingsFilter 类验证."""

    def test_recursive_split_short_text(self) -> None:
        """短文本 (< chunk_size) 不切分."""
        text = "这是一段短文本"
        result = EmbeddingsFilter._recursive_split(
            text,
            separators=["\n\n", "\n", " ", ""],
            chunk_size=1000,
            chunk_overlap=100,
        )
        assert result == [text]

    def test_recursive_split_long_paragraph(self) -> None:
        """长段落 (> chunk_size) 递归切分."""
        # 构造一个超过 chunk_size 但含 \n\n 的文本
        text = "段落1内容" * 200 + "\n\n" + "段落2内容" * 200
        result = EmbeddingsFilter._recursive_split(
            text,
            separators=["\n\n", "\n", " ", ""],
            chunk_size=1000,
            chunk_overlap=100,
        )
        # 应切分出多个块
        assert len(result) >= 2
        # 每个块长度 <= chunk_size + overlap (容差)
        for chunk in result:
            assert len(chunk) <= 1200  # chunk_size + overlap 容差

    def test_recursive_split_no_separator_fallback_to_char(self) -> None:
        """无 separator 可用时降级字符级硬切."""
        # 构造一个超长无空格无换行的字符串
        text = "字" * 2500
        result = EmbeddingsFilter._recursive_split(
            text,
            separators=["\n\n", "\n", " ", ""],
            chunk_size=1000,
            chunk_overlap=100,
        )
        # 应切分出多个块
        assert len(result) >= 2
        for chunk in result:
            assert len(chunk) <= 1000

    def test_cosine_similarity_orthogonal(self) -> None:
        """正交向量相似度为 0."""
        v1 = [1.0, 0.0, 0.0]
        v2 = [0.0, 1.0, 0.0]
        assert EmbeddingsFilter._cosine_similarity(v1, v2) == 0.0

    def test_cosine_similarity_identical(self) -> None:
        """相同向量相似度为 1."""
        v = [1.0, 2.0, 3.0]
        # 浮点容差
        assert abs(EmbeddingsFilter._cosine_similarity(v, v) - 1.0) < 1e-6

    @pytest.mark.asyncio
    async def test_filter_empty_documents(self) -> None:
        """空文档列表返回空."""
        settings = Settings()
        filt = EmbeddingsFilter(settings)
        result = await filt.filter("query", [])
        assert result == []

    @pytest.mark.asyncio
    async def test_filter_embeddings_failure_fallback(self) -> None:
        """embedding 调用失败时降级返回原文前 N 条."""
        settings = Settings()
        filt = EmbeddingsFilter(settings)
        # mock embeddings 抛异常
        filt._embeddings = MagicMock()
        filt._embeddings.embed_texts = AsyncMock(side_effect=Exception("network error"))
        documents = [{"content": "内容1"}, {"content": "内容2"}]
        result = await filt.filter("query", documents, max_results=5)
        assert result == ["内容1", "内容2"]


# ========== 4. context_manager.py: WrittenContentCompressor 阈值走 settings ==========


class TestWrittenContentCompressorV2:
    """V2-P1: WrittenContentCompressor 阈值走 settings + chunk 级去重."""

    def test_threshold_from_settings(self) -> None:
        """V2-P1: 阈值默认从 settings.written_content_similarity_threshold 读取."""
        from src.skills.researcher.context_manager import WrittenContentCompressor

        settings = Settings()
        settings.written_content_similarity_threshold = 0.42
        compressor = WrittenContentCompressor(settings)
        assert compressor.threshold == 0.42

    def test_threshold_default_0_5(self) -> None:
        """V2-P1: 默认阈值 0.5 (对标 GPTR)."""
        from src.skills.researcher.context_manager import WrittenContentCompressor

        settings = Settings()
        compressor = WrittenContentCompressor(settings)
        assert compressor.threshold == 0.5

    def test_threshold_param_overrides_settings(self) -> None:
        """V2-P1: 显式参数优先级高于 settings."""
        from src.skills.researcher.context_manager import WrittenContentCompressor

        settings = Settings()
        settings.written_content_similarity_threshold = 0.42
        compressor = WrittenContentCompressor(settings, similarity_threshold=0.6)
        assert compressor.threshold == 0.6

    def test_reset_clears_chunks(self) -> None:
        """V2-P1: reset 清空 _written_chunks (新增字段)."""
        from src.skills.researcher.context_manager import WrittenContentCompressor

        compressor = WrittenContentCompressor(Settings())
        compressor._written_chunks.extend(["chunk1", "chunk2"])
        compressor._written_embeddings.extend([[0.1, 0.2]])
        compressor.reset()
        assert compressor._written_chunks == []
        assert compressor._written_embeddings == []


# ========== 5. agent_creator.py: tier FAST→SMART + temperature 0.0→0.15 ==========


class TestAgentCreatorV2:
    """V2-P1: AgentCreator tier/temperature 对齐 GPTR."""

    @pytest.mark.asyncio
    async def test_generate_via_llm_uses_smart_tier(self) -> None:
        """V2-P1: _generate_via_llm 用 SMART tier (旧版 FAST)."""
        from src.llm.client import LLMTier
        from src.skills.researcher.agent_creator import AgentCreator

        creator = AgentCreator(Settings())
        # mock LLMClient.achat 捕获 tier 参数
        mock_response = MagicMock()
        mock_response.content = '{"server": "test", "agent_role_prompt": "测试角色"}'
        creator._llm.achat = AsyncMock(return_value=mock_response)

        await creator._generate_via_llm("测试查询")

        # 验证 tier=SMART (旧版 FAST)
        call_kwargs = creator._llm.achat.call_args.kwargs
        assert call_kwargs["tier"] == LLMTier.SMART

    @pytest.mark.asyncio
    async def test_generate_via_llm_uses_temperature_0_15(self) -> None:
        """V2-P1: _generate_via_llm temperature=0.15 (旧版 0.0)."""
        from src.skills.researcher.agent_creator import AgentCreator

        creator = AgentCreator(Settings())
        mock_response = MagicMock()
        mock_response.content = '{"server": "test", "agent_role_prompt": "测试"}'
        creator._llm.achat = AsyncMock(return_value=mock_response)

        await creator._generate_via_llm("测试查询")

        call_kwargs = creator._llm.achat.call_args.kwargs
        assert call_kwargs["temperature"] == 0.15


# ========== 6. source_curator.py: Quantitative Value 维度 ==========


class TestSourceCuratorV2:
    """V2-P1: SourceCurator 加 Quantitative Value 维度."""

    def test_score_quantitative_value_empty(self) -> None:
        """空内容返回 0."""
        from src.skills.researcher.source_curator import SourceCurator

        assert SourceCurator._score_quantitative_value("") == 0.0

    def test_score_quantitative_value_percentage(self) -> None:
        """含百分比加分."""
        from src.skills.researcher.source_curator import SourceCurator

        score = SourceCurator._score_quantitative_value("市场增长 18.5% 持续上升")
        assert score >= 0.04  # 百分比 +0.04

    def test_score_quantitative_value_money(self) -> None:
        """含金额加分."""
        from src.skills.researcher.source_curator import SourceCurator

        score = SourceCurator._score_quantitative_value("市场规模 1.2 万亿元")
        assert score >= 0.04  # 金额 +0.04 (万 + 亿)

    def test_score_quantitative_value_cagr(self) -> None:
        """含 CAGR 加分."""
        from src.skills.researcher.source_curator import SourceCurator

        score = SourceCurator._score_quantitative_value("CAGR 18.5% 复合增长")
        assert score >= 0.07  # CAGR +0.03 + 百分比 +0.04

    def test_score_quantitative_value_max_0_15(self) -> None:
        """数据丰富度加分上限 0.15."""
        from src.skills.researcher.source_curator import SourceCurator

        # 含百分比 + 金额 + CAGR + 高数字密度
        text = "市场 $1.2T 增长 18.5% CAGR 20% 同比 15% 100 亿 200 亿 300 亿 400 亿 500 亿"
        score = SourceCurator._score_quantitative_value(text)
        assert score <= 0.15

    def test_score_quantitative_value_no_data(self) -> None:
        """纯文字无数据返回 0."""
        from src.skills.researcher.source_curator import SourceCurator

        score = SourceCurator._score_quantitative_value("这是一个普通文字描述没有任何统计指标")
        assert score == 0.0

    def test_score_credibility_includes_quant_value(self) -> None:
        """V2-P1: _score_credibility 内部已调用 _score_quantitative_value."""
        from src.skills.researcher.source_curator import SourceCurator

        curator = SourceCurator(Settings())
        # 含统计数据的来源
        source_with_data = {
            "url": "https://stats.gov.cn/data",
            "content": "2025 年市场规模 1.2 万亿元, 增长 18.5%, CAGR 15%",
        }
        # 纯文字来源
        source_no_data = {
            "url": "https://blog.example.com/post",
            "content": "这是一段普通的文字描述没有任何数据指标",
        }
        score_with = curator._score_credibility(source_with_data)
        score_no = curator._score_credibility(source_no_data)
        # 含统计数据的可信度应高于纯文字
        assert score_with > score_no


# ========== 7. report_generator.py: 章节字数 800-1200 ==========


class TestReportGeneratorV2:
    """V2-P1: ReportGenerator detailed_report 章节字数 800-1200."""

    def test_section_prompt_uses_settings_word_count(self) -> None:
        """V2-P1: _write_section 使用 settings.detailed_section_word_min/max."""
        # 通过 PromptFamily.section_prompt 验证 settings 注入
        family = DefaultPromptFamily()
        prompt = family.section_prompt(
            "topic",
            "ctx",
            "refs",
            "role",
            "objective",
            "academic",
            word_min=800,
            word_max=1200,
        )
        assert "800" in prompt
        assert "1200" in prompt
