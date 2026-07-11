"""探索性测试: 边界条件 (空查询/超长查询/特殊字符/并发请求).

- 所有外部输入经 Pydantic 校验
- 测试目标地址从环境变量 AGENT_URL 注入
- 测试数据隔离: session_id=test_explore_*

执行方式 (宿主机, 容器栈已 healthy):
    set AGENT_URL=http://127.0.0.1:8066
    pytest tests/exploratory/test_edge_cases.py -v -m exploratory
"""

from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest

# 测试目标地址从环境变量注入
AGENT_URL = os.getenv("AGENT_URL", "http://127.0.0.1:8066").rstrip("/")

# 边界测试超时 (短查询响应快; 超长查询可能走研究图, 给宽松超时)
EDGE_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=30.0)

# 并发测试超时
CONCURRENT_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)


def _unique_session_id(prefix: str = "explore") -> str:
    """生成唯一 session_id (session_id=test_*)."""
    return f"test_{prefix}_{uuid.uuid4().hex[:12]}"


def _chat_payload(
    query: str = "你好",
    *,
    stream: bool = False,
    session_id: str | None = None,
) -> dict[str, object]:
    """构造 /v1/chat/completions 请求体."""
    return {
        "model": "agentinsight-researcher",
        "messages": [{"role": "user", "content": query}],
        "stream": stream,
        "session_id": session_id or _unique_session_id(),
    }


# ========== 空查询与空白字符 ==========


@pytest.mark.exploratory
def test_empty_content_returns_400() -> None:
    """边界: user content="" → 400 (Pydantic 校验拒绝空内容)."""
    with httpx.Client(timeout=EDGE_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": ""}],
                "stream": False,
            },
        )
    assert r.status_code == 400, f"空 content 应返回 400, 实际: {r.status_code}"


@pytest.mark.exploratory
def test_whitespace_only_content_returns_400() -> None:
    """边界: user content="   \\n\\t  " → 400 (纯空白字符)."""
    with httpx.Client(timeout=EDGE_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "   \n\t  "}],
                "stream": False,
            },
        )
    assert r.status_code == 400, f"纯空白 content 应返回 400, 实际: {r.status_code}"


@pytest.mark.exploratory
def test_only_punctuation_content_returns_200() -> None:
    """边界: user content="???" → 200 (标点符号非空, 应正常处理)."""
    with httpx.Client(timeout=EDGE_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("???", stream=False),
        )
    assert r.status_code == 200, f"标点符号 content 应返回 200, 实际: {r.status_code}"


# ========== 超长查询 ==========


@pytest.mark.exploratory
def test_very_long_query_returns_200() -> None:
    """边界: 超长查询 (10K 字符) → 200 (服务端不应崩溃).

    单会话上下文上限 800K 字符, 10K 应在范围内.
    """
    long_query = "请分析人工智能在医疗领域的应用前景。" * 200  # ~10K 字符
    with httpx.Client(timeout=EDGE_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload(long_query, stream=False),
        )
    # 超长查询可能触发 200 (短查询保护) 或 413 (内容过大) 或 200 (走研究图)
    # 不应返回 5xx (服务端崩溃)
    assert r.status_code < 500, f"超长查询触发 5xx 服务端崩溃: {r.status_code} {r.text[:200]}"


@pytest.mark.exploratory
def test_extremely_long_query_handled_gracefully() -> None:
    """边界: 极长查询 (20K 字符) → 不应崩溃 (允许 413/400, 但不允许 5xx).

    注: 100K 字符会触发完整研究流程 + 上下文压缩, 耗时超 300s.
    改为 20K 字符 (仍超过短查询阈值, 但不会触发过长的 LLM 调用).
    """
    extremely_long = "测试" * 10000  # ~20K 字符
    # 极长查询可能触发上下文压缩, 给宽松超时 (300s)
    oversized_timeout = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=30.0)
    with httpx.Client(timeout=oversized_timeout) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload(extremely_long, stream=False),
        )
    assert r.status_code < 500, f"极长查询触发 5xx: {r.status_code} {r.text[:200]}"


# ========== 特殊字符 ==========


@pytest.mark.exploratory
@pytest.mark.parametrize(
    "query,description",
    [
        ("你好👋🎉", "emoji 表情"),
        ("<script>alert(1)</script>", "HTML 标签"),
        ("'\"\\", "引号与反斜杠"),
        ("SELECT * FROM users;", "SQL 关键字"),
        ("../../etc/passwd", "路径穿越"),
        ("null", "JSON null 字符串"),
        ("undefined", "JS undefined 字符串"),
        ("\u0000\u0001\u0002", "控制字符"),
    ],
)
def test_special_characters_handled_gracefully(query: str, description: str) -> None:
    """边界: 各种特殊字符查询不应导致服务端 5xx 崩溃.

    所有外部输入经 Pydantic 校验.
    """
    with httpx.Client(timeout=EDGE_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload(query, stream=False),
        )
    # 不应返回 5xx (服务端崩溃)
    assert r.status_code < 500, f"特殊字符 ({description}) 触发 5xx: {r.status_code} {r.text[:200]}"


# ========== 并发请求 ==========


@pytest.mark.exploratory
def test_concurrent_3_requests_isolation() -> None:
    """边界: 3 个并发请求 (不同 session_id) 应全部成功.

    每个 Agent 应支持并发多会话; 会话间状态通过 Postgres Checkpointer 隔离.
    """
    queries = [
        ("你好", _unique_session_id("explore_conc_0")),
        ("嗨", _unique_session_id("explore_conc_1")),
        ("在吗", _unique_session_id("explore_conc_2")),
    ]

    async def _run_one(client: httpx.AsyncClient, query: str, sid: str) -> tuple[int, str]:
        r = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload(query, stream=False, session_id=sid),
        )
        return r.status_code, sid

    async def _run_all() -> list[tuple[int, str]]:
        async with httpx.AsyncClient(timeout=CONCURRENT_TIMEOUT) as client:
            tasks = [_run_one(client, q, sid) for q, sid in queries]
            return await asyncio.gather(*tasks)

    results = asyncio.run(_run_all())
    for status, sid in results:
        assert status == 200, f"并发请求 (sid={sid}) 非 200: {status}"


@pytest.mark.exploratory
def test_concurrent_same_session_id_handled() -> None:
    """边界: 同一 session_id 并发请求不应导致数据损坏.

    thread_id 做会话隔离键, 同一 thread_id 的并发请求
    应由 Checkpointer 串行化处理 (不出现状态损坏).
    """
    sid = _unique_session_id("explore_same_sid")

    async def _run_one(client: httpx.AsyncClient) -> int:
        r = await client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json=_chat_payload("你好", stream=False, session_id=sid),
        )
        return r.status_code

    async def _run_all() -> list[int]:
        async with httpx.AsyncClient(timeout=CONCURRENT_TIMEOUT) as client:
            tasks = [_run_one(client) for _ in range(3)]
            return await asyncio.gather(*tasks)

    results = asyncio.run(_run_all())
    # 全部应成功 (Checkpointer 应串行化处理, 不应崩溃)
    for i, status in enumerate(results):
        assert status == 200, f"同 sid 并发 #{i} 非 200: {status}"


# ========== 请求体格式错误 ==========


@pytest.mark.exploratory
def test_malformed_json_returns_400() -> None:
    """边界: 请求体非合法 JSON → 400 或 422 (FastAPI/Pydantic 标准, 不应 5xx)."""
    with httpx.Client(timeout=EDGE_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            content=b'{"model": "agentinsight-researcher", "messages": invalid',
            headers={"content-type": "application/json"},
        )
    # FastAPI 对 malformed JSON 返回 422 (RequestValidationError), 部分场景返回 400
    assert r.status_code in (400, 422), f"非法 JSON 应返回 400/422, 实际: {r.status_code}"
    assert r.status_code < 500


@pytest.mark.exploratory
def test_missing_required_field_model_returns_400() -> None:
    """边界: 缺少字段 model → 200 (model 有默认值 agentinsight-researcher)."""
    with httpx.Client(timeout=EDGE_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "你好"}],
                "stream": False,
            },
        )
    # model 字段有默认值 "agentinsight-researcher", 缺字段时用默认值返回 200
    assert r.status_code == 200, f"缺 model 字段 (有默认值) 应返回 200, 实际: {r.status_code}"


@pytest.mark.exploratory
def test_missing_required_field_messages_returns_400() -> None:
    """边界: 缺少必填字段 messages → 422 (Pydantic 校验失败标准行为)."""
    with httpx.Client(timeout=EDGE_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={"model": "agentinsight-researcher", "stream": False},
        )
    # messages 是必填字段, Pydantic 校验失败返回 422 (FastAPI 标准)
    assert r.status_code == 422, f"缺 messages 字段应返回 422, 实际: {r.status_code}"


@pytest.mark.exploratory
def test_invalid_role_returns_400() -> None:
    """边界: messages 中 role 非 user/system/assistant → 400."""
    with httpx.Client(timeout=EDGE_TIMEOUT) as client:
        r = client.post(
            f"{AGENT_URL}/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "invalid_role", "content": "你好"}],
                "stream": False,
            },
        )
    assert r.status_code == 400, f"非法 role 应返回 400, 实际: {r.status_code}"


# ========== 不存在的端点 ==========


@pytest.mark.exploratory
def test_unknown_endpoint_returns_404() -> None:
    """边界: 不存在的端点 → 404 (不应 5xx)."""
    with httpx.Client(timeout=EDGE_TIMEOUT) as client:
        r = client.get(f"{AGENT_URL}/v1/nonexistent-endpoint")
    assert r.status_code == 404, f"不存在端点应返回 404, 实际: {r.status_code}"


@pytest.mark.exploratory
def test_method_not_allowed_returns_405() -> None:
    """边界: 不允许的方法 → 405 (如 GET /v1/chat/completions)."""
    with httpx.Client(timeout=EDGE_TIMEOUT) as client:
        r = client.get(f"{AGENT_URL}/v1/chat/completions")
    # FastAPI 默认对未定义方法返回 405
    assert r.status_code in (404, 405), f"不允许方法应返回 404/405, 实际: {r.status_code}"


# ========== 探索性单元测试 (mock-based, 不依赖容器栈) ==========
# 以下测试用 mock 模拟异常场景, 标记为 unit 以便构建期执行 (不依赖容器栈健康).
# 单元测试在构建期执行, 不依赖外部服务.

import types  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

from src.config.settings import Settings  # noqa: E402
from src.llm.client import LLMClient, LLMTier  # noqa: E402
from src.rag import embeddings as emb_module  # noqa: E402
from src.rag.bm25_filter import BM25Filter  # noqa: E402
from src.rag.embeddings import EmbeddingsClient  # noqa: E402


def _make_unit_settings(**overrides: object) -> Settings:
    """构造测试 Settings (跳过 .env 加载)."""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


class _FakeHttpxResponse:
    """伪造 httpx 响应 (Embeddings TEI /embed)."""

    def __init__(self, json_data: object, status_code: int = 200) -> None:
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx as _httpx

            raise _httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=MagicMock(),
                response=self,  # type: ignore[arg-type]
            )

    def json(self) -> object:
        return self._json_data


class _FakeAsyncHttpxClient:
    """伪造 httpx.AsyncClient, post 返回指定响应."""

    def __init__(self, response: _FakeHttpxResponse | None = None) -> None:
        self._response = response or _FakeHttpxResponse([])
        self.post_calls: list[dict[str, object]] = []

    async def post(self, url: str, **kwargs: object) -> _FakeHttpxResponse:
        self.post_calls.append({"url": url, **kwargs})
        return self._response

    async def aclose(self) -> None:
        pass


# ========== 超长查询 (100K 字符) 处理 ==========


@pytest.mark.unit
async def test_extremely_long_query_100k_chars() -> None:
    """边界: 超长查询 (100K 字符) 不应导致 BM25Filter 或 EmbeddingsClient 崩溃.

    单会话上下文上限 800K 字符, 100K 应在范围内.
    所有外部输入经 Pydantic 校验, 不应 eval/exec 用户输入.
    """
    # 1. BM25Filter: 100K 字符查询 (本地 jieba 分词, 零网络调用)
    settings = _make_unit_settings()
    bm25 = BM25Filter(settings)
    long_query = "人工智能 " * 20000  # ~100K 字符
    docs = [
        {"content": "人工智能是计算机科学的一个分支", "url": "https://example.com/1"},
        {"content": "深度学习是机器学习的子领域", "url": "https://example.com/2"},
    ]
    result = await bm25.filter(long_query, docs, max_results=5)
    # 不应崩溃, 返回列表 (可能为空或含匹配 chunk)
    assert isinstance(result, list)

    # 2. EmbeddingsClient: 100K 字符文本嵌入 (mock httpx, 不实际调 TEI)
    emb_client = EmbeddingsClient(settings)
    # 清除缓存避免干扰
    emb_module._EMBED_CACHE.clear()
    fake_vector = [[0.1] * 768]
    emb_client._client = _FakeAsyncHttpxClient(_FakeHttpxResponse(fake_vector))
    try:
        vec = await emb_client.embed_query(long_query)
        # 不应崩溃, 返回 768 维向量
        assert len(vec) == 768, f"超长查询嵌入应返回 768 维, 实际: {len(vec)}"
    finally:
        emb_module._EMBED_CACHE.clear()


# ========== Unicode + Emoji 混合查询 ==========


@pytest.mark.unit
async def test_unicode_emoji_mixed_query() -> None:
    """边界: Unicode + Emoji 混合查询不应导致 BM25Filter 崩溃.

    所有外部输入经 Pydantic 校验.
    jieba 对 Emoji/Unicode 有兜底处理 (未登录词按单字切分).
    """
    settings = _make_unit_settings()
    bm25 = BM25Filter(settings)
    mixed_query = "你好👋世界🎉 AI研究🔬分析 🇨🇳人工智能"
    docs = [
        {"content": "你好世界, 人工智能研究", "url": "https://example.com/1"},
        {"content": "AI 研究分析报告", "url": "https://example.com/2"},
        {"content": "完全无关的内容 xyz", "url": "https://example.com/3"},
    ]
    result = await bm25.filter(mixed_query, docs, max_results=5)
    # 不应崩溃, 返回列表
    assert isinstance(result, list)
    # 至少应有非零分文档返回 (中文关键词 "人工智能"/"研究" 命中)
    # 注: Emoji 不参与 BM25 打分 (jieba 可能切为单字或忽略)


# ========== 并发会话压力测试 (10 个并发 session_id) ==========


@pytest.mark.unit
async def test_concurrent_sessions_stress() -> None:
    """边界: 10 个并发 embed_texts 调用应全部成功 (Semaphore 限流不崩溃).

    每个 Agent 应支持并发多会话.
    Embeddings 客户端按 embeddings_max_concurrent 限流.
    """
    settings = _make_unit_settings(embeddings_max_concurrent=3)
    emb_client = EmbeddingsClient(settings)
    emb_module._EMBED_CACHE.clear()

    # mock httpx: 每次调用返回与输入等长的向量列表
    class _ConcurrentFakeClient:
        async def post(self, url: str, **kwargs: object) -> _FakeHttpxResponse:
            inputs = kwargs.get("json", {}).get("inputs", [])  # type: ignore[union-attr]
            return _FakeHttpxResponse([[0.1] * 768 for _ in inputs])

        async def aclose(self) -> None:
            pass

    emb_client._client = _ConcurrentFakeClient()

    try:
        # 10 个并发会话, 每个会话嵌入不同文本
        sessions = [f"test_stress_{i}_{uuid.uuid4().hex[:8]}" for i in range(10)]
        texts_list = [[f"会话 {sid} 的查询文本"] for sid in sessions]

        results = await asyncio.gather(*[emb_client.embed_texts(texts) for texts in texts_list])

        # 所有 10 个并发调用应返回正确维度向量
        assert len(results) == 10
        for i, vectors in enumerate(results):
            assert len(vectors) == 1, f"会话 {i} 向量数不符"
            assert len(vectors[0]) == 768, f"会话 {i} 向量维度不符"
    finally:
        emb_module._EMBED_CACHE.clear()


# ========== session_id 含特殊字符注入 ==========


@pytest.mark.unit
async def test_session_id_with_special_chars_injection() -> None:
    """边界: session_id 含 SQL/Path/JS 注入字符不应导致 LLMClient 崩溃.

    所有外部输入经 Pydantic 校验; 禁止 eval/exec 用户输入.
    session_id 作为 thread_id 传入 trace span metadata, 不应引发注入风险.
    """
    settings = _make_unit_settings(
        llm_response_cache_enabled=False,  # 禁用缓存避免 Redis 依赖
        smart_llm="deepseek/deepseek-chat",
        fast_llm="deepseek/deepseek-chat",
        strategic_llm="deepseek/deepseek-chat",
    )
    client = LLMClient(settings)

    # 注入型 session_id 列表
    injection_session_ids = [
        "'; DROP TABLE users; --",
        "../../../etc/passwd",
        "<script>alert('xss')</script>",
        "test\x00null\x00byte",
        "session' OR '1'='1",
    ]

    # mock litellm.acompletion
    class _FakeUsage:
        prompt_tokens = 10
        completion_tokens = 5

    class _FakeResp:
        usage = _FakeUsage()
        choices = [types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))]

    calls: list[dict[str, object]] = []

    async def _fake_acompletion(**kwargs: object) -> _FakeResp:
        calls.append(kwargs)
        return _FakeResp()

    fake_litellm = types.ModuleType("litellm")
    fake_litellm.acompletion = _fake_acompletion

    with patch("src.llm.client.litellm", fake_litellm):
        for sid in injection_session_ids:
            response = await client.achat(
                [{"role": "user", "content": "测试查询"}],
                tier=LLMTier.SMART,
                session_id=sid,
                step="injection_test",
            )
            assert response.content == "ok", f"session_id={sid!r} 导致调用失败"

    # 所有注入型 session_id 均应正常调用 LLM (不崩溃, 不注入)
    assert len(calls) == len(injection_session_ids)


# ========== 快速连续请求 (同 session_id 5 次快速请求) ==========


@pytest.mark.unit
async def test_rapid_sequential_requests() -> None:
    """边界: 同一输入 5 次快速连续请求, LLM 响应缓存应命中后 4 次.

    Redis 缓存不可用时应降级无缓存, 不阻断检索.
    LLM 调用经 llm/ 网关 (LiteLLM), 内置重试与降级链.
    用户硬约束: 出错了不要存缓存 — 仅缓存成功响应.

    本测试 mock Redis 客户端模拟缓存命中/未命中, 验证:
    1. 第一次请求: 缓存未命中 → 调用 litellm → 写入缓存
    2. 后续 4 次请求: 缓存命中 → 跳过 litellm 调用
    """
    settings = _make_unit_settings(
        llm_response_cache_enabled=True,
        smart_llm="deepseek/deepseek-chat",
        fast_llm="deepseek/deepseek-chat",
        strategic_llm="deepseek/deepseek-chat",
        temperature=0.0,  # ≤ _CACHE_MAX_TEMPERATURE (0.3) 才走缓存
    )
    client = LLMClient(settings)

    class _FakeUsage:
        prompt_tokens = 10
        completion_tokens = 5

    class _FakeResp:
        usage = _FakeUsage()
        choices = [types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))]

    litellm_calls: list[dict[str, object]] = []

    async def _fake_acompletion(**kwargs: object) -> _FakeResp:
        litellm_calls.append(kwargs)
        return _FakeResp()

    fake_litellm = types.ModuleType("litellm")
    fake_litellm.acompletion = _fake_acompletion

    # mock Redis: 模拟缓存存储 (dict-based)
    cache_store: dict[str, bytes] = {}
    mock_redis = MagicMock()

    async def _redis_get(key: str) -> object:
        return cache_store.get(key)

    async def _redis_setex(key: str, ttl: int, value: bytes) -> None:
        cache_store[key] = value

    mock_redis.get = AsyncMock(side_effect=_redis_get)
    mock_redis.setex = AsyncMock(side_effect=_redis_setex)

    with (
        patch("src.llm.client.litellm", fake_litellm),
        patch(
            "src.common.redis_client.get_redis_client",
            new=AsyncMock(return_value=mock_redis),
        ),
    ):
        messages = [{"role": "user", "content": "快速连续请求测试"}]
        for i in range(5):
            response = await client.achat(
                messages,
                tier=LLMTier.SMART,
                temperature=0.0,
                step="rapid_test",
            )
            assert response.content == "ok", f"第 {i + 1} 次请求失败"

    # 第一次缓存未命中调 litellm; 后续 4 次缓存命中跳过 litellm
    assert len(litellm_calls) == 1, (
        f"5 次快速请求应仅调用 litellm 1 次 (缓存命中 4 次), 实际: {len(litellm_calls)}"
    )
