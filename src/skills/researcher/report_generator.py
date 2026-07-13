"""ReportGenerator 报告生成器.

Writer (报告合成).

按动态角色 persona 合成长报告, 支持 tone 语气控制.
报告生成后可选生成 1 张配图 (deepseek-v4-flash).
detailed_report 实现子主题嵌套研究.

行业适配采用 4 层机制, 不再使用行业分类器:
- agent_role 参数 (AGENT_ROLE) 注入角色 persona, 由 LLM 动态生成或调用方注入

basic_report 的 writer prompt 经 PromptFamily 策略注入 (支持中英多语言切换).
detailed_report 的子主题/引言/章节/结论 prompt 暂保留内联 (流程专用, 后续可扩展 PromptFamily).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any

from src.common.json_utils import safe_json_parse
from src.config.settings import Settings, get_settings
from src.llm.client import LLMClient, LLMTier, get_llm_client
from src.observability.tracing import trace_chain
from src.skills.researcher.context_manager import WrittenContentCompressor
from src.skills.researcher.image_generator import ImageGenerator
from src.skills.researcher.prompts import PromptFamily, get_prompt_family
from src.skills.researcher.research_conductor import ResearchConductor

logger = logging.getLogger(__name__)

# 兜底角色 persona (默认 researcher role)
_DEFAULT_AGENT_ROLE = "你是一位资深研究分析专家, 擅长多领域综合研究."

# 章节/段落 LLM 调用失败后的占位文本
_SECTION_FAILURE_PLACEHOLDER = "[此章节生成失败, 请重试]"

# LLM 调用单章节重试次数 (失败后再试 1 次)
_LLM_RETRY_TIMES = 1

# 报告风格预设描述 (用于 detailed_report 内联 prompt 注入,
# 与 prompts.py DefaultPromptFamily._STYLE_PROMPTS 保持一致)
_REPORT_STYLE_DESCRIPTIONS: dict[str, str] = {
    "academic": (
        "学术风格: 严谨客观, 引用来源, 使用正式学术语言, "
        "段落间逻辑清晰, 论点需有数据或文献支撑, 避免口语化表达"
    ),
    "business": (
        "商业风格: 简洁明了, 结论先行, 使用商业术语, "
        "聚焦价值与决策建议, 突出关键指标与 ROI, 段落短小精悍"
    ),
    "casual": (
        "通俗风格: 易于理解, 避免专业术语, 适合大众阅读, 多用类比与案例, 语言亲切自然, 降低认知门槛"
    ),
    "news": (
        "新闻风格: 倒金字塔结构, 5W1H, 客观报道, "
        "导语概括核心事实, 正文按重要性递减展开, 强调时效与现场感"
    ),
}


class ReportGenerator:
    """报告生成器 (Writer 职责).

    用 smart_llm 合成长报告.
    image_generation_enabled=True 时调用 ImageGenerator 生成配图.
    detailed_report 实现子主题嵌套研究.
    """

    settings: Settings
    _llm: LLMClient
    _image_generator: ImageGenerator | None
    _prompt_family: PromptFamily

    # 多语言报告生成指令 (5 种语言, 中文默认无需额外指令)
    _LANGUAGE_INSTRUCTIONS: dict[str, str] = {
        "zh": "请用中文撰写报告，使用中文标点和格式。",
        "en": "Please write the report in English with proper English punctuation and formatting.",
        "ja": "日本語でレポートを執筆してください。日本語の句読点と書式を使用してください。",
        "ko": "한국어로 보고서를 작성해 주세요. 한국어 문장 부호와 형식을 사용하세요.",
        "fr": "Veuillez rédiger le rapport en français avec une ponctuation et un formatage français appropriés.",
    }

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        image_generator: ImageGenerator | None = None,
        prompt_family: PromptFamily | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or get_llm_client()
        # 图像生成器延迟初始化 (仅启用时创建, 避免无谓依赖)
        self._image_generator = image_generator
        self._prompt_family = prompt_family or get_prompt_family(self.settings.prompt_family)

    def _get_language_instruction(self, language: str | None) -> str:
        """获取语言指令 (多语言).

        中文 (zh) 为默认语言, 无需额外指令; 其他语言返回对应撰写指令,
        由调用方追加到 prompt 末尾, 让 LLM 直接用目标语言生成 (非翻译).

        Args:
            language: 语言代码 (zh|en|ja|ko|fr), None 或未知值视为 zh.

        Returns:
            语言指令字符串, 中文/未知返回空串.
        """
        if not language or language == "zh":
            return ""  # 中文是默认, 不需额外指令
        return self._LANGUAGE_INSTRUCTIONS.get(language, "")

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
        language: str = "zh",
    ) -> dict[str, Any]:
        """生成研究报告 (Markdown), 按 report_type 路由.

        - basic_report: 单次 LLM 合成
        - detailed_report: 子主题嵌套研究 + TOC 拼接

        agent_role (AGENT_ROLE): 角色 persona 字符串,
        由 AgentCreator LLM 动态生成或调用方注入, 优先级高于默认角色.

        language: 报告语言代码 (zh|en|ja|ko|fr), 默认 zh 中文;
        非 zh 时在 prompt 末尾追加语言指令, 让 LLM 直接用目标语言生成.

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
                language=language,
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
            language=language,
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
        language: str = "zh",
    ) -> dict[str, Any]:
        """基础报告: 单次 LLM 合成 (原 generate_report 逻辑).

        用户需求 6: 默认 Markdown, 至少 TOTAL_WORDS 字.
        image_generation_enabled=True 时生成 1 张配图插入报告.
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
            # 空上下文拒绝生成守卫 (防幻觉)
            # 不仅检查列表为空, 还检查所有上下文是否均为空白字符串
            if not contexts or all(not str(c).strip() for c in contexts):
                span.update(output={"error": "no_contexts"})
                return {
                    "report_md": (
                        f'抱歉，针对 "{query}" 未检索到任何有效资料来源。'
                        "搜索引擎可能未返回结果或被限制，无法生成可靠的、有来源支撑的研究报告。"
                        "请尝试更换查询词或稍后重试。"
                    ),
                    "image_url": None,
                    "image_b64": None,
                }

            # 合并上下文 (Token 优化: 截断避免超限)
            combined_context = "\n\n---\n\n".join(contexts)
            max_context_chars = self.settings.max_context_words * 4  # 粗估 4 字符 = 1 词
            if len(combined_context) > max_context_chars:
                combined_context = combined_context[:max_context_chars]

            # 构建来源引用列表 (APA 格式, 多语言占位文本)
            references = self._build_references(sources, language=language)

            # agent_role 作为角色 persona (来自 LLM 动态生成或调用方注入)
            role_persona = agent_role or _DEFAULT_AGENT_ROLE

            word_limit = total_words or self.settings.total_words
            current_date = datetime.now().strftime("%Y年%m月%d日")

            structure_hint = self._basic_report_structure(language=language)

            # prompt 经 PromptFamily 策略注入
            # 注入 report_style 风格预设
            # 末尾追加 Tone 语气提示词
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
                report_style=self.settings.report_style,
            ) + self._prompt_family.get_tone_prompt(tone)

            # 多语言报告生成 (非 zh 时追加语言指令, 让 LLM 直接用目标语言生成)
            lang_instruction = self._get_language_instruction(language)
            if lang_instruction:
                prompt += f"\n\n{lang_instruction}"

            messages = [{"role": "user", "content": prompt}]
            # 报告写作统一用 SMART tier (deepseek-v4-flash), 启用推理模式.
            # 对标 GPTR: 报告写作全程用 smart_llm (gpt-4.1 / deepseek-v4-flash).
            # FAST tier (glm-4-flash) 不用于产生报告内容 (质量不足).
            # LLM 调用增加 try/except + 1 次重试, 仍失败用占位文本
            report_md = await self._achat_with_retry(
                messages,
                tier=LLMTier.SMART,
                temperature=0.4,
                max_tokens=self.settings.smart_token_limit,
                user_id=user_id,
                session_id=session_id,
                span_name="writer-llm",
                step="writer",
                fallback=_SECTION_FAILURE_PLACEHOLDER,
                reasoning_effort=self.settings.deep_research_reasoning_effort,
            )

            # 规范化 Markdown 输出 (修复 LLM 常见格式问题: 段落紧贴、表格无空行、引用紧贴等)
            report_md = self._normalize_markdown(report_md)

            # 确保末尾有参考文献 (避免双重参考章节: 若 LLM 已生成参考文献, 不再追加 _format_sources)
            has_references_section = (
                "## 参考文献" in report_md
                or "## References" in report_md
                or "## 参考来源" in report_md
            )
            if not has_references_section:
                # LLM 未生成参考文献章节, 追加完整来源列表 (含章节标题 + 编号列表)
                report_md += self._format_sources(sources, language=language)
            else:
                # LLM 已生成参考文献章节, 仅补充来源 URL 列表 (无章节标题, 避免重复)
                # 追加引用来源列表 (APA 格式)
                pass  # 不再追加 _format_sources, 避免双重参考章节

            # YAML frontmatter (enable_frontmatter=True 时在报告首部追加元信息块)
            # 便于下游解析/索引.
            if getattr(self.settings, "enable_frontmatter", False):
                report_md = (
                    self._build_frontmatter(
                        query=query,
                        report_md=report_md,
                        sources=sources,
                        language=language,
                    )
                    + report_md
                )

            # 报告配图生成 (image_generation_enabled=True 时启用)
            image_url: str | None = None
            image_b64: str | None = None
            image_svg: str | None = None
            if self.settings.image_generation_enabled:
                image_url, image_b64, image_svg = await self._generate_report_image(
                    query, user_id, session_id, language=language
                )
                if image_url or image_b64 or image_svg:
                    report_md = self._insert_image_into_report(
                        report_md, image_url, image_b64, image_svg=image_svg, language=language
                    )

            span.update(
                output={
                    "report_len": len(report_md),
                    "has_image": image_url is not None
                    or image_b64 is not None
                    or image_svg is not None,
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
        language: str = "zh",
    ) -> dict[str, Any]:
        """详细报告: 子主题嵌套研究 + TOC 拼接.

        完整流程:
        1. 初始研究: 复用传入的 contexts (避免重复检索)
        2. LLM 生成 3-5 个子主题
        3. 写引言 (LLM 基于 query + contexts)
        4. 逐子主题嵌套研究:
           - ResearchConductor.conduct_research(sub_query, mode="basic", agent_role=...)
           - WrittenContentCompressor 去重已写章节 (相似度 >= 0.5 跳过)
           - LLM 写子主题章节
        5. TOC + 引言 + 正文 + 结论 + 引用拼接

        子主题并行化, 用 asyncio.gather 并行处理所有子主题
                  (每个子主题独立 research + write, 互不依赖).
        单个子主题 LLM 调用失败时重试 1 次, 仍失败用占位文本, 不阻断整体.
        注入 report_style 风格预设.

        整个流程包裹在 trace_chain 内.
        所有 LLM 调用经 LLMClient (achat 内部包裹 trace_generation).

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
            # 空上下文拒绝生成守卫 (防幻觉)
            # 不仅检查列表为空, 还检查所有上下文是否均为空白字符串
            if not contexts or all(not str(c).strip() for c in contexts):
                span.update(output={"error": "no_contexts"})
                return {
                    "report_md": (
                        f'抱歉，针对 "{query}" 未检索到任何有效资料来源。'
                        "搜索引擎可能未返回结果或被限制，无法生成可靠的、有来源支撑的研究报告。"
                        "请尝试更换查询词或稍后重试。"
                    ),
                    "image_url": None,
                    "image_b64": None,
                }

            # 合并初始上下文 (Token 优化: 截断避免超限)
            combined_context = "\n\n---\n\n".join(contexts)
            max_context_chars = self.settings.max_context_words * 4
            if len(combined_context) > max_context_chars:
                combined_context = combined_context[:max_context_chars]

            references = self._build_references(sources, language=language)
            role_persona = agent_role or _DEFAULT_AGENT_ROLE

            # 步骤 2+3: 子主题生成与引言并行 (两者均只依赖 query/contexts/
            # references/role_persona, 无数据依赖, 并行可节省引言 LLM 调用延迟 ~2-3s)
            subtopics, introduction = await asyncio.gather(
                self._generate_subtopics(
                    query,
                    combined_context,
                    role_persona=role_persona,
                    user_id=user_id,
                    session_id=session_id,
                    language=language,
                ),
                self._write_introduction(
                    query,
                    combined_context,
                    references,
                    role_persona=role_persona,
                    tone=tone,
                    user_id=user_id,
                    session_id=session_id,
                    language=language,
                ),
            )

            # 步骤 4: 子主题并行嵌套研究 + 去重 + 写章节
            research_conductor = ResearchConductor(
                settings=self.settings,
                llm=self._llm,
            )
            written_compressor = WrittenContentCompressor(self.settings)
            # 并行场景下保护 WrittenContentCompressor 内部状态 (should_keep 会修改 _written_embeddings)
            dedup_lock = asyncio.Lock()

            # 并行处理所有子主题, 每个独立 research + write, 互不依赖
            section_results = await asyncio.gather(
                *[
                    self._research_and_write_subtopic(
                        topic=topic,
                        query=query,
                        combined_context=combined_context,
                        references=references,
                        role_persona=role_persona,
                        tone=tone,
                        agent_role=agent_role,
                        max_context_chars=max_context_chars,
                        research_conductor=research_conductor,
                        written_compressor=written_compressor,
                        dedup_lock=dedup_lock,
                        user_id=user_id,
                        session_id=session_id,
                        language=language,
                    )
                    for topic in subtopics
                ],
                return_exceptions=False,
            )

            # 汇总并行结果 (按子主题顺序)
            # TOC 后置生成 + 失败章节标记 + 一致性校验
            # TOC 从实际 body 提取, 非独立生成
            sections: list[str] = []
            valid_topics_for_toc: list[str] = []
            all_sources: list[dict[str, Any]] = list(sources)
            skipped_count = 0
            for topic, (section_md, sub_sources, skipped) in zip(
                subtopics, section_results, strict=False
            ):
                if skipped:
                    skipped_count += 1
                    logger.info("子主题 '%s' 被去重跳过, 从 TOC 移除", topic)
                    continue
                if sub_sources:
                    all_sources.extend(sub_sources)
                if section_md:
                    if section_md == _SECTION_FAILURE_PLACEHOLDER:
                        # 优化 4: 失败章节在 TOC 中标记, 正文中显示失败提示
                        valid_topics_for_toc.append(f"{topic} (生成失败)")
                        sections.append(f"### {topic}\n\n*此章节内容生成失败, 请重试。*")
                    else:
                        valid_topics_for_toc.append(topic)
                        sections.append(section_md)
                else:
                    logger.warning("子主题 '%s' section_md 为空, 从 TOC 移除", topic)

            # 优化 5: 一致性校验 (防御性编程)
            if len(valid_topics_for_toc) != len(sections):
                logger.warning(
                    "TOC 条目数 (%d) 与 sections 数 (%d) 不一致, 可能存在内容缺失",
                    len(valid_topics_for_toc),
                    len(sections),
                )
                valid_topics_for_toc = valid_topics_for_toc[: len(sections)]

            # 空章节告警
            if len(sections) < len(subtopics):
                missing = len(subtopics) - len(sections)
                logger.warning(
                    "detailed_report 有 %d/%d 个子主题被跳过 (去重/失败), 报告可能不完整",
                    missing,
                    len(subtopics),
                )

            # 步骤 5: TOC + 引言 + 正文 + 结论 + 引用拼接
            # 优化 1: TOC 只含有效子主题 (TOC 后置生成)
            toc = self._generate_toc(valid_topics_for_toc, language=language)
            conclusion = await self._write_conclusion(
                query,
                sections,
                role_persona=role_persona,
                tone=tone,
                user_id=user_id,
                session_id=session_id,
                language=language,
            )

            # 重新构建参考文献 (含子主题研究新增的源); _format_sources 已含 ## 参考来源 章节, 此处无需重复
            current_date = datetime.now().strftime("%Y年%m月%d日")

            # 多语言: 标题和日期标签翻译
            if language == "en":
                # 英文报告: 翻译标题和日期标签
                report_title = await self._translate_query_title(query, user_id, session_id)
                date_label = f"_Generated on: {datetime.now().strftime('%Y-%m-%d')}_"
                empty_body_placeholder = "_(No section content)_"
            else:
                report_title = query
                date_label = f"_生成日期: {current_date}_"
                empty_body_placeholder = "_(无子主题章节内容)_"

            body = "\n\n".join(sections) if sections else empty_body_placeholder
            full_report = (
                f"# {report_title}\n\n"
                f"{date_label}\n\n"
                f"{toc}\n\n"
                f"{introduction}\n\n"
                f"{body}\n\n"
                f"{conclusion}\n\n"
            )

            # 追加引用来源列表 (APA 格式, 含子主题研究新增源)
            # 避免双重参考章节: 若 LLM 已在章节内/结论中生成参考文献块, 不再追加 _format_sources
            # 检测 H2 标题 (## 参考文献/References/参考来源/Bibliography) 和粗体块 (**参考文献** 等)
            has_references_section = (
                "## 参考文献" in full_report
                or "## References" in full_report
                or "## 参考来源" in full_report
                or "## Bibliography" in full_report
                or "**参考文献**" in full_report
                or "**References**" in full_report
                or "**参考来源**" in full_report
                or "**Bibliography**" in full_report
            )
            if not has_references_section:
                full_report += self._format_sources(all_sources, language=language)

            # 规范化 Markdown 输出 (修复 LLM 常见格式问题: 段落紧贴、表格无空行、引用紧贴等)
            full_report = self._normalize_markdown(full_report)

            # 报告配图生成 (image_generation_enabled=True 时启用)
            image_url: str | None = None
            image_b64: str | None = None
            image_svg: str | None = None
            if self.settings.image_generation_enabled:
                image_url, image_b64, image_svg = await self._generate_report_image(
                    query, user_id, session_id, language=language
                )
                if image_url or image_b64 or image_svg:
                    full_report = self._insert_image_into_report(
                        full_report, image_url, image_b64, image_svg=image_svg, language=language
                    )

            span.update(
                output={
                    "report_len": len(full_report),
                    "has_image": image_url is not None
                    or image_b64 is not None
                    or image_svg is not None,
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

    async def _research_and_write_subtopic(
        self,
        *,
        topic: str,
        query: str,
        combined_context: str,
        references: str,
        role_persona: str,
        tone: str,
        agent_role: str | None,
        max_context_chars: int,
        research_conductor: ResearchConductor,
        written_compressor: WrittenContentCompressor,
        dedup_lock: asyncio.Lock,
        user_id: str | None,
        session_id: str | None,
        language: str = "zh",
    ) -> tuple[str | None, list[dict[str, Any]], bool]:
        """单个子主题的 research + write 独立函数 (并行单元).

        每个子主题独立调用 research + write, 互不依赖:
        1. ResearchConductor.conduct_research(sub_query, mode="basic")
        2. WrittenContentCompressor 去重 (锁保护, 避免并行竞态)
        3. _write_section 写章节 (内部已含 try/except + 重试)

        异常处理: 单个子主题失败不阻断整体, 返回占位文本.

        Args:
            topic: 子主题名称
            query: 主研究问题
            combined_context: 初始合并上下文 (子主题研究失败时降级使用)
            references: 参考文献文本
            role_persona: 角色 persona
            tone: 语气
            agent_role: 调用方注入的角色 persona (传给 ResearchConductor)
            max_context_chars: 单子主题上下文字符上限
            research_conductor: 研究执行器 (共享实例, 线程安全)
            written_compressor: 已写入内容去重器 (共享实例, 用 dedup_lock 保护)
            dedup_lock: 保护 written_compressor 状态的异步锁
            user_id: 用户 ID
            session_id: 会话 ID
            language: 报告语言代码 (zh|en|ja|ko|fr), 默认 zh

        Returns:
            (section_md, sub_sources, skipped):
            - section_md: 章节 Markdown, 失败时为占位文本; 跳过时为 None
            - sub_sources: 子主题研究新增的来源列表
            - skipped: 是否因去重被跳过
        """
        sub_query = f"{query} - {topic}"
        # 复用主研究 contexts, 仅在上下文不足时触发嵌套搜索 (消除 5 倍搜索冗余)
        # 主研究 combined_context 已含主查询的完整搜索结果, 子主题可直接复用
        # 仅当 combined_context 过短 (<2000 字符, 可能主研究未覆盖该子主题) 时补充搜索
        sub_contexts: list[str] = []
        sub_sources: list[dict[str, Any]] = []
        if len(combined_context) < 2000:
            # 上下文不足, 触发嵌套搜索补充
            try:
                research_result = await research_conductor.conduct_research(
                    sub_query,
                    mode="basic",
                    agent_role=agent_role,
                    user_id=user_id,
                    session_id=session_id,
                )
                sub_contexts = research_result.get("contexts", [])
                sub_sources = research_result.get("sources", []) or []
            except Exception as e:  # noqa: BLE001
                logger.warning("子主题 '%s' 嵌套研究失败, 降级用初始上下文: %s", topic, e)
                sub_contexts = []
                sub_sources = []

        # 每个子主题使用独立 sub_context (串行设计)
        # 从 combined_context 中用 BM25 检索与 topic 相关的片段,
        # 而非整体复用, 避免并行场景下所有子主题 embedding 必然相似导致去重误判
        if sub_contexts:
            sub_context = "\n\n---\n\n".join(sub_contexts)
        elif combined_context:
            sub_context = self._extract_topic_context(topic, combined_context)
        else:
            sub_context = ""
        if len(sub_context) > max_context_chars:
            sub_context = sub_context[:max_context_chars]

        # 2. WrittenContentCompressor 去重 (缩小 dedup_lock 锁粒度)
        # 锁外: compute_embedding 完成网络 I/O (embed_texts), 不持锁保持并行度
        # 锁内: check_and_update 做 numpy 相似度比对 + 更新内部状态 (同步操作)
        # 使用 check_and_update_partial 只丢弃相似 chunk, 保留差异部分
        try:
            chunks, content_embs = await written_compressor.compute_embedding(sub_context)
            async with dedup_lock:
                keep, filtered_context = written_compressor.check_and_update_partial(
                    chunks, content_embs
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("子主题 '%s' 去重检查失败, 保留内容: %s", topic, e)
            keep = True
            filtered_context = sub_context

        if not keep:
            logger.info("子主题 '%s' 内容与已写章节高度相似, 跳过", topic)
            return None, sub_sources, True

        # 3. 写子主题章节 (_write_section 内部已含 try/except + 重试)
        # 优化 3: 使用过滤后的 context (只含不相似 chunk), 避免重复内容
        section_md = await self._write_section(
            topic,
            filtered_context,
            references,
            role_persona=role_persona,
            tone=tone,
            user_id=user_id,
            session_id=session_id,
            language=language,
        )
        return section_md, sub_sources, False

    def _extract_topic_context(self, topic: str, combined_context: str) -> str:
        """从 combined_context 中提取与 topic 相关的片段.

        用 BM25 关键词匹配检索相关片段, 而非整体复用 combined_context,
        避免并行场景下所有子主题 embedding 必然相似导致去重误判.

        串行设计: 用 for 循环串行处理子主题, 每个子主题独立研究;
        本项目用 asyncio.gather 并行, 通过 BM25 检索为每个子主题构造独立 sub_context,
        使 embedding 不会必然相似.

        Args:
            topic: 子主题文本 (用于 BM25 打分)
            combined_context: 主研究合并上下文

        Returns:
            与 topic 最相关的片段 (Top-3, 按 BM25 分数降序拼接).
            combined_context 过短或分词失败时降级返回原文.
        """
        if not combined_context or not combined_context.strip():
            return ""
        # 上下文过短时无需检索, 直接返回
        if len(combined_context) < 500:
            return combined_context

        try:
            # 1. 分块 (复用 embeddings_filter 的 recursive_split, chunk_size=500)
            from src.rag.embeddings_filter import DEFAULT_SEPARATORS, recursive_split

            chunks = recursive_split(
                combined_context,
                separators=DEFAULT_SEPARATORS,
                chunk_size=500,
                chunk_overlap=50,
            )
            if len(chunks) <= 1:
                return combined_context

            # 2. jieba 分词
            import jieba

            query_tokens = list(jieba.cut_for_search(topic))
            query_tokens = [t for t in query_tokens if t.strip()]
            chunk_tokens = [[t for t in jieba.cut_for_search(c) if t.strip()] for c in chunks]

            if not query_tokens or not any(chunk_tokens):
                return combined_context

            # 3. BM25 打分
            from rank_bm25 import BM25Okapi

            bm25 = BM25Okapi(chunk_tokens)
            scores = bm25.get_scores(query_tokens)

            # 4. 取 Top-3 相关片段 (分数 > 0)
            scored = list(zip(scores, chunks, strict=False))
            scored = [(s, c) for s, c in scored if s > 0]
            if not scored:
                # 无相关片段, 降级返回原文前 2000 字符
                return combined_context[:2000]
            scored.sort(key=lambda x: x[0], reverse=True)
            top_chunks = [c for _, c in scored[:3]]
            return "\n\n".join(top_chunks)
        except Exception as e:  # noqa: BLE001
            logger.warning("BM25 检索 topic context 失败, 降级用原文: %s", e)
            return combined_context

    async def _generate_subtopics(
        self,
        query: str,
        context: str,
        *,
        role_persona: str,
        user_id: str | None = None,
        session_id: str | None = None,
        language: str = "zh",
    ) -> list[str]:
        """LLM 生成 3-5 个子主题.

        优化:
        - prompt 提取到 PromptFamily.subtopics_prompt
        - temperature: 0.4 → 0.25
        - 用 STRATEGIC LLM (deepseek-v4-pro) 拆解, 高质量推理

        用 safe_json_parse 解析 LLM 输出的 JSON 数组.
        LLM 调用增加 try/except + 1 次重试, 失败降级为 [query].
        language 参数控制子主题语言 (zh=中文, en=英文).
        """
        # prompt 经 PromptFamily.subtopics_prompt 注入
        prompt = self._prompt_family.subtopics_prompt(
            query=query,
            context=context,
            role_persona=role_persona,
            max_subtopics=self.settings.max_subtopics,
        )
        # 多语言: 非 zh 时追加语言指令, 让 LLM 用目标语言生成子主题
        lang_instruction = self._get_language_instruction(language)
        if lang_instruction:
            prompt += f"\n\n{lang_instruction}"
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        # LLM 调用增加重试, 失败降级为 [query]
        # temperature 0.4 → 0.25
        # 子主题拆解是规划任务, 用 STRATEGIC (deepseek-v4-pro) 高质量推理
        content = await self._achat_with_retry(
            messages,
            tier=LLMTier.STRATEGIC,
            temperature=0.25,
            max_tokens=800,
            user_id=user_id,
            session_id=session_id,
            span_name="detailed-subtopics",
            step="planner",
            fallback=f'["{query}"]',
            reasoning_effort=self.settings.deep_research_reasoning_effort,
        )
        topics = safe_json_parse(content, fallback=[query])
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
        language: str = "zh",
    ) -> str:
        """LLM 写引言 (基于 query + contexts).

        优化:
        - prompt 提取到 PromptFamily.introduction_prompt
        - temperature: 0.4 → 0.25

        LLM 调用增加 try/except + 1 次重试, 失败用占位文本.
        注入 report_style 风格预设.
        language 参数控制引言语言 (zh=中文, en=英文).
        """
        current_date = datetime.now().strftime("%Y年%m月%d日")
        # 注入风格描述
        style_desc = _REPORT_STYLE_DESCRIPTIONS.get(
            self.settings.report_style, _REPORT_STYLE_DESCRIPTIONS["academic"]
        )
        # prompt 经 PromptFamily.introduction_prompt 注入
        prompt = self._prompt_family.introduction_prompt(
            query=query,
            context=context,
            references=references,
            role_persona=role_persona,
            tone=tone,
            current_date=current_date,
            style_desc=style_desc,
            word_min=self.settings.detailed_intro_word_min,
            word_max=self.settings.detailed_intro_word_max,
        )
        # 多语言: 非 zh 时追加语言指令, 让 LLM 用目标语言撰写引言
        lang_instruction = self._get_language_instruction(language)
        if lang_instruction:
            prompt += f"\n\n{lang_instruction}"
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        # temperature 0.4 → 0.25
        # 对标 GPTR: 报告引言用 smart_llm (gpt-4.1 / deepseek-v4-flash), 启用推理模式
        content = await self._achat_with_retry(
            messages,
            tier=LLMTier.SMART,
            temperature=0.25,
            max_tokens=self.settings.smart_token_limit,
            user_id=user_id,
            session_id=session_id,
            span_name="detailed-intro",
            step="writer",
            fallback=_SECTION_FAILURE_PLACEHOLDER,
            reasoning_effort=self.settings.deep_research_reasoning_effort,
        )
        content = content.strip()
        # 清洗引言末尾 LLM 偶尔生成的 **参考文献** 粗体块 (参考文献由报告组装层统一追加)
        content = self._strip_inline_references_block(content)
        # 多语言: 引言标题前缀
        intro_heading = "## Introduction" if language == "en" else "## 引言"
        if not content.startswith("## Introduction") and not content.startswith("## 引言"):
            content = f"{intro_heading}\n\n" + content
        return content

    @staticmethod
    def _sanitize_section_subtitles(content: str) -> str:
        """清洗章节内部的冲突子标题.

        检测 ``###`` 子标题, 若为"引言"/"总结"/"结论"等与报告级章节冲突的标题:
        - "引言"/"Introduction" → 移除子标题行 (引言只在报告开头)
        - "总结"/"结论"/"Summary"/"Conclusion" → 重命名为 "小结"

        Args:
            content: 章节内容 (含 ``##`` 章节标题 + ``###`` 子小节).

        Returns:
            清洗后的内容.
        """
        # 匹配 ### 引言 / ### 总结 / ### 结论 (含中英文)
        conflict_pattern = re.compile(
            r"^###\s+(引言|总结|结论|Introduction|Summary|Conclusion)\s*$",
            re.MULTILINE,
        )

        def _replace_subtitle(m: re.Match[str]) -> str:
            keyword = m.group(1)
            if keyword in ("引言", "Introduction"):
                # 引言: 移除子标题行 (内容保留, 引言只在报告开头)
                return ""
            # 总结/结论/Summary/Conclusion: 重命名为 "小结"
            return "### 小结"

        return conflict_pattern.sub(_replace_subtitle, content)

    @staticmethod
    def _strip_inline_references_block(content: str) -> str:
        """清洗章节末尾 LLM 偶尔生成的参考文献块.

        LLM 在 ``section_prompt``/``introduction_prompt`` 注入完整 references 后, 倾向于在章节末尾
        "复制" 出参考文献列表. 参考文献列表应由报告组装层 (``_format_sources``) 在报告末尾统一追加,
        章节内仅保留 ``[n]`` 行内编号引用.

        本方法匹配并移除以下 3 种格式:
        - 模式 1: ``**参考文献**`` / ``**References**`` / ``**参考来源**`` / ``**Bibliography**`` 粗体标题 + 后续所有内容
        - 模式 2: ``[^xxx]:`` 脚注定义块 (连续多行, 含文献标题/期刊/URL)
        - 模式 3: 无标题的 ``- \\`[n]\\` xxx`` 或 ``- [n] xxx`` 编号列表 (前置可选 ``---`` 分隔线, 含 URL/Retrieved from)

        Args:
            content: 章节内容 (独立章节, 不含其他章节).

        Returns:
            清洗后的内容, 末尾保留单个换行.
        """
        # 模式 1: (2+ 空行 + 可选 --- 分隔线 + 空行) + 粗体参考文献标题 + 后续所有内容到文末
        # 标题支持可选的括号注释 (如 "**参考文献（部分示例）**" / "**References (selected)**")
        pattern_bold = re.compile(
            r"\n{2,}(?:---\s*\n\s*)?"  # 前置空行 + 可选 --- 分隔线
            r"\*\*(?:参考文献|References|参考来源|Bibliography)"  # 粗体标题前缀
            r"[ \t]*(?:[（(][^）)]*[）)])?\*\*"  # 可选空格 + 可选括号注释 (如 "（部分示例）") + 粗体闭合
            r"\s*\n[\s\S]*$",  # 标题后续所有内容到文末 (贪婪)
            re.MULTILINE,
        )
        content = pattern_bold.sub("", content).rstrip() + "\n"

        # 模式 2: [^xxx]: 脚注定义块 (LLM 偶尔生成 Markdown 脚注定义, 含完整文献条目)
        # 匹配 2+ 空行后连续的 [^xxx]: 脚注定义行 (到文末)
        # 脚注定义格式: [^ref4]: 文献标题. 期刊名. [链接](url)
        pattern_footnote = re.compile(
            r"\n{2,}(?:\[\^[^\]]+\]:[^\n]*(?:\n|$))+\s*$",
            re.MULTILINE,
        )
        content = pattern_footnote.sub("", content).rstrip() + "\n"

        # 模式 3: 无标题的编号参考文献列表 (LLM 在章节末尾生成 `- `[n]` xxx` 格式)
        # 特征: 前置 2+ 空行 + 可选 --- 分隔线 + 连续的 `- `[n]` xxx` 或 `- [n] xxx` 列表项 (含 URL/Retrieved from)
        # 示例:
        #   ---
        #   - `[1]` Title. Retrieved from `https://...`
        #   - `[2]` Title. Retrieved from `https://...`
        #   - 【1】 Title. (中文方括号格式)
        # 正则说明: `?\[?\[?\d+\]?\]?`? 允许反引号/方括号包裹编号 (如 `[1]` / `[1]` / `1`)
        # 同时支持 `【n】` 中文方括号格式
        pattern_inline_list = re.compile(
            r"\n{2,}(?:---\s*\n\s*)?"  # 前置 2+ 空行 + 可选 --- 分隔线
            r"(?:-\s+`?(?:\[?\[?\d+\]?\]?|【\d+】)`?\s*[^\n]*(?:\n|$))+\s*$",  # 连续的列表项 (含 [n]/【n】) 到文末
            re.MULTILINE,
        )
        content = pattern_inline_list.sub("", content).rstrip() + "\n"

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
        language: str = "zh",
    ) -> str:
        """LLM 写子主题章节 (基于 sub_context + sources).

        优化:
        - prompt 提取到 PromptFamily.section_prompt
        - 章节字数 500-1000 → 800-1200
        - temperature: 0.4 → 0.35
        - 加 MUST 具体观点 + 表格 + [n] 编号引用

        LLM 调用增加 try/except + 1 次重试, 失败用占位文本.
        注入 report_style 风格预设.
        非 zh 时追加语言指令, 让 LLM 直接用目标语言生成章节.
        """
        # 注入风格描述
        style_desc = _REPORT_STYLE_DESCRIPTIONS.get(
            self.settings.report_style, _REPORT_STYLE_DESCRIPTIONS["academic"]
        )
        # prompt 经 PromptFamily.section_prompt 注入
        # 章节字数 500-1000 → 800-1200
        prompt = self._prompt_family.section_prompt(
            topic=topic,
            context=context,
            references=references,
            role_persona=role_persona,
            tone=tone,
            style_desc=style_desc,
            word_min=self.settings.detailed_section_word_min,
            word_max=self.settings.detailed_section_word_max,
        )
        # 末尾追加 Tone 语气提示词
        prompt += self._prompt_family.get_tone_prompt(tone)
        # 多语言报告生成 (非 zh 时追加语言指令, 让 LLM 直接用目标语言生成)
        lang_instruction = self._get_language_instruction(language)
        if lang_instruction:
            prompt += f"\n\n{lang_instruction}"
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        # temperature 0.4 → 0.35
        # 对标 GPTR: 报告章节正文用 smart_llm (gpt-4.1 / deepseek-v4-flash), 启用推理模式
        content = await self._achat_with_retry(
            messages,
            tier=LLMTier.SMART,
            temperature=0.35,
            max_tokens=self.settings.smart_token_limit,
            user_id=user_id,
            session_id=session_id,
            span_name="detailed-section",
            step="writer",
            fallback=_SECTION_FAILURE_PLACEHOLDER,
            reasoning_effort=self.settings.deep_research_reasoning_effort,
        )
        content = content.strip()
        # 清洗冲突子标题 (引言/总结/结论)
        content = self._sanitize_section_subtitles(content)
        # 清洗章节末尾 LLM 偶尔生成的 **参考文献** 粗体块 (参考文献由报告组装层统一追加)
        content = self._strip_inline_references_block(content)
        if not (content.startswith("## ") or content.startswith("### ")):
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
        language: str = "zh",
    ) -> str:
        """LLM 写结论 (基于 query + 已写章节摘要).

        优化:
        - prompt 提取到 PromptFamily.conclusion_prompt
        - temperature: 0.4 → 0.25

        LLM 调用增加 try/except + 1 次重试, 失败用占位文本.
        注入 report_style 风格预设.
        language 参数控制结论语言 (zh=中文, en=英文).
        """
        sections_summary = "\n\n".join(s[:500] for s in sections)
        # 注入风格描述
        style_desc = _REPORT_STYLE_DESCRIPTIONS.get(
            self.settings.report_style, _REPORT_STYLE_DESCRIPTIONS["academic"]
        )
        # prompt 经 PromptFamily.conclusion_prompt 注入
        prompt = self._prompt_family.conclusion_prompt(
            query=query,
            sections_summary=sections_summary,
            role_persona=role_persona,
            tone=tone,
            style_desc=style_desc,
            word_min=self.settings.detailed_intro_word_min,
            word_max=self.settings.detailed_intro_word_max,
        )
        # 多语言: 非 zh 时追加语言指令, 让 LLM 用目标语言撰写结论
        lang_instruction = self._get_language_instruction(language)
        if lang_instruction:
            prompt += f"\n\n{lang_instruction}"
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        # temperature 0.4 → 0.25
        # 对标 GPTR: 报告结论用 smart_llm (gpt-4.1 / deepseek-v4-flash), 启用推理模式
        content = await self._achat_with_retry(
            messages,
            tier=LLMTier.SMART,
            temperature=0.25,
            max_tokens=self.settings.smart_token_limit,
            user_id=user_id,
            session_id=session_id,
            span_name="detailed-conclusion",
            step="writer",
            fallback=_SECTION_FAILURE_PLACEHOLDER,
            reasoning_effort=self.settings.deep_research_reasoning_effort,
        )
        content = content.strip()
        # 清洗结论末尾 LLM 偶尔生成的 **参考文献** 粗体块 (参考文献由报告组装层统一追加)
        content = self._strip_inline_references_block(content)
        # 多语言: 结论标题前缀
        conclusion_heading = "## Conclusion" if language == "en" else "## 结论"
        if not content.startswith("## Conclusion") and not content.startswith("## 结论"):
            content = f"{conclusion_heading}\n\n" + content
        return content

    async def _translate_query_title(
        self,
        query: str,
        user_id: str | None,
        session_id: str | None,
    ) -> str:
        """翻译报告标题为英文 (language=en 时使用).

        用 FAST LLM 快速翻译, 失败时返回原始 query.
        """
        try:
            prompt = (
                f"Translate the following title to English. "
                f"Output ONLY the translated title, no explanations, no quotes:\n\n{query}"
            )
            messages = [{"role": "user", "content": prompt}]
            translated = await self._achat_with_retry(
                messages,
                tier=LLMTier.FAST,
                temperature=0.0,
                max_tokens=200,
                user_id=user_id,
                session_id=session_id,
                span_name="title-translation",
                step="translator",
                fallback=query,
            )
            return translated.strip().strip('"').strip("'") or query
        except Exception:  # noqa: BLE001
            return query

    async def _achat_with_retry(
        self,
        messages: list[dict[str, str]],
        *,
        tier: LLMTier,
        temperature: float,
        max_tokens: int,
        user_id: str | None,
        session_id: str | None,
        span_name: str,
        step: str,
        fallback: str,
        reasoning_effort: str | None = None,
    ) -> str:
        """LLM 调用通用重试封装.

        失败时重试 _LLM_RETRY_TIMES 次, 仍失败则返回 fallback 占位文本.
        用于章节/段落级 LLM 调用, 避免整篇报告因单点失败而重试.

        Args:
            messages: LLM 消息列表
            tier: LLM 层级 (FAST/SMART/STRATEGIC)
            temperature: 采样温度
            max_tokens: 最大 token 数
            user_id: 用户 ID
            session_id: 会话 ID
            span_name: trace span 名称
            step: 流程步骤标识
            fallback: 失败时的占位文本
            reasoning_effort: 推理强度 (仅 STRATEGIC tier 有效, None 时不添加)

        Returns:
            LLM 响应内容, 或失败时的 fallback 占位文本
        """
        total_attempts = _LLM_RETRY_TIMES + 1
        for attempt in range(total_attempts):
            try:
                response = await self._llm.achat(
                    messages,
                    tier=tier,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    reasoning_effort=reasoning_effort,
                    user_id=user_id,
                    session_id=session_id,
                    span_name=span_name,
                    step=step,
                )
                return response.content
            except Exception as e:  # noqa: BLE001
                is_last = attempt == total_attempts - 1
                if is_last:
                    logger.warning(
                        "LLM 调用最终失败 (span=%s, attempt=%d/%d), 使用占位文本: %s",
                        span_name,
                        attempt + 1,
                        total_attempts,
                        e,
                    )
                else:
                    logger.warning(
                        "LLM 调用失败 (span=%s, attempt=%d/%d), 重试中: %s",
                        span_name,
                        attempt + 1,
                        total_attempts,
                        e,
                    )
        return fallback

    @staticmethod
    def _slugify(text: str) -> str:
        """将标题文本转为 markdown 锚点 slug (detailed_report TOC).

        GitHub-flavored markdown 规则: 小写 ASCII, 空格转连字符,
        保留中文/字母/数字/连字符, 移除其余标点.
        """
        slug = text.strip().lower()
        # 空格转连字符
        slug = slug.replace(" ", "-")
        # 移除 markdown 锚点不支持的标点 (保留中文/字母/数字/连字符)
        slug = "".join(ch for ch in slug if ch.isalnum() or ch == "-" or "\u4e00" <= ch <= "\u9fff")
        # 合并连续连字符
        while "--" in slug:
            slug = slug.replace("--", "-")
        return slug.strip("-")

    @staticmethod
    def _generate_toc(subtopics: list[str], language: str = "zh") -> str:
        """生成目录 (TOC, 含锚点链接).

        每个目录项为可点击的锚点链接, 跳转到对应章节标题.
        language 参数控制目录标题语言 (zh=目录, en=Table of Contents).
        """
        if not subtopics:
            return ""
        toc_title = "Table of Contents" if language == "en" else "目录"
        lines = [f"## {toc_title}", ""]
        for i, topic in enumerate(subtopics, 1):
            anchor = ReportGenerator._slugify(topic)
            lines.append(f"{i}. [{topic}](#{anchor})")
        lines.append("")
        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    async def _generate_report_image(
        self,
        query: str,
        user_id: str | None,
        session_id: str | None,
        *,
        language: str = "zh",
    ) -> tuple[str | None, str | None, str | None]:
        """生成报告配图.

        图像生成失败时降级 (报告不带图, 记录 warning), 不阻断主流程.
        返回 (image_url, image_b64, image_svg), 失败均为 None.
        language 参数控制配图 prompt 和图片内文字语言 (zh=中文, en=英文).
        """
        try:
            if self._image_generator is None:
                self._image_generator = ImageGenerator(self.settings)

            # 配图提示词: 基于报告主题生成, 风格专业简洁 (根据语言切换)
            if language == "en":
                image_prompt = (
                    f'Generate a concept illustration for the research report on "{query}", '
                    f"professional and clean style, suitable for business research reports"
                )
            else:
                image_prompt = (
                    f"为「{query}」研究报告生成一张概念配图, 风格专业、简洁, 适合商业研究报告"
                )
            result = await self._image_generator.generate_image(
                image_prompt,
                size=self.settings.image_size,
                user_id=user_id,
                session_id=session_id,
                topic=query,  # 传入主题用于风格路由
                language=language,
            )
            return result.get("url"), result.get("b64"), result.get("svg")
        except Exception as e:  # noqa: BLE001
            # 图像生成失败降级: 报告不带图, 不阻断主流程
            logger.warning(
                "报告配图生成失败, 降级为不带图报告 (query=%s): %s",
                query[:100],
                e,
            )
            return None, None, None

    @staticmethod
    def _insert_image_into_report(
        report_md: str,
        image_url: str | None,
        image_b64: str | None,
        *,
        image_svg: str | None = None,
        language: str = "zh",
    ) -> str:
        """在报告 Markdown 中插入配图.

        策略: 在第一个 H1 标题后插入 (标题下方, 正文上方).
        支持三种格式:
        - image_svg: 嵌入 HTML <div> + 内联 <svg> (MD 渲染器原生支持内联 HTML)
        - image_url: ![报告配图/Report Image](url)
        - image_b64: ![报告配图/Report Image](data:image/png;base64,...)

        language 参数控制图片 alt 文本语言 (zh=报告配图, en=Report Image).
        SVG 预处理: 移除内部空行和 HTML 注释, 避免 CommonMark 渲染器
        (mistune/marked) 在空行处中断 HTML 块导致子元素被 <p> 包装.
        """
        # 图片 alt 文本多语言
        alt_text = "Report Image" if language == "en" else "报告配图"
        if image_svg:
            # SVG 预处理: 移除 HTML 注释和连续空行, 避免触发 CommonMark HTML 块中断
            # mistune/marked 在 HTML 块内遇到空行时会结束 HTML 块, 导致 SVG 子元素被 <p> 包装
            cleaned_svg = re.sub(r"<!--[\s\S]*?-->", "", image_svg)
            # 移除连续空行 (多个空行压缩为单个换行)
            cleaned_svg = re.sub(r"\n\s*\n", "\n", cleaned_svg)
            # 移除每行前导空白 (SVG 元素紧凑排列, 避免 mistune 将缩进行视为代码块)
            cleaned_svg = "\n".join(
                line.strip() for line in cleaned_svg.split("\n") if line.strip()
            )

            # SVG 模式: 嵌入 HTML <div> + 内联 <svg>
            # 不用 ```svg 代码块 (大多数 MD 渲染器不渲染, 只显示源代码)
            # 用内联 HTML, MD 渲染器 (GitHub/VSCode/Typora/mistune) 原生支持
            image_md = (
                f'\n\n<div class="report-image" aria-label="{alt_text}">\n{cleaned_svg}\n</div>\n\n'
            )
        elif image_url:
            image_md = f"\n\n![{alt_text}]({image_url})\n\n"
        elif image_b64:
            image_md = f"\n\n![{alt_text}](data:image/png;base64,{image_b64})\n\n"
        else:
            return report_md

        # 在第一个 H1 后插入
        lines = report_md.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("# "):
                return "\n".join(lines[: i + 1]) + image_md + "\n".join(lines[i + 1 :])

        # 无 H1, 在开头插入
        return image_md + "\n" + report_md

    @staticmethod
    def _basic_report_structure(language: str = "zh") -> str:
        """基础报告结构 (不再有行业预设维度, 由 LLM 自主决定).

        language 参数控制结构提示的章节标题语言 (zh=中文, 其他=英文),
        避免 structure_hint 注入 prompt 后 LLM 生成与目标语言不一致的标题.
        """
        if language == "en":
            return """# {Title}

## Abstract
(Briefly describe the research topic and core findings)

## Key Dimensions
(LLM autonomously extracts research dimensions based on context)

## Analysis and Insights
(In-depth analysis based on context)

## Conclusion and Outlook
(Summary and future trends)

## References
(APA format citation list)"""
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
    def _detailed_report_structure(language: str = "zh") -> str:
        """详细报告静态结构提示 (改用子主题嵌套动态生成, 此结构仅作参考).

        保留用于文档参考与潜在降级场景; _generate_detailed_report 不再使用此静态结构.
        language 参数控制结构提示的章节标题语言 (zh=中文, 其他=英文).
        """
        if language == "en":
            return """# {Title}

## Abstract
(Detail research background, purpose, methodology, and core findings)

## Industry Background
(Industry status, development history)

## In-depth Analysis
(LLM autonomously extracts research dimensions, detailed analysis by subsection)

## Competitive Landscape
(Key players, market share, competitive advantages)

## Trends and Outlook
(Short-term/medium-term/long-term trends)

## Risks and Challenges
(Major risk factors)

## Conclusion
(Core conclusions and recommendations)

## References
(APA format citation list)"""
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
    def _normalize_markdown(md: str) -> str:
        """规范化 Markdown 输出, 修复 LLM 常见格式问题.

        解决格式密集问题:
        - 末尾 rstrip 多余空行
        - 标题前后补空行 (确保标题与正文不紧贴)
        - 表格块前后补空行 (不在表格内部插入空行, 避免破坏渲染)
        - 列表块前后补空行 (不在列表项之间插入空行)
        - 连续 3+ 空行压缩为 2 个
        - 引用紧贴修复: [1][2] → [1] [2]
        - 引用块 (>) 前后补空行
        """
        import re

        if not md or not md.strip():
            return md

        # 1. 末尾 rstrip (去除多余空行), 保留单个换行
        md = md.rstrip() + "\n"

        # 2. 标题前后补空行 (确保标题前后有空行)
        # 匹配非空行后紧跟标题的情况
        md = re.sub(r"([^\n])\n(#{1,6}\s)", r"\1\n\n\2", md)
        # 匹配标题后紧跟非空行的情况
        md = re.sub(r"(#{1,6}[^\n]*)\n([^\n#])", r"\1\n\n\2", md)

        # 3. 表格块前后补空行 (关键: 不在表格内部插入空行)
        # 表格块定义: 连续 2+ 行 | 开头 (含 |---| 分隔行)
        # 前补空行: 非 | 行后紧跟 | 行, 且后面还有 | 行 (表明是表格块开头)
        md = re.sub(
            r"([^\n|])\n(\|[^\n]*\n\|)",
            r"\1\n\n\2",
            md,
        )
        # 后补空行: | 行 (且前一行也是 | 行) 后紧跟非 | 行 (表明是表格块结尾)
        md = re.sub(
            r"(\|[^\n]*\n\|[^\n]*)\n([^\n|])",
            r"\1\n\n\2",
            md,
        )

        # 4. 列表块前后补空行 (关键: 不在列表项之间插入空行)
        # 用回调函数判断前后行是否是列表项, 只在非列表项行相邻时补空行
        def _is_list_item(line: str) -> bool:
            """判断一行是否是列表项 (以 - / * / + / 数字. 开头)"""
            return bool(re.match(r"(?:[-*+]\s|\d+\.\s)", line))

        # 列表前补空行: 非列表项行 + \n + 列表项 + \n + 列表项
        def _list_prefix_pad(match: re.Match[str]) -> str:
            prefix_line = match.group(1)  # 前一行
            list_part = match.group(2)  # 列表项开头
            if _is_list_item(prefix_line):
                # 前一行也是列表项, 不补空行 (列表项之间)
                return match.group(0)
            return prefix_line + "\n\n" + list_part

        md = re.sub(
            r"([^\n]+)\n((?:[-*+]\s|\d+\.\s)[^\n]*\n(?:[-*+]\s|\d+\.\s))",
            _list_prefix_pad,
            md,
        )

        # 列表后补空行: 列表项 + \n + 列表项 + \n + 非列表项行
        def _list_suffix_pad(match: re.Match[str]) -> str:
            list_part = match.group(1)  # 列表项部分
            suffix_line = match.group(2)  # 后一行
            if _is_list_item(suffix_line):
                return match.group(0)
            return list_part + "\n\n" + suffix_line

        md = re.sub(
            r"((?:[-*+]\s|\d+\.\s)[^\n]*\n(?:[-*+]\s|\d+\.\s)[^\n]*)\n([^\n]+)",
            _list_suffix_pad,
            md,
        )

        # 列表项之间清理多余空行 (避免松散列表)
        # [-*] 行 + \n\n + [-*] 行 → [-*] 行 + \n + [-*] 行
        md = re.sub(
            r"((?:[-*+]\s)[^\n]*)\n\n((?:[-*+]\s))",
            r"\1\n\2",
            md,
        )
        md = re.sub(
            r"((?:\d+\.\s)[^\n]*)\n\n((?:\d+\.\s))",
            r"\1\n\2",
            md,
        )

        # 5. 引用块 (>) 前后补空行
        md = re.sub(r"([^\n])\n(>\s)", r"\1\n\n\2", md)
        md = re.sub(r"(>[^\n]*)\n([^\n>])", r"\1\n\n\2", md)

        # 6. 连续 3+ 空行压缩为 2 个
        md = re.sub(r"\n{3,}", "\n\n", md)

        # 7. 引用紧贴修复: [1][2] → [1] [2]
        md = re.sub(r"(\[\d+\])(\[\d+\])", r"\1 \2", md)

        return md

    @staticmethod
    def _build_references(sources: list[dict[str, Any]], language: str = "zh") -> str:
        """构建默认参考文献列表 (向后兼容, 等同 _format_citation_list(APA))."""
        return ReportGenerator._format_citation_list(sources, style="APA", language=language)

    @staticmethod
    def _format_citation_list(
        sources: list[dict[str, Any]],
        *,
        style: str = "APA",
        language: str = "zh",
    ) -> str:
        """按引用风格构建参考文献列表 (APA/MLA/Chicago/GB7714 可配置).

        在代码层实现真实格式化 (而非仅依赖 LLM 生成).

        Args:
            sources: 来源列表, 含 title/url/snippet 等字段.
            style: 引用风格 (APA/MLA/Chicago/GB7714), 默认 APA.
            language: 语言代码 (zh|en), 控制无来源/未知标题等占位文本语言.

        Returns:
            格式化后的参考文献字符串.
        """
        # 多语言占位文本
        no_sources_text = "(No sources available)" if language == "en" else "(无可用来源)"
        unknown_title = "Untitled" if language == "en" else "未知标题"
        if not sources:
            return no_sources_text

        refs: list[str] = []
        for i, src in enumerate(sources[:20], 1):  # 最多 20 条
            title = src.get("title", unknown_title)
            url = src.get("url", "")
            # 模拟作者/年份 (来源通常无作者字段, 用 hostname 占位)
            author = "Unknown"
            if url:
                try:
                    from urllib.parse import urlparse

                    author = urlparse(url).hostname or "Unknown"
                except Exception:  # noqa: BLE001
                    pass
            year = "2026"  # 检索时间默认当前年

            if style == "MLA":
                # MLA: Author. "Title." Website. URL.
                ref = f'[{i}] {author}. "{title}."'
                if url:
                    ref += f" {url}."
            elif style == "Chicago":
                # Chicago: Author. "Title." Accessed Date. URL.
                ref = f'[{i}] {author}. "{title}."'
                if url:
                    ref += f" Accessed 2026. {url}."
            elif style == "GB7714":
                # GB7714 (中文国标): [n] 作者. 题名[EB/OL]. (年)[2026-07-04]. URL.
                ref = f"[{i}] {author}. {title}[EB/OL]. ({year})[2026-07-04]."
                if url:
                    ref += f" {url}."
            else:
                # APA (默认): [n] Title. Retrieved from URL
                ref = f"[{i}] {title}."
                if url:
                    ref += f" Retrieved from {url}"
            refs.append(ref)
        return "\n\n".join(refs)

    def _format_sources(self, sources: list[dict[str, Any]], language: str | None = None) -> str:
        """格式化引用来源列表 (支持 APA/MLA/Chicago/GB7714 风格).

        通过 settings.report_format_style 配置风格, 默认 APA.
        与 _build_references 不同, 此方法生成带章节标题的完整来源列表,
        用于追加到报告末尾, 确保读者可访问原始来源.

        add_references 增强版:
        - 同类实现仅生成 `- [url](url)` 简单列表
        - 本方法支持 4 种引用风格, 含作者/年份/标题

        格式说明 (向后兼容):
        - 列表使用 `n. title. url` 格式 (非 [n] 前缀)
        - 详细风格字段由 _format_citation_list 在 "## 参考文献" 章节展示

        Args:
            sources: 引用来源列表.
            language: 语言代码 (zh|en), 优先级高于 settings.report_language.
        """
        if not sources:
            return ""
        # 读取配置风格 (settings.report_format_style 默认 "APA")
        # 对无 settings 的实例(如单元测试 fixture)安全降级到 "APA".
        settings = getattr(self, "settings", None)
        style = getattr(settings, "report_format_style", "APA") or "APA"
        # 章节标题 (中文报告用"参考来源", 英文报告用"References")
        # language 参数优先级 > settings.report_language
        lang = language or getattr(settings, "report_language", "zh") or "zh"
        section_title = "## 参考来源" if lang == "zh" else "## References"
        lines = [f"\n\n{section_title}\n\n"]
        # 多语言占位文本
        unknown_title = "Untitled" if lang == "en" else "未知标题"
        for i, src in enumerate(sources, 1):
            title = src.get("title", unknown_title)
            url = src.get("url", src.get("href", ""))
            # 在每条来源后追加风格标识 (便于客户端识别引用风格)
            lines.append(f"{i}. {title}. {url}  _[{style}]_")
        return "\n".join(lines)

    @staticmethod
    def _build_frontmatter(
        *,
        query: str,
        report_md: str,
        sources: list[dict[str, Any]],
        language: str = "zh",
    ) -> str:
        """构建 YAML frontmatter 元信息块.

        在报告首部追加元信息,
        便于下游解析器(如 static index.html)提取标题/日期/来源数等.

        Args:
            query: 原始查询.
            report_md: 报告正文 (用于统计字数).
            sources: 来源列表 (用于统计来源数).
            language: 报告语言.

        Returns:
            YAML frontmatter 字符串 (含结尾 '---\\n\\n' 分隔符).
        """
        from datetime import datetime

        word_count = len(report_md)
        sources_count = len(sources)
        # 转义查询中的特殊字符 (YAML 安全)
        safe_query = query.replace('"', '\\"').replace("\n", " ")[:200]
        date_str = datetime.now().strftime("%Y-%m-%d")

        return (
            "---\n"
            f'title: "{safe_query}"\n'
            f"date: {date_str}\n"
            f"language: {language}\n"
            f"word_count: {word_count}\n"
            f"sources_count: {sources_count}\n"
            f"generated_by: agentinsight-researcher\n"
            "---\n\n"
        )
