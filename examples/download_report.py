"""报告下载 (markdown / pdf / docx).

用途:
    演示完整流程: 生成报告 → 获取 report_id → 下载多种格式.
    agentinsight-researcher 支持实时格式转换, 同一报告可下载 5 种格式:
        markdown / html / pdf / docx / json

前置条件:
    1. 启动容器栈: docker compose -p agentinsight up -d
    2. 等待健康检查通过: curl http://localhost:8066/health

运行:
    python examples/download_report.py

环境变量:
    AGENT_BASE_URL   - API 基础 URL, 默认 http://localhost:8066/v1
    AGENT_JWT_TOKEN  - Bearer JWT Token, 可选

流程:
    1. POST /v1/chat/completions (非流式) 生成报告, 拿到 report_id
    2. GET /v1/reports/{report_id}/download?format=markdown 下载 Markdown
    3. GET /v1/reports/{report_id}/download?format=pdf 下载 PDF
    4. GET /v1/reports/{report_id}/download?format=docx 下载 DOCX
    5. 文件保存到当前目录 (或指定 output_dir)
"""

import os
import sys
from pathlib import Path

import httpx

BASE_URL = os.getenv("AGENT_BASE_URL", "http://localhost:8066/v1")
JWT_TOKEN = os.getenv("AGENT_JWT_TOKEN", "")
# 输出目录, 默认当前目录下的 reports_output
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "reports_output"))


def build_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if JWT_TOKEN:
        headers["Authorization"] = f"Bearer {JWT_TOKEN}"
    return headers


def generate_report(client: httpx.Client, query: str) -> str:
    """非流式生成报告, 返回 report_id."""
    print(f"[1/3] 生成报告: {query}")
    response = client.post(
        f"{BASE_URL}/chat/completions",
        headers=build_headers(),
        json={
            "model": "agentinsight-researcher",
            "messages": [{"role": "user", "content": query}],
            "stream": False,
            "report_type": "basic_report",
            "report_format": "markdown",
        },
        timeout=300,
    )
    response.raise_for_status()
    data = response.json()

    report_id = data.get("report_id")
    if not report_id:
        # 兜底: 打印内容并退出
        print("[警告] 响应未包含 report_id, 无法下载. 报告内容如下:")
        print(data["choices"][0]["message"]["content"])
        sys.exit(0)

    content_preview = data["choices"][0]["message"]["content"][:200]
    print(f"      report_id = {report_id}")
    print(f"      内容预览: {content_preview}...")
    return report_id


def download_report(client: httpx.Client, report_id: str, fmt: str) -> Path:
    """下载指定格式的报告, 返回保存路径."""
    print(f"  → 下载 {fmt.upper()} 格式 ...")
    response = client.get(
        f"{BASE_URL}/reports/{report_id}/download",
        params={"format": fmt},
        headers=build_headers(),
        timeout=120,
    )
    response.raise_for_status()

    # 从 Content-Disposition 解析文件名, 兜底用 report_id
    content_disposition = response.headers.get("content-disposition", "")
    filename = f"report_{report_id}.{fmt}"
    if "filename=" in content_disposition:
        # 形如: attachment; filename=report_xxx.md
        filename = content_disposition.split("filename=")[-1].strip().strip('"')

    output_path = OUTPUT_DIR / filename
    output_path.write_bytes(response.content)
    print(f"    已保存: {output_path} ({len(response.content) / 1024:.1f} KB)")
    return output_path


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with httpx.Client() as client:
        # 步骤 1: 生成报告
        report_id = generate_report(client, "调研 2026 年中国 AI 大模型行业竞争格局与代表厂商")

        # 步骤 2: 下载多种格式
        print(f"\n[2/3] 下载报告 (report_id={report_id})")
        saved_files: list[Path] = []
        for fmt in ("markdown", "pdf", "docx"):
            try:
                saved_files.append(download_report(client, report_id, fmt))
            except httpx.HTTPStatusError as e:
                # PDF 生成可能因系统依赖缺失失败, 不阻断其他格式
                print(f"    [失败] {fmt}: HTTP {e.response.status_code} - {e.response.text[:200]}")

        # 步骤 3: 汇总
        print("\n[3/3] 下载完成")
        print("=" * 60)
        print(f"输出目录: {OUTPUT_DIR.resolve()}")
        print(f"成功下载: {len(saved_files)} 个文件")
        for f in saved_files:
            print(f"  - {f.name}")


if __name__ == "__main__":
    main()
