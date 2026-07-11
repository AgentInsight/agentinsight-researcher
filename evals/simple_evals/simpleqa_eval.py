"""SimpleQA 评测器 (设计参考: evals/simple_evals/simpleqa_eval.py).

通过 HTTP API 调用 researcher, 对比标准答案计算准确率.
评测维度:
- 准确性 (accuracy): 答案是否与标准答案匹配 (大小写不敏感子串匹配, 支持 | 多候选)
- 完整性 (completeness): 标准答案关键词在响应中的覆盖率
- 响应时间 (response_time): 单次研究耗时 (秒)
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import Any

from evals import ResearcherClient

logger = logging.getLogger(__name__)


class SimpleQAEvaluator:
    """SimpleQA 评测器.

    通过 researcher_client 调用研究器 API, 对比标准答案评测响应质量.
    """

    def __init__(self, researcher_client: ResearcherClient) -> None:
        """初始化评测器.

        Args:
            researcher_client: 研究器 HTTP 客户端 (不直接 import src/).
        """
        self.researcher = researcher_client

    async def evaluate(self, questions: list[dict[str, Any]]) -> dict[str, Any]:
        """对一批问题执行评测.

        Args:
            questions: 问题列表, 每项含 id/question/expected_answer/category.

        Returns:
            {"total": N, "correct": N, "accuracy": float,
             "avg_completeness": float, "avg_response_time": float,
             "details": [...]}
        """
        details: list[dict[str, Any]] = []
        correct_count = 0
        completeness_sum = 0.0
        response_time_sum = 0.0

        for q in questions:
            qid = q.get("id", "?")
            question = q.get("question", "")
            expected = q.get("expected_answer", "")
            category = q.get("category", "unknown")

            if not question:
                logger.warning("跳过空问题 (id=%s)", qid)
                continue

            try:
                result = await self.researcher.research(question)
                report = result["report"]
                elapsed = result["elapsed_seconds"]
                sources = result["sources"]

                # 准确性: 标准答案是否出现在报告中 (大小写不敏感)
                is_correct = self._check_answer(expected, report)
                # 完整性: 标准答案关键词覆盖率
                completeness = self._compute_completeness(expected, report)

                if is_correct:
                    correct_count += 1
                completeness_sum += completeness
                response_time_sum += elapsed

                details.append(
                    {
                        "id": qid,
                        "question": question,
                        "expected_answer": expected,
                        "category": category,
                        "correct": is_correct,
                        "completeness": round(completeness, 4),
                        "response_time": round(elapsed, 2),
                        "source_count": len(sources),
                        "report_excerpt": report[:200],
                    }
                )
            except Exception as e:  # noqa: BLE001
                logger.error("评测问题 %s 失败: %s", qid, e)
                details.append(
                    {
                        "id": qid,
                        "question": question,
                        "expected_answer": expected,
                        "category": category,
                        "correct": False,
                        "completeness": 0.0,
                        "response_time": 0.0,
                        "source_count": 0,
                        "error": str(e)[:300],
                    }
                )

        total = len(details)
        accuracy = correct_count / total if total else 0.0
        avg_completeness = completeness_sum / total if total else 0.0
        avg_response_time = response_time_sum / total if total else 0.0

        return {
            "total": total,
            "correct": correct_count,
            "accuracy": round(accuracy, 4),
            "avg_completeness": round(avg_completeness, 4),
            "avg_response_time": round(avg_response_time, 2),
            "details": details,
        }

    @staticmethod
    def _check_answer(expected: str, report: str) -> bool:
        """检查标准答案是否出现在报告中 (大小写不敏感).

        简单子串匹配; 多个候选答案用 | 分隔, 任一命中即正确.
        """
        if not expected:
            return False
        report_lower = report.lower()
        # 支持 "答案A|答案B" 多候选
        candidates = [c.strip() for c in expected.split("|") if c.strip()]
        return any(cand.lower() in report_lower for cand in candidates)

    @staticmethod
    def _compute_completeness(expected: str, report: str) -> float:
        """计算标准答案关键词在报告中的覆盖率 (0.0-1.0).

        将标准答案拆分为关键词 (英文单词 + 中文连续片段), 统计在报告中命中的比例.
        """
        if not expected:
            return 0.0
        # 英文单词 (长度 ≥2)
        en_words = re.findall(r"[A-Za-z]{2,}", expected)
        # 中文连续片段 (按 2 字以上)
        cn_chunks = re.findall(r"[\u4e00-\u9fa5]{2,}", expected)
        # 单字中文兜底 (若无 2 字以上片段)
        cn_chars = re.findall(r"[\u4e00-\u9fa5]", expected)

        keywords: list[str] = list(en_words) + list(cn_chunks)
        if not cn_chunks and cn_chars:
            keywords.extend(cn_chars)

        if not keywords:
            return 0.0

        report_lower = report.lower()
        hit = sum(1 for kw in keywords if kw.lower() in report_lower)
        return hit / len(keywords)

    @staticmethod
    def load_questions_from_csv(csv_path: str | Path) -> list[dict[str, Any]]:
        """从 CSV 加载问题集.

        CSV 格式: id,question,expected_answer,category
        """
        questions: list[dict[str, Any]] = []
        with Path(csv_path).open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                questions.append(
                    {
                        "id": row.get("id", "").strip(),
                        "question": row.get("question", "").strip(),
                        "expected_answer": row.get("expected_answer", "").strip(),
                        "category": row.get("category", "general").strip(),
                    }
                )
        return questions
