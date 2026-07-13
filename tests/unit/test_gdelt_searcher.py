"""单元测试: GDELTSearcher GDELT 新闻搜索.

验证 src/skills/researcher/searchers/gdelt.py:
- 完全免费, 无需 API Key
- 请求构造: params (query/mode=ArtList/maxrecords/format=json/sort=DateDesc)
- 响应解析: {"articles": [{"title","url","socialimage","summary"}]}
- snippet: socialimage 或 summary (前者优先)
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
from src.skills.researcher.searchers.gdelt import GDELTSearcher

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


def test_gdelt_searcher_metadata() -> None:
    """GDELTSearcher 元数据."""
    assert GDELTSearcher.name == "gdelt"
    assert GDELTSearcher.region == SearchRegion.AUTO
    assert GDELTSearcher.cost_tier == "free"
    assert GDELTSearcher.quality_score == 65.0


def test_gdelt_base_url() -> None:
    """base_url 应为 GDELT v2 doc 端点."""
    searcher = GDELTSearcher(_make_settings())
    assert searcher.base_url == "https://api.gdeltproject.org/api/v2/doc/doc"


# ========== 请求构造 ==========


@pytest.mark.asyncio
async def test_search_params_constructed_correctly() -> None:
    """params 应含 query/mode=ArtList/maxrecords/format=json/sort=DateDesc."""
    response = _make_response(200, {"articles": []})
    client = _make_mock_client(response)
    searcher = GDELTSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.gdelt.httpx.AsyncClient", return_value=client):
        await searcher.search("climate change", max_results=10)

    params = client.get.call_args.kwargs["params"]
    assert params["query"] == "climate change"
    assert params["mode"] == "ArtList"
    assert params["maxrecords"] == "10"
    assert params["format"] == "json"
    assert params["sort"] == "DateDesc"


# ========== 响应解析 ==========


@pytest.mark.asyncio
async def test_search_parses_articles() -> None:
    """解析 {"articles": [{"title","url","socialimage","summary"}]} 结构."""
    json_data = {
        "articles": [
            {
                "title": "News Article 1",
                "url": "https://news.example.com/article1",
                "socialimage": "https://img.example.com/1.jpg",
                "summary": "Summary text 1",
            },
            {
                "title": "News Article 2",
                "url": "https://news.example.com/article2",
                "summary": "Summary text 2",
            },
        ]
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = GDELTSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.gdelt.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert len(results) == 2
    assert results[0]["title"] == "News Article 1"
    assert results[0]["url"] == "https://news.example.com/article1"
    # socialimage 优先于 summary
    assert results[0]["snippet"] == "https://img.example.com/1.jpg"
    assert results[0]["source"] == "gdelt"
    assert results[0]["region"] == "auto"
    assert results[1]["title"] == "News Article 2"
    assert results[1]["snippet"] == "Summary text 2"


@pytest.mark.asyncio
async def test_search_normalizes_result_fields() -> None:
    """返回结果含 5 个固定字段."""
    json_data = {"articles": [{"title": "T", "url": "https://x.com/1"}]}
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = GDELTSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.gdelt.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert set(results[0].keys()) == {"title", "url", "snippet", "source", "region"}


@pytest.mark.asyncio
async def test_search_empty_articles_returns_empty() -> None:
    """空 articles 返回空列表."""
    response = _make_response(200, {"articles": []})
    client = _make_mock_client(response)
    searcher = GDELTSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.gdelt.httpx.AsyncClient", return_value=client):
        results = await searcher.search("无结果")

    assert results == []


@pytest.mark.asyncio
async def test_search_missing_articles_key_returns_empty() -> None:
    """响应缺失 articles 键时返回空列表."""
    response = _make_response(200, {})
    client = _make_mock_client(response)
    searcher = GDELTSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.gdelt.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


@pytest.mark.asyncio
async def test_search_truncates_to_max_results() -> None:
    """结果数超过 max_results 时截断."""
    items = [{"title": f"T{i}", "url": f"https://x.com/{i}"} for i in range(10)]
    response = _make_response(200, {"articles": items})
    client = _make_mock_client(response)
    searcher = GDELTSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.gdelt.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test", max_results=3)

    assert len(results) == 3


@pytest.mark.asyncio
async def test_search_skips_items_without_url() -> None:
    """缺失 url 的条目不保留."""
    json_data = {
        "articles": [
            {"title": "no url"},
            {"title": "with url", "url": "https://x.com/1"},
        ]
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = GDELTSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.gdelt.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert len(results) == 1
    assert results[0]["url"] == "https://x.com/1"


# ========== HTTP 错误降级 ==========


@pytest.mark.asyncio
async def test_search_http_500_returns_empty() -> None:
    """HTTP 500 返回空列表."""
    response = _make_response(500, text="Error")
    client = _make_mock_client(response)
    searcher = GDELTSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.gdelt.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


@pytest.mark.asyncio
async def test_search_http_429_returns_empty() -> None:
    """HTTP 429 返回空列表 (GDELT 不抛 QuotaExceededError, 仅降级)."""
    response = _make_response(429, text="Rate limited")
    client = _make_mock_client(response)
    searcher = GDELTSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.gdelt.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


@pytest.mark.asyncio
async def test_search_json_parse_failure_returns_empty() -> None:
    """JSON 解析失败 (GDELT 偶发非标准 JSON) 返回空列表."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("invalid json")
    resp.text = "not json"
    client = _make_mock_client(resp)
    searcher = GDELTSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.gdelt.httpx.AsyncClient", return_value=client):
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
    searcher = GDELTSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.gdelt.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


# ========== query_domains 后置过滤 ==========


@pytest.mark.asyncio
async def test_search_query_domains_filter() -> None:
    """query_domains 后置过滤."""
    json_data = {
        "articles": [
            {"title": "a", "url": "https://reuters.com/article1"},
            {"title": "b", "url": "https://other.com/article2"},
        ]
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = GDELTSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.gdelt.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test", query_domains=["reuters.com"])

    assert len(results) == 1
    assert "reuters.com" in results[0]["url"]
