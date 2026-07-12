#!/usr/bin/env python3
"""SearXNG 引擎源码 patch 脚本 (构建时执行).

修复 CrossRef / PubMed 429 Too Many Requests 限流问题 + image_proxy HTTP 协议问题:
- CrossRef: 添加 mailto 参数进入 "Polite Pool" (从环境变量 CROSSREF_MAILTO 读取)
  https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api/
- PubMed: 添加 email 参数符合 NCBI E-utilities 速率限制建议 (从环境变量 PUBMED_EMAIL 读取)
  https://www.ncbi.nlm.nih.gov/books/NBK25497/
- network.py: 为所有网络启用 HTTP 协议 (修复 image_proxy UnsupportedProtocol)
- webapp.py: 将 image_proxy 关闭时的 httpx.ReadError 从 ERROR 降级为 DEBUG
  (客户端断开后 resp.close() drain 剩余数据失败是良性异常, 不应记录为 ERROR)

幂等设计: 已 patch 的文件重复执行不会重复添加, 安全可重复构建.
"""
from __future__ import annotations

import sys
from pathlib import Path

ENGINES_DIR = Path("/usr/local/searxng/searx/engines")
NETWORK_DIR = Path("/usr/local/searxng/searx/network")
WEBAPP_DIR = Path("/usr/local/searxng/searx")


def ensure_import_os(content: str, filename: str) -> tuple[str, bool]:
    """确保模块顶部 import 区域包含 'import os', 跳过 docstring.

    问题: SearXNG 引擎文件的 docstring 可能包含以 'from ' 或 'import ' 开头的行
    (如 pubmed.py 的 'from MEDLINE, life science journals...'),
    导致简单匹配会将 import os 插入到 docstring 中间.

    修复:
    1. 清理 docstring 中误插入的 import os 行
    2. 跳过模块 docstring (三引号区域), 在真正的 import 区域插入

    返回: (修改后的 content, 是否修改)
    """
    lines = content.split("\n")
    in_docstring = False
    has_import_os = False
    lines_to_remove: list[int] = []  # docstring 中误插入的 import os 行索引

    # 第 1 趟: 检查顶层 import os + 标记 docstring 中的错误 import os
    for i, line in enumerate(lines):
        # 跟踪三引号 docstring 状态
        if '"""' in line:
            count = line.count('"""')
            if count == 1:
                in_docstring = not in_docstring
            # count == 2 表示单行 docstring, 不改变状态
            continue
        if in_docstring:
            # docstring 内的 import os 为误插入, 删除
            if line.strip() == "import os":
                lines_to_remove.append(i)
                print(f"[SearXNG][PATCH] {filename}: 清理 docstring 中误插入的 import os (第 {i + 1} 行)")
            continue
        # 检查是否已有顶层 import os (行首, 非缩进)
        if line == "import os" or line.startswith("import os ") or line.startswith("import os,"):
            has_import_os = True
            break

    # 清理 docstring 中误插入的 import os 行 (从后往前删, 避免索引偏移)
    for idx in reversed(lines_to_remove):
        del lines[idx]
    content = "\n".join(lines)

    if has_import_os:
        if lines_to_remove:
            print(f"[SearXNG][PATCH] {filename}: 已包含 import os, 清理了 {len(lines_to_remove)} 行 docstring 误插入")
            return content, True
        print(f"[SearXNG][PATCH] {filename}: 已包含 import os, 跳过")
        return content, False

    # 第 2 趟: 在 docstring 之后的第一个 import 区域插入
    lines = content.split("\n")
    in_docstring = False
    for i, line in enumerate(lines):
        if '"""' in line:
            count = line.count('"""')
            if count == 1:
                in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        # 在第一个顶层 import/from 语句后插入
        if line.startswith("import ") or line.startswith("from "):
            lines.insert(i + 1, "import os")
            new_content = "\n".join(lines)
            print(f"[SearXNG][PATCH] {filename}: 添加 import os (第 {i + 2} 行)")
            return new_content, True

    print(f"[SearXNG][PATCH][WARNING] {filename}: 未找到 import 区域, 无法添加 import os", file=sys.stderr)
    return content, False


def patch_crossref() -> bool:
    """Patch crossref.py: 添加 mailto 参数 (从环境变量 CROSSREF_MAILTO 读取) + 修复 KeyError: 'type'.

    KeyError 根因: 部分 CrossRef 记录不含 "type" 字段, 直接 record["type"] 会抛异常.
    修复: 用 record.get("type") 替代, 缺失 type 的记录跳过.
    """
    path = ENGINES_DIR / "crossref.py"
    if not path.exists():
        print("[SearXNG][PATCH] 跳过 crossref.py (文件不存在)", file=sys.stderr)
        return False

    content = path.read_text(encoding="utf-8")
    changed = False

    # 1. 添加 import os (用于读取环境变量, 跳过 docstring)
    content, os_changed = ensure_import_os(content, "crossref.py")
    changed = os_changed or changed

    # 2. 添加 mailto 参数 (从环境变量 CROSSREF_MAILTO 读取)
    #    清理硬编码邮箱 patch, 改为环境变量
    old_mailto = '        "mailto": "agentinsightcn@gmail.com",'
    new_mailto = '        "mailto": os.environ.get("CROSSREF_MAILTO", ""),'
    if old_mailto in content:
        content = content.replace(old_mailto, new_mailto, 1)
        changed = True
        print("[SearXNG][PATCH] crossref.py: 清理旧硬编码 mailto -> 环境变量版本")

    if "CROSSREF_MAILTO" not in content:
        marker = '"offset": 20 * (params["pageno"] - 1),'
        if marker in content:
            content = content.replace(
                marker,
                f'{marker}\n{new_mailto}',
                1,
            )
            changed = True
            print("[SearXNG][PATCH] crossref.py: 添加 mailto (从环境变量 CROSSREF_MAILTO 读取)")
        else:
            print("[SearXNG][PATCH][WARNING] crossref.py 未找到 mailto marker, 跳过", file=sys.stderr)
    else:
        print("[SearXNG][PATCH] crossref.py 已包含 CROSSREF_MAILTO, 跳过")

    # 3. 修复 KeyError: 'type' (部分 CrossRef 记录不含 type 字段)
    if 'record["type"]' in content:
        content = content.replace('record["type"]', 'record.get("type")')
        changed = True
        print("[SearXNG][PATCH] crossref.py: 修复 KeyError 'type' (record['type'] -> record.get('type'))")
    else:
        print("[SearXNG][PATCH] crossref.py 已修复 type 访问, 跳过")

    if changed:
        path.write_text(content, encoding="utf-8")
        print("[SearXNG][PATCH] crossref.py patch 完成")
    return True


def patch_pubmed() -> bool:
    """Patch pubmed.py: 在 esearch 和 efetch 两个 urlencode 添加 email 参数 (从环境变量 PUBMED_EMAIL 读取)."""
    path = ENGINES_DIR / "pubmed.py"
    if not path.exists():
        print("[SearXNG][PATCH] 跳过 pubmed.py (文件不存在)", file=sys.stderr)
        return False

    content = path.read_text(encoding="utf-8")
    changed = False

    # 1. 添加 import os (用于读取环境变量, 跳过 docstring)
    content, os_changed = ensure_import_os(content, "pubmed.py")
    changed = os_changed or changed

    # 2. 添加 email 参数 (从环境变量 PUBMED_EMAIL 读取)
    #    清理硬编码邮箱 patch, 改为环境变量
    old_email = '            "email": "agentinsightcn@gmail.com",'
    new_email = '            "email": os.environ.get("PUBMED_EMAIL", ""),'
    if old_email in content:
        content = content.replace(old_email, new_email)
        changed = True
        print("[SearXNG][PATCH] pubmed.py: 清理旧硬编码 email -> 环境变量版本")

    if "PUBMED_EMAIL" not in content:
        # esearch: 在 "hits": page_size, 后插入 email
        esearch_marker = '"hits": page_size,'
        # efetch: 在 "id": ",".join(pmids), 后插入 email
        efetch_marker = '"id": ",".join(pmids),'

        if esearch_marker in content and efetch_marker in content:
            content = content.replace(
                esearch_marker,
                f'{esearch_marker}\n{new_email}',
                1,
            ).replace(
                efetch_marker,
                f'{efetch_marker}\n{new_email}',
                1,
            )
            changed = True
            print("[SearXNG][PATCH] pubmed.py: 添加 email (从环境变量 PUBMED_EMAIL 读取, esearch + efetch)")
        else:
            print("[SearXNG][PATCH][WARNING] pubmed.py 未找到 email marker, 跳过", file=sys.stderr)
    else:
        print("[SearXNG][PATCH] pubmed.py 已包含 PUBMED_EMAIL, 跳过")

    if changed:
        path.write_text(content, encoding="utf-8")
        print("[SearXNG][PATCH] pubmed.py patch 完成")
    return True


def patch_network_image_proxy_http() -> bool:
    """Patch network.py: 为所有网络启用 HTTP 协议.

    根因: network.py 的 default_params 硬编码 enable_http=False,
    导致 default 网络和 image_proxy 网络都不支持 HTTP 协议.
    当 image_proxy 请求 HTTP 图片 URL 时, get_context_network() 可能返回
    default 网络 (若 image_proxy 网络未正确初始化), 抛 UnsupportedProtocol.

    修复: 将 default_params 中的 enable_http 改为 True,
    同时为 image_proxy_params 显式设置 enable_http=True (双保险).
    这样所有网络都支持 HTTP 协议.
    """
    path = NETWORK_DIR / "network.py"
    if not path.exists():
        print("[SearXNG][PATCH] 跳过 network.py (文件不存在)", file=sys.stderr)
        return False

    content = path.read_text(encoding="utf-8")
    changed = False

    # 1. 修改 default_params 中的 enable_http: False -> True
    #    这是根因修复: 所有继承 default_params 的网络都会启用 HTTP
    old_default = "        'enable_http': False,"
    new_default = "        'enable_http': True,"
    if old_default in content:
        content = content.replace(old_default, new_default, 1)
        changed = True
        print("[SearXNG][PATCH] network.py: default_params enable_http False -> True (根因修复)")
    elif new_default in content:
        print("[SearXNG][PATCH] network.py: default_params enable_http 已是 True, 跳过")
    else:
        print("[SearXNG][PATCH][WARNING] network.py 未找到 default_params enable_http marker, 跳过", file=sys.stderr)

    # 2. 为 image_proxy_params 显式设置 enable_http=True (双保险)
    marker = "        image_proxy_params['enable_http'] = True"
    if marker not in content:
        target = "        image_proxy_params['enable_http2'] = False"
        if target in content:
            content = content.replace(target, f"{target}\n{marker}", 1)
            changed = True
            print("[SearXNG][PATCH] network.py: image_proxy_params enable_http=True (双保险)")
    else:
        print("[SearXNG][PATCH] network.py: image_proxy_params enable_http 已存在, 跳过")

    # 3. 清理错误缩进的 patch
    bad_marker = "image_proxy_params['enable_http'] = True"
    if bad_marker in content and marker not in content:
        content = content.replace(f"\n{bad_marker}\n", "\n", 1)
        changed = True
        print("[SearXNG][PATCH] network.py: 清理错误缩进的 image_proxy enable_http")

    if changed:
        path.write_text(content, encoding="utf-8")
    return True


def patch_github_api_token() -> bool:
    """Patch github.py: 添加 Authorization 头 (从环境变量 GITHUB_TOKEN 读取).

    根因: SearXNG github 引擎 require_api_key=False, 不自动注入 API Key.
    未认证请求 GitHub API 速率限制为 10 次/分钟, 容易触发 403.
    认证后速率限制提升到 5000 次/小时.

    修复: 在 request() 中添加 Authorization: token {GITHUB_TOKEN} 头.
    """
    path = ENGINES_DIR / "github.py"
    if not path.exists():
        print("[SearXNG][PATCH] 跳过 github.py (文件不存在)", file=sys.stderr)
        return False

    content = path.read_text(encoding="utf-8")
    changed = False

    # 1. 添加 import os (跳过 docstring)
    content, os_changed = ensure_import_os(content, "github.py")
    changed = os_changed or changed

    # 2. 在 request() 中添加 Authorization 头
    if "GITHUB_TOKEN" not in content:
        marker = "    params['headers']['Accept'] = accept_header"
        new_code = (
            "    params['headers']['Accept'] = accept_header\n"
            "    # 添加 GitHub API Token 认证 (从环境变量 GITHUB_TOKEN 读取)\n"
            "    # 未认证速率限制 10 次/分钟, 认证后 5000 次/小时\n"
            "    _github_token = os.environ.get('GITHUB_TOKEN', '')\n"
            "    if _github_token:\n"
            "        params['headers']['Authorization'] = f'token {_github_token}'"
        )
        if marker in content:
            content = content.replace(marker, new_code, 1)
            changed = True
            print("[SearXNG][PATCH] github.py: 添加 Authorization 头 (从环境变量 GITHUB_TOKEN 读取)")
        else:
            print("[SearXNG][PATCH][WARNING] github.py 未找到 Accept header marker, 跳过", file=sys.stderr)
    else:
        print("[SearXNG][PATCH] github.py 已包含 GITHUB_TOKEN, 跳过")

    if changed:
        path.write_text(content, encoding="utf-8")
    return True


def patch_webapp_image_proxy_close_error() -> bool:
    """Patch webapp.py: 将 image_proxy 的 HTTP 异常从 ERROR 降级.

    降级两类日志:
    1. image_proxy 请求失败 (httpx.ReadTimeout/ConnectError 等):
       logger.exception('HTTP error') -> logger.warning('HTTP error: %s', e)
       原因: image_proxy 请求外部图片 URL 失败是常见的 (图片服务器慢/不可达/SSL 错误),
       不应记录为 ERROR + 完整 traceback, 降级为 WARNING (保留可见性, 无 traceback).

    2. image_proxy 关闭时 resp.close() drain 失败 (httpx.ReadError):
       logger.exception('HTTP error on closing') -> logger.debug('HTTP error on closing: %s', e)
       原因: 客户端断开后关闭上游流时的良性异常, 降级为 DEBUG.
    """
    path = WEBAPP_DIR / "webapp.py"
    if not path.exists():
        print("[SearXNG][PATCH] 跳过 webapp.py (文件不存在)", file=sys.stderr)
        return False

    content = path.read_text(encoding="utf-8")
    changed = False

    # 1. 修复 image_proxy 请求失败的 logger.exception('HTTP error') -> logger.warning
    old_request_error = "    except httpx.HTTPError:\n        logger.exception('HTTP error')\n        return '', 400"
    new_request_error = "    except httpx.HTTPError as e:\n        logger.warning('HTTP error: %s', e)\n        return '', 400"
    if old_request_error in content:
        content = content.replace(old_request_error, new_request_error, 1)
        changed = True
        print("[SearXNG][PATCH] webapp.py: image_proxy request error logger.exception -> logger.warning (降级)")
    elif new_request_error in content:
        print("[SearXNG][PATCH] webapp.py: image_proxy request error 已降级为 logger.warning, 跳过")
    else:
        print("[SearXNG][PATCH][WARNING] webapp.py 未找到 image_proxy request error marker, 跳过", file=sys.stderr)

    # 2. 修复 image_proxy finally 块中的 logger.exception('HTTP error on closing') -> logger.debug
    old_close_code = "            except httpx.HTTPError:\n                logger.exception('HTTP error on closing')"
    new_close_code = "            except httpx.HTTPError as e:\n                logger.debug('HTTP error on closing: %s', e)"
    if old_close_code in content:
        content = content.replace(old_close_code, new_close_code, 1)
        changed = True
        print("[SearXNG][PATCH] webapp.py: image_proxy close error logger.exception -> logger.debug (降级)")
    elif new_close_code in content:
        print("[SearXNG][PATCH] webapp.py: image_proxy close error 已降级为 logger.debug, 跳过")
    else:
        print("[SearXNG][PATCH][WARNING] webapp.py 未找到 image_proxy close error marker, 跳过", file=sys.stderr)

    if changed:
        path.write_text(content, encoding="utf-8")
    return True


def main() -> int:
    print("[SearXNG][PATCH] 开始 patch 引擎源码 (CrossRef/PubMed Polite Pool + image_proxy HTTP + webapp close error)")
    # 不阻断构建: 任一失败仅告警
    try:
        patch_crossref()
    except Exception as e:
        print(f"[SearXNG][PATCH][WARNING] crossref.py patch 失败: {e}", file=sys.stderr)

    try:
        patch_pubmed()
    except Exception as e:
        print(f"[SearXNG][PATCH][WARNING] pubmed.py patch 失败: {e}", file=sys.stderr)

    try:
        patch_network_image_proxy_http()
    except Exception as e:
        print(f"[SearXNG][PATCH][WARNING] network.py patch 失败: {e}", file=sys.stderr)

    try:
        patch_github_api_token()
    except Exception as e:
        print(f"[SearXNG][PATCH][WARNING] github.py patch 失败: {e}", file=sys.stderr)

    try:
        patch_webapp_image_proxy_close_error()
    except Exception as e:
        print(f"[SearXNG][PATCH][WARNING] webapp.py patch 失败: {e}", file=sys.stderr)

    print("[SearXNG][PATCH] 引擎源码 patch 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
