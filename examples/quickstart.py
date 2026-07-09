"""快速开始: 非流式研究报告生成.

用途:
    最简示例, 10 行核心代码即可生成一份研究报告. 适合首次接触本项目的用户.

前置条件:
    1. 启动容器栈: docker compose -p agentinsight up -d
    2. 等待健康检查通过: curl http://localhost:8066/health
    3. (可选) 配置 .env 中的 LLM / 搜索引擎 API Key

运行:
    python examples/quickstart.py

环境变量:
    AGENT_BASE_URL   - API 基础 URL, 默认 http://localhost:8066/v1
    AGENT_JWT_TOKEN  - Bearer JWT Token, 可选 (SELF_HOST=True 时留空走匿名用户)
"""

import os

import httpx

BASE_URL = os.getenv("AGENT_BASE_URL", "http://localhost:8066/v1")
JWT_TOKEN = os.getenv("AGENT_JWT_TOKEN", "")


def main() -> None:
    # 构造请求头 (JWT Token 可选)
    headers = {"Content-Type": "application/json"}
    if JWT_TOKEN:
        headers["Authorization"] = f"Bearer {JWT_TOKEN}"

    response = httpx.post(
        f"{BASE_URL}/chat/completions",
        headers=headers,
        json={
            "model": "agentinsight-researcher",
            "messages": [{"role": "user", "content": "分析2026年中国新能源汽车市场格局"}],
            "stream": False,
            # 可选: 指定报告类型 / 格式 / 语气
            # "report_type": "basic_report",
            # "report_format": "markdown",
            # "tone": "analytical",
        },
        timeout=300,  # 研究报告生成可能较慢, 留足余量
    )
    response.raise_for_status()
    data = response.json()

    # 打印报告正文
    print("=" * 60)
    print("研究报告")
    print("=" * 60)
    print(data["choices"][0]["message"]["content"])

    # 打印元信息 (sources / report_id / 成本)
    print("\n" + "=" * 60)
    print("元信息")
    print("=" * 60)
    if sources := data.get("sources"):
        print(f"检索来源 ({len(sources)} 条):")
        for i, src in enumerate(sources, 1):
            print(f"  {i}. {src.get('title', '')} - {src.get('url', '')}")
    if report_id := data.get("report_id"):
        print(f"报告 ID: {report_id}")
    if usage := data.get("usage"):
        print(f"Token 用量: {usage.get('total_tokens')} (成本 ${usage.get('cost_usd', 0):.4f})")


if __name__ == "__main__":
    main()
