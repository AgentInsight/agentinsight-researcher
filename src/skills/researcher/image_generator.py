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


# SVG 矢量配图生成 prompt 模板
_SVG_IMAGE_PROMPT_TEMPLATE = """请生成一张表示「{topic}」概念的 SVG 矢量配图.

## 要求

- 尺寸: {width}x{height}
- 风格: {style_hint}
- 主色调: {color_palette}
- 适合作为研究报告配图 (专业、简洁、现代)
- 必须包含 xmlns 属性和 viewBox 属性
- 中文标注 (如需文字)

## 重要约束 (必须遵守)

- 禁止使用 emoji 字符 (如 📜📈🔬📚🎯🚀 等), cairosvg 无法渲染 emoji 会显示乱码
- 禁止使用 HTML 实体 (如 &amp; &lt; &gt;), 直接使用原始字符
- 禁止使用 HTML 注释 (如 <!-- 注释 -->), 注释会影响 Markdown 渲染器解析
- 文字使用纯中文/英文, 不加装饰符号
- font-family 使用 "Noto Sans CJK SC", "Arial", sans-serif
- SVG 元素之间禁止有空行, 所有元素紧凑排列 (空行会触发 CommonMark HTML 块中断)

## 输出格式

仅返回纯 SVG 代码, 不要 markdown 代码块, 不要任何解释.
SVG 必须以 <svg xmlns 开头, 以 </svg> 结尾.
保持 SVG 代码简洁 (控制在 5000 字符以内).
SVG 内部元素之间不要留空行, 保持紧凑格式.

## 主题描述

{prompt}
"""


# SVG 主题到风格的路由 (复用 _TOPIC_STYLE_KEYWORDS 的关键词匹配)
_SVG_STYLE_MAP: dict[str, dict[str, str]] = {
    "technology": {
        "style_hint": "futuristic, digital art, clean lines, geometric shapes",
        "color_palette": "blue and cyan (#007BFF, #00BCD4, #2196F3)",
    },
    "business": {
        "style_hint": "professional, corporate, minimalist, clean composition",
        "color_palette": "blue and gray (#2c3e50, #3498db, #95a5a6)",
    },
    "science": {
        "style_hint": "scientific illustration, detailed, accurate, academic style",
        "color_palette": "neutral palette (#34495e, #16a085, #27ae60)",
    },
    "medical": {
        "style_hint": "medical illustration, clean, professional, soft palette",
        "color_palette": "soft medical colors (#e74c3c, #ecf0f1, #3498db)",
    },
    "default": {
        "style_hint": "professional, modern, balanced composition",
        "color_palette": "blue palette (#007BFF, #6c757d, #adb5bd)",
    },
}


def _select_svg_style(topic: str) -> dict[str, str]:
    """根据主题选择 SVG 风格 (复用 _TOPIC_STYLE_KEYWORDS 路由)."""
    topic_lower = topic.lower()
    for keyword, style_name in _TOPIC_STYLE_KEYWORDS.items():
        if keyword in topic_lower:
            return _SVG_STYLE_MAP[style_name]
    return _SVG_STYLE_MAP["default"]


def _parse_size(size: str) -> tuple[int, int]:
    """解析尺寸字符串 '1024x1024' → (1024, 1024)."""
    try:
        w, h = size.lower().split("x")
        return int(w), int(h)
    except (ValueError, AttributeError):
        return 1024, 1024  # 默认尺寸


class ImageGenerator:
    """图像生成器 (报告配图).

    用户明确要求: 使用 deepseek-v4-flash 模型 (非 gemini).

    两种输出模式 (由 settings.image_output_format 路由):
    - svg: 通过 /chat/completions 生成 SVG 矢量图 (推荐, DeepSeek V4 Flash 支持)
    - url/b64: 通过 /images/generations 生成位图 (DeepSeek 不支持, 供未来扩展)

    全部 LLM/图像调用经 llm/ 网关 (LiteLLM), 禁厂商 SDK 直连.
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

        根据 image_output_format 配置路由:
        - svg: 通过 /chat/completions 生成 SVG 矢量图 (推荐, DeepSeek V4 Flash 支持)
        - url/b64: 通过 /images/generations 生成位图 (DeepSeek 不支持, 会 404)

        失败时抛出异常, 业务代码捕获降级 (报告不带图, 记录 warning).

        Args:
            prompt: 原始提示词 (会被 _enhance_prompt 增强).
            topic: 报告主题 (用于风格路由, 空字符串走 default 风格).
        """
        output_format = self.settings.image_output_format.lower()

        if output_format == "svg":
            return await self._generate_svg_image(
                prompt,
                topic,
                size=size,
                user_id=user_id,
                session_id=session_id,
            )

        # 位图模式 (保留原逻辑, 供未来支持 DALL-E/CogView 等)
        return await self._generate_bitmap_image(
            prompt,
            topic,
            size=size,
            user_id=user_id,
            session_id=session_id,
        )

    async def _generate_bitmap_image(
        self,
        prompt: str,
        topic: str,
        *,
        size: str = "1024x1024",
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """位图生成 (原 generate_image 逻辑, 保留供未来使用).

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

    async def _generate_svg_image(
        self,
        prompt: str,
        topic: str,
        *,
        size: str = "1024x1024",
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """通过 /chat/completions 生成 SVG 矢量配图.

        DeepSeek V4 Flash 是推理模型, 思考模式会消耗大量 reasoning_tokens
        导致 max_tokens 全用于推理, 输出 content 为空.
        SVG 生成是创意输出任务, 通过 extra_body={"thinking": {"type": "disabled"}}
        关闭思考模式, 确保输出 SVG 代码.

        Returns:
            dict 含:
            - svg: SVG 代码字符串
            - b64: SVG 的 base64 编码 (用于 data URI 嵌入)
            - url: None (SVG 模式无 URL)
            - prompt: 实际发送的提示词
            - model: 模型名
            - size: 图像尺寸
            - created_at: 生成时间 (ISO 8601 UTC)
            - format: "svg"
        """
        import base64

        model = self.settings.image_model
        api_key = self.settings.image_api_key or resolve_api_key(model, self.settings)
        width, height = _parse_size(size)
        style = _select_svg_style(topic)

        # 构建 prompt
        svg_prompt = _SVG_IMAGE_PROMPT_TEMPLATE.format(
            topic=topic or prompt[:50],
            width=width,
            height=height,
            style_hint=style["style_hint"],
            color_palette=style["color_palette"],
            prompt=prompt,
        )

        async with trace_chain(
            name="image-generator-svg",
            input={"prompt": prompt[:200], "topic": topic[:100], "size": size, "model": model},
            user_id=user_id,
            session_id=session_id,
        ) as span:
            try:
                # 延迟导入 litellm
                import litellm

                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": [{"role": "user", "content": svg_prompt}],
                    "temperature": self.settings.image_svg_temperature,
                    "max_tokens": self.settings.image_svg_max_tokens,
                    "timeout": self.settings.llm_timeout,
                    "num_retries": self.settings.llm_max_retries,
                    # DeepSeek V4 Flash 是推理模型, 思考模式会消耗大量 reasoning_tokens
                    # 导致 max_tokens=8192 全用于推理, 输出 content 为空 (finish_reason=length)
                    # SVG 生成是创意输出任务, 不需要推理, 关闭思考模式
                    "extra_body": {"thinking": {"type": "disabled"}},
                }
                if api_key:
                    kwargs["api_key"] = api_key

                # 通过 LiteLLM acompletion 调用聊天接口 (非 aimage_generation)
                response = await litellm.acompletion(**kwargs)

                # 提取响应内容 (LiteLLM ModelResponse 支持 dict 风格访问 choices/message/content)
                content = response["choices"][0]["message"]["content"]
                finish_reason = response["choices"][0].get("finish_reason", "")

                # usage 是 LiteLLM Usage pydantic model 对象 (非 dict),
                # 必须用 getattr 访问属性, 不能用 .get() (CompletionTokensDetailsWrapper 无 .get 方法)
                usage = getattr(response, "usage", None)
                completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
                completion_tokens_details = getattr(usage, "completion_tokens_details", None) if usage else None
                reasoning_tokens = getattr(completion_tokens_details, "reasoning_tokens", 0) if completion_tokens_details else 0

                # 提取 SVG 代码
                svg_code = self._extract_svg(content)

                if not svg_code:
                    raise RuntimeError(
                        f"SVG 代码提取失败 (finish_reason={finish_reason}, "
                        f"content_length={len(content)})"
                    )

                # 检查是否被截断
                if finish_reason == "length":
                    logger.warning(
                        "SVG 生成可能被 max_tokens 截断 (finish_reason=length, "
                        "completion_tokens=%s). 考虑提升 image_svg_max_tokens.",
                        completion_tokens,
                    )

                # SVG 验证
                validation = self._validate_svg(svg_code)
                if not validation["valid"]:
                    raise RuntimeError(f"SVG 验证失败: {validation['issues']}")

                # SVG 转 base64 (用于 data URI 嵌入 Markdown)
                svg_b64 = base64.b64encode(svg_code.encode("utf-8")).decode("ascii")

                result = {
                    "svg": svg_code,
                    "b64": svg_b64,
                    "url": None,
                    "prompt": svg_prompt[:500],  # 截断用于 trace
                    "model": model,
                    "size": size,
                    "created_at": datetime.now(UTC).isoformat(),
                    "format": "svg",
                }

                span.update(
                    output={
                        "has_svg": True,
                        "svg_length": len(svg_code),
                        "model": model,
                        "finish_reason": finish_reason,
                    },
                    metadata={
                        "completion_tokens": completion_tokens,
                        "reasoning_tokens": reasoning_tokens,
                        "svg_validation": validation,
                    },
                )
                return result

            except Exception as e:  # noqa: BLE001
                logger.error(
                    "SVG 图像生成失败 (model=%s, topic=%s): %s",
                    model,
                    topic[:100],
                    e,
                )
                span.update(metadata={"error": str(e)})
                raise

    @staticmethod
    def _extract_svg(content: str) -> str | None:
        """从 LLM 响应中提取 SVG 代码.

        支持两种格式:
        1. ```svg\\n<svg...>...</svg>\\n``` (markdown 代码块)
        2. <svg xmlns=...>...</svg> (裸 SVG)
        """
        import re

        # 1. 尝试提取 ```svg ... ``` 或 ```xml ... ``` 代码块
        code_block_pattern = r"```(?:svg|xml|html)?\s*\n?(<svg[\s\S]*?</svg>)\s*\n?```"
        matches: list[str] = re.findall(code_block_pattern, content, re.DOTALL)
        if matches:
            return str(matches[0]).strip()

        # 2. 尝试直接提取 <svg ... </svg>
        svg_pattern = r"(<svg[\s\S]*?</svg>)"
        matches = re.findall(svg_pattern, content, re.DOTALL)
        if matches:
            return str(matches[0]).strip()

        return None

    @staticmethod
    def _validate_svg(svg: str) -> dict[str, Any]:
        """验证 SVG 基本语法.

        Returns:
            {"valid": bool, "issues": list[str]}
        """
        import re

        issues: list[str] = []
        if "xmlns" not in svg:
            issues.append("缺少 xmlns 属性")
        if "viewBox" not in svg:
            issues.append("缺少 viewBox 属性 (影响缩放)")
        if "</svg>" not in svg:
            issues.append("未闭合 </svg> 标签")
        if "<svg" not in svg:
            issues.append("缺少 <svg> 开标签")

        # 标签匹配粗略检查
        open_tags = re.findall(r"<(\w+)[\s>]", svg)
        close_tags = re.findall(r"</(\w+)>", svg)
        self_closing = re.findall(r"<(\w+)[^>]*/>", svg)
        for tag in self_closing:
            if tag in open_tags:
                open_tags.remove(tag)
        if len(open_tags) != len(close_tags):
            issues.append(f"标签不匹配 (开 {len(open_tags)} / 闭 {len(close_tags)})")

        return {"valid": len(issues) == 0, "issues": issues}
