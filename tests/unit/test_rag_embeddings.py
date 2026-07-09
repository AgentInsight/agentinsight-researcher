"""单元测试: Embeddings 客户端.

验证 src/rag/embeddings.py:
- EmbeddingsClient.__init__: API_KEY 鉴权头注入 (embeddings_api_key 非空时携带 Bearer)
- embed_texts 空 texts 返回 []
- embed_texts 主流程: mock httpx 返回向量, 验证 token_count 估算 (字符数//3), span.update 调用
- embed_texts 异常路径: HTTP 错误 raise_for_status, span.update(metadata={"error":...}) 后 raise
- embed_query: 委托 embed_texts, 空向量返回 []
- warmup: 失败不阻断启动
- get_embeddings_client 单例

AGENTS.md 第 13 章: 单元测试不依赖外部服务.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from src.config.settings import Settings
from src.rag import embeddings as emb_module
from src.rag.embeddings import EmbeddingsClient, get_embeddings_client


class _FakeResponse:
    """伪造 httpx 响应."""

    def __init__(self, json_data: Any, status_code: int = 200) -> None:
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._json_data


class _FakeAsyncClient:
    """伪造 httpx.AsyncClient, 捕获 post 调用."""

    def __init__(
        self,
        response: _FakeResponse | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._response = response
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if self._exc is not None:
            raise self._exc
        if self._response is None:
            return _FakeResponse([])
        return self._response

    async def aclose(self) -> None:
        pass


class _CapturingSpan:
    """捕获 span.update 调用, 追加到共享 store."""

    def __init__(self, store: list[dict[str, Any]]) -> None:
        self._store = store

    def update(self, **kwargs: Any) -> _CapturingSpan:
        self._store.append(kwargs)
        return self


def _install_capturing_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """注入伪造 trace_embedding, 返回 span.update 调用记录列表."""
    captured: list[dict[str, Any]] = []

    @asynccontextmanager
    async def _fake_trace(name: str, **_kwargs: Any) -> AsyncGenerator[Any, None]:
        yield _CapturingSpan(captured)

    monkeypatch.setattr(emb_module, "trace_embedding", _fake_trace)
    return captured


# ========== __init__ API_KEY 鉴权头 ==========


def test_init_no_api_key_no_auth_header() -> None:
    """embeddings_api_key 为空时不注入 Authorization 头."""
    settings = Settings(embeddings_api_key=None, _env_file=None)
    client = EmbeddingsClient(settings)
    assert "authorization" not in client._client.headers


def test_init_with_api_key_injects_bearer() -> None:
    """embeddings_api_key 非空时注入 Authorization: Bearer <key>."""
    settings = Settings(embeddings_api_key="secret-key-123", _env_file=None)
    client = EmbeddingsClient(settings)
    assert client._client.headers["authorization"] == "Bearer secret-key-123"


# ========== embed_texts ==========


@pytest.mark.asyncio
async def test_embed_texts_empty_returns_empty() -> None:
    """空 texts 列表直接返回 [], 不调用 HTTP."""
    settings = Settings(_env_file=None)
    client = EmbeddingsClient(settings)
    result = await client.embed_texts([])
    assert result == []


@pytest.mark.asyncio
async def test_embed_texts_main_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """主流程: mock httpx 返回向量, 验证返回值 + token_count 估算 + span.update 调用."""
    settings = Settings(embeddings_model="BAAI/bge-base-zh-v1.5", _env_file=None)
    client = EmbeddingsClient(settings)

    captured = _install_capturing_trace(monkeypatch)

    fake_vectors = [[0.1] * 768, [0.2] * 768]
    fake_response = _FakeResponse(fake_vectors)
    fake_http_client = _FakeAsyncClient(response=fake_response)
    client._client = fake_http_client  # type: ignore[assignment]

    # "文本A" (3 字符) + "文本BB" (4 字符) = 7 字符, token_count = 7 // 3 = 2
    texts = ["文本A", "文本BB"]
    result = await client.embed_texts(texts)

    assert result == fake_vectors
    # 验证 HTTP 调用
    assert len(fake_http_client.calls) == 1
    assert fake_http_client.calls[0]["url"] == "/embed"
    assert fake_http_client.calls[0]["json"] == {"inputs": texts}
    # 验证 span.update: output + usage_details
    output_updates = [u for u in captured if "output" in u]
    assert len(output_updates) == 1
    assert output_updates[0]["output"] == {"vector_count": 2}
    usage_updates = [u for u in captured if "usage_details" in u]
    assert len(usage_updates) == 1
    # usage_details 字段名对齐 AgentInsightService: token_count → total_tokens
    assert usage_updates[0]["usage_details"]["total_tokens"] == 2  # 7 // 3 = 2


@pytest.mark.asyncio
async def test_embed_texts_exception_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """异常路径: HTTP 错误 raise_for_status, span.update(metadata={"error":...}) 后 raise."""
    settings = Settings(_env_file=None)
    client = EmbeddingsClient(settings)

    captured = _install_capturing_trace(monkeypatch)

    fake_response = _FakeResponse([], status_code=500)
    client._client = _FakeAsyncClient(response=fake_response)  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="HTTP 500"):
        await client.embed_texts(["text"])

    # 验证 span.update 被调用, 包含 metadata.error
    error_updates = [u for u in captured if "metadata" in u and "error" in u.get("metadata", {})]
    assert len(error_updates) == 1
    assert "RuntimeError" in error_updates[0]["metadata"]["error"]
    assert "HTTP 500" in error_updates[0]["metadata"]["error"]


@pytest.mark.asyncio
async def test_embed_texts_http_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 请求本身抛异常 (如连接错误), span.update 后 raise."""
    settings = Settings(_env_file=None)
    client = EmbeddingsClient(settings)

    captured = _install_capturing_trace(monkeypatch)

    client._client = _FakeAsyncClient(exc=ConnectionError("network down"))  # type: ignore[assignment]

    with pytest.raises(ConnectionError, match="network down"):
        await client.embed_texts(["text"])

    error_updates = [u for u in captured if "metadata" in u and "error" in u.get("metadata", {})]
    assert len(error_updates) == 1
    assert "ConnectionError" in error_updates[0]["metadata"]["error"]


# ========== embed_query ==========


@pytest.mark.asyncio
async def test_embed_query_delegates_to_embed_texts() -> None:
    """embed_query 委托 embed_texts, 返回第一个向量."""
    settings = Settings(_env_file=None)
    client = EmbeddingsClient(settings)

    fake_vectors = [[0.5] * 768]
    fake_response = _FakeResponse(fake_vectors)
    client._client = _FakeAsyncClient(response=fake_response)  # type: ignore[assignment]

    result = await client.embed_query("test query")
    assert result == [0.5] * 768


@pytest.mark.asyncio
async def test_embed_query_empty_vectors_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """embed_texts 返回空列表时, embed_query 返回 []."""
    settings = Settings(_env_file=None)
    client = EmbeddingsClient(settings)

    async def _fake_embed_texts(texts: list[str], **_kwargs: Any) -> list[list[float]]:
        return []

    monkeypatch.setattr(client, "embed_texts", _fake_embed_texts)

    result = await client.embed_query("test")
    assert result == []


# ========== warmup ==========


@pytest.mark.asyncio
async def test_warmup_failure_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """warmup 失败不阻断启动 (mock httpx 抛异常, 验证不 raise)."""
    settings = Settings(_env_file=None)
    client = EmbeddingsClient(settings)

    # 让 HTTP 调用抛异常
    client._client = _FakeAsyncClient(exc=RuntimeError("warmup service down"))  # type: ignore[assignment]

    # 不应抛异常 (warmup 内部 try/except)
    await client.warmup()


@pytest.mark.asyncio
async def test_warmup_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """warmup 成功路径: 调用 embed_texts 不抛异常."""
    settings = Settings(_env_file=None)
    client = EmbeddingsClient(settings)

    fake_vectors = [[0.1] * 768 for _ in client._WARMUP_TEXTS]
    fake_response = _FakeResponse(fake_vectors)
    client._client = _FakeAsyncClient(response=fake_response)  # type: ignore[assignment]

    await client.warmup()  # 不应抛异常


# ========== get_embeddings_client 单例 ==========


def test_get_embeddings_client_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_embeddings_client 两次调用返回同一实例."""
    test_settings = Settings(_env_file=None)
    monkeypatch.setattr(emb_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(emb_module, "_client", None)

    c1 = get_embeddings_client()
    c2 = get_embeddings_client()
    assert c1 is c2
