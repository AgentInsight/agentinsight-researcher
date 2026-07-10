# SPDX-License-Identifier: AGPL-3.0-or-later
"""Baidu 搜索引擎 - curl_cffi 隐身模式 + Cookie Warmup (规避 CAPTCHA 检测).

问题: baidu 检测到 Python httpx 请求 (无 BAIDUID cookie + IP 频率) 后,
      重定向到 wappass.baidu.com CAPTCHA 页面, SearXNG 抛 SearxEngineCaptchaException.
根因: baidu CAPTCHA 不是 TLS 指纹检测, 而是 IP 频率 + Cookie 缺失 + antiFlag 检测。
      curl_cffi 的 TLS 指纹模拟对 baidu 无效 (baidu 不做 JA3 校验)。
方案: 三重防护:
      1. Cookie Warmup: 首次请求前 GET https://www.baidu.com/s?wd=test 预热完整 cookie 链
         (BAIDUID + H_PS_PSSID + H_PS_645EC 等, 参考 baidu-serp-api 项目, 缓存 1h)
      2. 完整浏览器 headers: Referer/Accept-Language/Accept 模拟真实浏览器
      3. curl_cffi: 保留 TLS 指纹模拟作为额外防护 (虽然 baidu 不做 JA3, 但无害)

部署:
  1. 本文件 bind mount 到 SearXNG 容器内 searx/engines/baidu_stealth.py
  2. docker-compose 启动时安装 curl_cffi (见 searxng service entrypoint)
  3. settings.yml 中 baidu 引擎的 engine: baidu_stealth (替代 engine: baidu)
"""

import logging
import threading
import time
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

# ========== Cookie Warmup 缓存 (进程级, 所有线程共享) ==========
# BAIDUID cookie 预热: 首次请求前 GET https://www.baidu.com/ 获取 BAIDUID
# baidu general 搜索缺少 BAIDUID cookie 是 CAPTCHA 触发的主要根因之一
# 参考 SearXNG baidu.py get_image_cookies() 的实现模式 (仅 images 类别有, general 缺失)
_BAIDUID_COOKIE_CACHE: dict[str, Any] | None = None
_BAIDUID_COOKIE_EXPIRE: float = 0.0
_BAIDUID_COOKIE_TTL = 3600  # 1 小时缓存 (与 SearXNG baidu.py COOKIE_CACHE_EXPIRATION_SECONDS 一致)
_BAIDUID_LOCK = threading.Lock()

# 预热 URL: 用搜索页 (而非首页) 获取更完整的 cookie 链
# 参考 baidu-serp-api 项目: 搜索页会返回 BAIDUID + H_PS_PSSID + H_PS_645EC 等完整 cookie
# 首页仅返回 BAIDUID, 缺少 H_PS_PSSID (会话 ID) 和 H_PS_645EC (与 rsv_t 同步的安全字段)
_BAIDU_WARMUP_URL = "https://www.baidu.com/s?wd=test"

# 完整浏览器 headers (模拟真实 Chrome 请求, 降低 antiFlag 触发率)
_BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Ch-Ua": '"Chromium";v="146", "Not?A_Brand";v="99", "Google Chrome";v="146"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.baidu.com/",
}


def _warmup_baiduid_cookie(headers: dict[str, str]) -> dict[str, str] | None:
    """预热 BAIDUID cookie (GET https://www.baidu.com/ 获取 Set-Cookie).

    baidu general 搜索缺少 BAIDUID cookie 是 CAPTCHA 触发的主要根因。
    SearXNG baidu.py 仅为 images 类别做 cookie warmup, general 缺失。
    本函数为 general 搜索补充 cookie warmup, 参考 get_image_cookies() 模式。

    Args:
        headers: 请求头字典 (用于 warmup 请求).

    Returns:
        BAIDUID cookie 字典, 或 None (warmup 失败时).
    """
    global _BAIDUID_COOKIE_CACHE, _BAIDUID_COOKIE_EXPIRE

    # 1. 检查缓存是否有效
    now = time.time()
    if _BAIDUID_COOKIE_CACHE is not None and now < _BAIDUID_COOKIE_EXPIRE:
        return _BAIDUID_COOKIE_CACHE

    # 2. 加锁预热 (避免多线程同时 warmup)
    with _BAIDUID_LOCK:
        # double-check (可能其他线程已 warmup 完成)
        now = time.time()
        if _BAIDUID_COOKIE_CACHE is not None and now < _BAIDUID_COOKIE_EXPIRE:
            return _BAIDUID_COOKIE_CACHE

        # 3. warmup 请求 (用 curl_cffi 或降级 httpx)
        warmup_headers = {**headers, **_BROWSER_HEADERS}
        try:
            if _HAS_CURL_CFFI:
                from curl_cffi import requests as curl_requests

                resp = curl_requests.get(
                    _BAIDU_WARMUP_URL,
                    headers=warmup_headers,
                    impersonate="chrome146",
                    timeout=10,
                    allow_redirects=True,
                )
            else:
                # 降级: 用 SearXNG 的 httpx (通过 searx.network)
                from searx.network import get as http_get  # type: ignore[import-not-found]

                resp = http_get(_BAIDU_WARMUP_URL, headers=warmup_headers, timeout=10)

            # 4. 提取 BAIDUID cookie
            cookies = dict(resp.cookies.items())
            if cookies:
                _BAIDUID_COOKIE_CACHE = cookies
                _BAIDUID_COOKIE_EXPIRE = now + _BAIDUID_COOKIE_TTL
                logger.info(
                    "baidu_stealth: BAIDUID cookie warmup 成功 (%d cookies, TTL=%ds)",
                    len(cookies),
                    _BAIDUID_COOKIE_TTL,
                )
                return cookies
            logger.warning("baidu_stealth: BAIDUID cookie warmup 未获取到 cookie")
        except Exception as e:  # noqa: BLE001
            logger.warning("baidu_stealth: BAIDUID cookie warmup 失败: %s", e)

        return None


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
    """构建 baidu 搜索请求, 用 curl_cffi 发送 (Chrome TLS 指纹 + Cookie Warmup).

    三重防护降低 CAPTCHA 触发率:
    1. Cookie Warmup: 预热 BAIDUID cookie (baidu general 缺少 cookie 是 CAPTCHA 主因)
    2. 完整浏览器 headers: Referer/Accept-Language/Sec-Ch-Ua 模拟真实浏览器
    3. curl_cffi: TLS 指纹模拟 (额外防护, 虽然 baidu 不做 JA3 校验)

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

    # 2. Cookie Warmup: 预热 BAIDUID cookie (baidu general 缺失 cookie 是 CAPTCHA 主因)
    #    SearXNG baidu.py 仅为 images 类别做 warmup, general 缺失 — 本处补充
    base_headers = dict(params.get("headers", {}))
    warmup_cookies = _warmup_baiduid_cookie(base_headers)
    if warmup_cookies:
        # 合并 warmup cookie 与已有 cookie (已有优先, 避免覆盖 images cookie)
        existing_cookies = params.get("cookies") or {}
        merged = {**warmup_cookies, **existing_cookies}
        params["cookies"] = merged

    # 3. 注入完整浏览器 headers (降低 antiFlag 触发率)
    for key, value in _BROWSER_HEADERS.items():
        if key not in params.get("headers", {}):
            params.setdefault("headers", {})[key] = value

    # 4. curl_cffi 不可用时降级 (SearXNG 用 httpx 发请求, cookie+headers 已注入)
    if not _HAS_CURL_CFFI:
        logger.warning("curl_cffi 未安装, baidu_stealth 降级为 httpx (cookie warmup 已生效)")
        return params

    # 5. 用 curl_cffi 发送请求 (Chrome TLS 指纹 + warmup cookie + 浏览器 headers)
    url = params["url"]
    headers = dict(params.get("headers", {}))
    cookies = params.get("cookies")
    try:
        from curl_cffi import requests as curl_requests

        curl_resp = curl_requests.get(
            url,
            headers=headers,
            cookies=cookies,
            impersonate="chrome146",  # Chrome 146 TLS 指纹 (额外防护)
            timeout=10,
            allow_redirects=False,  # 不跟随重定向 (检测 CAPTCHA 重定向)
        )
        # 检测重定向到 wappass.baidu.com (CAPTCHA 触发标志)
        if curl_resp.status_code in (301, 302):
            location = curl_resp.headers.get("Location", "")
            if "wappass.baidu.com/static/captcha" in location:
                # 降低 suspended_time: 3600→300 (配合 cookie warmup 快速重试)
                raise SearxEngineCaptchaException(
                    message="Baidu CAPTCHA (baidu_stealth: cookie warmup + curl_cffi 仍被检测, "
                    "可能是 IP 频率限制, 建议配置代理轮换)",
                    suspended_time=300,  # 5 分钟 (原 3600s 过于激进)
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
