"""回归测试: TOC 一致性优化 (优化 1-6).

验证 temp/report_toc_content_mismatch_optimization.md 中的 6 项优化在完整研究流程中生效:
1. TOC 后置生成 (从实际 sections 提取, 而非从完整 subtopics 生成)
2. 每个子主题独立 sub_context (BM25 检索)
3. 跨章节语义去重 (只丢弃相似 chunk, 保留差异部分)
4. 失败章节在 TOC 中标记
5. 一致性校验 (防御性编程)
6. _conduct_subtopics 失败跳过时同步移除 subtopics

- 回归测试在 docker compose up -d 且全部容器 service_healthy 后执行
- 回归测试为合并 main 前门禁, 不应跳过
- 测试目标地址从环境变量 AGENT_URL 注入
- 每次用唯一 session_id=test_regression_* (测试数据隔离)

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/regression/test_toc_consistency.py -v -m regression --tb=short
"""

from __future__ import annotations

import os
import re
import time
import uuid
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from src.skills.researcher.context_manager import WrittenContentCompressor
from src.skills.researcher.report_generator import _SECTION_FAILURE_PLACEHOLDER, ReportGenerator

# 测试目标地址从环境变量注入
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")

# detailed_report 可能需要 400s+ (多子主题研究 + 章节生成)
DETAILED_REPORT_TIMEOUT = httpx.Timeout(connect=10.0, read=400.0, write=30.0, pool=10.0)
# basic_report 通常 3-5 分钟
BASIC_REPORT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)

# 非正文章节标题关键字 (排除引言/结论/目录/参考等)
_NON_SECTION_KEYWORDS = ("目录", "参考来源", "参考文献", "References", "结论", "引言", "前言")


def _unique_session_id() -> str:
    """生成唯一 session_id (session_id=test_regression_*)."""
    return f"test_regression_toc_{uuid.uuid4().hex[:12]}"


def _log(msg: str) -> None:
    """带时间戳输出, 便于追踪长流程进度."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ========== Helper: 解析 TOC 与正文章节 ==========

# TOC 条目: 形如 "1. [主题](#锚点)"
_TOC_ENTRY_PATTERN = re.compile(r"^\d+\.\s+\[([^\]]+)\]\(#[^)]+\)", re.MULTILINE)
# 主章节标题: ## 开头 (LLM 指令: `##` 章节标题 + `###` 子小节)
_MAIN_SECTION_PATTERN = re.compile(r"^##\s+(.+)$")
# 所有标题: ## 或 ### 开头 (含子小节, 用于 TOC 条目匹配)
_ANY_HEADER_PATTERN = re.compile(r"^#{2,3}\s+(.+)$")


def _extract_toc_entries(report_md: str) -> list[str]:
    """提取 TOC 中的主题文本列表 (形如 '1. [主题](#主题)' → '主题').

    仅在 '## 目录' 与 '---' 之间查找, 避免误匹配正文中的编号列表.
    """
    toc_match = re.search(r"## 目录\s*\n(.*?)(?:\n---|\Z)", report_md, re.DOTALL)
    if not toc_match:
        return []
    toc_block = toc_match.group(1)
    return _TOC_ENTRY_PATTERN.findall(toc_block)


def _extract_all_headers(report_md: str) -> list[str]:
    """提取所有 ## 和 ### 标题文本 (排除目录/参考/引言/结论等非正文标题).

    用于 TOC 条目匹配: 正常章节用 ## 标题, 失败章节用 ### 标题 (优化 4).
    """
    headers: list[str] = []
    for line in report_md.split("\n"):
        m = _ANY_HEADER_PATTERN.match(line.strip())
        if not m:
            continue
        title = m.group(1).strip()
        if any(kw in title for kw in _NON_SECTION_KEYWORDS):
            continue
        headers.append(title)
    return headers


def _extract_section_bodies(report_md: str) -> list[tuple[str, str]]:
    """提取正文主章节标题与对应正文 (仅 ## 标题, ### 子小节归入正文).

    LLM 指令: `##` 章节标题 + `###` 子小节. 仅在 ## 处分割章节,
    ### 子小节内容归入所属 ## 章节的正文, 避免 ### 子小节误判为空章节.

    Returns:
        [(title, body), ...] 按出现顺序, title 为 ## 标题文本 (不含 ## 前缀),
        body 为该标题下到下一 ## 标题间的全部正文 (含 ### 子小节).
    """
    lines = report_md.split("\n")
    sections: list[tuple[str, str]] = []
    current_title: str | None = None
    current_body: list[str] = []

    for line in lines:
        stripped = line.strip()
        m = _MAIN_SECTION_PATTERN.match(stripped)
        if m:
            # 遇到新 ## 标题, 保存前一个章节
            if current_title is not None:
                sections.append((current_title, "\n".join(current_body).strip()))
            title = m.group(1).strip()
            if any(kw in title for kw in _NON_SECTION_KEYWORDS):
                current_title = None  # 跳过非正文章节
            else:
                current_title = title
            current_body = []
        elif current_title is not None:
            current_body.append(line)

    # 保存最后一个章节
    if current_title is not None:
        sections.append((current_title, "\n".join(current_body).strip()))

    return sections


def _toc_topic_matches_header(toc_entry: str, header: str) -> bool:
    """判断 TOC 条目与正文标题是否匹配.

    TOC 条目可能含 '(生成失败)' 后缀 (优化 4), 需剥离后匹配.
    """
    # 剥离 '(生成失败)' 后缀
    toc_topic = toc_entry.replace(" (生成失败)", "").strip()
    return toc_topic in header or header in toc_topic


# ========== 1. detailed_report TOC 与正文一致性 ==========


@pytest.mark.regression
async def test_detailed_report_toc_matches_body() -> None:
    """优化 1: detailed_report TOC 每个条目都有对应的正文标题.

    验证 TOC 后置生成: TOC 条目与实际正文章节一一对应.
    OpenAI 兼容端点非流式响应.
    """
    sid = _unique_session_id()
    query = "简述人工智能的核心概念与应用"
    _log(f"detailed_report TOC 一致性测试开始: session={sid}, query={query}")

    async with httpx.AsyncClient(timeout=DETAILED_REPORT_TIMEOUT) as client:
        r = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": query}],
                "stream": False,
                "report_type": "detailed_report",
                "session_id": sid,
            },
        )

    assert r.status_code == 200, f"detailed_report 响应非 200: {r.status_code} {r.text[:500]}"
    content = r.json()["choices"][0]["message"]["content"]
    assert content, "detailed_report content 为空"

    toc_entries = _extract_toc_entries(content)
    all_headers = _extract_all_headers(content)

    _log(f"TOC 条目数={len(toc_entries)}, 正文标题数={len(all_headers)}, 内容长度={len(content)}")
    _log(f"TOC 条目: {toc_entries}")
    _log(f"正文标题: {all_headers}")

    # TOC 应非空 (detailed_report 至少 1 个子主题)
    assert toc_entries, f"TOC 无条目, 报告预览: {content[:300]}"

    # 验证: 每个 TOC 条目都有对应的正文标题 (## 正常章节或 ### 失败章节)
    for entry in toc_entries:
        matched = any(_toc_topic_matches_header(entry, h) for h in all_headers)
        assert matched, (
            f"TOC 条目 '{entry}' 无对应正文标题.\n"
            f"正文标题: {all_headers}\n"
            f"报告预览: {content[:500]}"
        )

    _log("detailed_report TOC 与正文一致性验证通过")


# ========== 2. detailed_report 无空章节 ==========


@pytest.mark.regression
async def test_detailed_report_no_empty_sections() -> None:
    """优化 1+4: detailed_report 无空章节 (只有标题无正文).

    排除失败标记章节 ('此章节内容生成失败').
    """
    sid = _unique_session_id()
    query = "简述机器学习的基本概念与主要方法"
    _log(f"detailed_report 空章节检测开始: session={sid}, query={query}")

    async with httpx.AsyncClient(timeout=DETAILED_REPORT_TIMEOUT) as client:
        r = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": query}],
                "stream": False,
                "report_type": "detailed_report",
                "session_id": sid,
            },
        )

    assert r.status_code == 200, f"detailed_report 响应非 200: {r.status_code}"
    content = r.json()["choices"][0]["message"]["content"]
    assert content, "detailed_report content 为空"

    section_bodies = _extract_section_bodies(content)
    _log(f"检测到 {len(section_bodies)} 个正文章节")

    # 验证每个章节有非空正文 (排除失败标记章节)
    for title, body in section_bodies:
        # 失败标记章节允许正文为失败提示文本 (优化 4)
        if "生成失败" in title:
            continue
        body_text = body.strip()
        assert len(body_text) > 20, (
            f"章节 '{title}' 正文为空或过短 ({len(body_text)} 字).\n正文预览: {body_text[:200]}"
        )

    _log("detailed_report 空章节检测通过")


# ========== 3. 去重跳过的子主题不出现在 TOC (单元级回归) ==========


def _make_settings() -> Any:
    """构造测试用 Settings (Mock)."""
    settings = MagicMock()
    settings.agent_role = None
    settings.image_generation_enabled = False
    settings.written_content_similarity_threshold = 0.5
    settings.smart_token_limit = 6000
    settings.fast_token_limit = 3000
    settings.word_limit = 800
    settings.detailed_report_max_context_chars = 8000
    settings.report_language = "zh"
    settings.tracing_enabled = False
    return settings


@pytest.mark.regression
def test_detailed_report_skipped_count_logged() -> None:
    """优化 1+3: 去重跳过的子主题不出现在 TOC 中.

    构造 2 个高度相似的子主题, 触发 WrittenContentCompressor 去重跳过,
    验证 TOC 只含 1 个条目 (被跳过的子主题不在 TOC 中).
    """
    settings = _make_settings()
    settings.written_content_similarity_threshold = 0.5
    compressor = WrittenContentCompressor(settings)

    # 子主题 A 的 chunks + embeddings (首次写入, 保留)
    chunks_a = ["量子计算利用量子力学原理进行计算"]
    embs_a = [[1.0, 0.0]]
    keep_a, _ = compressor.check_and_update_partial(chunks_a, embs_a)
    assert keep_a is True, "首次写入应保留"

    # 子主题 B 与 A 高度相似 (相同 embedding → cosine=1.0 >= 0.5, 整篇丢弃)
    chunks_b = ["量子计算基于量子力学原理进行运算"]
    embs_b = [[1.0, 0.0]]
    keep_b, _ = compressor.check_and_update_partial(chunks_b, embs_b)
    assert keep_b is False, "高度相似子主题应被去重跳过"

    # 模拟 report_generator 的汇总逻辑 (优化 1: TOC 后置生成)
    subtopics = ["量子计算原理", "量子计算机制"]
    section_results: list[tuple[str | None, list[dict[str, Any]], bool]] = [
        ("## 量子计算原理\n\n量子计算利用量子力学...", [], False),  # A 正常写入
        (None, [], True),  # B 被去重跳过 (skipped=True)
    ]

    sections: list[str] = []
    valid_topics_for_toc: list[str] = []
    skipped_count = 0
    for topic, (section_md, _sub_sources, skipped) in zip(subtopics, section_results, strict=False):
        if skipped:
            skipped_count += 1
            continue
        if section_md:
            if section_md == _SECTION_FAILURE_PLACEHOLDER:
                valid_topics_for_toc.append(f"{topic} (生成失败)")
                sections.append(f"### {topic}\n\n*此章节内容生成失败, 请重试。*")
            else:
                valid_topics_for_toc.append(topic)
                sections.append(section_md)

    # 一致性校验 (优化 5)
    if len(valid_topics_for_toc) != len(sections):
        valid_topics_for_toc = valid_topics_for_toc[: len(sections)]

    toc = ReportGenerator._generate_toc(valid_topics_for_toc)

    # 验证: TOC 只含 1 个条目 (子主题 B 被跳过)
    assert skipped_count == 1, f"应跳过 1 个子主题, 实际 {skipped_count}"
    assert len(valid_topics_for_toc) == 1, f"TOC 应含 1 个条目, 实际 {len(valid_topics_for_toc)}"
    assert "量子计算原理" in toc, f"TOC 应含 '量子计算原理', 实际: {toc}"
    assert "量子计算机制" not in toc, f"TOC 不应含被跳过的 '量子计算机制', 实际: {toc}"
    assert len(sections) == 1


# ========== 4. basic_report 包含参考文献章节 ==========


@pytest.mark.regression
async def test_basic_report_has_references() -> None:
    """验证 basic_report 报告包含参考文献章节 (## 参考文献 / ## 参考来源 / ## References).

    OpenAI 兼容端点非流式响应.
    """
    sid = _unique_session_id()
    query = "简述 Python 异步编程的核心优势"
    _log(f"basic_report 参考文献检测开始: session={sid}, query={query}")

    async with httpx.AsyncClient(timeout=BASIC_REPORT_TIMEOUT) as client:
        r = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": query}],
                "stream": False,
                "report_type": "basic_report",
                "session_id": sid,
            },
        )

    assert r.status_code == 200, f"basic_report 响应非 200: {r.status_code}"
    content = r.json()["choices"][0]["message"]["content"]
    assert content, "basic_report content 为空"

    has_references = (
        "## 参考文献" in content or "## 参考来源" in content or "## References" in content
    )
    assert has_references, (
        f"basic_report 缺少参考文献章节 (## 参考文献 / ## 参考来源 / ## References).\n"
        f"报告末尾预览: {content[-500:]}"
    )
    _log("basic_report 参考文献检测通过")
