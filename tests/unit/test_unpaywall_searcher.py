"""单元测试: UnpaywallSearcher Unpaywall OA 查找.

验证 src/skills/researcher/searchers/unpaywall.py:
- 必填 unpaywall_email (未配置返回空列表)
- query 必须是 DOI 格式 (10.xxxx/yyy), 否则返回空
- 请求构造: URL = {base_url}/{doi}?email={email}
- 响应解析: title + best_oa_location.url/url_for_pdf + oa_status snippet
- HTTP 错误/JSON 解析失败/网络异常降级返回空列表
- UnpaywallDOISearcher 子类 (别名)

单元测试在构建期执行, 不依赖外部服务, 全部 mock httpx.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.skills.researcher.searchers import SearchRegion
from src.skills.researcher.searchers.unpaywall import UnpaywallDOISearcher, UnpaywallSearcher

pytestmark = pytest.mark.unit


# ========== 辅助函数 ==========


def _make_settings(email: str = "researcher@uni.edu") -> Settings:
    """构造带 unpaywall_email 的 Settings (隔离 .env)."""
    return Settings(_env_file=None, unpaywall_email=email)


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


def test_unpaywall_searcher_metadata() -> None:
    """UnpaywallSearcher 元数据."""
    assert UnpaywallSearcher.name == "unpaywall"
    assert UnpaywallSearcher.region == SearchRegion.ACADEMIC
    assert UnpaywallSearcher.cost_tier == "free"
    assert UnpaywallSearcher.quality_score == 70.0


def test_unpaywall_doi_searcher_metadata() -> None:
    """UnpaywallDOISearcher 子类元数据 (别名)."""
    assert UnpaywallDOISearcher.name == "unpaywall_doi"
    assert issubclass(UnpaywallDOISearcher, UnpaywallSearcher)


def test_unpaywall_base_url() -> None:
    """base_url 应为 Unpaywall v2 端点."""
    searcher = UnpaywallSearcher(_make_settings())
    assert searcher.base_url == "https://api.unpaywall.org/v2"


def test_unpaywall_init_email() -> None:
    """构造函数应从 settings 读取 unpaywall_email."""
    searcher = UnpaywallSearcher(_make_settings(email="custom@lab.org"))
    assert searcher.email == "custom@lab.org"


def test_unpaywall_init_email_empty_when_not_configured() -> None:
    """unpaywall_email 未配置时 email 为空串."""
    searcher = UnpaywallSearcher(Settings(_env_file=None, unpaywall_email=""))
    assert searcher.email == ""


# ========== email 未配置 ==========


@pytest.mark.asyncio
async def test_search_no_email_returns_empty() -> None:
    """unpaywall_email 未配置时返回空列表 (必填字段)."""
    searcher = UnpaywallSearcher(Settings(_env_file=None, unpaywall_email=""))
    results = await searcher.search("10.1234/test")
    assert results == []


# ========== 非 DOI 查询 ==========


@pytest.mark.asyncio
async def test_search_non_doi_query_returns_empty() -> None:
    """非 DOI 查询返回空列表."""
    response = _make_response(200, {"title": "x"})
    client = _make_mock_client(response)
    searcher = UnpaywallSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.unpaywall.httpx.AsyncClient", return_value=client):
        results = await searcher.search("deep learning transformer")

    assert results == []
    # 非 DOI 不应发起 HTTP 请求
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_search_keyword_query_returns_empty() -> None:
    """关键词查询 (不含 10. 前缀) 返回空列表."""
    searcher = UnpaywallSearcher(_make_settings())
    results = await searcher.search("machine learning")
    assert results == []


@pytest.mark.asyncio
async def test_search_doi_without_slash_returns_empty() -> None:
    """以 10. 开头但无斜杠的查询返回空列表 (非完整 DOI)."""
    searcher = UnpaywallSearcher(_make_settings())
    results = await searcher.search("10.1234")
    assert results == []


@pytest.mark.asyncio
async def test_search_strips_query_whitespace() -> None:
    """DOI 查询前后空格应被 strip."""
    response = _make_response(
        200,
        {"title": "T", "best_oa_location": {"url": "https://oa.example.com/x"}, "oa_status": "green"},
    )
    client = _make_mock_client(response)
    searcher = UnpaywallSearcher(_make_settings(email="user@x.com"))

    with patch("src.skills.researcher.searchers.unpaywall.httpx.AsyncClient", return_value=client):
        await searcher.search("  10.1234/test  ")

    # URL 应含去空格的 DOI
    called_url = client.get.call_args.args[0]
    assert "10.1234/test" in called_url
    assert "user@x.com" in called_url


# ========== 请求构造 ==========


@pytest.mark.asyncio
async def test_search_url_contains_doi_and_email() -> None:
    """请求 URL 应含 DOI 和 email 参数."""
    response = _make_response(200, {"title": "T", "best_oa_location": {"url": "https://oa.com/x"}})
    client = _make_mock_client(response)
    searcher = UnpaywallSearcher(_make_settings(email="lab@uni.edu"))

    with patch("src.skills.researcher.searchers.unpaywall.httpx.AsyncClient", return_value=client):
        await searcher.search("10.1234/foo")

    called_url = client.get.call_args.args[0]
    assert "https://api.unpaywall.org/v2/10.1234/foo" in called_url
    assert "email=lab@uni.edu" in called_url


# ========== 响应解析 ==========


@pytest.mark.asyncio
async def test_search_parses_best_oa_location_url() -> None:
    """解析 best_oa_location.url 作为结果 URL."""
    json_data = {
        "title": "Open Access Paper",
        "best_oa_location": {"url": "https://oa.example.com/paper.pdf"},
        "oa_status": "gold",
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = UnpaywallSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.unpaywall.httpx.AsyncClient", return_value=client):
        results = await searcher.search("10.1234/test")

    assert len(results) == 1
    assert results[0]["title"] == "Open Access Paper"
    assert results[0]["url"] == "https://oa.example.com/paper.pdf"
    assert results[0]["snippet"] == "OA版本: gold"
    assert results[0]["source"] == "unpaywall"
    assert results[0]["region"] == "academic"


@pytest.mark.asyncio
async def test_search_fallback_url_for_pdf() -> None:
    """best_oa_location.url 为空时, 降级使用 url_for_pdf."""
    json_data = {
        "title": "Paper X",
        "best_oa_location": {"url_for_pdf": "https://oa.example.com/alt.pdf"},
        "oa_status": "hybrid",
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = UnpaywallSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.unpaywall.httpx.AsyncClient", return_value=client):
        results = await searcher.search("10.1234/x")

    assert len(results) == 1
    assert results[0]["url"] == "https://oa.example.com/alt.pdf"
    assert results[0]["snippet"] == "OA版本: hybrid"


@pytest.mark.asyncio
async def test_search_default_oa_status_unknown() -> None:
    """响应缺失 oa_status 时, snippet 使用 'unknown'."""
    json_data = {
        "title": "T",
        "best_oa_location": {"url": "https://oa.com/x"},
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = UnpaywallSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.unpaywall.httpx.AsyncClient", return_value=client):
        results = await searcher.search("10.1/x")

    assert results[0]["snippet"] == "OA版本: unknown"


@pytest.mark.asyncio
async def test_search_no_oa_url_returns_empty() -> None:
    """best_oa_location.url 和 url_for_pdf 均为空时返回空列表."""
    json_data = {"title": "T", "best_oa_location": {}}
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = UnpaywallSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.unpaywall.httpx.AsyncClient", return_value=client):
        results = await searcher.search("10.1/x")

    assert results == []


@pytest.mark.asyncio
async def test_search_no_best_oa_location_returns_empty() -> None:
    """响应缺失 best_oa_location 时返回空列表."""
    json_data = {"title": "T"}
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = UnpaywallSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.unpaywall.httpx.AsyncClient", return_value=client):
        results = await searcher.search("10.1/x")

    assert results == []


@pytest.mark.asyncio
async def test_search_returns_at_most_one_result() -> None:
    """Unpaywall 单条记录, 返回列表长度 <= 1."""
    response = _make_response(
        200, {"title": "T", "best_oa_location": {"url": "https://oa.com/x"}}
    )
    client = _make_mock_client(response)
    searcher = UnpaywallSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.unpaywall.httpx.AsyncClient", return_value=client):
        results = await searcher.search("10.1/x", max_results=10)

    assert len(results) == 1


# ========== HTTP 错误降级 ==========


@pytest.mark.asyncio
async def test_search_http_404_returns_empty() -> None:
    """HTTP 404 (DOI 未找到) 返回空列表."""
    response = _make_response(404, text="Not Found")
    client = _make_mock_client(response)
    searcher = UnpaywallSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.unpaywall.httpx.AsyncClient", return_value=client):
        results = await searcher.search("10.1/missing")

    assert results == []


@pytest.mark.asyncio
async def test_search_http_422_returns_empty() -> None:
    """HTTP 422 (email 缺失/格式错误) 返回空列表."""
    response = _make_response(422, text="Unprocessable")
    client = _make_mock_client(response)
    searcher = UnpaywallSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.unpaywall.httpx.AsyncClient", return_value=client):
        results = await searcher.search("10.1/x")

    assert results == []


@pytest.mark.asyncio
async def test_search_json_parse_failure_returns_empty() -> None:
    """JSON 解析失败返回空列表."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("invalid json")
    resp.text = "not json"
    client = _make_mock_client(resp)
    searcher = UnpaywallSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.unpaywall.httpx.AsyncClient", return_value=client):
        results = await searcher.search("10.1/x")

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
    searcher = UnpaywallSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.unpaywall.httpx.AsyncClient", return_value=client):
        results = await searcher.search("10.1/x")

    assert results == []


# ========== UnpaywallDOISearcher 子类 ==========


@pytest.mark.asyncio
async def test_unpaywall_doi_searcher_works() -> None:
    """UnpaywallDOISearcher 子类应继承 UnpaywallSearcher 的 search 方法."""
    response = _make_response(
        200,
        {"title": "Sub", "best_oa_location": {"url": "https://oa.com/sub"}, "oa_status": "bronze"},
    )
    client = _make_mock_client(response)
    searcher = UnpaywallDOISearcher(_make_settings())

    with patch("src.skills.researcher.searchers.unpaywall.httpx.AsyncClient", return_value=client):
        results = await searcher.search("10.1234/sub")

    assert len(results) == 1
    assert results[0]["source"] == "unpaywall_doi"
    assert results[0]["title"] == "Sub"
