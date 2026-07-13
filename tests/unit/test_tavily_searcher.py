"""单元测试: TavilySearcher Tavily AI 搜索.

验证 src/skills/researcher/searchers/tavily.py:
- 需 TAVILY_API_KEY, 未配置返回空列表
- 请求构造: payload (api_key/query/max_results/search_depth/include_domains)
- 响应解析: {"results": [{"title","url","content"}]} 结构
- HTTP 429 抛 QuotaExceededError (engine=tavily)
- include_domains 原生域名过滤
- 其他 HTTP 错误/网络异常降级返回空列表

单元测试在构建期执行, 不依赖外部服务, 全部 mock httpx.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings
from src.skills.researcher.searchers import SearchRegion
from src.skills.researcher.searchers.exceptions import QuotaExceededError
from src.skills.researcher.searchers.tavily import TavilySearcher

pytestmark = pytest.mark.unit


# ========== 辅助函数 ==========


def _make_settings(api_key: str | None = "test-tavily-key") -> Settings:
    """构造带 tavily_api_key 的 Settings (隔离 .env)."""
    return Settings(_env_file=None, tavily_api_key=api_key)


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
    api_key: str | None = "test-tavily-key",
    response: MagicMock | None = None,
) -> TavilySearcher:
    """构造 TavilySearcher 并注入 mock httpx 客户端."""
    settings = _make_settings(api_key)
    searcher = TavilySearcher(settings)
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=response or _make_response(200, {}))
    searcher._client = mock_client
    return searcher


# ========== 类元数据 ==========


def test_tavily_searcher_metadata() -> None:
    """TavilySearcher 元数据."""
    assert TavilySearcher.name == "tavily"
    assert TavilySearcher.region == SearchRegion.GLOBAL
    assert TavilySearcher.cost_tier == "paid"
    assert TavilySearcher.quality_score == 93.3


def test_tavily_api_url() -> None:
    """API URL 应为 Tavily 搜索端点."""
    assert TavilySearcher._api_url == "https://api.tavily.com/search"


# ========== API Key 未配置 ==========


@pytest.mark.asyncio
async def test_search_no_api_key_returns_empty() -> None:
    """api_key 未配置时返回空列表, 不调用 HTTP."""
    searcher = _make_searcher(api_key=None)
    assert await searcher.search("test") == []
    searcher._client.post.assert_not_called()


# ========== 请求构造 ==========


@pytest.mark.asyncio
async def test_search_payload_contains_api_key() -> None:
    """payload 含 api_key 字段."""
    searcher = _make_searcher(response=_make_response(200, {"results": []}))
    await searcher.search("test")
    payload = searcher._client.post.call_args.kwargs["json"]
    assert payload["api_key"] == "test-tavily-key"


@pytest.mark.asyncio
async def test_search_payload_contains_query_and_max_results() -> None:
    """payload 含 query 和 max_results 字段."""
    searcher = _make_searcher(response=_make_response(200, {"results": []}))
    await searcher.search("AI", max_results=8)
    payload = searcher._client.post.call_args.kwargs["json"]
    assert payload["query"] == "AI"
    assert payload["max_results"] == 8


@pytest.mark.asyncio
async def test_search_payload_search_depth_basic() -> None:
    """payload search_depth 默认为 basic."""
    searcher = _make_searcher(response=_make_response(200, {"results": []}))
    await searcher.search("test")
    payload = searcher._client.post.call_args.kwargs["json"]
    assert payload["search_depth"] == "basic"


@pytest.mark.asyncio
async def test_search_payload_include_domains_when_query_domains() -> None:
    """query_domains 非空时, payload 含 include_domains 字段 (原生域名过滤)."""
    searcher = _make_searcher(response=_make_response(200, {"results": []}))
    await searcher.search("test", query_domains=["arxiv.org", "nature.com"])
    payload = searcher._client.post.call_args.kwargs["json"]
    assert payload["include_domains"] == ["arxiv.org", "nature.com"]


@pytest.mark.asyncio
async def test_search_payload_no_include_domains_when_query_domains_none() -> None:
    """query_domains 为 None 时, payload 不含 include_domains 字段."""
    searcher = _make_searcher(response=_make_response(200, {"results": []}))
    await searcher.search("test", query_domains=None)
    payload = searcher._client.post.call_args.kwargs["json"]
    assert "include_domains" not in payload


# ========== 响应解析 ==========


@pytest.mark.asyncio
async def test_search_parses_results() -> None:
    """解析 {"results": [{"title","url","content"}]} 结构."""
    json_data = {
        "results": [
            {"title": "AI 研究", "url": "https://x.com/1", "content": "内容1"},
            {"title": "ML 论文", "url": "https://x.com/2", "content": "内容2"},
        ]
    }
    searcher = _make_searcher(response=_make_response(200, json_data))
    results = await searcher.search("AI")

    assert len(results) == 2
    assert results[0]["title"] == "AI 研究"
    assert results[0]["url"] == "https://x.com/1"
    assert results[0]["snippet"] == "内容1"  # content 映射到 snippet
    assert results[0]["source"] == "tavily"
    assert results[0]["region"] == "global"


@pytest.mark.asyncio
async def test_search_normalizes_result_fields() -> None:
    """返回结果含 5 个固定字段."""
    json_data = {"results": [{"title": "t", "url": "https://x.com", "content": "s"}]}
    searcher = _make_searcher(response=_make_response(200, json_data))
    results = await searcher.search("test")
    assert set(results[0].keys()) == {"title", "url", "snippet", "source", "region"}


@pytest.mark.asyncio
async def test_search_empty_results() -> None:
    """空 results 返回空列表."""
    searcher = _make_searcher(response=_make_response(200, {"results": []}))
    assert await searcher.search("无结果") == []


@pytest.mark.asyncio
async def test_search_truncates_to_max_results() -> None:
    """结果数超过 max_results 时截断."""
    items = [{"title": f"t{i}", "url": f"https://x.com/{i}", "content": "s"} for i in range(10)]
    searcher = _make_searcher(response=_make_response(200, {"results": items}))
    assert len(await searcher.search("test", max_results=3)) == 3


# ========== HTTP 429 抛 QuotaExceededError ==========


@pytest.mark.asyncio
async def test_search_http_429_raises_quota_exceeded() -> None:
    """HTTP 429 抛 QuotaExceededError (engine=tavily)."""
    searcher = _make_searcher(response=_make_response(429, text="limited", headers={}))
    with pytest.raises(QuotaExceededError) as exc_info:
        await searcher.search("test")
    assert exc_info.value.engine == "tavily"


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
