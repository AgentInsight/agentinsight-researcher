"""SourceCurator 来源策展师.

对标 GPT Researcher skills/curator.py.
AGENTS.md 用户需求 3: Reviewer (质量审查).

用 LLM 评估来源可信度与相关性, 过滤低质量来源.
cfg.CURATE_SOURCES=True 时启用 (默认 False).

行业适配采用 GPTR 风格 4 层机制, 不再使用行业分类器:
- agent_role 参数 (对标 GPTR AGENT_ROLE) 注入角色 persona, 由 LLM 动态生成或调用方注入

P1-Future-04: curator prompt 经 PromptFamily 策略注入 (支持中英多语言切换).
"""

from __future__ import annotations

import logging
from typing import Any

from src.common.json_utils import safe_json_parse
from src.config.settings import Settings, get_settings
from src.llm.client import LLMClient, LLMTier
from src.observability.tracing import trace_chain
from src.skills.researcher.prompts import PromptFamily, get_prompt_family

logger = logging.getLogger(__name__)


class SourceCurator:
    """来源策展师 (Reviewer 职责).

    对标 GPT Researcher SourceCurator.
    用 smart_llm 评估来源可信度与相关性.

    P2-02: 新增域名可信度字典 + _score_credibility 方法,
    与 LLM 相关性分数综合排序 (相关性 * 0.6 + 可信度 * 0.4).
    """

    settings: Settings
    _llm: LLMClient
    _prompt_family: PromptFamily

    # P2-02: 权威域名可信度字典 (对标 GPTR curate_sources)
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
        self._llm = llm or LLMClient(self.settings)
        self._prompt_family = prompt_family or get_prompt_family(self.settings.prompt_family)

    def _score_credibility(self, source: dict[str, Any]) -> float:
        """计算来源可信度 (0-1, P2-02).

        综合域名权威性 + 内容长度 + 是否含统计数据.
        对标 GPTR curate_sources 域名可信度评估.

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
        # 含统计数据 (前 500 字符含数字)
        if any(c.isdigit() for c in content[:500]):
            score += 0.03
        return max(0.0, min(1.0, score))

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

        对标 GPT Researcher curate_sources.
        用 smart_llm (temperature=0.2) 评估来源.
        agent_role (对标 GPTR AGENT_ROLE): 角色 persona 字符串,
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
            # 对标 GPTR: agent_role 作为角色 persona
            role_persona = agent_role or "你是一位资深研究分析专家, 擅长多领域综合研究."

            # 截断避免 token 过大
            sources_text = "\n".join(
                f"[{i + 1}] {s.get('title', '')[:80]} | {s.get('url', '')[:100]} | {s.get('snippet', '')[:150]}"
                for i, s in enumerate(sources[:30])  # 最多 30 条
            )

            # P1-Future-04: prompt 经 PromptFamily 策略注入
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
                max_tokens=8000,
                user_id=user_id,
                session_id=session_id,
                span_name="curator-llm",
                step="curator",
            )

            # 解析 JSON
            try:
                scored = safe_json_parse(response.content, fallback=[])
                if isinstance(scored, list) and scored:
                    # 映射回原 sources 并计算可信度
                    curated: list[dict[str, Any]] = []
                    for item in scored:
                        idx = item.get("index", 0) - 1
                        if 0 <= idx < len(sources):
                            source = sources[idx].copy()
                            curator_score = item.get("score", 0)
                            source["curator_score"] = curator_score
                            source["curator_reason"] = item.get("reason", "")
                            # P2-02: 可信度评分 + 综合排序
                            credibility = self._score_credibility(source)
                            source["credibility_score"] = round(credibility, 4)
                            # 相关性归一化 (LLM score 0-10 → 0-1)
                            relevance = max(0.0, min(1.0, curator_score / 10.0))
                            combined = relevance * 0.6 + credibility * 0.4
                            source["combined_score"] = round(combined, 4)
                            curated.append(source)

                    # P2-02: 按 (相关性 * 0.6 + 可信度 * 0.4) 综合排序
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
