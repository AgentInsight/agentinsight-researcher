"""单元测试: BraveSearcher Brave Search API 搜索.

验证 src/skills/researcher/searchers/brave_searcher.py:
- 需 BRAVE_API_KEY, 未配置返回空列表
- 请求构造: headers (X-Subscription-Token / Accept) + params (q/count)
- 响应解析: {"web": {"results": [{"title","url","description"}]}} 结构
- HTTP 错误/网络异常降级返回空列表
- query_domains 后置过滤

单元测试在构建期执行, 不依赖外部服务, 全部 mock httpx.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings
from src.skills.researcher.searchers import SearchRegion
from src.skills.researcher.searchers.brave_searcher import BraveSearcher

pytestmark = pytest.mark.unit


# ========== 辅助函数 ==========


def _make_settings(api_key: str | None = "test-brave-key") -> Settings:
    """构造带 brave_api_key 的 Settings (隔离 .env)."""
    return Settings(_env_file=None, brave_api_key=api_key)


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


def _make_searcher(
    api_key: str | None = "test-brave-key",
    response: MagicMock | None = None,
) -> BraveSearcher:
    """构造 BraveSearcher 并注入 mock httpx 客户端."""
    settings = _make_settings(api_key)
    searcher = BraveSearcher(settings)
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=response or _make_response(200, {}))
    searcher._client = mock_client
    return searcher


# ========== 类元数据 ==========


def test_brave_searcher_metadata() -> None:
    """BraveSearcher 元数据."""
    assert BraveSearcher.name == "brave"
    assert BraveSearcher.region == SearchRegion.GLOBAL
    assert BraveSearcher.cost_tier == "paid"
    assert BraveSearcher.quality_score == 76.1


def test_brave_api_url() -> None:
    """API URL 应为 Brave Search 端点."""
    assert BraveSearcher._api_url == "https://api.search.brave.com/res/v1/web/search"


# ========== API Key 未配置 ==========


@pytest.mark.asyncio
async def test_search_no_api_key_returns_empty() -> None:
    """api_key 未配置时返回空列表, 不调用 HTTP."""
    searcher = _make_searcher(api_key=None)
    assert await searcher.search("test") == []
    searcher._client.get.assert_not_called()


# ========== 请求构造 ==========


@pytest.mark.asyncio
async def test_search_headers_x_subscription_token() -> None:
    """headers 含 X-Subscription-Token."""
    searcher = _make_searcher(response=_make_response(200, {"web": {"results": []}}))
    await searcher.search("test")
    headers = searcher._client.get.call_args.kwargs["headers"]
    assert headers["X-Subscription-Token"] == "test-brave-key"


@pytest.mark.asyncio
async def test_search_headers_accept_json() -> None:
    """headers 含 Accept: application/json."""
    searcher = _make_searcher(response=_make_response(200, {"web": {"results": []}}))
    await searcher.search("test")
    headers = searcher._client.get.call_args.kwargs["headers"]
    assert headers["Accept"] == "application/json"


@pytest.mark.asyncio
async def test_search_params_q_and_count() -> None:
    """params 含 q=query, count=max_results."""
    searcher = _make_searcher(response=_make_response(200, {"web": {"results": []}}))
    await searcher.search("AI", max_results=8)
    params = searcher._client.get.call_args.kwargs["params"]
    assert params["q"] == "AI"
    assert params["count"] == 8


# ========== 响应解析 ==========


@pytest.mark.asyncio
async def test_search_parses_web_results() -> None:
    """解析 {"web": {"results": [{"title","url","description"}]}} 结构."""
    json_data = {
        "web": {
            "results": [
                {"title": "标题1", "url": "https://x.com/1", "description": "描述1"},
                {"title": "标题2", "url": "https://x.com/2", "description": "描述2"},
            ]
        }
    }
    searcher = _make_searcher(response=_make_response(200, json_data))
    results = await searcher.search("AI")

    assert len(results) == 2
    assert results[0]["title"] == "标题1"
    assert results[0]["url"] == "https://x.com/1"
    assert results[0]["snippet"] == "描述1"  # description 映射到 snippet
    assert results[0]["source"] == "brave"
    assert results[0]["region"] == "global"


@pytest.mark.asyncio
async def test_search_normalizes_result_fields() -> None:
    """返回结果含 5 个固定字段."""
    json_data = {"web": {"results": [{"title": "t", "url": "https://x.com", "description": "s"}]}}
    searcher = _make_searcher(response=_make_response(200, json_data))
    results = await searcher.search("test")
    assert set(results[0].keys()) == {"title", "url", "snippet", "source", "region"}


@pytest.mark.asyncio
async def test_search_empty_results() -> None:
    """空 web.results 返回空列表."""
    searcher = _make_searcher(response=_make_response(200, {"web": {"results": []}}))
    assert await searcher.search("无结果") == []


@pytest.mark.asyncio
async def test_search_truncates_to_max_results() -> None:
    """结果数超过 max_results 时截断."""
    items = [
        {"title": f"t{i}", "url": f"https://x.com/{i}", "description": "s"} for i in range(10)
    ]
    searcher = _make_searcher(response=_make_response(200, {"web": {"results": items}}))
    assert len(await searcher.search("test", max_results=3)) == 3


# ========== HTTP 错误降级 ==========


@pytest.mark.asyncio
async def test_search_http_500_returns_empty() -> None:
    """HTTP 500 返回空列表."""
    searcher = _make_searcher(response=_make_response(500, text="Error"))
    assert await searcher.search("test") == []


@pytest.mark.asyncio
async def test_search_http_403_returns_empty() -> None:
    """HTTP 403 返回空列表."""
    searcher = _make_searcher(response=_make_response(403, text="Forbidden"))
    assert await searcher.search("test") == []


# ========== 网络异常降级 ==========


@pytest.mark.asyncio
async def test_search_network_exception_returns_empty() -> None:
    """网络异常返回空列表."""
    searcher = _make_searcher()
    searcher._client.get = AsyncMock(side_effect=ConnectionError("down"))
    assert await searcher.search("test") == []


# ========== query_domains 后置过滤 ==========


@pytest.mark.asyncio
async def test_search_query_domains_filter() -> None:
    """query_domains 后置过滤."""
    json_data = {
        "web": {
            "results": [
                {"title": "a", "url": "https://arxiv.org/1", "description": "s"},
                {"title": "b", "url": "https://other.com/2", "description": "s"},
            ]
        }
    }
    searcher = _make_searcher(response=_make_response(200, json_data))
    results = await searcher.search("test", query_domains=["arxiv.org"])
    assert len(results) == 1
    assert "arxiv.org" in results[0]["url"]
