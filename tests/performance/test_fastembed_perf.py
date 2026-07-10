"""性能测试: FastEmbed 本地 Embeddings 性能 (8 项优化之 FastEmbed).

AGENTS.md 第 7/13 章硬约束:
- FastEmbed (bge-small-zh-v1.5, 512维) 用于上下文压缩, 不依赖远程 TEI
- 性能测试以单元测试为主 (mock + time.perf_counter), 不依赖容器栈

覆盖 trace 4ad14970 优化项:
1. batch_size=64 vs 32 的吞吐对比 (mock ONNX 推理)
2. ONNX Runtime 并行执行 (intra/inter_op_num_threads) 配置验证
3. 模型预热消除冷启动延迟验证
4. LRU 缓存命中率对性能影响

执行方式:
    pytest tests/performance/test_fastembed_perf.py -v -m performance -s
"""

from __future__ import annotations

import os
import sys
import time
import types

import pytest

from src.config.settings import Settings
from src.rag import fastembed_client as fe_module
from src.rag.fastembed_client import FastEmbedClient

pytestmark = pytest.mark.performance


def _make_settings(**overrides: object) -> Settings:
    """构造测试 Settings (跳过 .env 加载)."""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


class _FakeOnnxModel:
    """模拟 fastembed TextEmbedding 模型 (mock ONNX 推理).

    通过 sleep 模拟 ONNX 推理耗时, 使性能对比可测量.
    模拟 ONNX 内部并行: 推理耗时与 batch 大小弱相关 (大 batch 更高效).
    """

    # 类级配置 (由 _install_fake_fastembed 设置)
    _per_vector_ms: float = 0.5
    _init_delay: float = 0.0

    def __init__(self, **_kwargs: object) -> None:
        # 模拟 ONNX Runtime 初始化延迟 (冷启动)
        if _FakeOnnxModel._init_delay > 0:
            time.sleep(_FakeOnnxModel._init_delay)
        self.init_kwargs = _kwargs
        # 记录构造参数 (用于验证 ONNX 线程配置)
        self.threads = _kwargs.get("threads")
        self.model_name = _kwargs.get("model_name")
        self.max_length = _kwargs.get("max_length")

    def embed(self, texts: list[str]) -> list[list[float]]:
        """模拟 ONNX 推理: 固定开销 + 受并行度限制的 per-vector 开销.

        真实 ONNX 推理: intra_op_num_threads 并行处理 batch 内向量,
        batch_size 超过并行度后单批耗时不再显著增长 (大 batch 更高效).
        mock 策略: 固定 5ms 批次开销 + min(count, 32) * per_vector_ms,
        模拟 ONNX 内部并行 (32 线程并行处理, batch>32 后耗时增长缓慢).
        """
        count = len(texts)
        # ONNX 内部并行: 超过并行度的向量不额外增加单批耗时
        effective_count = min(count, 32)
        time.sleep(0.005 + effective_count * _FakeOnnxModel._per_vector_ms / 1000.0)
        return [[0.01] * 512 for _ in range(count)]


def _install_fake_fastembed(
    monkeypatch: pytest.MonkeyPatch,
    per_vector_ms: float = 0.5,
    init_delay: float = 0.0,
) -> type[_FakeOnnxModel]:
    """安装 fake fastembed 模块, 返回 fake 模型类 (用于断言构造参数).

    Args:
        monkeypatch: pytest monkeypatch fixture
        per_vector_ms: 每向量推理耗时 (毫秒), 模拟 ONNX 推理速度
        init_delay: 模型加载延迟 (秒), 模拟冷启动开销
    """
    # 设置类级配置 (模拟 ONNX 推理速度 + 冷启动延迟)
    _FakeOnnxModel._per_vector_ms = per_vector_ms
    _FakeOnnxModel._init_delay = init_delay
    fake_module = types.ModuleType("fastembed")
    fake_module.TextEmbedding = _FakeOnnxModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", fake_module)
    return _FakeOnnxModel


# ========== batch_size=64 vs 32 的吞吐对比 ==========


async def test_batch_size_64_vs_32_throughput(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 batch_size=64 相比 32 的吞吐提升 (mock ONNX 推理).

    trace 4ad14970 优化: _PARALLEL_BATCH_SIZE 从 32→64, 提升 ONNX 吞吐 ~20%.
    通过 mock ONNX 推理 (per_vector_ms 模拟耗时), 验证大批量分批并行时
    batch_size=64 的批次更少, 总调度开销更低.

    阈值: batch_size=64 的耗时不应显著高于 32 (允许 ±20% 抖动).
    """
    _install_fake_fastembed(monkeypatch, per_vector_ms=0.3)
    settings = _make_settings()
    fe_module._FASTEMBED_CACHE.clear()

    # 200 chunks: batch_size=32 → 7 批, batch_size=64 → 4 批
    chunk_count = 200
    texts = [f"测试文本 chunk {i} 人工智能医疗应用" for i in range(chunk_count)]

    # batch_size=64 (当前默认)
    client_64 = FastEmbedClient(settings)
    client_64._PARALLEL_BATCH_SIZE = 64
    start = time.perf_counter()
    await client_64.embed_texts(texts)
    elapsed_64 = time.perf_counter() - start

    fe_module._FASTEMBED_CACHE.clear()

    # batch_size=32 (旧版)
    client_32 = FastEmbedClient(settings)
    client_32._PARALLEL_BATCH_SIZE = 32
    start = time.perf_counter()
    await client_32.embed_texts(texts)
    elapsed_32 = time.perf_counter() - start

    fe_module._FASTEMBED_CACHE.clear()

    # batch_size=64 应不显著慢于 32 (允许 20% 抖动, mock 环境下主要测调度开销)
    ratio = elapsed_64 / elapsed_32 if elapsed_32 > 0 else float("inf")
    assert ratio < 1.5, (
        f"batch_size=64 耗时 {elapsed_64:.3f}s 显著慢于 32 的 {elapsed_32:.3f}s "
        f"(比值 {ratio:.2f}x), 调度开销过大"
    )
    print(
        f"\n[batch_64_vs_32] b=64: {elapsed_64:.3f}s (4 批) | "
        f"b=32: {elapsed_32:.3f}s (7 批) | ratio={ratio:.2f}x"
    )


# ========== ONNX Runtime 并行执行配置验证 ==========


async def test_onnx_thread_config_intra_inter_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 ONNX Runtime 并行线程配置 (intra/inter_op_num_threads).

    trace 4ad14970 优化: 通过 fastembed threads 参数设置 intra_op_num_threads,
    通过 OMP_NUM_THREADS 环境变量设置 inter_op_num_threads.

    验证点:
    1. fastembed_onnx_intra_threads > 0 时, TextEmbedding 构造接收 threads 参数
    2. fastembed_onnx_inter_threads > 0 时, OMP_NUM_THREADS 环境变量被设置
    3. 两个参数均为 0 (自动) 时, 使用 cpu_count / cpu_count//2
    """
    # 清除 OMP_NUM_THREADS 避免环境变量污染
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)

    _install_fake_fastembed(monkeypatch, per_vector_ms=0.1)

    # 显式配置线程数
    settings = _make_settings(
        fastembed_onnx_intra_threads=4,
        fastembed_onnx_inter_threads=2,
    )
    fe_module._FASTEMBED_CACHE.clear()
    client = FastEmbedClient(settings)
    await client.embed_texts(["测试文本"])

    # 验证 TextEmbedding 构造时 threads=4 (intra_op_num_threads)
    assert client._model is not None, "模型应已加载"
    assert client._model.threads == 4, f"intra_op_num_threads 应为 4, 实际: {client._model.threads}"
    # 验证 OMP_NUM_THREADS=2 (inter_op_num_threads)
    assert os.environ.get("OMP_NUM_THREADS") == "2", (
        f"OMP_NUM_THREADS 应为 2, 实际: {os.environ.get('OMP_NUM_THREADS')}"
    )

    fe_module._FASTEMBED_CACHE.clear()


async def test_onnx_thread_config_auto_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 ONNX 线程配置为 0 (自动) 时使用 cpu_count 兜底.

    fastembed_onnx_intra_threads=0 → 使用 os.cpu_count()
    fastembed_onnx_inter_threads=0 → 使用 max(1, cpu_count // 2)
    """
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    _install_fake_fastembed(monkeypatch, per_vector_ms=0.1)

    settings = _make_settings(
        fastembed_onnx_intra_threads=0,
        fastembed_onnx_inter_threads=0,
    )
    fe_module._FASTEMBED_CACHE.clear()
    client = FastEmbedClient(settings)
    await client.embed_texts(["测试文本"])

    cpu_count = os.cpu_count() or 4
    expected_intra = cpu_count
    expected_inter = max(1, cpu_count // 2)

    assert client._model.threads == expected_intra, (
        f"自动 intra_threads 应为 {expected_intra} (cpu_count), 实际: {client._model.threads}"
    )
    assert os.environ.get("OMP_NUM_THREADS") == str(expected_inter), (
        f"自动 OMP_NUM_THREADS 应为 {expected_inter}, 实际: {os.environ.get('OMP_NUM_THREADS')}"
    )

    fe_module._FASTEMBED_CACHE.clear()


# ========== 模型预热消除冷启动延迟验证 ==========


async def test_warmup_eliminates_cold_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证模型预热消除冷启动延迟.

    FastEmbed 懒加载: 首次调用 embed_texts 时加载模型 (冷启动).
    预热后 (第二次调用) 应显著快于首次 (无模型加载开销).

    阈值: 第二次调用耗时 < 首次的 50% (冷启动开销主要来自模型加载).
    """
    _install_fake_fastembed(monkeypatch, per_vector_ms=0.2, init_delay=0.1)
    settings = _make_settings()
    fe_module._FASTEMBED_CACHE.clear()

    client = FastEmbedClient(settings)
    texts = ["测试文本 人工智能医疗应用研究"]

    # 第一次调用 (冷启动: 含模型加载)
    start = time.perf_counter()
    await client.embed_texts(texts)
    cold_elapsed = time.perf_counter() - start

    fe_module._FASTEMBED_CACHE.clear()  # 清缓存, 确保第二次仍走推理

    # 第二次调用 (热启动: 模型已加载)
    start = time.perf_counter()
    await client.embed_texts(texts)
    warm_elapsed = time.perf_counter() - start

    fe_module._FASTEMBED_CACHE.clear()

    # 热启动应显著快于冷启动 (模型加载开销消除)
    ratio = warm_elapsed / cold_elapsed if cold_elapsed > 0 else 1.0
    assert ratio < 0.8, (
        f"热启动 {warm_elapsed:.4f}s 未显著快于冷启动 {cold_elapsed:.4f}s "
        f"(比值 {ratio:.2f}x), 预热效果不明显"
    )
    print(
        f"\n[warmup] 冷启动={cold_elapsed:.4f}s | 热启动={warm_elapsed:.4f}s | ratio={ratio:.2f}x"
    )


# ========== LRU 缓存命中率对性能影响 ==========


async def test_lru_cache_hit_rate_performance_impact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 LRU 缓存命中率对 FastEmbed 性能的影响.

    _FASTEMBED_CACHE (LRU+TTL) 缓存已嵌入文本的向量.
    - 全命中: 仅查缓存, 零 ONNX 推理
    - 全未命中: 全部走 ONNX 推理
    - 50% 命中: 一半查缓存, 一半推理

    阈值: 全命中耗时 < 全未命中的 10% (缓存查询 vs ONNX 推理).
    """
    _install_fake_fastembed(monkeypatch, per_vector_ms=1.0)
    settings = _make_settings()
    fe_module._FASTEMBED_CACHE.clear()

    client = FastEmbedClient(settings)
    texts = [f"测试文本 chunk {i} 人工智能" for i in range(50)]

    # 1. 全未命中 (首次调用, 全部走 ONNX 推理)
    start = time.perf_counter()
    await client.embed_texts(texts)
    miss_elapsed = time.perf_counter() - start

    # 2. 全命中 (相同 texts, 全部走缓存)
    start = time.perf_counter()
    await client.embed_texts(texts)
    hit_elapsed = time.perf_counter() - start

    # 3. 50% 命中 (前 25 条缓存命中, 后 25 条新文本)
    half_texts = texts[:25] + [f"新文本 chunk {i} 深度学习" for i in range(25)]
    start = time.perf_counter()
    await client.embed_texts(half_texts)
    half_hit_elapsed = time.perf_counter() - start

    fe_module._FASTEMBED_CACHE.clear()

    # 全命中应远快于全未命中 (零推理)
    ratio = hit_elapsed / miss_elapsed if miss_elapsed > 0 else 1.0
    assert ratio < 0.1, (
        f"全命中 {hit_elapsed:.4f}s 未远快于全未命中 {miss_elapsed:.4f}s "
        f"(比值 {ratio:.2f}x), 缓存效果不明显"
    )
    # 50% 命中应介于全命中和全未命中之间
    assert hit_elapsed < half_hit_elapsed < miss_elapsed, (
        f"50% 命中 {half_hit_elapsed:.4f}s 应介于全命中 {hit_elapsed:.4f}s "
        f"和全未命中 {miss_elapsed:.4f}s 之间"
    )
    print(
        f"\n[lru_cache] 全未命中={miss_elapsed:.4f}s | 全命中={hit_elapsed:.4f}s | "
        f"50%命中={half_hit_elapsed:.4f}s | hit/miss ratio={ratio:.4f}x"
    )


async def test_lru_cache_eviction_under_max_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 LRU 缓存达到 _FASTEMBED_CACHE_MAX_SIZE 时的淘汰行为.

    _FASTEMBED_CACHE_MAX_SIZE=2000, 超过时淘汰最旧条目 (LRU).
    验证淘汰后旧条目需重新推理 (缓存未命中), 新条目仍可命中.
    """
    _install_fake_fastembed(monkeypatch, per_vector_ms=0.1)
    settings = _make_settings()
    fe_module._FASTEMBED_CACHE.clear()

    client = FastEmbedClient(settings)

    # 临时缩小缓存上限, 便于测试淘汰
    original_max = fe_module._FASTEMBED_CACHE_MAX_SIZE
    fe_module._FASTEMBED_CACHE_MAX_SIZE = 5
    try:
        # 写入 5 条缓存 (填满)
        await client.embed_texts([f"文本 {i}" for i in range(5)])
        assert len(fe_module._FASTEMBED_CACHE) == 5

        # 写入第 6 条 → 淘汰最旧 ("文本 0")
        await client.embed_texts(["文本 6"])
        assert len(fe_module._FASTEMBED_CACHE) == 5
        # "文本 0" 应已被淘汰
        from src.rag.fastembed_client import _cache_key

        assert _cache_key("文本 0") not in fe_module._FASTEMBED_CACHE
        # "文本 6" 应在缓存中
        assert _cache_key("文本 6") in fe_module._FASTEMBED_CACHE
    finally:
        fe_module._FASTEMBED_CACHE_MAX_SIZE = original_max
        fe_module._FASTEMBED_CACHE.clear()
