"""单元测试: BochaSearcher 博查搜索.

验证 src/skills/researcher/searchers/bocha.py:
- 需 BOCHA_API_KEY, 未配置返回空列表
- 请求构造: headers (Authorization Bearer / Content-Type) + payload (query/freshness/summary/count)
- 响应解析: {"data": {"webPages": {"value": [...]}}} 结构
- HTTP 429 抛 QuotaExceededError (engine=bocha)
- 其他 HTTP 错误/网络异常降级返回空列表
- query_domains 后置过滤

单元测试在构建期执行, 不依赖外部服务, 全部 mock httpx.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings
from src.skills.researcher.searchers import SearchRegion
from src.skills.researcher.searchers.bocha import BochaSearcher
from src.skills.researcher.searchers.exceptions import QuotaExceededError

pytestmark = pytest.mark.unit


# ========== 辅助函数 ==========


def _make_settings(api_key: str | None = "test-bocha-key") -> Settings:
    """构造带 bocha_api_key 的 Settings (隔离 .env)."""
    return Settings(_env_file=None, bocha_api_key=api_key)


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
    api_key: str | None = "test-bocha-key",
    response: MagicMock | None = None,
) -> BochaSearcher:
    """构造 BochaSearcher 并注入 mock httpx 客户端."""
    settings = _make_settings(api_key)
    searcher = BochaSearcher(settings)
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=response or _make_response(200, {}))
    searcher._client = mock_client
    return searcher


# ========== 类元数据 ==========


def test_bocha_searcher_metadata() -> None:
    """BochaSearcher 元数据."""
    assert BochaSearcher.name == "bocha"
    assert BochaSearcher.region == SearchRegion.CN
    assert BochaSearcher.cost_tier == "paid"
    assert BochaSearcher.quality_score == 62.0


def test_bocha_api_url() -> None:
    """API URL 应为博查搜索端点."""
    assert BochaSearcher._api_url == "https://api.bochaai.com/v1/web-search"


# ========== API Key 未配置 ==========


@pytest.mark.asyncio
async def test_search_no_api_key_returns_empty() -> None:
    """api_key 未配置时返回空列表, 不调用 HTTP."""
    searcher = _make_searcher(api_key=None)
    results = await searcher.search("测试")
    assert results == []
    searcher._client.post.assert_not_called()


# ========== 请求构造 ==========


@pytest.mark.asyncio
async def test_search_headers_authorization_bearer() -> None:
    """headers 含 Authorization: Bearer <api_key>."""
    searcher = _make_searcher(response=_make_response(200, {"data": {"webPages": {"value": []}}}))
    await searcher.search("测试")
    headers = searcher._client.post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer test-bocha-key"


@pytest.mark.asyncio
async def test_search_headers_content_type_json() -> None:
    """headers 含 Content-Type: application/json."""
    searcher = _make_searcher(response=_make_response(200, {"data": {"webPages": {"value": []}}}))
    await searcher.search("测试")
    headers = searcher._client.post.call_args.kwargs["headers"]
    assert headers["Content-Type"] == "application/json"


@pytest.mark.asyncio
async def test_search_payload_contains_query() -> None:
    """payload 含 query 字段."""
    searcher = _make_searcher(response=_make_response(200, {"data": {"webPages": {"value": []}}}))
    await searcher.search("AI 行业")
    payload = searcher._client.post.call_args.kwargs["json"]
    assert payload["query"] == "AI 行业"


@pytest.mark.asyncio
async def test_search_payload_contains_freshness_no_limit() -> None:
    """payload 含 freshness=noLimit."""
    searcher = _make_searcher(response=_make_response(200, {"data": {"webPages": {"value": []}}}))
    await searcher.search("测试")
    payload = searcher._client.post.call_args.kwargs["json"]
    assert payload["freshness"] == "noLimit"


@pytest.mark.asyncio
async def test_search_payload_contains_summary_true() -> None:
    """payload 含 summary=True."""
    searcher = _make_searcher(response=_make_response(200, {"data": {"webPages": {"value": []}}}))
    await searcher.search("测试")
    payload = searcher._client.post.call_args.kwargs["json"]
    assert payload["summary"] is True


@pytest.mark.asyncio
async def test_search_payload_count_reflects_max_results() -> None:
    """payload count 字段反映 max_results."""
    searcher = _make_searcher(response=_make_response(200, {"data": {"webPages": {"value": []}}}))
    await searcher.search("测试", max_results=10)
    payload = searcher._client.post.call_args.kwargs["json"]
    assert payload["count"] == 10


# ========== 响应解析 ==========


@pytest.mark.asyncio
async def test_search_parses_webpages_value() -> None:
    """解析 {"data": {"webPages": {"value": [...]}}} 结构."""
    json_data = {
        "data": {
            "webPages": {
                "value": [
                    {"name": "标题1", "url": "https://x.com/1", "snippet": "摘要1"},
                    {"name": "标题2", "url": "https://x.com/2", "summary": "摘要2"},
                ]
            }
        }
    }
    searcher = _make_searcher(response=_make_response(200, json_data))
    results = await searcher.search("AI")

    assert len(results) == 2
    assert results[0]["title"] == "标题1"
    assert results[0]["url"] == "https://x.com/1"
    assert results[0]["snippet"] == "摘要1"
    assert results[0]["source"] == "bocha"
    assert results[0]["region"] == "cn"
    assert results[1]["snippet"] == "摘要2"  # summary 回退


@pytest.mark.asyncio
async def test_search_normalizes_result_fields() -> None:
    """返回结果含 5 个固定字段."""
    json_data = {
        "data": {"webPages": {"value": [{"name": "t", "url": "https://x.com", "snippet": "s"}]}}
    }
    searcher = _make_searcher(response=_make_response(200, json_data))
    results = await searcher.search("test")
    assert set(results[0].keys()) == {"title", "url", "snippet", "source", "region"}


@pytest.mark.asyncio
async def test_search_empty_results() -> None:
    """空 webPages.value 返回空列表."""
    searcher = _make_searcher(response=_make_response(200, {"data": {"webPages": {"value": []}}}))
    results = await searcher.search("无结果")
    assert results == []


@pytest.mark.asyncio
async def test_search_truncates_to_max_results() -> None:
    """结果数超过 max_results 时截断."""
    items = [{"name": f"t{i}", "url": f"https://x.com/{i}", "snippet": "s"} for i in range(10)]
    json_data = {"data": {"webPages": {"value": items}}}
    searcher = _make_searcher(response=_make_response(200, json_data))
    results = await searcher.search("test", max_results=3)
    assert len(results) == 3


# ========== HTTP 429 抛 QuotaExceededError ==========


@pytest.mark.asyncio
async def test_search_http_429_raises_quota_exceeded() -> None:
    """HTTP 429 抛 QuotaExceededError (engine=bocha)."""
    searcher = _make_searcher(response=_make_response(429, text="limited", headers={}))
    with pytest.raises(QuotaExceededError) as exc_info:
        await searcher.search("test")
    assert exc_info.value.engine == "bocha"


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
    """HTTP 500 返回空列表, 不抛异常."""
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
    searcher._client.post = AsyncMock(side_effect=ConnectionError("down"))
    assert await searcher.search("test") == []


# ========== query_domains 后置过滤 ==========


@pytest.mark.asyncio
async def test_search_query_domains_filter() -> None:
    """query_domains 后置过滤."""
    json_data = {
        "data": {
            "webPages": {
                "value": [
                    {"name": "a", "url": "https://arxiv.org/1", "snippet": "s"},
                    {"name": "b", "url": "https://other.com/2", "snippet": "s"},
                ]
            }
        }
    }
    searcher = _make_searcher(response=_make_response(200, json_data))
    results = await searcher.search("test", query_domains=["arxiv.org"])
    assert len(results) == 1
    assert "arxiv.org" in results[0]["url"]
