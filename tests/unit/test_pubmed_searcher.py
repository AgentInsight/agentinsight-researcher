"""单元测试: PubMedSearcher PubMed 医学文献搜索.

验证 src/skills/researcher/searchers/pubmed_searcher.py:
- 无需 API Key (完全免费), 可选配置 pubmed_email
- 两步检索: esearch 获取 PMID 列表 -> esummary 获取摘要
- 请求参数: db=pubmed, term=query, retmax=max_results, retmode=json
- 响应解析: esearchresult.idlist + result.<pmid>.title/abstract
- 空 idlist 返回空列表
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
from src.skills.researcher.searchers.pubmed_searcher import PubMedSearcher

pytestmark = pytest.mark.unit


# ========== 辅助函数 ==========


def _make_settings(email: str = "test@example.com") -> Settings:
    """构造带 pubmed_email 的 Settings (隔离 .env)."""
    return Settings(_env_file=None, pubmed_email=email)


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


def _make_esearch_response(id_list: list[str]) -> MagicMock:
    """构造 esearch 响应."""
    return _make_response(200, {"esearchresult": {"idlist": id_list}})


def _make_esummary_response(items: dict[str, dict]) -> MagicMock:
    """构造 esummary 响应.

    Args:
        items: {pmid: {"title": ..., "abstract": [...]}}
    """
    return _make_response(200, {"result": items})


def _make_mock_client(get_side_effect=None, get_return=None) -> MagicMock:
    """构造支持 async context manager 的 mock httpx client."""
    client = MagicMock()
    if get_side_effect is not None:
        client.get = AsyncMock(side_effect=get_side_effect)
    else:
        client.get = AsyncMock(return_value=get_return)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.aclose = AsyncMock(return_value=None)
    return client


# ========== 类元数据 ==========


def test_pubmed_searcher_metadata() -> None:
    """PubMedSearcher 元数据: name=pubmed, region=ACADEMIC, cost_tier=free, quality_score=90."""
    assert PubMedSearcher.name == "pubmed"
    assert PubMedSearcher.region == SearchRegion.ACADEMIC
    assert PubMedSearcher.cost_tier == "free"
    assert PubMedSearcher.quality_score == 90.0


def test_pubmed_searcher_init_email() -> None:
    """构造函数应从 settings 读取 pubmed_email."""
    settings = _make_settings(email="custom@lab.org")
    searcher = PubMedSearcher(settings)
    assert searcher._email == "custom@lab.org"


def test_pubmed_searcher_urls() -> None:
    """API URL 应为 NCBI E-utilities 端点."""
    assert PubMedSearcher._esearch_url == "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    assert PubMedSearcher._esummary_url == "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


# ========== 两步检索: esearch + esummary ==========


@pytest.mark.asyncio
async def test_search_two_step_esearch_then_esummary() -> None:
    """两步检索: esearch 获取 PMID 列表, esummary 获取摘要."""
    esearch_resp = _make_esearch_response(["111", "222"])
    esummary_resp = _make_esummary_response({
        "111": {"title": "Paper A", "abstract": [{"text": "Abstract A"}]},
        "222": {"title": "Paper B", "abstract": [{"text": "Abstract B"}]},
    })
    client = _make_mock_client(get_side_effect=[esearch_resp, esummary_resp])
    searcher = PubMedSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.pubmed_searcher.httpx.AsyncClient", return_value=client):
        results = await searcher.search("cancer immunotherapy")

    assert len(results) == 2
    assert results[0]["title"] == "Paper A"
    assert results[0]["url"] == "https://pubmed.ncbi.nlm.nih.gov/111/"
    assert results[0]["snippet"] == "Abstract A"
    assert results[0]["source"] == "pubmed"
    assert results[1]["title"] == "Paper B"


@pytest.mark.asyncio
async def test_search_esearch_params_contains_term_and_retmax() -> None:
    """esearch 请求参数含 db=pubmed, term=query, retmax=max_results."""
    esearch_resp = _make_esearch_response([])
    client = _make_mock_client(get_return=esearch_resp)
    searcher = PubMedSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.pubmed_searcher.httpx.AsyncClient", return_value=client):
        await searcher.search("covid vaccine", max_results=7)

    first_call_params = client.get.call_args_list[0].kwargs["params"]
    assert first_call_params["db"] == "pubmed"
    assert first_call_params["term"] == "covid vaccine"
    assert first_call_params["retmax"] == 7
    assert first_call_params["retmode"] == "json"


@pytest.mark.asyncio
async def test_search_esearch_includes_email_when_configured() -> None:
    """配置了 email 时, esearch 参数含 email 字段."""
    esearch_resp = _make_esearch_response([])
    client = _make_mock_client(get_return=esearch_resp)
    searcher = PubMedSearcher(_make_settings(email="lab@uni.edu"))

    with patch("src.skills.researcher.searchers.pubmed_searcher.httpx.AsyncClient", return_value=client):
        await searcher.search("test")

    first_call_params = client.get.call_args_list[0].kwargs["params"]
    assert first_call_params["email"] == "lab@uni.edu"


@pytest.mark.asyncio
async def test_search_esummary_params_contains_id_list() -> None:
    """esummary 请求参数 id 应为逗号分隔的 PMID 列表."""
    esearch_resp = _make_esearch_response(["111", "222", "333"])
    esummary_resp = _make_esummary_response({})
    client = _make_mock_client(get_side_effect=[esearch_resp, esummary_resp])
    searcher = PubMedSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.pubmed_searcher.httpx.AsyncClient", return_value=client):
        await searcher.search("test")

    second_call_params = client.get.call_args_list[1].kwargs["params"]
    assert second_call_params["id"] == "111,222,333"
    assert second_call_params["db"] == "pubmed"


# ========== 空结果处理 ==========


@pytest.mark.asyncio
async def test_search_empty_idlist_returns_empty_list() -> None:
    """esearch 返回空 idlist 时, 不调用 esummary, 返回空列表."""
    esearch_resp = _make_esearch_response([])
    client = _make_mock_client(get_return=esearch_resp)
    searcher = PubMedSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.pubmed_searcher.httpx.AsyncClient", return_value=client):
        results = await searcher.search("无结果查询")

    assert results == []
    # idlist 为空时不调用 esummary
    assert client.get.call_count == 1


# ========== 结果归一化 ==========


@pytest.mark.asyncio
async def test_search_normalizes_result_fields() -> None:
    """返回结果含 5 个固定字段: title/url/snippet/source/region."""
    esearch_resp = _make_esearch_response(["111"])
    esummary_resp = _make_esummary_response({
        "111": {"title": "T", "abstract": [{"text": "S"}]}
    })
    client = _make_mock_client(get_side_effect=[esearch_resp, esummary_resp])
    searcher = PubMedSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.pubmed_searcher.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert len(results) == 1
    assert set(results[0].keys()) == {"title", "url", "snippet", "source", "region"}
    assert results[0]["source"] == "pubmed"
    assert results[0]["region"] == "academic"


@pytest.mark.asyncio
async def test_search_abstract_string_fallback() -> None:
    """abstract 为字符串时直接用作 snippet."""
    esearch_resp = _make_esearch_response(["111"])
    esummary_resp = _make_esummary_response({
        "111": {"title": "T", "abstract": "String abstract"}
    })
    client = _make_mock_client(get_side_effect=[esearch_resp, esummary_resp])
    searcher = PubMedSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.pubmed_searcher.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results[0]["snippet"] == "String abstract"


@pytest.mark.asyncio
async def test_search_skips_missing_pmid_in_esummary() -> None:
    """esummary 缺少某 PMID 的数据时跳过该项."""
    esearch_resp = _make_esearch_response(["111", "222"])
    esummary_resp = _make_esummary_response({
        "111": {"title": "Found", "abstract": []},
        # 222 缺失
    })
    client = _make_mock_client(get_side_effect=[esearch_resp, esummary_resp])
    searcher = PubMedSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.pubmed_searcher.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert len(results) == 1
    assert results[0]["title"] == "Found"


# ========== HTTP 错误降级 ==========


@pytest.mark.asyncio
async def test_search_esearch_http_error_returns_empty() -> None:
    """esearch HTTP 错误时返回空列表, 不抛异常."""
    esearch_resp = _make_response(500, text="Internal Server Error")
    client = _make_mock_client(get_return=esearch_resp)
    searcher = PubMedSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.pubmed_searcher.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


@pytest.mark.asyncio
async def test_search_esummary_http_error_returns_empty() -> None:
    """esummary HTTP 错误时返回空列表, 不抛异常."""
    esearch_resp = _make_esearch_response(["111"])
    esummary_resp = _make_response(500, text="Internal Server Error")
    client = _make_mock_client(get_side_effect=[esearch_resp, esummary_resp])
    searcher = PubMedSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.pubmed_searcher.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


# ========== 网络异常降级 ==========


@pytest.mark.asyncio
async def test_search_network_exception_returns_empty() -> None:
    """httpx 请求抛异常时返回空列表, 不向调用方抛异常."""
    import httpx

    client = MagicMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("network down"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    searcher = PubMedSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.pubmed_searcher.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


# ========== query_domains 后置过滤 ==========


@pytest.mark.asyncio
async def test_search_query_domains_filter() -> None:
    """query_domains 后置过滤: 仅保留 url 含白名单域名的结果."""
    esearch_resp = _make_esearch_response(["111", "222"])
    esummary_resp = _make_esummary_response({
        "111": {"title": "pubmed", "abstract": []},
        "222": {"title": "other", "abstract": []},
    })
    client = _make_mock_client(get_side_effect=[esearch_resp, esummary_resp])
    searcher = PubMedSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.pubmed_searcher.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test", query_domains=["pubmed.ncbi.nlm.nih.gov"])

    assert len(results) == 2  # PubMed URL 均含 pubmed.ncbi.nlm.nih.gov
    assert all("pubmed.ncbi.nlm.nih.gov" in r["url"] for r in results)
