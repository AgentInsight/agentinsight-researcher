"""DeepEval Agent 行为评测器 (CI 门禁).

用 LLM 评估 researcher 的 Agent 行为质量, 核心指标:
- task_completion (任务完成率 ≥0.9): 报告是否回答了研究问题
- tool_call_accuracy (工具调用正确率 ≥0.95): MCP 工具调用是否正确
- hallucination_rate (幻觉率 ≤0.1): 报告内容是否有幻觉

LLM 评测复用项目 LLMClient (LiteLLM 网关).
门禁 task_completion ≥0.9 / tool_call_accuracy ≥0.95 / hallucination_rate ≤0.1.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from evals import ResearcherClient
from src.llm.client import LLMClient, LLMTier

logger = logging.getLogger(__name__)


class DeepEvalEvaluator:
    """DeepEval Agent 行为评测器.

    通过 researcher_client 获取研究报告+来源+工具调用, 用 llm_client (LiteLLM) 评估三项核心指标.
    """

    def __init__(
        self,
        researcher_client: ResearcherClient,
        llm_client: LLMClient,
    ) -> None:
        """初始化 DeepEval 评测器.

        Args:
            researcher_client: 研究器 HTTP 客户端 (不直接 import src/).
            llm_client: LLM 客户端 (复用项目 LLMClient, 用于评判).
        """
        self.researcher = researcher_client
        self.llm = llm_client

    async def evaluate(
        self,
        question: str,
        report: str,
        sources: list[dict[str, Any]] | list[str],
        expected_tools: list[str] | None = None,
        expected_keywords: list[str] | None = None,
        ground_truth: str = "",
        actual_tool_calls: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """评测单份研究报告的 Agent 行为质量.

        Args:
            question: 原始研究问题.
            report: 研究报告正文.
            sources: 参考来源列表 (dict 含 title/url 或字符串).
            expected_tools: 期望调用的 MCP 工具名列表 (用于工具调用正确率评测).
            expected_keywords: 期望报告出现的关键词列表 (辅助任务完成率评测).
            ground_truth: 标准答案 (用于幻觉评测参考).
            actual_tool_calls: 实际工具调用列表 (从 raw 响应解析; None 则从报告推断).

        Returns:
            {"task_completion": float, "tool_call_accuracy": float,
             "hallucination_rate": float, "details": {...}}
        """
        expected_tools = expected_tools or []
        expected_keywords = expected_keywords or []
        actual_tool_calls = actual_tool_calls or []

        # 1. 任务完成率: LLM 判断报告是否回答了研究问题
        task_result = await self._eval_task_completion(
            question, report, expected_keywords, ground_truth
        )

        # 2. 工具调用正确率: 对比期望工具与实际工具调用
        tool_result = self._eval_tool_call_accuracy(expected_tools, actual_tool_calls, report)

        # 3. 幻觉率: LLM 检查报告声明是否有来源支持
        hallucination_result = await self._eval_hallucination(report, sources, ground_truth)

        return {
            "question": question,
            "task_completion": task_result["score"],
            "task_completion_reason": task_result["reason"],
            "tool_call_accuracy": tool_result["score"],
            "tool_call_accuracy_reason": tool_result["reason"],
            "hallucination_rate": hallucination_result["score"],
            "hallucination_rate_reason": hallucination_result["reason"],
            "expected_tools": expected_tools,
            "actual_tool_calls": actual_tool_calls,
            "expected_keywords": expected_keywords,
            "ground_truth": ground_truth,
            "details": {
                "task_completion": task_result,
                "tool_call_accuracy": tool_result,
                "hallucination": hallucination_result,
            },
        }

    async def _eval_task_completion(
        self,
        question: str,
        report: str,
        expected_keywords: list[str],
        ground_truth: str,
    ) -> dict[str, Any]:
        """评测任务完成率: 报告是否回答了研究问题.

        评分标准 (0.0-1.0):
        - 1.0: 完整回答问题, 覆盖所有关键点
        - 0.7-0.9: 基本回答, 遗漏少量非关键点
        - 0.4-0.6: 部分回答, 遗漏重要关键点
        - 0.0-0.3: 未回答或严重偏离问题

        Args:
            question: 研究问题.
            report: 研究报告.
            expected_keywords: 期望出现的关键词.
            ground_truth: 标准答案参考.

        Returns:
            {"score": float, "reason": str}
        """
        # 关键词覆盖率 (辅助指标, 不单独决定分数)
        keyword_hit = 0
        if expected_keywords:
            report_lower = report.lower()
            keyword_hit = sum(1 for kw in expected_keywords if kw.lower() in report_lower)
            keyword_coverage = keyword_hit / len(expected_keywords)
        else:
            keyword_coverage = 1.0

        prompt = (
            "你是研究报告质量评判员。判断以下研究报告是否完整回答了研究问题。\n\n"
            f"研究问题: {question}\n\n"
            f"标准答案参考: {ground_truth[:1000] if ground_truth else '(无)'}\n\n"
            f"期望关键词覆盖率: {keyword_coverage:.0%}"
            f" ({keyword_hit}/{len(expected_keywords) if expected_keywords else 0})\n\n"
            f"研究报告 (前 4000 字):\n{report[:4000]}\n\n"
            "评判标准:\n"
            "- 1.0: 完整准确回答问题, 覆盖所有关键点\n"
            "- 0.7-0.9: 基本回答, 遗漏少量非关键点\n"
            "- 0.4-0.6: 部分回答, 遗漏重要关键点\n"
            "- 0.0-0.3: 未回答或严重偏离问题\n\n"
            "输出严格 JSON, 不要额外文本:\n"
            '{"score": 0.0-1.0, "reason": "简短理由"}'
        )
        messages = [{"role": "user", "content": prompt}]
        try:
            resp = await self.llm.achat(
                messages,
                tier=LLMTier.SMART,
                temperature=0.0,
                max_tokens=300,
                step="deepeval_task_completion",
            )
            return self._parse_score_json(resp.content, default_score=0.0)
        except Exception as e:  # noqa: BLE001
            logger.error("任务完成率评测失败: %s", e)
            return {"score": 0.0, "reason": f"评测异常: {e}"}

    def _eval_tool_call_accuracy(
        self,
        expected_tools: list[str],
        actual_tool_calls: list[dict[str, Any]],
        report: str,
    ) -> dict[str, Any]:
        """评测工具调用正确率: 期望工具 vs 实际工具调用.

        评分逻辑:
        - 无期望工具时返回 1.0 (无工具调用需求)
        - 计算期望工具的命中率 (实际调用工具名匹配期望工具)
        - 实际工具调用从 raw 响应解析, 缺失时从报告文本推断 (降级)

        Args:
            expected_tools: 期望调用的工具名列表.
            actual_tool_calls: 实际工具调用列表 (含 name 字段).
            report: 研究报告 (降级推断用).

        Returns:
            {"score": float, "reason": str, "matched": [...], "missing": [...]}
        """
        if not expected_tools:
            return {
                "score": 1.0,
                "reason": "无期望工具, 跳过工具调用正确率评测",
                "matched": [],
                "missing": [],
            }

        # 实际调用的工具名集合 (大小写不敏感)
        actual_names = {
            str(tc.get("name", "")).lower() for tc in actual_tool_calls if tc.get("name")
        }

        # 降级: 若无实际工具调用记录, 从报告文本推断 (匹配期望工具名出现)
        if not actual_names:
            report_lower = report.lower()
            actual_names = {
                tool.lower() for tool in expected_tools if tool.lower() in report_lower
            }
            if actual_names:
                logger.debug("从报告文本推断工具调用: %s", actual_names)

        matched: list[str] = []
        missing: list[str] = []
        for tool in expected_tools:
            if tool.lower() in actual_names:
                matched.append(tool)
            else:
                missing.append(tool)

        score = len(matched) / len(expected_tools) if expected_tools else 1.0
        reason = (
            f"期望 {len(expected_tools)} 个工具, 命中 {len(matched)} 个"
            f"{' (缺失: ' + ', '.join(missing) + ')' if missing else ''}"
        )
        return {
            "score": round(score, 4),
            "reason": reason,
            "matched": matched,
            "missing": missing,
        }

    async def _eval_hallucination(
        self,
        report: str,
        sources: list[dict[str, Any]] | list[str],
        ground_truth: str,
    ) -> dict[str, Any]:
        """评测幻觉率: 报告内容是否有幻觉.

        评分逻辑:
        - 用 LLM 提取报告中的事实声明
        - 逐条检查声明是否有来源支持 (supported/unsupported/contradicted)
        - 幻觉率 = 无支持声明数 / 总声明数

        Args:
            report: 研究报告.
            sources: 参考来源列表.
            ground_truth: 标准答案 (辅助核查).

        Returns:
            {"score": float, "reason": str, "total_claims": N, "unsupported": N}
        """
        # 1. 提取事实声明
        claims = await self._extract_claims(report)
        if not claims:
            return {
                "score": 0.0,
                "reason": "未提取到事实声明, 幻觉率记 0.0",
                "total_claims": 0,
                "unsupported": 0,
            }

        # 2. 构建来源文本
        source_texts: list[str] = []
        for src in sources:
            if isinstance(src, dict):
                title = src.get("title", "")
                url = src.get("url", "")
                source_texts.append(f"标题: {title}\nURL: {url}")
            else:
                source_texts.append(str(src))
        sources_text = "\n---\n".join(source_texts) if source_texts else "(无来源)"

        # 3. 逐条检查声明支持情况
        unsupported = 0
        for claim in claims:
            verdict = await self._check_claim_support(claim, sources_text, ground_truth)
            if verdict["verdict"] != "supported":
                unsupported += 1

        rate = unsupported / len(claims) if claims else 0.0
        reason = (
            f"共 {len(claims)} 条声明, {unsupported} 条无来源支持 (幻觉率 {rate:.0%})"
        )
        return {
            "score": round(rate, 4),
            "reason": reason,
            "total_claims": len(claims),
            "unsupported": unsupported,
        }

    async def _extract_claims(self, report: str) -> list[str]:
        """用 LLM 从报告中提取事实声明列表.

        Args:
            report: 研究报告正文.

        Returns:
            事实声明字符串列表 (提取失败返回空列表).
        """
        prompt = (
            "你是事实核查助手。从下面的研究报告中提取所有可验证的事实声明 "
            "(factual claims), 每条声明为一句完整陈述.\n"
            "仅提取事实性声明, 跳过主观评价、过渡语句、章节标题.\n"
            "输出严格 JSON 数组, 不要任何额外文本:\n"
            '["声明1", "声明2", ...]\n\n'
            f"研究报告:\n{report[:6000]}"
        )
        messages = [{"role": "user", "content": prompt}]
        try:
            resp = await self.llm.achat(
                messages,
                tier=LLMTier.SMART,
                temperature=0.0,
                max_tokens=2000,
                step="deepeval_extract_claims",
            )
            return self._parse_claims_json(resp.content)
        except Exception as e:  # noqa: BLE001
            logger.error("提取声明失败: %s", e)
            return []

    async def _check_claim_support(
        self,
        claim: str,
        sources_text: str,
        ground_truth: str,
    ) -> dict[str, str]:
        """用 LLM 检查单条声明是否有来源支持.

        Args:
            claim: 待核查的事实声明.
            sources_text: 来源材料合并文本.
            ground_truth: 标准答案 (辅助判断).

        Returns:
            {"verdict": "supported" | "unsupported" | "contradicted", "reason": str}
        """
        prompt = (
            "你是事实核查助手。判断以下声明是否有来源材料支持.\n\n"
            f"声明: {claim}\n\n"
            f"来源材料:\n{sources_text[:4000]}\n\n"
            f"标准答案参考: {ground_truth[:500] if ground_truth else '(无)'}\n\n"
            "判定标准:\n"
            "- supported: 声明能从来源材料或标准答案中找到直接或间接支持\n"
            "- unsupported: 来源材料中未找到相关支持\n"
            "- contradicted: 来源材料与声明矛盾\n\n"
            "输出严格 JSON, 不要额外文本:\n"
            '{"verdict": "supported|unsupported|contradicted", "reason": "简短理由"}'
        )
        messages = [{"role": "user", "content": prompt}]
        try:
            resp = await self.llm.achat(
                messages,
                tier=LLMTier.FAST,
                temperature=0.0,
                max_tokens=300,
                step="deepeval_check_claim",
            )
            return self._parse_verdict_json(resp.content)
        except Exception as e:  # noqa: BLE001
            logger.error("检查声明失败 (claim=%s...): %s", claim[:50], e)
            return {"verdict": "unsupported", "reason": f"检查异常: {e}"}

    @staticmethod
    def _parse_score_json(text: str, default_score: float = 0.0) -> dict[str, Any]:
        """解析 LLM 返回的评分 JSON (容错).

        Args:
            text: LLM 返回文本.
            default_score: 解析失败时的默认分数.

        Returns:
            {"score": float, "reason": str}
        """
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {"score": default_score, "reason": "无法解析评分结果"}
        raw = match.group(0)

        def _build(data: Any) -> dict[str, Any]:
            try:
                score = float(data.get("score", default_score))
            except (TypeError, ValueError):
                score = default_score
            # 限制 0-1 范围
            score = max(0.0, min(1.0, score))
            return {"score": round(score, 4), "reason": str(data.get("reason", ""))}

        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return _build(data)
        except json.JSONDecodeError:
            try:
                from json_repair import repair_json

                data = json.loads(repair_json(raw))
                if isinstance(data, dict):
                    return _build(data)
            except Exception as e:  # noqa: BLE001
                logger.debug("评分 JSON 解析失败: %s", e)
        return {"score": default_score, "reason": "无法解析评分结果"}

    @staticmethod
    def _parse_claims_json(text: str) -> list[str]:
        """解析 LLM 返回的声明 JSON 数组 (容错).

        优先 json.loads, 失败时降级 json_repair (项目已依赖).
        """
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return []
        raw = match.group(0)
        try:
            claims = json.loads(raw)
        except json.JSONDecodeError:
            try:
                from json_repair import repair_json

                claims = json.loads(repair_json(raw))
            except Exception as e:  # noqa: BLE001
                logger.debug("声明 JSON 解析失败: %s", e)
                return []
        if isinstance(claims, list):
            return [str(c).strip() for c in claims if str(c).strip()]
        return []

    @staticmethod
    def _parse_verdict_json(text: str) -> dict[str, str]:
        """解析 LLM 返回的判定 JSON (容错).

        优先 json.loads, 失败时降级 json_repair.
        """
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {"verdict": "unsupported", "reason": "无法解析判定结果"}
        raw = match.group(0)

        def _build(data: Any) -> dict[str, str]:
            verdict = str(data.get("verdict", "unsupported")).lower().strip()
            if verdict not in ("supported", "unsupported", "contradicted"):
                verdict = "unsupported"
            return {"verdict": verdict, "reason": str(data.get("reason", ""))}

        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return _build(data)
        except json.JSONDecodeError:
            try:
                from json_repair import repair_json

                data = json.loads(repair_json(raw))
                if isinstance(data, dict):
                    return _build(data)
            except Exception as e:  # noqa: BLE001
                logger.debug("判定 JSON 解析失败: %s", e)
        return {"verdict": "unsupported", "reason": "无法解析判定结果"}
