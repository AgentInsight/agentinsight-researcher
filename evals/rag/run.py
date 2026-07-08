"""RAGAS 评测运行器 (对标 AGENTS.md 第 10 章, CI 门禁).

从 JSON 加载查询集 (含 question + ground_truth), 对每个查询:
  调用 researcher → 获取报告+来源 → 调用 RAGASEvaluator → 输出汇总报告 + 门禁判定.

门禁阈值 (AGENTS.md 第 10 章):
- faithfulness ≥ 0.8
- answer_relevancy ≥ 0.8
- context_precision ≥ 0.7

用法:
    python -m evals.rag.run --dataset evals/rag/dataset.json
    python -m evals.rag.run --num-queries 3
    python -m evals.rag.run --threshold-override context_precision=0.6

环境变量:
    AGENT_URL: 研究器 API 地址 (默认 http://agent:8066)
    EVAL_AUTHORIZATION: Bearer JWT Token (可选)
    EVAL_LLM_MODEL: 评估器 LLM 模型名 (默认 deepseek/deepseek-chat)
    EVAL_LLM_API_KEY: 评估器 LLM API Key
    EVAL_LLM_API_BASE: 评估器 LLM API Base URL (可选)
    EVAL_EMBEDDING_MODEL: 评估器 Embedding 模型名 (默认 BAAI/bge-small-zh-v1.5)
    EVAL_EMBEDDING_API_BASE: 评估器 Embedding API Base URL (可选, 本地 TEI)
    EVAL_EMBEDDING_API_KEY: 评估器 Embedding API Key (可选)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

# 确保 src/ 可导入
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals import ResearcherClient  # noqa: E402
from evals.rag.evaluate import RAGASEvaluator  # noqa: E402

logger = logging.getLogger(__name__)

_DEFAULT_DATASET = Path(__file__).parent / "dataset.json"
_DEFAULT_OUTPUT = Path(__file__).parent / "results"

# AGENTS.md 第 10 章门禁阈值
GATE_FAITHFULNESS = 0.8
GATE_ANSWER_RELEVANCY = 0.8
GATE_CONTEXT_PRECISION = 0.7


def load_dataset(json_path: str | Path) -> list[dict[str, Any]]:
    """从 JSON 加载评测数据集.

    每条: {"question": "...", "ground_truth": "...", "tags": [...]}
    """
    with Path(json_path).open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"数据集格式错误: 期望 list, 实际 {type(data)}")
    return data


def _build_evaluator_llm() -> Any:
    """构建 RAGAS 评估器 LLM (LangchainLLMWrapper + langchain_openai.ChatOpenAI).

    AGENTS.md 第 4 章: 例外允许 langchain_openai (仅 RAGAS 评测内部使用).
    """
    from langchain_openai import ChatOpenAI
    from ragas.llms import LangchainLLMWrapper

    model = os.getenv("EVAL_LLM_MODEL", "deepseek/deepseek-chat")
    api_key = os.getenv("EVAL_LLM_API_KEY", os.getenv("LLM_API_KEY", ""))
    api_base = os.getenv("EVAL_LLM_API_BASE")

    kwargs: dict[str, Any] = {"model": model, "api_key": api_key, "temperature": 0.0}
    if api_base:
        kwargs["base_url"] = api_base

    llm = ChatOpenAI(**kwargs)
    return LangchainLLMWrapper(llm)


def _build_evaluator_embeddings() -> Any:
    """构建 RAGAS 评估器 Embedding (LangchainEmbeddingsWrapper).

    优先使用本地 TEI (bge-large-zh-v1.5) 的 OpenAI 兼容端点;
    无配置时降级到 OpenAI embedding.
    """
    from langchain_openai import OpenAIEmbeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper

    embedding_model = os.getenv("EVAL_EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5")
    embedding_base = os.getenv("EVAL_EMBEDDING_API_BASE")
    embedding_key = os.getenv("EVAL_EMBEDDING_API_KEY", os.getenv("EMBEDDINGS_API_KEY", "dummy"))

    kwargs: dict[str, Any] = {"model": embedding_model, "api_key": embedding_key}
    if embedding_base:
        kwargs["base_url"] = embedding_base

    embeddings = OpenAIEmbeddings(**kwargs)
    return LangchainEmbeddingsWrapper(embeddings)


async def run(
    num_queries: int,
    output: Path,
    base_url: str,
    authorization: str | None,
    dataset_path: Path,
    threshold_overrides: dict[str, float] | None,
) -> dict[str, Any]:
    """执行 RAGAS 评测.

    Args:
        num_queries: 评测查询数量 (0=全部).
        output: 评测报告输出目录.
        base_url: 研究器 API 地址.
        authorization: Bearer JWT Token (可选).
        dataset_path: 数据集 JSON 文件路径.
        threshold_overrides: 门禁阈值覆盖 (如 {"context_precision": 0.6}).

    Returns:
        评测汇总 dict.
    """
    dataset = load_dataset(dataset_path)
    if num_queries > 0:
        dataset = dataset[:num_queries]
    logger.info("加载 %d 个评测查询 (来源: %s)", len(dataset), dataset_path)

    # 构造客户端与评测器
    researcher = ResearcherClient(base_url=base_url, authorization=authorization)
    evaluator_llm = _build_evaluator_llm()
    evaluator_embeddings = _build_evaluator_embeddings()
    evaluator = RAGASEvaluator(researcher, evaluator_llm, evaluator_embeddings)

    # 应用门禁阈值覆盖
    gate_faithfulness = GATE_FAITHFULNESS
    gate_answer_relevancy = GATE_ANSWER_RELEVANCY
    gate_context_precision = GATE_CONTEXT_PRECISION
    if threshold_overrides:
        gate_faithfulness = threshold_overrides.get("faithfulness", gate_faithfulness)
        gate_answer_relevancy = threshold_overrides.get(
            "answer_relevancy", gate_answer_relevancy
        )
        gate_context_precision = threshold_overrides.get(
            "context_precision", gate_context_precision
        )

    results: list[dict[str, Any]] = []
    total_start = time.perf_counter()

    for i, item in enumerate(dataset, 1):
        question = item.get("question", "")
        ground_truth = item.get("ground_truth", "")
        if not question:
            continue

        logger.info("[%d/%d] 评测查询: %s", i, len(dataset), question[:80])
        try:
            eval_result = await evaluator.evaluate_single(question, ground_truth)
            results.append(eval_result)
            logger.info(
                "  faithfulness=%.3f, answer_relevancy=%.3f, context_precision=%.3f",
                eval_result["faithfulness"],
                eval_result["answer_relevancy"],
                eval_result["context_precision"],
            )
        except Exception as e:  # noqa: BLE001
            logger.error("评测查询失败 (%s): %s", question[:60], e)
            results.append(
                {
                    "question": question,
                    "ground_truth": ground_truth,
                    "error": str(e)[:500],
                    "faithfulness": 0.0,
                    "answer_relevancy": 0.0,
                    "context_precision": 0.0,
                }
            )

    total_elapsed = time.perf_counter() - total_start

    # 汇总统计 (排除 error 项)
    valid_results = [r for r in results if not r.get("error")]
    n_valid = len(valid_results)
    avg_faithfulness = sum(r["faithfulness"] for r in valid_results) / n_valid if n_valid else 0.0
    avg_answer_relevancy = (
        sum(r["answer_relevancy"] for r in valid_results) / n_valid if n_valid else 0.0
    )
    avg_context_precision = (
        sum(r["context_precision"] for r in valid_results) / n_valid if n_valid else 0.0
    )

    # 门禁判定
    gate_passed = (
        avg_faithfulness >= gate_faithfulness
        and avg_answer_relevancy >= gate_answer_relevancy
        and avg_context_precision >= gate_context_precision
    )

    summary: dict[str, Any] = {
        "evaluator": "ragas",
        "num_queries": len(results),
        "num_valid": n_valid,
        "num_errors": len(results) - n_valid,
        "avg_faithfulness": round(avg_faithfulness, 4),
        "avg_answer_relevancy": round(avg_answer_relevancy, 4),
        "avg_context_precision": round(avg_context_precision, 4),
        "gate_thresholds": {
            "faithfulness": gate_faithfulness,
            "answer_relevancy": gate_answer_relevancy,
            "context_precision": gate_context_precision,
        },
        "gate_passed": gate_passed,
        "total_elapsed_seconds": round(total_elapsed, 2),
        "results": results,
    }

    # 输出报告 (文件 I/O 轻量, 项目用 asyncio 非 trio/anyio, noqa ASYNC240)
    output.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    ts = time.strftime("%Y%m%d_%H%M%S")
    report_path = output / f"ragas_report_{ts}.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")  # noqa: ASYNC240

    # 控制台摘要
    print("\n=== RAGAS 评测报告 ===")
    print(f"查询数:         {summary['num_queries']} (有效 {n_valid}, 错误 {summary['num_errors']})")
    print(f"faithfulness:   {summary['avg_faithfulness']:.4f} (门禁 ≥{gate_faithfulness})")
    print(f"answer_relevancy: {summary['avg_answer_relevancy']:.4f} (门禁 ≥{gate_answer_relevancy})")
    print(f"context_precision: {summary['avg_context_precision']:.4f} (门禁 ≥{gate_context_precision})")
    print(f"门禁判定:       {'✅ 通过' if gate_passed else '❌ 不达标'}")
    print(f"总耗时:         {summary['total_elapsed_seconds']:.2f}s")
    print(f"报告已写入:     {report_path}")

    return summary


def main() -> None:
    """命令行入口."""
    parser = argparse.ArgumentParser(description="RAGAS RAG 质量评测运行器")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=_DEFAULT_DATASET,
        help="评测数据集 JSON 文件路径",
    )
    parser.add_argument(
        "--num-queries",
        type=int,
        default=0,
        help="评测查询数量 (0=全部)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="评测报告输出目录",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=os.getenv("AGENT_URL", "http://agent:8066"),
        help="研究器 API 地址 (默认读 AGENT_URL 环境变量)",
    )
    parser.add_argument(
        "--authorization",
        type=str,
        default=os.getenv("EVAL_AUTHORIZATION"),
        help="Bearer JWT Token (可选, 默认读 EVAL_AUTHORIZATION 环境变量)",
    )
    parser.add_argument(
        "--threshold-override",
        action="append",
        help="覆盖门禁阈值 (格式: metric=value, 如 context_precision=0.6)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="启用 DEBUG 日志",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # 解析阈值覆盖
    threshold_overrides: dict[str, float] | None = None
    if args.threshold_override:
        threshold_overrides = {}
        for item in args.threshold_override:
            if "=" in item:
                k, v = item.split("=", 1)
                try:
                    threshold_overrides[k.strip()] = float(v.strip())
                except ValueError:
                    logger.warning("无效的阈值覆盖: %s (跳过)", item)

    summary = asyncio.run(
        run(
            num_queries=args.num_queries,
            output=args.output,
            base_url=args.base_url,
            authorization=args.authorization,
            dataset_path=args.dataset,
            threshold_overrides=threshold_overrides,
        )
    )

    # 门禁不达标 exit 1 (CI 阻断)
    if not summary["gate_passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
