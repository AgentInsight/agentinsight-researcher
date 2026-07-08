"""RAGAS 评测器 (AGENTS.md 第 10 章, CI 门禁).

用 RAGAS 库评估 researcher 的 RAG 质量, 核心指标:
- faithfulness (忠实度 ≥0.8): 答案是否忠于检索上下文
- answer_relevancy (答案相关性 ≥0.8): 答案是否回应了问题
- context_precision (上下文精度 ≥0.7): 检索到的上下文是否相关

AGENTS.md 第 4 章: 例外允许 langchain_openai (仅 RAGAS 评测内部使用, 不侵入业务代码).
AGENTS.md 第 10 章: 门禁 faithfulness ≥0.8 / answer_relevancy ≥0.8 / context_precision ≥0.7.

RAGAS 0.2+ API:
- EvaluationDataset + SingleTurnSample 组织评测样本
- LangchainLLMWrapper 包装评估器 LLM
- LangchainEmbeddingsWrapper 包装评估器 Embedding
"""

from __future__ import annotations

import logging
from typing import Any

from evals import ResearcherClient

logger = logging.getLogger(__name__)


class RAGASEvaluator:
    """RAGAS RAG 质量评测器.

    通过 researcher_client 获取研究报告+来源, 用 RAGAS 库评估三项核心指标.
    """

    def __init__(
        self,
        researcher_client: ResearcherClient,
        evaluator_llm: Any,
        evaluator_embeddings: Any,
    ) -> None:
        """初始化 RAGAS 评测器.

        Args:
            researcher_client: 研究器 HTTP 客户端 (不直接 import src/).
            evaluator_llm: RAGAS 评估器 LLM (LangchainLLMWrapper 包装).
            evaluator_embeddings: RAGAS 评估器 Embedding (LangchainEmbeddingsWrapper 包装).
        """
        self.researcher = researcher_client
        self.evaluator_llm = evaluator_llm
        self.evaluator_embeddings = evaluator_embeddings

    async def evaluate_single(
        self,
        question: str,
        ground_truth: str,
    ) -> dict[str, Any]:
        """评测单个查询的 RAG 质量.

        Args:
            question: 用户查询.
            ground_truth: 标准答案 (用于 context_precision 参考).

        Returns:
            {"question": str, "answer": str, "contexts": list[str],
             "faithfulness": float, "answer_relevancy": float,
             "context_precision": float, "error": str | None}
        """
        # 1. 调用 researcher 获取报告 + 来源
        research_result = await self.researcher.research(question)
        answer = research_result["report"]
        sources = research_result["sources"]
        elapsed = research_result["elapsed_seconds"]

        # 将来源转为上下文文本列表 (RAGAS contexts 字段)
        contexts: list[str] = []
        for src in sources:
            title = src.get("title", "")
            url = src.get("url", "")
            contexts.append(f"{title}\n{url}")

        # 若无来源, 用报告本身作为上下文 (避免 RAGAS 报错)
        if not contexts:
            contexts = [answer]

        result: dict[str, Any] = {
            "question": question,
            "ground_truth": ground_truth,
            "answer": answer,
            "contexts": contexts,
            "elapsed_seconds": round(elapsed, 2),
            "source_count": len(sources),
            "faithfulness": 0.0,
            "answer_relevancy": 0.0,
            "context_precision": 0.0,
            "error": None,
        }

        # 2. 构建 RAGAS SingleTurnSample 并评估
        try:
            from ragas import EvaluationDataset, SingleTurnSample
            from ragas.metrics import (
                AnswerRelevancy,
                Faithfulness,
                LLMContextPrecisionWithReference,
            )

            sample = SingleTurnSample(
                user_input=question,
                response=answer,
                retrieved_contexts=contexts,
                reference=ground_truth,
            )
            dataset = EvaluationDataset([sample])

            # 逐指标评估 (每个指标单独计算, 避免某项失败影响其他)
            metrics = {
                "faithfulness": Faithfulness(),
                "answer_relevancy": AnswerRelevancy(),
                "context_precision": LLMContextPrecisionWithReference(),
            }

            scores = {}
            for name, metric in metrics.items():
                try:
                    score_result = dataset.evaluate(
                        metrics=[metric],
                        llm=self.evaluator_llm,
                        embeddings=self.evaluator_embeddings,
                    )
                    # 提取分数 (to_pandas 返回 DataFrame)
                    df = score_result.to_pandas()
                    if name in df.columns and len(df) > 0:
                        val = df[name].iloc[0]
                        scores[name] = float(val) if val is not None else 0.0
                    else:
                        scores[name] = 0.0
                        logger.warning("指标 %s 未返回有效分数", name)
                except Exception as e:  # noqa: BLE001
                    scores[name] = 0.0
                    logger.warning("指标 %s 评估失败: %s", name, e)
                    result["error"] = f"{name}: {e}"

            result["faithfulness"] = scores.get("faithfulness", 0.0)
            result["answer_relevancy"] = scores.get("answer_relevancy", 0.0)
            result["context_precision"] = scores.get("context_precision", 0.0)

        except ImportError as e:
            result["error"] = f"RAGAS 库未安装或版本不兼容: {e}"
            logger.error("RAGAS 库导入失败: %s", e)
        except Exception as e:  # noqa: BLE001
            result["error"] = f"RAGAS 评估异常: {e}"
            logger.error("RAGAS 评估异常: %s", e)

        return result
