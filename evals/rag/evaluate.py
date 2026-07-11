"""RAGAS 评测器 (CI 门禁).

用 RAGAS 库评估 researcher 的 RAG 质量, 核心指标:
- faithfulness (忠实度 ≥0.8): 答案是否忠于检索上下文
- answer_relevancy (答案相关性 ≥0.8): 答案是否回应了问题
- context_precision (上下文精度 ≥0.7): 检索到的上下文是否相关

例外允许 langchain_openai (仅 RAGAS 评测内部使用, 不侵入业务代码).
门禁 faithfulness ≥0.8 / answer_relevancy ≥0.8 / context_precision ≥0.7.

RAGAS 0.2+ API:
- ragas.evaluate() (模块级函数, 非 dataset.evaluate())
- EvaluationDataset + SingleTurnSample 组织评测样本
- LangchainLLMWrapper 包装评估器 LLM
- LangchainEmbeddingsWrapper 包装评估器 Embedding
- 子进程隔离: ragas.evaluate() 内部用 asyncio + nest_asyncio, 与 Python 3.14 主事件循环
  冲突 (RuntimeError "Timeout should be used inside a task"), 通过子进程完全隔离事件循环
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from evals import ResearcherClient

logger = logging.getLogger(__name__)

# 项目根目录 (3 级上: evals/rag/evaluate.py -> 项目根), 模块级预计算避免在 async 函数内做阻塞 I/O
_PROJECT_ROOT = os.path.abspath(os.path.join(__file__, os.pardir, os.pardir, os.pardir))


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

        # 2. 通过子进程运行 RAGAS 评估
        # (修复 Python 3.14 + RAGAS nest_asyncio 冲突:
        #  RuntimeError "Timeout should be used inside a task" / coroutine never awaited)
        # 子进程完全隔离事件循环, 避免 nest_asyncio 与主事件循环冲突
        try:
            import json
            import sys as _sys
            from pathlib import Path

            subprocess_script = Path(__file__).parent / "_subprocess_eval.py"
            input_data = json.dumps(
                {
                    "question": question,
                    "answer": answer,
                    "contexts": contexts,
                    "reference": ground_truth,
                },
                ensure_ascii=False,
            )

            proc = await asyncio.create_subprocess_exec(
                _sys.executable,
                str(subprocess_script),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=_PROJECT_ROOT,
            )
            stdout, stderr = await proc.communicate(input=input_data.encode("utf-8"))

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace")[:500]
                result["error"] = f"RAGAS 子进程退出码 {proc.returncode}: {err_msg}"
                logger.error("RAGAS 子进程失败: %s", err_msg)
            else:
                output = json.loads(stdout.decode("utf-8"))
                scores = output.get("scores", {})
                errors = output.get("errors", {})

                result["faithfulness"] = scores.get("faithfulness", 0.0)
                result["answer_relevancy"] = scores.get("answer_relevancy", 0.0)
                result["context_precision"] = scores.get("context_precision", 0.0)

                # 记录子进程中的错误 (不阻断, 某些指标可能仍有效)
                error_parts = [f"{k}: {v}" for k, v in errors.items() if v]
                if error_parts:
                    result["error"] = "; ".join(error_parts)
                    logger.warning("RAGAS 子进程指标错误: %s", result["error"])

        except Exception as e:  # noqa: BLE001
            err_msg = str(e)
            if hasattr(e, "exceptions"):
                sub_msgs = [f"{type(sub).__name__}: {sub}" for sub in e.exceptions]
                err_msg = f"{type(e).__name__}: {' | '.join(sub_msgs)}"
            result["error"] = f"RAGAS 评估异常: {err_msg}"
            logger.error("RAGAS 评估异常: %s", err_msg)

        return result
