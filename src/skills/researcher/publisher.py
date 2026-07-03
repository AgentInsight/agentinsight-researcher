"""Publisher 发布器.

对标 GPT Researcher multi_agents/agents/publisher.py.
AGENTS.md 用户需求 6: 输出报告格式需支持 Markdown/HTML/PDF, 默认 Markdown.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.settings import Settings, get_settings
from src.observability.tracing import trace_chain

logger = logging.getLogger(__name__)


class Publisher:
    """报告发布器.

    对标 GPT Researcher Publisher.
    支持 Markdown (默认) / HTML / PDF / DOCX / JSON 输出 (P1-05 扩展 docx + json).
    """

    settings: Settings

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def publish(
        self,
        report_md: str,
        *,
        output_format: str = "markdown",
        title: str = "",
        sources: list[dict[str, Any]] | None = None,
        agent_role_server: str = "",
        research_mode: str = "",
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """发布报告.

        返回 {"format","content","path"}.
        - markdown: 返回 Markdown 原文
        - html: 返回 HTML 渲染
        - pdf: 返回 PDF 文件路径
        - docx: 返回 DOCX 二进制 (P1-05)
        - json: 返回结构化 JSON 字符串 (P1-05)
        """
        async with trace_chain(
            name="publisher",
            input={"format": output_format, "report_len": len(report_md)},
            user_id=user_id,
            session_id=session_id,
        ) as span:
            if output_format == "markdown":
                span.update(output={"format": "markdown"})
                return {"format": "markdown", "content": report_md, "path": None}

            if output_format == "html":
                html = self._md_to_html(report_md)
                span.update(output={"format": "html", "html_len": len(html)})
                return {"format": "html", "content": html, "path": None}

            if output_format == "pdf":
                pdf_path = await self._md_to_pdf(report_md, session_id)
                span.update(output={"format": "pdf", "pdf_path": pdf_path})
                return {"format": "pdf", "content": None, "path": pdf_path}

            if output_format == "docx":
                docx_bytes = self._to_docx(report_md, title=title)
                span.update(output={"format": "docx", "size": len(docx_bytes)})
                return {"format": "docx", "content": docx_bytes, "path": None}

            if output_format == "json":
                json_str = self._to_json(
                    report_md,
                    title=title,
                    sources=sources or [],
                    agent_role_server=agent_role_server,
                    research_mode=research_mode,
                )
                span.update(output={"format": "json", "len": len(json_str)})
                return {"format": "json", "content": json_str, "path": None}

            # 默认 markdown
            span.update(output={"format": "markdown"})
            return {"format": "markdown", "content": report_md, "path": None}

    def _to_docx(self, content: str, *, title: str = "") -> bytes:
        """Markdown → DOCX (python-docx, P1-05).

        简易 Markdown 解析 (标题/段落/列表), 失败返回空 bytes.
        """
        try:
            import io

            from docx import Document

            doc = Document()
            if title:
                doc.add_heading(title, level=0)
            # 简易 Markdown 解析 (标题/段落/列表)
            for line in content.split("\n"):
                line = line.rstrip()
                if not line:
                    continue
                if line.startswith("# "):
                    doc.add_heading(line[2:], level=1)
                elif line.startswith("## "):
                    doc.add_heading(line[3:], level=2)
                elif line.startswith("### "):
                    doc.add_heading(line[4:], level=3)
                elif line.startswith("- ") or line.startswith("* "):
                    doc.add_paragraph(line[2:], style="List Bullet")
                elif line[:2].lstrip().isdigit() and ". " in line:
                    # 简单数字列表 (1. xxx / 2. xxx)
                    text = line.split(". ", 1)[-1]
                    doc.add_paragraph(text, style="List Number")
                else:
                    doc.add_paragraph(line)
            buf = io.BytesIO()
            doc.save(buf)
            return buf.getvalue()
        except Exception as e:  # noqa: BLE001
            logger.warning("DOCX 生成失败: %s", e)
            return b""

    def _to_json(
        self,
        content: str,
        *,
        title: str = "",
        sources: list[dict[str, Any]] | None = None,
        agent_role_server: str = "",
        research_mode: str = "",
    ) -> str:
        """报告 → JSON (结构化输出, P1-05)."""
        import json
        from datetime import datetime

        report_data = {
            "title": title or "研究报告",
            "content": content,
            "sources": sources or [],
            "metadata": {
                "agent_role_server": agent_role_server,
                "research_mode": research_mode,
                "generated_at": datetime.now().isoformat(),
            },
        }
        return json.dumps(report_data, ensure_ascii=False, indent=2)

    def _md_to_html(self, md: str) -> str:
        """Markdown 转 HTML (用 mistune + jinja2 模板)."""
        try:
            import mistune

            markdown = mistune.create_markdown()
            body = markdown(md)

            # HTML 模板 (内联 CSS, 离线友好)
            template = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>研究报告</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; line-height: 1.8; max-width: 800px; margin: 40px auto; padding: 20px; color: #333; }}
h1 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
h2 {{ color: #34495e; border-bottom: 1px solid #ecf0f1; padding-bottom: 8px; margin-top: 30px; }}
h3 {{ color: #34495e; margin-top: 25px; }}
a {{ color: #3498db; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 3px; font-family: "Source Code Pro", monospace; }}
blockquote {{ border-left: 4px solid #3498db; margin: 0; padding-left: 16px; color: #666; }}
table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
th {{ background: #f8f9fa; font-weight: 600; }}
</style>
</head>
<body>
{body}
</body>
</html>"""
            return template.replace("{body}", body)
        except Exception as e:  # noqa: BLE001
            logger.warning("HTML 渲染失败, 返回纯文本: %s", e)
            return f"<html><body><pre>{md}</pre></body></html>"

    async def _md_to_pdf(self, md: str, session_id: str | None = None) -> str:
        """Markdown 转 PDF (WeasyPrint)."""
        try:
            import asyncio
            import os

            # 先转 HTML
            html = self._md_to_html(md)

            # PDF 输出路径
            upload_dir = self.settings.upload_dir
            os.makedirs(upload_dir, exist_ok=True)
            filename = f"report_{session_id or 'unknown'}.pdf"
            pdf_path = os.path.join(upload_dir, filename)

            def _sync_pdf() -> None:
                from weasyprint import HTML

                HTML(string=html).write_pdf(pdf_path)

            await asyncio.to_thread(_sync_pdf)
            return pdf_path
        except Exception as e:  # noqa: BLE001
            logger.warning("PDF 生成失败: %s", e)
            # 降级返回 HTML
            html_path = await self._save_html_fallback(md, session_id)
            return html_path

    async def _save_html_fallback(self, md: str, session_id: str | None = None) -> str:
        """PDF 失败时降级保存 HTML."""
        import asyncio
        import os

        upload_dir = self.settings.upload_dir
        os.makedirs(upload_dir, exist_ok=True)
        filename = f"report_{session_id or 'unknown'}.html"
        html_path = os.path.join(upload_dir, filename)
        html = self._md_to_html(md)

        def _write() -> None:
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)

        await asyncio.to_thread(_write)
        return html_path
