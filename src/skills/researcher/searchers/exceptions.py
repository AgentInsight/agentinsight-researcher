"""搜索引擎异常定义 (v1.1 新增).

QuotaExceededError 用于在引擎返回 HTTP 429 (频率限制) 或 402 (付费额度已满) 时
触发额度缓存机制, 由 QuotaCache 标记该引擎不可用, TTL 根据额度时限自动过期
(最高 24 小时).
"""

from __future__ import annotations

from datetime import datetime


class QuotaExceededError(Exception):
    """额度已满异常, 触发缓存机制.

    Attributes:
        engine: 引擎名称 (如 "metaso", "tavily", "exa")
        reset_at: 额度重置时间 (UTC)
        message: 错误消息
    """

    def __init__(
        self,
        engine: str,
        reset_at: datetime,
        message: str = "",
    ) -> None:
        self.engine = engine
        self.reset_at = reset_at
        self.message = message or f"{engine} 额度已满，将在 {reset_at} 重置"
        super().__init__(self.message)
