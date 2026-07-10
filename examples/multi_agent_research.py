"""多 Agent 协作模式 (deep_research).

用途:
    演示 report_type="deep_research" + multi_agent=True, 触发多 Agent 协作流水线.
    deep_research 会启动 Supervisor 编排多个子 Agent:
        Researcher → Writer → FactChecker → Reviewer → Reviser → Visualizer → Publisher
    生成最深度的研究报告, 适合需要严格事实核查与多轮修订的场景.

前置条件:
    1. 启动容器栈: docker compose -p agentinsight up -d
    2. 等待健康检查通过: curl http://localhost:8066/health

运行:
    python examples/multi_agent_research.py

环境变量:
    AGENT_BASE_URL   - API 基础 URL, 默认 http://localhost:8066/v1
    AGENT_JWT_TOKEN  - Bearer JWT Token, 可选

注意:
    deep_research 生成时间最长 (通常 3-10 分钟), 涉及多轮 LLM 调用与事实核查.
    timeout 设为 900s (15 分钟) 留足余量. 请耐心等待.
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
        "messages": [
            {
                "role": "user",
                # 复杂主题, 适合多 Agent 深度研究
                "content": "深度研究: 大语言模型在医疗诊断中的应用现状、技术挑战与监管框架, 需事实核查与多角度论证",
            }
        ],
        "stream": True,
        "report_type": "deep_research",  # 深度研究 (触发多 Agent 流水线)
        "multi_agent": True,  # 显式启用多 Agent Supervisor 模式
        "report_format": "markdown",
        "tone": "objective",
        # 自定义行业 persona (优先级高于 LLM 自动生成)
        "agent_role": "医疗 AI 领域资深研究员",
    }

    print("=" * 60)
    print("多 Agent 协作深度研究 (deep_research)")
    print("主题: 大语言模型在医疗诊断中的应用")
    print("流水线: Researcher → Writer → FactChecker → Reviewer → Reviser → Publisher")
    print("=" * 60 + "\n")

    full_content: list[str] = []
    sources: list[dict] = []
    report_id: str | None = None
    tool_calls: list[dict] = []

    with httpx.stream(
        "POST",
        f"{BASE_URL}/chat/completions",
        headers=headers,
        json=payload,
        timeout=900,  # deep_research 最慢, 放宽到 15 分钟
    ) as response:
        response.raise_for_status()

        for line in response.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            data_str = line[len("data: ") :]

            if data_str == "[DONE]":
                break

            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            choices = chunk.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})

            # 正文内容
            if content := delta.get("content"):
                full_content.append(content)
                print(content, end="", flush=True)

            # 检索来源
            if srcs := delta.get("sources"):
                sources.extend(srcs)

            # 工具调用 (MCP 工具触发时推送)
            if tc := delta.get("tool_calls"):
                tool_calls.extend(tc)

            # 报告 ID
            if rid := delta.get("report_id"):
                report_id = rid

    # 汇总统计
    report_text = "".join(full_content)
    print("\n" + "=" * 60)
    print("多 Agent 研究汇总")
    print("=" * 60)
    print(f"报告字数: {len(report_text)} 字符")
    print(f"检索来源: {len(sources)} 条")
    print(f"工具调用: {len(tool_calls)} 次")
    if sources:
        print("\n来源 Top 5:")
        # 按 score 降序取前 5
        for i, src in enumerate(
            sorted(sources, key=lambda x: x.get("score", 0), reverse=True)[:5], 1
        ):
            print(f"  {i}. [{src.get('score', 0):.2f}] {src.get('title', '')}")
            print(f"     {src.get('url', '')}")
    if report_id:
        print(f"\n报告 ID: {report_id}")
        print(f"DOCX 下载: {BASE_URL}/reports/{report_id}/download?format=docx")


if __name__ == "__main__":
    main()
