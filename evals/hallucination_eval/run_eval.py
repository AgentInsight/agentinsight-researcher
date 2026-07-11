"""幻觉评测运行器.

从 JSONL 加载查询集, 对每个查询:
  调用 researcher → 获取报告+来源 → 调用 HallucinationEvaluator → 输出汇总报告.
用法:
    python -m evals.hallucination_eval.run_eval --output results/
    python -m evals.hallucination_eval.run_eval --num-queries 5
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

# 确保 src/ 可导入 (复用 LLMClient, AGENTS.md 第 9 章 LiteLLM 网关)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals import ResearcherClient  # noqa: E402
from evals.hallucination_eval.evaluate import HallucinationEvaluator  # noqa: E402
from src.llm.client import LLMClient  # noqa: E402

logger = logging.getLogger(__name__)

_DEFAULT_INPUT = Path(__file__).parent / "inputs" / "search_queries.jsonl"
_DEFAULT_OUTPUT = Path(__file__).parent / "results"


def load_queries(jsonl_path: str | Path) -> list[dict[str, Any]]:
    """从 JSONL 加载查询集.

    每行一个 JSON: {"query": "...", "expected_source_count": N}
    """
    queries: list[dict[str, Any]] = []
    with Path(jsonl_path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                queries.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning("跳过无效 JSONL 行: %s (错误: %s)", line[:80], e)
    return queries


async def run(
    num_queries: int,
    output: Path,
    base_url: str,
    authorization: str | None,
    input_path: Path,
) -> None:
    """执行幻觉评测.

    Args:
        num_queries: 评测查询数量 (0=全部).
        output: 评测报告输出目录.
        base_url: 研究器 API 地址.
        authorization: Bearer JWT Token (可选).
        input_path: 查询集 JSONL 文件路径.
    """
    queries = load_queries(input_path)
    if num_queries > 0:
        queries = queries[:num_queries]
    logger.info("加载 %d 个查询 (来源: %s)", len(queries), input_path)

    # 构造客户端
    researcher = ResearcherClient(base_url=base_url, authorization=authorization)
    llm_client = LLMClient()
    evaluator = HallucinationEvaluator(researcher, llm_client)

    results: list[dict[str, Any]] = []
    total_unsupported = 0
    total_claims = 0
    start = time.perf_counter()

    for i, q in enumerate(queries, 1):
        query = q.get("query", "")
        expected_sources = q.get("expected_source_count", 0)
        if not query:
            continue

        logger.info("[%d/%d] 评测查询: %s", i, len(queries), query[:80])
        try:
            # 1. 调用 researcher 获取报告 + 来源
            research_result = await researcher.research(query)
            report = research_result["report"]
            sources = research_result["sources"]
            elapsed = research_result["elapsed_seconds"]

            # 将来源转为文本列表 (用 URL + title, 供 LLM 核查)
            source_texts = [f"标题: {s.get('title', '')}\nURL: {s.get('url', '')}" for s in sources]
            if len(sources) < expected_sources:
                logger.warning(
                    "来源数量 %d 低于预期 %d (query=%s)",
                    len(sources),
                    expected_sources,
                    query[:60],
                )

            # 2. 调用幻觉评测器
            eval_result = await evaluator.evaluate(query, report, source_texts)
            eval_result["elapsed_seconds"] = round(elapsed, 2)
            eval_result["source_count"] = len(sources)
            eval_result["expected_source_count"] = expected_sources

            results.append(eval_result)
            total_unsupported += len(eval_result["unsupported_claims"])
            total_claims += eval_result["total_claims"]

            logger.info(
                "  声明 %d, 无支持 %d, 幻觉率 %.2f%%",
                eval_result["total_claims"],
                len(eval_result["unsupported_claims"]),
                eval_result["hallucination_rate"] * 100,
            )
        except Exception as e:  # noqa: BLE001
            logger.error("评测查询失败 (%s): %s", query[:60], e)
            results.append(
                {
                    "query": query,
                    "error": str(e)[:500],
                    "hallucination_rate": 1.0,
                    "total_claims": 0,
                    "unsupported_claims": [],
                    "supported_claims": [],
                }
            )

    total_elapsed = time.perf_counter() - start
    overall_rate = total_unsupported / total_claims if total_claims else 0.0

    summary: dict[str, Any] = {
        "evaluator": "hallucination",
        "num_queries": len(results),
        "total_claims": total_claims,
        "total_unsupported_claims": total_unsupported,
        "overall_hallucination_rate": round(overall_rate, 4),
        "total_elapsed_seconds": round(total_elapsed, 2),
        "results": results,
    }

    # 输出报告 (文件 I/O 轻量, 项目用 asyncio 非 trio/anyio, noqa ASYNC240)
    output.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    ts = time.strftime("%Y%m%d_%H%M%S")
    report_path = output / f"hallucination_report_{ts}.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # 控制台摘要
    print("\n=== 幻觉评测报告 ===")
    print(f"查询数:       {summary['num_queries']}")
    print(f"声明总数:     {summary['total_claims']}")
    print(f"无支持声明:   {summary['total_unsupported_claims']}")
    print(f"幻觉率:       {summary['overall_hallucination_rate']:.2%}")
    print(f"总耗时:       {summary['total_elapsed_seconds']:.2f}s")
    print(f"报告已写入:   {report_path}")


def main() -> None:
    """命令行入口."""
    parser = argparse.ArgumentParser(description="幻觉评测运行器")
    parser.add_argument(
        "--num-queries",
        type=int,
        default=0,
        help="评测查询数量 (0=全部)",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=_DEFAULT_INPUT,
        help="查询集 JSONL 文件路径",
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
        "--verbose",
        action="store_true",
        help="启用 DEBUG 日志",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    asyncio.run(
        run(
            num_queries=args.num_queries,
            output=args.output,
            base_url=args.base_url,
            authorization=args.authorization,
            input_path=args.input,
        )
    )


if __name__ == "__main__":
    main()
