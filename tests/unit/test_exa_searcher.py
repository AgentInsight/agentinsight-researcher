"""单元测试: ExaSearcher Exa 搜索引擎 (验证 P1 timeout 优化).

验证 src/skills/researcher/searchers/exa.py:
- timeout=10.0 配置 (P1 优化: 15s→10s, 消除 >10s 离群点, trace 4ad14970)
- api_key 未配置时返回空列表, 不发起 HTTP 请求
- 请求构造: headers (Authorization Bearer / Content-Type) + payload (query/num_results/use_autoprompt/contents)
- 响应解析: 正常 results 结构 / 空响应 / 截断到 max_results
- HTTP 429 抛 QuotaExceededError (含额度重置时间)
- 其他 HTTP 错误 (500/403) 与网络异常降级返回空列表

AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务, 全部 mock httpx.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings
from src.skills.researcher.searchers import SearchRegion
from src.skills.researcher.searchers.exa import ExaSearcher
from src.skills.researcher.searchers.exceptions import QuotaExceededError

pytestmark = pytest.mark.unit


# ========== 辅助函数 ==========


def _make_settings(api_key: str | None = "test-exa-key") -> Settings:
    """构造带 exa_api_key 的 Settings (隔离 .env)."""
    return Settings(_env_file=None, exa_api_key=api_key)


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
    api_key: str | None = "test-exa-key",
    response: MagicMock | None = None,
) -> ExaSearcher:
    """构造 ExaSearcher 并注入 mock httpx 客户端.

    Args:
        api_key: Exa API Key, None 表示未配置
        response: mock 响应对象, None 时默认 200 空响应
    """
    settings = _make_settings(api_key)
    searcher = ExaSearcher(settings)
    # 替换 httpx.AsyncClient 为 mock, 避免真实网络调用
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=response or _make_response(200, {}))
    searcher._client = mock_client
    return searcher


# ========== timeout=10.0 配置 (P1 优化: 15s→10s) ==========


def test_init_client_timeout_is_10_seconds() -> None:
    """httpx.AsyncClient timeout 应为 10.0s (P1: 15s→10s, 消除 >10s 离群点)."""
    searcher = ExaSearcher(_make_settings())
    # httpx.AsyncClient(timeout=10.0) 创建 Timeout 对象, 四类超时 (connect/read/write/pool) 均 10.0
    assert searcher._client.timeout.read == pytest.approx(10.0)
    assert searcher._client.timeout.connect == pytest.approx(10.0)


def test_init_client_timeout_not_15_seconds() -> None:
    """timeout 不应为旧值 15s (回归保护: 防止回退到优化前的 15s)."""
    searcher = ExaSearcher(_make_settings())
    assert searcher._client.timeout.read != pytest.approx(15.0)


# ========== 类元数据 ==========


def test_exa_searcher_metadata() -> None:
    """ExaSearcher 元数据: name=exa, region=GLOBAL, cost_tier=paid, quality_score=76."""
    assert ExaSearcher.name == "exa"
    assert ExaSearcher.region == SearchRegion.GLOBAL
    assert ExaSearcher.cost_tier == "paid"
    assert ExaSearcher.quality_score == 76.0


def test_exa_api_url_constant() -> None:
    """_api_url 应为 Exa 官方搜索端点."""
    assert ExaSearcher._api_url == "https://api.exa.ai/search"


# ========== api_key 未配置 ==========


@pytest.mark.asyncio
async def test_search_no_api_key_returns_empty_list() -> None:
    """api_key 未配置时返回空列表, 不调用 HTTP."""
    searcher = _make_searcher(api_key=None)

    results = await searcher.search("测试查询")

    assert results == []
    # 未配置 key 时不应发起 HTTP 请求
    searcher._client.post.assert_not_called()


@pytest.mark.asyncio
async def test_search_no_api_key_does_not_raise() -> None:
    """api_key 未配置时不抛异常, 静默返回空列表."""
    searcher = _make_searcher(api_key=None)

    # 多次调用均应安全返回空列表
    assert await searcher.search("query1") == []
    assert await searcher.search("query2") == []


# ========== 请求构造: headers ==========


@pytest.mark.asyncio
async def test_search_headers_contains_authorization_bearer() -> None:
    """headers 含 Authorization: Bearer <api_key>."""
    searcher = _make_searcher(response=_make_response(200, {"results": []}))

    await searcher.search("AI research")

    headers = searcher._client.post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer test-exa-key"


@pytest.mark.asyncio
async def test_search_headers_contains_content_type_json() -> None:
    """headers 含 Content-Type: application/json."""
    searcher = _make_searcher(response=_make_response(200, {"results": []}))

    await searcher.search("测试")

    headers = searcher._client.post.call_args.kwargs["headers"]
    assert headers["Content-Type"] == "application/json"


# ========== 请求构造: payload ==========


@pytest.mark.asyncio
async def test_search_payload_contains_query_field() -> None:
    """payload 的 query 字段应为查询字符串."""
    searcher = _make_searcher(response=_make_response(200, {"results": []}))

    await searcher.search("大模型行业")

    payload = searcher._client.post.call_args.kwargs["json"]
    assert payload["query"] == "大模型行业"


@pytest.mark.asyncio
async def test_search_payload_num_results_reflects_max_results() -> None:
    """payload 的 num_results 字段应反映 max_results 参数."""
    searcher = _make_searcher(response=_make_response(200, {"results": []}))

    await searcher.search("测试", max_results=8)

    payload = searcher._client.post.call_args.kwargs["json"]
    assert payload["num_results"] == 8


@pytest.mark.asyncio
async def test_search_payload_contains_use_autoprompt_true() -> None:
    """payload 含 use_autoprompt=True (Exa 自动优化查询)."""
    searcher = _make_searcher(response=_make_response(200, {"results": []}))

    await searcher.search("新能源车")

    payload = searcher._client.post.call_args.kwargs["json"]
    assert payload["use_autoprompt"] is True


@pytest.mark.asyncio
async def test_search_payload_contains_contents_text_max_characters() -> None:
    """payload 含 contents.text.maxCharacters=1000 (限制返回文本长度)."""
    searcher = _make_searcher(response=_make_response(200, {"results": []}))

    await searcher.search("测试")

    payload = searcher._client.post.call_args.kwargs["json"]
    assert payload["contents"]["text"]["maxCharacters"] == 1000


@pytest.mark.asyncio
async def test_search_posts_to_api_url() -> None:
    """POST 请求应发往 Exa 官方搜索端点."""
    searcher = _make_searcher(response=_make_response(200, {"results": []}))

    await searcher.search("测试")

    call_args = searcher._client.post.call_args
    assert call_args.args[0] == "https://api.exa.ai/search"


# ========== 响应解析: 正常结构 ==========


@pytest.mark.asyncio
async def test_search_parses_results_structure() -> None:
    """解析 {"results": [{"title","url","text"}]} 结构 (Exa 标准响应)."""
    items = [
        {"title": "AI 行业报告", "url": "https://example.com/1", "text": "摘要内容1"},
        {"title": "大模型研究", "url": "https://example.com/2", "text": "摘要内容2"},
    ]
    response = _make_response(200, {"results": items})
    searcher = _make_searcher(response=response)

    results = await searcher.search("AI 行业")

    assert len(results) == 2
    assert results[0]["title"] == "AI 行业报告"
    assert results[0]["url"] == "https://example.com/1"
    assert results[0]["snippet"] == "摘要内容1"  # text 字段映射到 snippet
    assert results[0]["source"] == "exa"
    assert results[1]["title"] == "大模型研究"
    assert results[1]["snippet"] == "摘要内容2"


@pytest.mark.asyncio
async def test_search_normalizes_result_fields() -> None:
    """返回结果含 5 个固定字段: title/url/snippet/source/region."""
    items = [{"title": "t", "url": "https://x.com", "text": "s"}]
    response = _make_response(200, {"results": items})
    searcher = _make_searcher(response=response)

    results = await searcher.search("测试")

    assert len(results) == 1
    assert set(results[0].keys()) == {"title", "url", "snippet", "source", "region"}
    assert results[0]["source"] == "exa"
    assert results[0]["region"] == "global"  # SearchRegion.GLOBAL.value


@pytest.mark.asyncio
async def test_search_text_field_maps_to_snippet() -> None:
    """Exa 的 text 字段映射到归一化结果的 snippet 字段."""
    items = [{"title": "标题", "url": "https://x.com", "text": "这是正文内容"}]
    response = _make_response(200, {"results": items})
    searcher = _make_searcher(response=response)

    results = await searcher.search("测试")

    assert results[0]["snippet"] == "这是正文内容"


@pytest.mark.asyncio
async def test_search_missing_fields_default_to_empty_string() -> None:
    """结果项缺少 title/url/text 字段时回退为空字符串."""
    items = [{"title": "只有标题"}]  # 缺 url / text
    response = _make_response(200, {"results": items})
    searcher = _make_searcher(response=response)

    results = await searcher.search("测试")

    assert len(results) == 1
    assert results[0]["title"] == "只有标题"
    assert results[0]["url"] == ""
    assert results[0]["snippet"] == ""


# ========== 响应解析: 空响应 ==========


@pytest.mark.asyncio
async def test_search_empty_results_returns_empty_list() -> None:
    """响应 results 为空数组时返回空列表."""
    response = _make_response(200, {"results": []})
    searcher = _make_searcher(response=response)

    results = await searcher.search("无结果查询")

    assert results == []


@pytest.mark.asyncio
async def test_search_missing_results_key_returns_empty_list() -> None:
    """响应缺少 results 字段时返回空列表 (data.get('results', []) 兜底)."""
    response = _make_response(200, {"other_field": "value"})
    searcher = _make_searcher(response=response)

    results = await searcher.search("测试")

    assert results == []


# ========== 结果截断 ==========


@pytest.mark.asyncio
async def test_search_truncates_to_max_results() -> None:
    """结果数超过 max_results 时截断 ([:max_results] 切片)."""
    items = [{"title": f"标题{i}", "url": f"https://x.com/{i}", "text": "s"} for i in range(10)]
    response = _make_response(200, {"results": items})
    searcher = _make_searcher(response=response)

    results = await searcher.search("测试", max_results=3)

    assert len(results) == 3


# ========== 额度已满 (HTTP 429) ==========


@pytest.mark.asyncio
async def test_search_http_429_raises_quota_exceeded_error() -> None:
    """HTTP 429 抛 QuotaExceededError (engine=exa)."""
    response = _make_response(429, text="Rate limited", headers={})
    searcher = _make_searcher(response=response)

    with pytest.raises(QuotaExceededError) as exc_info:
        await searcher.search("测试")

    assert exc_info.value.engine == "exa"


@pytest.mark.asyncio
async def test_search_http_429_message_is_exa_quota_exceeded() -> None:
    """429 异常 message 应为 'Exa 月度额度已满'."""
    response = _make_response(429, text="limited", headers={})
    searcher = _make_searcher(response=response)

    with pytest.raises(QuotaExceededError) as exc_info:
        await searcher.search("测试")

    assert "额度已满" in exc_info.value.message


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


@pytest.mark.asyncio
async def test_search_quota_reset_defaults_to_next_month_without_retry_after() -> None:
    """无 Retry-After 头时, 额度重置时间默认为次月 1 日."""
    response = _make_response(429, text="limited", headers={})
    searcher = _make_searcher(response=response)

    with pytest.raises(QuotaExceededError) as exc_info:
        await searcher.search("测试")

    from datetime import UTC, datetime

    now = datetime.now(UTC)
    reset_at = exc_info.value.reset_at
    # reset_at 应在现在之后 (次月 1 日)
    assert reset_at > now
    # reset_at 应为某月 1 日 00:00:00
    assert reset_at.day == 1
    assert reset_at.hour == 0
    assert reset_at.minute == 0
    assert reset_at.second == 0


# ========== 其他 HTTP 错误降级 ==========


@pytest.mark.asyncio
async def test_search_http_500_returns_empty_list() -> None:
    """HTTP 500 (非 429) 返回空列表, 不抛异常 (raise_for_status 被捕获)."""
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
    items = [
        {"title": "arxiv", "url": "https://arxiv.org/abs/1", "text": "s"},
        {"title": "other", "url": "https://example.com/x", "text": "s"},
        {"title": "nature", "url": "https://nature.com/articles/2", "text": "s"},
    ]
    response = _make_response(200, {"results": items})
    searcher = _make_searcher(response=response)

    results = await searcher.search("测试", query_domains=["arxiv.org", "nature.com"])

    assert len(results) == 2
    assert results[0]["url"] == "https://arxiv.org/abs/1"
    assert results[1]["url"] == "https://nature.com/articles/2"


@pytest.mark.asyncio
async def test_search_query_domains_none_returns_all() -> None:
    """query_domains=None 时不过滤, 返回全部结果."""
    items = [
        {"title": "a", "url": "https://a.com", "text": "s"},
        {"title": "b", "url": "https://b.com", "text": "s"},
    ]
    response = _make_response(200, {"results": items})
    searcher = _make_searcher(response=response)

    results = await searcher.search("测试", query_domains=None)

    assert len(results) == 2


# ========== 网络异常降级 ==========


@pytest.mark.asyncio
async def test_search_network_exception_returns_empty_list() -> None:
    """httpx.post 抛异常时返回空列表, 不向调用方抛异常."""
    searcher = _make_searcher()
    searcher._client.post = AsyncMock(side_effect=ConnectionError("network down"))

    results = await searcher.search("测试")

    assert results == []


@pytest.mark.asyncio
async def test_search_timeout_exception_returns_empty_list() -> None:
    """请求超时 (httpx.TimeoutException) 时返回空列表, 不抛异常."""
    import httpx

    searcher = _make_searcher()
    searcher._client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

    results = await searcher.search("测试")

    assert results == []


# ========== 空文档列表 ==========


@pytest.mark.asyncio
async def test_search_empty_documents_returns_empty() -> None:
    """空响应不抛异常, 返回空列表."""
    response = _make_response(200, {"results": []})
    searcher = _make_searcher(response=response)

    results = await searcher.search("")

    assert results == []
