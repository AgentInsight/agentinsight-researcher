# SPDX-License-Identifier: AGPL-3.0-or-later
"""Baidu 搜索引擎 - curl_cffi 隐身模式 (规避 CAPTCHA 检测).

问题: baidu 检测到 httpx 的 Python TLS 指纹后, 重定向到 wappass.baidu.com CAPTCHA 页面,
      SearXNG 检测到后抛 SearxEngineCaptchaException (暂停服务).
方案: 用 curl_cffi 模拟 Chrome TLS 指纹 (BoringSSL) 替代 httpx 发请求, 规避反爬检测。
      curl_cffi 是本地工具 (非 SaaS), 通过 BoringSSL 模拟浏览器 TLS 握手。

部署:
  1. 本文件 bind mount 到 SearXNG 容器内 searx/engines/baidu_stealth.py
  2. docker-compose 启动时安装 curl_cffi (见 searxng service command)
  3. settings.yml 中 baidu 引擎的 engine: baidu_stealth (替代 engine: baidu)

原理:
  - request(): 复用原 baidu URL 构建 + 用 curl_cffi 发请求 (Chrome 指纹) + 缓存响应到线程本地
  - response(): 优先用 curl_cffi 缓存的响应解析, 降级用 SearXNG httpx 响应
  - SearXNG 仍会用 httpx 发请求 (无法阻止), 但 response() 忽略 httpx 响应, 使用 curl_cffi 响应
"""

import logging
import threading
from typing import Any

# curl_cffi 可选导入 (容器启动时 pip install curl_cffi 安装)
# 注: 不在模块级暴露 curl_requests (SearXNG 引擎加载器会扫描模块属性作为配置,
#     None 值会触发 "Missing engine config attribute" 错误), 改为函数内导入
try:
    import curl_cffi  # noqa: F401

    _HAS_CURL_CFFI = True
except ImportError:
    _HAS_CURL_CFFI = False

# 复用原 baidu 引擎的 URL 构建与结果解析逻辑
try:
    from searx.engines import baidu as _baidu

    _HAS_ORIGINAL_BAIDU = True
except ImportError:
    _HAS_ORIGINAL_BAIDU = False
    _baidu = None  # type: ignore[assignment]

# SearXNG 异常 (用于 CAPTCHA 检测)
try:
    from searx.exceptions import SearxEngineCaptchaException
except ImportError:
    SearxEngineCaptchaException = Exception  # type: ignore[misc, assignment]

logger = logging.getLogger(__name__)

# 线程本地存储: 缓存 curl_cffi 响应 (SearXNG 引擎在线程池中同步运行, 每线程独立)
_local = threading.local()

# ========== 引擎元数据 (复用原 baidu 配置) ==========
if _HAS_ORIGINAL_BAIDU:
    about = _baidu.about
    language = _baidu.language
    paging = _baidu.paging
    categories = _baidu.categories
    results_per_page = _baidu.results_per_page
    baidu_category = _baidu.baidu_category
    time_range_support = _baidu.time_range_support
    time_range_dict = _baidu.time_range_dict
    # 复用 setup/init 函数 (baidu 有 EngineCache 用于图片搜索 cookie 缓存)
    setup = _baidu.setup
    init = _baidu.init
else:
    about = {
        "website": "https://www.baidu.com",
        "wikidata_id": "Q14772",
        "use_official_api": False,
        "require_api_key": False,
        "results": "JSON",
    }
    language = "zh"
    paging = True
    categories = []
    results_per_page = 10
    baidu_category = "general"
    time_range_support = True
    time_range_dict = {"day": 86400, "week": 604800, "month": 2592000, "year": 31536000}


def request(query: str, params: dict[str, Any]) -> dict[str, Any]:
    """构建 baidu 搜索请求, 用 curl_cffi 发送 (Chrome TLS 指纹).

    若 curl_cffi 不可用, 降级为原 baidu 逻辑 (SearXNG 用 httpx 发请求, 可能触发 CAPTCHA).

    Args:
        query: 搜索查询词.
        params: SearXNG 请求参数字典 (含 pageno/time_range/headers 等).

    Returns:
        更新后的 params 字典 (含 url/headers/allow_redirects).
    """
    # 清除上次请求的缓存 (防线程池复用导致脏数据)
    _local.curl_response = None

    # 1. 复用原 baidu 的 URL 构建逻辑 (含 cookie 缓存等)
    if _HAS_ORIGINAL_BAIDU:
        params = _baidu.request(query, params)

    # 2. curl_cffi 不可用时降级 (SearXNG 用 httpx 发请求, 可能触发 CAPTCHA)
    if not _HAS_CURL_CFFI:
        logger.warning("curl_cffi 未安装, baidu_stealth 降级为 httpx 请求 (可能触发 CAPTCHA)")
        return params

    # 3. 用 curl_cffi 发送请求 (模拟 Chrome TLS 指纹, 规避反爬检测)
    url = params["url"]
    headers = dict(params.get("headers", {}))
    cookies = params.get("cookies")
    try:
        from curl_cffi import requests as curl_requests

        curl_resp = curl_requests.get(
            url,
            headers=headers,
            cookies=cookies,
            impersonate="chrome",  # 模拟 Chrome TLS 指纹 (BoringSSL)
            timeout=10,
            allow_redirects=False,  # 不跟随重定向 (检测 CAPTCHA 重定向)
        )
        # 检测重定向到 wappass.baidu.com (CAPTCHA 触发标志)
        if curl_resp.status_code in (301, 302):
            location = curl_resp.headers.get("Location", "")
            if "wappass.baidu.com/static/captcha" in location:
                raise SearxEngineCaptchaException(
                    message="Baidu CAPTCHA (baidu_stealth: curl_cffi Chrome 指纹仍被检测)",
                    suspended_time=3600,
                )
        # 缓存 curl_cffi 响应到线程本地
        _local.curl_response = curl_resp
    except SearxEngineCaptchaException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("curl_cffi 请求失败, 降级为 httpx: %s", e)
        _local.curl_response = None

    return params


def response(resp: Any) -> list[dict[str, Any]]:
    """解析 baidu 搜索结果.

    优先使用 curl_cffi 缓存的响应 (Chrome TLS 指纹, 无 CAPTCHA);
    若缓存不存在 (curl_cffi 不可用或请求失败), 降级使用 SearXNG httpx 响应.

    Args:
        resp: SearXNG 传入的 httpx Response 对象 (被忽略, 使用 curl_cffi 缓存).

    Returns:
        搜索结果列表.
    """
    # 1. 优先使用 curl_cffi 缓存的响应
    curl_resp = getattr(_local, "curl_response", None)
    if curl_resp is not None:
        _local.curl_response = None
        wrapper = _CurlResponseWrapper(curl_resp)
        if _HAS_ORIGINAL_BAIDU:
            return _baidu.response(wrapper)
        return _parse_baidu_json(wrapper.text)

    # 2. 降级: 使用 SearXNG httpx 响应
    if _HAS_ORIGINAL_BAIDU:
        return _baidu.response(resp)
    return _parse_baidu_json(resp.text)


class _CurlResponseWrapper:
    """curl_cffi 响应包装器, 兼容 SearXNG baidu 引擎的 Response 接口.

    baidu response() 检查 resp.headers.get('Location') 和 resp.text,
    本包装器将 curl_cffi 响应适配为兼容接口.
    """

    def __init__(self, curl_resp: Any) -> None:
        self.text = curl_resp.text
        self.content = curl_resp.content
        self.status_code = curl_resp.status_code
        self.url = str(curl_resp.url)
        self.headers = curl_resp.headers


def _parse_baidu_json(text: str) -> list[dict[str, Any]]:
    """降级解析: 直接从 JSON 解析 baidu 搜索结果 (不依赖原 baidu 引擎模块)."""
    import json
    from datetime import datetime
    from html import unescape

    results: list[dict[str, Any]] = []
    try:
        data = json.loads(text, strict=False)
        if data.get("antiFlag") == 1:
            raise SearxEngineCaptchaException(
                message=data.get("message", "Forbid spider access"),
                suspended_time=3600,
            )
        for entry in data.get("feed", {}).get("entry", []):
            if not entry.get("title") or not entry.get("url"):
                continue
            published_date = None
            if entry.get("time"):
                try:
                    published_date = datetime.fromtimestamp(entry["time"])
                except (ValueError, TypeError):
                    pass
            results.append(
                {
                    "title": unescape(entry["title"]),
                    "url": entry["url"],
                    "content": unescape(entry.get("abs", "")),
                    "publishedDate": published_date,
                }
            )
    except SearxEngineCaptchaException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("baidu_stealth 降级解析失败: %s", e)
    return results
