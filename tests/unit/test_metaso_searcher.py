"""单元测试: MetasoSearcher 秘塔 AI 搜索 (验证 METASO 修复).

验证 src/skills/researcher/searchers/metaso.py 的任务3 修复:
- payload 构造: scope="webpage", size=str(max_results), includeSummary=True
- headers 含 Accept: application/json (修复前缺失导致 API 拒绝)
- 响应解析: {"result": {"webpages": [...]}} 与 {"webpages": [...]} 双结构兼容
- HTTP 429/402 额度已满抛 QuotaExceededError
- api_key 未配置返回空列表

AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务, 全部 mock httpx.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings
from src.skills.researcher.searchers.exceptions import QuotaExceededError
from src.skills.researcher.searchers.metaso import MetasoSearcher

pytestmark = pytest.mark.unit


# ========== 辅助函数 ==========


def _make_settings(api_key: str | None = "test-metaso-key") -> Settings:
    """构造带 metaso_api_key 的 Settings (隔离 .env)."""
    return Settings(_env_file=None, metaso_api_key=api_key)


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


def _make_searcher(
    api_key: str | None = "test-metaso-key",
    response: MagicMock | None = None,
) -> MetasoSearcher:
    """构造 MetasoSearcher 并注入 mock httpx 客户端.

    Args:
        api_key: 秘塔 API Key, None 表示未配置
        response: mock 响应对象, None 时默认 200 空响应
    """
    settings = _make_settings(api_key)
    searcher = MetasoSearcher(settings)
    # 替换 httpx.AsyncClient 为 mock, 避免真实网络调用
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=response or _make_response(200, {}))
    searcher._client = mock_client
    return searcher


# ========== api_key 未配置 ==========


@pytest.mark.asyncio
async def test_search_no_api_key_returns_empty_list() -> None:
    """api_key 未配置时返回空列表, 不调用 HTTP."""
    searcher = _make_searcher(api_key=None)

    results = await searcher.search("测试查询")

    assert results == []
    # 未配置 key 时不应发起 HTTP 请求
    searcher._client.post.assert_not_called()


# ========== payload 构造 (任务3 修复核心) ==========


@pytest.mark.asyncio
async def test_search_payload_contains_scope_webpage() -> None:
    """payload 必须含 scope="webpage" (修复前缺失, 导致返回非网页数据)."""
    searcher = _make_searcher(response=_make_response(200, {"result": {"webpages": []}}))

    await searcher.search("AI 行业研究")

    call_kwargs = searcher._client.post.call_args.kwargs
    payload = call_kwargs["json"]
    assert payload["scope"] == "webpage"


@pytest.mark.asyncio
async def test_search_payload_size_is_string_type() -> None:
    """payload 的 size 必须为字符串类型 (秘塔 API 要求, 修复前用 int 被拒绝)."""
    searcher = _make_searcher(response=_make_response(200, {"result": {"webpages": []}}))

    await searcher.search("测试", max_results=8)

    call_kwargs = searcher._client.post.call_args.kwargs
    payload = call_kwargs["json"]
    assert payload["size"] == "8"  # str(max_results), 不是 int 8
    assert isinstance(payload["size"], str)


@pytest.mark.asyncio
async def test_search_payload_size_reflects_max_results() -> None:
    """size 字段应反映 max_results 参数 (字符串形式)."""
    searcher = _make_searcher(response=_make_response(200, {"result": {"webpages": []}}))

    await searcher.search("测试", max_results=15)

    payload = searcher._client.post.call_args.kwargs["json"]
    assert payload["size"] == "15"


@pytest.mark.asyncio
async def test_search_payload_contains_include_summary_true() -> None:
    """payload 必须含 includeSummary=True (提升结果质量, 返回 summary 字段)."""
    searcher = _make_searcher(response=_make_response(200, {"result": {"webpages": []}}))

    await searcher.search("新能源车")

    payload = searcher._client.post.call_args.kwargs["json"]
    assert payload["includeSummary"] is True


@pytest.mark.asyncio
async def test_search_payload_contains_query_field() -> None:
    """payload 的 q 字段应为查询字符串."""
    searcher = _make_searcher(response=_make_response(200, {"result": {"webpages": []}}))

    await searcher.search("大模型行业")

    payload = searcher._client.post.call_args.kwargs["json"]
    assert payload["q"] == "大模型行业"


# ========== headers 构造 (任务3 修复核心) ==========


@pytest.mark.asyncio
async def test_search_headers_contains_accept_json() -> None:
    """headers 必须含 Accept: application/json (修复前缺失, 导致 API 拒绝)."""
    searcher = _make_searcher(response=_make_response(200, {"result": {"webpages": []}}))

    await searcher.search("测试")

    call_kwargs = searcher._client.post.call_args.kwargs
    headers = call_kwargs["headers"]
    assert headers["Accept"] == "application/json"


@pytest.mark.asyncio
async def test_search_headers_contains_authorization_bearer() -> None:
    """headers 含 Authorization: Bearer <api_key>."""
    searcher = _make_searcher(response=_make_response(200, {"result": {"webpages": []}}))

    await searcher.search("测试")

    headers = searcher._client.post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer test-metaso-key"


@pytest.mark.asyncio
async def test_search_headers_contains_content_type_json() -> None:
    """headers 含 Content-Type: application/json."""
    searcher = _make_searcher(response=_make_response(200, {"result": {"webpages": []}}))

    await searcher.search("测试")

    headers = searcher._client.post.call_args.kwargs["headers"]
    assert headers["Content-Type"] == "application/json"


# ========== 响应解析: {"result": {"webpages": [...]}} 结构 ==========


@pytest.mark.asyncio
async def test_search_parses_result_webpages_structure() -> None:
    """解析 {"result": {"webpages": [...]}} 结构 (秘塔标准响应)."""
    webpages = [
        {"title": "AI 行业报告", "url": "https://example.com/1", "snippet": "摘要1"},
        {"title": "大模型研究", "url": "https://example.com/2", "summary": "摘要2"},
    ]
    response = _make_response(200, {"result": {"webpages": webpages}})
    searcher = _make_searcher(response=response)

    results = await searcher.search("AI 行业")

    assert len(results) == 2
    assert results[0]["title"] == "AI 行业报告"
    assert results[0]["url"] == "https://example.com/1"
    assert results[0]["snippet"] == "摘要1"
    assert results[0]["source"] == "metaso"
    assert results[1]["title"] == "大模型研究"
    assert results[1]["snippet"] == "摘要2"  # summary 字段回退


@pytest.mark.asyncio
async def test_search_parses_result_webpages_with_name_field() -> None:
    """webpages 项用 name 字段作 title 回退."""
    webpages = [{"name": "标题", "url": "https://x.com", "snippet": "摘要"}]
    response = _make_response(200, {"result": {"webpages": webpages}})
    searcher = _make_searcher(response=response)

    results = await searcher.search("测试")

    assert len(results) == 1
    assert results[0]["title"] == "标题"


@pytest.mark.asyncio
async def test_search_parses_result_webpages_with_link_field() -> None:
    """webpages 项用 link 字段作 url 回退."""
    webpages = [{"title": "标题", "link": "https://link-field.com", "snippet": "摘要"}]
    response = _make_response(200, {"result": {"webpages": webpages}})
    searcher = _make_searcher(response=response)

    results = await searcher.search("测试")

    assert len(results) == 1
    assert results[0]["url"] == "https://link-field.com"


# ========== 响应解析: {"webpages": [...]} 裸结构 ==========


@pytest.mark.asyncio
async def test_search_parses_bare_webpages_structure() -> None:
    """解析 {"webpages": [...]} 裸结构 (无 result 包裹)."""
    webpages = [
        {"title": "裸结构标题", "url": "https://bare.com/1", "snippet": "裸摘要"},
    ]
    response = _make_response(200, {"webpages": webpages})
    searcher = _make_searcher(response=response)

    results = await searcher.search("测试")

    assert len(results) == 1
    assert results[0]["title"] == "裸结构标题"
    assert results[0]["url"] == "https://bare.com/1"


@pytest.mark.asyncio
async def test_search_parses_bare_results_structure() -> None:
    """解析 {"results": [...]} 裸结构 (results 字段回退)."""
    items = [{"title": "t", "url": "https://r.com", "snippet": "s"}]
    response = _make_response(200, {"results": items})
    searcher = _make_searcher(response=response)

    results = await searcher.search("测试")

    assert len(results) == 1
    assert results[0]["url"] == "https://r.com"


@pytest.mark.asyncio
async def test_search_parses_bare_data_structure() -> None:
    """解析 {"data": [...]} 裸结构 (data 字段回退)."""
    items = [{"title": "t", "url": "https://d.com", "snippet": "s"}]
    response = _make_response(200, {"data": items})
    searcher = _make_searcher(response=response)

    results = await searcher.search("测试")

    assert len(results) == 1
    assert results[0]["url"] == "https://d.com"


# ========== 结果归一化与截断 ==========


@pytest.mark.asyncio
async def test_search_truncates_to_max_results() -> None:
    """结果数超过 max_results 时截断."""
    webpages = [
        {"title": f"标题{i}", "url": f"https://x.com/{i}", "snippet": "s"} for i in range(10)
    ]
    response = _make_response(200, {"result": {"webpages": webpages}})
    searcher = _make_searcher(response=response)

    results = await searcher.search("测试", max_results=3)

    assert len(results) == 3


@pytest.mark.asyncio
async def test_search_skips_items_without_url() -> None:
    """缺少 url 的项被跳过."""
    webpages = [
        {"title": "有url", "url": "https://x.com", "snippet": "s"},
        {"title": "无url", "snippet": "s"},  # 无 url, 应跳过
    ]
    response = _make_response(200, {"result": {"webpages": webpages}})
    searcher = _make_searcher(response=response)

    results = await searcher.search("测试")

    assert len(results) == 1
    assert results[0]["title"] == "有url"


@pytest.mark.asyncio
async def test_search_normalizes_result_fields() -> None:
    """返回结果含 5 个固定字段: title/url/snippet/source/region."""
    webpages = [{"title": "t", "url": "https://x.com", "snippet": "s"}]
    response = _make_response(200, {"result": {"webpages": webpages}})
    searcher = _make_searcher(response=response)

    results = await searcher.search("测试")

    assert len(results) == 1
    assert set(results[0].keys()) == {"title", "url", "snippet", "source", "region"}
    assert results[0]["source"] == "metaso"
    assert results[0]["region"] == "cn"  # SearchRegion.CN.value


# ========== 额度已满 (HTTP 429/402) ==========


@pytest.mark.asyncio
async def test_search_http_429_raises_quota_exceeded_error() -> None:
    """HTTP 429 抛 QuotaExceededError (触发额度缓存机制)."""
    response = _make_response(429, text="Rate limited", headers={})
    searcher = _make_searcher(response=response)

    with pytest.raises(QuotaExceededError) as exc_info:
        await searcher.search("测试")

    assert exc_info.value.engine == "metaso"
    assert "429" in exc_info.value.message


@pytest.mark.asyncio
async def test_search_http_402_raises_quota_exceeded_error() -> None:
    """HTTP 402 (付费额度已满) 同样抛 QuotaExceededError."""
    response = _make_response(402, text="Payment required", headers={})
    searcher = _make_searcher(response=response)

    with pytest.raises(QuotaExceededError) as exc_info:
        await searcher.search("测试")

    assert exc_info.value.engine == "metaso"


@pytest.mark.asyncio
async def test_search_quota_reset_uses_retry_after_header() -> None:
    """额度重置时间优先读取 Retry-After 头 (秒数)."""
    response = _make_response(429, text="limited", headers={"Retry-After": "3600"})
    searcher = _make_searcher(response=response)

    with pytest.raises(QuotaExceededError) as exc_info:
        await searcher.search("测试")

    # Retry-After=3600s, reset_at 应在现在之后约 1 小时
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    delta = (exc_info.value.reset_at - now).total_seconds()
    # 允许 10s 误差 (测试执行时间)
    assert 3500 < delta < 3700


# ========== 其他 HTTP 错误 ==========


@pytest.mark.asyncio
async def test_search_http_500_returns_empty_list() -> None:
    """HTTP 500 (非 429/402) 返回空列表, 不抛异常."""
    response = _make_response(500, text="Internal Server Error")
    searcher = _make_searcher(response=response)

    results = await searcher.search("测试")

    assert results == []


@pytest.mark.asyncio
async def test_search_http_403_returns_empty_list() -> None:
    """HTTP 403 (鉴权失败) 返回空列表, 不抛异常."""
    response = _make_response(403, text="Forbidden")
    searcher = _make_searcher(response=response)

    results = await searcher.search("测试")

    assert results == []


@pytest.mark.asyncio
async def test_search_json_parse_failure_returns_empty_list() -> None:
    """响应 JSON 解析失败时返回空列表, 不抛异常."""
    response = _make_response(200, text="not json")
    # json() 抛异常模拟解析失败
    response.json.side_effect = ValueError("invalid json")
    searcher = _make_searcher(response=response)

    results = await searcher.search("测试")

    assert results == []


# ========== query_domains 后置过滤 ==========


@pytest.mark.asyncio
async def test_search_query_domains_filter() -> None:
    """query_domains 后置过滤: 仅保留 url 含白名单域名的结果."""
    webpages = [
        {"title": "arxiv", "url": "https://arxiv.org/abs/1", "snippet": "s"},
        {"title": "other", "url": "https://example.com/x", "snippet": "s"},
        {"title": "nature", "url": "https://nature.com/articles/2", "snippet": "s"},
    ]
    response = _make_response(200, {"result": {"webpages": webpages}})
    searcher = _make_searcher(response=response)

    results = await searcher.search("测试", query_domains=["arxiv.org", "nature.com"])

    assert len(results) == 2
    assert results[0]["url"] == "https://arxiv.org/abs/1"
    assert results[1]["url"] == "https://nature.com/articles/2"


# ========== 网络异常降级 ==========


@pytest.mark.asyncio
async def test_search_network_exception_returns_empty_list() -> None:
    """httpx.post 抛异常时返回空列表, 不向调用方抛异常."""
    searcher = _make_searcher()
    searcher._client.post = AsyncMock(side_effect=ConnectionError("network down"))

    results = await searcher.search("测试")

    assert results == []
