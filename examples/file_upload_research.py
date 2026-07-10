"""文件上传 + 基于文件研究.

用途:
    演示完整流程: 上传文件 → 引用文件 ID 做研究 → 获取基于文件内容的报告.
    适合需要对私有文档 (PDF/Word/Markdown 等) 进行分析总结的场景.

前置条件:
    1. 启动容器栈: docker compose -p agentinsight up -d
    2. 等待健康检查通过: curl http://localhost:8066/health

运行:
    python examples/file_upload_research.py <文件路径>
    python examples/file_upload_research.py ./data/industry_report.pdf

    若不传文件路径, 会自动创建一个示例 txt 文件用于演示.

环境变量:
    AGENT_BASE_URL   - API 基础 URL, 默认 http://localhost:8066/v1
    AGENT_JWT_TOKEN  - Bearer JWT Token, 可选

支持的文件类型:
    pdf / docx / md / txt / html / csv / xlsx / pptx
    大小限制: MAX_UPLOAD_SIZE_MB (默认 50MB)

流程:
    1. POST /v1/files (multipart/form-data) 上传文件, 拿到 file_id
    2. POST /v1/chat/completions (流式) 带 uploaded_files=[file_id] 做研究
    3. 流式打印基于文件内容生成的研究报告
"""

import json
import os
import sys
from pathlib import Path

import httpx

BASE_URL = os.getenv("AGENT_BASE_URL", "http://localhost:8066/v1")
JWT_TOKEN = os.getenv("AGENT_JWT_TOKEN", "")


def build_headers(json_content: bool = True) -> dict[str, str]:
    headers: dict[str, str] = {}
    if json_content:
        headers["Content-Type"] = "application/json"
    if JWT_TOKEN:
        headers["Authorization"] = f"Bearer {JWT_TOKEN}"
    return headers


def ensure_sample_file(file_arg: str | None) -> Path:
    """若未传文件路径, 创建示例 txt 文件."""
    if file_arg:
        path = Path(file_arg)
        if not path.exists():
            print(f"[错误] 文件不存在: {path}")
            sys.exit(1)
        return path

    # 创建示例文件
    sample = Path("sample_research_data.txt")
    sample.write_text(
        """2026 年 AI Agent 行业调研数据

1. 市场规模: 据 Gartner 预测, 2026 年全球 AI Agent 市场规模将达到 500 亿美元,
   年复合增长率 (CAGR) 约 35%.

2. 代表厂商:
   - 国外: OpenAI (GPT Agents), Anthropic (Claude), Google (Gemini Agents)
   - 国内: 智谱 (ChatGLM), 深度求索 (DeepSeek), 阿里 (通义千问 Agent)

3. 技术趋势:
   - 多 Agent 协作成为主流架构 (Supervisor / Swarm / Hierarchical)
   - MCP (Model Context Protocol) 成为工具协议事实标准
   - 长上下文 + 状态机编排 (LangGraph) 替代简单 ReAct 循环

4. 挑战:
   - 幻觉问题尚未完全解决, 事实核查成为标配
   - 企业级安全与数据隔离需求提升
   - 成本控制: 多 Agent 流水线 token 消耗显著
""",
        encoding="utf-8",
    )
    print(f"[示例] 已创建示例文件: {sample.resolve()}")
    return sample


def upload_file(client: httpx.Client, file_path: Path) -> str:
    """上传文件, 返回 file_id."""
    print(f"[1/2] 上传文件: {file_path.name} ({file_path.stat().st_size / 1024:.1f} KB)")

    # multipart 上传, 注意不要预设 Content-Type (httpx 会自动设置 boundary)
    headers: dict[str, str] = {}
    if JWT_TOKEN:
        headers["Authorization"] = f"Bearer {JWT_TOKEN}"

    with file_path.open("rb") as f:
        response = client.post(
            f"{BASE_URL}/files",
            headers=headers,
            files={"file": (file_path.name, f, "application/octet-stream")},
            timeout=120,
        )
    response.raise_for_status()
    data = response.json()

    file_id = data["file_id"]
    print(f"      file_id = {file_id}")
    print(f"      扩展名: {data.get('extension')}, 大小: {data.get('size_mb')} MB")
    return file_id


def research_with_file(client: httpx.Client, file_id: str, query: str) -> None:
    """基于上传的文件做研究, 流式打印报告."""
    print("\n[2/2] 基于文件研究 (流式)")
    print(f"      查询: {query}")
    print("=" * 60 + "\n")

    payload = {
        "model": "agentinsight-researcher",
        "messages": [{"role": "user", "content": query}],
        "stream": True,
        "report_type": "basic_report",
        "report_format": "markdown",
        "uploaded_files": [file_id],  # 引用已上传文件
    }

    sources: list[dict] = []
    report_id: str | None = None

    with client.stream(
        "POST",
        f"{BASE_URL}/chat/completions",
        headers=build_headers(),
        json=payload,
        timeout=300,
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
                print(content, end="", flush=True)
            if srcs := delta.get("sources"):
                sources.extend(srcs)
            if rid := delta.get("report_id"):
                report_id = rid

    print("\n" + "=" * 60)
    if sources:
        print(f"检索来源 ({len(sources)} 条, 含文件内容):")
        for i, src in enumerate(sources[:5], 1):
            print(f"  {i}. {src.get('title', '')} (score={src.get('score', 0):.2f})")
    if report_id:
        print(f"\n报告 ID: {report_id}")


def main() -> None:
    # 命令行参数: 可选文件路径
    file_arg = sys.argv[1] if len(sys.argv) > 1 else None
    file_path = ensure_sample_file(file_arg)

    with httpx.Client() as client:
        # 步骤 1: 上传文件
        file_id = upload_file(client, file_path)

        # 步骤 2: 基于文件内容研究
        # 提示词明确引用上传文件, Agent 会读取文件内容作为研究数据源
        query = "基于上传的文件内容, 总结 AI Agent 行业的核心趋势与挑战, 并补充最新市场动态"
        research_with_file(client, file_id, query)


if __name__ == "__main__":
    main()
