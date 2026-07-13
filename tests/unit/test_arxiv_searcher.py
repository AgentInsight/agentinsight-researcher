"""单元测试: ArxivSearcher Arxiv 学术论文搜索.

验证 src/skills/researcher/searchers/arxiv.py:
- 无需 API Key (完全免费)
- httpx + Atom XML 解析 (ElementTree)
- 请求参数构造: search_query=all:<query>, sortBy=relevance, sortOrder=descending
- 响应解析: Atom XML entry 结构 (title/summary/id/link)
- HTTP 5xx 可重试 (指数退避), 4xx 不重试
- 网络异常/超时重试, 耗尽后返回空列表
- query_domains 后置过滤

单元测试在构建期执行, 不依赖外部服务, 全部 mock httpx.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.skills.researcher.searchers import SearchRegion
from src.skills.researcher.searchers.arxiv import ArxivSearcher

pytestmark = pytest.mark.unit


# ========== 辅助函数 ==========


def _make_settings() -> Settings:
    """构造 Settings (隔离 .env)."""
    return Settings(_env_file=None)


def _make_response(
    status_code: int = 200,
    text: str = "",
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """构造 mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.headers = headers or {}
    return resp


_ARXIV_XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>{title}</title>
    <summary>{summary}</summary>
    <id>{id_url}</id>
    <link rel="alternate" type="text/html" href="{id_url}"/>
  </entry>
</feed>
"""


def _make_arxiv_xml(
    title: str = "Attention Is All You Need",
    summary: str = "We propose a new architecture...",
    id_url: str = "http://arxiv.org/abs/1706.03762v1",
) -> str:
    """构造 arxiv Atom XML 响应."""
    return _ARXIV_XML_TEMPLATE.format(title=title, summary=summary, id_url=id_url)


def _make_arxiv_xml_multi(entries: list[dict[str, str]]) -> str:
    """构造含多条 entry 的 arxiv Atom XML."""
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<feed xmlns="http://www.w3.org/2005/Atom">']
    for e in entries:
        parts.append(
            f"  <entry>\n"
            f"    <title>{e.get('title', '')}</title>\n"
            f"    <summary>{e.get('summary', '')}</summary>\n"
            f"    <id>{e.get('id', '')}</id>\n"
            f"    <link rel='alternate' type='text/html' href='{e.get('id', '')}'/>\n"
            f"  </entry>"
        )
    parts.append("</feed>")
    return "\n".join(parts)


def _make_mock_client(response: MagicMock) -> MagicMock:
    """构造支持 async context manager 的 mock httpx client."""
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.aclose = AsyncMock(return_value=None)
    return client


# ========== 类元数据 ==========


def test_arxiv_searcher_metadata() -> None:
    """ArxivSearcher 元数据: name=arxiv, region=GLOBAL, cost_tier=free, quality_score=85."""
    assert ArxivSearcher.name == "arxiv"
    assert ArxivSearcher.region == SearchRegion.GLOBAL
    assert ArxivSearcher.cost_tier == "free"
    assert ArxivSearcher.quality_score == 85.0


def test_arxiv_searcher_init_url() -> None:
    """构造函数应设置 base_url 为 arxiv API 端点."""
    searcher = ArxivSearcher(_make_settings())
    assert searcher.base_url == "http://export.arxiv.org/api/query"


def test_arxiv_searcher_no_api_key_required() -> None:
    """Arxiv 无需 API Key, 实例化不依赖任何 Key 配置."""
    searcher = ArxivSearcher(Settings(_env_file=None))
    assert searcher.name == "arxiv"


# ========== 请求参数构造 ==========


@pytest.mark.asyncio
async def test_search_params_contains_search_query() -> None:
    """请求参数 search_query 应为 all:<query> 格式."""
    response = _make_response(200, text=_make_arxiv_xml())
    client = _make_mock_client(response)
    searcher = ArxivSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.arxiv.httpx.AsyncClient", return_value=client):
        await searcher.search("transformer")

    params = client.get.call_args.kwargs["params"]
    assert params["search_query"] == "all:transformer"


@pytest.mark.asyncio
async def test_search_params_contains_max_results() -> None:
    """请求参数 max_results 应反映 max_results 参数 (字符串形式)."""
    response = _make_response(200, text=_make_arxiv_xml())
    client = _make_mock_client(response)
    searcher = ArxivSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.arxiv.httpx.AsyncClient", return_value=client):
        await searcher.search("测试", max_results=8)

    params = client.get.call_args.kwargs["params"]
    assert params["max_results"] == "8"


@pytest.mark.asyncio
async def test_search_params_sort_by_relevance_descending() -> None:
    """请求含 sortBy=relevance, sortOrder=descending."""
    response = _make_response(200, text=_make_arxiv_xml())
    client = _make_mock_client(response)
    searcher = ArxivSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.arxiv.httpx.AsyncClient", return_value=client):
        await searcher.search("AI")

    params = client.get.call_args.kwargs["params"]
    assert params["sortBy"] == "relevance"
    assert params["sortOrder"] == "descending"


# ========== 响应解析 ==========


@pytest.mark.asyncio
async def test_search_parses_xml_entry() -> None:
    """解析 Atom XML entry 结构, 提取 title/summary/url."""
    xml = _make_arxiv_xml(
        title="Attention Is All You Need",
        summary="We propose a transformer architecture.",
        id_url="http://arxiv.org/abs/1706.03762v1",
    )
    response = _make_response(200, text=xml)
    client = _make_mock_client(response)
    searcher = ArxivSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.arxiv.httpx.AsyncClient", return_value=client):
        results = await searcher.search("transformer")

    assert len(results) == 1
    assert results[0]["title"] == "Attention Is All You Need"
    assert results[0]["url"] == "http://arxiv.org/abs/1706.03762v1"
    assert "transformer architecture" in results[0]["snippet"]
    assert results[0]["source"] == "arxiv"
    assert results[0]["region"] == "global"


@pytest.mark.asyncio
async def test_search_parses_multiple_entries() -> None:
    """解析多条 entry."""
    xml = _make_arxiv_xml_multi([
        {"title": "Paper A", "summary": "Summary A", "id": "http://arxiv.org/abs/1"},
        {"title": "Paper B", "summary": "Summary B", "id": "http://arxiv.org/abs/2"},
    ])
    response = _make_response(200, text=xml)
    client = _make_mock_client(response)
    searcher = ArxivSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.arxiv.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert len(results) == 2
    assert results[0]["title"] == "Paper A"
    assert results[1]["title"] == "Paper B"


@pytest.mark.asyncio
async def test_search_normalizes_result_fields() -> None:
    """返回结果含 5 个固定字段: title/url/snippet/source/region."""
    xml = _make_arxiv_xml()
    response = _make_response(200, text=xml)
    client = _make_mock_client(response)
    searcher = ArxivSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.arxiv.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert len(results) == 1
    assert set(results[0].keys()) == {"title", "url", "snippet", "source", "region"}


@pytest.mark.asyncio
async def test_search_snippet_normalized_whitespace() -> None:
    """snippet 应规范化多余空白 (join split)."""
    xml = _make_arxiv_xml(summary="  multiple   spaces   here  ")
    response = _make_response(200, text=xml)
    client = _make_mock_client(response)
    searcher = ArxivSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.arxiv.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results[0]["snippet"] == "multiple spaces here"


@pytest.mark.asyncio
async def test_search_empty_xml_returns_empty_list() -> None:
    """空 feed (无 entry) 返回空列表."""
    xml = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    response = _make_response(200, text=xml)
    client = _make_mock_client(response)
    searcher = ArxivSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.arxiv.httpx.AsyncClient", return_value=client):
        results = await searcher.search("无结果")

    assert results == []


# ========== 结果截断 ==========


@pytest.mark.asyncio
async def test_search_passes_max_results_param() -> None:
    """max_results 应作为 API 参数传递 (arxiv 依赖服务端截断, 非客户端切片)."""
    entries = [
        {"title": f"Paper {i}", "summary": "s", "id": f"http://arxiv.org/abs/{i}"}
        for i in range(10)
    ]
    xml = _make_arxiv_xml_multi(entries)
    response = _make_response(200, text=xml)
    client = _make_mock_client(response)
    searcher = ArxivSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.arxiv.httpx.AsyncClient", return_value=client):
        await searcher.search("test", max_results=3)

    # arxiv 搜索器将 max_results 作为 API 参数传递, 由服务端截断 (非客户端切片)
    params = client.get.call_args.kwargs["params"]
    assert params["max_results"] == "3"


# ========== HTTP 错误处理 ==========


@pytest.mark.asyncio
async def test_search_http_4xx_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 4xx (非 5xx) 不重试, 直接返回空列表."""
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    response = _make_response(400, text="Bad Request")
    client = _make_mock_client(response)
    searcher = ArxivSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.arxiv.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []
    assert client.get.call_count == 1  # 4xx 不重试


@pytest.mark.asyncio
async def test_search_http_5xx_retries_then_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 5xx 重试, 全部失败后返回空列表."""
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    response = _make_response(503, text="Service Unavailable")
    client = _make_mock_client(response)
    searcher = ArxivSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.arxiv.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []
    # 默认重试 3 次 (_DEFAULT_MAX_RETRIES=3)
    assert client.get.call_count == 3


@pytest.mark.asyncio
async def test_search_http_5xx_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 5xx 重试, 第二次成功返回结果."""
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    success_response = _make_response(200, text=_make_arxiv_xml(title="OK"))
    fail_response = _make_response(503, text="Service Unavailable")
    client = MagicMock()
    client.get = AsyncMock(side_effect=[fail_response, success_response])
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    searcher = ArxivSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.arxiv.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert len(results) == 1
    assert results[0]["title"] == "OK"
    assert client.get.call_count == 2


# ========== 网络异常降级 ==========


@pytest.mark.asyncio
async def test_search_network_exception_retries_then_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """网络异常 (httpx.RequestError) 重试, 耗尽后返回空列表."""
    import httpx

    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    client = MagicMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("network down"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    searcher = ArxivSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.arxiv.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []
    assert client.get.call_count == 3  # 默认 3 次重试


@pytest.mark.asyncio
async def test_search_timeout_exception_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超时 (httpx.TimeoutException) 重试耗尽后返回空列表."""
    import httpx

    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    client = MagicMock()
    client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    searcher = ArxivSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.arxiv.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


# ========== query_domains 后置过滤 ==========


@pytest.mark.asyncio
async def test_search_query_domains_filter() -> None:
    """query_domains 后置过滤: 仅保留 url 含白名单域名的结果."""
    xml = _make_arxiv_xml_multi([
        {"title": "arxiv1", "summary": "s", "id": "http://arxiv.org/abs/1"},
        {"title": "other", "summary": "s", "id": "http://example.com/x"},
    ])
    response = _make_response(200, text=xml)
    client = _make_mock_client(response)
    searcher = ArxivSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.arxiv.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test", query_domains=["arxiv.org"])

    assert len(results) == 1
    assert "arxiv.org" in results[0]["url"]
