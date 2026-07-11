"""幻觉评测器 (设计参考: evals/hallucination_eval/evaluate.py).

用 LLM 检查报告中每个声明是否有来源支持, 计算幻觉率.
评测维度:
- 事实性 (factual): 声明是否有来源支持 (supported/unsupported/contradicted)
- 一致性 (consistency): 报告与来源是否矛盾

LLM 评测复用项目 LLMClient (LiteLLM 网关, AGENTS.md 第 9 章).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from evals import ResearcherClient
from src.llm.client import LLMClient, LLMTier

logger = logging.getLogger(__name__)


class HallucinationEvaluator:
    """幻觉评测器.

    通过 researcher_client 获取报告, 用 llm_client (LiteLLM) 检查声明支持情况.
    """

    def __init__(
        self,
        researcher_client: ResearcherClient,
        llm_client: LLMClient,
    ) -> None:
        """初始化幻觉评测器.

        Args:
            researcher_client: 研究器 HTTP 客户端 (不直接 import src/).
            llm_client: LLM 客户端 (复用项目 LLMClient, 用于声明提取与核查).
        """
        self.researcher = researcher_client
        self.llm = llm_client

    async def evaluate(
        self,
        query: str,
        report: str,
        sources: list[str],
    ) -> dict[str, Any]:
        """评测单份报告的幻觉情况.

        Args:
            query: 原始查询.
            report: 研究报告正文.
            sources: 来源文本列表 (URL 或来源内容片段).

        Returns:
            {"hallucination_rate": float, "unsupported_claims": [...],
             "supported_claims": [...], "total_claims": N, "query": str}
        """
        # 1. 提取报告中的事实声明
        claims = await self._extract_claims(report)
        if not claims:
            return {
                "hallucination_rate": 0.0,
                "unsupported_claims": [],
                "supported_claims": [],
                "total_claims": 0,
                "query": query,
            }

        # 2. 逐条检查声明是否有来源支持
        sources_text = "\n---\n".join(sources) if sources else "(无来源)"
        supported: list[dict[str, Any]] = []
        unsupported: list[dict[str, Any]] = []

        for claim in claims:
            verdict = await self._check_claim_support(claim, sources_text)
            entry: dict[str, Any] = {
                "claim": claim,
                "verdict": verdict["verdict"],
                "reason": verdict["reason"],
            }
            if verdict["verdict"] == "supported":
                supported.append(entry)
            else:
                unsupported.append(entry)

        total = len(claims)
        rate = len(unsupported) / total if total else 0.0

        return {
            "hallucination_rate": round(rate, 4),
            "unsupported_claims": unsupported,
            "supported_claims": supported,
            "total_claims": total,
            "query": query,
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
                step="hallucination_extract_claims",
            )
            return self._parse_claims_json(resp.content)
        except Exception as e:  # noqa: BLE001
            logger.error("提取声明失败: %s", e)
            return []

    async def _check_claim_support(self, claim: str, sources_text: str) -> dict[str, str]:
        """用 LLM 检查单条声明是否有来源支持.

        Args:
            claim: 待核查的事实声明.
            sources_text: 来源材料合并文本.

        Returns:
            {"verdict": "supported" | "unsupported" | "contradicted", "reason": str}
        """
        prompt = (
            "你是事实核查助手。判断以下声明是否有来源材料支持.\n\n"
            f"声明: {claim}\n\n"
            f"来源材料:\n{sources_text[:4000]}\n\n"
            "判定标准:\n"
            "- supported: 声明能从来源材料中找到直接或间接支持\n"
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
                step="hallucination_check_claim",
            )
            return self._parse_verdict_json(resp.content)
        except Exception as e:  # noqa: BLE001
            logger.error("检查声明失败 (claim=%s...): %s", claim[:50], e)
            return {"verdict": "unsupported", "reason": f"检查异常: {e}"}

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
