"""DeepEval Agent 行为评测运行器 (CI 门禁).

从 JSON 加载查询集 (含 question + expected_tools + expected_keywords + ground_truth),
对每个查询:
  调用 researcher → 获取报告+来源+工具调用 → 调用 DeepEvalEvaluator → 输出汇总报告 + 门禁判定.

门禁阈值:
- task_completion ≥ 0.9
- tool_call_accuracy ≥ 0.95
- hallucination_rate ≤ 0.1

用法:
    python -m evals.agent.run --dataset evals/agent/dataset.json
    python -m evals.agent.run --num-queries 3
    python -m evals.agent.run --threshold-override task_completion=0.85

环境变量:
    AGENT_URL: 研究器 API 地址 (默认 http://agent:8066)
    EVAL_AUTHORIZATION: Bearer JWT Token (可选)
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

# 确保 src/ 可导入 (复用 LLMClient, LiteLLM 网关)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals import ResearcherClient  # noqa: E402
from evals.agent.evaluate import DeepEvalEvaluator  # noqa: E402
from src.llm.client import LLMClient  # noqa: E402

logger = logging.getLogger(__name__)

_DEFAULT_DATASET = Path(__file__).parent / "dataset.json"
_DEFAULT_OUTPUT = Path(__file__).parent / "results"

# 门禁阈值 (AGENTS.md 第 10 章)
GATE_TASK_COMPLETION = 0.9
GATE_TOOL_CALL_ACCURACY = 0.95
GATE_HALLUCINATION_RATE = 0.1


def load_dataset(json_path: str | Path) -> list[dict[str, Any]]:
    """从 JSON 加载评测数据集.

    每条: {"question": "...", "expected_tools": [...],
           "expected_keywords": [...], "ground_truth": "..."}
    """
    with Path(json_path).open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"数据集格式错误: 期望 list, 实际 {type(data)}")
    return data


def _extract_tool_calls(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """从 OpenAI 兼容响应中解析工具调用列表.

    Args:
        raw: OpenAI 兼容响应原始 dict.

    Returns:
        工具调用列表, 每项含 name/arguments 字段.
    """
    tool_calls: list[dict[str, Any]] = []
    choices = raw.get("choices", [])
    if not choices:
        return tool_calls
    message = choices[0].get("message", {})
    raw_calls = message.get("tool_calls", [])
    for tc in raw_calls:
        function = tc.get("function", {})
        tool_calls.append(
            {
                "name": function.get("name", ""),
                "arguments": function.get("arguments", ""),
                "id": tc.get("id", ""),
            }
        )
    return tool_calls


async def run(
    num_queries: int,
    output: Path,
    base_url: str,
    authorization: str | None,
    dataset_path: Path,
    threshold_overrides: dict[str, float] | None,
) -> dict[str, Any]:
    """执行 DeepEval Agent 行为评测.

    Args:
        num_queries: 评测查询数量 (0=全部).
        output: 评测报告输出目录.
        base_url: 研究器 API 地址.
        authorization: Bearer JWT Token (可选).
        dataset_path: 数据集 JSON 文件路径.
        threshold_overrides: 门禁阈值覆盖 (如 {"task_completion": 0.85}).

    Returns:
        评测汇总 dict.
    """
    dataset = load_dataset(dataset_path)
    if num_queries > 0:
        dataset = dataset[:num_queries]
    logger.info("加载 %d 个评测查询 (来源: %s)", len(dataset), dataset_path)

    # 构造客户端与评测器
    researcher = ResearcherClient(base_url=base_url, authorization=authorization)
    llm_client = LLMClient()
    evaluator = DeepEvalEvaluator(researcher, llm_client)

    # 应用门禁阈值覆盖
    gate_task_completion = GATE_TASK_COMPLETION
    gate_tool_call_accuracy = GATE_TOOL_CALL_ACCURACY
    gate_hallucination_rate = GATE_HALLUCINATION_RATE
    if threshold_overrides:
        gate_task_completion = threshold_overrides.get(
            "task_completion", gate_task_completion
        )
        gate_tool_call_accuracy = threshold_overrides.get(
            "tool_call_accuracy", gate_tool_call_accuracy
        )
        gate_hallucination_rate = threshold_overrides.get(
            "hallucination_rate", gate_hallucination_rate
        )

    results: list[dict[str, Any]] = []
    total_start = time.perf_counter()

    for i, item in enumerate(dataset, 1):
        question = item.get("question", "")
        expected_tools = item.get("expected_tools", [])
        expected_keywords = item.get("expected_keywords", [])
        ground_truth = item.get("ground_truth", "")
        if not question:
            continue

        logger.info("[%d/%d] 评测查询: %s", i, len(dataset), question[:80])
        try:
            # 1. 调用 researcher 获取报告 + 来源 + 工具调用
            research_result = await researcher.research(question)
            report = research_result["report"]
            sources = research_result["sources"]
            elapsed = research_result["elapsed_seconds"]
            raw = research_result["raw"]
            actual_tool_calls = _extract_tool_calls(raw)

            # 2. 调用 DeepEval 评测器
            eval_result = await evaluator.evaluate(
                question=question,
                report=report,
                sources=sources,
                expected_tools=expected_tools,
                expected_keywords=expected_keywords,
                ground_truth=ground_truth,
                actual_tool_calls=actual_tool_calls,
            )
            eval_result["elapsed_seconds"] = round(elapsed, 2)
            eval_result["source_count"] = len(sources)
            eval_result["session_id"] = research_result["session_id"]

            results.append(eval_result)
            logger.info(
                "  task_completion=%.3f, tool_call_accuracy=%.3f, hallucination_rate=%.3f",
                eval_result["task_completion"],
                eval_result["tool_call_accuracy"],
                eval_result["hallucination_rate"],
            )
        except Exception as e:  # noqa: BLE001
            logger.error("评测查询失败 (%s): %s", question[:60], e)
            results.append(
                {
                    "question": question,
                    "error": str(e)[:500],
                    "task_completion": 0.0,
                    "tool_call_accuracy": 0.0,
                    "hallucination_rate": 1.0,
                }
            )

    total_elapsed = time.perf_counter() - total_start

    # 汇总统计 (排除 error 项)
    valid_results = [r for r in results if not r.get("error")]
    n_valid = len(valid_results)
    avg_task_completion = (
        sum(r["task_completion"] for r in valid_results) / n_valid if n_valid else 0.0
    )
    avg_tool_call_accuracy = (
        sum(r["tool_call_accuracy"] for r in valid_results) / n_valid if n_valid else 0.0
    )
    avg_hallucination_rate = (
        sum(r["hallucination_rate"] for r in valid_results) / n_valid if n_valid else 0.0
    )

    # 门禁判定
    gate_passed = (
        avg_task_completion >= gate_task_completion
        and avg_tool_call_accuracy >= gate_tool_call_accuracy
        and avg_hallucination_rate <= gate_hallucination_rate
    )

    summary: dict[str, Any] = {
        "evaluator": "deepeval",
        "num_queries": len(results),
        "num_valid": n_valid,
        "num_errors": len(results) - n_valid,
        "avg_task_completion": round(avg_task_completion, 4),
        "avg_tool_call_accuracy": round(avg_tool_call_accuracy, 4),
        "avg_hallucination_rate": round(avg_hallucination_rate, 4),
        "gate_thresholds": {
            "task_completion": gate_task_completion,
            "tool_call_accuracy": gate_tool_call_accuracy,
            "hallucination_rate": gate_hallucination_rate,
        },
        "gate_passed": gate_passed,
        "total_elapsed_seconds": round(total_elapsed, 2),
        "results": results,
    }

    # 输出报告 (文件 I/O 轻量, 项目用 asyncio 非 trio/anyio, noqa ASYNC240)
    output.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    ts = time.strftime("%Y%m%d_%H%M%S")
    report_path = output / f"deepeval_report_{ts}.json"
    report_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )  # noqa: ASYNC240

    # 控制台摘要
    print("\n=== DeepEval Agent 行为评测报告 ===")
    print(
        f"查询数:             {summary['num_queries']}"
        f" (有效 {n_valid}, 错误 {summary['num_errors']})"
    )
    print(
        f"task_completion:    {summary['avg_task_completion']:.4f}"
        f" (门禁 ≥{gate_task_completion})"
    )
    print(
        f"tool_call_accuracy: {summary['avg_tool_call_accuracy']:.4f}"
        f" (门禁 ≥{gate_tool_call_accuracy})"
    )
    print(
        f"hallucination_rate: {summary['avg_hallucination_rate']:.4f}"
        f" (门禁 ≤{gate_hallucination_rate})"
    )
    print(f"门禁判定:           {'✅ 通过' if gate_passed else '❌ 不达标'}")
    print(f"总耗时:             {summary['total_elapsed_seconds']:.2f}s")
    print(f"报告已写入:         {report_path}")

    return summary


def main() -> None:
    """命令行入口."""
    parser = argparse.ArgumentParser(description="DeepEval Agent 行为评测运行器")
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
        help="覆盖门禁阈值 (格式: metric=value, 如 task_completion=0.85)",
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
