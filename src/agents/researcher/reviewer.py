"""Reviewer 报告评审 Agent (多维度评分).

AGENTS.md 第 5 章: LangGraph StateGraph 唯一编排, 节点纯函数.

Reviewer 职责:
- 评审报告质量, 按 4 维度打分 (事实性/结构性/语言性/完整性)
- 返回 review_decision ("accept"|"revise") + review_feedback + review_scores
- 用 LLMClient tier=SMART 调用 (评分用 SMART 层)
- 用 safe_json_parse 解析 LLM 返回的 JSON
- 用 trace_chain 包裹 (AGENTS.md 第 10 章, 禁 agentinsight.observe 装饰器)

行业适配采用 4 层机制, agent_role 注入角色 persona.

单维度 pass/revise 升级为多维度评分:
  - factual (事实性): 报告内容是否基于检索上下文, 有无幻觉
  - structural (结构性): 报告结构是否清晰, 章节划分是否合理
  - language (语言性): 语言是否流畅, 是否有语法错误
  - completeness (完整性): 是否充分回答用户查询
  评分规则: 任何维度 score < 6 → revise; 全部 >= 6 → accept

评分缓存.
- 模块级 _REVIEW_CACHE 跨 Reviewer 实例共享, TTL 30 分钟.
- 缓存键 md5(report_content + review_criteria), 报告变化则不命中.
- 缓存命中跳过 LLM 调用; miss 时正常评审后写入缓存.
- 缓存读写异常时静默降级, 不影响评审主流程.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from src.common.json_utils import safe_json_parse
from src.config.settings import Settings, get_settings
from src.graph.state import ResearcherState
from src.llm.client import LLMClient, LLMTier, get_llm_client
from src.observability.tracing import trace_chain

logger = logging.getLogger(__name__)

# 评分维度配置
_REVIEW_DIMENSIONS = ("factual", "structural", "language", "completeness")
_ACCEPT_THRESHOLD = 6  # score >= 6 视为该维度合格, < 6 触发 revise

# 维度中文映射, 用于反馈展示
_DIM_LABELS = {
    "factual": "事实性",
    "structural": "结构性",
    "language": "语言性",
    "completeness": "完整性",
}

# Reviewer 评分缓存 (模块级, 跨 Reviewer 实例共享).
# 缓存键: md5(report_content + review_criteria); 缓存值: 评审结果 + 时间戳.
# TTL: 30 分钟, 避免同一报告重复评审时浪费 LLM 调用.
_REVIEW_CACHE: dict[str, dict[str, Any]] = {}
_REVIEW_CACHE_TTL = 1800  # 秒 (30 分钟)


class Reviewer:
    """报告评审 Agent (多维度评分).

    用 smart_llm 按 4 维度评审报告质量, 返回 accept/revise 决策与反馈.
    reviewer 角色 + 章节级修订循环入口.
    """

    settings: Settings
    _llm: LLMClient

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm or get_llm_client()

    async def review(
        self,
        state: ResearcherState,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """评审报告质量 (多维度评分).

        Args:
            state: 研究状态, 含 report_md / contexts / query
            user_id: 用户 ID (隔离键, AGENTS.md 第 8 章)
            session_id: 会话 ID (隔离键, AGENTS.md 第 6 章)

        Returns:
            {
                "review_decision": "accept"|"revise",
                "review_feedback": str,
                "review_scores": dict[str, dict[str, Any]],
            }
            - review_decision: 接受或需修订 (由各维度 score 阈值规则决定)
            - review_feedback: 评审反馈 (revise 时含具体修订建议)
            - review_scores: 各维度评分 {"factual": {"score": int, "issues": [...]}, ...}
        """
        async with trace_chain(
            name="reviewer",
            input={
                "query": state.get("query", "")[:100],
                "report_len": len(state.get("report_md", "")),
            },
            user_id=user_id,
            session_id=session_id,
        ) as span:
            report_md = state.get("report_md", "")
            # 空报告: 全维度 0 分, 直接 revise
            if not report_md:
                # 标注类型避免 mypy 将 dict comprehension 推断为 dict[str, dict[str, object]],
                # 进而影响主分支 review_scores 的类型推断 (mypy 跨分支类型 join 行为).
                review_scores: dict[str, dict[str, Any]] = {
                    dim: {"score": 0, "issues": ["报告内容为空"]} for dim in _REVIEW_DIMENSIONS
                }
                feedback = "报告内容为空, 请重新生成."
                span.update(
                    output={"review_decision": "revise", "reason": "empty_report"},
                    metadata={"error": "report_md 为空", "review_scores": review_scores},
                )
                return {
                    "review_decision": "revise",
                    "review_feedback": feedback,
                    "review_scores": review_scores,
                }

            # 评分缓存检查 — 同一报告重复评审时跳过 LLM 调用.
            # 缓存键含报告内容 hash, 报告变化 (如 revise 后) 不会命中.
            cache_key = self._get_review_cache_key(state)
            cached = self._get_cached_review(cache_key)
            if cached is not None:
                logger.info(
                    "Reviewer 缓存命中, 跳过 LLM 评审 (key=%s, decision=%s)",
                    cache_key[:8],
                    cached.get("review_decision"),
                )
                span.update(
                    output={
                        "review_decision": cached.get("review_decision"),
                        "cache_hit": True,
                    },
                    metadata={
                        "cache_hit": True,
                        "review_scores": cached.get("review_scores", {}),
                    },
                )
                return cached

            role_persona = (
                state.get("agent_role") or "你是一位资深研究分析专家, 擅长多领域综合研究."
            )
            contexts = state.get("contexts", [])
            contexts_text = self._format_contexts(contexts)

            prompt = f"""{role_persona}

你的任务是: 对以下研究报告进行多维度评分 (0-10 分整数), 决定是否接受 (accept) 或需要修订 (revise).

评分维度:
1. factual (事实性): 报告内容是否基于检索到的上下文, 有无幻觉或编造事实
2. structural (结构性): 报告结构是否清晰, 章节划分是否合理, 逻辑是否连贯
3. language (语言性): 语言是否流畅专业, 是否有语法错误或表达不清
4. completeness (完整性): 是否充分回答了用户查询, 信息是否完整

评分规则:
- 0-5 分: 该维度不合格, 需修订
- 6-10 分: 该维度合格

研究问题: {state.get("query", "")}

上下文 (节选):
{contexts_text}

报告内容:
{report_md[:8000]}

请返回 JSON (仅返回 JSON, 不要其他内容):
{{
  "factual": {{"score": 8, "issues": ["具体问题1", "具体问题2"]}},
  "structural": {{"score": 7, "issues": ["..."]}},
  "language": {{"score": 9, "issues": []}},
  "completeness": {{"score": 6, "issues": ["..."]}},
  "overall_decision": "accept" 或 "revise",
  "revision_instructions": "若需修订, 给出具体修订建议 (按维度/章节指出问题与改进方向); 若接受可留空"
}}

注意:
- score 必须是 0-10 的整数
- issues 列出该维度具体问题, 无问题则返回空数组
- overall_decision 为你的整体建议, 但最终决策由各维度 score 阈值规则决定 (任一维度 < 6 → revise)
- revision_instructions 仅在有问题时填写"""

            messages = [{"role": "user", "content": prompt}]
            response = await self._llm.achat(
                messages,
                tier=LLMTier.SMART,
                temperature=0.2,
                max_tokens=4000,
                user_id=user_id,
                session_id=session_id,
                span_name="reviewer-llm",
                step="reviewer",
            )

            # 解析 LLM 返回的多维度评分 JSON
            result = safe_json_parse(
                response.content,
                fallback=self._fallback_scores(),
            )
            if not isinstance(result, dict):
                result = self._fallback_scores()

            # 解析多维度评分 (类型由空报告分支的标注确立, 无需再标注)
            review_scores = self._parse_review_scores(result)
            revision_instructions = str(result.get("revision_instructions", "")).strip()

            # 决策规则: 任一维度 score < 6 → revise, 全部 >= 6 → accept
            decision = "accept"
            for dim in _REVIEW_DIMENSIONS:
                if review_scores[dim]["score"] < _ACCEPT_THRESHOLD:
                    decision = "revise"
                    break

            feedback = self._build_feedback(decision, review_scores, revision_instructions)

            span.update(
                output={
                    "review_decision": decision,
                    "feedback_len": len(feedback),
                    "review_scores": review_scores,
                },
                metadata={
                    "decision": decision,
                    "factual": review_scores["factual"]["score"],
                    "structural": review_scores["structural"]["score"],
                    "language": review_scores["language"]["score"],
                    "completeness": review_scores["completeness"]["score"],
                },
            )
            result_payload = {
                "review_decision": decision,
                "review_feedback": feedback,
                "review_scores": review_scores,
            }
            # 写入缓存, 供后续相同报告重复评审复用.
            self._set_review_cache(cache_key, result_payload)
            return result_payload

    @staticmethod
    def _format_contexts(contexts: list[Any]) -> str:
        """格式化上下文列表为文本 (截断避免 token 过大)."""
        if not contexts:
            return "(无上下文)"
        return "\n".join(f"[{i + 1}] {str(c)[:500]}" for i, c in enumerate(contexts[:20]))

    @staticmethod
    def _fallback_scores() -> dict[str, Any]:
        """LLM 解析失败时的兜底评分 (全 6 分, 视为 accept).

        与原单维度实现 fallback=accept 行为一致, 解析失败不阻断流程.
        """
        return {dim: {"score": 6, "issues": []} for dim in _REVIEW_DIMENSIONS}

    @staticmethod
    def _parse_review_scores(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """解析并校验 LLM 返回的多维度评分.

        Args:
            result: LLM 返回的 JSON dict

        Returns:
            标准化后的 review_scores, 含 4 个维度, 每维度 {"score": int, "issues": list[str]}
        """
        scores: dict[str, dict[str, Any]] = {}
        for dim in _REVIEW_DIMENSIONS:
            dim_data = result.get(dim, {})
            if not isinstance(dim_data, dict):
                dim_data = {}

            # 校验 score: 转 int, 越界裁剪到 [0, 10], 非法值兜底 6
            try:
                score = int(dim_data.get("score", 6))
            except (TypeError, ValueError):
                score = 6
            score = max(0, min(10, score))

            # 校验 issues: 必须为 list, 元素转 str, 过滤空值
            issues = dim_data.get("issues", [])
            if not isinstance(issues, list):
                issues = []
            issues = [str(i) for i in issues if i]

            scores[dim] = {"score": score, "issues": issues}
        return scores

    @staticmethod
    def _build_feedback(
        decision: str,
        review_scores: dict[str, dict[str, Any]],
        revision_instructions: str,
    ) -> str:
        """组装评审反馈文本.

        Args:
            decision: accept | revise
            review_scores: 各维度评分
            revision_instructions: LLM 给出的修订建议

        Returns:
            反馈文本 (accept 时简短确认, revise 时含各维度问题与修订建议)
        """
        # 拼接各维度分数与问题
        lines: list[str] = []
        for dim in _REVIEW_DIMENSIONS:
            score = review_scores[dim]["score"]
            issues = review_scores[dim]["issues"]
            label = _DIM_LABELS[dim]
            line = f"- {label} ({dim}): {score}/10"
            if issues:
                issues_text = "; ".join(issues)
                line += f" — {issues_text}"
            lines.append(line)

        scores_block = "多维度评分:\n" + "\n".join(lines)

        if decision == "accept":
            return f"{scores_block}\n\n报告质量合格, 予以接受."

        # revise: 收集低分维度问题, 拼接修订建议
        parts: list[str] = [scores_block]

        low_dim_issues: list[str] = []
        for dim in _REVIEW_DIMENSIONS:
            if review_scores[dim]["score"] < _ACCEPT_THRESHOLD:
                for issue in review_scores[dim]["issues"]:
                    low_dim_issues.append(f"[{_DIM_LABELS[dim]}] {issue}")

        if low_dim_issues:
            parts.append("需修订的问题:\n" + "\n".join(f"- {i}" for i in low_dim_issues))

        if revision_instructions:
            parts.append(f"修订建议:\n{revision_instructions}")
        else:
            parts.append("请针对上述低分维度进行修订.")

        return "\n\n".join(parts)

    # ===== 评分缓存 =====

    def _get_review_cache_key(self, state: ResearcherState) -> str:
        """生成评审缓存键.

        缓存键 = md5(report_content + review_criteria).
        review_criteria 由影响评审结果的输入组成: agent_role (persona) +
        query (问题) + contexts (检索上下文). 任一变化则缓存不命中,
        保证缓存正确性; 报告内容变化 (如 revise 后) 必然不命中.

        Args:
            state: 研究状态.

        Returns:
            64 字符 sha256 十六进制摘要字符串.
        """
        report_md = state.get("report_md", "")
        role_persona = state.get("agent_role") or ""
        query = state.get("query", "")
        contexts_text = self._format_contexts(state.get("contexts", []))
        raw = f"{report_md}\x1f{role_persona}\x1f{query}\x1f{contexts_text}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _get_cached_review(key: str) -> dict[str, Any] | None:
        """查询评审缓存.

        命中且未过期时返回缓存结果; 未命中/过期/异常时返回 None (降级为正常评审).
        过期条目惰性删除.

        Args:
            key: _get_review_cache_key 生成的缓存键.

        Returns:
            缓存的评审结果 dict, 或 None.
        """
        try:
            entry = _REVIEW_CACHE.get(key)
        except Exception:  # noqa: BLE001
            # 缓存读取异常时降级, 不影响评审主流程
            return None
        if entry is None:
            return None
        ts = entry.get("ts", 0.0)
        if time.time() - ts > _REVIEW_CACHE_TTL:
            # 过期, 惰性清理
            _REVIEW_CACHE.pop(key, None)
            return None
        scores = entry.get("scores")
        # isinstance 校验: 收窄 Any → dict, 满足 --warn-return-any
        if not isinstance(scores, dict):
            return None
        return scores

    @staticmethod
    def _set_review_cache(key: str, scores: dict[str, Any]) -> None:
        """写入评审缓存.

        记录写入时间戳用于 TTL 检查. 写入异常时静默降级 (不影响评审结果).

        Args:
            key: _get_review_cache_key 生成的缓存键.
            scores: 评审结果 dict (review_decision / review_feedback / review_scores).
        """
        try:
            _REVIEW_CACHE[key] = {"ts": time.time(), "scores": scores}
        except Exception:  # noqa: BLE001
            # 缓存写入异常时降级, 不影响评审主流程
            logger.debug("Reviewer 缓存写入失败 (key=%s), 跳过缓存", key[:8])
