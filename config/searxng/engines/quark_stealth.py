# SPDX-License-Identifier: AGPL-3.0-or-later
"""Quark 搜索引擎 - curl_cffi 隐身模式 (规避 CAPTCHA 检测).

问题: quark 检测到 httpx 的 Python TLS 指纹后, 返回 Alibaba X5SEC CAPTCHA 页面,
      SearXNG 检测到后抛 SearxEngineCaptchaException (暂停 900s).
方案: 用 curl_cffi 模拟 Chrome TLS 指纹 (BoringSSL) 替代 httpx 发请求, 规避反爬检测。
      curl_cffi 是本地工具 (非 SaaS), 通过 BoringSSL 模拟浏览器 TLS 握手。

部署:
  1. 本文件 bind mount 到 SearXNG 容器内 searx/engines/quark_stealth.py
  2. docker-compose 启动时安装 curl_cffi (见 searxng service command)
  3. settings.yml 中 quark 引擎的 engine: quark_stealth (替代 engine: quark)

原理:
  - request(): 复用原 quark URL 构建 + 用 curl_cffi 发请求 (Chrome 指纹) + 缓存响应到线程本地
  - response(): 优先用 curl_cffi 缓存的响应解析, 降级用 SearXNG httpx 响应
  - SearXNG 仍会用 httpx 发请求 (无法阻止), 但 response() 忽略 httpx 响应, 使用 curl_cffi 响应
"""

import logging
import threading
from typing import Any

# curl_cffi 可选导入 (容器启动时 pip install curl_cffi 安装)
try:
    import curl_cffi  # noqa: F401

    _HAS_CURL_CFFI = True
except ImportError:
    _HAS_CURL_CFFI = False

# 复用原 quark 引擎的 URL 构建与结果解析逻辑
try:
    from searx.engines import quark as _quark

    _HAS_ORIGINAL_QUARK = True
except ImportError:
    _HAS_ORIGINAL_QUARK = False
    _quark = None  # type: ignore[assignment]

# SearXNG 异常 (用于 CAPTCHA 检测)
try:
    from searx.exceptions import SearxEngineCaptchaException
except ImportError:
    SearxEngineCaptchaException = Exception  # type: ignore[misc, assignment]

logger = logging.getLogger(__name__)

# 线程本地存储: 缓存 curl_cffi 响应
_local = threading.local()

# ========== 引擎元数据 (复用原 quark 配置) ==========
if _HAS_ORIGINAL_QUARK:
    about = _quark.about
    language = _quark.language
    paging = _quark.paging
    categories = _quark.categories
    results_per_page = _quark.results_per_page
    quark_category = _quark.quark_category
    time_range_support = _quark.time_range_support
    time_range_dict = _quark.time_range_dict
    init = _quark.init
else:
    about = {
        "website": "https://quark.sm.cn/",
        "wikidata_id": "Q48816502",
        "use_official_api": False,
        "require_api_key": False,
        "results": "HTML",
    }
    language = "zh"
    paging = True
    categories = []
    results_per_page = 10
    quark_category = "general"
    time_range_support = True
    time_range_dict = {"day": "4", "week": "3", "month": "2", "year": "1"}


def request(query: str, params: dict[str, Any]) -> dict[str, Any]:
    """构建 quark 搜索请求, 用 curl_cffi 发送 (Chrome TLS 指纹).

    若 curl_cffi 不可用, 降级为原 quark 逻辑 (SearXNG 用 httpx 发请求, 可能触发 CAPTCHA).

    Args:
        query: 搜索查询词.
        params: SearXNG 请求参数字典.

    Returns:
        更新后的 params 字典.
    """
    _local.curl_response = None

    # 1. 复用原 quark 的 URL 构建逻辑
    if _HAS_ORIGINAL_QUARK:
        params = _quark.request(query, params)

    # 2. curl_cffi 不可用时降级
    if not _HAS_CURL_CFFI:
        logger.warning("curl_cffi 未安装, quark_stealth 降级为 httpx 请求 (可能触发 CAPTCHA)")
        return params

    # 3. 用 curl_cffi 发送请求 (模拟 Chrome TLS 指纹)
    url = params["url"]
    headers = dict(params.get("headers", {}))
    try:
        from curl_cffi import requests as curl_requests

        curl_resp = curl_requests.get(
            url,
            headers=headers,
            impersonate="chrome",
            timeout=10,
            allow_redirects=True,  # quark CAPTCHA 在 HTML 内容中检测, 需跟随重定向
        )
        # quark CAPTCHA 在 HTML 内容中检测 (Alibaba X5SEC pattern)
        if _HAS_ORIGINAL_QUARK and _quark.is_alibaba_captcha(curl_resp.text):
            raise SearxEngineCaptchaException(
                message="Quark Alibaba CAPTCHA (quark_stealth: curl_cffi Chrome 指纹仍被检测)",
                suspended_time=900,
            )
        _local.curl_response = curl_resp
    except SearxEngineCaptchaException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("curl_cffi 请求失败, 降级为 httpx: %s", e)
        _local.curl_response = None

    return params


def response(resp: Any) -> list[dict[str, Any]]:
    """解析 quark 搜索结果.

    优先使用 curl_cffi 缓存的响应; 若缓存不存在, 降级使用 SearXNG httpx 响应.
    """
    # 1. 优先使用 curl_cffi 缓存的响应
    curl_resp = getattr(_local, "curl_response", None)
    if curl_resp is not None:
        _local.curl_response = None
        wrapper = _CurlResponseWrapper(curl_resp)
        if _HAS_ORIGINAL_QUARK:
            return _quark.response(wrapper)
        return []

    # 2. 降级: 使用 SearXNG httpx 响应
    if _HAS_ORIGINAL_QUARK:
        return _quark.response(resp)
    return []


class _CurlResponseWrapper:
    """curl_cffi 响应包装器, 兼容 SearXNG quark 引擎的 Response 接口.

    quark response() 检查 resp.text (HTML 内容) 做 CAPTCHA 检测和结果解析.
    """

    def __init__(self, curl_resp: Any) -> None:
        self.text = curl_resp.text
        self.content = curl_resp.content
        self.status_code = curl_resp.status_code
        self.url = str(curl_resp.url)
        self.headers = curl_resp.headers
