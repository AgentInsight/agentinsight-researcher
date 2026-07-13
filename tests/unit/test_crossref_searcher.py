"""单元测试: CrossRefSearcher CrossRef DOI 学术搜索.

验证 src/skills/researcher/searchers/crossref.py:
- 无需 API Key (完全免费), 可选配置 crossref_mailto 进入 polite pool
- 请求构造: params (query/rows/mailto) + headers (User-Agent)
- 响应解析: {"message": {"items": [{"title","DOI","subtitle","abstract"}]}} 结构
- DOI 转 URL: https://doi.org/<DOI>
- HTTP 错误/网络异常/JSON 解析失败降级返回空列表
- query_domains 后置过滤

单元测试在构建期执行, 不依赖外部服务, 全部 mock httpx.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.skills.researcher.searchers import SearchRegion
from src.skills.researcher.searchers.crossref import CrossRefSearcher

pytestmark = pytest.mark.unit


# ========== 辅助函数 ==========


def _make_settings(mailto: str = "test@lab.org") -> Settings:
    """构造带 crossref_mailto 的 Settings (隔离 .env)."""
    return Settings(_env_file=None, crossref_mailto=mailto)


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


def test_crossref_searcher_metadata() -> None:
    """CrossRefSearcher 元数据."""
    assert CrossRefSearcher.name == "crossref"
    assert CrossRefSearcher.region == SearchRegion.ACADEMIC
    assert CrossRefSearcher.cost_tier == "free"
    assert CrossRefSearcher.quality_score == 75.0


def test_crossref_searcher_init_mailto() -> None:
    """构造函数应从 settings 读取 crossref_mailto."""
    settings = _make_settings(mailto="custom@uni.edu")
    searcher = CrossRefSearcher(settings)
    assert searcher.mailto == "custom@uni.edu"


def test_crossref_searcher_base_url() -> None:
    """base_url 应为 CrossRef API 端点."""
    searcher = CrossRefSearcher(_make_settings())
    assert searcher.base_url == "https://api.crossref.org/works"


# ========== 请求构造 ==========


@pytest.mark.asyncio
async def test_search_params_query_and_rows() -> None:
    """params 含 query=query, rows=max_results."""
    response = _make_response(200, {"message": {"items": []}})
    client = _make_mock_client(response)
    searcher = CrossRefSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.crossref.httpx.AsyncClient", return_value=client):
        await searcher.search("deep learning", max_results=10)

    params = client.get.call_args.kwargs["params"]
    assert params["query"] == "deep learning"
    assert params["rows"] == 10


@pytest.mark.asyncio
async def test_search_params_mailto_when_configured() -> None:
    """配置了 mailto 时, params 含 mailto 字段."""
    response = _make_response(200, {"message": {"items": []}})
    client = _make_mock_client(response)
    searcher = CrossRefSearcher(_make_settings(mailto="lab@uni.edu"))

    with patch("src.skills.researcher.searchers.crossref.httpx.AsyncClient", return_value=client):
        await searcher.search("test")

    params = client.get.call_args.kwargs["params"]
    assert params["mailto"] == "lab@uni.edu"


@pytest.mark.asyncio
async def test_search_headers_user_agent_with_mailto() -> None:
    """配置了 mailto 时, User-Agent 含 mailto."""
    response = _make_response(200, {"message": {"items": []}})
    client = _make_mock_client(response)
    searcher = CrossRefSearcher(_make_settings(mailto="lab@uni.edu"))

    with patch("src.skills.researcher.searchers.crossref.httpx.AsyncClient", return_value=client):
        await searcher.search("test")

    headers = client.get.call_args.kwargs["headers"]
    assert "lab@uni.edu" in headers["User-Agent"]


# ========== 响应解析 ==========


@pytest.mark.asyncio
async def test_search_parses_items() -> None:
    """解析 {"message": {"items": [{"title","DOI","subtitle"}]}} 结构."""
    json_data = {
        "message": {
            "items": [
                {"title": ["Paper A"], "DOI": "10.1234/a", "subtitle": ["Sub A"]},
                {"title": ["Paper B"], "DOI": "10.1234/b", "subtitle": ["Sub B"]},
            ]
        }
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = CrossRefSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.crossref.httpx.AsyncClient", return_value=client):
        results = await searcher.search("AI")

    assert len(results) == 2
    assert results[0]["title"] == "Paper A"
    assert results[0]["url"] == "https://doi.org/10.1234/a"
    assert results[0]["snippet"] == "Sub A"
    assert results[0]["source"] == "crossref"
    assert results[0]["region"] == "academic"


@pytest.mark.asyncio
async def test_search_normalizes_result_fields() -> None:
    """返回结果含 5 个固定字段."""
    json_data = {
        "message": {"items": [{"title": ["T"], "DOI": "10.1/t", "subtitle": ["S"]}]}
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = CrossRefSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.crossref.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert set(results[0].keys()) == {"title", "url", "snippet", "source", "region"}


@pytest.mark.asyncio
async def test_search_empty_items() -> None:
    """空 items 返回空列表."""
    response = _make_response(200, {"message": {"items": []}})
    client = _make_mock_client(response)
    searcher = CrossRefSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.crossref.httpx.AsyncClient", return_value=client):
        results = await searcher.search("无结果")

    assert results == []


@pytest.mark.asyncio
async def test_search_truncates_to_max_results() -> None:
    """结果数超过 max_results 时截断."""
    items = [{"title": [f"T{i}"], "DOI": f"10.1/{i}", "subtitle": ["S"]} for i in range(10)]
    response = _make_response(200, {"message": {"items": items}})
    client = _make_mock_client(response)
    searcher = CrossRefSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.crossref.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test", max_results=3)

    assert len(results) == 3


# ========== HTTP 错误降级 ==========


@pytest.mark.asyncio
async def test_search_http_500_returns_empty() -> None:
    """HTTP 500 返回空列表."""
    response = _make_response(500, text="Error")
    client = _make_mock_client(response)
    searcher = CrossRefSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.crossref.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


@pytest.mark.asyncio
async def test_search_json_parse_failure_returns_empty() -> None:
    """JSON 解析失败返回空列表."""
    response = _make_response(200, text="not json")
    response.json.side_effect = ValueError("invalid json")
    client = _make_mock_client(response)
    searcher = CrossRefSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.crossref.httpx.AsyncClient", return_value=client):
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
    searcher = CrossRefSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.crossref.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


# ========== query_domains 后置过滤 ==========


@pytest.mark.asyncio
async def test_search_query_domains_filter() -> None:
    """query_domains 后置过滤."""
    json_data = {
        "message": {
            "items": [
                {"title": ["a"], "DOI": "10.1/a", "subtitle": ["s"]},
                {"title": ["b"], "DOI": "10.1/b", "subtitle": ["s"]},
            ]
        }
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = CrossRefSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.crossref.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test", query_domains=["doi.org"])

    assert len(results) == 2  # DOI URL 均含 doi.org
