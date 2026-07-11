"""OpenAI Python SDK 兼容调用.

用途:
    使用官方 openai SDK 调用 agentinsight-researcher, 证明 OpenAI 兼容性.
    任何已接入 OpenAI SDK 的客户端, 只需替换 base_url 即可无缝切换到本 Agent.

前置条件:
    1. 启动容器栈: docker compose -p agentinsight up -d
    2. 等待健康检查通过: curl http://localhost:8066/health
    3. 安装 OpenAI SDK: pip install openai

运行:
    python examples/openai_sdk_compatible.py

环境变量:
    AGENT_BASE_URL   - API 基础 URL, 默认 http://localhost:8066/v1
    AGENT_JWT_TOKEN  - Bearer JWT Token, 可选 (作为 api_key 传入)

说明:
    - SELF_HOST=True (默认) 时 api_key 可为任意字符串, 不校验
    - SELF_HOST=False 时 api_key 应为真实 JWT Token
    - 报告类型 / 格式等非 OpenAI 标准字段通过 extra_body 传入
"""

import os

from openai import OpenAI

BASE_URL = os.getenv("AGENT_BASE_URL", "http://localhost:8066/v1")
# JWT Token 可选; SELF_HOST=True 时可为任意字符串
JWT_TOKEN = os.getenv("AGENT_JWT_TOKEN", "any-string-not-used-in-self-host-mode")


def main() -> None:
    # 用 OpenAI SDK 构造客户端, 仅替换 base_url + api_key
    client = OpenAI(
        base_url=BASE_URL,
        api_key=JWT_TOKEN,
        timeout=300,  # 研究报告生成可能较慢
    )

    # ========== 1. 非流式调用 ==========
    print("=" * 60)
    print("1. 非流式调用 (OpenAI SDK)")
    print("=" * 60)

    resp = client.chat.completions.create(
        model="agentinsight-researcher",
        messages=[{"role": "user", "content": "分析 2026 年中国新能源汽车市场格局"}],
        stream=False,
        # 非 OpenAI 标准字段经 extra_body 传入
        extra_body={
            "report_type": "basic_report",
            "report_format": "markdown",
            "tone": "analytical",
        },
    )

    print(resp.choices[0].message.content)

    # 扩展字段 (sources / report_id) 经 model_extra 字典访问
    # OpenAI SDK 严格模式下扩展字段不在顶层属性, 需通过 model_extra 获取
    print("\n--- 扩展元信息 ---")
    extra = resp.model_extra or {}
    sources = extra.get("sources") or []
    if sources:
        print(f"检索来源 ({len(sources)} 条):")
        for i, src in enumerate(sources, 1):
            print(f"  {i}. {src.get('title', '')} - {src.get('url', '')}")
    if extra.get("report_id"):
        print(f"报告 ID: {extra['report_id']}")
    if resp.usage:
        print(f"Token 用量: {resp.usage.total_tokens}")

    # ========== 2. 流式调用 ==========
    print("\n" + "=" * 60)
    print("2. 流式调用 (OpenAI SDK)")
    print("=" * 60 + "\n")

    stream = client.chat.completions.create(
        model="agentinsight-researcher",
        messages=[{"role": "user", "content": "对比 React 与 Vue 3 在企业级应用的优劣"}],
        stream=True,
        extra_body={"report_type": "detailed_report"},
    )

    report_id: str | None = None
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        # 正文内容逐块打印
        if delta.content:
            print(delta.content, end="", flush=True)

        # 扩展字段 (SDK 解析为 ModelExtra, 通过 model_extra 字典访问)
        extra = delta.model_extra or {}
        if rid := extra.get("report_id"):
            report_id = rid

    print("\n")
    if report_id:
        print(f"报告 ID: {report_id}")


if __name__ == "__main__":
    main()
