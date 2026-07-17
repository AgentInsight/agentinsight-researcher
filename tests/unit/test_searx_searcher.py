"""单元测试: SearXNGSearcher SearXNG 元搜索.

验证 src/skills/researcher/searchers/searx.py:
- 无需 API Key, 配置 searx_url (默认 http://searxng:8099)
- 共享 HttpClientPool (get_http_client_pool) + CircuitBreaker (failure_threshold=3) + max_retries=2
- 请求构造: params (q/format=json/pageno=1/safesearch=0/language=zh-CN/categories) + headers (X-Forwarded-For)
- 可选 time_range 参数 (kwargs)
- 响应解析: {"results": [{"title","url","content"}]}
- 熔断器开启时跳过搜索返回空列表
- HTTP 错误/网络异常触发熔断器 record_failure + 重试 (max 2 次)
- JSON 解析失败降级返回空列表
- query_domains 后置过滤
- close() 为 no-op (客户端由 HttpClientPool 统一管理生命周期)

单元测试在构建期执行, 不依赖外部服务, 全部 mock httpx.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.skills.researcher.searchers import SearchRegion
from src.skills.researcher.searchers.searx import SearXNGSearcher

pytestmark = pytest.mark.unit


# ========== 辅助函数 ==========


def _make_settings(searx_url: str = "http://searxng:8099") -> Settings:
    """构造带 searx_url 的 Settings (隔离 .env)."""
    return Settings(_env_file=None, searx_url=searx_url)


def _make_response(
    status_code: int = 200,
    json_data: Any = None,
    text: str = "",
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """构造 mock httpx.Response."""
    import httpx

    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text or (str(json_data) if json_data else "")
    resp.headers = headers or {}
    resp.request = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=resp.request, response=resp
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


def _make_searcher(
    settings: Settings | None = None,
    circuit_open: bool = False,
) -> SearXNGSearcher:
    """构造 SearXNGSearcher 实例 (替换 _circuit_breaker 为 mock).

    HTTP 客户端由 HttpClientPool 统一管理, 测试中通过 mock_pool_client fixture 注入.
    """
    searcher = SearXNGSearcher(settings or _make_settings())
    # 替换熔断器为 mock
    searcher._circuit_breaker = MagicMock()
    searcher._circuit_breaker.is_open.return_value = circuit_open
    return searcher


@pytest.fixture
def mock_pool_client() -> Any:
    """Patch get_http_client_pool, 返回 mock client 供测试配置.

    SearXNGSearcher.search 通过 ``await get_http_client_pool()`` 获取池,
    再 ``await pool.get_client(name)`` 获取客户端. 本 fixture 注入 mock 池与客户端,
    测试只需配置 ``mock_pool_client.get`` 的返回值/副作用.
    """
    client = MagicMock()
    pool = MagicMock()
    pool.get_client = AsyncMock(return_value=client)
    with patch(
        "src.skills.researcher.searchers.searx.get_http_client_pool",
        new=AsyncMock(return_value=pool),
    ):
        yield client


# ========== 类元数据 ==========


def test_searxng_searcher_metadata() -> None:
    """SearXNGSearcher 元数据."""
    assert SearXNGSearcher.name == "searxng"
    assert SearXNGSearcher.region == SearchRegion.GLOBAL
    assert SearXNGSearcher.cost_tier == "free"
    assert SearXNGSearcher.quality_score == 90.0


def test_searxng_api_url_default() -> None:
    """API URL 默认拼接为 http://searxng:8099/search."""
    searcher = SearXNGSearcher(_make_settings())
    assert searcher._api_url == "http://searxng:8099/search"


def test_searxng_api_url_strips_trailing_slash() -> None:
    """searx_url 含尾部斜杠时应正确拼接 (无双斜杠)."""
    searcher = SearXNGSearcher(_make_settings(searx_url="http://searxng:8099/"))
    assert searcher._api_url == "http://searxng:8099/search"


def test_searxng_max_retries() -> None:
    """max_retries 应为 2."""
    searcher = SearXNGSearcher(_make_settings())
    assert searcher._max_retries == 2


# ========== 熔断器开启 ==========


@pytest.mark.asyncio
async def test_search_circuit_open_returns_empty() -> None:
    """熔断器开启时返回空列表, 不发起请求."""
    searcher = _make_searcher(circuit_open=True)

    with patch(
        "src.skills.researcher.searchers.searx.get_http_client_pool",
        new=AsyncMock(),
    ) as mock_get_pool:
        results = await searcher.search("test")

    assert results == []
    mock_get_pool.assert_not_called()


# ========== 请求构造 ==========


@pytest.mark.asyncio
async def test_search_params_constructed_correctly(mock_pool_client: MagicMock) -> None:
    """params 含 q/format=json/pageno=1/safesearch=0/language=zh-CN/categories."""
    response = _make_response(200, {"results": []})
    searcher = _make_searcher()
    mock_pool_client.get = AsyncMock(return_value=response)

    await searcher.search("深度学习", max_results=5)

    params = mock_pool_client.get.call_args.kwargs["params"]
    assert params["q"] == "深度学习"
    assert params["format"] == "json"
    assert params["pageno"] == 1
    assert params["safesearch"] == 0
    assert params["language"] == "zh-CN"
    assert "categories" in params


@pytest.mark.asyncio
async def test_search_headers_contain_x_forwarded_for(mock_pool_client: MagicMock) -> None:
    """headers 含 X-Forwarded-For: 127.0.0.1 (避免 botdetection 警告)."""
    response = _make_response(200, {"results": []})
    searcher = _make_searcher()
    mock_pool_client.get = AsyncMock(return_value=response)

    await searcher.search("test")

    headers = mock_pool_client.get.call_args.kwargs["headers"]
    assert headers["X-Forwarded-For"] == "127.0.0.1"


@pytest.mark.asyncio
async def test_search_time_range_added_when_provided(mock_pool_client: MagicMock) -> None:
    """time_range 通过 kwargs 传入时加入 params."""
    response = _make_response(200, {"results": []})
    searcher = _make_searcher()
    mock_pool_client.get = AsyncMock(return_value=response)

    await searcher.search("test", time_range="week")

    params = mock_pool_client.get.call_args.kwargs["params"]
    assert params["time_range"] == "week"


@pytest.mark.asyncio
async def test_search_time_range_omitted_when_not_provided(mock_pool_client: MagicMock) -> None:
    """未传 time_range 时 params 不含该键."""
    response = _make_response(200, {"results": []})
    searcher = _make_searcher()
    mock_pool_client.get = AsyncMock(return_value=response)

    await searcher.search("test")

    params = mock_pool_client.get.call_args.kwargs["params"]
    assert "time_range" not in params


# ========== 响应解析 ==========


@pytest.mark.asyncio
async def test_search_parses_results(mock_pool_client: MagicMock) -> None:
    """解析 {"results": [{"title","url","content"}]} 结构."""
    json_data = {
        "results": [
            {
                "title": "深度学习综述",
                "url": "https://example.com/article1",
                "content": "本文介绍深度学习基础",
            }
        ]
    }
    response = _make_response(200, json_data)
    searcher = _make_searcher()
    mock_pool_client.get = AsyncMock(return_value=response)

    results = await searcher.search("深度学习")

    assert len(results) == 1
    assert results[0]["title"] == "深度学习综述"
    assert results[0]["url"] == "https://example.com/article1"
    assert results[0]["snippet"] == "本文介绍深度学习基础"
    assert results[0]["source"] == "searxng"
    assert results[0]["region"] == "global"
    # 成功时应记录熔断器 success
    searcher._circuit_breaker.record_success.assert_called_once()


@pytest.mark.asyncio
async def test_search_normalizes_result_fields(mock_pool_client: MagicMock) -> None:
    """返回结果含 5 个固定字段."""
    json_data = {"results": [{"title": "T", "url": "https://x.com/1", "content": "S"}]}
    response = _make_response(200, json_data)
    searcher = _make_searcher()
    mock_pool_client.get = AsyncMock(return_value=response)

    results = await searcher.search("test")

    assert set(results[0].keys()) == {"title", "url", "snippet", "source", "region"}


@pytest.mark.asyncio
async def test_search_empty_results_returns_empty(mock_pool_client: MagicMock) -> None:
    """空 results 返回空列表."""
    response = _make_response(200, {"results": []})
    searcher = _make_searcher()
    mock_pool_client.get = AsyncMock(return_value=response)

    results = await searcher.search("无结果")

    assert results == []


@pytest.mark.asyncio
async def test_search_missing_results_key_returns_empty(mock_pool_client: MagicMock) -> None:
    """响应缺失 results 键时返回空列表."""
    response = _make_response(200, {})
    searcher = _make_searcher()
    mock_pool_client.get = AsyncMock(return_value=response)

    results = await searcher.search("test")

    assert results == []


@pytest.mark.asyncio
async def test_search_truncates_to_max_results(mock_pool_client: MagicMock) -> None:
    """结果数超过 max_results 时截断."""
    items = [{"title": f"T{i}", "url": f"https://x.com/{i}", "content": "S"} for i in range(10)]
    response = _make_response(200, {"results": items})
    searcher = _make_searcher()
    mock_pool_client.get = AsyncMock(return_value=response)

    results = await searcher.search("test", max_results=3)

    assert len(results) == 3


# ========== HTTP 错误 + 重试 + 熔断器 ==========


@pytest.mark.asyncio
async def test_search_http_error_retries_then_returns_empty(mock_pool_client: MagicMock) -> None:
    """HTTP 500 触发重试 (max 2 次), 最终返回空列表."""
    response = _make_response(500, text="Error")
    searcher = _make_searcher()
    mock_pool_client.get = AsyncMock(return_value=response)

    with patch("src.skills.researcher.searchers.searx.asyncio.sleep", new=AsyncMock()):
        results = await searcher.search("test")

    assert results == []
    # 应重试 max_retries + 1 = 3 次
    assert mock_pool_client.get.call_count == 3
    # 每次失败都应记录熔断器 failure
    assert searcher._circuit_breaker.record_failure.call_count == 3


@pytest.mark.asyncio
async def test_search_http_429_returns_empty(mock_pool_client: MagicMock) -> None:
    """HTTP 429 返回空列表 (SearXNG 不抛 QuotaExceededError, 触发重试+降级)."""
    response = _make_response(429, text="Rate limited")
    searcher = _make_searcher()
    mock_pool_client.get = AsyncMock(return_value=response)

    with patch("src.skills.researcher.searchers.searx.asyncio.sleep", new=AsyncMock()):
        results = await searcher.search("test")

    assert results == []


@pytest.mark.asyncio
async def test_search_succeeds_after_retry(mock_pool_client: MagicMock) -> None:
    """第一次失败, 第二次成功, 应返回结果."""
    fail_response = _make_response(500, text="Error")
    success_response = _make_response(
        200, {"results": [{"title": "T", "url": "https://x.com/1", "content": "S"}]}
    )
    searcher = _make_searcher()
    mock_pool_client.get = AsyncMock(side_effect=[fail_response, success_response])

    with patch("src.skills.researcher.searchers.searx.asyncio.sleep", new=AsyncMock()):
        results = await searcher.search("test")

    assert len(results) == 1
    assert results[0]["title"] == "T"
    # 失败 1 次 + 成功 1 次
    assert searcher._circuit_breaker.record_failure.call_count == 1
    searcher._circuit_breaker.record_success.assert_called_once()


# ========== 网络异常 + 重试 ==========


@pytest.mark.asyncio
async def test_search_network_exception_retries_then_returns_empty(
    mock_pool_client: MagicMock,
) -> None:
    """网络异常触发重试, 最终返回空列表."""
    import httpx

    searcher = _make_searcher()
    mock_pool_client.get = AsyncMock(side_effect=httpx.ConnectError("network down"))

    with patch("src.skills.researcher.searchers.searx.asyncio.sleep", new=AsyncMock()):
        results = await searcher.search("test")

    assert results == []
    assert mock_pool_client.get.call_count == 3
    assert searcher._circuit_breaker.record_failure.call_count == 3


# ========== JSON 解析失败 ==========


@pytest.mark.asyncio
async def test_search_json_parse_failure_returns_empty(mock_pool_client: MagicMock) -> None:
    """JSON 解析失败触发重试, 最终返回空列表."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("invalid json")
    resp.text = "not json"
    resp.headers = {}
    resp.request = MagicMock()
    resp.raise_for_status = MagicMock()
    searcher = _make_searcher()
    mock_pool_client.get = AsyncMock(return_value=resp)

    with patch("src.skills.researcher.searchers.searx.asyncio.sleep", new=AsyncMock()):
        results = await searcher.search("test")

    assert results == []


# ========== query_domains 后置过滤 ==========


@pytest.mark.asyncio
async def test_search_query_domains_filter(mock_pool_client: MagicMock) -> None:
    """query_domains 后置过滤."""
    json_data = {
        "results": [
            {"title": "a", "url": "https://arxiv.org/1", "content": "S"},
            {"title": "b", "url": "https://other.com/2", "content": "S"},
        ]
    }
    response = _make_response(200, json_data)
    searcher = _make_searcher()
    mock_pool_client.get = AsyncMock(return_value=response)

    results = await searcher.search("test", query_domains=["arxiv.org"])

    assert len(results) == 1
    assert "arxiv.org" in results[0]["url"]


# ========== close() ==========


@pytest.mark.asyncio
async def test_close_is_noop() -> None:
    """close() 应为 no-op (客户端由 HttpClientPool 统一管理生命周期)."""
    searcher = _make_searcher()

    with patch(
        "src.skills.researcher.searchers.searx.get_http_client_pool",
        new=AsyncMock(),
    ) as mock_get_pool:
        # close() 不应抛异常, 也不应访问共享客户端池
        await searcher.close()
        mock_get_pool.assert_not_called()
