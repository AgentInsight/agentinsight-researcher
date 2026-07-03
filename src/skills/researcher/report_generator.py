"""ReportGenerator 报告生成器.

对标 GPT Researcher skills/writer.py.
AGENTS.md 用户需求 3: Writer (报告合成).

按行业模板合成长报告, 支持 tone 语气控制.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.config.settings import Settings, get_settings
from src.llm.client import LLMClient, LLMTier
from src.observability.tracing import trace_chain

logger = logging.getLogger(__name__)


class ReportGenerator:
    """报告生成器 (Writer 职责).

    对标 GPT Researcher ReportGenerator.
    用 smart_llm 合成长报告.
    """

    settings: Settings
    _llm: LLMClient

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or LLMClient(self.settings)

    async def generate_report(
        self,
        query: str,
        contexts: list[str],
        sources: list[dict[str, Any]],
        *,
        report_type: str = "basic_report",
        tone: str = "objective",
        total_words: int | None = None,
        industry_prompt_family: dict[str, Any] | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> str:
        """生成研究报告 (Markdown).

        对标 GPT Researcher generate_report.
        用户需求 6: 默认 Markdown, 至少 TOTAL_WORDS 字.
        """
        async with trace_chain(
            name="report-generator",
            input={
                "query": query[:100],
                "contexts_count": len(contexts),
                "sources_count": len(sources),
                "report_type": report_type,
            },
            user_id=user_id,
            session_id=session_id,
        ) as span:
            # 上下文为空时拒绝生成 (对标 GPT Researcher 防幻觉)
            if not contexts:
                span.update(output={"error": "no_contexts"})
                return "# 研究报告\n\n无法生成报告: 未检索到相关上下文.\n"

            # 合并上下文 (Token 优化: 截断避免超限)
            combined_context = "\n\n---\n\n".join(contexts)
            max_context_chars = self.settings.max_context_words * 4  # 粗估 4 字符 = 1 词
            if len(combined_context) > max_context_chars:
                combined_context = combined_context[:max_context_chars]

            # 构建来源引用列表 (APA 格式)
            references = self._build_references(sources)

            # 行业专家角色
            industry_name = (
                industry_prompt_family.get("industry_name", "通用研究")
                if industry_prompt_family
                else "通用研究"
            )
            writer_prompt = (
                industry_prompt_family.get("writer_prompt", "") if industry_prompt_family else ""
            )
            key_dimensions = (
                industry_prompt_family.get("key_dimensions", []) if industry_prompt_family else []
            )

            word_limit = total_words or self.settings.total_words
            current_date = datetime.now().strftime("%Y年%m月%d日")

            # 报告类型决定结构
            if report_type == "detailed_report":
                structure_hint = self._detailed_report_structure(key_dimensions)
            else:
                structure_hint = self._basic_report_structure(key_dimensions)

            prompt = f"""你是一位{industry_name}行业的高级研究分析专家. {writer_prompt}

请基于以下检索到的上下文, 撰写一份关于「{query}」的研究报告.

要求:
1. 报告字数不少于 {word_limit} 字
2. 语气: {tone} (objective=客观, analytical=分析性, opinionated=观点鲜明, casual=通俗)
3. 结构化标题: # ## ### 层级
4. Web 源必须超链接引用: ([说明](url))
5. 末尾附参考文献列表 (APA 格式)
6. 注入当前日期: {current_date}
7. 不得编造未在上下文中出现的数据

报告结构:
{structure_hint}

上下文:
{combined_context}

参考文献来源:
{references}

请生成完整的研究报告 (Markdown 格式):"""

            messages = [{"role": "user", "content": prompt}]
            response = await self._llm.achat(
                messages,
                tier=LLMTier.SMART,
                temperature=0.4,
                max_tokens=self.settings.smart_token_limit,
                user_id=user_id,
                session_id=session_id,
                span_name="writer-llm",
            )

            report_md = response.content

            # 确保末尾有参考文献
            if "## 参考文献" not in report_md and "## References" not in report_md:
                report_md += f"\n\n## 参考文献\n\n{references}\n"

            span.update(output={"report_len": len(report_md)})
            return report_md

    @staticmethod
    def _basic_report_structure(key_dimensions: list[str]) -> str:
        """基础报告结构."""
        dims = "\n".join(f"- {d}" for d in key_dimensions) if key_dimensions else "- 核心概念与背景"
        return f"""# {{标题}}

## 摘要
(简述研究主题与核心发现)

## 关键维度
{dims}

## 分析与洞察
(基于上下文的深度分析)

## 结论与展望
(总结与未来趋势)

## 参考文献
(APA 格式引用列表)"""

    @staticmethod
    def _detailed_report_structure(key_dimensions: list[str]) -> str:
        """详细报告结构."""
        dims = (
            "\n".join(f"### {d}\n(详细分析)" for d in key_dimensions)
            if key_dimensions
            else "### 核心概念\n(详细分析)"
        )
        return f"""# {{标题}}

## 摘要
(详述研究背景、目的、方法与核心发现)

## 行业背景
(行业现状、发展历程)

## 深度分析
{dims}

## 竞争格局
(主要参与者、市场份额、竞争优势)

## 趋势与展望
(短期/中期/长期趋势)

## 风险与挑战
(主要风险因素)

## 结论
(核心结论与建议)

## 参考文献
(APA 格式引用列表)"""

    @staticmethod
    def _build_references(sources: list[dict[str, Any]]) -> str:
        """构建 APA 格式参考文献列表."""
        if not sources:
            return "(无可用来源)"

        refs: list[str] = []
        for i, src in enumerate(sources[:20], 1):  # 最多 20 条
            title = src.get("title", "未知标题")
            url = src.get("url", "")
            # APA 格式: [n] 标题. Retrieved from URL
            ref = f"[{i}] {title}."
            if url:
                ref += f" Retrieved from {url}"
            refs.append(ref)
        return "\n".join(refs)
