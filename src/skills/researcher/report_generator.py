"""ReportGenerator 报告生成器.

对标 GPT Researcher skills/writer.py.
AGENTS.md 用户需求 3: Writer (报告合成).

按动态角色 persona 合成长报告, 支持 tone 语气控制.
P2-06: 报告生成后可选生成 1 张配图 (deepseek-v4-flash).
P0-Future-04: detailed_report 实现子主题嵌套研究 (对标 GPTR detailed_report.py).

行业适配采用 GPTR 风格 4 层机制, 不再使用行业分类器:
- agent_role 参数 (对标 GPTR AGENT_ROLE) 注入角色 persona, 由 LLM 动态生成或调用方注入

P1-Future-04: basic_report 的 writer prompt 经 PromptFamily 策略注入 (支持中英多语言切换).
detailed_report 的子主题/引言/章节/结论 prompt 暂保留内联 (流程专用, 后续可扩展 PromptFamily).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.common.json_utils import safe_json_parse
from src.config.settings import Settings, get_settings
from src.llm.client import LLMClient, LLMTier
from src.observability.tracing import trace_chain
from src.skills.researcher.context_manager import WrittenContentCompressor
from src.skills.researcher.image_generator import ImageGenerator
from src.skills.researcher.prompts import PromptFamily, get_prompt_family
from src.skills.researcher.research_conductor import ResearchConductor

logger = logging.getLogger(__name__)

# 兜底角色 persona (对标 GPTR 默认 researcher role)
_DEFAULT_AGENT_ROLE = "你是一位资深研究分析专家, 擅长多领域综合研究."


class ReportGenerator:
    """报告生成器 (Writer 职责).

    对标 GPT Researcher ReportGenerator.
    用 smart_llm 合成长报告.
    P2-06: image_generation_enabled=True 时调用 ImageGenerator 生成配图.
    P0-Future-04: detailed_report 实现子主题嵌套研究 (对标 GPTR detailed_report).
    """

    settings: Settings
    _llm: LLMClient
    _image_generator: ImageGenerator | None
    _prompt_family: PromptFamily

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        image_generator: ImageGenerator | None = None,
        prompt_family: PromptFamily | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or LLMClient(self.settings)
        # 图像生成器延迟初始化 (仅启用时创建, 避免无谓依赖)
        self._image_generator = image_generator
        self._prompt_family = prompt_family or get_prompt_family(self.settings.prompt_family)

    async def generate_report(
        self,
        query: str,
        contexts: list[str],
        sources: list[dict[str, Any]],
        *,
        report_type: str = "basic_report",
        tone: str = "objective",
        total_words: int | None = None,
        agent_role: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """生成研究报告 (Markdown), 按 report_type 路由.

        - basic_report: 单次 LLM 合成 (对标 GPT Researcher generate_report)
        - detailed_report: 子主题嵌套研究 + TOC 拼接 (对标 GPTR detailed_report)

        agent_role (对标 GPTR AGENT_ROLE): 角色 persona 字符串,
        由 AgentCreator LLM 动态生成或调用方注入, 优先级高于默认角色.

        返回 dict 含:
        - report_md: Markdown 报告 (含配图, 若生成成功)
        - image_url: 图像 URL (若无则为 None)
        - image_b64: 图像 base64 (若无则为 None)
        """
        if report_type == "detailed_report":
            return await self._generate_detailed_report(
                query,
                contexts,
                sources,
                tone=tone,
                total_words=total_words,
                agent_role=agent_role,
                user_id=user_id,
                session_id=session_id,
            )
        return await self._generate_basic_report(
            query,
            contexts,
            sources,
            tone=tone,
            total_words=total_words,
            agent_role=agent_role,
            user_id=user_id,
            session_id=session_id,
        )

    async def _generate_basic_report(
        self,
        query: str,
        contexts: list[str],
        sources: list[dict[str, Any]],
        *,
        tone: str = "objective",
        total_words: int | None = None,
        agent_role: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """基础报告: 单次 LLM 合成 (原 generate_report 逻辑).

        对标 GPT Researcher generate_report.
        用户需求 6: 默认 Markdown, 至少 TOTAL_WORDS 字.
        P2-06: image_generation_enabled=True 时生成 1 张配图插入报告.
        """
        async with trace_chain(
            name="basic-report-generator",
            input={
                "query": query[:100],
                "contexts_count": len(contexts),
                "sources_count": len(sources),
                "report_type": "basic_report",
            },
            user_id=user_id,
            session_id=session_id,
        ) as span:
            # 上下文为空时拒绝生成 (对标 GPT Researcher 防幻觉)
            if not contexts:
                span.update(output={"error": "no_contexts"})
                return {
                    "report_md": "# 研究报告\n\n无法生成报告: 未检索到相关上下文.\n",
                    "image_url": None,
                    "image_b64": None,
                }

            # 合并上下文 (Token 优化: 截断避免超限)
            combined_context = "\n\n---\n\n".join(contexts)
            max_context_chars = self.settings.max_context_words * 4  # 粗估 4 字符 = 1 词
            if len(combined_context) > max_context_chars:
                combined_context = combined_context[:max_context_chars]

            # 构建来源引用列表 (APA 格式)
            references = self._build_references(sources)

            # 对标 GPTR: agent_role 作为角色 persona (来自 LLM 动态生成或调用方注入)
            role_persona = agent_role or _DEFAULT_AGENT_ROLE

            word_limit = total_words or self.settings.total_words
            current_date = datetime.now().strftime("%Y年%m月%d日")

            structure_hint = self._basic_report_structure()

            # P1-Future-04: prompt 经 PromptFamily 策略注入
            prompt = self._prompt_family.writer_prompt(
                query=query,
                contexts=combined_context,
                agent_role=role_persona,
                tone=tone,
                word_limit=word_limit,
                report_type="basic_report",
                current_date=current_date,
                references=references,
                structure_hint=structure_hint,
            )

            messages = [{"role": "user", "content": prompt}]
            response = await self._llm.achat(
                messages,
                tier=LLMTier.SMART,
                temperature=0.4,
                max_tokens=self.settings.smart_token_limit,
                user_id=user_id,
                session_id=session_id,
                span_name="writer-llm",
                step="writer",
            )

            report_md = response.content

            # 确保末尾有参考文献
            if "## 参考文献" not in report_md and "## References" not in report_md:
                report_md += f"\n\n## 参考文献\n\n{references}\n"

            # P2-06: 报告配图生成 (image_generation_enabled=True 时启用)
            image_url: str | None = None
            image_b64: str | None = None
            if self.settings.image_generation_enabled:
                image_url, image_b64 = await self._generate_report_image(query, user_id, session_id)
                if image_url or image_b64:
                    report_md = self._insert_image_into_report(report_md, image_url, image_b64)

            span.update(
                output={
                    "report_len": len(report_md),
                    "has_image": image_url is not None or image_b64 is not None,
                }
            )
            return {
                "report_md": report_md,
                "image_url": image_url,
                "image_b64": image_b64,
            }

    async def _generate_detailed_report(
        self,
        query: str,
        contexts: list[str],
        sources: list[dict[str, Any]],
        *,
        tone: str = "objective",
        total_words: int | None = None,  # noqa: ARG002 (保留以保持路由签名一致)
        agent_role: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """详细报告: 子主题嵌套研究 + TOC 拼接 (对标 GPTR detailed_report).

        完整流程:
        1. 初始研究: 复用传入的 contexts (避免重复检索)
        2. LLM 生成 3-5 个子主题
        3. 写引言 (LLM 基于 query + contexts)
        4. 逐子主题嵌套研究:
           - ResearchConductor.conduct_research(sub_query, mode="basic", agent_role=...)
           - WrittenContentCompressor 去重已写章节 (相似度 >= 0.5 跳过)
           - LLM 写子主题章节
        5. TOC + 引言 + 正文 + 结论 + 引用拼接

        AGENTS.md 第 10 章: 整个流程包裹在 trace_chain 内.
        AGENTS.md 第 9 章: 所有 LLM 调用经 LLMClient (achat 内部包裹 trace_generation).

        返回 dict 含:
        - report_md: 完整 Markdown 报告
        - image_url: 图像 URL (若无则为 None)
        - image_b64: 图像 base64 (若无则为 None)
        """
        async with trace_chain(
            name="detailed-report-generator",
            input={
                "query": query[:100],
                "contexts_count": len(contexts),
                "sources_count": len(sources),
                "report_type": "detailed_report",
            },
            user_id=user_id,
            session_id=session_id,
        ) as span:
            # 上下文为空时拒绝生成 (对标 GPT Researcher 防幻觉)
            if not contexts:
                span.update(output={"error": "no_contexts"})
                return {
                    "report_md": "# 研究报告\n\n无法生成报告: 未检索到相关上下文.\n",
                    "image_url": None,
                    "image_b64": None,
                }

            # 合并初始上下文 (Token 优化: 截断避免超限)
            combined_context = "\n\n---\n\n".join(contexts)
            max_context_chars = self.settings.max_context_words * 4
            if len(combined_context) > max_context_chars:
                combined_context = combined_context[:max_context_chars]

            references = self._build_references(sources)
            role_persona = agent_role or _DEFAULT_AGENT_ROLE

            # 步骤 2: LLM 生成子主题 (3-5 个)
            subtopics = await self._generate_subtopics(
                query,
                combined_context,
                role_persona=role_persona,
                user_id=user_id,
                session_id=session_id,
            )

            # 步骤 3: 写引言
            introduction = await self._write_introduction(
                query,
                combined_context,
                references,
                role_persona=role_persona,
                tone=tone,
                user_id=user_id,
                session_id=session_id,
            )

            # 步骤 4: 逐子主题嵌套研究 + 去重 + 写章节
            research_conductor = ResearchConductor(
                settings=self.settings,
                llm=self._llm,
            )
            written_compressor = WrittenContentCompressor(self.settings)

            sections: list[str] = []
            all_sources: list[dict[str, Any]] = list(sources)
            skipped_count = 0

            for topic in subtopics:
                sub_query = f"{query} - {topic}"
                try:
                    research_result = await research_conductor.conduct_research(
                        sub_query,
                        mode="basic",
                        agent_role=agent_role,
                        user_id=user_id,
                        session_id=session_id,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("子主题 '%s' 嵌套研究失败: %s", topic, e)
                    continue

                sub_contexts = research_result.get("contexts", [])
                sub_sources = research_result.get("sources", [])
                if sub_sources:
                    all_sources.extend(sub_sources)

                sub_context = "\n\n---\n\n".join(sub_contexts) if sub_contexts else combined_context
                if len(sub_context) > max_context_chars:
                    sub_context = sub_context[:max_context_chars]

                # WrittenContentCompressor 去重 (与已写入内容相似度 >= threshold 跳过)
                keep = await written_compressor.should_keep(sub_context)
                if not keep:
                    skipped_count += 1
                    logger.info("子主题 '%s' 内容与已写章节高度相似, 跳过", topic)
                    continue

                # 写子主题章节
                try:
                    section_md = await self._write_section(
                        topic,
                        sub_context,
                        references,
                        role_persona=role_persona,
                        tone=tone,
                        user_id=user_id,
                        session_id=session_id,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("子主题 '%s' 章节写入失败: %s", topic, e)
                    continue
                sections.append(section_md)

            # 步骤 5: TOC + 引言 + 正文 + 结论 + 引用拼接
            toc = self._generate_toc(subtopics)
            conclusion = await self._write_conclusion(
                query,
                sections,
                role_persona=role_persona,
                tone=tone,
                user_id=user_id,
                session_id=session_id,
            )

            # 重新构建参考文献 (含子主题研究新增的源)
            all_references = self._build_references(all_sources)
            current_date = datetime.now().strftime("%Y年%m月%d日")

            body = "\n\n".join(sections) if sections else "_(无子主题章节内容)_"
            full_report = (
                f"# {query}\n\n"
                f"_生成日期: {current_date}_\n\n"
                f"{toc}\n\n"
                f"{introduction}\n\n"
                f"{body}\n\n"
                f"{conclusion}\n\n"
                f"## 参考文献\n\n{all_references}\n"
            )

            # P2-06: 报告配图生成 (image_generation_enabled=True 时启用)
            image_url: str | None = None
            image_b64: str | None = None
            if self.settings.image_generation_enabled:
                image_url, image_b64 = await self._generate_report_image(query, user_id, session_id)
                if image_url or image_b64:
                    full_report = self._insert_image_into_report(full_report, image_url, image_b64)

            span.update(
                output={
                    "report_len": len(full_report),
                    "has_image": image_url is not None or image_b64 is not None,
                    "subtopics_count": len(subtopics),
                    "sections_count": len(sections),
                    "skipped_count": skipped_count,
                }
            )
            return {
                "report_md": full_report,
                "image_url": image_url,
                "image_b64": image_b64,
            }

    async def _generate_subtopics(
        self,
        query: str,
        context: str,
        *,
        role_persona: str,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[str]:
        """LLM 生成 3-5 个子主题 (对标 GPTR detailed_report subtopic list).

        用 safe_json_parse 解析 LLM 输出的 JSON 数组.
        """
        prompt = f"""{role_persona}

请基于以下研究问题与初始上下文, 拆解为 3-5 个用于分章节深入研究的子主题.

要求:
1. 子主题应覆盖问题的不同维度 (如市场/技术/竞争/政策/趋势等)
2. 子主题应基于上下文中实际出现的内容, 不得编造
3. 每个子主题为简洁的中/英文短语
4. 返回 JSON 数组格式: ["子主题1", "子主题2", ...]

研究问题: {query}

初始上下文:
{context[:4000]}

请返回 3-5 个子主题的 JSON 数组:"""
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        response = await self._llm.achat(
            messages,
            tier=LLMTier.STRATEGIC,
            temperature=0.4,
            max_tokens=800,
            user_id=user_id,
            session_id=session_id,
            span_name="detailed-subtopics",
            step="planner",
        )
        topics = safe_json_parse(response.content, fallback=[query])
        if isinstance(topics, list) and topics:
            return [str(t) for t in topics if t][:5]
        return [query]

    async def _write_introduction(
        self,
        query: str,
        context: str,
        references: str,
        *,
        role_persona: str,
        tone: str = "objective",
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> str:
        """LLM 写引言 (基于 query + contexts)."""
        current_date = datetime.now().strftime("%Y年%m月%d日")
        prompt = f"""{role_persona}

请基于以下上下文, 为「{query}」研究报告撰写引言部分.

要求:
1. 简述研究背景、目的与核心发现
2. 字数 300-500 字
3. 语气: {tone} (objective=客观, analytical=分析性, opinionated=观点鲜明, casual=通俗)
4. Web 源必须超链接引用: ([说明](url))
5. 不得编造未在上下文中出现的数据
6. 注入当前日期: {current_date}
7. 仅输出引言内容 (## 引言 标题下), 不含其他章节

上下文:
{context[:6000]}

参考文献来源:
{references}

请输出引言 (以 `## 引言` 开头):"""
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        response = await self._llm.achat(
            messages,
            tier=LLMTier.SMART,
            temperature=0.4,
            max_tokens=self.settings.smart_token_limit,
            user_id=user_id,
            session_id=session_id,
            span_name="detailed-intro",
            step="writer",
        )
        content = response.content.strip()
        if not content.startswith("## 引言"):
            content = "## 引言\n\n" + content
        return content

    async def _write_section(
        self,
        topic: str,
        context: str,
        references: str,
        *,
        role_persona: str,
        tone: str = "objective",
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> str:
        """LLM 写子主题章节 (基于 sub_context + sources)."""
        prompt = f"""{role_persona}

请基于以下子主题上下文, 撰写「{topic}」章节内容.

要求:
1. 字数 500-1000 字
2. 结构化标题: ### 子小节
3. 语气: {tone} (objective=客观, analytical=分析性, opinionated=观点鲜明, casual=通俗)
4. Web 源必须超链接引用: ([说明](url))
5. 不得编造未在上下文中出现的数据
6. 仅输出本章节内容 (## 章节标题 下), 不含其他章节

子主题上下文:
{context[:6000]}

参考文献来源:
{references}

请输出本章节 (以 `## {topic}` 开头):"""
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        response = await self._llm.achat(
            messages,
            tier=LLMTier.SMART,
            temperature=0.4,
            max_tokens=self.settings.smart_token_limit,
            user_id=user_id,
            session_id=session_id,
            span_name="detailed-section",
            step="writer",
        )
        content = response.content.strip()
        if not content.startswith("## "):
            content = f"## {topic}\n\n" + content
        return content

    async def _write_conclusion(
        self,
        query: str,
        sections: list[str],
        *,
        role_persona: str,
        tone: str = "objective",
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> str:
        """LLM 写结论 (基于 query + 已写章节摘要)."""
        sections_summary = "\n\n".join(s[:500] for s in sections)
        prompt = f"""{role_persona}

请基于以下已写章节内容, 为「{query}」研究报告撰写结论部分.

要求:
1. 总结核心发现与洞察
2. 提出未来展望与建议
3. 字数 300-500 字
4. 语气: {tone} (objective=客观, analytical=分析性, opinionated=观点鲜明, casual=通俗)
5. 仅输出结论内容 (## 结论 标题下), 不含其他章节

已写章节摘要:
{sections_summary[:6000]}

请输出结论 (以 `## 结论` 开头):"""
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        response = await self._llm.achat(
            messages,
            tier=LLMTier.SMART,
            temperature=0.4,
            max_tokens=self.settings.smart_token_limit,
            user_id=user_id,
            session_id=session_id,
            span_name="detailed-conclusion",
            step="writer",
        )
        content = response.content.strip()
        if not content.startswith("## 结论"):
            content = "## 结论\n\n" + content
        return content

    @staticmethod
    def _generate_toc(subtopics: list[str]) -> str:
        """生成目录 (对标 GPTR TOC)."""
        if not subtopics:
            return ""
        lines = ["## 目录", ""]
        for i, topic in enumerate(subtopics, 1):
            lines.append(f"{i}. {topic}")
        lines.append("")
        return "\n".join(lines)

    async def _generate_report_image(
        self,
        query: str,
        user_id: str | None,
        session_id: str | None,
    ) -> tuple[str | None, str | None]:
        """生成报告配图 (P2-06).

        图像生成失败时降级 (报告不带图, 记录 warning), 不阻断主流程.
        返回 (image_url, image_b64), 失败均为 None.
        """
        try:
            if self._image_generator is None:
                self._image_generator = ImageGenerator(self.settings)

            # 配图提示词: 基于报告主题生成, 风格专业简洁
            image_prompt = (
                f"为「{query}」研究报告生成一张概念配图, 风格专业、简洁, 适合商业研究报告"
            )
            result = await self._image_generator.generate_image(
                image_prompt,
                size=self.settings.image_size,
                user_id=user_id,
                session_id=session_id,
            )
            return result.get("url"), result.get("b64")
        except Exception as e:  # noqa: BLE001
            # 图像生成失败降级: 报告不带图, 不阻断主流程
            logger.warning(
                "报告配图生成失败, 降级为不带图报告 (query=%s): %s",
                query[:100],
                e,
            )
            return None, None

    @staticmethod
    def _insert_image_into_report(
        report_md: str,
        image_url: str | None,
        image_b64: str | None,
    ) -> str:
        """在报告 Markdown 中插入配图.

        策略: 在第一个 H1 标题后插入 (标题下方, 正文上方).
        若无 H1, 则在报告开头插入.
        """
        if image_url:
            image_ref = image_url
        elif image_b64:
            image_ref = f"data:image/png;base64,{image_b64}"
        else:
            return report_md

        image_md = f"\n![报告配图]({image_ref})\n"

        # 在第一个 H1 后插入
        lines = report_md.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("# "):
                return "\n".join(lines[: i + 1]) + image_md + "\n" + "\n".join(lines[i + 1 :])

        # 无 H1, 在开头插入
        return image_md + "\n" + report_md

    @staticmethod
    def _basic_report_structure() -> str:
        """基础报告结构 (不再有行业预设维度, 由 LLM 自主决定)."""
        return """# {标题}

## 摘要
(简述研究主题与核心发现)

## 关键维度
(由 LLM 根据上下文自主提炼研究维度)

## 分析与洞察
(基于上下文的深度分析)

## 结论与展望
(总结与未来趋势)

## 参考文献
(APA 格式引用列表)"""

    @staticmethod
    def _detailed_report_structure() -> str:
        """详细报告静态结构提示 (P0-Future-04 后改用子主题嵌套动态生成, 此结构仅作参考).

        保留用于文档参考与潜在降级场景; _generate_detailed_report 不再使用此静态结构.
        """
        return """# {标题}

## 摘要
(详述研究背景、目的、方法与核心发现)

## 行业背景
(行业现状、发展历程)

## 深度分析
(由 LLM 根据上下文自主提炼研究维度, 分小节详细分析)

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
