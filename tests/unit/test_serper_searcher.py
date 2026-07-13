"""单元测试: SerperSearcher Serper.dev Google Search API 搜索.

验证 src/skills/researcher/searchers/serper_searcher.py:
- 需 SERPER_API_KEY, 未配置返回空列表
- 请求构造: headers (X-API-KEY / Content-Type) + payload (q/num)
- 响应解析: {"organic": [{"title","link","snippet"}]} 结构
- HTTP 429 抛 QuotaExceededError (engine=serper)
- 其他 HTTP 错误/网络异常降级返回空列表
- query_domains 后置过滤

单元测试在构建期执行, 不依赖外部服务, 全部 mock httpx.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings
from src.skills.researcher.searchers import SearchRegion
from src.skills.researcher.searchers.exceptions import QuotaExceededError
from src.skills.researcher.searchers.serper_searcher import SerperSearcher

pytestmark = pytest.mark.unit


# ========== 辅助函数 ==========


def _make_settings(api_key: str | None = "test-serper-key") -> Settings:
    """构造带 serper_api_key 的 Settings (隔离 .env)."""
    return Settings(_env_file=None, serper_api_key=api_key)


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
    if status_code >= 400 and status_code != 429:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=resp.request, response=resp
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


def _make_searcher(
    api_key: str | None = "test-serper-key",
    response: MagicMock | None = None,
) -> SerperSearcher:
    """构造 SerperSearcher 并注入 mock httpx 客户端."""
    settings = _make_settings(api_key)
    searcher = SerperSearcher(settings)
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=response or _make_response(200, {}))
    searcher._client = mock_client
    return searcher


# ========== 类元数据 ==========


def test_serper_searcher_metadata() -> None:
    """SerperSearcher 元数据."""
    assert SerperSearcher.name == "serper"
    assert SerperSearcher.region == SearchRegion.GLOBAL
    assert SerperSearcher.cost_tier == "paid"
    assert SerperSearcher.quality_score == 82.2


def test_serper_api_url() -> None:
    """API URL 应为 Serper.dev 端点."""
    assert SerperSearcher._api_url == "https://google.serper.dev/search"


# ========== API Key 未配置 ==========


@pytest.mark.asyncio
async def test_search_no_api_key_returns_empty() -> None:
    """api_key 未配置时返回空列表, 不调用 HTTP."""
    searcher = _make_searcher(api_key=None)
    assert await searcher.search("test") == []
    searcher._client.post.assert_not_called()


# ========== 请求构造 ==========


@pytest.mark.asyncio
async def test_search_headers_x_api_key() -> None:
    """headers 含 X-API-KEY."""
    searcher = _make_searcher(response=_make_response(200, {"organic": []}))
    await searcher.search("test")
    headers = searcher._client.post.call_args.kwargs["headers"]
    assert headers["X-API-KEY"] == "test-serper-key"


@pytest.mark.asyncio
async def test_search_headers_content_type_json() -> None:
    """headers 含 Content-Type: application/json."""
    searcher = _make_searcher(response=_make_response(200, {"organic": []}))
    await searcher.search("test")
    headers = searcher._client.post.call_args.kwargs["headers"]
    assert headers["Content-Type"] == "application/json"


@pytest.mark.asyncio
async def test_search_payload_q_and_num() -> None:
    """payload 含 q=query, num=max_results."""
    searcher = _make_searcher(response=_make_response(200, {"organic": []}))
    await searcher.search("AI", max_results=8)
    payload = searcher._client.post.call_args.kwargs["json"]
    assert payload["q"] == "AI"
    assert payload["num"] == 8


# ========== 响应解析 ==========


@pytest.mark.asyncio
async def test_search_parses_organic_results() -> None:
    """解析 {"organic": [{"title","link","snippet"}]} 结构."""
    json_data = {
        "organic": [
            {"title": "标题1", "link": "https://x.com/1", "snippet": "摘要1"},
            {"title": "标题2", "link": "https://x.com/2", "snippet": "摘要2"},
        ]
    }
    searcher = _make_searcher(response=_make_response(200, json_data))
    results = await searcher.search("AI")

    assert len(results) == 2
    assert results[0]["title"] == "标题1"
    assert results[0]["url"] == "https://x.com/1"  # link 映射到 url
    assert results[0]["snippet"] == "摘要1"
    assert results[0]["source"] == "serper"
    assert results[0]["region"] == "global"


@pytest.mark.asyncio
async def test_search_normalizes_result_fields() -> None:
    """返回结果含 5 个固定字段."""
    json_data = {"organic": [{"title": "t", "link": "https://x.com", "snippet": "s"}]}
    searcher = _make_searcher(response=_make_response(200, json_data))
    results = await searcher.search("test")
    assert set(results[0].keys()) == {"title", "url", "snippet", "source", "region"}


@pytest.mark.asyncio
async def test_search_empty_results() -> None:
    """空 organic 返回空列表."""
    searcher = _make_searcher(response=_make_response(200, {"organic": []}))
    assert await searcher.search("无结果") == []


@pytest.mark.asyncio
async def test_search_truncates_to_max_results() -> None:
    """结果数超过 max_results 时截断."""
    items = [{"title": f"t{i}", "link": f"https://x.com/{i}", "snippet": "s"} for i in range(10)]
    searcher = _make_searcher(response=_make_response(200, {"organic": items}))
    assert len(await searcher.search("test", max_results=3)) == 3


# ========== HTTP 429 抛 QuotaExceededError ==========


@pytest.mark.asyncio
async def test_search_http_429_raises_quota_exceeded() -> None:
    """HTTP 429 抛 QuotaExceededError (engine=serper)."""
    searcher = _make_searcher(response=_make_response(429, text="limited", headers={}))
    with pytest.raises(QuotaExceededError) as exc_info:
        await searcher.search("test")
    assert exc_info.value.engine == "serper"


@pytest.mark.asyncio
async def test_search_429_retry_after_header() -> None:
    """429 时额度重置时间优先读取 Retry-After 头."""
    searcher = _make_searcher(
        response=_make_response(429, text="limited", headers={"Retry-After": "3600"})
    )
    from datetime import UTC, datetime

    with pytest.raises(QuotaExceededError) as exc_info:
        await searcher.search("test")
    now = datetime.now(UTC)
    delta = (exc_info.value.reset_at - now).total_seconds()
    assert 3500 < delta < 3700


# ========== 其他 HTTP 错误降级 ==========


@pytest.mark.asyncio
async def test_search_http_500_returns_empty() -> None:
    """HTTP 500 返回空列表."""
    searcher = _make_searcher(response=_make_response(500, text="Error"))
    assert await searcher.search("test") == []


# ========== 网络异常降级 ==========


@pytest.mark.asyncio
async def test_search_network_exception_returns_empty() -> None:
    """网络异常返回空列表."""
    searcher = _make_searcher()
    searcher._client.post = AsyncMock(side_effect=ConnectionError("down"))
    assert await searcher.search("test") == []


# ========== query_domains 后置过滤 ==========


@pytest.mark.asyncio
async def test_search_query_domains_filter() -> None:
    """query_domains 后置过滤."""
    json_data = {
        "organic": [
            {"title": "a", "link": "https://arxiv.org/1", "snippet": "s"},
            {"title": "b", "link": "https://other.com/2", "snippet": "s"},
        ]
    }
    searcher = _make_searcher(response=_make_response(200, json_data))
    results = await searcher.search("test", query_domains=["arxiv.org"])
    assert len(results) == 1
    assert "arxiv.org" in results[0]["url"]
