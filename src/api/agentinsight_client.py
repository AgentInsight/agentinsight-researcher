"""AgentInsight Service API 客户端.

SELF_HOST=False 时复用 AgentInsightService 的点数校验/扣除 API.
AGENTS.md 第 8 章: JWT 验证与 user_id 获取在 API 入口中间件完成.

对标: D:\\Projects\\Entrepreneurship\\AIProjects\\AgentInsightService\\Agents\\common\\api_client.py

AgentType 枚举 (对标 AgentInsightService Models/Common/Enums/PaymentEnums.cs):
- Research = 2: 研究型 Agent, 校验/扣除 MonthlyResearchRate, 服务端从 JWT Token
  解析 UserId → 默认 OrgId, 请求不传 orgId/projectId

注: 本项目仅调用 type=2 (Research), 不存在其他类型调用.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.settings import Settings, get_settings
from src.observability.tracing import trace_tool

logger = logging.getLogger(__name__)

# AgentType 枚举常量 (对标 AgentInsightService AgentType enum)
# Research = 2: 研究型 (本项目唯一使用类型)
_AGENT_TYPE_RESEARCH: int = 2


class AgentInsightClient:
    """AgentInsight Service API 客户端 (点数校验/扣除).

    仅在 SELF_HOST=False 时启用.
    失败降级策略: fail_open=True 时 API 失败放行 (与 AgentInsightService Python Agents 一致).

    所有调用均使用 type=2 (Research), 服务端从 JWT Token 解析 UserId → 默认 OrgId.
    """

    settings: Settings
    _client: httpx.AsyncClient

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = httpx.AsyncClient(
            timeout=self.settings.agent_privilege_api_timeout,
        )

    async def validate_agent_usage(self, token: str) -> tuple[bool, str | None]:
        """校验 Research Agent 月度配额是否超限.

        服务端从 JWT Token 解析 UserId → 默认 OrgId, 请求仅传 type=2.

        Args:
            token: JWT 令牌

        Returns:
            (exceeded, error_message)
            - exceeded=True: 已超限, 应拒绝
            - exceeded=False: 可使用
            - API 失败时: 按 fail_open 策略, fail_open=True 返回 (False, None),
              fail_open=False 返回 (True, error)
        """
        async with trace_tool(
            name="agentinsight-validate",
            input={"agent_type": _AGENT_TYPE_RESEARCH},
            metadata={"api": "validate"},
        ) as span:
            try:
                params: dict[str, Any] = {"type": _AGENT_TYPE_RESEARCH}

                resp = await self._client.get(
                    f"{self.settings.agent_privilege_api_base_url}"
                    f"{self.settings.agent_privilege_validate_path}",
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                data = resp.json()

                # ApiResponse<bool>: Data[0] == true 表示已超限
                api_data = data.get("Data") or []
                exceeded = bool(api_data[0]) if api_data else False

                span.update(
                    output={"exceeded": exceeded},
                    metadata={"api": "validate", "success": True},
                )
                return exceeded, None
            except Exception as e:  # noqa: BLE001
                logger.warning("AgentInsight validate 调用失败: %s", e)
                span.update(metadata={"api": "validate", "success": False, "error": str(e)})
                if self.settings.agent_privilege_fail_open:
                    return False, None  # 放行
                return True, f"校验失败: {type(e).__name__}"

    async def deduct_agent_usage(self, token: str) -> bool:
        """扣除一次 Research Agent 使用配额.

        服务端从 JWT Token 解析 UserId → 默认 OrgId, 请求仅传 type=2.

        Args:
            token: JWT 令牌

        Returns:
            success: 是否扣除成功
        """
        async with trace_tool(
            name="agentinsight-deduct",
            input={"agent_type": _AGENT_TYPE_RESEARCH},
            metadata={"api": "deduct"},
        ) as span:
            try:
                params: dict[str, Any] = {"type": _AGENT_TYPE_RESEARCH}

                resp = await self._client.get(
                    f"{self.settings.agent_privilege_api_base_url}"
                    f"{self.settings.agent_privilege_deduct_path}",
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()

                span.update(
                    output={"success": True},
                    metadata={"api": "deduct", "success": True},
                )
                return True
            except Exception as e:  # noqa: BLE001
                logger.warning("AgentInsight deduct 调用失败: %s", e)
                span.update(metadata={"api": "deduct", "success": False, "error": str(e)})
                return False

    async def close(self) -> None:
        await self._client.aclose()


# 全局单例
_client: AgentInsightClient | None = None


def get_agentinsight_client() -> AgentInsightClient:
    """获取全局 AgentInsightClient 单例."""
    global _client
    if _client is None:
        _client = AgentInsightClient()
    return _client
