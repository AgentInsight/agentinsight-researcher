"""命令行入口 (P1-06).

提供 ``python -m src.cli`` 命令行研究入口, 复用 LangGraph 研究流水线.

用法示例::

    python -m src.cli -q "研究问题" -m detailed -f markdown -o report.md
    python -m src.cli --query "..." --mode deep --breadth 4 --depth 3 --format html
    python -m src.cli --help

AGENTS.md 硬约束:
- 第 4 章: 禁用 langchain 全家桶 (仅 langchain-core), 禁用 AgentExecutor
- 第 5 章: 唯一编排范式为 LangGraph StateGraph, 直接 await graph.ainvoke
- 第 9 章: 所有 LLM 调用经 LLMClient (LiteLLM), 禁止厂商 SDK
- 第 10 章: trace_agent 包裹 graph.ainvoke 作为根 span
- 第 8 章: CLI 无 HTTP 中间件, user_id 取固定 "cli-user" (无 IP 上下文)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from src.api.routes import _get_graph
from src.config.settings import Settings, get_settings
from src.observability.tracing import trace_agent

logger = logging.getLogger(__name__)

# CLI 默认用户 ID (CLI 无 HTTP 中间件, 无客户端 IP, 用固定标识)
_CLI_USER_ID = "cli-user"

# 研究模式 → 报告类型映射
# research_mode (State): basic|detailed|quick|sources|deep|summary|subtopics
# report_type  (State): basic_report|detailed_report|deep_research|summary|subtopics
_MODE_TO_REPORT_TYPE: dict[str, str] = {
    "basic": "basic_report",
    "detailed": "detailed_report",
    "quick": "basic_report",
    "sources": "basic_report",
    "deep": "deep_research",
    "summary": "summary",
    "subtopics": "subtopics",
}

_VALID_MODES: list[str] = list(_MODE_TO_REPORT_TYPE.keys())
_VALID_FORMATS: list[str] = ["markdown", "html", "pdf", "docx", "json"]
_VALID_TONES: list[str] = ["objective", "analytical", "opinionated", "casual"]


def _build_parser() -> argparse.ArgumentParser:
    """构造 argparse 参数解析器."""
    parser = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="AgentInsight Researcher 命令行研究入口 (复用 LangGraph 流水线)",
    )
    parser.add_argument("-q", "--query", required=True, help="研究问题 (必填)")
    parser.add_argument(
        "-m",
        "--mode",
        default="basic",
        choices=_VALID_MODES,
        help="研究模式 (默认 basic): basic|detailed|quick|sources|deep|summary|subtopics",
    )
    parser.add_argument(
        "-f",
        "--format",
        default="markdown",
        choices=_VALID_FORMATS,
        help="输出格式 (默认 markdown): markdown|html|pdf|docx|json",
    )
    parser.add_argument("-o", "--output", default=None, help="输出文件路径, 未指定时打印到 stdout")
    parser.add_argument("--breadth", type=int, default=4, help="deep 模式广度 (默认 4)")
    parser.add_argument("--depth", type=int, default=2, help="deep 模式深度 (默认 2)")
    parser.add_argument(
        "--tone",
        default="objective",
        choices=_VALID_TONES,
        help="语气 (默认 objective): objective|analytical|opinionated|casual",
    )
    parser.add_argument(
        "--words",
        type=int,
        default=None,
        help="报告字数, 未指定用 settings 默认值 (settings.total_words)",
    )
    parser.add_argument(
        "--multi-agent",
        dest="multi_agent",
        action="store_true",
        help="启用多 Agent Supervisor 模式 (默认单图流水线)",
    )
    parser.add_argument(
        "--agent-role",
        dest="agent_role",
        default=None,
        help=(
            "设计参考 AGENT_ROLE 配置: 注入行业 persona 字符串, "
            "优先级高于 LLM 动态生成 (AgentCreator). "
            "行业适配采用 4 层机制, 不再使用行业分类器."
        ),
    )
    parser.add_argument(
        "--query-domains",
        dest="query_domains",
        default=None,
        help="域名过滤白名单 (逗号分隔, 如: arxiv.org,github.com), 仅检索这些域名",
    )
    parser.add_argument("--verbose", action="store_true", help="详细日志 (DEBUG 级别)")
    return parser


def _build_initial_state(
    *,
    query: str,
    mode: str,
    report_format: str,
    tone: str,
    breadth: int,
    depth: int,
    total_words: int,
    settings: Settings,
    agent_role: str | None = None,
    query_domains: list[str] | None = None,
) -> dict[str, Any]:
    """构造 LangGraph 初始 State.

    参考 src/api/routes.py 中 ChatRequest → initial_state 的转换逻辑.
    AGENTS.md 第 5 章: State 为 TypedDict, 节点返回 delta 由 reducer 合并.
    AGENTS.md 第 8 章: CLI 无 HTTP 中间件, user_id 取固定 "cli-user".
    """
    session_id = str(uuid.uuid4())
    report_type = _MODE_TO_REPORT_TYPE[mode]
    return {
        # 请求上下文
        "query": query,
        "session_id": session_id,
        "user_id": _CLI_USER_ID,
        "agent_id": settings.agent_name,
        # 报告配置
        "report_type": report_type,
        "report_format": report_format,
        "tone": tone,
        "total_words": total_words,
        # 设计参考: agent_role (来自 --agent-role 或 settings) 优先级高于 LLM 动态生成
        "agent_role": agent_role or settings.agent_role or "",
        "agent_role_server": "",
        # P1-Future-02: 域名过滤白名单
        "query_domains": query_domains or [],
        # 研究流程占位
        "messages": [],
        "sub_queries": [],
        "contexts": [],
        "sources": [],
        "visited_urls": set[str](),
        "curated_sources": [],
        # 输出占位
        "report_md": "",  # deprecated: 兼容期保留, 新代码用 report_formats
        "report_formats": {},  # P2-1: {md|html|pdf|docx|json: 内容或路径}
        "status": "pending",
        # 深度研究配置
        "research_mode": mode,
        "deep_research_breadth": breadth,
        "deep_research_depth": depth,
        # 多 Agent 迭代计数器 (Annotated[int, operator.add] 累加)
        "iteration_count": 0,
    }


def _emit_result(
    final_state: dict[str, Any],
    fmt: str,
    output_path: str | None,
) -> int:
    """按格式输出研究结果, 返回退出码.

    - markdown/html/json: 文本, 写文件 (-o) 或 stdout
    - pdf: 提示 PDF 路径; -o 时复制到目标路径
    - docx: 二进制, -o 写文件; 无 -o 则提示需指定路径

    P2-1: 优先从 report_formats 读取, 兼容期回退旧字段 (report_pdf_path/report_docx).
    """
    # P2-1: 优先从 report_formats 读取, 兼容期回退旧字段
    formats = final_state.get("report_formats") or {}

    if fmt == "pdf":
        pdf_path = str(formats.get("pdf") or final_state.get("report_pdf_path", "") or "")
        if not pdf_path:
            print("警告: 未生成 PDF 路径", file=sys.stderr)
            return 1
        if output_path:
            src = Path(pdf_path)
            if not src.exists():
                print(f"错误: PDF 源文件不存在: {pdf_path}", file=sys.stderr)
                return 1
            shutil.copyfile(src, Path(output_path))
            print(f"PDF 已复制到: {output_path}")
        else:
            print(pdf_path)
        return 0

    if fmt == "docx":
        data = formats.get("docx") or final_state.get("report_docx", b"")
        if not isinstance(data, (bytes, bytearray)):
            data = str(data).encode("utf-8")
        if output_path:
            Path(output_path).write_bytes(bytes(data))
            print(f"DOCX 已写入: {output_path}")
            return 0
        # 二进制无法安全打印到 stdout, 直接写 stdout 缓冲区
        sys.stdout.flush()
        sys.stdout.buffer.write(bytes(data))
        sys.stdout.buffer.flush()
        return 0

    # 文本类: markdown / html / json (P2-1: 优先 report_formats, 兼容旧字段)
    field_map = {"markdown": "md", "html": "html", "json": "json"}
    key = field_map[fmt]
    content = str(
        formats.get(key)
        or final_state.get(f"report_{key}", "")
        or (final_state.get("report_md", "") if key == "md" else "")
        or ""
    )
    if output_path:
        Path(output_path).write_text(content, encoding="utf-8")
        print(f"已写入: {output_path}")
    else:
        print(content)
    return 0


async def main() -> int:
    """CLI 异步入口.

    AGENTS.md 第 10 章: 用 trace_agent 包裹 graph.ainvoke 作为根 span.
    AGENTS.md 第 5 章: 直接 await graph.ainvoke, 不走 HTTP.
    """
    parser = _build_parser()
    args = parser.parse_args()

    # 日志配置 (verbose 时 DEBUG, 否则 WARNING 保持 stdout 干净)
    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    settings = get_settings()

    query: str = args.query
    mode: str = args.mode
    fmt: str = args.format
    output_path: str | None = args.output
    breadth: int = args.breadth
    depth: int = args.depth
    tone: str = args.tone
    words: int | None = args.words
    multi_agent: bool = args.multi_agent
    agent_role: str | None = args.agent_role
    # P1-Future-02: 解析域名过滤白名单 (逗号分隔)
    query_domains_str: str | None = args.query_domains
    query_domains: list[str] | None = None
    if query_domains_str:
        query_domains = [d.strip() for d in query_domains_str.split(",") if d.strip()]

    total_words = words if words is not None else settings.total_words

    initial_state = _build_initial_state(
        query=query,
        mode=mode,
        report_format=fmt,
        tone=tone,
        breadth=breadth,
        depth=depth,
        total_words=total_words,
        settings=settings,
        agent_role=agent_role,
        query_domains=query_domains,
    )
    session_id = str(initial_state["session_id"])
    graph_config: dict[str, Any] = {"configurable": {"thread_id": session_id}}

    logger.debug(
        "CLI 启动研究: query=%r mode=%s format=%s multi_agent=%s session_id=%s",
        query[:80],
        mode,
        fmt,
        multi_agent,
        session_id,
    )

    try:
        async with trace_agent(
            name="agentinsight-researcher",
            input={"query": query[:200], "session_id": session_id},
            metadata={
                "session_id": session_id,
                "intent": "research",
                "user_id": _CLI_USER_ID,
            },
            session_id=session_id,
            user_id=_CLI_USER_ID,
        ):
            graph = await _get_graph(multi_agent=multi_agent)
            final_state_raw = await graph.ainvoke(initial_state, config=graph_config)

        if not isinstance(final_state_raw, dict):
            print(f"错误: 图返回非 dict 结果: {type(final_state_raw).__name__}", file=sys.stderr)
            return 1

        return _emit_result(final_state_raw, fmt, output_path)
    except Exception as e:  # noqa: BLE001 - CLI 顶层兜底, 任意异常转退出码 1
        logger.exception("CLI 研究执行失败")
        print(f"错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
