"""SourceCurator 来源策展师.

对标 GPT Researcher skills/curator.py.
AGENTS.md 用户需求 3: Reviewer (质量审查).

用 LLM 评估来源可信度与相关性, 过滤低质量来源.
cfg.CURATE_SOURCES=True 时启用 (默认 False).
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.settings import Settings, get_settings
from src.llm.client import LLMClient, LLMTier
from src.observability.tracing import trace_chain

logger = logging.getLogger(__name__)


class SourceCurator:
    """来源策展师 (Reviewer 职责).

    对标 GPT Researcher SourceCurator.
    用 smart_llm 评估来源可信度与相关性.
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

    async def curate_sources(
        self,
        query: str,
        sources: list[dict[str, Any]],
        *,
        max_results: int = 10,
        industry_prompt_family: dict[str, Any] | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """策展来源 (Reviewer 职责).

        对标 GPT Researcher curate_sources.
        用 smart_llm (temperature=0.2) 评估来源.
        """
        if not sources:
            return []

        async with trace_chain(
            name="source-curator",
            input={"query": query[:100], "sources_count": len(sources)},
            user_id=user_id,
            session_id=session_id,
        ) as span:
            industry_name = (
                industry_prompt_family.get("industry_name", "通用研究")
                if industry_prompt_family
                else "通用研究"
            )
            reviewer_prompt = (
                industry_prompt_family.get("reviewer_prompt", "") if industry_prompt_family else ""
            )

            # 截断避免 token 过大
            sources_text = "\n".join(
                f"[{i + 1}] {s.get('title', '')[:80]} | {s.get('url', '')[:100]} | {s.get('snippet', '')[:150]}"
                for i, s in enumerate(sources[:30])  # 最多 30 条
            )

            prompt = f"""你是一位{industry_name}行业的研究分析专家. {reviewer_prompt}

你的任务是: 评估以下搜索来源的相关性与可信度, 选出最值得引用的 {max_results} 条.

评估标准:
1. 相关性: 与研究问题的相关程度 (0-10 分)
2. 可信度: 来源权威性 (官方机构 > 学术期刊 > 行业媒体 > 自媒体)
3. 时效性: 信息新鲜度
4. 深度: 内容详实程度

研究问题: {query}

来源列表:
{sources_text}

请返回 JSON 数组, 每项含 index (1-based) 与 score (0-10):
[{{"index": 1, "score": 9, "reason": "官方权威数据"}}, ...]

仅返回最相关的 {max_results} 条的 JSON 数组:"""

            messages = [{"role": "user", "content": prompt}]
            response = await self._llm.achat(
                messages,
                tier=LLMTier.SMART,
                temperature=0.2,
                max_tokens=8000,
                user_id=user_id,
                session_id=session_id,
                span_name="curator-llm",
            )

            # 解析 JSON
            try:
                import json_repair

                scored = json_repair.loads(response.content)
                if isinstance(scored, list) and scored:
                    # 按 score 降序排序
                    scored.sort(key=lambda x: x.get("score", 0), reverse=True)

                    # 映射回原 sources
                    curated: list[dict[str, Any]] = []
                    for item in scored[:max_results]:
                        idx = item.get("index", 0) - 1
                        if 0 <= idx < len(sources):
                            source = sources[idx].copy()
                            source["curator_score"] = item.get("score", 0)
                            source["curator_reason"] = item.get("reason", "")
                            curated.append(source)

                    span.update(output={"curated_count": len(curated)})
                    return curated
            except Exception as e:  # noqa: BLE001
                logger.warning("来源策展解析失败, 返回原列表: %s", e)

            # 解析失败, 返回原列表前 max_results 条
            span.update(output={"curated_count": min(max_results, len(sources)), "fallback": True})
            return sources[:max_results]
