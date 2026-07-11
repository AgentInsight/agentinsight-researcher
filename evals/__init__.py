"""评测门禁 (CI 强制).

RAGAS: faithfulness ≥0.8 / answer_relevancy ≥0.8 / context_precision ≥0.7
DeepEval: 任务完成率 ≥0.9 / 工具调用正确率 ≥0.95 / 幻觉率 ≤0.1

SimpleQA + Hallucination 评测套件.
评测器通过 HTTP API 调用 researcher (不直接 import src/), LLM 评测复用项目 LLMClient.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ResearcherClient:
    """研究器 HTTP API 客户端 (通过 OpenAI 兼容端点调用, 不直接 import src/).

    测试目标地址从环境变量 AGENT_URL 注入, 禁止硬编码.
    统一调用 POST /v1/chat/completions (非流式), 获取研究报告 + 来源.
    """

    def __init__(
        self,
        base_url: str = "http://agent:8066",
        *,
        timeout: float = 300.0,
        authorization: str | None = None,
        default_report_type: str = "basic_report",
    ) -> None:
        """初始化研究器客户端.

        Args:
            base_url: 研究器 API 地址 (默认读 AGENT_URL 环境变量).
            timeout: 单次请求超时 (秒, 研究任务耗时较长, 默认 300s).
            authorization: Bearer JWT Token (可选, 用于身份解析).
            default_report_type: 默认报告类型.
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.authorization = authorization
        self.default_report_type = default_report_type

    async def research(
        self,
        query: str,
        *,
        report_type: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """调用研究器执行一次研究, 返回报告与来源.

        Args:
            query: 研究查询.
            report_type: 报告类型 (None 用默认).
            session_id: 会话 ID (None 自动生成, 用于会话隔离).

        Returns:
            {"report": str, "sources": list[dict], "session_id": str,
             "elapsed_seconds": float, "raw": dict}
        """
        start = time.perf_counter()
        sid = session_id or f"eval-{uuid.uuid4().hex[:12]}"
        rtype = report_type or self.default_report_type

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.authorization:
            headers["Authorization"] = self.authorization

        payload: dict[str, Any] = {
            "model": "agentinsight-researcher",
            "messages": [{"role": "user", "content": query}],
            "stream": False,
            "report_type": rtype,
            "session_id": sid,
        }

        url = f"{self.base_url}/v1/chat/completions"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        elapsed = time.perf_counter() - start

        # 从 OpenAI 兼容响应中提取报告正文
        content = ""
        choices = data.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")

        # 解析来源 (后端在报告末尾附加 "## 参考来源" 段落)
        sources: list[dict[str, Any]] = self._extract_sources(content)

        return {
            "report": content,
            "sources": sources,
            "session_id": sid,
            "elapsed_seconds": elapsed,
            "raw": data,
        }

    @staticmethod
    def _extract_sources(content: str) -> list[dict[str, Any]]:
        """从报告正文中解析参考来源列表.

        后端格式 (routes.py _run_research):
            ## 参考来源
            1. [标题](url)
            2. [标题](url)
        """
        sources: list[dict[str, Any]] = []
        if "## 参考来源" not in content:
            return sources
        section = content.split("## 参考来源", 1)[-1]
        # 匹配 markdown 链接 [title](url)
        pattern = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
        for match in pattern.finditer(section):
            sources.append({"title": match.group(1), "url": match.group(2)})
        return sources
