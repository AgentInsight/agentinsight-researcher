"""图像生成器 (报告配图).

本项目使用 deepseek-v4-flash (用户明确要求, 非 gemini).

全部 LLM/图像调用经 llm/ 网关 (LiteLLM), 禁厂商 SDK 直连.
必须包裹在 trace span 内 (用 trace_chain).
禁用观察者模式.

注意: deepseek-v4-flash 图像生成能力假设支持, 实际能力以官方文档为准.
配置项 image_model 可由用户在 .env 覆盖.
若 LiteLLM aimage_generation 不支持该模型, 业务代码捕获异常降级 (报告不带图).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from src.common.llm_key_resolver import resolve_api_key
from src.config.settings import Settings, get_settings
from src.observability.tracing import trace_chain

logger = logging.getLogger(__name__)

# 图像生成 prompt 增强
# 风格预设: 报告配图按主题选择风格
_IMAGE_STYLE_PRESETS: dict[str, dict[str, str]] = {
    "technology": {
        "style": "futuristic, digital art, clean lines, blue and cyan palette, tech aesthetic",
        "negative": "low quality, blurry, distorted, watermark, text, ugly",
    },
    "business": {
        "style": "professional, corporate, minimalist, blue and gray palette, business appropriate",
        "negative": "cartoon, anime, casual, low quality, watermark",
    },
    "science": {
        "style": "scientific illustration, detailed, accurate, neutral palette, academic style",
        "negative": "cartoon, stylized, low quality, text, watermark",
    },
    "medical": {
        "style": "medical illustration, anatomically accurate, clean, professional, soft palette",
        "negative": "gruesome, cartoon, stylized, low quality, text",
    },
    "default": {
        "style": "professional, high quality, detailed, balanced composition",
        "negative": "low quality, blurry, distorted, watermark, text, ugly",
    },
}

# 主题关键词到风格的路由
_TOPIC_STYLE_KEYWORDS: dict[str, str] = {
    "tech": "technology",
    "ai": "technology",
    "软件": "technology",
    "算法": "technology",
    "编程": "technology",
    "business": "business",
    "商业": "business",
    "金融": "business",
    "市场": "business",
    "science": "science",
    "科学": "science",
    "研究": "science",
    "medical": "medical",
    "医学": "medical",
    "医疗": "medical",
    "健康": "medical",
}


def _select_style(topic: str) -> dict[str, str]:
    """根据主题选择风格预设.

    Args:
        topic: 报告主题.

    Returns:
        {"style": ..., "negative": ...} 字典.
    """
    topic_lower = topic.lower()
    for keyword, style_name in _TOPIC_STYLE_KEYWORDS.items():
        if keyword in topic_lower:
            return _IMAGE_STYLE_PRESETS[style_name]
    return _IMAGE_STYLE_PRESETS["default"]


def _enhance_prompt(
    base_prompt: str,
    topic: str,
    *,
    aspect_ratio: str = "16:9",
) -> dict[str, str]:
    """增强图像生成 prompt.

    多步生成: 计划 → 生成 → 评估 → 重试.
    AIR 简化版: 单步增强 prompt (主题风格 + negative prompt + 质量词).

    Args:
        base_prompt: 用户原始 prompt.
        topic: 报告主题 (用于风格路由).
        aspect_ratio: 宽高比 (默认 16:9 报告配图).

    Returns:
        {"prompt": 增强后的正向 prompt, "negative_prompt": 反向 prompt,
         "style": 选中风格名}.
    """
    style = _select_style(topic)
    # 组合: 原始 prompt + 主题风格 + 质量词 + 宽高比
    enhanced = (
        f"{base_prompt}, {style['style']}, "
        f"high resolution, sharp focus, professional composition, "
        f"aspect ratio {aspect_ratio}"
    )
    return {
        "prompt": enhanced,
        "negative_prompt": style["negative"],
        "style": style["style"][:50],  # 截断用于 trace
    }


class ImageGenerator:
    """图像生成器 (报告配图).

    用户明确要求: 使用 deepseek-v4-flash 模型 (非 gemini).

    通过 LiteLLM aimage_generation 调用, 禁厂商 SDK 直连.
    """

    settings: Settings

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def generate_image(
        self,
        prompt: str,
        *,
        size: str = "1024x1024",
        user_id: str | None = None,
        session_id: str | None = None,
        topic: str = "",
    ) -> dict[str, Any]:
        """生成图像 (报告配图).

        通过 LiteLLM aimage_generation 调用 deepseek-v4-flash 生成图像.
        调用前用 _enhance_prompt 增强 prompt (主题风格 + negative prompt + 质量词),
        AIR 简化为单步增强.

        返回 dict 含:
        - url: 图像 URL (若 API 返回 URL, 否则 None)
        - b64: 图像 base64 数据 (若 API 返回 b64, 否则 None)
        - prompt: 实际发送的提示词 (增强后)
        - model: 模型名
        - size: 图像尺寸
        - created_at: 生成时间 (ISO 8601 UTC)

        失败时抛出异常, 业务代码捕获降级 (报告不带图, 记录 warning).

        Args:
            prompt: 原始提示词 (会被 _enhance_prompt 增强).
            topic: 报告主题 (用于风格路由, 空字符串走 default 风格).

        注意: deepseek-v4-flash 图像生成能力假设支持, 实际能力以官方文档为准.
        """
        model = self.settings.image_model
        # 复用 common/llm_key_resolver.resolve_api_key (DRY, 不再定义 _get_api_key)
        # 优先用 image_api_key (若单独配置), 否则回退到对应厂商 API Key
        api_key = self.settings.image_api_key or resolve_api_key(model, self.settings)
        quality = self.settings.image_quality

        async with trace_chain(
            name="image-generator",
            input={"prompt": prompt[:200], "model": model, "size": size, "topic": topic[:100]},
            user_id=user_id,
            session_id=session_id,
        ) as span:
            try:
                # prompt 增强
                # 多步生成: 计划 → 生成 → 评估 → 重试
                # AIR 简化版: 单步增强 prompt (主题风格 + negative prompt + 质量词)
                enhanced = _enhance_prompt(prompt, topic)
                prompt = enhanced["prompt"]
                negative_prompt = enhanced["negative_prompt"]
                style_label = enhanced["style"]

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

                # 通过 LiteLLM aimage_generation 调用图像生成
                # deepseek-v4-flash 图像生成能力假设支持, 实际以官方文档为准
                # OpenAI DALL-E 不支持 negative_prompt, 仅用正向增强 prompt;
                # negative_prompt 记录在 trace metadata 供排查 (SDXL/ComfyUI 可用)
                response = await litellm.aimage_generation(**kwargs)

                # 解析响应 (LiteLLM 统一为 OpenAI 兼容 ImageResponse 格式)
                result = self._parse_image_response(response, model, prompt, size)

                span.update(
                    output={
                        "has_url": result["url"] is not None,
                        "has_b64": result["b64"] is not None,
                        "model": model,
                    },
                    metadata={
                        "enhanced_prompt": prompt[:200],
                        "negative_prompt": negative_prompt,
                        "style": style_label,
                    },
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
