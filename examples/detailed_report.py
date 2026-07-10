"""多章节深度报告 (detailed_report).

用途:
    演示 report_type="detailed_report", 获取多章节深度研究报告.
    detailed_report 会将复杂主题拆解为多个章节, 每章节独立检索 + 生成,
    最终汇总为完整报告, 适合需要全面分析的场景.

前置条件:
    1. 启动容器栈: docker compose -p agentinsight up -d
    2. 等待健康检查通过: curl http://localhost:8066/health

运行:
    python examples/detailed_report.py

环境变量:
    AGENT_BASE_URL   - API 基础 URL, 默认 http://localhost:8066/v1
    AGENT_JWT_TOKEN  - Bearer JWT Token, 可选

注意:
    detailed_report 生成时间较长 (通常 2-5 分钟), 请耐心等待.
    timeout 设为 600s 留足余量.
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
                "content": "深度分析 2026 年全球半导体产业链格局, 涵盖设计、制造、封测、设备、材料各环节",
            }
        ],
        "stream": True,
        "report_type": "detailed_report",  # 多章节深度报告
        "report_format": "markdown",
        "tone": "analytical",  # 分析型语气
        # agent_role: 自定义行业 persona (优先级高于 LLM 自动生成)
        # "agent_role": "半导体行业资深分析师",
    }

    print("=" * 60)
    print("多章节深度报告 (detailed_report)")
    print("主题: 2026 年全球半导体产业链格局")
    print("=" * 60 + "\n")

    full_content: list[str] = []
    sources: list[dict] = []
    report_id: str | None = None

    with httpx.stream(
        "POST",
        f"{BASE_URL}/chat/completions",
        headers=headers,
        json=payload,
        timeout=600,  # detailed_report 较慢, 放宽到 10 分钟
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

            if content := delta.get("content"):
                full_content.append(content)
                print(content, end="", flush=True)

            if srcs := delta.get("sources"):
                sources.extend(srcs)

            if rid := delta.get("report_id"):
                report_id = rid

    # 章节统计 (简单按 markdown 标题计数)
    report_text = "".join(full_content)
    h1_count = report_text.count("\n# ")
    h2_count = report_text.count("\n## ")

    print("\n" + "=" * 60)
    print("报告统计")
    print("=" * 60)
    print(f"总字数: {len(report_text)} 字符")
    print(f"一级标题 (#): {h1_count} 个")
    print(f"二级标题 (##): {h2_count} 个")
    print(f"检索来源: {len(sources)} 条")
    if report_id:
        print(f"报告 ID: {report_id}")
        print(f"PDF 下载: {BASE_URL}/reports/{report_id}/download?format=pdf")


if __name__ == "__main__":
    main()
