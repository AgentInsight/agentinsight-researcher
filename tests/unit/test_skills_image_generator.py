"""单元测试: ImageGenerator 静态纯函数.

验证 _parse_image_response (dict/对象形式响应) 与 _get_api_key (按路由前缀).
单元测试在构建期执行, 不依赖外部服务.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.common.llm_key_resolver import resolve_api_key
from src.config.settings import Settings
from src.skills.researcher.image_generator import ImageGenerator

pytestmark = pytest.mark.unit


# ========== _parse_image_response dict 形式 ==========


def test_parse_image_response_dict_with_url() -> None:
    """测试 dict 形式响应含 url 时正确提取."""
    response = {"data": [{"url": "https://example.com/image.png"}]}
    result = ImageGenerator._parse_image_response(response, "model", "prompt", "1024x1024")
    assert result["url"] == "https://example.com/image.png"
    assert result["b64"] is None
    assert result["prompt"] == "prompt"
    assert result["model"] == "model"
    assert result["size"] == "1024x1024"
    assert "created_at" in result


def test_parse_image_response_dict_with_b64() -> None:
    """测试 dict 形式响应含 b64_json 时正确提取."""
    response = {"data": [{"b64_json": "abc123base64"}]}
    result = ImageGenerator._parse_image_response(response, "model", "prompt", "1024x1024")
    assert result["b64"] == "abc123base64"
    assert result["url"] is None


def test_parse_image_response_dict_with_both_url_and_b64() -> None:
    """测试 dict 形式响应同时含 url 与 b64_json."""
    response = {"data": [{"url": "https://x.com/img.png", "b64_json": "xyz"}]}
    result = ImageGenerator._parse_image_response(response, "m", "p", "s")
    assert result["url"] == "https://x.com/img.png"
    assert result["b64"] == "xyz"


# ========== _parse_image_response 对象形式 ==========


def test_parse_image_response_object_with_url() -> None:
    """测试对象形式响应 (SimpleNamespace) 含 url."""
    data = SimpleNamespace(url="https://example.com/img.png", b64_json=None)
    response = SimpleNamespace(data=[data])
    result = ImageGenerator._parse_image_response(response, "model", "prompt", "1024x1024")
    assert result["url"] == "https://example.com/img.png"
    assert result["b64"] is None


def test_parse_image_response_object_with_b64() -> None:
    """测试对象形式响应含 b64_json."""
    data = SimpleNamespace(url=None, b64_json="base64data")
    response = SimpleNamespace(data=[data])
    result = ImageGenerator._parse_image_response(response, "m", "p", "s")
    assert result["b64"] == "base64data"
    assert result["url"] is None


def test_parse_image_response_object_attribute_access() -> None:
    """测试对象属性访问 (无 b64_json 字段时返回 None)."""
    data = SimpleNamespace(url="https://example.com/img.png")  # 无 b64_json 属性
    response = SimpleNamespace(data=[data])
    result = ImageGenerator._parse_image_response(response, "m", "p", "s")
    assert result["url"] == "https://example.com/img.png"
    assert result["b64"] is None  # getattr 默认 None


# ========== _parse_image_response 缺失字段降级 ==========


def test_parse_image_response_empty_data_raises() -> None:
    """测试空 data 抛 RuntimeError."""
    response: dict[str, Any] = {"data": []}
    with pytest.raises(RuntimeError, match="空数据"):
        ImageGenerator._parse_image_response(response, "m", "p", "s")


def test_parse_image_response_no_data_key_raises() -> None:
    """测试无 data key 抛 RuntimeError."""
    response: dict[str, Any] = {}
    with pytest.raises(RuntimeError):
        ImageGenerator._parse_image_response(response, "m", "p", "s")


def test_parse_image_response_missing_url_and_b64_raises() -> None:
    """测试既无 url 也无 b64_json 抛 RuntimeError."""
    response = {"data": [{"other": "field"}]}
    with pytest.raises(RuntimeError, match="未返回 url 或 b64_json"):
        ImageGenerator._parse_image_response(response, "m", "p", "s")


def test_parse_image_response_returns_dict_with_required_keys() -> None:
    """测试返回 dict 含 url/b64/prompt/model/size/created_at 六键."""
    response = {"data": [{"url": "https://x.com/i.png"}]}
    result = ImageGenerator._parse_image_response(response, "model", "prompt", "size")
    expected_keys = {"url", "b64", "prompt", "model", "size", "created_at"}
    assert set(result.keys()) == expected_keys


# ========== resolve_api_key 按路由前缀 (抽取到 common/llm_key_resolver) ==========


@pytest.fixture()
def generator_with_keys() -> ImageGenerator:
    """构造 ImageGenerator 实例, 配置各厂商 API Key."""
    settings = Settings(
        _env_file=None,
        deepseek_api_key="deepseek-key-xxx",
        openai_api_key="openai-key-yyy",
        anthropic_api_key="anthropic-key-zzz",
        zhipu_api_key="zhipu-key-www",
        image_api_key=None,  # 不设独立 image_api_key, 走路由前缀
    )
    return ImageGenerator(settings=settings)


def test_get_api_key_deepseek_prefix(generator_with_keys: ImageGenerator) -> None:
    """测试 'deepseek/' 前缀返回 deepseek_api_key."""
    assert (
        resolve_api_key("deepseek/deepseek-v4-flash", generator_with_keys.settings)
        == "deepseek-key-xxx"
    )


def test_get_api_key_openai_prefix(generator_with_keys: ImageGenerator) -> None:
    """测试 'openai/' 前缀返回 openai_api_key."""
    assert resolve_api_key("openai/dall-e-3", generator_with_keys.settings) == "openai-key-yyy"


def test_get_api_key_anthropic_prefix(generator_with_keys: ImageGenerator) -> None:
    """测试 'anthropic/' 前缀返回 anthropic_api_key."""
    assert (
        resolve_api_key("anthropic/claude-3-sonnet", generator_with_keys.settings)
        == "anthropic-key-zzz"
    )


def test_get_api_key_zhipu_prefix(generator_with_keys: ImageGenerator) -> None:
    """测试 'zhipu/' 前缀返回 zhipu_api_key."""
    assert resolve_api_key("zhipu/glm-4v", generator_with_keys.settings) == "zhipu-key-www"


def test_get_api_key_unknown_prefix_returns_none(generator_with_keys: ImageGenerator) -> None:
    """测试未知前缀返回 None."""
    assert resolve_api_key("unknown/model", generator_with_keys.settings) is None


def test_get_api_key_no_prefix_returns_none(generator_with_keys: ImageGenerator) -> None:
    """测试无前缀模型名返回 None."""
    assert resolve_api_key("plain-model-name", generator_with_keys.settings) is None


def test_get_api_key_image_api_key_overrides_routes() -> None:
    """测试 image_api_key 优先级高于路由前缀 (若单独配置).

    ImageGenerator.generate_image 用 `settings.image_api_key or resolve_api_key(...)`,
    即 image_api_key 配置后优先于路由前缀返回.
    """
    settings = Settings(
        _env_file=None,
        deepseek_api_key="deepseek-key",
        image_api_key="dedicated-image-key",
    )
    # 即使模型名是 deepseek/*, 也应优先返回 image_api_key
    api_key = settings.image_api_key or resolve_api_key("deepseek/deepseek-v4-flash", settings)
    assert api_key == "dedicated-image-key"


def test_get_api_key_no_keys_configured_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试所有 Key 未配置时返回 None.

    注意: Settings(_env_file=None) 仍会从环境变量读取 API Key,
    需 monkeypatch 清除所有相关环境变量确保测试隔离.
    """
    for key in [
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ZHIPU_API_KEY",
        "IMAGE_API_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    # image_api_key 未配置, 走 resolve_api_key (deepseek_api_key 也未配置 → None)
    api_key = settings.image_api_key or resolve_api_key("deepseek/deepseek-v4-flash", settings)
    assert api_key is None


# ========== SVG 矢量配图生成 ==========


@pytest.fixture()
def svg_settings() -> Settings:
    """构造 SVG 模式 Settings (image_output_format=svg)."""
    return Settings(
        _env_file=None,
        image_output_format="svg",
        image_svg_max_tokens=16384,
        image_svg_temperature=0.7,
    )


@pytest.fixture()
def svg_generator(svg_settings: Settings) -> ImageGenerator:
    """构造 SVG 模式 ImageGenerator."""
    return ImageGenerator(svg_settings)


async def test_generate_svg_image_success(svg_generator: ImageGenerator) -> None:
    """测试 SVG 生成成功."""
    mock_response = {
        "choices": [
            {
                "message": {
                    "content": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">'
                    '<rect width="200" height="200" fill="#007BFF"/></svg>'
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "completion_tokens": 100,
            "completion_tokens_details": {"reasoning_tokens": 80},
        },
    }

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
        result = await svg_generator.generate_image(
            "AI 概念图", topic="artificial intelligence", size="200x200"
        )

    assert result["svg"].startswith("<svg xmlns")
    assert result["svg"].endswith("</svg>")
    assert result["b64"] is not None
    assert result["url"] is None
    assert result["format"] == "svg"


async def test_generate_svg_image_extraction_from_code_block(
    svg_generator: ImageGenerator,
) -> None:
    """测试从 ```svg 代码块提取 SVG."""
    mock_response = {
        "choices": [
            {
                "message": {
                    "content": '```svg\n<svg xmlns="http://www.w3.org/2000/svg" '
                    'viewBox="0 0 100 100">'
                    '<rect width="100" height="100" fill="blue"/></svg>\n```'
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {},
    }

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
        result = await svg_generator.generate_image("test", topic="test")

    assert "<svg" in result["svg"]
    assert "</svg>" in result["svg"]


async def test_generate_svg_image_truncated(svg_generator: ImageGenerator) -> None:
    """测试 SVG 被截断 (finish_reason=length, 无 </svg> 闭合)."""
    mock_response = {
        "choices": [
            {
                "message": {"content": '<svg xmlns="http://www.w3.org/2000/svg"><rect'},
                "finish_reason": "length",
            }
        ],
        "usage": {"completion_tokens": 8192},
    }

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
        with pytest.raises(RuntimeError, match="SVG 代码提取失败"):
            await svg_generator.generate_image("test", topic="test")


async def test_generate_svg_image_invalid_svg(svg_generator: ImageGenerator) -> None:
    """测试 SVG 验证失败 (缺 viewBox)."""
    mock_response = {
        "choices": [
            {
                "message": {"content": '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'},
                "finish_reason": "stop",
            }
        ],
        "usage": {},
    }

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
        with pytest.raises(RuntimeError, match="SVG 验证失败"):
            await svg_generator.generate_image("test", topic="test")


def test_extract_svg_raw() -> None:
    """测试裸 SVG 提取."""
    content = '<svg xmlns="x" viewBox="0 0 100 100"><rect/></svg>'
    svg = ImageGenerator._extract_svg(content)
    assert svg is not None
    assert "<svg" in svg


def test_extract_svg_code_block() -> None:
    """测试代码块 SVG 提取."""
    content = '```svg\n<svg xmlns="x" viewBox="0 0 100 100"><rect/></svg>\n```'
    svg = ImageGenerator._extract_svg(content)
    assert svg is not None
    assert svg.startswith("<svg")


def test_validate_svg_valid() -> None:
    """测试有效 SVG 验证."""
    svg = '<svg xmlns="x" viewBox="0 0 100 100"><rect/></svg>'
    result = ImageGenerator._validate_svg(svg)
    assert result["valid"] is True
    assert len(result["issues"]) == 0


def test_validate_svg_missing_viewbox() -> None:
    """测试缺 viewBox 的 SVG."""
    svg = '<svg xmlns="x"><rect/></svg>'
    result = ImageGenerator._validate_svg(svg)
    assert result["valid"] is False
    assert "缺少 viewBox" in " ".join(result["issues"])
