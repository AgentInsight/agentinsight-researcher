"""单元测试: SemanticScholarSearcher Semantic Scholar 学术搜索.

验证 src/skills/researcher/searchers/semantic_scholar_searcher.py:
- 主源: Semantic Scholar Graph API (可选 API Key)
- 备用源: CrossRef (主源故障/限流/空结果时自动切换)
- HTTP 429 抛 QuotaExceededError (engine=semantic_scholar)
- CrossRef 超时保护 (asyncio.wait_for, 30s)
- 双源均失败时: 有 QuotaExceededError 则抛出, 否则返回空列表
- query_domains 后置过滤

单元测试在构建期执行, 不依赖外部服务, 全部 mock httpx.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.skills.researcher.searchers import SearchRegion
from src.skills.researcher.searchers.exceptions import QuotaExceededError
from src.skills.researcher.searchers.semantic_scholar_searcher import SemanticScholarSearcher

pytestmark = pytest.mark.unit


# ========== 辅助函数 ==========


def _make_settings(api_key: str | None = None) -> Settings:
    """构造 Settings (隔离 .env)."""
    return Settings(_env_file=None, semantic_scholar_api_key=api_key)


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


def test_semantic_scholar_metadata() -> None:
    """SemanticScholarSearcher 元数据."""
    assert SemanticScholarSearcher.name == "semantic_scholar"
    assert SemanticScholarSearcher.region == SearchRegion.ACADEMIC
    assert SemanticScholarSearcher.cost_tier == "free"
    assert SemanticScholarSearcher.quality_score == 80.0


def test_semantic_scholar_api_url() -> None:
    """API URL 应为 Semantic Scholar Graph API 端点."""
    assert SemanticScholarSearcher._api_url == "https://api.semanticscholar.org/graph/v1/paper/search"


def test_semantic_scholar_init_api_key() -> None:
    """构造函数应从 settings 读取 semantic_scholar_api_key."""
    settings = _make_settings(api_key="test-ss-key")
    searcher = SemanticScholarSearcher(settings)
    assert searcher._api_key == "test-ss-key"


# ========== 主源搜索: Semantic Scholar ==========


@pytest.mark.asyncio
async def test_search_main_source_returns_results() -> None:
    """主源 Semantic Scholar 返回结果."""
    ss_response = _make_response(200, {
        "data": [
            {"title": "Paper A", "url": "https://x.com/a", "abstract": "Abstract A"},
            {"title": "Paper B", "url": "https://x.com/b", "abstract": "Abstract B"},
        ]
    })
    client = _make_mock_client(get_return=ss_response)
    searcher = SemanticScholarSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.semantic_scholar_searcher.httpx.AsyncClient", return_value=client):
        results = await searcher.search("AI")

    assert len(results) == 2
    assert results[0]["title"] == "Paper A"
    assert results[0]["url"] == "https://x.com/a"
    assert results[0]["snippet"] == "Abstract A"
    assert results[0]["source"] == "semantic_scholar"
    assert results[0]["region"] == "academic"


@pytest.mark.asyncio
async def test_search_main_source_params() -> None:
    """主源请求参数含 query, limit, fields.

    注: 主源返回空结果时会回退 CrossRef, 需用 call_args_list[0] 检查主源请求.
    """
    ss_response = _make_response(200, {"data": []})
    crossref_response = _make_response(200, {"message": {"items": []}})
    client = _make_mock_client(get_side_effect=[ss_response, crossref_response])
    searcher = SemanticScholarSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.semantic_scholar_searcher.httpx.AsyncClient", return_value=client):
        await searcher.search("deep learning", max_results=10)

    # call_args_list[0] = 主源 Semantic Scholar 请求 (call_args_list[1] = CrossRef 备用)
    params = client.get.call_args_list[0].kwargs["params"]
    assert params["query"] == "deep learning"
    assert params["limit"] == 10
    assert "title,url,abstract,year" in params["fields"]


@pytest.mark.asyncio
async def test_search_api_key_header() -> None:
    """配置了 API Key 时, 主源请求头含 x-api-key.

    注: 主源返回空结果时会回退 CrossRef, 需用 call_args_list[0] 检查主源请求.
    """
    ss_response = _make_response(200, {"data": []})
    crossref_response = _make_response(200, {"message": {"items": []}})
    client = _make_mock_client(get_side_effect=[ss_response, crossref_response])
    searcher = SemanticScholarSearcher(_make_settings(api_key="my-key"))

    with patch("src.skills.researcher.searchers.semantic_scholar_searcher.httpx.AsyncClient", return_value=client):
        await searcher.search("test")

    # call_args_list[0] = 主源 Semantic Scholar 请求
    headers = client.get.call_args_list[0].kwargs["headers"]
    assert headers["x-api-key"] == "my-key"


@pytest.mark.asyncio
async def test_search_no_api_key_no_header() -> None:
    """未配置 API Key 时, 请求头不含 x-api-key."""
    ss_response = _make_response(200, {"data": []})
    client = _make_mock_client(get_return=ss_response)
    searcher = SemanticScholarSearcher(_make_settings(api_key=None))

    with patch("src.skills.researcher.searchers.semantic_scholar_searcher.httpx.AsyncClient", return_value=client):
        await searcher.search("test")

    headers = client.get.call_args.kwargs["headers"]
    assert "x-api-key" not in headers


# ========== 空结果切换 CrossRef 备用源 ==========


@pytest.mark.asyncio
async def test_search_empty_results_falls_back_to_crossref() -> None:
    """主源返回空结果时, 切换 CrossRef 备用源."""
    ss_response = _make_response(200, {"data": []})
    crossref_response = _make_response(200, {
        "message": {
            "items": [
                {"title": ["CrossRef Paper"], "DOI": "10.1234/test", "subtitle": ["Sub"]}
            ]
        }
    })
    client = _make_mock_client(get_side_effect=[ss_response, crossref_response])
    searcher = SemanticScholarSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.semantic_scholar_searcher.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert len(results) == 1
    assert results[0]["title"] == "CrossRef Paper"
    assert results[0]["url"] == "https://doi.org/10.1234/test"
    assert results[0]["source"] == "crossref"  # 备用源标记


# ========== 429 限流: 抛 QuotaExceededError ==========


@pytest.mark.asyncio
async def test_search_http_429_raises_quota_exceeded() -> None:
    """主源 HTTP 429 且 CrossRef 备用源也失败时, 抛 QuotaExceededError.

    注: 主源 429 后会回退 CrossRef; 仅当 CrossRef 也失败时才重新抛出 QuotaExceededError.
    """
    ss_response = _make_response(429, text="Rate limited", headers={})
    # CrossRef 备用源也失败 (HTTP 500), 触发双源失败路径
    crossref_response = _make_response(500, text="CrossRef Error")
    client = _make_mock_client(get_side_effect=[ss_response, crossref_response])
    searcher = SemanticScholarSearcher(_make_settings())

    with pytest.raises(QuotaExceededError) as exc_info:
        with patch("src.skills.researcher.searchers.semantic_scholar_searcher.httpx.AsyncClient", return_value=client):
            await searcher.search("test")

    assert exc_info.value.engine == "semantic_scholar"


@pytest.mark.asyncio
async def test_search_429_retry_after_header() -> None:
    """429 时额度重置时间优先读取 Retry-After 头 (需 CrossRef 也失败才抛出)."""
    ss_response = _make_response(429, text="limited", headers={"Retry-After": "300"})
    # CrossRef 备用源也失败, 触发 QuotaExceededError 重新抛出
    crossref_response = _make_response(500, text="CrossRef Error")
    client = _make_mock_client(get_side_effect=[ss_response, crossref_response])
    searcher = SemanticScholarSearcher(_make_settings())

    from datetime import UTC, datetime

    with pytest.raises(QuotaExceededError) as exc_info:
        with patch("src.skills.researcher.searchers.semantic_scholar_searcher.httpx.AsyncClient", return_value=client):
            await searcher.search("test")

    now = datetime.now(UTC)
    delta = (exc_info.value.reset_at - now).total_seconds()
    assert 280 < delta < 320


# ========== 主源异常切换 CrossRef ==========


@pytest.mark.asyncio
async def test_search_main_source_exception_falls_back() -> None:
    """主源异常时切换 CrossRef 备用源."""
    ss_response = _make_response(500, text="Internal Server Error")
    crossref_response = _make_response(200, {
        "message": {"items": [{"title": ["FB"], "DOI": "10.1/fb"}]}
    })
    client = _make_mock_client(get_side_effect=[ss_response, crossref_response])
    searcher = SemanticScholarSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.semantic_scholar_searcher.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert len(results) == 1
    assert results[0]["source"] == "crossref"


# ========== 双源均失败 ==========


@pytest.mark.asyncio
async def test_search_both_sources_fail_returns_empty() -> None:
    """主源空结果 + CrossRef 异常 → 返回空列表."""
    ss_response = _make_response(200, {"data": []})
    crossref_response = _make_response(500, text="Error")
    client = _make_mock_client(get_side_effect=[ss_response, crossref_response])
    searcher = SemanticScholarSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.semantic_scholar_searcher.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


# ========== 结果归一化 ==========


@pytest.mark.asyncio
async def test_search_normalizes_result_fields() -> None:
    """返回结果含 5 个固定字段: title/url/snippet/source/region."""
    ss_response = _make_response(200, {
        "data": [{"title": "T", "url": "https://x.com", "abstract": "S"}]
    })
    client = _make_mock_client(get_return=ss_response)
    searcher = SemanticScholarSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.semantic_scholar_searcher.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert len(results) == 1
    assert set(results[0].keys()) == {"title", "url", "snippet", "source", "region"}


@pytest.mark.asyncio
async def test_search_truncates_to_max_results() -> None:
    """结果数超过 max_results 时截断."""
    items = [{"title": f"T{i}", "url": f"https://x.com/{i}", "abstract": "S"} for i in range(10)]
    ss_response = _make_response(200, {"data": items})
    client = _make_mock_client(get_return=ss_response)
    searcher = SemanticScholarSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.semantic_scholar_searcher.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test", max_results=3)

    assert len(results) == 3


# ========== query_domains 后置过滤 ==========


@pytest.mark.asyncio
async def test_search_query_domains_filter() -> None:
    """query_domains 后置过滤."""
    ss_response = _make_response(200, {
        "data": [
            {"title": "a", "url": "https://arxiv.org/1", "abstract": "s"},
            {"title": "b", "url": "https://other.com/2", "abstract": "s"},
        ]
    })
    client = _make_mock_client(get_return=ss_response)
    searcher = SemanticScholarSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.semantic_scholar_searcher.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test", query_domains=["arxiv.org"])

    assert len(results) == 1
    assert "arxiv.org" in results[0]["url"]
