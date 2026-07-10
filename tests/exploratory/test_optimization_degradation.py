"""探索性测试: 8 项优化的降级场景.

AGENTS.md 第 7/9/13 章硬约束:
- FastEmbed 加载失败时降级到远程 TEI (EmbeddingsClient)
- Redis 不可用时应降级无缓存, 不阻断检索
- exa-search timeout=10s 超时降级 (返回空列表)
- curator max_tokens=2000 截断处理 (P0: 4000→2000)
- ONNX Runtime 线程配置无效时降级

本测试使用 mock 模拟异常场景, 不依赖容器栈.
标记为 exploratory + unit (unit 确保 conftest 不跳过).

执行方式:
    pytest tests/exploratory/test_optimization_degradation.py -v -m exploratory -s
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.config.settings import Settings

pytestmark = [pytest.mark.exploratory, pytest.mark.unit]


def _make_settings(**overrides: object) -> Settings:
    """构造测试 Settings (跳过 .env 加载)."""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


# ========== FastEmbed ONNX 初始化失败时降级 ==========


async def test_fastembed_onnx_init_failure_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """降级: FastEmbed ONNX 初始化失败时应抛出 RuntimeError.

    FastEmbedClient._ensure_model 在模型加载失败时设置 _load_failed=True
    并重新抛出异常. 后续调用直接抛出 RuntimeError("FastEmbed 模型加载失败").

    ContextManager._embeddings_rerank 捕获该异常并降级返回 BM25 结果.
    """
    from src.rag import fastembed_client as fe_module
    from src.rag.fastembed_client import FastEmbedClient

    # 安装会抛异常的 fake fastembed 模块
    fake_module = types.ModuleType("fastembed")

    class _FailingTextEmbedding:
        def __init__(self, **_kwargs: object) -> None:
            raise RuntimeError("ONNX Runtime 初始化失败: 模型文件损坏")

    fake_module.TextEmbedding = _FailingTextEmbedding  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", fake_module)

    settings = _make_settings()
    fe_module._FASTEMBED_CACHE.clear()
    client = FastEmbedClient(settings)

    # 首次调用: _ensure_model 尝试加载模型失败, 重新抛出原始异常
    # (源码: except Exception → _load_failed=True → raise)
    with pytest.raises(RuntimeError, match="ONNX Runtime 初始化失败"):
        await client.embed_texts(["测试文本"])

    # 后续调用: _load_failed=True, 直接抛出 RuntimeError("FastEmbed 模型加载失败")
    with pytest.raises(RuntimeError, match="FastEmbed 模型加载失败"):
        await client.embed_texts(["测试文本 2"])

    fe_module._FASTEMBED_CACHE.clear()


async def test_fastembed_failure_degrades_to_bm25_in_rerank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """降级: FastEmbed 精排失败时 _embeddings_rerank 降级返回 BM25 结果.

    ContextManager._embeddings_rerank 捕获 FastEmbed 异常,
    降级返回原 BM25 结果前 N 条 (不阻断检索流程).
    """
    from src.skills.researcher.context_manager import ContextManager

    settings = _make_settings()
    cm = ContextManager(settings)

    # mock FastEmbed 抛出异常
    cm._fastembed = MagicMock()
    cm._fastembed.embed_texts = AsyncMock(side_effect=RuntimeError("ONNX 推理失败"))

    documents = ["文档1 内容", "文档2 内容", "文档3 内容"]

    # _embeddings_rerank 应捕获异常并降级返回 BM25 结果
    result = await cm._embeddings_rerank(
        "查询", documents, max_results=2, user_id="test", session_id="test"
    )

    assert result == documents[:2], f"FastEmbed 失败应降级返回 BM25 结果前 2 条, 实际: {result}"


# ========== Redis 不可用时搜索缓存降级 ==========


async def test_redis_unavailable_search_cache_degrades() -> None:
    """降级: Redis 不可用时 _cached_search 降级为直接搜索.

    AGENTS.md 第 7 章: Redis 不可用时应降级无缓存, 不阻断检索.
    get_redis_client 返回 None 时, _cached_search 跳过缓存直接调用 searcher.
    """
    from src.skills.researcher.research_conductor import ResearchConductor

    settings = _make_settings(search_cache_ttl=300)
    mock_llm = MagicMock()
    mock_cm = MagicMock()
    mock_pf = MagicMock()
    conductor = ResearchConductor(
        settings=settings,
        llm=mock_llm,  # type: ignore[arg-type]
        context_manager=mock_cm,  # type: ignore[arg-type]
        prompt_family=mock_pf,  # type: ignore[arg-type]
    )

    # mock searcher
    search_calls: list[str] = []

    class _MockSearcher:
        name = "test_engine"

        async def search(
            self, query: str, *, max_results: int = 5, **_kw: Any
        ) -> list[dict[str, Any]]:
            search_calls.append(query)
            return [{"title": f"结果 for {query}", "url": "http://example.com"}]

    searcher = _MockSearcher()

    # mock get_redis_client 返回 None (Redis 不可用)
    with patch(
        "src.skills.researcher.research_conductor.get_redis_client",
        new=AsyncMock(return_value=None),
    ):
        # 第一次搜索 (无缓存, 直接搜索)
        result_1 = await conductor._cached_search(
            searcher, "测试查询", max_results=5, query_domains=None, user_id="test"
        )
        # 第二次搜索 (仍无缓存, 再次直接搜索)
        result_2 = await conductor._cached_search(
            searcher, "测试查询", max_results=5, query_domains=None, user_id="test"
        )

    assert len(result_1) == 1, "第一次搜索应返回 1 条结果"
    assert len(result_2) == 1, "第二次搜索应返回 1 条结果"
    assert len(search_calls) == 2, (
        f"Redis 不可用时每次都应直接搜索 (2 次), 实际: {len(search_calls)} 次"
    )


async def test_redis_get_exception_degrades_to_direct_search() -> None:
    """降级: Redis get 异常时应降级直接搜索 (不阻断).

    _cached_search 第 1 步 try/except 捕获 Redis get 异常, 降级为直接搜索.
    """
    from src.skills.researcher.research_conductor import ResearchConductor

    settings = _make_settings(search_cache_ttl=300)
    mock_llm = MagicMock()
    mock_cm = MagicMock()
    mock_pf = MagicMock()
    conductor = ResearchConductor(
        settings=settings,
        llm=mock_llm,  # type: ignore[arg-type]
        context_manager=mock_cm,  # type: ignore[arg-type]
        prompt_family=mock_pf,  # type: ignore[arg-type]
    )

    class _FailingRedis:
        async def get(self, key: str) -> str | None:
            raise ConnectionError("Redis 连接断开")

        async def setex(self, key: str, ttl: int, value: str) -> None:
            raise ConnectionError("Redis 连接断开")

    class _MockSearcher:
        name = "test_engine"

        async def search(
            self, query: str, *, max_results: int = 5, **_kw: Any
        ) -> list[dict[str, Any]]:
            return [{"title": "搜索结果", "url": "http://example.com"}]

    with patch(
        "src.skills.researcher.research_conductor.get_redis_client",
        new=AsyncMock(return_value=_FailingRedis()),
    ):
        result = await conductor._cached_search(
            _MockSearcher(), "测试查询", max_results=5, query_domains=None, user_id="test"
        )

    assert len(result) == 1, "Redis 异常时应降级直接搜索并返回结果"


# ========== exa-search timeout 10s 超时降级 ==========


async def test_exa_search_timeout_returns_empty() -> None:
    """降级: exa-search 超时 (10s) 应返回空列表, 不抛异常.

    ExaSearcher 构造函数 timeout=10.0s (P1: 15s→10s, trace 4ad14970 优化).
    超时后 httpx.TimeoutException 被 except Exception 捕获, 返回 [].
    """
    from src.skills.researcher.searchers.exa import ExaSearcher

    settings = _make_settings(exa_api_key="test_key")
    searcher = ExaSearcher(settings)

    # mock httpx.AsyncClient.post 抛出超时异常
    async def _timeout_post(*_args: object, **_kwargs: object) -> Any:
        raise httpx.TimeoutException("请求超时 (10s)")

    searcher._client = MagicMock()
    searcher._client.post = _timeout_post
    searcher._client.aclose = AsyncMock()

    try:
        result = await searcher.search("测试查询", max_results=5)

        assert result == [], f"Exa 搜索超时应返回空列表, 实际: {result}"
    finally:
        await searcher.close()


async def test_exa_search_timeout_is_10s() -> None:
    """验证 ExaSearcher 客户端超时配置为 10s (P1: 15s→10s).

    trace 4ad14970 优化: 消除 >10s 离群点, timeout 从 15s 降到 10s.
    """
    from src.skills.researcher.searchers.exa import ExaSearcher

    settings = _make_settings(exa_api_key="test_key")
    searcher = ExaSearcher(settings)

    assert searcher._client.timeout.read == 10.0, (
        f"Exa 客户端 read timeout 应为 10.0s, 实际: {searcher._client.timeout.read}"
    )

    await searcher.close()


async def test_exa_search_no_api_key_returns_empty() -> None:
    """降级: Exa API Key 未配置时应跳过搜索, 返回空列表.

    ExaSearcher.search 在 _api_key 为空时直接返回 [], 不调用 API.
    """
    from src.skills.researcher.searchers.exa import ExaSearcher

    settings = _make_settings(exa_api_key=None)
    searcher = ExaSearcher(settings)

    try:
        result = await searcher.search("测试查询", max_results=5)
        assert result == [], "无 API Key 应返回空列表"
    finally:
        await searcher.close()


# ========== curator max_tokens=2000 截断处理 ==========


async def test_curator_max_tokens_2000_passed_to_llm() -> None:
    """验证 curator 调用 LLM 时 max_tokens=2000 (P0: 4000→2000).

    trace 4ad14970 优化: 策展 JSON 仅需 index+score, 不需要长输出.
    max_tokens 从 4000 降到 2000, 节省 token 成本.
    """
    from src.skills.researcher.source_curator import SourceCurator

    settings = _make_settings()
    mock_llm = MagicMock()

    # mock LLM 返回策展 JSON
    class _FakeResponse:
        content = '[{"index": 1, "score": 8, "reason": "高相关"}]'

    mock_llm.achat = AsyncMock(return_value=_FakeResponse())
    mock_pf = MagicMock()
    mock_pf.curator_prompt = MagicMock(return_value="策展 prompt")

    curator = SourceCurator(
        settings=settings,
        llm=mock_llm,
        prompt_family=mock_pf,  # type: ignore[arg-type]
    )

    sources = [
        {
            "title": "测试来源",
            "url": "https://arxiv.org/abs/1234",
            "snippet": "人工智能医疗应用研究",
            "content": "这是一篇关于 AI 医疗的研究论文",
        }
    ]

    await curator.curate_sources("AI 医疗", sources, max_results=5)

    # 验证 LLM 调用参数中 max_tokens=2000
    call_kwargs = mock_llm.achat.call_args.kwargs
    assert call_kwargs.get("max_tokens") == 2000, (
        f"curator max_tokens 应为 2000, 实际: {call_kwargs.get('max_tokens')}"
    )


async def test_curator_max_tokens_2000_truncates_long_output() -> None:
    """降级: curator LLM 输出超过 max_tokens=2000 时应安全解析.

    LLM 输出可能因 max_tokens=2000 截断导致 JSON 不完整.
    safe_json_parse 应处理截断的 JSON (fallback 返回空列表).
    截断后 curate_sources 降级为按可信度排序返回.
    """
    from src.skills.researcher.source_curator import SourceCurator

    settings = _make_settings()
    mock_llm = MagicMock()

    # mock LLM 返回截断的 JSON (max_tokens=2000 导致截断)
    class _FakeResponse:
        # 截断的 JSON (不完整的数组)
        content = '[{"index": 1, "score": 8, "reason": "高相关"}, {"index": 2, "sc'

    mock_llm.achat = AsyncMock(return_value=_FakeResponse())
    mock_pf = MagicMock()
    mock_pf.curator_prompt = MagicMock(return_value="策展 prompt")

    curator = SourceCurator(
        settings=settings,
        llm=mock_llm,
        prompt_family=mock_pf,  # type: ignore[arg-type]
    )

    sources = [
        {
            "title": f"来源 {i}",
            "url": f"https://arxiv.org/abs/{i}",
            "snippet": f"研究内容 {i}",
            "content": f"这是一篇研究论文 {i}",
        }
        for i in range(1, 4)
    ]

    # 截断 JSON 解析失败 → 降级按可信度排序返回
    result = await curator.curate_sources("AI 研究", sources, max_results=5)

    # 应返回降级结果 (按可信度排序, 非空)
    assert len(result) > 0, "截断 JSON 应降级返回可信度排序结果"
    assert len(result) <= 5, "结果不应超过 max_results=5"
    # 每条结果应有 credibility_score
    for item in result:
        assert "credibility_score" in item, "降级结果应含 credibility_score"


# ========== ONNX Runtime 线程配置无效时降级 ==========


async def test_onnx_invalid_thread_config_falls_back_to_auto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """降级: ONNX 线程配置为 0 (自动) 时使用 cpu_count 兜底.

    fastembed_onnx_intra_threads=0 → 使用 os.cpu_count()
    fastembed_onnx_inter_threads=0 → 使用 max(1, cpu_count // 2)

    这不是"无效"配置, 而是自动兜底机制 (0 表示自动).
    """
    import os

    from src.rag import fastembed_client as fe_module
    from src.rag.fastembed_client import FastEmbedClient

    # 安装 fake fastembed 模块
    fake_module = types.ModuleType("fastembed")

    class _RecordingTextEmbedding:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.01] * 512 for _ in texts]

    fake_module.TextEmbedding = _RecordingTextEmbedding  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", fake_module)
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)

    settings = _make_settings(
        fastembed_onnx_intra_threads=0,
        fastembed_onnx_inter_threads=0,
    )
    fe_module._FASTEMBED_CACHE.clear()
    client = FastEmbedClient(settings)
    await client.embed_texts(["测试文本"])

    cpu_count = os.cpu_count() or 4
    # intra=0 → cpu_count, inter=0 → cpu_count//2
    assert client._model.kwargs.get("threads") == cpu_count, (
        f"intra_threads=0 应兜底为 cpu_count={cpu_count}, "
        f"实际: {client._model.kwargs.get('threads')}"
    )
    assert os.environ.get("OMP_NUM_THREADS") == str(max(1, cpu_count // 2)), (
        f"inter_threads=0 应兜底为 cpu_count//2={max(1, cpu_count // 2)}, "
        f"实际: {os.environ.get('OMP_NUM_THREADS')}"
    )

    fe_module._FASTEMBED_CACHE.clear()


async def test_onnx_negative_thread_config_handled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """降级: ONNX 线程配置为负数时应使用 0 传入 (fastembed 内部兜底).

    Settings 中 fastembed_onnx_intra_threads 为负数时,
    _ensure_model 的条件判断 (> 0) 不满足, 使用 cpu_count 兜底.
    """
    import os

    from src.rag import fastembed_client as fe_module
    from src.rag.fastembed_client import FastEmbedClient

    fake_module = types.ModuleType("fastembed")

    class _RecordingTextEmbedding:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.01] * 512 for _ in texts]

    fake_module.TextEmbedding = _RecordingTextEmbedding  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", fake_module)
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)

    # 负数线程配置 (异常值)
    settings = _make_settings(
        fastembed_onnx_intra_threads=-1,
        fastembed_onnx_inter_threads=-2,
    )
    fe_module._FASTEMBED_CACHE.clear()
    client = FastEmbedClient(settings)
    await client.embed_texts(["测试文本"])

    cpu_count = os.cpu_count() or 4
    # 负数不满足 > 0 条件, 使用 cpu_count 兜底
    assert client._model.kwargs.get("threads") == cpu_count, (
        f"负数 intra_threads 应兜底为 cpu_count={cpu_count}, "
        f"实际: {client._model.kwargs.get('threads')}"
    )

    fe_module._FASTEMBED_CACHE.clear()
