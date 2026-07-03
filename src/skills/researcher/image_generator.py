"""图像生成器 (P2-06 报告配图).

对标 GPT Researcher skills/image_generator.py (用 gemini 生成报告配图).
本项目改用 deepseek-v4-flash (用户明确要求, 非 gemini).

AGENTS.md 第 9 章: 全部 LLM/图像调用经 llm/ 网关 (LiteLLM), 禁厂商 SDK 直连.
AGENTS.md 第 10 章: 必须包裹在 trace span 内 (用 trace_chain).
AGENTS.md 第 4 章: 禁用观察者模式.

注意: deepseek-v4-flash 图像生成能力假设支持, 实际能力以官方文档为准.
配置项 image_model 可由用户在 .env 覆盖.
若 LiteLLM aimage_generation 不支持该模型, 业务代码捕获异常降级 (报告不带图).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from src.config.settings import Settings, get_settings
from src.observability.tracing import trace_chain

logger = logging.getLogger(__name__)


class ImageGenerator:
    """图像生成器 (报告配图).

    对标 GPT Researcher skills/image_generator.py.
    用户明确要求: 使用 deepseek-v4-flash 模型 (非 gemini).

    AGENTS.md 第 9 章: 通过 LiteLLM aimage_generation 调用, 禁厂商 SDK 直连.
    """

    settings: Settings

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def _get_api_key(self, model: str) -> str | None:
        """按 LiteLLM 路由前缀获取对应 API Key.

        复用 llm/client.py 的路由逻辑; 优先用 image_api_key (若单独配置),
        否则回退到对应厂商 API Key (deepseek-v4-flash 与 deepseek-chat 共用同一 Key).
        """
        if self.settings.image_api_key:
            return self.settings.image_api_key
        if model.startswith("deepseek/"):
            return self.settings.deepseek_api_key
        if model.startswith("openai/"):
            return self.settings.openai_api_key
        if model.startswith("anthropic/"):
            return self.settings.anthropic_api_key
        if model.startswith("zhipu/"):
            return self.settings.zhipu_api_key
        return None

    async def generate_image(
        self,
        prompt: str,
        *,
        size: str = "1024x1024",
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """生成图像 (报告配图).

        通过 LiteLLM aimage_generation 调用 deepseek-v4-flash 生成图像.
        返回 dict 含:
        - url: 图像 URL (若 API 返回 URL, 否则 None)
        - b64: 图像 base64 数据 (若 API 返回 b64, 否则 None)
        - prompt: 原始提示词
        - model: 模型名
        - size: 图像尺寸
        - created_at: 生成时间 (ISO 8601 UTC)

        失败时抛出异常, 业务代码捕获降级 (报告不带图, 记录 warning).

        注意: deepseek-v4-flash 图像生成能力假设支持, 实际能力以官方文档为准.
        """
        model = self.settings.image_model
        api_key = self._get_api_key(model)
        quality = self.settings.image_quality

        async with trace_chain(
            name="image-generator",
            input={"prompt": prompt[:200], "model": model, "size": size},
            user_id=user_id,
            session_id=session_id,
        ) as span:
            try:
                # 延迟导入 litellm, 避免模块加载时强依赖 (与 llm/client.py 一致)
                import litellm

                kwargs: dict[str, Any] = {
                    "model": model,
                    "prompt": prompt,
                    "n": 1,
                    "size": size,
                    "timeout": self.settings.llm_timeout,
                    "num_retries": self.settings.llm_max_retries,
                }
                if api_key:
                    kwargs["api_key"] = api_key
                if quality:
                    kwargs["quality"] = quality

                # 通过 LiteLLM aimage_generation 调用图像生成 (AGENTS.md 第 9 章)
                # deepseek-v4-flash 图像生成能力假设支持, 实际以官方文档为准
                response = await litellm.aimage_generation(**kwargs)

                # 解析响应 (LiteLLM 统一为 OpenAI 兼容 ImageResponse 格式)
                result = self._parse_image_response(response, model, prompt, size)

                span.update(
                    output={
                        "has_url": result["url"] is not None,
                        "has_b64": result["b64"] is not None,
                        "model": model,
                    }
                )
                return result

            except Exception as e:  # noqa: BLE001
                logger.error(
                    "图像生成失败 (model=%s, prompt=%s): %s",
                    model,
                    prompt[:100],
                    e,
                )
                span.update(metadata={"error": str(e)})
                raise

    @staticmethod
    def _parse_image_response(
        response: Any,
        model: str,
        prompt: str,
        size: str,
    ) -> dict[str, Any]:
        """解析 LiteLLM aimage_generation 响应.

        LiteLLM 统一为 OpenAI 兼容格式: response.data[0].url | .b64_json
        兼容 dict 与对象两种访问方式.
        """
        data_list = getattr(response, "data", None)
        if data_list is None and isinstance(response, dict):
            data_list = response.get("data")
        if not data_list:
            raise RuntimeError("图像生成 API 返回空数据")

        data = data_list[0]

        # 兼容对象属性与 dict 两种访问方式
        url: str | None = None
        b64: str | None = None

        if isinstance(data, dict):
            url = data.get("url")
            b64 = data.get("b64_json")
        else:
            url = getattr(data, "url", None)
            b64 = getattr(data, "b64_json", None)

        if not url and not b64:
            raise RuntimeError("图像生成 API 未返回 url 或 b64_json")

        return {
            "url": url,
            "b64": b64,
            "prompt": prompt,
            "model": model,
            "size": size,
            "created_at": datetime.now(UTC).isoformat(),
        }
