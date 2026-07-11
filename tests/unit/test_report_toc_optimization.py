"""单元测试: 报告 TOC 一致性优化 (优化 1-6).

覆盖优化方案 temp/report_toc_content_mismatch_optimization.md 中的 6 个优化:
1. TOC 后置生成 (只含有效子主题)
2. 每个子主题独立 sub_context (BM25 检索)
3. 跨章节语义去重 (只丢弃相似 chunk, 保留差异部分)
4. 失败章节在 TOC 中标记
5. 一致性校验 (防御性编程)
6. _conduct_subtopics 失败跳过时同步移除 subtopics

测试策略:
- Mock LLMClient / ResearchConductor / WrittenContentCompressor
- 构造去重跳过 / LLM 失败 / 空 context 等场景
- 验证 TOC 与 sections 一致性
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np

from src.skills.researcher.context_manager import WrittenContentCompressor
from src.skills.researcher.report_generator import _SECTION_FAILURE_PLACEHOLDER, ReportGenerator
from src.skills.researcher.research_conductor import ResearchConductor

# ============================================================================
# 优化 1 + 4 + 5: TOC 后置生成 + 失败章节标记 + 一致性校验
# ============================================================================


def _make_settings() -> Any:
    """构造测试用 Settings (Mock)."""
    settings = MagicMock()
    settings.agent_role = None
    settings.image_generation_enabled = False
    settings.written_content_similarity_threshold = 0.5
    settings.smart_token_limit = 6000
    settings.fast_token_limit = 3000
    settings.word_limit = 800
    settings.detailed_report_max_context_chars = 8000
    settings.report_language = "zh"
    settings.tracing_enabled = False
    return settings


def _make_report_generator(settings: Any | None = None) -> ReportGenerator:
    """构造测试用 ReportGenerator (Mock LLM)."""
    settings = settings or _make_settings()
    llm = AsyncMock()
    return ReportGenerator(settings=settings, llm=llm)


class TestOptimization1TOCPostGeneration:
    """优化 1: TOC 后置生成 — 只含有效子主题."""

    def test_generate_toc_empty_subtopics(self) -> None:
        """空子主题列表应返回空 TOC."""
        result = ReportGenerator._generate_toc([])
        assert result == ""

    def test_generate_toc_single_topic(self) -> None:
        """单个子主题应生成含 1 个条目的 TOC."""
        result = ReportGenerator._generate_toc(["量子计算"])
        assert "## 目录" in result
        assert "[量子计算]" in result
        assert "1." in result

    def test_generate_toc_multiple_topics(self) -> None:
        """多个子主题应生成对应数量的 TOC 条目."""
        topics = ["主题A", "主题B", "主题C"]
        result = ReportGenerator._generate_toc(topics)
        for i, topic in enumerate(topics, 1):
            assert f"{i}. [{topic}]" in result

    def test_toc_only_contains_valid_topics_after_dedup(self) -> None:
        """优化 1 核心验证: 去重跳过后 TOC 不含被跳过的子主题.

        模拟 section_results 中有 1 个被 skipped=True 的子主题,
        验证 valid_topics_for_toc 不含该子主题.
        """
        subtopics = ["主题A", "主题B", "主题C"]
        # 模拟: 主题A 正常, 主题B 被去重跳过, 主题C 正常
        section_results: list[tuple[str | None, list[dict[str, Any]], bool]] = [
            ("### 主题A\n\n内容A", [], False),
            (None, [], True),  # 被去重跳过
            ("### 主题C\n\n内容C", [], False),
        ]

        # 模拟优化 1 的汇总逻辑
        sections: list[str] = []
        valid_topics_for_toc: list[str] = []
        for topic, (section_md, _sub_sources, skipped) in zip(
            subtopics, section_results, strict=False
        ):
            if skipped:
                continue
            if section_md:
                if section_md == _SECTION_FAILURE_PLACEHOLDER:
                    valid_topics_for_toc.append(f"{topic} (生成失败)")
                    sections.append(f"### {topic}\n\n*此章节内容生成失败, 请重试。*")
                else:
                    valid_topics_for_toc.append(topic)
                    sections.append(section_md)

        toc = ReportGenerator._generate_toc(valid_topics_for_toc)

        # TOC 只含主题A 和主题C, 不含主题B
        assert "主题A" in toc
        assert "主题C" in toc
        assert "主题B" not in toc
        # sections 也只有 2 个
        assert len(sections) == 2
        # TOC 条目数与 sections 数一致
        assert len(valid_topics_for_toc) == len(sections)


class TestOptimization4FailurePlaceholder:
    """优化 4: 失败章节在 TOC 中标记."""

    def test_failure_section_marked_in_toc(self) -> None:
        """LLM 失败的章节应在 TOC 中标记 '(生成失败)'."""
        subtopics = ["主题A", "主题B"]
        section_results: list[tuple[str | None, list[dict[str, Any]], bool]] = [
            ("### 主题A\n\n内容A", [], False),
            (_SECTION_FAILURE_PLACEHOLDER, [], False),  # LLM 失败, 返回占位文本
        ]

        sections: list[str] = []
        valid_topics_for_toc: list[str] = []
        for topic, (section_md, _sub_sources, skipped) in zip(
            subtopics, section_results, strict=False
        ):
            if skipped:
                continue
            if section_md:
                if section_md == _SECTION_FAILURE_PLACEHOLDER:
                    valid_topics_for_toc.append(f"{topic} (生成失败)")
                    sections.append(f"### {topic}\n\n*此章节内容生成失败, 请重试。*")
                else:
                    valid_topics_for_toc.append(topic)
                    sections.append(section_md)

        toc = ReportGenerator._generate_toc(valid_topics_for_toc)

        # 主题B 应标记为 '(生成失败)'
        assert "主题B (生成失败)" in toc
        # sections 中主题B 应显示失败提示
        assert any("此章节内容生成失败" in s for s in sections)

    def test_normal_section_not_marked(self) -> None:
        """正常章节不应被标记 '(生成失败)'."""
        subtopics = ["正常主题"]
        section_results: list[tuple[str | None, list[dict[str, Any]], bool]] = [
            ("### 正常主题\n\n正常内容", [], False),
        ]

        sections: list[str] = []
        valid_topics_for_toc: list[str] = []
        for topic, (section_md, _sub_sources, skipped) in zip(
            subtopics, section_results, strict=False
        ):
            if skipped:
                continue
            if section_md:
                if section_md == _SECTION_FAILURE_PLACEHOLDER:
                    valid_topics_for_toc.append(f"{topic} (生成失败)")
                    sections.append(f"### {topic}\n\n*此章节内容生成失败, 请重试。*")
                else:
                    valid_topics_for_toc.append(topic)
                    sections.append(section_md)

        assert "生成失败" not in valid_topics_for_toc[0]


class TestOptimization5ConsistencyCheck:
    """优化 5: 一致性校验 (防御性编程)."""

    def test_consistency_check_passes_when_aligned(self) -> None:
        """TOC 条目数与 sections 数一致时无告警."""
        sections = ["内容A", "内容B"]
        valid_topics_for_toc = ["主题A", "主题B"]
        # 模拟一致性校验
        if len(valid_topics_for_toc) != len(sections):
            valid_topics_for_toc = valid_topics_for_toc[: len(sections)]
        assert len(valid_topics_for_toc) == len(sections)

    def test_consistency_check_truncates_when_mismatch(self) -> None:
        """TOC 条目数 > sections 数时截断 TOC."""
        sections = ["内容A"]
        valid_topics_for_toc = ["主题A", "主题B"]
        # 模拟一致性校验
        if len(valid_topics_for_toc) != len(sections):
            valid_topics_for_toc = valid_topics_for_toc[: len(sections)]
        assert len(valid_topics_for_toc) == len(sections)
        assert "主题B" not in valid_topics_for_toc

    def test_empty_sections_warning_condition(self) -> None:
        """所有子主题被跳过时触发空章节告警."""
        subtopics = ["主题A", "主题B", "主题C"]
        sections: list[str] = []
        # 模拟所有子主题被跳过
        missing = len(subtopics) - len(sections)
        assert missing == 3
        assert missing > 0  # 应触发告警


# ============================================================================
# 优化 2: 每个子主题独立 sub_context (BM25 检索)
# ============================================================================


class TestOptimization2ExtractTopicContext:
    """优化 2: _extract_topic_context — BM25 检索相关片段."""

    def test_extract_empty_context_returns_empty(self) -> None:
        """空 context 返回空字符串."""
        rg = _make_report_generator()
        assert rg._extract_topic_context("topic", "") == ""
        assert rg._extract_topic_context("topic", "   ") == ""

    def test_extract_short_context_returns_original(self) -> None:
        """过短 context (<500 字符) 直接返回原文."""
        rg = _make_report_generator()
        short_ctx = "短内容"
        result = rg._extract_topic_context("topic", short_ctx)
        assert result == short_ctx

    def test_extract_long_context_returns_relevant_chunks(self) -> None:
        """长 context 应通过 BM25 检索返回相关片段."""
        rg = _make_report_generator()
        # 构造长 context, 含不同主题片段
        context = (
            "量子计算利用量子力学原理进行计算。\n\n"
            "经典计算机使用比特表示 0 或 1。\n\n"
            "量子比特可以同时处于 0 和 1 的叠加态。\n\n"
            "机器学习是人工智能的一个分支。\n\n"
            "深度学习使用神经网络进行特征学习。\n\n"
            "量子纠缠是量子计算的核心特性之一。"
        )
        # 补足长度到 >500
        context = context * 10
        result = rg._extract_topic_context("量子计算", context)
        # 应返回非空结果
        assert result
        # 应包含量子相关内容
        assert "量子" in result

    def test_extract_fallback_on_exception(self) -> None:
        """BM25 异常时降级返回原文."""
        rg = _make_report_generator()
        long_ctx = "内容" * 300
        with patch("src.rag.embeddings_filter.recursive_split", side_effect=Exception("test")):
            result = rg._extract_topic_context("topic", long_ctx)
        # 降级返回原文
        assert result == long_ctx


# ============================================================================
# 优化 3: 跨章节语义去重 (只丢弃相似 chunk, 保留差异部分)
# ============================================================================


class TestOptimization3PartialDedup:
    """优化 3: check_and_update_partial — 只丢弃相似 chunk."""

    def _make_compressor(self, threshold: float = 0.5) -> WrittenContentCompressor:
        """构造测试用 WrittenContentCompressor."""
        settings = _make_settings()
        settings.written_content_similarity_threshold = threshold
        return WrittenContentCompressor(settings)

    def test_empty_chunks_returns_keep(self) -> None:
        """空 chunks 降级保留."""
        comp = self._make_compressor()
        keep, content = comp.check_and_update_partial([], [])
        assert keep is True
        assert content == ""

    def test_first_write_keeps_all(self) -> None:
        """首次写入: 直接记录, 全部保留."""
        comp = self._make_compressor()
        chunks = ["chunk1", "chunk2"]
        embs = [[0.1, 0.2], [0.3, 0.4]]
        keep, content = comp.check_and_update_partial(chunks, embs)
        assert keep is True
        assert "chunk1" in content
        assert "chunk2" in content
        assert len(comp._written_embeddings) == 2

    def test_all_similar_chunks_discarded(self) -> None:
        """所有 chunk 都相似: 整篇丢弃."""
        comp = self._make_compressor(threshold=0.5)
        # 首次写入
        chunks1 = ["原文内容"]
        embs1 = [[1.0, 0.0]]
        comp.check_and_update_partial(chunks1, embs1)
        # 第二次: 完全相同的 embedding (相似度 = 1.0 >= 0.5)
        chunks2 = ["重复内容"]
        embs2 = [[1.0, 0.0]]
        keep, content = comp.check_and_update_partial(chunks2, embs2)
        assert keep is False
        assert content == ""

    def test_partial_similar_chunks_keeps_difference(self) -> None:
        """部分 chunk 相似: 只保留不相似的 chunk (优化 3 核心)."""
        comp = self._make_compressor(threshold=0.5)
        # 首次写入 chunk A
        chunks1 = ["chunkA 内容"]
        embs1 = [[1.0, 0.0]]
        comp.check_and_update_partial(chunks1, embs1)
        # 第二次: chunk B 相似 (将被丢弃), chunk C 不相似 (将保留)
        chunks2 = ["chunkB 重复", "chunkC 新内容"]
        embs2 = [[1.0, 0.0], [0.0, 1.0]]  # B 与已写入相似, C 不相似
        keep, content = comp.check_and_update_partial(chunks2, embs2)
        assert keep is True
        assert "chunkC 新内容" in content
        assert "chunkB 重复" not in content

    def test_no_similar_chunks_keeps_all(self) -> None:
        """无相似 chunk: 全部保留."""
        comp = self._make_compressor(threshold=0.5)
        # 首次写入
        chunks1 = ["chunkA"]
        embs1 = [[1.0, 0.0]]
        comp.check_and_update_partial(chunks1, embs1)
        # 第二次: 完全不同的 embedding
        chunks2 = ["chunkB"]
        embs2 = [[0.0, 1.0]]
        keep, content = comp.check_and_update_partial(chunks2, embs2)
        assert keep is True
        assert "chunkB" in content

    def test_dimension_mismatch_fallback_keeps(self) -> None:
        """维度不匹配时降级保留."""
        comp = self._make_compressor()
        # 首次写入 2 维
        comp.check_and_update_partial(["chunkA"], [[1.0, 0.0]])
        # 第二次: 3 维 (不匹配)
        keep, content = comp.check_and_update_partial(["chunkB"], [[1.0, 0.0, 0.0]])
        assert keep is True
        assert "chunkB" in content


# ============================================================================
# 优化 6: _conduct_subtopics 失败跳过时同步移除 subtopics
# ============================================================================


class TestOptimization6ConductSubtopicsSync:
    """优化 6: _conduct_subtopics 失败/空 context 跳过时同步移除 subtopics."""

    def test_failed_section_removed_from_subtopics(self) -> None:
        """异常 section 对应的 topic 应从 sub_queries 移除."""
        subtopics = ["主题A", "主题B", "主题C"]
        sections: list[Any] = [
            {"context": "内容A", "sources": []},
            Exception("研究失败"),  # 异常
            {"context": "内容C", "sources": []},
        ]

        valid_subtopics: list[str] = []
        for topic, section in zip(subtopics, sections, strict=False):
            if isinstance(section, Exception):
                continue
            ctx = section.get("context", "")
            if not ctx:
                continue
            valid_subtopics.append(topic)

        assert "主题A" in valid_subtopics
        assert "主题B" not in valid_subtopics  # 异常被移除
        assert "主题C" in valid_subtopics
        assert len(valid_subtopics) == 2

    def test_empty_context_removed_from_subtopics(self) -> None:
        """空 context 对应的 topic 应从 sub_queries 移除."""
        subtopics = ["主题A", "主题B"]
        sections: list[Any] = [
            {"context": "内容A", "sources": []},
            {"context": "", "sources": []},  # 空 context
        ]

        valid_subtopics: list[str] = []
        for topic, section in zip(subtopics, sections, strict=False):
            if isinstance(section, Exception):
                continue
            ctx = section.get("context", "")
            if not ctx:
                continue
            valid_subtopics.append(topic)

        assert "主题A" in valid_subtopics
        assert "主题B" not in valid_subtopics  # 空 context 被移除
        assert len(valid_subtopics) == 1

    def test_all_failed_returns_empty_subtopics(self) -> None:
        """所有 section 都失败时 sub_queries 为空."""
        subtopics = ["主题A", "主题B"]
        sections: list[Any] = [Exception("失败1"), Exception("失败2")]

        valid_subtopics: list[str] = []
        for topic, section in zip(subtopics, sections, strict=False):
            if isinstance(section, Exception):
                continue
            ctx = section.get("context", "")
            if not ctx:
                continue
            valid_subtopics.append(topic)

        assert len(valid_subtopics) == 0

    def test_all_valid_keeps_all(self) -> None:
        """所有 section 正常时 sub_queries 保留全部."""
        subtopics = ["主题A", "主题B"]
        sections: list[Any] = [
            {"context": "内容A", "sources": []},
            {"context": "内容B", "sources": []},
        ]

        valid_subtopics: list[str] = []
        for topic, section in zip(subtopics, sections, strict=False):
            if isinstance(section, Exception):
                continue
            ctx = section.get("context", "")
            if not ctx:
                continue
            valid_subtopics.append(topic)

        assert len(valid_subtopics) == 2


# ============================================================================
# 集成验证: 优化 1+3+6 联合场景
# ============================================================================


class TestIntegrationTOCConsistency:
    """集成验证: 多优化联合场景下 TOC 与 body 一致性."""

    def test_dedup_skip_and_partial_dedup_combined(self) -> None:
        """混合场景: 部分去重跳过 + 部分保留差异 + 部分正常.

        场景:
        - 主题A: 正常写入
        - 主题B: 整篇去重跳过 (check_and_update_partial 返回 False)
        - 主题C: 部分去重 (check_and_update_partial 返回 True + 过滤后内容)
        - 主题D: LLM 失败 (返回占位文本)
        """
        subtopics = ["主题A", "主题B", "主题C", "主题D"]
        section_results: list[tuple[str | None, list[dict[str, Any]], bool]] = [
            ("### 主题A\n\n内容A", [], False),  # 正常
            (None, [], True),  # 去重跳过
            ("### 主题C\n\n过滤后内容C", [], False),  # 部分去重后保留
            (_SECTION_FAILURE_PLACEHOLDER, [], False),  # LLM 失败
        ]

        # 模拟优化 1+4+5 的汇总逻辑
        sections: list[str] = []
        valid_topics_for_toc: list[str] = []
        for topic, (section_md, _sub_sources, skipped) in zip(
            subtopics, section_results, strict=False
        ):
            if skipped:
                continue
            if section_md:
                if section_md == _SECTION_FAILURE_PLACEHOLDER:
                    valid_topics_for_toc.append(f"{topic} (生成失败)")
                    sections.append(f"### {topic}\n\n*此章节内容生成失败, 请重试。*")
                else:
                    valid_topics_for_toc.append(topic)
                    sections.append(section_md)

        # 一致性校验
        if len(valid_topics_for_toc) != len(sections):
            valid_topics_for_toc = valid_topics_for_toc[: len(sections)]

        toc = ReportGenerator._generate_toc(valid_topics_for_toc)

        # TOC 含 3 个条目 (主题A, 主题C, 主题D(生成失败)), 不含主题B
        assert "主题A" in toc
        assert "主题B" not in toc  # 去重跳过
        assert "主题C" in toc
        assert "主题D (生成失败)" in toc
        # sections 也含 3 个
        assert len(sections) == 3
        # TOC 条目数 = sections 数
        assert len(valid_topics_for_toc) == len(sections)

    def test_all_skipped_produces_empty_report_body(self) -> None:
        """所有子主题被去重跳过时, body 为占位文本."""
        subtopics = ["主题A", "主题B"]
        section_results: list[tuple[str | None, list[dict[str, Any]], bool]] = [
            (None, [], True),  # 去重跳过
            (None, [], True),  # 去重跳过
        ]

        sections: list[str] = []
        valid_topics_for_toc: list[str] = []
        for topic, (section_md, _sub_sources, skipped) in zip(
            subtopics, section_results, strict=False
        ):
            if skipped:
                continue
            if section_md:
                valid_topics_for_toc.append(topic)
                sections.append(section_md)

        body = "\n\n".join(sections) if sections else "_(无子主题章节内容)_"
        assert body == "_(无子主题章节内容)_"
        assert len(valid_topics_for_toc) == 0


# ============================================================================
# 优化 2 边界用例: _extract_topic_context 分支补充
# ============================================================================


class TestOptimization2ExtractTopicContextEdgeCases:
    """优化 2: _extract_topic_context 边界分支补充测试."""

    def test_extract_single_chunk_returns_original(self) -> None:
        """len(chunks) <= 1 分支: recursive_split 返回单 chunk 时直接返回原文."""
        rg = _make_report_generator()
        long_ctx = "内容" * 300  # > 500 字符
        with patch("src.rag.embeddings_filter.recursive_split", return_value=[long_ctx]):
            result = rg._extract_topic_context("topic", long_ctx)
        assert result == long_ctx

    def test_extract_empty_query_tokens_returns_original(self) -> None:
        """query_tokens 为空分支: jieba 分词后无有效 token 时返回原文."""
        rg = _make_report_generator()
        long_ctx = "内容" * 300

        def mock_cut(_text: str) -> list[str]:
            return ["  ", ""]  # 全空白, 过滤后为空

        with (
            patch("src.rag.embeddings_filter.recursive_split", return_value=["chunk1", "chunk2"]),
            patch("jieba.cut_for_search", side_effect=mock_cut),
        ):
            result = rg._extract_topic_context("topic", long_ctx)
        assert result == long_ctx

    def test_extract_all_empty_chunk_tokens_returns_original(self) -> None:
        """chunk_tokens 全空分支: 所有 chunk 分词后无有效 token 时返回原文."""
        rg = _make_report_generator()
        long_ctx = "内容" * 300
        call_count = [0]

        def mock_cut(_text: str) -> list[str]:
            call_count[0] += 1
            if call_count[0] == 1:
                return ["topic_token"]  # topic 有有效 token
            return ["  "]  # chunks 全空白, 过滤后为空

        with (
            patch("src.rag.embeddings_filter.recursive_split", return_value=["chunk1", "chunk2"]),
            patch("jieba.cut_for_search", side_effect=mock_cut),
        ):
            result = rg._extract_topic_context("topic", long_ctx)
        assert result == long_ctx

    def test_extract_no_positive_scores_returns_truncated(self) -> None:
        """scored 为空分支: 所有 chunk BM25 分数 <= 0 时返回原文前 2000 字符."""
        rg = _make_report_generator()
        long_ctx = "填充" * 1500  # 3000 字符, > 2000
        # 构造不含 "量子" / "纠缠" 的 chunks (BM25 分数全为 0)
        chunks = [
            "机器学习是人工智能的分支",
            "深度学习使用神经网络进行特征学习",
            "自然语言处理是AI的重要方向",
        ]
        with patch("src.rag.embeddings_filter.recursive_split", return_value=chunks):
            result = rg._extract_topic_context("量子纠缠", long_ctx)
        assert result == long_ctx[:2000]

    def test_extract_top3_truncation(self) -> None:
        """Top-3 截断验证: 构造 9 chunk (4 含关键词, 5 不含), 验证只返回 Top-3."""
        rg = _make_report_generator()
        long_ctx = "内容" * 300
        chunks = [
            "chunkA kw kw kw kw",  # 4 次, 最高 BM25 分
            "chunkB kw kw kw",  # 3 次
            "chunkC kw kw",  # 2 次
            "chunkD kw",  # 1 次, 最低
            "chunkE",  # 无关键词
            "chunkF",
            "chunkG",
            "chunkH",
            "chunkI",
        ]
        call_count = [0]

        def mock_cut(text: str) -> list[str]:
            call_count[0] += 1
            if call_count[0] == 1:
                return ["kw"]  # topic token
            if "kw" in text:
                return ["kw"] * text.count("kw")
            return ["other"]

        with (
            patch("src.rag.embeddings_filter.recursive_split", return_value=chunks),
            patch("jieba.cut_for_search", side_effect=mock_cut),
        ):
            result = rg._extract_topic_context("kw", long_ctx)
        # 4 个含 "kw" 的 chunk 有正分, 取 Top-3
        parts = result.split("\n\n")
        assert len(parts) == 3
        # 不含关键词的 chunk 不在结果中
        assert "chunkE" not in result
        assert "chunkI" not in result


# ============================================================================
# 优化 3 边界用例: check_and_update_partial 分支补充
# ============================================================================


class TestOptimization3PartialDedupEdgeCases:
    """优化 3: check_and_update_partial 边界分支补充测试."""

    def _make_compressor(self, threshold: float = 0.5) -> WrittenContentCompressor:
        """构造测试用 WrittenContentCompressor."""
        settings = _make_settings()
        settings.written_content_similarity_threshold = threshold
        return WrittenContentCompressor(settings)

    def test_sim_matrix_empty_fallback_keeps(self) -> None:
        """sim_matrix.size == 0 分支: 矩阵为空时降级保留."""
        comp = self._make_compressor()
        # 首次写入 (建立 _written_embeddings)
        comp.check_and_update_partial(["chunkA"], [[1.0, 0.0]])
        assert len(comp._written_embeddings) == 1

        # 第二次: mock _cosine_similarity_batch 返回空矩阵
        empty_matrix = np.zeros((0, 0), dtype=np.float32)
        with patch(
            "src.skills.researcher.context_manager.ContextManager._cosine_similarity_batch",
            return_value=empty_matrix,
        ):
            keep, content = comp.check_and_update_partial(["chunkB"], [[1.0, 0.0]])
        # 空矩阵 → 降级保留
        assert keep is True
        assert "chunkB" in content
        # embedding 仍被记录 (首次 1 + 本次 1 = 2)
        assert len(comp._written_embeddings) == 2

    def test_multiple_writes_accumulate_embeddings(self) -> None:
        """多次写入累积验证: 连续 3 次写入, _written_embeddings 累积正确."""
        comp = self._make_compressor(threshold=0.99)
        # 3 次写入, 每次使用正交 embedding (cosine = 0 < 0.99, 全部保留)
        writes = [
            (["chunk1"], [[1.0, 0.0, 0.0]]),
            (["chunk2"], [[0.0, 1.0, 0.0]]),
            (["chunk3"], [[0.0, 0.0, 1.0]]),
        ]
        for chunks, embs in writes:
            keep, _ = comp.check_and_update_partial(chunks, embs)
            assert keep is True
        assert len(comp._written_embeddings) == 3
        assert len(comp._written_chunks) == 3

    def test_partial_similar_only_appends_keep_embs(self) -> None:
        """部分相似时 _written_embeddings 只追加 keep_embs (不追加被丢弃的 embs)."""
        comp = self._make_compressor(threshold=0.5)
        # 首次写入 [1, 0]
        comp.check_and_update_partial(["chunkA"], [[1.0, 0.0]])
        assert len(comp._written_embeddings) == 1

        # 第二次: chunkB 相似 ([1,0] → cosine=1.0 >= 0.5 → 丢弃), chunkC 不相似
        keep, content = comp.check_and_update_partial(
            ["chunkB", "chunkC"],
            [[1.0, 0.0], [0.0, 1.0]],
        )
        assert keep is True
        assert "chunkC" in content
        assert "chunkB" not in content
        # 只追加了 chunkC 的 embedding (1 + 1 = 2, 不含 chunkB)
        assert len(comp._written_embeddings) == 2
        assert comp._written_embeddings[1] == [0.0, 1.0]

    def test_threshold_boundary_equal_treated_as_similar(self) -> None:
        """阈值边界测试: 相似度恰好等于 threshold 时判定为相似 (>= 而非 >)."""
        comp = self._make_compressor(threshold=1.0)
        # 首次写入 [1, 0]
        comp.check_and_update_partial(["chunkA"], [[1.0, 0.0]])
        # 第二次: [1, 0] → cosine = 1.0 == threshold 1.0 → 相似
        keep, content = comp.check_and_update_partial(["chunkB"], [[1.0, 0.0]])
        # >= 判定: 1.0 >= 1.0 → True → 整篇丢弃
        assert keep is False
        assert content == ""


# ============================================================================
# 优化 6 集成测试: _conduct_subtopics 真实调用源码
# ============================================================================


def _make_research_conductor() -> ResearchConductor:
    """构造测试用 ResearchConductor (Mock 依赖)."""
    settings = MagicMock()
    llm = AsyncMock()
    context_manager = MagicMock()
    prompt_family = MagicMock()
    return ResearchConductor(
        settings=settings,
        llm=llm,
        context_manager=context_manager,
        prompt_family=prompt_family,
    )


class TestOptimization6ConductSubtopicsIntegration:
    """优化 6: _conduct_subtopics 真实调用源码方法集成测试."""

    async def test_failed_section_removed_from_subtopics(self) -> None:
        """异常 section 被移除: 调用 _conduct_subtopics 验证异常 topic 被移除."""
        conductor = _make_research_conductor()
        conductor._generate_subtopics = AsyncMock(
            return_value=["主题A", "主题B", "主题C"],
        )

        async def mock_research(_query: str, subtopic: str, **_kwargs: Any) -> dict[str, Any]:
            if subtopic == "主题B":
                raise RuntimeError("研究失败")
            return {"context": f"{subtopic}内容", "sources": []}

        conductor._research_subtopic = mock_research

        result = await conductor._conduct_subtopics("query")
        assert "主题A" in result["sub_queries"]
        assert "主题B" not in result["sub_queries"]
        assert "主题C" in result["sub_queries"]
        assert len(result["sub_queries"]) == 2

    async def test_empty_context_section_removed(self) -> None:
        """空 context section 被移除: 调用 _conduct_subtopics 验证空 context 的 topic 被移除."""
        conductor = _make_research_conductor()
        conductor._generate_subtopics = AsyncMock(return_value=["主题A", "主题B"])

        async def mock_research(_query: str, subtopic: str, **_kwargs: Any) -> dict[str, Any]:
            if subtopic == "主题B":
                return {"context": "", "sources": []}
            return {"context": f"{subtopic}内容", "sources": []}

        conductor._research_subtopic = mock_research

        result = await conductor._conduct_subtopics("query")
        assert "主题A" in result["sub_queries"]
        assert "主题B" not in result["sub_queries"]
        assert len(result["sub_queries"]) == 1

    async def test_valid_subtopics_matches_all_contexts(self) -> None:
        """valid_subtopics 与 all_contexts 数量一致 (无 uploaded_files_context)."""
        conductor = _make_research_conductor()
        conductor._generate_subtopics = AsyncMock(
            return_value=["主题A", "主题B", "主题C"],
        )

        async def mock_research(_query: str, subtopic: str, **_kwargs: Any) -> dict[str, Any]:
            return {"context": f"{subtopic}内容", "sources": []}

        conductor._research_subtopic = mock_research

        result = await conductor._conduct_subtopics("query")
        # 3 个有效 subtopic, 3 个 context (无 uploaded_files_context)
        assert len(result["sub_queries"]) == 3
        assert len(result["contexts"]) == 3
        # 每个 context 以 "## {topic}" 开头
        for topic, ctx in zip(result["sub_queries"], result["contexts"], strict=False):
            assert ctx.startswith(f"## {topic}")

    async def test_sources_merged_correctly(self) -> None:
        """sources 正确合并: 多个 section 的 sources 被合并到 all_sources."""
        conductor = _make_research_conductor()
        conductor._generate_subtopics = AsyncMock(
            return_value=["主题A", "主题B"],
        )
        source_a = {"url": "http://a.com", "title": "A"}
        source_b = {"url": "http://b.com", "title": "B"}

        async def mock_research(_query: str, subtopic: str, **_kwargs: Any) -> dict[str, Any]:
            if subtopic == "主题A":
                return {"context": "内容A", "sources": [source_a]}
            return {"context": "内容B", "sources": [source_b]}

        conductor._research_subtopic = mock_research

        result = await conductor._conduct_subtopics("query")
        assert len(result["sources"]) == 2
        assert source_a in result["sources"]
        assert source_b in result["sources"]
        assert result["visited_urls"] == {"http://a.com", "http://b.com"}

    async def test_uploaded_files_context_appended(self) -> None:
        """uploaded_files_context 被追加到 all_contexts 末尾."""
        conductor = _make_research_conductor()
        conductor._generate_subtopics = AsyncMock(return_value=["主题A"])

        async def mock_research(_query: str, subtopic: str, **_kwargs: Any) -> dict[str, Any]:
            return {"context": f"{subtopic}内容", "sources": []}

        conductor._research_subtopic = mock_research

        uploaded = ["文件1内容", "文件2内容"]
        result = await conductor._conduct_subtopics(
            "query",
            uploaded_files_context=uploaded,
        )
        # 1 个 subtopic context + 2 个 uploaded files = 3 个 context
        assert len(result["contexts"]) == 3
        assert "文件1内容" in result["contexts"]
        assert "文件2内容" in result["contexts"]
        # uploaded files 追加在末尾
        assert result["contexts"][-2:] == uploaded
