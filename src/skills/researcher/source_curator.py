"""SourceCurator 来源策展师.

Reviewer (质量审查).

用 LLM 评估来源可信度与相关性, 过滤低质量来源.
cfg.CURATE_SOURCES=True 时启用 (默认 False).

行业适配采用 4 层机制, 不再使用行业分类器:
- agent_role 参数 (AGENT_ROLE) 注入角色 persona, 由 LLM 动态生成或调用方注入

curator prompt 经 PromptFamily 策略注入 (支持中英多语言切换).
"""

from __future__ import annotations

import logging
from typing import Any

from src.common.json_utils import safe_json_parse
from src.config.settings import Settings, get_settings
from src.llm.client import LLMClient, LLMTier, get_llm_client
from src.observability.tracing import trace_chain
from src.skills.researcher.prompts import PromptFamily, get_prompt_family

logger = logging.getLogger(__name__)


class SourceCurator:
    """来源策展师 (Reviewer 职责).

    用 smart_llm 评估来源可信度与相关性.

    新增域名可信度字典 + _score_credibility 方法,
    与 LLM 相关性分数综合排序 (相关性 * 0.6 + 可信度 * 0.4).
    """

    settings: Settings
    _llm: LLMClient
    _prompt_family: PromptFamily

    # 权威域名可信度字典
    _DOMAIN_CREDIBILITY: dict[str, float] = {
        # 学术
        "arxiv.org": 0.95,
        "pubmed.ncbi.nlm.nih.gov": 0.95,
        "scholar.google.com": 0.90,
        "nature.com": 0.95,
        "science.org": 0.95,
        "ieee.org": 0.90,
        # 政府
        "gov": 0.90,
        "gov.cn": 0.90,
        "stats.gov.cn": 0.95,
        # 主流媒体
        "reuters.com": 0.85,
        "bbc.com": 0.85,
        "nytimes.com": 0.85,
        # 百科
        "wikipedia.org": 0.70,
        "baike.baidu.com": 0.65,
    }

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        prompt_family: PromptFamily | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or get_llm_client()
        self._prompt_family = prompt_family or get_prompt_family(self.settings.prompt_family)

    def _score_credibility(self, source: dict[str, Any]) -> float:
        """计算来源可信度 (0-1).

        优化:
        - 旧版: 含统计数据仅 +0.03 微弱加分
        - V2: 将 Quantitative Value 提升为独立维度, 含统计指标 (百分比/金额/CAGR) 显著加分 (+0.10)

        综合域名权威性 + 内容长度 + 数据丰富度 (Quantitative Value).
        域名可信度评估.

        Args:
            source: 来源 dict, 含 url/content/body/snippet 等字段

        Returns:
            可信度分数 0.0-1.0
        """
        url = source.get("url", source.get("href", ""))
        score = 0.5  # 基础分
        # 域名权威性 (取所有匹配域名的最高分, 避免顺序依赖)
        for domain, cred in self._DOMAIN_CREDIBILITY.items():
            if domain in url:
                score = max(score, cred)
        # 内容长度 (长内容通常更可信)
        content = source.get("content", source.get("body", source.get("snippet", "")))
        if len(content) > 2000:
            score += 0.05
        elif len(content) < 200:
            score -= 0.10

        # Quantitative Value 评估
        # "Quantitative Value" 强调 5 次, 含统计数据的来源优先级显著高于纯文字描述.
        quant_score = self._score_quantitative_value(content)
        score += quant_score

        return max(0.0, min(1.0, score))

    @staticmethod
    def _score_quantitative_value(content: str) -> float:
        """评估内容的数据丰富度 (Quantitative Value).

        SourceCurator 的第 5 维评估标准, 含具体数字/百分比/金额/统计指标
        的来源优先级显著高于纯文字描述. prompt 中 "Quantitative Value" 出现 5 次.

        Args:
            content: 来源内容文本

        Returns:
            数据丰富度加分 (0.0 - 0.15)
        """
        if not content:
            return 0.0

        # 检查前 1000 字符内的统计指标
        text = content[:1000]
        bonus = 0.0

        # 1. 百分比 (如 "18.5%", "增长 20%")
        if "%" in text:
            bonus += 0.04

        # 2. 金额 (如 "$1.2T", "1.2 万亿", "100亿美元")
        money_patterns = ["$", "¥", "万", "亿", "trillion", "billion", "million"]
        if any(p in text.lower() for p in money_patterns):
            bonus += 0.04

        # 3. CAGR / 增长率 / 同比 / 环比 (统计学指标)
        growth_patterns = ["CAGR", "cagr", "增长率", "同比", "环比", "复合增长", "年增长"]
        if any(p in text for p in growth_patterns):
            bonus += 0.03

        # 4. 数字密度 (每 100 字符含数字个数, 高密度 → 数据丰富)
        digit_count = sum(1 for c in text if c.isdigit())
        density = digit_count / max(1, len(text) / 100)
        if density >= 5:  # 每 100 字符 ≥ 5 个数字
            bonus += 0.04

        return min(0.15, bonus)

    async def curate_sources(
        self,
        query: str,
        sources: list[dict[str, Any]],
        *,
        max_results: int = 10,
        agent_role: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """策展来源 (Reviewer 职责).

        用 smart_llm (temperature=0.2) 评估来源.
        agent_role (AGENT_ROLE): 角色 persona 字符串,
        由 AgentCreator LLM 动态生成或调用方注入.
        """
        if not sources:
            return []

        async with trace_chain(
            name="source-curator",
            input={"query": query[:100], "sources_count": len(sources)},
            user_id=user_id,
            session_id=session_id,
        ) as span:
            # agent_role 作为角色 persona
            role_persona = agent_role or "你是一位资深研究分析专家, 擅长多领域综合研究."

            # LLM 前用 credibility 预过滤, 减少输入 token (30 条 → 最多 20 条)
            filtered_sources = [s for s in sources if self._score_credibility(s) >= 0.5]
            if not filtered_sources:
                filtered_sources = sources  # 全部低于阈值时保留原列表
            sources_text = "\n".join(
                f"[{i + 1}] {s.get('title', '')[:80]} | {s.get('url', '')[:100]} | {s.get('snippet', '')[:150]}"
                for i, s in enumerate(filtered_sources[:20])  # 30→20
            )

            # prompt 经 PromptFamily 策略注入
            prompt = self._prompt_family.curator_prompt(
                query=query,
                sources_text=sources_text,
                agent_role=role_persona,
                max_results=max_results,
            )

            messages = [{"role": "user", "content": prompt}]
            response = await self._llm.achat(
                messages,
                tier=LLMTier.SMART,
                temperature=0.2,
                max_tokens=2000,  # 4000→2000 (策展 JSON 仅需 index+score, 不需要长输出)
                user_id=user_id,
                session_id=session_id,
                span_name="curator-llm",
                step="curator",
            )

            # 解析 JSON
            try:
                scored = safe_json_parse(response.content, fallback=[])
                if isinstance(scored, list) and scored:
                    # 映射回 filtered_sources 并计算可信度 (索引基于预过滤后列表)
                    curated: list[dict[str, Any]] = []
                    for item in scored:
                        idx = item.get("index", 0) - 1
                        if 0 <= idx < len(filtered_sources):  # 用 filtered_sources
                            source = filtered_sources[idx].copy()
                            curator_score = item.get("score", 0)
                            source["curator_score"] = curator_score
                            source["curator_reason"] = item.get("reason", "")
                            # 可信度评分 + 综合排序
                            credibility = self._score_credibility(source)
                            source["credibility_score"] = round(credibility, 4)
                            # 相关性归一化 (LLM score 0-10 → 0-1)
                            relevance = max(0.0, min(1.0, curator_score / 10.0))
                            combined = relevance * 0.6 + credibility * 0.4
                            source["combined_score"] = round(combined, 4)
                            curated.append(source)

                    # 按 (相关性 * 0.6 + 可信度 * 0.4) 综合排序
                    curated.sort(key=lambda x: x.get("combined_score", 0.0), reverse=True)
                    curated = curated[:max_results]

                    span.update(output={"curated_count": len(curated)})
                    return curated
            except Exception as e:  # noqa: BLE001
                logger.warning("来源策展解析失败, 返回原列表: %s", e)

            # 解析失败, 按可信度排序返回前 max_results 条
            fallback_scored: list[dict[str, Any]] = []
            for s in sources:
                s_copy = s.copy()
                credibility = self._score_credibility(s_copy)
                s_copy["credibility_score"] = round(credibility, 4)
                s_copy["combined_score"] = round(credibility, 4)
                fallback_scored.append(s_copy)
            fallback_scored.sort(key=lambda x: x.get("combined_score", 0.0), reverse=True)
            span.update(
                output={
                    "curated_count": min(max_results, len(fallback_scored)),
                    "fallback": True,
                }
            )
            return fallback_scored[:max_results]
