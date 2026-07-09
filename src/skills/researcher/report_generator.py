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

import asyncio
import logging
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

# 兜底角色 persona (对标 GPTR 默认 researcher role)
_DEFAULT_AGENT_ROLE = "你是一位资深研究分析专家, 擅长多领域综合研究."

# V4-P1-03: 章节/段落 LLM 调用失败后的占位文本
_SECTION_FAILURE_PLACEHOLDER = "[此章节生成失败, 请重试]"

# V4-P1-03: LLM 调用单章节重试次数 (失败后再试 1 次)
_LLM_RETRY_TIMES = 1

# P1-1: 短报告 FAST tier 阈值 — word_limit <= 此值时优先用 FAST tier (低延迟),
# 失败回退 SMART tier. 默认 2000 字 (FAST tier fast_token_limit=3000 足以覆盖).
_FAST_TIER_WORD_THRESHOLD: int = 2000

# V4-P2-01: 报告风格预设描述 (用于 detailed_report 内联 prompt 注入,
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

    对标 GPT Researcher ReportGenerator.
    用 smart_llm 合成长报告.
    P2-06: image_generation_enabled=True 时调用 ImageGenerator 生成配图.
    P0-Future-04: detailed_report 实现子主题嵌套研究 (对标 GPTR detailed_report).
    """

    settings: Settings
    _llm: LLMClient
    _image_generator: ImageGenerator | None
    _prompt_family: PromptFamily

    # P2-05: 多语言报告生成指令 (5 种语言, 中文默认无需额外指令)
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
        """获取语言指令 (P2-05 多语言).

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

        - basic_report: 单次 LLM 合成 (对标 GPT Researcher generate_report)
        - detailed_report: 子主题嵌套研究 + TOC 拼接 (对标 GPTR detailed_report)

        agent_role (对标 GPTR AGENT_ROLE): 角色 persona 字符串,
        由 AgentCreator LLM 动态生成或调用方注入, 优先级高于默认角色.

        language (P2-05): 报告语言代码 (zh|en|ja|ko|fr), 默认 zh 中文;
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
            # 空上下文拒绝生成守卫 (对标 GPTR writer.py:82-88, 防幻觉)
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

            # 构建来源引用列表 (APA 格式)
            references = self._build_references(sources)

            # 对标 GPTR: agent_role 作为角色 persona (来自 LLM 动态生成或调用方注入)
            role_persona = agent_role or _DEFAULT_AGENT_ROLE

            word_limit = total_words or self.settings.total_words
            current_date = datetime.now().strftime("%Y年%m月%d日")

            structure_hint = self._basic_report_structure()

            # P1-Future-04: prompt 经 PromptFamily 策略注入
            # V4-P2-01: 注入 report_style 风格预设
            # V4-P2-02: 末尾追加 Tone 语气提示词 (对标 GPTR 17 种 Tone)
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

            # P2-05: 多语言报告生成 (非 zh 时追加语言指令, 让 LLM 直接用目标语言生成)
            lang_instruction = self._get_language_instruction(language)
            if lang_instruction:
                prompt += f"\n\n{lang_instruction}"

            messages = [{"role": "user", "content": prompt}]
            # P1-1: 短报告 (word_limit <= _FAST_TIER_WORD_THRESHOLD) 优先用 FAST tier
            # (低延迟), 失败回退 SMART tier; 长报告直接用 SMART tier (支持 2k+ 字长响应).
            # V4-P1-03: LLM 调用增加 try/except + 1 次重试, 仍失败用占位文本
            use_fast_tier = word_limit <= _FAST_TIER_WORD_THRESHOLD
            if use_fast_tier:
                report_md = await self._achat_with_retry(
                    messages,
                    tier=LLMTier.FAST,
                    temperature=0.4,
                    max_tokens=self.settings.fast_token_limit,
                    user_id=user_id,
                    session_id=session_id,
                    span_name="writer-llm-fast",
                    step="writer",
                    fallback=_SECTION_FAILURE_PLACEHOLDER,
                )
                # P1-1: FAST tier 失败 (返回占位文本) 时回退 SMART tier
                if report_md == _SECTION_FAILURE_PLACEHOLDER:
                    logger.info(
                        "P1-1: FAST tier 失败, 回退 SMART tier (word_limit=%d)",
                        word_limit,
                    )
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
                    )
            else:
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
                report_md += self._format_sources(sources)
            else:
                # LLM 已生成参考文献章节, 仅补充来源 URL 列表 (无章节标题, 避免重复)
                # V4-P2-02: 追加引用来源列表 (对标 GPTR APA 格式)
                pass  # 不再追加 _format_sources, 避免双重参考章节

            # P2-05: YAML frontmatter (enable_frontmatter=True 时在报告首部追加元信息块)
            # 对标 GPTR cli.py 的 YAML 输出, 便于下游解析/索引.
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
        language: str = "zh",
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

        V4-P0-02: 子主题并行化, 用 asyncio.gather 并行处理所有子主题
                  (每个子主题独立 research + write, 互不依赖).
        V4-P1-03: 单个子主题 LLM 调用失败时重试 1 次, 仍失败用占位文本, 不阻断整体.
        V4-P2-01: 注入 report_style 风格预设.

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
            # 空上下文拒绝生成守卫 (对标 GPTR writer.py:82-88, 防幻觉)
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

            references = self._build_references(sources)
            role_persona = agent_role or _DEFAULT_AGENT_ROLE

            # 步骤 2+3: P1-2 子主题生成与引言并行 (两者均只依赖 query/contexts/
            # references/role_persona, 无数据依赖, 并行可节省引言 LLM 调用延迟 ~2-3s)
            subtopics, introduction = await asyncio.gather(
                self._generate_subtopics(
                    query,
                    combined_context,
                    role_persona=role_persona,
                    user_id=user_id,
                    session_id=session_id,
                ),
                self._write_introduction(
                    query,
                    combined_context,
                    references,
                    role_persona=role_persona,
                    tone=tone,
                    user_id=user_id,
                    session_id=session_id,
                ),
            )

            # 步骤 4: V4-P0-02 子主题并行嵌套研究 + 去重 + 写章节
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
            sections: list[str] = []
            all_sources: list[dict[str, Any]] = list(sources)
            skipped_count = 0
            for section_md, sub_sources, skipped in section_results:
                if skipped:
                    skipped_count += 1
                if sub_sources:
                    all_sources.extend(sub_sources)
                if section_md:
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

            # 重新构建参考文献 (含子主题研究新增的源); _format_sources 已含 ## 参考来源 章节, 此处无需重复
            current_date = datetime.now().strftime("%Y年%m月%d日")

            body = "\n\n".join(sections) if sections else "_(无子主题章节内容)_"
            full_report = (
                f"# {query}\n\n"
                f"_生成日期: {current_date}_\n\n"
                f"{toc}\n\n"
                f"{introduction}\n\n"
                f"{body}\n\n"
                f"{conclusion}\n\n"
            )

            # V4-P2-02: 追加引用来源列表 (对标 GPTR APA 格式, 含子主题研究新增源)
            # 避免双重参考章节: 仅追加一次 _format_sources (含 ## 参考来源 章节标题)
            full_report += self._format_sources(all_sources)

            # 规范化 Markdown 输出 (修复 LLM 常见格式问题: 段落紧贴、表格无空行、引用紧贴等)
            full_report = self._normalize_markdown(full_report)

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
        """V4-P0-02: 单个子主题的 research + write 独立函数 (并行单元).

        每个子主题独立调用 research + write, 互不依赖:
        1. ResearchConductor.conduct_research(sub_query, mode="basic")
        2. WrittenContentCompressor 去重 (锁保护, 避免并行竞态)
        3. _write_section 写章节 (V4-P1-03: 内部已含 try/except + 重试)

        异常处理: 单个子主题失败不阻断整体, 返回占位文本 (V4-P1-03).

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
            language: P2-05 报告语言代码 (zh|en|ja|ko|fr), 默认 zh

        Returns:
            (section_md, sub_sources, skipped):
            - section_md: 章节 Markdown, 失败时为占位文本; 跳过时为 None
            - sub_sources: 子主题研究新增的来源列表
            - skipped: 是否因去重被跳过
        """
        sub_query = f"{query} - {topic}"
        # P0-2: 复用主研究 contexts, 仅在上下文不足时触发嵌套搜索 (消除 5 倍搜索冗余)
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

        # 复用主研究上下文 (优先), 不足时用嵌套搜索结果补充
        if sub_contexts:
            sub_context = "\n\n---\n\n".join(sub_contexts)
        elif combined_context:
            sub_context = combined_context
        else:
            sub_context = ""
        if len(sub_context) > max_context_chars:
            sub_context = sub_context[:max_context_chars]

        # 2. WrittenContentCompressor 去重 (P4 修复: 缩小 dedup_lock 锁粒度)
        # 锁外: compute_embedding 完成网络 I/O (embed_texts), 不持锁保持并行度
        # 锁内: check_and_update 做 numpy 相似度比对 + 更新内部状态 (同步操作)
        # 旧版将 should_keep 整体放锁内, embedding 网络调用使并行退化为串行
        try:
            chunks, content_embs = await written_compressor.compute_embedding(sub_context)
            async with dedup_lock:
                keep = written_compressor.check_and_update(chunks, content_embs)
        except Exception as e:  # noqa: BLE001
            logger.warning("子主题 '%s' 去重检查失败, 保留内容: %s", topic, e)
            keep = True

        if not keep:
            logger.info("子主题 '%s' 内容与已写章节高度相似, 跳过", topic)
            return None, sub_sources, True

        # 3. 写子主题章节 (V4-P1-03: _write_section 内部已含 try/except + 重试)
        section_md = await self._write_section(
            topic,
            sub_context,
            references,
            role_persona=role_persona,
            tone=tone,
            user_id=user_id,
            session_id=session_id,
            language=language,
        )
        return section_md, sub_sources, False

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

        V2-P1 优化 (对标 GPTR detailed_report.py):
        - prompt 提取到 PromptFamily.subtopics_prompt (旧版内联)
        - temperature: 0.4 → 0.25 (对标 GPTR draft_titles temp)
        - 用 STRATEGIC LLM 拆解 (与 GPTR 一致)

        用 safe_json_parse 解析 LLM 输出的 JSON 数组.
        V4-P1-03: LLM 调用增加 try/except + 1 次重试, 失败降级为 [query].
        """
        # V2-P1: prompt 经 PromptFamily.subtopics_prompt 注入 (旧版内联)
        prompt = self._prompt_family.subtopics_prompt(
            query=query,
            context=context,
            role_persona=role_persona,
            max_subtopics=self.settings.max_subtopics,
        )
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        # V4-P1-03: LLM 调用增加重试, 失败降级为 [query]
        # V2-P1: temperature 0.4 → 0.25 (对标 GPTR draft_titles temp)
        # P1-7: 子主题列表生成是短 JSON 数组任务, SMART 足够, 省 2/3 成本
        content = await self._achat_with_retry(
            messages,
            tier=LLMTier.SMART,
            temperature=0.25,
            max_tokens=800,
            user_id=user_id,
            session_id=session_id,
            span_name="detailed-subtopics",
            step="planner",
            fallback=f'["{query}"]',
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
    ) -> str:
        """LLM 写引言 (基于 query + contexts).

        V2-P1 优化 (对标 GPTR detailed_report.py):
        - prompt 提取到 PromptFamily.introduction_prompt (旧版内联)
        - temperature: 0.4 → 0.25 (对标 GPTR write_introduction temp)

        V4-P1-03: LLM 调用增加 try/except + 1 次重试, 失败用占位文本.
        V4-P2-01: 注入 report_style 风格预设.
        """
        current_date = datetime.now().strftime("%Y年%m月%d日")
        # V4-P2-01: 注入风格描述
        style_desc = _REPORT_STYLE_DESCRIPTIONS.get(
            self.settings.report_style, _REPORT_STYLE_DESCRIPTIONS["academic"]
        )
        # V2-P1: prompt 经 PromptFamily.introduction_prompt 注入 (旧版内联)
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
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        # V2-P1: temperature 0.4 → 0.25 (对标 GPTR write_introduction temp)
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
        )
        content = content.strip()
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
        language: str = "zh",
    ) -> str:
        """LLM 写子主题章节 (基于 sub_context + sources).

        V2-P1 优化 (对标 GPTR detailed_report.py):
        - prompt 提取到 PromptFamily.section_prompt (旧版内联)
        - 章节字数 500-1000 → 800-1200 (对标 GPTR write_section 字数)
        - temperature: 0.4 → 0.35 (对标 GPTR write_section temp)
        - 加 MUST 具体观点 + 表格 + [n] 编号引用 (对标 GPTR writer_prompt)

        V4-P1-03: LLM 调用增加 try/except + 1 次重试, 失败用占位文本.
        V4-P2-01: 注入 report_style 风格预设.
        P2-05: 非 zh 时追加语言指令, 让 LLM 直接用目标语言生成章节.
        """
        # V4-P2-01: 注入风格描述
        style_desc = _REPORT_STYLE_DESCRIPTIONS.get(
            self.settings.report_style, _REPORT_STYLE_DESCRIPTIONS["academic"]
        )
        # V2-P1: prompt 经 PromptFamily.section_prompt 注入 (旧版内联)
        # 章节字数 500-1000 → 800-1200 (对标 GPTR)
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
        # V4-P2-02: 末尾追加 Tone 语气提示词 (对标 GPTR 17 种 Tone)
        prompt += self._prompt_family.get_tone_prompt(tone)
        # P2-05: 多语言报告生成 (非 zh 时追加语言指令, 让 LLM 直接用目标语言生成)
        lang_instruction = self._get_language_instruction(language)
        if lang_instruction:
            prompt += f"\n\n{lang_instruction}"
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        # V2-P1: temperature 0.4 → 0.35 (对标 GPTR write_section temp)
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
        )
        content = content.strip()
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
    ) -> str:
        """LLM 写结论 (基于 query + 已写章节摘要).

        V2-P1 优化 (对标 GPTR detailed_report.py):
        - prompt 提取到 PromptFamily.conclusion_prompt (旧版内联)
        - temperature: 0.4 → 0.25 (对标 GPTR write_conclusion temp)

        V4-P1-03: LLM 调用增加 try/except + 1 次重试, 失败用占位文本.
        V4-P2-01: 注入 report_style 风格预设.
        """
        sections_summary = "\n\n".join(s[:500] for s in sections)
        # V4-P2-01: 注入风格描述
        style_desc = _REPORT_STYLE_DESCRIPTIONS.get(
            self.settings.report_style, _REPORT_STYLE_DESCRIPTIONS["academic"]
        )
        # V2-P1: prompt 经 PromptFamily.conclusion_prompt 注入 (旧版内联)
        prompt = self._prompt_family.conclusion_prompt(
            query=query,
            sections_summary=sections_summary,
            role_persona=role_persona,
            tone=tone,
            style_desc=style_desc,
            word_min=self.settings.detailed_intro_word_min,
            word_max=self.settings.detailed_intro_word_max,
        )
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        # V2-P1: temperature 0.4 → 0.25 (对标 GPTR write_conclusion temp)
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
        )
        content = content.strip()
        if not content.startswith("## 结论"):
            content = "## 结论\n\n" + content
        return content

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
    ) -> str:
        """V4-P1-03: LLM 调用通用重试封装.

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
        """将标题文本转为 markdown 锚点 slug (对标 GPTR detailed_report TOC).

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
    def _generate_toc(subtopics: list[str]) -> str:
        """生成目录 (对标 GPTR TOC, 含锚点链接).

        每个目录项为可点击的锚点链接, 跳转到对应章节标题.
        """
        if not subtopics:
            return ""
        lines = ["## 目录", ""]
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

        image_md = f"\n\n![报告配图]({image_ref})\n\n"

        # 在第一个 H1 后插入
        lines = report_md.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("# "):
                return "\n".join(lines[: i + 1]) + image_md + "\n".join(lines[i + 1 :])

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
    def _build_references(sources: list[dict[str, Any]]) -> str:
        """构建默认参考文献列表 (向后兼容, 等同 _format_citation_list(APA))."""
        return ReportGenerator._format_citation_list(sources, style="APA")

    @staticmethod
    def _format_citation_list(
        sources: list[dict[str, Any]],
        *,
        style: str = "APA",
    ) -> str:
        """按引用风格构建参考文献列表 (P1-02: APA/MLA/Chicago/GB7714 可配置).

        对标 GPTR prompts.py reference_prompt 的 report_format 字符串注入,
        但在代码层实现真实格式化 (GPTR 仅依赖 LLM 生成).

        Args:
            sources: 来源列表, 含 title/url/snippet 等字段.
            style: 引用风格 (APA/MLA/Chicago/GB7714), 默认 APA.

        Returns:
            格式化后的参考文献字符串.
        """
        if not sources:
            return "(无可用来源)"

        refs: list[str] = []
        for i, src in enumerate(sources[:20], 1):  # 最多 20 条
            title = src.get("title", "未知标题")
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

    def _format_sources(self, sources: list[dict[str, Any]]) -> str:
        """格式化引用来源列表 (P1-02: 支持 APA/MLA/Chicago/GB7714 风格).

        通过 settings.report_format_style 配置风格, 默认 APA.
        与 _build_references 不同, 此方法生成带章节标题的完整来源列表,
        用于追加到报告末尾, 确保读者可访问原始来源.

        对标 GPTR add_references (markdown_processing.py:94) 但功能更强:
        - GPTR 仅生成 `- [url](url)` 简单列表
        - 本方法支持 4 种引用风格, 含作者/年份/标题

        格式说明 (向后兼容):
        - 列表使用 `n. title. url` 格式 (非 [n] 前缀)
        - 详细风格字段由 _format_citation_list 在 "## 参考文献" 章节展示
        """
        if not sources:
            return ""
        # P1-02: 读取配置风格 (settings.report_format_style 默认 "APA")
        # 对无 settings 的实例(如单元测试 fixture)安全降级到 "APA".
        settings = getattr(self, "settings", None)
        style = getattr(settings, "report_format_style", "APA") or "APA"
        # 章节标题 (中文报告用"参考来源", 英文报告用"References")
        lang = getattr(settings, "report_language", "zh") or "zh"
        section_title = "## 参考来源" if lang == "zh" else "## References"
        lines = [f"\n\n{section_title}\n\n"]
        for i, src in enumerate(sources, 1):
            title = src.get("title", "未知标题")
            url = src.get("url", src.get("href", ""))
            # P1-02: 在每条来源后追加风格标识 (便于客户端识别引用风格)
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
        """构建 YAML frontmatter 元信息块 (P2-05).

        对标 GPTR cli.py 的 YAML 输出, 在报告首部追加元信息,
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
