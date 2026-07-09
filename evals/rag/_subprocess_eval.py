"""RAGAS 子进程评测脚本 (隔离事件循环, 修复 Python 3.14 + nest_asyncio 冲突).

从 stdin 读取 JSON: {"question":..., "answer":..., "contexts":[...], "reference":...}
向 stdout 输出 JSON: {"scores":{...}, "errors":{...}}

绕过 RAGAS Executor (使用 nest_asyncio, 与 Python 3.14 asyncio.timeout 冲突),
直接调用 metric._single_turn_ascore() 在干净的 asyncio.run() 上下文中运行.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path


def main() -> None:
    # 读取输入
    data = json.loads(sys.stdin.read())
    question = data["question"]
    answer = data["answer"]
    contexts = data["contexts"]
    reference = data["reference"]

    # 添加项目根目录到 sys.path (确保 evals 模块可导入)
    # parents[0]=rag/, parents[1]=evals/, parents[2]=项目根目录
    project_root = Path(__file__).resolve().parents[2]
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)

    # 加载环境变量 (override=True 确保 .env.qa 的 EVAL_* 配置优先生效)
    from dotenv import load_dotenv

    for env_file in [".env.qa", ".env"]:
        p = project_root / env_file
        if p.exists():
            load_dotenv(p, override=True)
            break

    # 保存原始 asyncio 方法 (在 import ragas 之前)
    from evals.rag._asyncio_fix import restore_original_asyncio, save_original_asyncio

    saved_asyncio = save_original_asyncio()

    # 修复 langchain_community 0.4+ 兼容性 (在 import ragas 之前)
    import evals.rag._compat_shim  # noqa: F401

    # 构建 RAGAS 评估器 (import ragas 会触发 nest_asyncio.apply())
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import SingleTurnSample
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import (
        AnswerRelevancy,
        Faithfulness,
        LLMContextPrecisionWithReference,
    )

    # 恢复原始 asyncio 方法 (撤销 nest_asyncio 补丁, 修复 Python 3.14 兼容性)
    restore_original_asyncio(saved_asyncio)

    model = os.getenv("EVAL_LLM_MODEL", "deepseek/deepseek-chat")
    api_key = os.getenv("EVAL_LLM_API_KEY", os.getenv("LLM_API_KEY", ""))
    api_base = os.getenv("EVAL_LLM_API_BASE")

    llm_kwargs: dict = {"model": model, "api_key": api_key, "temperature": 0.0}
    if api_base:
        llm_kwargs["base_url"] = api_base
    llm = ChatOpenAI(**llm_kwargs)
    evaluator_llm = LangchainLLMWrapper(llm)

    embedding_model = os.getenv("EVAL_EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5")
    embedding_base = os.getenv("EVAL_EMBEDDING_API_BASE")
    embedding_key = os.getenv("EVAL_EMBEDDING_API_KEY", os.getenv("EMBEDDINGS_API_KEY", "dummy"))
    emb_kwargs: dict = {"model": embedding_model, "api_key": embedding_key}
    if embedding_base:
        emb_kwargs["base_url"] = embedding_base
    embeddings = OpenAIEmbeddings(**emb_kwargs)
    evaluator_embeddings = LangchainEmbeddingsWrapper(embeddings)

    sample = SingleTurnSample(
        user_input=question,
        response=answer,
        retrieved_contexts=contexts,
        reference=reference,
    )

    metrics = {
        "faithfulness": Faithfulness(),
        "answer_relevancy": AnswerRelevancy(),
        "context_precision": LLMContextPrecisionWithReference(),
    }

    # 初始化 metrics (LLM/embeddings 注入)
    for metric in metrics.values():
        metric.llm = evaluator_llm
        metric.embeddings = evaluator_embeddings

    async def run_metrics() -> dict:
        scores: dict[str, float] = {}
        errors: dict[str, str] = {}
        for name, metric in metrics.items():
            try:
                # 直接调用 _single_turn_ascore, 绕过 Executor (nest_asyncio 冲突)
                score = await metric._single_turn_ascore(sample, {})  # type: ignore[attr-defined]
                import math

                if score is None or (isinstance(score, float) and math.isnan(score)):
                    scores[name] = 0.0
                    errors[name] = "metric returned NaN"
                else:
                    scores[name] = float(score)
            except Exception as e:
                err_msg = str(e)
                if hasattr(e, "exceptions"):
                    sub_msgs = [f"{type(sub).__name__}: {sub}" for sub in e.exceptions]
                    err_msg = " | ".join(sub_msgs)
                scores[name] = 0.0
                errors[name] = err_msg[:500]
        return {"scores": scores, "errors": errors}

    # 在干净的 asyncio.run() 上下文中运行 (子进程无已有事件循环)
    result = asyncio.run(run_metrics())
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
