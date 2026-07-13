"""单元测试: GitHubSearcher GitHub 代码搜索.

验证 src/skills/researcher/searchers/github.py:
- 配置 github_token (可选, 提高 30 req/min 配额)
- 请求构造: headers (Accept/X-GitHub-Api-Version/Authorization) + params (q/per_page/sort/order)
- 403 + X-RateLimit-Remaining: 0 抛 QuotaExceededError (触发额度缓存)
- 响应解析: {"items": [{"full_name","html_url","description","stargazers_count"}]}
- snippet 格式: "⭐ {stars} - {description}"
- _calc_quota_reset: X-RateLimit-Reset 头解析 / 默认 1 小时
- HTTP 错误/JSON 解析失败/网络异常降级返回空列表
- query_domains 后置过滤

单元测试在构建期执行, 不依赖外部服务, 全部 mock httpx.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import Settings
from src.skills.researcher.searchers import SearchRegion
from src.skills.researcher.searchers.exceptions import QuotaExceededError
from src.skills.researcher.searchers.github import GitHubSearcher

pytestmark = pytest.mark.unit


# ========== 辅助函数 ==========


def _make_settings(token: str | None = None) -> Settings:
    """构造带 github_token 的 Settings (隔离 .env)."""
    return Settings(_env_file=None, github_token=token)


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


def test_github_searcher_metadata() -> None:
    """GitHubSearcher 元数据."""
    assert GitHubSearcher.name == "github"
    assert GitHubSearcher.region == SearchRegion.GLOBAL
    assert GitHubSearcher.cost_tier == "freemium"
    assert GitHubSearcher.quality_score == 80.0


def test_github_base_url() -> None:
    """base_url 应为 GitHub search/repositories 端点."""
    searcher = GitHubSearcher(_make_settings())
    assert searcher.base_url == "https://api.github.com/search/repositories"


def test_github_init_token() -> None:
    """构造函数应从 settings 读取 github_token."""
    searcher = GitHubSearcher(_make_settings(token="ghp_xxx"))
    assert searcher.token == "ghp_xxx"


def test_github_init_token_none_when_not_configured() -> None:
    """github_token 未配置时 token 为 None."""
    searcher = GitHubSearcher(_make_settings())
    assert searcher.token is None


# ========== 请求构造 ==========


@pytest.mark.asyncio
async def test_search_headers_without_token() -> None:
    """未配置 token 时, headers 不含 Authorization."""
    response = _make_response(200, {"items": []})
    client = _make_mock_client(response)
    searcher = GitHubSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.github.httpx.AsyncClient", return_value=client):
        await searcher.search("langchain")

    headers = client.get.call_args.kwargs["headers"]
    assert headers["Accept"] == "application/vnd.github+json"
    assert headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert "Authorization" not in headers


@pytest.mark.asyncio
async def test_search_headers_with_token() -> None:
    """配置了 token 时, headers 含 Authorization: Bearer <token>."""
    response = _make_response(200, {"items": []})
    client = _make_mock_client(response)
    searcher = GitHubSearcher(_make_settings(token="ghp_abc"))

    with patch("src.skills.researcher.searchers.github.httpx.AsyncClient", return_value=client):
        await searcher.search("langchain")

    headers = client.get.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer ghp_abc"


@pytest.mark.asyncio
async def test_search_params_constructed_correctly() -> None:
    """params 含 q/per_page/sort=stars/order=desc."""
    response = _make_response(200, {"items": []})
    client = _make_mock_client(response)
    searcher = GitHubSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.github.httpx.AsyncClient", return_value=client):
        await searcher.search("langchain", max_results=10)

    params = client.get.call_args.kwargs["params"]
    assert params["q"] == "langchain"
    assert params["per_page"] == 10
    assert params["sort"] == "stars"
    assert params["order"] == "desc"


@pytest.mark.asyncio
async def test_search_per_page_capped_at_30() -> None:
    """per_page 上限为 30 (GitHub API 单页限制)."""
    response = _make_response(200, {"items": []})
    client = _make_mock_client(response)
    searcher = GitHubSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.github.httpx.AsyncClient", return_value=client):
        await searcher.search("test", max_results=100)

    params = client.get.call_args.kwargs["params"]
    assert params["per_page"] == 30


# ========== 响应解析 ==========


@pytest.mark.asyncio
async def test_search_parses_items() -> None:
    """解析 {"items": [{"full_name","html_url","description","stargazers_count"}]} 结构."""
    json_data = {
        "items": [
            {
                "full_name": "langchain-ai/langgraph",
                "html_url": "https://github.com/langchain-ai/langgraph",
                "description": "Build resilient language agents as graphs.",
                "stargazers_count": 9000,
            }
        ]
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = GitHubSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.github.httpx.AsyncClient", return_value=client):
        results = await searcher.search("langgraph")

    assert len(results) == 1
    assert results[0]["title"] == "langchain-ai/langgraph"
    assert results[0]["url"] == "https://github.com/langchain-ai/langgraph"
    assert "9000" in results[0]["snippet"]
    assert "Build resilient language agents" in results[0]["snippet"]
    assert results[0]["source"] == "github"
    assert results[0]["region"] == "global"


@pytest.mark.asyncio
async def test_search_snippet_format() -> None:
    """snippet 格式为 '⭐ {stars} - {description}'."""
    json_data = {
        "items": [
            {
                "full_name": "a/b",
                "html_url": "https://github.com/a/b",
                "description": "desc",
                "stargazers_count": 42,
            }
        ]
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = GitHubSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.github.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results[0]["snippet"] == "⭐ 42 - desc"


@pytest.mark.asyncio
async def test_search_normalizes_result_fields() -> None:
    """返回结果含 5 个固定字段."""
    json_data = {"items": [{"full_name": "a/b", "html_url": "https://github.com/a/b"}]}
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = GitHubSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.github.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert set(results[0].keys()) == {"title", "url", "snippet", "source", "region"}


@pytest.mark.asyncio
async def test_search_empty_items_returns_empty() -> None:
    """空 items 返回空列表."""
    response = _make_response(200, {"items": []})
    client = _make_mock_client(response)
    searcher = GitHubSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.github.httpx.AsyncClient", return_value=client):
        results = await searcher.search("无结果")

    assert results == []


@pytest.mark.asyncio
async def test_search_truncates_to_max_results() -> None:
    """结果数超过 max_results 时截断."""
    items = [
        {"full_name": f"a/{i}", "html_url": f"https://github.com/a/{i}"} for i in range(10)
    ]
    response = _make_response(200, {"items": items})
    client = _make_mock_client(response)
    searcher = GitHubSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.github.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test", max_results=3)

    assert len(results) == 3


@pytest.mark.asyncio
async def test_search_skips_items_without_html_url() -> None:
    """缺失 html_url 的条目不保留."""
    json_data = {
        "items": [
            {"full_name": "no url"},
            {"full_name": "a/b", "html_url": "https://github.com/a/b"},
        ]
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = GitHubSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.github.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert len(results) == 1
    assert results[0]["url"] == "https://github.com/a/b"


# ========== QuotaExceededError (403 + X-RateLimit-Remaining: 0) ==========


@pytest.mark.asyncio
async def test_search_403_rate_limit_raises_quota_exceeded() -> None:
    """HTTP 403 + X-RateLimit-Remaining: 0 抛 QuotaExceededError."""
    reset_ts = int((datetime.now(UTC) + timedelta(hours=1)).timestamp())
    response = _make_response(
        403,
        text="rate limited",
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset_ts)},
    )
    client = _make_mock_client(response)
    searcher = GitHubSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.github.httpx.AsyncClient", return_value=client):
        with pytest.raises(QuotaExceededError) as exc_info:
            await searcher.search("test")

    assert exc_info.value.engine == "github"
    assert "GitHub API 配额已满" in exc_info.value.message


@pytest.mark.asyncio
async def test_search_403_without_rate_limit_returns_empty() -> None:
    """HTTP 403 但 X-RateLimit-Remaining 非 0 时返回空列表 (不抛异常)."""
    response = _make_response(
        403,
        text="forbidden",
        headers={"X-RateLimit-Remaining": "10"},
    )
    client = _make_mock_client(response)
    searcher = GitHubSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.github.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


@pytest.mark.asyncio
async def test_search_403_missing_rate_limit_header_returns_empty() -> None:
    """HTTP 403 且无 X-RateLimit-Remaining 头时返回空列表."""
    response = _make_response(403, text="forbidden")
    client = _make_mock_client(response)
    searcher = GitHubSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.github.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


# ========== _calc_quota_reset ==========


def test_calc_quota_reset_parses_header() -> None:
    """_calc_quota_reset 解析 X-RateLimit-Reset Unix 时间戳."""
    import httpx

    reset_ts = int((datetime.now(UTC) + timedelta(minutes=30)).timestamp())
    resp = MagicMock(spec=httpx.Response)
    resp.headers = {"X-RateLimit-Reset": str(reset_ts)}
    searcher = GitHubSearcher(_make_settings())

    result = searcher._calc_quota_reset(resp)

    assert result.timestamp() == pytest.approx(float(reset_ts), abs=1.0)
    assert result.tzinfo is not None


def test_calc_quota_reset_missing_header_defaults_1h() -> None:
    """_calc_quota_reset 缺失 X-RateLimit-Reset 头时默认 1 小时后."""
    import httpx

    now = datetime.now(UTC)
    resp = MagicMock(spec=httpx.Response)
    resp.headers = {}
    searcher = GitHubSearcher(_make_settings())

    result = searcher._calc_quota_reset(resp)

    # 默认 1 小时后
    assert result >= now + timedelta(minutes=59)
    assert result <= now + timedelta(minutes=61)


def test_calc_quota_reset_non_digit_header_defaults_1h() -> None:
    """X-RateLimit-Reset 非数字时默认 1 小时后."""
    import httpx

    now = datetime.now(UTC)
    resp = MagicMock(spec=httpx.Response)
    resp.headers = {"X-RateLimit-Reset": "invalid"}
    searcher = GitHubSearcher(_make_settings())

    result = searcher._calc_quota_reset(resp)

    assert result >= now + timedelta(minutes=59)


# ========== HTTP 错误降级 ==========


@pytest.mark.asyncio
async def test_search_http_500_returns_empty() -> None:
    """HTTP 500 返回空列表."""
    response = _make_response(500, text="Error")
    client = _make_mock_client(response)
    searcher = GitHubSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.github.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


@pytest.mark.asyncio
async def test_search_json_parse_failure_returns_empty() -> None:
    """JSON 解析失败返回空列表."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("invalid json")
    resp.text = "not json"
    resp.headers = {}
    client = _make_mock_client(resp)
    searcher = GitHubSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.github.httpx.AsyncClient", return_value=client):
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
    searcher = GitHubSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.github.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test")

    assert results == []


# ========== query_domains 后置过滤 ==========


@pytest.mark.asyncio
async def test_search_query_domains_filter() -> None:
    """query_domains 后置过滤."""
    json_data = {
        "items": [
            {"full_name": "a/b", "html_url": "https://github.com/a/b"},
            {"full_name": "c/d", "html_url": "https://gitlab.com/c/d"},
        ]
    }
    response = _make_response(200, json_data)
    client = _make_mock_client(response)
    searcher = GitHubSearcher(_make_settings())

    with patch("src.skills.researcher.searchers.github.httpx.AsyncClient", return_value=client):
        results = await searcher.search("test", query_domains=["github.com"])

    assert len(results) == 1
    assert "github.com" in results[0]["url"]
