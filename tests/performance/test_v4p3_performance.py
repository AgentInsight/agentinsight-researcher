"""性能测试: 两层方案性能 (BM25Filter).

两层方案设计:
- Layer 1 Fast Path: < 8K 字符, 直接拼接原文 (零计算)
- Layer 2 BM25Filter: >= 8K 字符, jieba+BM25Okapi 本地过滤 (主路径)

AGENTS.md 第 13 章硬约束:
- 性能测试以单元测试为主 (不依赖容器栈), 使用 mock + time.perf_counter 测量
- 测试数据隔离: session_id=test_perf_*

执行方式:
    pytest tests/performance/test_v4p3_performance.py -v -s
"""

from __future__ import annotations

import time

import pytest

from src.config.settings import get_settings
from src.rag.bm25_filter import BM25Filter

pytestmark = pytest.mark.unit


def _generate_test_documents(chunk_count: int) -> list[dict[str, str]]:
    """生成测试文档列表 (模拟 258 chunks 的场景)."""
    base_text = (
        "人工智能在医疗领域的应用前景非常广阔。近年来，机器学习和深度学习技术"
        "取得了重大突破，为医疗诊断、药物研发、健康管理等多个方面带来了革命性"
        "的变化。医疗影像诊断是 AI 应用最成熟的领域之一，通过计算机视觉技术"
        "可以辅助医生进行疾病筛查和诊断。"
    )
    return [
        {"content": base_text + f" 第{i}段内容", "url": f"http://example.com/doc{i}.html"}
        for i in range(chunk_count)
    ]


# ========== BM25Filter 性能测试 ==========


async def test_bm25_filter_response_time_under_200ms() -> None:
    """验证 BM25Filter 响应时间 < 200ms (258 chunks).

    L2: BM25Filter 替代 EmbeddingsFilter 作为主路径.
    基线: 258 chunks × 本地 jieba+BM25 = ~2 秒 (文档注释实测).
    阈值: 宽松到 200ms 允许首次 jieba 加载, 正常应在 10-50ms.
    首次 jieba 加载耗时约 1s, 需预热后再计时.
    """
    settings = get_settings()
    filt = BM25Filter(settings)
    documents = _generate_test_documents(258)
    query = "人工智能医疗影像诊断应用"

    await filt.filter(query, documents, max_results=10)

    start = time.perf_counter()
    result = await filt.filter(query, documents, max_results=10)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert len(result) > 0, "BM25Filter 返回空结果"
    assert elapsed_ms < 200.0, f"BM25Filter 响应时间 {elapsed_ms:.1f}ms 超过阈值 200ms"
    print(f"\n[bm25_filter_response_time] {elapsed_ms:.1f}ms (258 chunks)")


async def test_bm25_filter_vs_fastembed_rerank_latency() -> None:
    """验证 BM25Filter 粗筛 + FastEmbed 精排两阶段延迟可接受.

    两层路由架构:
    - BM25 粗筛 (本地 jieba+BM25Okapi): 50 chunks ~10ms
    - FastEmbed 精排 (本地 bge-small-zh ONNX): 50 → 20 chunks ~50ms
    - 旧 EmbeddingsFilter (已删除): 258 chunks × TEI 推理 = ~43 分钟

    本测试仅验证 BM25Filter 本地执行时间稳定 < 200ms (与 FastEmbed 精排解耦,
    FastEmbed 精排性能由 test_fastembed_* 系列覆盖).
    """
    settings = get_settings()
    documents = _generate_test_documents(50)
    query = "人工智能医疗应用"

    bm25_filt = BM25Filter(settings)
    # 预热 jieba
    await bm25_filt.filter(query, documents, max_results=10)
    start = time.perf_counter()
    await bm25_filt.filter(query, documents, max_results=10)
    bm25_time = time.perf_counter() - start

    assert bm25_time < 0.2, f"BM25Filter 50 chunks 延迟 {bm25_time:.3f}s 超过 200ms"
    print(f"\n[bm25_filter_latency] 50 chunks = {bm25_time:.3f}s (<200ms)")


# ========== Trafilatura vs BS+markdownify 抓取延迟对比 ==========


async def test_trafilatura_vs_bs_scraping_latency() -> None:
    """验证 Trafilatura vs BS+markdownify 抓取延迟对比.

    L1 降级链: Trafilatura 为主要抓取路径, BS+markdownify 为降级链 L2.
    Trafilatura 优势: 输出 LLM 友好 Markdown, 内置去噪, 依赖少, 速度快.
    BS+markdownify 优势: 纯本地计算, 零网络调用.

    本测试使用 mock HTTP 响应, 测量两者的处理时间对比.
    """
    pytest.importorskip("bs4")
    pytest.importorskip("markdownify")
    pytest.importorskip("trafilatura")

    mock_html = (
        "<html><head><title>测试页面</title></head>"
        "<body><article><h1>人工智能</h1><p>医疗应用前景广阔。</p></article></body></html>"
    )

    from unittest.mock import AsyncMock, MagicMock

    mock_session = MagicMock()
    mock_response = AsyncMock()
    mock_response.text = mock_html
    mock_response.raise_for_status = lambda: None
    mock_session.get = AsyncMock(return_value=mock_response)

    from src.skills.researcher.scrapers.bs_markdownify_scraper import BSMarkdownifyScraper
    from src.skills.researcher.scrapers.trafilatura_scraper import TrafilaturaScraper

    settings = get_settings()
    original_enabled = settings.bm25_filter_enabled
    try:
        bs_scraper = BSMarkdownifyScraper("http://example.com", session=mock_session)

        start = time.perf_counter()
        await bs_scraper.scrape()
        bs_time = time.perf_counter() - start

        tf_scraper = TrafilaturaScraper("http://example.com", session=mock_session)

        start = time.perf_counter()
        await tf_scraper.scrape()
        tf_time = time.perf_counter() - start
    finally:
        settings.bm25_filter_enabled = original_enabled

    print(f"\n[bs_markdownify_latency] {bs_time:.3f}s")
    print(f"[trafilatura_latency] {tf_time:.3f}s")


# ========== Fast Path 性能测试 ==========


async def test_fast_path_no_compression_latency() -> None:
    """验证 Fast Path (<8K) 零计算延迟 < 10ms.

    L1: 文档总字符 < 8000 时跳过压缩, 直接拼接原文返回.
    此路径应极快 (<10ms), 仅包含字符串拼接操作.
    """
    settings = get_settings()
    original_threshold = settings.bm25_filter_char_threshold
    try:
        settings.bm25_filter_char_threshold = 8000

        documents = _generate_test_documents(2)
        total_chars = sum(len(d["content"]) for d in documents)
        assert total_chars < 8000, f"测试文档字符数 {total_chars} 超过 8000"

        query = "测试查询"

        from src.skills.researcher.context_manager import ContextManager

        cm = ContextManager(settings)
        cm._embeddings.is_circuit_open = lambda: False
        cm._written_compressor.reset = lambda: None

        await cm.get_similar_content(query, documents, max_results=5)

        start = time.perf_counter()
        context = await cm.get_similar_content(query, documents, max_results=5)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert len(context) > 0, "Fast Path 返回空上下文"
        assert elapsed_ms < 10.0, f"Fast Path 延迟 {elapsed_ms:.1f}ms 超过阈值 10ms"
        print(f"\n[fast_path_latency] {elapsed_ms:.1f}ms (阈值 10ms)")
    finally:
        settings.bm25_filter_char_threshold = original_threshold
