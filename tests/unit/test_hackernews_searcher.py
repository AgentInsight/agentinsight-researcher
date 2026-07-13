"""单元测试: HackerNewsSearcher Hacker News 技术社区搜索.

验证 src/skills/researcher/searchers/hackernews.py:
- 完全免费, 无需 API Key (10,000 req/h/IP)
- 请求构造: params (query/tags=story/hitsPerPage)
- 响应解析: {"hits": [{"title/story_title","url","objectID","story_text/comment_text"}]}
- objectID fallback URL: https://news.ycombinator.com/item?id={objectID}
- snippet: story_text 或 comment_text
- HTTP 错误/JSON 解析失败/网络异常降级返回空列表
- 结果数截断至 max_results
- query_domains 后置过滤

单元测试在构建期执行, 不依赖外部服务, 全部 mock httpx.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.skills.researcher.searchers import SearchRegion
from src.skills.researcher.searchers.hackernews import HackerNewsSearcher

pytestmark = pytest.mark.unit


# ========== 辅助函数 ==========


def _make_settings() -> Settings:
    """构造 Settings (隔离 .env)."""
    return Settings(_env_file=None)


def _make_response(
    status_code: int = 200,
    json_data: Any = None,
    text: str = "",
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """构造 mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text or (str(json_data) if json_data else "")
    resp.headers = headers or {}
    resp.request = MagicMock()
    return resp


def _make_mock_client(response: MagicMock) -> MagicMock:
    """构造支持 async context manager 的 mock httpx client."""
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.aclose = AsyncMock(return_value=None)
    return client


# ========== 类元数据 ==========


def test_hackernews_searcher_metadata() -> None:
    """HackerNewsSearcher 元数据."""
    assert HackerNewsSearcher.name == "hackernews"
    assert HackerNewsSearcher.region == SearchRegion.GLOBAL
    assert HackerNewsSearcher.cost_tier == "free"
    assert HackerNewsSearcher.quality_score == 60.0


def test_hackernews_base_url() -> None:
    """base_url 应为 HN Algolia API 端点."""
    searcher = HackerNewsSearcher(_make_settings())
    assert searcher.base_url == "https://hn.algolia.com/api/v1/search"


# ========== 请求构造 ==========


@pytest.mark.asyncio
async def test_search_params_constructed_correctly() -> None:
    """params 含 query/tags=story/hitsPerPage."""
    response = _make_response(200, {"hits": []})
    client = _make_mock_client(response)
    searcher = HackerNewsSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.hackernews.httpx.AsyncClient", return_value=client):
        await searcher.search("rust async", max_results=7)

    params = client.get.call_args.kwargs["params"]
    assert params["query"] == "rust async"
    assert params["tags"] == "story"
    assert params["hitsPerPage"] == "7"


# ========== 响应解析 ==========


@pytest.mark.asyncio
async def test_search_parses_hits() -> None:
    """解析 {"hits": [{"title","url","story_text"}]} 结构."""
    json_data = {
        "hits": [
            {
                "title": "Show HN: Rust async runtime",
                "url": "https://github.com/example/rust-async",
                "story_text": "I built a new async runtime.",
            }
        ]
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = HackerNewsSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.hackernews.httpx.AsyncClient", return_value=client):
        results = await searcher.search("rust")

    assert len(results) == 1
    assert results[0]["title"] == "Show HN: Rust async runtime"
    assert results[0]["url"] == "https://github.com/example/rust-async"
    assert results[0]["snippet"] == "I built a new async runtime."
    assert results[0]["source"] == "hackernews"
    assert results[0]["region"] == "global"


@pytest.mark.asyncio
async def test_search_fallback_story_title() -> None:
    """title 为空时降级使用 story_title."""
    json_data = {
        "hits": [
            {
                "story_title": "Fallback Title",
                "url": "https://x.com/1",
            }
        ]
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = HackerNewsSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.hackernews.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results[0]["title"] == "Fallback Title"


@pytest.mark.asyncio
async def test_search_fallback_comment_text_snippet() -> None:
    """story_text 为空时降级使用 comment_text 作为 snippet."""
    json_data = {
        "hits": [
            {
                "title": "T",
                "url": "https://x.com/1",
                "comment_text": "Comment body",
            }
        ]
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = HackerNewsSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.hackernews.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results[0]["snippet"] == "Comment body"


@pytest.mark.asyncio
async def test_search_objectid_fallback_url() -> None:
    """url 为空但有 objectID 时, 使用 HN 讨论页作为 URL."""
    json_data = {
        "hits": [
            {
                "title": "T",
                "objectID": "12345",
            }
        ]
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = HackerNewsSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.hackernews.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert len(results) == 1
    assert results[0]["url"] == "https://news.ycombinator.com/item?id=12345"


@pytest.mark.asyncio
async def test_search_skips_hits_without_url_and_objectid() -> None:
    """缺失 url 和 objectID 的条目不保留."""
    json_data = {
        "hits": [
            {"title": "no url no objectID"},
            {"title": "with url", "url": "https://x.com/1"},
        ]
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = HackerNewsSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.hackernews.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert len(results) == 1
    assert results[0]["url"] == "https://x.com/1"


@pytest.mark.asyncio
async def test_search_normalizes_result_fields() -> None:
    """返回结果含 5 个固定字段."""
    json_data = {"hits": [{"title": "T", "url": "https://x.com/1"}]}
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = HackerNewsSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.hackernews.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert set(results[0].keys()) == {"title", "url", "snippet", "source", "region"}


@pytest.mark.asyncio
async def test_search_empty_hits_returns_empty() -> None:
    """空 hits 返回空列表."""
    response = _make_response(200, {"hits": []})
    client = _make_mock_client(response)
    searcher = HackerNewsSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.hackernews.httpx.AsyncClient", return_value=client):
        results = await searcher.search("无结果")

    assert results == []


@pytest.mark.asyncio
async def test_search_missing_hits_key_returns_empty() -> None:
    """响应缺失 hits 键时返回空列表."""
    response = _make_response(200, {})
    client = _make_mock_client(response)
    searcher = HackerNewsSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.hackernews.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


@pytest.mark.asyncio
async def test_search_truncates_to_max_results() -> None:
    """结果数超过 max_results 时截断."""
    items = [{"title": f"T{i}", "url": f"https://x.com/{i}"} for i in range(10)]
    response = _make_response(200, {"hits": items})
    client = _make_mock_client(response)
    searcher = HackerNewsSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.hackernews.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test", max_results=3)

    assert len(results) == 3


# ========== HTTP 错误降级 ==========


@pytest.mark.asyncio
async def test_search_http_500_returns_empty() -> None:
    """HTTP 500 返回空列表."""
    response = _make_response(500, text="Error")
    client = _make_mock_client(response)
    searcher = HackerNewsSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.hackernews.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


@pytest.mark.asyncio
async def test_search_http_429_returns_empty() -> None:
    """HTTP 429 返回空列表 (HN 不抛 QuotaExceededError, 仅降级)."""
    response = _make_response(429, text="Rate limited")
    client = _make_mock_client(response)
    searcher = HackerNewsSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.hackernews.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


@pytest.mark.asyncio
async def test_search_json_parse_failure_returns_empty() -> None:
    """JSON 解析失败返回空列表."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("invalid json")
    resp.text = "not json"
    client = _make_mock_client(resp)
    searcher = HackerNewsSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.hackernews.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


# ========== 网络异常降级 ==========


@pytest.mark.asyncio
async def test_search_network_exception_returns_empty() -> None:
    """网络异常返回空列表."""
    import httpx

    client = MagicMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("network down"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    searcher = HackerNewsSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.hackernews.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


# ========== query_domains 后置过滤 ==========


@pytest.mark.asyncio
async def test_search_query_domains_filter() -> None:
    """query_domains 后置过滤."""
    json_data = {
        "hits": [
            {"title": "a", "url": "https://github.com/x"},
            {"title": "b", "url": "https://other.com/y"},
        ]
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = HackerNewsSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.hackernews.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test", query_domains=["github.com"])

    assert len(results) == 1
    assert "github.com" in results[0]["url"]
