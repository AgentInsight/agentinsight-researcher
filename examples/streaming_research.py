"""流式研究报告生成 (SSE 逐块打印).

用途:
    演示如何通过 httpx 流式请求 SSE 响应, 实时逐块打印报告内容.
    流式模式用户体验更好, 无需等待完整报告生成即可看到进度.

前置条件:
    1. 启动容器栈: docker compose -p agentinsight up -d
    2. 等待健康检查通过: curl http://localhost:8066/health

运行:
    python examples/streaming_research.py

环境变量:
    AGENT_BASE_URL   - API 基础 URL, 默认 http://localhost:8066/v1
    AGENT_JWT_TOKEN  - Bearer JWT Token, 可选

SSE 帧格式:
    data: {"choices":[{"delta":{"content":"..."}}]}
    data: {"choices":[{"delta":{"sources":[...]}}]}
    data: {"choices":[{"delta":{"report_id":"..."}}]}
    data: [DONE]
"""

import json
import os

import httpx

BASE_URL = os.getenv("AGENT_BASE_URL", "http://localhost:8066/v1")
JWT_TOKEN = os.getenv("AGENT_JWT_TOKEN", "")


def main() -> None:
    headers = {"Content-Type": "application/json"}
    if JWT_TOKEN:
        headers["Authorization"] = f"Bearer {JWT_TOKEN}"

    payload = {
        "model": "agentinsight-researcher",
        "messages": [{"role": "user", "content": "对比 React 与 Vue 3 在企业级应用的优劣"}],
        "stream": True,
        "report_type": "basic_report",
        "report_format": "markdown",
    }

    print("=" * 60)
    print("流式研究报告 (SSE)")
    print("=" * 60 + "\n")

    sources: list[dict] = []
    report_id: str | None = None

    # 使用 stream 上下文管理器逐行读取 SSE
    with httpx.stream(
        "POST",
        f"{BASE_URL}/chat/completions",
        headers=headers,
        json=payload,
        timeout=300,
    ) as response:
        response.raise_for_status()

        for line in response.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            data_str = line[len("data: ") :]

            # 结束标记
            if data_str == "[DONE]":
                print("\n\n[流式结束]")
                break

            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            choices = chunk.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})

            # 1. 正文内容 (逐块追加打印)
            if content := delta.get("content"):
                print(content, end="", flush=True)

            # 2. 检索来源 (可能在流中途推送)
            if srcs := delta.get("sources"):
                sources.extend(srcs)

            # 3. 报告 ID (流末推送)
            if rid := delta.get("report_id"):
                report_id = rid

            # 4. 结束原因
            if choices[0].get("finish_reason") == "stop":
                continue  # 等待 [DONE]

    # 打印汇总信息
    print("\n" + "=" * 60)
    print("汇总")
    print("=" * 60)
    if sources:
        print(f"检索来源 ({len(sources)} 条):")
        for i, src in enumerate(sources, 1):
            print(f"  {i}. {src.get('title', '')} (score={src.get('score', 0):.2f})")
            print(f"     {src.get('url', '')}")
    if report_id:
        print(f"\n报告 ID: {report_id}")
        print(f"下载地址: {BASE_URL}/reports/{report_id}/download?format=markdown")


if __name__ == "__main__":
    main()
