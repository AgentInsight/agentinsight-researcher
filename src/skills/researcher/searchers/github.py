"""GitHub 代码搜索 (api.github.com, v1.1 新增).

代码搜索权威, 30 req/min (认证) / 60 req/h (未认证).
- cost_tier: freemium (免费配额充足, Token 提高配额)
- quality_score: 80.0
- region: GLOBAL
- API: https://api.github.com/search/repositories  或 /search/code
- 配置: GITHUB_TOKEN (可选, 提高配额)
- Token 获取: https://github.com/settings/tokens

额度已满时抛出 QuotaExceededError 触发缓存机制.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from src.config.settings import Settings
from src.skills.researcher.searchers import BaseSearcher, SearchRegion, register_searcher
from src.skills.researcher.searchers.exceptions import QuotaExceededError

logger = logging.getLogger(__name__)


@register_searcher("github")
class GitHubSearcher(BaseSearcher):
    """GitHub 代码搜索器.

    30 req/min (认证) / 60 req/h (未认证). 额度已满时抛 QuotaExceededError.
    """

    name = "github"
    region = SearchRegion.GLOBAL
    cost_tier = "freemium"
    quality_score = 80.0

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://api.github.com/search/repositories"
        # P0-2: 字段已在 Settings 中声明, 直接访问 (消除 getattr 防御式编程)
        self.token = settings.github_token or None

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """调用 GitHub Search API."""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        params: dict[str, str | int] = {
            "q": query,
            "per_page": min(max_results, 30),
            "sort": "stars",
            "order": "desc",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(self.base_url, headers=headers, params=params)
        except Exception as e:
            logger.warning(f"github 调用失败: {e}")
            return []

        # v1.1: 额度已满检测 (GitHub 返回 403 + X-RateLimit-Remaining: 0)
        if resp.status_code == 403:
            remaining = resp.headers.get("X-RateLimit-Remaining", "")
            if remaining == "0":
                reset_at = self._calc_quota_reset(resp)
                raise QuotaExceededError(
                    engine="github",
                    reset_at=reset_at,
                    message="GitHub API 配额已满",
                )

        if resp.status_code != 200:
            logger.warning(f"github HTTP {resp.status_code}: {resp.text[:200]}")
            return []

        try:
            data = resp.json()
        except Exception as e:
            logger.warning(f"github JSON 解析失败: {e}")
            return []

        results: list[dict[str, Any]] = []
        for item in (data.get("items") or [])[:max_results]:
            title = item.get("full_name") or item.get("name") or ""
            url = item.get("html_url") or ""
            description = item.get("description") or ""
            stars = item.get("stargazers_count", 0)
            snippet = f"⭐ {stars} - {description}"
            if url:
                results.append(self._normalize_result(title, url, snippet))

        return self._filter_by_domains(results, query_domains)

    def _calc_quota_reset(self, resp: httpx.Response) -> datetime:
        """额度重置时间: 优先 X-RateLimit-Reset 头 (Unix 时间戳), 默认 1 小时."""
        reset_ts = resp.headers.get("X-RateLimit-Reset")
        if reset_ts and reset_ts.isdigit():
            return datetime.fromtimestamp(int(reset_ts), tz=UTC)
        return datetime.now(UTC) + timedelta(hours=1)
