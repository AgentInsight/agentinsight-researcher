"""单元测试: FastEmbed 本地 Embeddings 客户端.

覆盖 src/rag/fastembed_client.py 的所有分支:
- batch_size=64 配置 (_PARALLEL_BATCH_SIZE 从 32→64)
- ONNX Runtime 并行执行 (intra_op_num_threads / inter_op_num_threads 配置)
- 模型预热逻辑 (embed_texts(["预热"]) 首次调用加载模型)
- _ensure_model() 懒加载
- LRU 缓存命中/未命中
- 批量推理分片 (>64 chunks 分多批)
- asyncio.to_thread 卸载到线程池
- 降级策略 (ONNX 初始化失败)
- get_fastembed_client() 单例工厂

AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务 (ONNX 模型/Redis/LLM).
所有 fastembed.TextEmbedding 调用均通过 sys.modules 注入 mock, 不加载真实 ONNX 模型.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture(autouse=True)
def _clear_cache_and_singleton() -> None:
    """每个测试前后清理模块级缓存和单例, 保证测试隔离."""
    from src.rag import fastembed_client as mod

    mod._FASTEMBED_CACHE.clear()
    mod._client = None
    yield
    mod._FASTEMBED_CACHE.clear()
    mod._client = None


@pytest.fixture
def mock_text_embedding_class(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """注入 mock fastembed.TextEmbedding 到 sys.modules.

    fastembed 库在 _ensure_model() 内部通过 `from fastembed import TextEmbedding` 导入,
    通过 sys.modules 注入 mock 模块可拦截该导入, 避免加载真实 ONNX 模型.
    """
    mock_module = MagicMock()
    mock_class = MagicMock()
    mock_module.TextEmbedding = mock_class
    monkeypatch.setitem(sys.modules, "fastembed", mock_module)
    return mock_class


@pytest.fixture
def mock_model(mock_text_embedding_class: MagicMock) -> MagicMock:
    """创建 mock model 实例, 设置 embed side_effect 返回确定性向量.

    model.embed(batch) 返回 list[list[float]], 每个向量基于文本内容生成,
    确保不同文本产生不同向量 (用于缓存命中/未命中验证).
    """
    model = MagicMock()

    def _embed(batch: list[str]) -> list[list[float]]:
        return [[sum(ord(c) for c in t) * 0.001, len(t) * 0.01] for t in batch]

    model.embed.side_effect = _embed
    mock_text_embedding_class.return_value = model
    return model


@pytest.fixture
def patch_anyio_path(monkeypatch: pytest.MonkeyPatch):
    """Patch anyio.Path 在 fastembed_client 模块中, 返回设置函数.

    _ensure_model() 使用 `anyio.Path(path).exists()` 检查本地模型路径,
    此 fixture 替换 anyio 引用为 mock, 避免真实文件系统访问.
    返回 _set_exists 函数, 测试中可动态切换 exists 返回值.
    """
    mock_anyio = MagicMock()
    mock_path_cls = MagicMock()
    mock_path_instance = MagicMock()
    mock_path_instance.exists = AsyncMock(return_value=True)
    mock_path_cls.return_value = mock_path_instance
    mock_anyio.Path = mock_path_cls
    monkeypatch.setattr("src.rag.fastembed_client.anyio", mock_anyio)

    def _set_exists(exists: bool) -> None:
        mock_path_instance.exists = AsyncMock(return_value=exists)

    return _set_exists


@pytest.fixture
def fastembed_client(patch_anyio_path, mock_model: MagicMock):
    """创建 FastEmbedClient 实例 (model 未加载, mock 已就绪).

    依赖 patch_anyio_path + mock_model, 确保 _ensure_model() 调用时能成功加载 mock 模型.
    """
    from src.config.settings import Settings
    from src.rag.fastembed_client import FastEmbedClient

    settings = Settings(_env_file=None)
    return FastEmbedClient(settings)


@pytest.fixture
def track_to_thread(monkeypatch: pytest.MonkeyPatch) -> list:
    """追踪 asyncio.to_thread 调用 (同时执行真实函数).

    返回 calls 列表, 每个元素为 (func, args, kwargs) 元组.
    用于验证小批量/大批量路径的 to_thread 调用次数.
    """
    calls: list[tuple] = []
    real_to_thread = asyncio.to_thread

    async def _wrapped(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr("asyncio.to_thread", _wrapped)
    return calls


# ========== batch_size 配置 ==========


def test_parallel_batch_size_is_64() -> None:
    """验证 _PARALLEL_BATCH_SIZE 为 64 (P2 优化: 32→64)."""
    from src.rag.fastembed_client import FastEmbedClient

    assert FastEmbedClient._PARALLEL_BATCH_SIZE == 64


def test_parallel_batch_threshold_is_32() -> None:
    """验证 _PARALLEL_BATCH_THRESHOLD 为 32 (小批量/大批量分界)."""
    from src.rag.fastembed_client import FastEmbedClient

    assert FastEmbedClient._PARALLEL_BATCH_THRESHOLD == 32


# ========== ONNX Runtime 并行执行 ==========


async def test_onnx_threads_auto_when_zero(
    mock_text_embedding_class: MagicMock,
    patch_anyio_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """测试 ONNX 线程自动配置 (intra=0/inter=0 时用 cpu_count)."""
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    monkeypatch.setattr("os.cpu_count", lambda: 8)

    from src.config.settings import Settings
    from src.rag.fastembed_client import FastEmbedClient

    settings = Settings(
        fastembed_onnx_intra_threads=0,
        fastembed_onnx_inter_threads=0,
        _env_file=None,
    )
    client = FastEmbedClient(settings)
    await client._ensure_model()

    call_kwargs = mock_text_embedding_class.call_args.kwargs
    # intra=0 → 自动使用 cpu_count (8)
    assert call_kwargs["threads"] == 8
    # inter=0 → 自动使用 cpu_count//2 (4)
    assert os.environ.get("OMP_NUM_THREADS") == "4"


async def test_onnx_threads_explicit_values(
    mock_text_embedding_class: MagicMock,
    patch_anyio_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """测试 ONNX 线程显式配置 (使用用户指定值)."""
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)

    from src.config.settings import Settings
    from src.rag.fastembed_client import FastEmbedClient

    settings = Settings(
        fastembed_onnx_intra_threads=6,
        fastembed_onnx_inter_threads=3,
        _env_file=None,
    )
    client = FastEmbedClient(settings)
    await client._ensure_model()

    call_kwargs = mock_text_embedding_class.call_args.kwargs
    assert call_kwargs["threads"] == 6
    assert os.environ.get("OMP_NUM_THREADS") == "3"


async def test_onnx_threads_passed_to_text_embedding(
    mock_text_embedding_class: MagicMock,
    patch_anyio_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """测试 threads 参数传递给 TextEmbedding 构造函数."""
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    monkeypatch.setattr("os.cpu_count", lambda: 16)

    from src.config.settings import Settings
    from src.rag.fastembed_client import FastEmbedClient

    settings = Settings(_env_file=None)
    client = FastEmbedClient(settings)
    await client._ensure_model()

    call_kwargs = mock_text_embedding_class.call_args.kwargs
    assert "threads" in call_kwargs
    # 默认 intra=0 → cpu_count (16)
    assert call_kwargs["threads"] == 16


async def test_omp_num_threads_setdefault_does_not_overwrite(
    mock_text_embedding_class: MagicMock,
    patch_anyio_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """测试 OMP_NUM_THREADS 已存在时 setdefault 不覆盖."""
    monkeypatch.setenv("OMP_NUM_THREADS", "99")

    from src.config.settings import Settings
    from src.rag.fastembed_client import FastEmbedClient

    settings = Settings(_env_file=None)
    client = FastEmbedClient(settings)
    await client._ensure_model()

    # 已存在的 OMP_NUM_THREADS 不应被覆盖
    assert os.environ.get("OMP_NUM_THREADS") == "99"


# ========== _ensure_model() 懒加载 ==========


async def test_ensure_model_lazy_loading(
    mock_text_embedding_class: MagicMock,
    patch_anyio_path,
) -> None:
    """测试模型懒加载: __init__ 不加载模型, _ensure_model() 才加载."""
    from src.config.settings import Settings
    from src.rag.fastembed_client import FastEmbedClient

    settings = Settings(_env_file=None)
    client = FastEmbedClient(settings)

    # 初始状态: 模型未加载
    assert client._model is None
    assert client._initialized is False
    assert mock_text_embedding_class.call_count == 0

    # 调用 _ensure_model() 后: 模型加载
    await client._ensure_model()
    assert client._model is not None
    assert client._initialized is True
    assert mock_text_embedding_class.call_count == 1


async def test_ensure_model_idempotent(
    mock_text_embedding_class: MagicMock,
    patch_anyio_path,
    fastembed_client,
) -> None:
    """测试 _ensure_model() 幂等: 多次调用只加载一次模型."""
    await fastembed_client._ensure_model()
    assert mock_text_embedding_class.call_count == 1

    # 第二次调用不应重新加载
    await fastembed_client._ensure_model()
    assert mock_text_embedding_class.call_count == 1
    assert fastembed_client._initialized is True


async def test_ensure_model_local_path_exists(
    mock_text_embedding_class: MagicMock,
    patch_anyio_path,
) -> None:
    """测试本地模型路径存在时 specific_model_path 传入 kwargs."""
    from src.config.settings import Settings
    from src.rag.fastembed_client import FastEmbedClient

    settings = Settings(_env_file=None)
    client = FastEmbedClient(settings)
    await client._ensure_model()

    call_kwargs = mock_text_embedding_class.call_args.kwargs
    assert "specific_model_path" in call_kwargs
    assert call_kwargs["specific_model_path"] == settings.fastembed_model_path


async def test_ensure_model_local_path_not_exists(
    mock_text_embedding_class: MagicMock,
    patch_anyio_path,
) -> None:
    """测试本地模型路径不存在时不传 specific_model_path."""
    from src.config.settings import Settings
    from src.rag.fastembed_client import FastEmbedClient

    settings = Settings(_env_file=None)
    client = FastEmbedClient(settings)
    patch_anyio_path(False)  # 本地模型路径不存在
    await client._ensure_model()

    call_kwargs = mock_text_embedding_class.call_args.kwargs
    assert "specific_model_path" not in call_kwargs


# ========== 降级策略 (ONNX 初始化失败) ==========


async def test_onnx_init_failure_sets_load_failed(
    mock_text_embedding_class: MagicMock,
    patch_anyio_path,
) -> None:
    """测试 ONNX 初始化失败时设置 _load_failed 标志并抛出异常."""
    from src.config.settings import Settings
    from src.rag.fastembed_client import FastEmbedClient

    mock_text_embedding_class.side_effect = RuntimeError("ONNX init failed")
    settings = Settings(_env_file=None)
    client = FastEmbedClient(settings)

    # 第一次调用: 抛出原始异常
    with pytest.raises(RuntimeError, match="ONNX init failed"):
        await client._ensure_model()

    assert client._load_failed is True
    assert client._initialized is False


async def test_onnx_init_failure_subsequent_calls_raise_runtime_error(
    mock_text_embedding_class: MagicMock,
    patch_anyio_path,
) -> None:
    """测试 _load_failed=True 后再次调用抛出 RuntimeError."""
    from src.config.settings import Settings
    from src.rag.fastembed_client import FastEmbedClient

    mock_text_embedding_class.side_effect = RuntimeError("ONNX init failed")
    settings = Settings(_env_file=None)
    client = FastEmbedClient(settings)

    # 第一次调用失败
    with pytest.raises(RuntimeError, match="ONNX init failed"):
        await client._ensure_model()

    # 第二次调用: 抛出 "FastEmbed 模型加载失败" (不再尝试加载)
    with pytest.raises(RuntimeError, match="FastEmbed 模型加载失败"):
        await client._ensure_model()

    # TextEmbedding 只被调用一次 (第二次不再尝试)
    assert mock_text_embedding_class.call_count == 1


async def test_embed_texts_raises_on_model_failure(
    mock_text_embedding_class: MagicMock,
    patch_anyio_path,
) -> None:
    """测试 embed_texts 在模型加载失败时抛出异常."""
    from src.config.settings import Settings
    from src.rag.fastembed_client import FastEmbedClient

    mock_text_embedding_class.side_effect = RuntimeError("ONNX init failed")
    settings = Settings(_env_file=None)
    client = FastEmbedClient(settings)

    with pytest.raises(RuntimeError, match="ONNX init failed"):
        await client.embed_texts(["测试文本"])


# ========== 模型预热逻辑 ==========


async def test_warmup_first_call_loads_model(
    mock_text_embedding_class: MagicMock,
    patch_anyio_path,
    fastembed_client,
) -> None:
    """测试 embed_texts(["预热"]) 首次调用触发模型加载."""
    assert fastembed_client._initialized is False
    assert mock_text_embedding_class.call_count == 0

    await fastembed_client.embed_texts(["预热"])

    assert fastembed_client._initialized is True
    assert fastembed_client._model is not None
    assert mock_text_embedding_class.call_count == 1


# ========== LRU 缓存命中/未命中 ==========


def test_cache_key_deterministic() -> None:
    """测试 _cache_key 对相同文本生成相同 key (SHA256)."""
    from src.rag.fastembed_client import _cache_key

    k1 = _cache_key("hello")
    k2 = _cache_key("hello")
    k3 = _cache_key("world")
    assert k1 == k2
    assert k1 != k3


async def test_cache_miss_calls_model(
    fastembed_client,
    mock_model: MagicMock,
) -> None:
    """测试缓存未命中时调用 model.embed."""
    await fastembed_client.embed_texts(["测试文本"])

    assert mock_model.embed.call_count == 1


async def test_cache_hit_avoids_model_call(
    fastembed_client,
    mock_model: MagicMock,
) -> None:
    """测试缓存命中时不调用 model.embed."""
    # 第一次调用: 缓存未命中, 调用 model
    await fastembed_client.embed_texts(["缓存测试"])
    assert mock_model.embed.call_count == 1

    # 第二次调用相同文本: 缓存命中, 不调用 model
    await fastembed_client.embed_texts(["缓存测试"])
    assert mock_model.embed.call_count == 1  # 仍然只调用了一次


async def test_cache_partial_hit(
    fastembed_client,
    mock_model: MagicMock,
) -> None:
    """测试部分缓存命中: 已缓存文本不传给 model, 仅未缓存文本传给 model."""
    # 先缓存 "文本A"
    await fastembed_client.embed_texts(["文本A"])
    assert mock_model.embed.call_count == 1

    # 混合调用: "文本A" (已缓存) + "文本B" (未缓存)
    results = await fastembed_client.embed_texts(["文本A", "文本B"])
    # model.embed 只被调用一次 (仅 "文本B"), batch=["文本B"]
    assert mock_model.embed.call_count == 2
    called_batch = mock_model.embed.call_args_list[-1].args[0]
    assert called_batch == ["文本B"]
    # 返回两个向量
    assert len(results) == 2


async def test_cache_all_hit_skips_model_load(
    mock_text_embedding_class: MagicMock,
    patch_anyio_path,
    fastembed_client,
    mock_model: MagicMock,
) -> None:
    """测试全缓存命中时不加载模型 (不调用 _ensure_model)."""
    # 先缓存文本
    await fastembed_client.embed_texts(["预缓存"])
    assert mock_text_embedding_class.call_count == 1

    # 再次调用相同文本: 全命中, 不应再调用 model
    results = await fastembed_client.embed_texts(["预缓存"])
    assert mock_text_embedding_class.call_count == 1  # 未重新加载
    assert mock_model.embed.call_count == 1  # 未重新推理
    assert len(results) == 1


def test_cache_lru_eviction(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试 LRU 缓存淘汰 (超出最大容量时淘汰最久未访问)."""
    from src.rag.fastembed_client import _cache_get, _cache_set

    monkeypatch.setattr("src.rag.fastembed_client._FASTEMBED_CACHE_MAX_SIZE", 3)

    _cache_set("k1", [1.0])
    _cache_set("k2", [2.0])
    _cache_set("k3", [3.0])

    # 访问 k1, 使其成为最近使用 (k2 变为最久未使用)
    assert _cache_get("k1") == [1.0]

    # 添加 k4, 应淘汰 k2 (LRU)
    _cache_set("k4", [4.0])

    assert _cache_get("k1") == [1.0]  # 仍在缓存中 (刚访问过)
    assert _cache_get("k2") is None  # 已被淘汰
    assert _cache_get("k3") == [3.0]  # 仍在缓存中
    assert _cache_get("k4") == [4.0]  # 新添加


def test_cache_ttl_expiry() -> None:
    """测试缓存 TTL 过期: 超时条目被删除并返回 None."""
    from src.rag.fastembed_client import (
        _FASTEMBED_CACHE,
        _FASTEMBED_CACHE_TTL,
        _cache_get,
        _cache_set,
    )

    _cache_set("fresh_key", [1.0])
    # 手动设置过期时间戳 (TTL 默认 3600 秒)
    _FASTEMBED_CACHE["stale_key"] = {
        "vector": [2.0],
        "ts": time.time() - _FASTEMBED_CACHE_TTL - 1,
    }

    # 新鲜条目: 命中
    assert _cache_get("fresh_key") == [1.0]
    # 过期条目: 未命中 (且被删除)
    assert _cache_get("stale_key") is None
    assert "stale_key" not in _FASTEMBED_CACHE


def test_cache_get_moves_to_end() -> None:
    """测试 _cache_get 命中时将条目移到末尾 (LRU 更新访问顺序)."""
    from src.rag.fastembed_client import _FASTEMBED_CACHE, _cache_get, _cache_set

    _cache_set("k1", [1.0])
    _cache_set("k2", [2.0])

    # k1 在 k2 之前 (k1 是最久未使用)
    assert list(_FASTEMBED_CACHE.keys()) == ["k1", "k2"]

    # 访问 k1, 应移到末尾
    _cache_get("k1")
    assert list(_FASTEMBED_CACHE.keys()) == ["k2", "k1"]


# ========== 批量推理分片 ==========


async def test_batch_small_uses_single_to_thread(
    fastembed_client,
    mock_model: MagicMock,
    track_to_thread: list,
) -> None:
    """测试小批量 (< 32) 使用单次 asyncio.to_thread."""
    texts = [f"文本{i}" for i in range(10)]
    await fastembed_client.embed_texts(texts)

    # 小批量: 1 次 to_thread 调用
    assert len(track_to_thread) == 1
    # model.embed 调用 1 次
    assert mock_model.embed.call_count == 1


async def test_batch_threshold_uses_gather(
    fastembed_client,
    mock_model: MagicMock,
    track_to_thread: list,
) -> None:
    """测试批量 >= 阈值 (32) 时使用 asyncio.gather 分批并行."""
    texts = [f"文本{i}" for i in range(32)]
    await fastembed_client.embed_texts(texts)

    # 32 texts, batch_size=64 → 1 batch, 但走 gather 路径
    # to_thread 调用 1 次 (1 batch)
    assert len(track_to_thread) == 1
    assert mock_model.embed.call_count == 1


async def test_batch_over_64_split_multiple_batches(
    fastembed_client,
    mock_model: MagicMock,
    track_to_thread: list,
) -> None:
    """测试 > 64 chunks 分多批: 100 texts → 2 batches (64 + 36)."""
    texts = [f"文本{i}" for i in range(100)]
    results = await fastembed_client.embed_texts(texts)

    # 100 texts, batch_size=64 → ceil(100/64) = 2 batches
    assert len(track_to_thread) == 2
    assert mock_model.embed.call_count == 2
    # 返回向量数 = 输入文本数
    assert len(results) == 100


async def test_batch_200_split_into_4_batches(
    fastembed_client,
    mock_model: MagicMock,
    track_to_thread: list,
) -> None:
    """测试 200 chunks 分 4 批 (64×3 + 8)."""
    texts = [f"文本{i}" for i in range(200)]
    results = await fastembed_client.embed_texts(texts)

    # 200 texts, batch_size=64 → ceil(200/64) = 4 batches
    assert len(track_to_thread) == 4
    assert mock_model.embed.call_count == 4
    assert len(results) == 200


async def test_batch_results_order_preserved(
    fastembed_client,
    mock_model: MagicMock,
) -> None:
    """测试分批并行后结果顺序与输入一致."""
    texts = [f"文本{i}" for i in range(100)]
    results = await fastembed_client.embed_texts(texts)

    # 验证每个结果对应正确的文本 (mock_model.embed 基于文本内容生成向量)
    for i, (text, vec) in enumerate(zip(texts, results, strict=True)):
        expected = [sum(ord(c) for c in text) * 0.001, len(text) * 0.01]
        assert vec == expected, f"索引 {i} 的向量不匹配"


# ========== asyncio.to_thread 卸载到线程池 ==========


async def test_to_thread_offloads_sync_embed(
    fastembed_client,
    mock_model: MagicMock,
    track_to_thread: list,
) -> None:
    """测试 sync embed 调用通过 asyncio.to_thread 卸载到线程池."""
    await fastembed_client.embed_texts(["卸载测试"])

    # 验证 asyncio.to_thread 被调用
    assert len(track_to_thread) == 1
    func, args, kwargs = track_to_thread[0]
    # 第一个参数应是 _embed_batch 闭包, 第二个是文本列表
    assert callable(func)
    assert args == (["卸载测试"],)


# ========== embed_texts / embed_text 基本功能 ==========


async def test_embed_texts_empty_returns_empty_list(fastembed_client) -> None:
    """测试空文本列表返回空列表."""
    result = await fastembed_client.embed_texts([])
    assert result == []


async def test_embed_text_single_returns_vector(fastembed_client) -> None:
    """测试 embed_text 单条文本返回单个向量."""
    vec = await fastembed_client.embed_text("单条测试")
    assert isinstance(vec, list)
    assert len(vec) == 2  # mock_model 返回 2 维向量


async def test_embed_texts_returns_correct_dimension(fastembed_client) -> None:
    """测试 embed_texts 返回向量维度正确."""
    results = await fastembed_client.embed_texts(["维度测试"])
    assert len(results) == 1
    assert len(results[0]) == 2  # mock_model 返回 2 维


def test_dimension_property() -> None:
    """测试 dimension 属性返回 settings.fastembed_dimension."""
    from src.config.settings import Settings
    from src.rag.fastembed_client import FastEmbedClient

    settings = Settings(_env_file=None)
    client = FastEmbedClient(settings)
    assert client.dimension == settings.fastembed_dimension
    assert client.dimension == 512  # bge-small-zh-v1.5 固定 512 维


# ========== get_fastembed_client() 单例工厂 ==========


def test_get_fastembed_client_returns_singleton() -> None:
    """测试 get_fastembed_client 返回全局单例."""
    from src.rag.fastembed_client import FastEmbedClient, get_fastembed_client

    c1 = get_fastembed_client()
    c2 = get_fastembed_client()
    assert c1 is c2
    assert isinstance(c1, FastEmbedClient)


def test_get_fastembed_client_creates_new_after_reset() -> None:
    """测试单例重置后创建新实例."""
    from src.rag import fastembed_client as mod
    from src.rag.fastembed_client import get_fastembed_client

    c1 = get_fastembed_client()
    mod._client = None  # 模拟重置
    c2 = get_fastembed_client()
    assert c1 is not c2
