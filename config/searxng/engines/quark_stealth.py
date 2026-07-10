# SPDX-License-Identifier: AGPL-3.0-or-later
"""Quark 搜索引擎 - curl_cffi 隐身模式 + Cookie Warmup + 频率控制 (规避 CAPTCHA 检测).

问题: quark 在短时间 9 次请求后触发 Alibaba X5SEC CAPTCHA (滑块验证码),
      SearXNG 检测到后抛 SearxEngineCaptchaException (暂停 900s).
根因: quark CAPTCHA 不是 TLS 指纹检测, 而是请求频率 (9次/短周期) + Cookie 缺失。
      Alibaba X5SEC 需要 JS 执行环境生成 bx-ua/bx-pp, curl_cffi 无法绕过。
      但通过 Cookie Warmup + 降频 + cookie 复用计数可降低触发率。
方案: 四重防护:
      1. Cookie Warmup: 首次请求前 GET https://quark.sm.cn/ 预热 cookie (缓存 1h)
      2. Cookie 复用计数: 单 cookie 最多使用 8 次后强制刷新 (低于 9次/周期阈值)
      3. 完整浏览器 headers: Referer/Accept-Language/Sec-Ch-Ua 模拟真实浏览器
      4. 请求频率控制: 最小间隔 3 秒 (降低短周期请求密度)
      5. 降低 suspended_time: 900→60 (快速恢复重试)

注意: Alibaba X5SEC 滑块需 JS 执行环境, curl_cffi 无法完全绕过。
      若仍触发 CAPTCHA, 建议配置代理轮换 (settings.yml outgoing.proxies)。
      彻底解决需 Playwright sidecar 服务 (见 trace_4ad14970_optimization.md Section 7.3.3)。

部署:
  1. 本文件 bind mount 到 SearXNG 容器内 searx/engines/quark_stealth.py
  2. docker-compose 启动时安装 curl_cffi (见 searxng service entrypoint)
  3. settings.yml 中 quark 引擎的 engine: quark_stealth (替代 engine: quark)
"""

import logging
import threading
import time
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

# ========== 请求频率控制 (降低 X5SEC CAPTCHA 触发率) ==========
# quark 源码注释: "9 requests in a short period" 触发 CAPTCHA
# 通过进程级时间戳控制请求间隔, 避免短时间内大量请求
# 参考 baidu-serp-api 最佳实践: 单 cookie 使用不超过 8 次后主动更换
_QUARK_LAST_REQUEST_TIME: float = 0.0
_QUARK_MIN_INTERVAL = 3.0  # 最小请求间隔 3 秒 (原 2s, 提升到 3s 进一步降低触发率)
_QUARK_RATE_LOCK = threading.Lock()
_QUARK_COOKIE_USE_COUNT: int = 0  # cookie 复用计数器
_QUARK_COOKIE_MAX_USES = 8  # 单 cookie 最多使用 8 次后强制刷新 (降低 9次/周期 触发率)

# ========== Cookie Warmup 缓存 (进程级, 所有线程共享) ==========
# quark cookie 预热: 首次请求前 GET https://quark.sm.cn/ 获取 session cookie
# quark 缺少 session cookie 是 CAPTCHA 触发的根因之一 (9次/周期限制基于无 cookie 的裸请求)
_QUARK_COOKIE_CACHE: dict[str, Any] | None = None
_QUARK_COOKIE_EXPIRE: float = 0.0
_QUARK_COOKIE_TTL = 3600  # 1 小时缓存
_QUARK_LOCK = threading.Lock()

# 预热 URL
_QUARK_WARMUP_URL = "https://quark.sm.cn/"

# 完整浏览器 headers (模拟真实 Chrome 请求, 降低 CAPTCHA 触发率)
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
    "Referer": "https://quark.sm.cn/",
}


def _warmup_quark_cookie(headers: dict[str, str]) -> dict[str, str] | None:
    """预热 quark session cookie (GET https://quark.sm.cn/ 获取 Set-Cookie).

    quark 缺少 session cookie 时, 9 次请求即触发 Alibaba X5SEC CAPTCHA。
    预热 cookie 可降低触发率 (有 cookie 的请求被视为"回访用户")。
    单 cookie 最多使用 _QUARK_COOKIE_MAX_USES 次后强制刷新 (降低 9次/周期 触发率)。

    Args:
        headers: 请求头字典 (用于 warmup 请求).

    Returns:
        quark cookie 字典, 或 None (warmup 失败时).
    """
    global _QUARK_COOKIE_CACHE, _QUARK_COOKIE_EXPIRE, _QUARK_COOKIE_USE_COUNT

    # 1. 检查缓存是否有效 (TTL 未过期 + 使用次数未超限)
    now = time.time()
    if (
        _QUARK_COOKIE_CACHE is not None
        and now < _QUARK_COOKIE_EXPIRE
        and _QUARK_COOKIE_USE_COUNT < _QUARK_COOKIE_MAX_USES
    ):
        _QUARK_COOKIE_USE_COUNT += 1
        return _QUARK_COOKIE_CACHE

    # 2. 加锁预热 (避免多线程同时 warmup)
    with _QUARK_LOCK:
        # double-check (可能其他线程已 warmup 完成)
        now = time.time()
        if (
            _QUARK_COOKIE_CACHE is not None
            and now < _QUARK_COOKIE_EXPIRE
            and _QUARK_COOKIE_USE_COUNT < _QUARK_COOKIE_MAX_USES
        ):
            _QUARK_COOKIE_USE_COUNT += 1
            return _QUARK_COOKIE_CACHE

        # 3. warmup 请求 (用 curl_cffi 或降级 httpx)
        warmup_headers = {**headers, **_BROWSER_HEADERS}
        try:
            if _HAS_CURL_CFFI:
                from curl_cffi import requests as curl_requests

                resp = curl_requests.get(
                    _QUARK_WARMUP_URL,
                    headers=warmup_headers,
                    impersonate="chrome146",
                    timeout=10,
                    allow_redirects=True,
                )
            else:
                from searx.network import get as http_get  # type: ignore[import-not-found]

                resp = http_get(_QUARK_WARMUP_URL, headers=warmup_headers, timeout=10)

            # 4. 提取 cookie
            cookies = dict(resp.cookies.items())
            if cookies:
                _QUARK_COOKIE_CACHE = cookies
                _QUARK_COOKIE_EXPIRE = now + _QUARK_COOKIE_TTL
                _QUARK_COOKIE_USE_COUNT = 1  # 重置计数器 (本次算第 1 次使用)
                logger.info(
                    "quark_stealth: cookie warmup 成功 (%d cookies, TTL=%ds, max_uses=%d)",
                    len(cookies),
                    _QUARK_COOKIE_TTL,
                    _QUARK_COOKIE_MAX_USES,
                )
                return cookies
            logger.warning("quark_stealth: cookie warmup 未获取到 cookie")
        except Exception as e:  # noqa: BLE001
            logger.warning("quark_stealth: cookie warmup 失败: %s", e)

        return None


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
    """构建 quark 搜索请求, 用 curl_cffi 发送 (Chrome TLS 指纹 + Cookie Warmup).

    三重防护降低 CAPTCHA 触发率:
    1. Cookie Warmup: 预热 session cookie (quark 9次/周期限制基于无 cookie 的裸请求)
    2. 完整浏览器 headers: Referer/Accept-Language/Sec-Ch-Ua 模拟真实浏览器
    3. curl_cffi: TLS 指纹模拟 (额外防护)

    若 curl_cffi 不可用, 降级为原 quark 逻辑 (cookie+headers 仍注入到 httpx 请求).

    Args:
        query: 搜索查询词.
        params: SearXNG 请求参数字典.

    Returns:
        更新后的 params 字典.
    """
    _local.curl_response = None

    # 0. 请求频率控制: 确保最小间隔 2 秒 (降低 9次/周期 X5SEC 触发率)
    global _QUARK_LAST_REQUEST_TIME
    with _QUARK_RATE_LOCK:
        now = time.time()
        elapsed = now - _QUARK_LAST_REQUEST_TIME
        if elapsed < _QUARK_MIN_INTERVAL:
            sleep_time = _QUARK_MIN_INTERVAL - elapsed
            time.sleep(sleep_time)
        _QUARK_LAST_REQUEST_TIME = time.time()

    # 1. 复用原 quark 的 URL 构建逻辑
    if _HAS_ORIGINAL_QUARK:
        params = _quark.request(query, params)

    # 2. Cookie Warmup: 预热 quark session cookie
    base_headers = dict(params.get("headers", {}))
    warmup_cookies = _warmup_quark_cookie(base_headers)
    if warmup_cookies:
        existing_cookies = params.get("cookies") or {}
        merged = {**warmup_cookies, **existing_cookies}
        params["cookies"] = merged

    # 3. 注入完整浏览器 headers
    for key, value in _BROWSER_HEADERS.items():
        if key not in params.get("headers", {}):
            params.setdefault("headers", {})[key] = value

    # 4. curl_cffi 不可用时降级 (cookie+headers 已注入到 httpx 请求)
    if not _HAS_CURL_CFFI:
        logger.warning("curl_cffi 未安装, quark_stealth 降级为 httpx (cookie warmup 已生效)")
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
            impersonate="chrome146",  # Chrome 146 TLS 指纹
            timeout=10,
            allow_redirects=True,  # quark CAPTCHA 在 HTML 内容中检测, 需跟随重定向
        )
        # quark CAPTCHA 在 HTML 内容中检测 (Alibaba X5SEC pattern)
        if _HAS_ORIGINAL_QUARK and _quark.is_alibaba_captcha(curl_resp.text):
            # suspended_time 降到 60s (配合频率控制 + cookie warmup 快速恢复)
            raise SearxEngineCaptchaException(
                message="Quark Alibaba CAPTCHA (quark_stealth: cookie warmup + 频率控制仍被检测, "
                "X5SEC 需 JS 执行环境, 60s 后自动重试)",
                suspended_time=60,  # 1 分钟 (原 900s, 降到 60s 快速恢复)
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
