# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sogou 搜索引擎 - curl_cffi 隐身模式 (规避 CAPTCHA 检测).

问题: sogou 检测到 httpx 的 Python TLS 指纹后, HTTP 302 跳转到 antispider 页面,
      SearXNG 检测到后抛 SearxEngineCaptchaException (暂停 3600s)。
方案: 用 curl_cffi 模拟 Chrome TLS 指纹 (BoringSSL) 替代 httpx 发请求, 规避反爬检测。
      curl_cffi 是本地工具 (非 SaaS), 通过 BoringSSL 模拟浏览器 TLS 握手。

部署:
  1. 本文件 bind mount 到 SearXNG 容器内 searx/engines/sogou_stealth.py
     (docker-compose 启动时 cp /etc/searxng/engines/sogou_stealth.py → searx/engines/)
  2. docker-compose 启动时 pip install curl_cffi
  3. settings.yml 中 engine: sogou_stealth (替代 engine: sogou)

原理:
  - request(): 复用原 sogou URL 构建 + 用 curl_cffi 发请求 (Chrome 指纹) + 缓存响应到线程本地
  - response(): 优先用 curl_cffi 缓存的响应解析, 降级用 SearXNG httpx 响应
  - SearXNG 仍会用 httpx 发请求 (无法阻止), 但 response() 忽略 httpx 响应, 使用 curl_cffi 响应
"""

import logging
import threading
from typing import Any

# curl_cffi 可选导入 (容器启动时 pip install curl_cffi 安装)
# curl_cffi 是本地库, 通过 BoringSSL 模拟 Chrome TLS 指纹, 不调用外部 API
# 注: 不在模块级暴露 curl_requests (SearXNG 引擎加载器会扫描模块属性作为配置,
#     None 值会触发 "Missing engine config attribute" 错误), 改为函数内导入
try:
    import curl_cffi  # noqa: F401

    _HAS_CURL_CFFI = True
except ImportError:
    _HAS_CURL_CFFI = False

# 复用原 sogou 引擎的 URL 构建与结果解析逻辑
try:
    from searx.engines import sogou as _sogou

    _HAS_ORIGINAL_SOGOU = True
except ImportError:
    _HAS_ORIGINAL_SOGOU = False
    _sogou = None  # type: ignore[assignment]

# SearXNG 异常 (用于 CAPTCHA 检测)
try:
    from searx.exceptions import SearxEngineCaptchaException
except ImportError:
    # 降级: searx 包不可用时用 Exception 兜底 (不应发生在 SearXNG 容器内)
    SearxEngineCaptchaException = Exception  # type: ignore[misc, assignment]

logger = logging.getLogger(__name__)

# 线程本地存储: 缓存 curl_cffi 响应 (SearXNG 引擎在线程池中同步运行, 每线程独立)
_local = threading.local()

# ========== 引擎元数据 (复用原 sogou 配置) ==========
if _HAS_ORIGINAL_SOGOU:
    about = _sogou.about
    language = _sogou.language
    categories = _sogou.categories
    paging = _sogou.paging
    time_range_support = _sogou.time_range_support
    time_range_dict = _sogou.time_range_dict
    base_url = _sogou.base_url
else:
    # 降级: 原 sogou 模块不可用时使用默认元数据
    about = {
        "website": "https://www.sogou.com/",
        "wikidata_id": "Q7554565",
        "use_official_api": False,
        "require_api_key": False,
        "results": "HTML",
    }
    language = "zh"
    categories = ["general"]
    paging = True
    time_range_support = True
    time_range_dict = {
        "day": "inttime_day",
        "week": "inttime_week",
        "month": "inttime_month",
        "year": "inttime_year",
    }
    base_url = "https://www.sogou.com"


def request(query: str, params: dict[str, Any]) -> dict[str, Any]:
    """构建 sogou 搜索请求, 用 curl_cffi 发送 (Chrome TLS 指纹).

    SearXNG 调用此函数构建请求参数。本函数额外用 curl_cffi 发送实际 HTTP 请求,
    并将响应缓存到线程本地, response() 从缓存读取解析。
    若 curl_cffi 不可用, 降级为原 sogou 逻辑 (SearXNG 用 httpx 发请求, 可能触发 CAPTCHA)。

    Args:
        query: 搜索查询词.
        params: SearXNG 请求参数字典 (含 pageno/time_range/headers 等).

    Returns:
        更新后的 params 字典 (含 url/headers/allow_redirects).
    """
    # 清除上次请求的缓存 (防线程池复用导致脏数据)
    _local.curl_response = None

    # 1. 复用原 sogou 的 URL 构建逻辑
    if _HAS_ORIGINAL_SOGOU:
        params = _sogou.request(query, params)
    else:
        # 降级: 自行构建 sogou 搜索 URL
        from urllib.parse import urlencode

        query_params: dict[str, Any] = {
            "query": query,
            "page": params["pageno"],
        }
        if time_range_dict.get(params.get("time_range", "")):
            query_params["s_from"] = time_range_dict[params["time_range"]]
            query_params["tsn"] = 1
        params["allow_redirects"] = False
        params["url"] = f"{base_url}/web?{urlencode(query_params)}"

    # 2. curl_cffi 不可用时降级 (SearXNG 用 httpx 发请求, 可能触发 CAPTCHA)
    if not _HAS_CURL_CFFI:
        logger.warning("curl_cffi 未安装, sogou_stealth 降级为 httpx 请求 (可能触发 CAPTCHA)")
        return params

    # 3. 用 curl_cffi 发送请求 (模拟 Chrome TLS 指纹, 规避反爬检测)
    url = params["url"]
    headers = dict(params.get("headers", {}))
    try:
        from curl_cffi import requests as curl_requests

        curl_resp = curl_requests.get(
            url,
            headers=headers,
            impersonate="chrome146",  # 最新 Chrome 146 指纹 (curl_cffi 0.15.0, BoringSSL)
            timeout=10,
            allow_redirects=False,  # 不跟随重定向 (检测 302 CAPTCHA)
        )
        # 检测 302 重定向到 antispider (CAPTCHA 触发标志)
        if curl_resp.status_code == 302:
            location = curl_resp.headers.get("Location", "")
            if "antispider" in location:
                raise SearxEngineCaptchaException(
                    message="Sogou CAPTCHA (sogou_stealth: curl_cffi Chrome 指纹仍被检测)",
                    suspended_time=3600,
                )
        # 缓存 curl_cffi 响应到线程本地
        _local.curl_response = curl_resp
    except SearxEngineCaptchaException:
        # CAPTCHA 异常向上传播 (SearXNG 会暂停引擎 3600s)
        raise
    except Exception as e:  # noqa: BLE001
        # 其他异常: 降级为 httpx (SearXNG 默认网络层)
        logger.warning("curl_cffi 请求失败, 降级为 httpx: %s", e)
        _local.curl_response = None

    return params


def response(resp: Any) -> list[dict[str, Any]]:
    """解析 sogou 搜索结果.

    优先使用 curl_cffi 缓存的响应 (Chrome TLS 指纹, 无 CAPTCHA);
    若缓存不存在 (curl_cffi 不可用或请求失败), 降级使用 SearXNG httpx 响应。

    Args:
        resp: SearXNG 传入的 httpx Response 对象 (被忽略, 使用 curl_cffi 缓存).

    Returns:
        搜索结果列表 [{"title", "url", "content", "publishedDate", ...}].
    """
    # 1. 优先使用 curl_cffi 缓存的响应
    curl_resp = getattr(_local, "curl_response", None)
    if curl_resp is not None:
        # 清除缓存 (避免下次请求复用旧响应)
        _local.curl_response = None
        # 构造兼容原 sogou response() 的 Response 包装器
        wrapper = _CurlResponseWrapper(curl_resp)
        if _HAS_ORIGINAL_SOGOU:
            return _sogou.response(wrapper)
        return _parse_sogou_html(wrapper.text)

    # 2. 降级: 使用 SearXNG httpx 响应 (curl_cffi 不可用或请求失败时)
    if _HAS_ORIGINAL_SOGOU:
        return _sogou.response(resp)
    return _parse_sogou_html(resp.text)


class _CurlResponseWrapper:
    """curl_cffi 响应包装器, 兼容 SearXNG sogou 引擎的 Response 接口.

    SearXNG 的 response(resp) 期望 resp 含 text/status_code/next_request 等属性。
    本包装器将 curl_cffi 响应适配为兼容接口, 使原 sogou response() 可直接解析。

    Attributes:
        text: HTML 响应正文.
        content: HTML 响应正文字节.
        status_code: HTTP 状态码.
        url: 最终请求 URL.
        headers: 响应头字典.
        next_request: 模拟 httpx 的重定向下一请求对象 (含 url 属性), 用于 CAPTCHA 检测.
    """

    def __init__(self, curl_resp: Any) -> None:
        self.text = curl_resp.text
        self.content = curl_resp.content
        self.status_code = curl_resp.status_code
        self.url = str(curl_resp.url)
        self.headers = curl_resp.headers
        # next_request: 兼容原 sogou response() 的 CAPTCHA 检测
        # httpx 的 next_request 属性表示重定向的下一个请求; curl_cffi 用 Location 头模拟
        self.next_request: Any = None
        if curl_resp.status_code == 302:
            location = curl_resp.headers.get("Location", "")
            if location:
                # 构造简单对象模拟 httpx.Response.next_request
                self.next_request = type("_NextRequest", (), {"url": location})()


def _parse_sogou_html(html_text: str) -> list[dict[str, Any]]:
    """降级解析: 直接从 HTML 解析 sogou 搜索结果 (不依赖原 sogou 引擎模块).

    当 searx.engines.sogou 模块不可用时 (非 SearXNG 容器环境) 使用此降级解析。
    解析逻辑对标原 sogou.py 的 response() 函数。

    Args:
        html_text: sogou 搜索结果页 HTML.

    Returns:
        搜索结果列表.
    """
    results: list[dict[str, Any]] = []
    try:
        import re
        from datetime import datetime

        from lxml import html as lxml_html
        from searx.utils import extract_text

        dom = lxml_html.fromstring(html_text)
        for item in dom.xpath(
            '//div[contains(@class, "rb")] | '
            '//div[contains(@class, "vrwrap") and not(.//div[contains(@class, "special-wrap")])]'
        ):
            item_html = lxml_html.tostring(item, encoding="unicode")
            # 尝试两种结果类型 (普通结果 / 带图结果)
            title_nodes = item.xpath('.//h3[@class="pt"]/a') or item.xpath(
                './/h3[contains(@class, "vr-title")]/a'
            )
            if not title_nodes:
                continue
            title = extract_text(title_nodes[0])
            url = title_nodes[0].get("href", "")
            # sogou 链接可能是跳转链接 (/link?url=...)
            if url and url.startswith("/link?url="):
                match = re.search(r'data-url="([^"]+)"', item_html)
                url = match.group(1) if match else f"{base_url}{url}"
            # 提取摘要内容
            content_nodes = (
                item.xpath('.//div[@class="ft"]')
                or item.xpath('.//div[contains(@class, "attribute-centent")]')
                or item.xpath('.//div[contains(@class, "fz-mid space-txt")]')
            )
            content = extract_text(content_nodes[0]) if content_nodes else ""
            # 提取发布日期
            published_date = None
            date_nodes = item.xpath(".//cite") or item.xpath('.//span[@class="cite-date"]')
            if date_nodes:
                date_text = extract_text(date_nodes[0])
                date_match = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", date_text)
                if date_match:
                    try:
                        published_date = datetime.strptime(date_match.group(1), "%Y-%m-%d")
                    except (ValueError, TypeError):
                        pass
            if title and url:
                results.append(
                    {
                        "title": title,
                        "url": url,
                        "content": content,
                        "publishedDate": published_date,
                    }
                )
    except Exception as e:  # noqa: BLE001
        logger.warning("sogou_stealth 降级解析失败: %s", e)
    return results
