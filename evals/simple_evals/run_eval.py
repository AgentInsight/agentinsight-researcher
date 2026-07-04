"""SimpleQA 评测运行器 (对标 GPTR evals/simple_evals/run_eval.py).

从 CSV 加载问题集, 调用 SimpleQAEvaluator, 输出评测报告到 results/.
用法:
    python -m evals.simple_evals.run_eval --num-questions 10 --output results/
    python -m evals.simple_evals.run_eval --base-url http://localhost:8066
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

# 确保 src/ 可导入 (复用 LLMClient, 仅 run_eval 需要; evaluator 自身不 import src/)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals import ResearcherClient  # noqa: E402
from evals.simple_evals.simpleqa_eval import SimpleQAEvaluator  # noqa: E402

logger = logging.getLogger(__name__)

# 默认路径
_DEFAULT_CSV = Path(__file__).parent / "problems" / "sample_questions.csv"
_DEFAULT_OUTPUT = Path(__file__).parent / "results"


async def run(
    num_questions: int,
    output: Path,
    base_url: str,
    authorization: str | None,
) -> None:
    """执行 SimpleQA 评测.

    Args:
        num_questions: 评测问题数量 (0=全部).
        output: 评测报告输出目录.
        base_url: 研究器 API 地址.
        authorization: Bearer JWT Token (可选).
    """
    # 加载问题集
    questions = SimpleQAEvaluator.load_questions_from_csv(_DEFAULT_CSV)
    if num_questions > 0:
        questions = questions[:num_questions]
    logger.info("加载 %d 个问题 (来源: %s)", len(questions), _DEFAULT_CSV)

    # 构造客户端
    researcher = ResearcherClient(base_url=base_url, authorization=authorization)
    evaluator = SimpleQAEvaluator(researcher)

    # 执行评测
    start = time.perf_counter()
    result = await evaluator.evaluate(questions)
    total_elapsed = time.perf_counter() - start

    result["total_elapsed_seconds"] = round(total_elapsed, 2)
    result["evaluator"] = "simpleqa"
    result["num_questions"] = len(questions)

    # 输出报告 (文件 I/O 轻量, 项目用 asyncio 非 trio/anyio, noqa ASYNC240)
    output.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    ts = time.strftime("%Y%m%d_%H%M%S")
    report_path = output / f"simpleqa_report_{ts}.json"
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # 控制台摘要
    print("\n=== SimpleQA 评测报告 ===")
    print(f"问题总数:   {result['total']}")
    print(f"正确数:     {result['correct']}")
    print(f"准确率:     {result['accuracy']:.2%}")
    print(f"平均完整性: {result['avg_completeness']:.2%}")
    print(f"平均响应时间: {result['avg_response_time']:.2f}s")
    print(f"总耗时:     {result['total_elapsed_seconds']:.2f}s")
    print(f"报告已写入: {report_path}")


def main() -> None:
    """命令行入口."""
    parser = argparse.ArgumentParser(description="SimpleQA 评测运行器")
    parser.add_argument(
        "--num-questions",
        type=int,
        default=0,
        help="评测问题数量 (0=全部)",
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
            num_questions=args.num_questions,
            output=args.output,
            base_url=args.base_url,
            authorization=args.authorization,
        )
    )


if __name__ == "__main__":
    main()
