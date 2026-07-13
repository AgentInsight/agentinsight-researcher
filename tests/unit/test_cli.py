"""单元测试: CLI 命令行入口.

验证 src/cli.py:
- _build_parser: argparse 参数解析 (必填 -q / 默认值 / choices 校验)
- _build_initial_state: 模式 → report_type 映射, session_id 生成, 默认值填充
- _emit_result: 各格式 (markdown/html/json/pdf/docx) 输出, 文件写入 vs stdout
- main: trace_agent 包裹 + graph.ainvoke 调用 + 异常兜底

CLI 无 HTTP 中间件, user_id 取固定 "cli-user".
单元测试不依赖外部服务 (graph/trace_agent/settings 全部 mock).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.cli import (
    _CLI_USER_ID,
    _VALID_FORMATS,
    _VALID_MODES,
    _VALID_TONES,
    _build_initial_state,
    _build_parser,
    _emit_result,
    main,
)
from src.config.settings import Settings

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture()
def settings() -> Settings:
    """构造最小 Settings (跳过 .env 加载)."""
    return Settings(_env_file=None, total_words=1500, agent_role=None)


# ========== _build_parser: argparse 参数解析 ==========


class TestBuildParser:
    """_build_parser 参数解析测试."""

    def test_parser_has_required_query_argument(self) -> None:
        """-q/--query 是必填参数."""
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])  # 缺少 -q 应抛 SystemExit

    def test_parser_query_short_form(self) -> None:
        """-q 短选项可解析."""
        parser = _build_parser()
        args = parser.parse_args(["-q", "研究问题"])
        assert args.query == "研究问题"

    def test_parser_query_long_form(self) -> None:
        """--query 长选项可解析."""
        parser = _build_parser()
        args = parser.parse_args(["--query", "研究问题"])
        assert args.query == "研究问题"

    def test_parser_default_mode_is_basic(self) -> None:
        """默认 mode=basic."""
        parser = _build_parser()
        args = parser.parse_args(["-q", "test"])
        assert args.mode == "basic"

    def test_parser_default_format_is_markdown(self) -> None:
        """默认 format=markdown."""
        parser = _build_parser()
        args = parser.parse_args(["-q", "test"])
        assert args.format == "markdown"

    def test_parser_default_tone_is_objective(self) -> None:
        """默认 tone=objective."""
        parser = _build_parser()
        args = parser.parse_args(["-q", "test"])
        assert args.tone == "objective"

    def test_parser_default_breadth_and_depth(self) -> None:
        """默认 breadth=4, depth=2."""
        parser = _build_parser()
        args = parser.parse_args(["-q", "test"])
        assert args.breadth == 4
        assert args.depth == 2

    def test_parser_default_output_is_none(self) -> None:
        """默认 output=None (打印到 stdout)."""
        parser = _build_parser()
        args = parser.parse_args(["-q", "test"])
        assert args.output is None

    def test_parser_default_words_is_none(self) -> None:
        """默认 words=None (用 settings.total_words)."""
        parser = _build_parser()
        args = parser.parse_args(["-q", "test"])
        assert args.words is None

    def test_parser_default_multi_agent_is_false(self) -> None:
        """默认 multi_agent=False."""
        parser = _build_parser()
        args = parser.parse_args(["-q", "test"])
        assert args.multi_agent is False

    def test_parser_multi_agent_flag_sets_true(self) -> None:
        """--multi-agent 启用多 Agent 模式."""
        parser = _build_parser()
        args = parser.parse_args(["-q", "test", "--multi-agent"])
        assert args.multi_agent is True

    def test_parser_verbose_flag_sets_true(self) -> None:
        """--verbose 启用 DEBUG 日志."""
        parser = _build_parser()
        args = parser.parse_args(["-q", "test", "--verbose"])
        assert args.verbose is True

    def test_parser_invalid_mode_rejected(self) -> None:
        """非法 mode 应被 choices 拒绝 (SystemExit)."""
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["-q", "test", "-m", "invalid_mode"])

    def test_parser_invalid_format_rejected(self) -> None:
        """非法 format 应被 choices 拒绝."""
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["-q", "test", "-f", "xml"])

    def test_parser_invalid_tone_rejected(self) -> None:
        """非法 tone 应被 choices 拒绝."""
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["-q", "test", "--tone", "invalid_tone"])

    def test_parser_all_valid_modes_accepted(self) -> None:
        """所有 _VALID_MODES 都应被接受."""
        parser = _build_parser()
        for mode in _VALID_MODES:
            args = parser.parse_args(["-q", "test", "-m", mode])
            assert args.mode == mode

    def test_parser_all_valid_formats_accepted(self) -> None:
        """所有 _VALID_FORMATS 都应被接受."""
        parser = _build_parser()
        for fmt in _VALID_FORMATS:
            args = parser.parse_args(["-q", "test", "-f", fmt])
            assert args.format == fmt

    def test_parser_all_valid_tones_accepted(self) -> None:
        """所有 _VALID_TONES 都应被接受."""
        parser = _build_parser()
        for tone in _VALID_TONES:
            args = parser.parse_args(["-q", "test", "--tone", tone])
            assert args.tone == tone

    def test_parser_agent_role_can_be_set(self) -> None:
        """--agent-role 注入行业 persona."""
        parser = _build_parser()
        args = parser.parse_args(["-q", "test", "--agent-role", "金融分析师"])
        assert args.agent_role == "金融分析师"

    def test_parser_query_domains_can_be_set(self) -> None:
        """--query-domains 设置域名过滤白名单."""
        parser = _build_parser()
        args = parser.parse_args(["-q", "test", "--query-domains", "arxiv.org,github.com"])
        assert args.query_domains == "arxiv.org,github.com"

    def test_parser_output_path_can_be_set(self) -> None:
        """-o 指定输出文件路径."""
        parser = _build_parser()
        args = parser.parse_args(["-q", "test", "-o", "report.md"])
        assert args.output == "report.md"

    def test_parser_words_can_be_set(self) -> None:
        """--words 设置报告字数."""
        parser = _build_parser()
        args = parser.parse_args(["-q", "test", "--words", "3000"])
        assert args.words == 3000


# ========== _build_initial_state: 初始 State 构造 ==========


class TestBuildInitialState:
    """_build_initial_state 初始 State 构造测试."""

    def test_state_contains_required_fields(self, settings: Settings) -> None:
        """State 应包含所有必要字段."""
        state = _build_initial_state(
            query="研究问题",
            mode="basic",
            report_format="markdown",
            tone="objective",
            breadth=4,
            depth=2,
            total_words=1500,
            settings=settings,
        )
        required_keys = {
            "query",
            "session_id",
            "user_id",
            "agent_id",
            "report_type",
            "report_format",
            "tone",
            "total_words",
            "agent_role",
            "query_domains",
            "messages",
            "sub_queries",
            "contexts",
            "sources",
            "visited_urls",
            "curated_sources",
            "report_md",
            "report_formats",
            "status",
            "research_mode",
            "deep_research_breadth",
            "deep_research_depth",
            "iteration_count",
        }
        assert required_keys.issubset(state.keys())

    def test_state_user_id_is_cli_user(self, settings: Settings) -> None:
        """CLI 固定 user_id 为 'cli-user'."""
        state = _build_initial_state(
            query="test",
            mode="basic",
            report_format="markdown",
            tone="objective",
            breadth=4,
            depth=2,
            total_words=1500,
            settings=settings,
        )
        assert state["user_id"] == _CLI_USER_ID
        assert state["user_id"] == "cli-user"

    def test_state_session_id_is_uuid_string(self, settings: Settings) -> None:
        """session_id 应为 UUID 字符串格式."""
        state = _build_initial_state(
            query="test",
            mode="basic",
            report_format="markdown",
            tone="objective",
            breadth=4,
            depth=2,
            total_words=1500,
            settings=settings,
        )
        session_id = state["session_id"]
        assert isinstance(session_id, str)
        assert len(session_id) == 36  # UUID4 字符长度

    def test_state_session_id_unique_each_call(self, settings: Settings) -> None:
        """每次调用生成不同 session_id."""
        kwargs = {
            "query": "test",
            "mode": "basic",
            "report_format": "markdown",
            "tone": "objective",
            "breadth": 4,
            "depth": 2,
            "total_words": 1500,
            "settings": settings,
        }
        state1 = _build_initial_state(**kwargs)
        state2 = _build_initial_state(**kwargs)
        assert state1["session_id"] != state2["session_id"]

    def test_state_agent_id_from_settings(self, settings: Settings) -> None:
        """agent_id 取自 settings.agent_name."""
        state = _build_initial_state(
            query="test",
            mode="basic",
            report_format="markdown",
            tone="objective",
            breadth=4,
            depth=2,
            total_words=1500,
            settings=settings,
        )
        assert state["agent_id"] == settings.agent_name

    @pytest.mark.parametrize(
        ("mode", "expected_report_type"),
        [
            ("basic", "basic_report"),
            ("detailed", "detailed_report"),
            ("quick", "basic_report"),
            ("sources", "basic_report"),
            ("deep", "deep_research"),
            ("summary", "summary"),
            ("subtopics", "subtopics"),
        ],
    )
    def test_state_mode_to_report_type_mapping(
        self,
        settings: Settings,
        mode: str,
        expected_report_type: str,
    ) -> None:
        """mode → report_type 映射应与 _MODE_TO_REPORT_TYPE 一致."""
        state = _build_initial_state(
            query="test",
            mode=mode,
            report_format="markdown",
            tone="objective",
            breadth=4,
            depth=2,
            total_words=1500,
            settings=settings,
        )
        assert state["report_type"] == expected_report_type

    def test_state_agent_role_from_explicit_arg(self, settings: Settings) -> None:
        """显式传入 agent_role 优先级最高."""
        state = _build_initial_state(
            query="test",
            mode="basic",
            report_format="markdown",
            tone="objective",
            breadth=4,
            depth=2,
            total_words=1500,
            settings=settings,
            agent_role="金融分析师",
        )
        assert state["agent_role"] == "金融分析师"

    def test_state_agent_role_from_settings_when_no_arg(self, settings: Settings) -> None:
        """无显式 agent_role 时回退 settings.agent_role."""
        custom_settings = Settings(_env_file=None, agent_role="默认行业角色")
        state = _build_initial_state(
            query="test",
            mode="basic",
            report_format="markdown",
            tone="objective",
            breadth=4,
            depth=2,
            total_words=1500,
            settings=custom_settings,
        )
        assert state["agent_role"] == "默认行业角色"

    def test_state_agent_role_empty_string_when_none(self, settings: Settings) -> None:
        """agent_role 无显式且 settings 也无 → 空字符串."""
        state = _build_initial_state(
            query="test",
            mode="basic",
            report_format="markdown",
            tone="objective",
            breadth=4,
            depth=2,
            total_words=1500,
            settings=settings,
        )
        assert state["agent_role"] == ""

    def test_state_query_domains_from_explicit_list(self, settings: Settings) -> None:
        """显式传入 query_domains."""
        domains = ["arxiv.org", "github.com"]
        state = _build_initial_state(
            query="test",
            mode="basic",
            report_format="markdown",
            tone="objective",
            breadth=4,
            depth=2,
            total_words=1500,
            settings=settings,
            query_domains=domains,
        )
        assert state["query_domains"] == domains

    def test_state_query_domains_empty_list_when_none(self, settings: Settings) -> None:
        """无 query_domains → 空列表."""
        state = _build_initial_state(
            query="test",
            mode="basic",
            report_format="markdown",
            tone="objective",
            breadth=4,
            depth=2,
            total_words=1500,
            settings=settings,
        )
        assert state["query_domains"] == []

    def test_state_visited_urls_is_empty_set(self, settings: Settings) -> None:
        """visited_urls 初始化为空 set."""
        state = _build_initial_state(
            query="test",
            mode="basic",
            report_format="markdown",
            tone="objective",
            breadth=4,
            depth=2,
            total_words=1500,
            settings=settings,
        )
        assert state["visited_urls"] == set()
        assert isinstance(state["visited_urls"], set)

    def test_state_iteration_count_zero(self, settings: Settings) -> None:
        """iteration_count 初始化为 0."""
        state = _build_initial_state(
            query="test",
            mode="basic",
            report_format="markdown",
            tone="objective",
            breadth=4,
            depth=2,
            total_words=1500,
            settings=settings,
        )
        assert state["iteration_count"] == 0

    def test_state_status_pending(self, settings: Settings) -> None:
        """status 初始化为 'pending'."""
        state = _build_initial_state(
            query="test",
            mode="basic",
            report_format="markdown",
            tone="objective",
            breadth=4,
            depth=2,
            total_words=1500,
            settings=settings,
        )
        assert state["status"] == "pending"

    def test_state_research_mode_equals_mode_param(self, settings: Settings) -> None:
        """research_mode 应等于传入的 mode 参数."""
        state = _build_initial_state(
            query="test",
            mode="deep",
            report_format="markdown",
            tone="objective",
            breadth=4,
            depth=2,
            total_words=1500,
            settings=settings,
        )
        assert state["research_mode"] == "deep"

    def test_state_agent_role_server_empty_string(self, settings: Settings) -> None:
        """agent_role_server 初始化为空字符串."""
        state = _build_initial_state(
            query="test",
            mode="basic",
            report_format="markdown",
            tone="objective",
            breadth=4,
            depth=2,
            total_words=1500,
            settings=settings,
        )
        assert state["agent_role_server"] == ""


# ========== _emit_result: 输出结果处理 ==========


class TestEmitResult:
    """_emit_result 输出结果测试."""

    def test_markdown_to_stdout(self, capsys: pytest.CaptureFixture[str]) -> None:
        """markdown 格式无 -o 时打印到 stdout."""
        state = {"report_formats": {"md": "# 报告内容"}}
        rc = _emit_result(state, "markdown", None)
        assert rc == 0
        captured = capsys.readouterr()
        assert "# 报告内容" in captured.out

    def test_markdown_to_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """markdown 格式 -o 时写入文件."""
        out_file = tmp_path / "report.md"
        state = {"report_formats": {"md": "# 报告内容"}}
        rc = _emit_result(state, "markdown", str(out_file))
        assert rc == 0
        assert out_file.read_text(encoding="utf-8") == "# 报告内容"
        captured = capsys.readouterr()
        assert "已写入" in captured.out

    def test_html_to_stdout(self, capsys: pytest.CaptureFixture[str]) -> None:
        """html 格式无 -o 时打印到 stdout."""
        state = {"report_formats": {"html": "<h1>报告</h1>"}}
        rc = _emit_result(state, "html", None)
        assert rc == 0
        captured = capsys.readouterr()
        assert "<h1>报告</h1>" in captured.out

    def test_json_to_stdout(self, capsys: pytest.CaptureFixture[str]) -> None:
        """json 格式无 -o 时打印到 stdout."""
        state = {"report_formats": {"json": '{"key": "value"}'}}
        rc = _emit_result(state, "json", None)
        assert rc == 0
        captured = capsys.readouterr()
        assert '{"key": "value"}' in captured.out

    def test_markdown_fallback_to_report_md_field(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """无 report_formats.md 时回退 report_md 兼容字段."""
        state = {"report_md": "# 旧字段报告"}
        rc = _emit_result(state, "markdown", None)
        assert rc == 0
        captured = capsys.readouterr()
        assert "# 旧字段报告" in captured.out

    def test_markdown_empty_state_prints_empty_string(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """空 State 时打印空字符串 (不抛异常)."""
        state: dict[str, Any] = {}
        rc = _emit_result(state, "markdown", None)
        assert rc == 0

    def test_pdf_no_path_returns_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """pdf 格式无路径信息 → 返回 1 + 警告."""
        state: dict[str, Any] = {}
        rc = _emit_result(state, "pdf", None)
        assert rc == 1
        captured = capsys.readouterr()
        assert "未生成 PDF 路径" in captured.err

    def test_pdf_prints_path_to_stdout(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """pdf 格式有路径无 -o → 打印路径到 stdout."""
        state = {"report_formats": {"pdf": "/tmp/report.pdf"}}
        rc = _emit_result(state, "pdf", None)
        assert rc == 0
        captured = capsys.readouterr()
        assert "/tmp/report.pdf" in captured.out

    def test_pdf_copy_to_output_path(self, tmp_path: Path) -> None:
        """pdf 格式 -o 时复制源 PDF 到目标路径."""
        src_pdf = tmp_path / "source.pdf"
        src_pdf.write_bytes(b"%PDF-1.4 fake pdf content")
        out_pdf = tmp_path / "output.pdf"
        state = {"report_formats": {"pdf": str(src_pdf)}}
        rc = _emit_result(state, "pdf", str(out_pdf))
        assert rc == 0
        assert out_pdf.read_bytes() == b"%PDF-1.4 fake pdf content"

    def test_pdf_source_not_exist_returns_error(self, tmp_path: Path) -> None:
        """pdf -o 但源文件不存在 → 返回 1 + 错误."""
        out_pdf = tmp_path / "output.pdf"
        state = {"report_formats": {"pdf": "/nonexistent/path.pdf"}}
        rc = _emit_result(state, "pdf", str(out_pdf))
        assert rc == 1

    def test_pdf_fallback_to_report_pdf_path_field(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """无 report_formats.pdf 时回退 report_pdf_path 字段."""
        state = {"report_pdf_path": "/legacy/report.pdf"}
        rc = _emit_result(state, "pdf", None)
        assert rc == 0
        captured = capsys.readouterr()
        assert "/legacy/report.pdf" in captured.out

    def test_docx_writes_to_file(self, tmp_path: Path) -> None:
        """docx 格式 -o 写入二进制文件."""
        out_docx = tmp_path / "report.docx"
        docx_bytes = b"PK\x03\x04 fake docx"
        state = {"report_formats": {"docx": docx_bytes}}
        rc = _emit_result(state, "docx", str(out_docx))
        assert rc == 0
        assert out_docx.read_bytes() == docx_bytes

    def test_docx_string_content_encoded_to_bytes(self, tmp_path: Path) -> None:
        """docx 内容为字符串时编码为 UTF-8 字节."""
        out_docx = tmp_path / "report.docx"
        state = {"report_formats": {"docx": "fake docx as string"}}
        rc = _emit_result(state, "docx", str(out_docx))
        assert rc == 0
        assert out_docx.read_bytes() == b"fake docx as string"

    def test_docx_fallback_to_report_docx_field(self, tmp_path: Path) -> None:
        """无 report_formats.docx 时回退 report_docx 字段."""
        out_docx = tmp_path / "report.docx"
        state = {"report_docx": b"legacy docx bytes"}
        rc = _emit_result(state, "docx", str(out_docx))
        assert rc == 0
        assert out_docx.read_bytes() == b"legacy docx bytes"


# ========== main: CLI 异步入口 ==========


class TestMain:
    """main() CLI 异步入口测试."""

    @pytest.mark.asyncio
    async def test_main_invokes_graph_ainvoke(self) -> None:
        """main() 应调用 graph.ainvoke 并返回退出码 0."""
        argv = ["cli", "-q", "研究 AI", "-m", "basic", "-f", "markdown"]
        with (
            patch.object(sys, "argv", argv),
            patch("src.cli._get_graph", new=AsyncMock()) as mock_get_graph,
            patch("src.cli.get_settings", return_value=Settings(_env_file=None)),
            patch("src.cli.trace_agent") as mock_trace_agent,
        ):
            # trace_agent 是 async context manager
            mock_trace_agent.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_trace_agent.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_graph = MagicMock()
            mock_graph.ainvoke = AsyncMock(return_value={"report_formats": {"md": "# 结果"}})
            mock_get_graph.return_value = mock_graph

            rc = await main()

        assert rc == 0
        mock_graph.ainvoke.assert_awaited_once()
        mock_get_graph.assert_awaited_once_with(multi_agent=False)

    @pytest.mark.asyncio
    async def test_main_multi_agent_flag_passed_to_get_graph(self) -> None:
        """--multi-agent 标志应传递给 _get_graph(multi_agent=True)."""
        argv = ["cli", "-q", "test", "--multi-agent"]
        with (
            patch.object(sys, "argv", argv),
            patch("src.cli._get_graph", new=AsyncMock()) as mock_get_graph,
            patch("src.cli.get_settings", return_value=Settings(_env_file=None)),
            patch("src.cli.trace_agent") as mock_trace_agent,
        ):
            mock_trace_agent.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_trace_agent.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_graph = MagicMock()
            mock_graph.ainvoke = AsyncMock(return_value={"report_formats": {"md": "x"}})
            mock_get_graph.return_value = mock_graph

            await main()

        mock_get_graph.assert_awaited_once_with(multi_agent=True)

    @pytest.mark.asyncio
    async def test_main_non_dict_result_returns_error(self) -> None:
        """图返回非 dict → 返回 1 + 错误."""
        argv = ["cli", "-q", "test"]
        with (
            patch.object(sys, "argv", argv),
            patch("src.cli._get_graph", new=AsyncMock()) as mock_get_graph,
            patch("src.cli.get_settings", return_value=Settings(_env_file=None)),
            patch("src.cli.trace_agent") as mock_trace_agent,
        ):
            mock_trace_agent.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_trace_agent.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_graph = MagicMock()
            mock_graph.ainvoke = AsyncMock(return_value="not a dict")  # 非 dict
            mock_get_graph.return_value = mock_graph

            rc = await main()

        assert rc == 1

    @pytest.mark.asyncio
    async def test_main_graph_exception_returns_error(self) -> None:
        """graph.ainvoke 抛异常 → main 顶层兜底返回 1."""
        argv = ["cli", "-q", "test"]
        with (
            patch.object(sys, "argv", argv),
            patch("src.cli._get_graph", new=AsyncMock()) as mock_get_graph,
            patch("src.cli.get_settings", return_value=Settings(_env_file=None)),
            patch("src.cli.trace_agent") as mock_trace_agent,
        ):
            mock_trace_agent.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_trace_agent.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_graph = MagicMock()
            mock_graph.ainvoke = AsyncMock(side_effect=RuntimeError("graph 构建失败"))
            mock_get_graph.return_value = mock_graph

            rc = await main()

        assert rc == 1

    @pytest.mark.asyncio
    async def test_main_query_domains_parsed_from_comma_string(self) -> None:
        """--query-domains 逗号分隔字符串应解析为列表."""
        argv = ["cli", "-q", "test", "--query-domains", "arxiv.org, github.com ,  "]
        captured_state: dict[str, Any] = {}

        async def _capture_ainvoke(state: dict[str, Any], config: Any) -> dict[str, Any]:
            captured_state.update(state)
            return {"report_formats": {"md": "ok"}}

        with (
            patch.object(sys, "argv", argv),
            patch("src.cli._get_graph", new=AsyncMock()) as mock_get_graph,
            patch("src.cli.get_settings", return_value=Settings(_env_file=None)),
            patch("src.cli.trace_agent") as mock_trace_agent,
        ):
            mock_trace_agent.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_trace_agent.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_graph = MagicMock()
            mock_graph.ainvoke = _capture_ainvoke
            mock_get_graph.return_value = mock_graph

            await main()

        # 应剔除空白项 + strip
        assert captured_state["query_domains"] == ["arxiv.org", "github.com"]

    @pytest.mark.asyncio
    async def test_main_words_override_settings_total_words(self) -> None:
        """--words 显式指定时覆盖 settings.total_words."""
        argv = ["cli", "-q", "test", "--words", "5000"]
        captured_state: dict[str, Any] = {}

        async def _capture_ainvoke(state: dict[str, Any], config: Any) -> dict[str, Any]:
            captured_state.update(state)
            return {"report_formats": {"md": "ok"}}

        with (
            patch.object(sys, "argv", argv),
            patch("src.cli._get_graph", new=AsyncMock()) as mock_get_graph,
            patch("src.cli.get_settings", return_value=Settings(_env_file=None, total_words=1500)),
            patch("src.cli.trace_agent") as mock_trace_agent,
        ):
            mock_trace_agent.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_trace_agent.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_graph = MagicMock()
            mock_graph.ainvoke = _capture_ainvoke
            mock_get_graph.return_value = mock_graph

            await main()

        assert captured_state["total_words"] == 5000

    @pytest.mark.asyncio
    async def test_main_uses_settings_total_words_when_no_words_flag(self) -> None:
        """无 --words 时使用 settings.total_words."""
        argv = ["cli", "-q", "test"]
        captured_state: dict[str, Any] = {}

        async def _capture_ainvoke(state: dict[str, Any], config: Any) -> dict[str, Any]:
            captured_state.update(state)
            return {"report_formats": {"md": "ok"}}

        with (
            patch.object(sys, "argv", argv),
            patch("src.cli._get_graph", new=AsyncMock()) as mock_get_graph,
            patch("src.cli.get_settings", return_value=Settings(_env_file=None, total_words=2400)),
            patch("src.cli.trace_agent") as mock_trace_agent,
        ):
            mock_trace_agent.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_trace_agent.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_graph = MagicMock()
            mock_graph.ainvoke = _capture_ainvoke
            mock_get_graph.return_value = mock_graph

            await main()

        assert captured_state["total_words"] == 2400

    @pytest.mark.asyncio
    async def test_main_trace_agent_provides_root_span(self) -> None:
        """main() 应通过 trace_agent 包裹 graph.ainvoke 建立根 span."""
        argv = ["cli", "-q", "test"]
        with (
            patch.object(sys, "argv", argv),
            patch("src.cli._get_graph", new=AsyncMock()) as mock_get_graph,
            patch("src.cli.get_settings", return_value=Settings(_env_file=None)),
            patch("src.cli.trace_agent") as mock_trace_agent,
        ):
            mock_trace_agent.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_trace_agent.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_graph = MagicMock()
            mock_graph.ainvoke = AsyncMock(return_value={"report_formats": {"md": "x"}})
            mock_get_graph.return_value = mock_graph

            await main()

        mock_trace_agent.assert_called_once()
        call_kwargs = mock_trace_agent.call_args.kwargs
        assert call_kwargs["name"] == "agentinsight-researcher"
        assert call_kwargs["user_id"] == _CLI_USER_ID
        assert "session_id" in call_kwargs["metadata"]
        assert call_kwargs["metadata"]["intent"] == "research"
