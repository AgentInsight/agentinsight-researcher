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
    """

    settings: Settings
    _llm: LLMClient
    _prompt_family: PromptFamily

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        prompt_family: PromptFamily | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or LLMClient(self.settings)
        self._prompt_family = prompt_family or get_prompt_family(self.settings.prompt_family)

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
