"""单元测试: OpenAlexSearcher OpenAlex 学术搜索.

验证 src/skills/researcher/searchers/openalex.py:
- 无需 API Key (完全免费), 可选配置 openalex_email 进入 polite pool
- _reconstruct_abstract: 倒排索引 -> 纯文本摘要
- _format_authors: authorships -> 作者显示名列表
- 请求构造: params (search/per_page/mailto) + headers (User-Agent)
- 响应解析: {"results": [{"title","doi","authorships","abstract_inverted_index"}]}
- snippet 拼接: 摘要 + 作者 + 发表日期 + DOI
- HTTP 错误/网络异常降级返回空列表
- query_domains 后置过滤

单元测试在构建期执行, 不依赖外部服务, 全部 mock httpx.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.skills.researcher.searchers import SearchRegion
from src.skills.researcher.searchers.openalex import OpenAlexSearcher

pytestmark = pytest.mark.unit


# ========== 辅助函数 ==========


def _make_settings(email: str = "test@lab.org") -> Settings:
    """构造带 openalex_email 的 Settings (隔离 .env)."""
    return Settings(_env_file=None, openalex_email=email)


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


def _make_mock_client(response: MagicMock) -> MagicMock:
    """构造支持 async context manager 的 mock httpx client."""
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.aclose = AsyncMock(return_value=None)
    return client


# ========== 类元数据 ==========


def test_openalex_searcher_metadata() -> None:
    """OpenAlexSearcher 元数据."""
    assert OpenAlexSearcher.name == "openalex"
    assert OpenAlexSearcher.region == SearchRegion.ACADEMIC
    assert OpenAlexSearcher.cost_tier == "free"
    assert OpenAlexSearcher.quality_score == 78.0


def test_openalex_api_url() -> None:
    """API URL 应为 OpenAlex Works 端点."""
    assert OpenAlexSearcher._api_url == "https://api.openalex.org/works"


def test_openalex_init_email() -> None:
    """构造函数应从 settings 读取 openalex_email."""
    settings = _make_settings(email="custom@uni.edu")
    searcher = OpenAlexSearcher(settings)
    assert searcher._email == "custom@uni.edu"


# ========== _reconstruct_abstract 静态方法 ==========


def test_reconstruct_abstract_normal() -> None:
    """倒排索引正确重建为纯文本."""
    inverted = {"Hello": [0], "world": [1], "AI": [3], "research": [2]}
    result = OpenAlexSearcher._reconstruct_abstract(inverted)
    assert result == "Hello world research AI"


def test_reconstruct_abstract_empty() -> None:
    """空倒排索引返回空字符串."""
    assert OpenAlexSearcher._reconstruct_abstract(None) == ""
    assert OpenAlexSearcher._reconstruct_abstract({}) == ""


def test_reconstruct_abstract_multi_positions() -> None:
    """同一词多次出现时正确重建."""
    inverted = {"the": [0, 3], "cat": [1], "dog": [4], "chased": [2]}
    result = OpenAlexSearcher._reconstruct_abstract(inverted)
    assert result == "the cat chased the dog"


# ========== _format_authors 静态方法 ==========


def test_format_authors_normal() -> None:
    """正常作者列表拼接."""
    authorships = [
        {"author": {"display_name": "Alice"}},
        {"author": {"display_name": "Bob"}},
    ]
    assert OpenAlexSearcher._format_authors(authorships) == "Alice, Bob"


def test_format_authors_empty() -> None:
    """空作者列表返回空字符串."""
    assert OpenAlexSearcher._format_authors(None) == ""
    assert OpenAlexSearcher._format_authors([]) == ""


def test_format_authors_skips_empty_name() -> None:
    """跳过无 display_name 的作者."""
    authorships = [
        {"author": {"display_name": "Alice"}},
        {"author": {}},
        {"author": {"display_name": "Bob"}},
    ]
    assert OpenAlexSearcher._format_authors(authorships) == "Alice, Bob"


# ========== 请求构造 ==========


@pytest.mark.asyncio
async def test_search_params_search_and_per_page() -> None:
    """params 含 search=query, per_page=max_results."""
    response = _make_response(200, {"results": []})
    client = _make_mock_client(response)
    searcher = OpenAlexSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.openalex.httpx.AsyncClient", return_value=client):
        await searcher.search("deep learning", max_results=10)

    params = client.get.call_args.kwargs["params"]
    assert params["search"] == "deep learning"
    assert params["per_page"] == 10


@pytest.mark.asyncio
async def test_search_params_mailto_when_email_configured() -> None:
    """配置了 email 时, params 含 mailto 字段."""
    response = _make_response(200, {"results": []})
    client = _make_mock_client(response)
    searcher = OpenAlexSearcher(_make_settings(email="lab@uni.edu"))

    with patch("src.skills.researcher.searchers.openalex.httpx.AsyncClient", return_value=client):
        await searcher.search("test")

    params = client.get.call_args.kwargs["params"]
    assert params["mailto"] == "lab@uni.edu"


@pytest.mark.asyncio
async def test_search_headers_user_agent_with_email() -> None:
    """配置了 email 时, User-Agent 含邮箱."""
    response = _make_response(200, {"results": []})
    client = _make_mock_client(response)
    searcher = OpenAlexSearcher(_make_settings(email="lab@uni.edu"))

    with patch("src.skills.researcher.searchers.openalex.httpx.AsyncClient", return_value=client):
        await searcher.search("test")

    headers = client.get.call_args.kwargs["headers"]
    assert "lab@uni.edu" in headers["User-Agent"]


# ========== 响应解析 ==========


@pytest.mark.asyncio
async def test_search_parses_results() -> None:
    """解析 {"results": [{"title","doi","authorships","abstract_inverted_index"}]} 结构."""
    json_data = {
        "results": [
            {
                "title": "Paper A",
                "doi": "https://doi.org/10.1234/a",
                "authorships": [{"author": {"display_name": "Alice"}}],
                "abstract_inverted_index": {"Hello": [0], "world": [1]},
                "publication_date": "2024-01-15",
            }
        ]
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = OpenAlexSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.openalex.httpx.AsyncClient", return_value=client):
        results = await searcher.search("AI")

    assert len(results) == 1
    assert results[0]["title"] == "Paper A"
    assert results[0]["url"] == "https://doi.org/10.1234/a"
    assert "Hello world" in results[0]["snippet"]
    assert "Alice" in results[0]["snippet"]
    assert "2024-01-15" in results[0]["snippet"]
    assert results[0]["source"] == "openalex"
    assert results[0]["region"] == "academic"


@pytest.mark.asyncio
async def test_search_normalizes_result_fields() -> None:
    """返回结果含 5 个固定字段."""
    json_data = {"results": [{"title": "T", "doi": "https://doi.org/10.1/t"}]}
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = OpenAlexSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.openalex.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert set(results[0].keys()) == {"title", "url", "snippet", "source", "region"}


@pytest.mark.asyncio
async def test_search_empty_results() -> None:
    """空 results 返回空列表."""
    response = _make_response(200, {"results": []})
    client = _make_mock_client(response)
    searcher = OpenAlexSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.openalex.httpx.AsyncClient", return_value=client):
        results = await searcher.search("无结果")

    assert results == []


@pytest.mark.asyncio
async def test_search_truncates_to_max_results() -> None:
    """结果数超过 max_results 时截断."""
    items = [{"title": f"T{i}", "doi": f"https://doi.org/10.1/{i}"} for i in range(10)]
    response = _make_response(200, {"results": items})
    client = _make_mock_client(response)
    searcher = OpenAlexSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.openalex.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test", max_results=3)

    assert len(results) == 3


# ========== HTTP 错误降级 ==========


@pytest.mark.asyncio
async def test_search_http_500_returns_empty() -> None:
    """HTTP 500 返回空列表."""
    response = _make_response(500, text="Error")
    client = _make_mock_client(response)
    searcher = OpenAlexSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.openalex.httpx.AsyncClient", return_value=client):
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
    searcher = OpenAlexSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.openalex.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


# ========== query_domains 后置过滤 ==========


@pytest.mark.asyncio
async def test_search_query_domains_filter() -> None:
    """query_domains 后置过滤."""
    json_data = {
        "results": [
            {"title": "a", "doi": "https://arxiv.org/1"},
            {"title": "b", "doi": "https://other.com/2"},
        ]
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = OpenAlexSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.openalex.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test", query_domains=["arxiv.org"])

    assert len(results) == 1
    assert "arxiv.org" in results[0]["url"]
