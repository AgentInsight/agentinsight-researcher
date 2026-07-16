# Agent-Researcher 容器错误修复方案 V2（第二轮）

> **生成时间**: 2026-07-16
> **数据来源**: 并行研究（"研究生产力与消费能力的关系" + "研究下雨跟眼泪的关系"）期间容器日志
> **前序文档**: `docs/CONTAINER_ERROR_FIX_PLAN.md`（第一轮，已完成全部 E01-E14 / A1-A6 / C1 修复）
> **本轮状态**: 第一轮修复验证通过（无 OOM、无崩溃），但日志中仍存在可修复的 WARNING/ERROR

---

## 目录

1. [执行摘要](#1-执行摘要)
2. [错误统计概览](#2-错误统计概览)
3. [错误详细分析与修复方案](#3-错误详细分析与修复方案)
   - 3.1 E2R-01 — mongodb-mcp-server npx 缓存损坏（CRITICAL）
   - 3.2 E2R-02 — chrome-devtools-mcp 端口 3001 冲突（HIGH）
   - 3.3 E2R-03 — MCP TaskGroup 僵尸客户端循环（MEDIUM）
   - 3.4 E2R-04 — SearXNG 搜索引擎 CAPTCHA 封锁（MEDIUM）
   - 3.5 E2R-05 — SearXNG 熔断器级联触发（MEDIUM）
   - 3.6 E2R-06 — GDELT 并发 429 限流（LOW）
   - 3.7 E2R-07 — Tavily 432 额度耗尽（INFO）
   - 3.8 E2R-08 — arxiv 库未安装（LOW）
   - 3.9 E2R-09 — SSL 证书验证失败（LOW）
   - 3.10 E2R-10 — PDF 被传入 HTML 解析器（LOW）
   - 3.11 E2R-11 — bs_markdownify 递归深度超限（LOW）
   - 3.12 E2R-12 — MCP JSONRPC 解析错误残留（LOW）
4. [修复优先级排序](#4-修复优先级排序)
5. [验证清单](#5-验证清单)

---

## 1. 执行摘要

第一轮修复（E01-E14 / A1-A6 / C1 / P0-1~P0-11 / P1-12~P1-17）已全部落地，并行研究成功完成：

| 指标 | 第一轮修复前 | 第一轮修复后（本轮） |
|------|------------|-------------------|
| 并行研究完成率 | OOM 崩溃（exit 137） | **100% 完成**（2/2 成功） |
| 峰值内存 | 8.16 GB | **4.55 GB**（降低 44%） |
| 研究后稳定内存 | 6.38 GB | **3.19 GB**（降低 50%） |
| MCP 僵尸客户端泄漏 | 永久累积 | **A6 自动清理生效**（但存在重试循环） |
| 搜索器资源泄漏 | 未关闭 | **P0-1 修复生效** |

**结论**: 核心架构修复有效，但日志暴露了 12 个二级问题，主要集中在 MCP npx 缓存损坏、端口冲突、SearXNG 外部引擎封锁。这些问题不影响研究完成，但增加了无效重试和日志噪音。

---

## 2. 错误统计概览

| 编号 | 错误类型 | 严重级别 | 出现次数 | 根因分类 |
|------|---------|---------|---------|---------|
| E2R-01 | mongodb-mcp-server npx ENOTEMPTY | CRITICAL | 15+ | npx 缓存损坏 |
| E2R-02 | chrome-devtools-mcp EADDRINUSE:3001 | HIGH | 4+ | 端口冲突 |
| E2R-03 | MCP TaskGroup 僵尸客户端循环 | MEDIUM | 8+ | E2R-01/02 连锁反应 |
| E2R-04 | SearXNG Baidu/Sogou CAPTCHA | MEDIUM | 6+ | 外部引擎反爬 |
| E2R-05 | 熔断器 OPEN 级联 | MEDIUM | 5+ | E2R-04 连锁反应 |
| E2R-06 | GDELT HTTP 429 限流 | LOW | 4+ | 并发超出 5s 间隔 |
| E2R-07 | Tavily HTTP 432 额度满 | INFO | 6+ | 免费额度耗尽 |
| E2R-08 | arxiv 库未安装 | LOW | 2+ | 缺少依赖 |
| E2R-09 | SSL CERTIFICATE_VERIFY_FAILED | LOW | 2+ | CA 证书缺失 |
| E2R-10 | trafilatura empty HTML tree | LOW | 4+ | PDF 传入 HTML 解析 |
| E2R-11 | bs_markdownify 递归超限 | LOW | 1+ | 深层嵌套 HTML |
| E2R-12 | MCP JSONRPC 解析错误 | LOW | 1+ | E2R-01 连锁 |

---

## 3. 错误详细分析与修复方案

### 3.1 E2R-01 — mongodb-mcp-server npx 缓存损坏（CRITICAL）

**错误日志**:
```
npm error code ENOTEMPTY
npm error syscall rmdir
npm error path /app/.npm/_npx/191c568aa03d4fb8/node_modules/mongodb-mcp-server/dist/cjs/common
npm error errno -39
npm error ENOTEMPTY: directory not empty, rmdir '/app/.npm/_npx/.../mongodb-mcp-server/dist/cjs/common'

npm error code ENOENT
npm error syscall chmod
npm error path /app/.npm/_npx/191c568aa03d4fb8/node_modules/mongodb-schema/bin/mongodb-schema
npm error errno -2
npm error enoent ENOENT: no such file or directory
```

**根因分析**:
- `scripts/init.sql` 第 363-365 行配置了 `mongodb` MCP，命令为 `npx -y mongodb-mcp-server mongodb://localhost:27017/mydb`
- `mongodb://localhost:27017/mydb` 是占位符连接字符串（容器内无 MongoDB 服务）
- npx 首次安装 `mongodb-mcp-server` 时，并发请求导致 npm 缓存目录写入冲突，产生不完整的 `node_modules`
- 后续每次 npx 尝试都因 ENOTEMPTY 无法清理损坏目录，永久失败
- 这直接触发 E2R-03（TaskGroup 异常 → 僵尸客户端循环）

**影响**:
- 每次 MCP 调用都会触发 npx 重试（15+ 次），浪费 CPU 和 I/O
- npx 子进程退出码非零，导致 `MultiServerMCPClient.get_tools()` 抛出 `TaskGroup` 异常
- A6 僵尸清理生效但无效——清理后下次请求又创建新的失败 client

**修复方案**:

**方案 A（推荐）: 禁用 mongodb MCP**

`mongodb://localhost:27017/mydb` 是占位符，容器内无 MongoDB 服务，启用此 MCP 毫无意义。

**文件**: `scripts/init.sql` 第 363-365 行

```sql
-- 修改前 (enabled=TRUE)
(NULL, 'system', 'mongodb', NULL, 'stdio', 'npx',
 '["-y", "mongodb-mcp-server", "mongodb://localhost:27017/mydb"]'::jsonb, NULL,
 TRUE, TRUE, 4,'MongoDB: NoSQL 数据库交互与查询 (推荐, 需配置连接字符串)'),

-- 修改后 (enabled=FALSE, 占位符未配置连接字符串)
(NULL, 'system', 'mongodb', NULL, 'stdio', 'npx',
 '["-y", "mongodb-mcp-server", "mongodb://localhost:27017/mydb"]'::jsonb, NULL,
 FALSE, TRUE, 4,'MongoDB: NoSQL 数据库交互与推荐, 需克隆后配置真实 MongoDB 连接字符串)'),
```

同时将版本号从 `4` 升级到 `5`，触发已部署配置的 UPSERT 更新：

```sql
-- init.sql 顶部版本号
-- v4 → v5 (禁用 mongodb 占位符)
```

**方案 B（可选）: Dockerfile 清理 npx 缓存**

在 `Dockerfile` / `Dockerfile.qa` / `Dockerfile.offline` 的 builder 阶段添加：

```dockerfile
# 预装 mongodb-mcp-server 到全局 npx 缓存, 避免运行时并发安装冲突
RUN npx -y mongodb-mcp-server --version 2>/dev/null || true \
    && rm -rf /app/.npm/_npx/191c568aa03d4fb8/node_modules/.tmp.*
```

> **注意**: 方案 B 仅修复 npx 缓存损坏，但 MongoDB 连接仍会失败（无服务）。推荐方案 A。

---

### 3.2 E2R-02 — chrome-devtools-mcp 端口 3001 冲突（HIGH）

**错误日志**:
```
Warning: Server is binding to 0.0.0.0 without DNS rebinding protection.
Failed to start server: Error: listen EADDRINUSE: address already in use :::3001
```

**根因分析**:
- `scripts/init.sql` 第 345-347 行配置了 `chrome-mcp`，命令为 `npx -y chrome-devtools-mcp`
- `chrome-devtools-mcp` 启动时会尝试绑定 HTTP 服务器到端口 3001
- 并行研究触发 2 个 MCP 调用，两个 `chrome-devtools-mcp` 实例竞争同一端口
- 第一个实例绑定成功（或被其他进程占用），第二个实例 EADDRINUSE 失败
- 容器内无 Chrome 浏览器，即使端口不冲突，CDP 连接也会失败

**影响**:
- chrome-mcp 无法启动，贡献 TaskGroup 异常
- 端口竞争导致不稳定的失败模式

**修复方案**:

**方案 A（推荐）: 禁用 chrome-mcp**

容器内无 Chrome 浏览器，chrome-devtools-mcp 无法工作。

**文件**: `scripts/init.sql` 第 345-347 行

```sql
-- 修改前 (enabled=TRUE)
(NULL, 'system', 'chrome-mcp', NULL, 'stdio', 'npx',
 '["-y", "chrome-devtools-mcp"]'::jsonb, NULL,
 TRUE, TRUE, 4,'Chrome 浏览器控制: 通过 CDP 协议操控本地 Chrome (社区实现 chrome-devtools-mcp)'),

-- 修改后 (enabled=FALSE, 容器内无 Chrome)
(NULL, 'system', 'chrome-mcp', NULL, 'stdio', 'npx',
 '["-y", "chrome-devtools-mcp"]'::jsonb, NULL,
 FALSE, TRUE, 5,'Chrome 浏览器控制: 通过 CDP 协议操控本地 Chrome (需克隆后在有 Chrome 的环境配置)'),
```

**方案 B（可选）: 配置端口参数**

如果未来需要在有 Chrome 的环境使用：

```sql
'["-y", "chrome-devtools-mcp", "--port=3002"]'::jsonb,
```

---

### 3.3 E2R-03 — MCP TaskGroup 僵尸客户端循环（MEDIUM）

**错误日志**:
```
WARNING:src.skills.researcher.mcp_coordinator:MCP get_tools() 失败, 移除僵尸 client: unhandled errors in a TaskGroup (1 sub-exception)
WARNING:src.skills.researcher.mcp_coordinator:MCP 执行失败 (servers=['chrome-mcp', 'fetch', 'hackernews', 'mongodb', 'pdf-tools', 'wikipedia']): unhandled errors in a TaskGroup (1 sub-exception)
```

**根因分析**:
- 第一轮 A6 修复**已生效**：僵尸 client 被正确移除并 `aclose()`
- 但问题在于：MCP 配置中 `mongodb` 和 `chrome-mcp` 仍然 `enabled=TRUE`
- 每次新请求都会重新创建 client → npx 再次失败 → TaskGroup 异常 → A6 清理 → 下次请求重复
- 形成"创建-失败-清理-重创建"的无效循环

**影响**:
- 8+ 次无效 MCP client 创建/销毁循环
- 每次循环消耗 CPU（npx 进程启动）和内存（TaskGroup 子进程）
- 日志噪音

**修复方案**:

**方案 1（依赖 E2R-01/E2R-02）: 禁用占位符 MCP**

执行 E2R-01 和 E2R-02 的修复后，mongodb 和 chrome-mcp 被禁用，TaskGroup 异常将不再出现。

**方案 2（增强）: MCP 级别熔断器**

在 `src/skills/researcher/mcp_coordinator.py` 中添加 per-server 失败计数器 + 熔断机制：

```python
# MCPCoordinator.__init__ 中添加
self._server_failure_counts: dict[str, int] = {}  # server_name -> consecutive_failures
self._server_circuit_open: dict[str, float] = {}  # server_name -> open_until_timestamp
_MCP_SERVER_FAILURE_THRESHOLD = 3
_MCP_SERVER_COOLDOWN_SECONDS = 300  # 5 分钟冷却

# _get_or_create_client 中, 创建前检查熔断状态
def _is_server_circuit_open(self, server_name: str) -> bool:
    """检查某 MCP server 是否处于熔断状态."""
    open_until = self._server_circuit_open.get(server_name)
    if open_until and time.monotonic() < open_until:
        return True
    if open_until:
        # 熔断过期, 重置
        self._server_circuit_open.pop(server_name, None)
        self._server_failure_counts.pop(server_name, None)
    return False

# get_tools() 失败时, 记录失败并可能触发熔断
def _record_server_failure(self, server_name: str) -> None:
    count = self._server_failure_counts.get(server_name, 0) + 1
    self._server_failure_counts[server_name] = count
    if count >= _MCP_SERVER_FAILURE_THRESHOLD:
        self._server_circuit_open[server_name] = time.monotonic() + _MCP_SERVER_COOLDOWN_SECONDS
        logger.warning(
            "MCP server '%s' 连续失败 %d 次, 熔断 %ds",
            server_name, count, _MCP_SERVER_COOLDOWN_SECONDS,
        )
```

在 `conduct_research` 中过滤掉熔断中的 server：

```python
# 过滤掉熔断中的 server_configs
active_configs = {
    name: cfg for name, cfg in server_configs.items()
    if not self._is_server_circuit_open(name)
}
if not active_configs:
    logger.warning("所有 MCP server 均处于熔断状态, 跳过 MCP 调用")
    return []
```

---

### 3.4 E2R-04 — SearXNG 搜索引擎 CAPTCHA 封锁（MEDIUM）

**错误日志**:
```
searx.exceptions.SearxEngineCaptchaException: Baidu CAPTCHA (baidu_stealth: cookie warmup + curl_cffi 仍被检测, 可能是 IP 频率限制, 建议配置代理轮换) (suspended_time=300)
searx.exceptions.SearxEngineCaptchaException: Sogou CAPTCHA (sogou_stealth: curl_cffi Chrome 指纹仍被检测) (suspended_time=3600)
searx.exceptions.SearxEngineAccessDeniedException: HTTP error 403 (suspended_time=180)  # GitHub
```

**根因分析**:
- SearXNG 配置了 Baidu、Sogou 等中文搜索引擎的 stealth 模式
- 这些引擎检测到 SearXNG 的爬虫行为，返回 CAPTCHA 或 403
- SearXNG 自动暂停该引擎 300-3600 秒
- GitHub API 返回 403（无 token 限流）

**影响**:
- 中文搜索结果质量下降（Baidu/Sogou 被暂停）
- 触发 E2R-05 熔断器级联

**修复方案**:

**方案: 调整 SearXNG 引擎配置**

**文件**: `config/searxng/settings.yml`

```yaml
# 1. 禁用不稳定的中文字体引擎 (Baidu/Sogou CAPTCHA 不可避免)
engines:
  - name: baidu_stealth
    disabled: true  # CAPTCHA 不可绕过
  - name: sogou_stealth
    disabled: true  # CAPTCHA 不可绕过

  # 2. 保留稳定的引擎
  - name: google
    disabled: false
  - name: bing
    disabled: false
  - name: duckduckgo
    disabled: false
  - name: wikipedia
    disabled: false

  # 3. 保留 Baidu 标准模式 (非 stealth, 降级可用)
  - name: baidu
    disabled: false
```

---

### 3.5 E2R-05 — SearXNG 熔断器级联触发（MEDIUM）

**错误日志**:
```
WARNING:src.common.circuit_breaker:熔断器开启 (OPEN): 连续失败 3 次, 熔断 60s
WARNING:src.skills.researcher.searchers.searx:SearXNG 搜索失败 (重试 2 次):
```

**根因分析**:
- E2R-04 中 Baidu/Sogou CAPTCHA 导致 SearXNG 返回空结果
- `circuit_breaker.py` 连续 3 次失败后开启熔断，60 秒内拒绝所有 SearXNG 请求
- 并行研究中两个子查询同时触发 SearXNG，加速熔断触发
- 60 秒熔断期内所有 SearXNG 搜索被拒绝

**影响**:
- 60 秒搜索空窗期
- 研究质量受影响（SearXNG 是主要搜索源之一）

**修复方案**:

**方案: 降低熔断阈值 + 半开探测**

**文件**: `src/common/circuit_breaker.py`

```python
# 当前: 连续 3 次失败 → 熔断 60s
# 修改: 连续 5 次失败 → 熔断 30s + 半开探测

CIRCUIT_FAILURE_THRESHOLD = 5  # 3 → 5 (容忍更多瞬时失败)
CIRCUIT_OPEN_DURATION = 30     # 60 → 30 (缩短熔断期)
CIRCUIT_HALF_OPEN_PROBES = 1   # 半开状态允许 1 个探测请求
```

---

### 3.6 E2R-06 — GDELT 并发 429 限流（LOW）

**错误日志**:
```
WARNING:src.skills.researcher.searchers.gdelt:gdelt HTTP 429: 请求过于频繁, 已触发限流 (间隔需 ≥5 秒)
WARNING:src.skills.researcher.searchers.gdelt:gdelt JSON 解析失败: Expecting value: line 1 column 1 (char 0)
WARNING:src.skills.researcher.searchers.gdelt:gdelt 调用失败:
```

**根因分析**:
- 第一轮 E11 修复**已生效**：5 秒全局间隔限制正常工作
- 但并行研究中，两个研究同时调用 GDELT，超过 5 秒间隔
- 429 响应体非 JSON，导致 `json.loads()` 失败（JSON 解析错误）

**影响**:
- 部分 GDELT 查询被丢弃
- JSON 解析错误产生日志噪音

**修复方案**:

**方案: 增加间隔 + 429 响应体保护**

**文件**: `src/skills/researcher/searchers/gdelt.py`

```python
# 1. 增加全局间隔 (5s → 8s, 容忍并行并发)
_GDELT_MIN_INTERVAL = 8.0  # 5.0 → 8.0

# 2. 429 响应体保护 (避免 JSON 解析错误)
async def _call_gdelt(...) -> list[dict]:
    response = await client.get(url)
    if response.status_code == 429:
        logger.warning("gdelt HTTP 429: 请求过于频繁, 已触发限流 (间隔需 ≥%d 秒)", int(_GDELT_MIN_INTERVAL))
        return []  # 直接返回空, 不尝试 JSON 解析

    # 非 200 且非 429, 记录但也不解析
    if response.status_code != 200:
        logger.warning("gdelt HTTP %d: 非预期状态码", response.status_code)
        return []

    try:
        data = response.json()
    except Exception:
        logger.warning("gdelt JSON 解析失败: %s", response.text[:200] if response.text else "(empty body)")
        return []
```

---

### 3.7 E2R-07 — Tavily 432 额度耗尽（INFO）

**错误日志**:
```
WARNING:src.skills.researcher.research_conductor:tavily 额度已满: Tavily 搜索失败 (HTTP 432): 月度额度已满或 API Key 无效
```

**根因分析**:
- Tavily 免费额度（1000 次/月）已耗尽
- 代码已正确处理（降级到其他搜索源）

**影响**: 无（降级正常工作）

**修复方案**: **无需代码修复**。用户需更换 Tavily API Key 或升级付费计划。

---

### 3.8 E2R-08 — arxiv 库未安装（LOW）

**错误日志**:
```
WARNING:src.skills.researcher.scrapers.arxiv_scraper:arxiv 库未安装, 跳过 Arxiv 抓取
```

**根因分析**:
- `arxiv` Python 包未在 `requirements.txt` 中声明

**修复方案**:

**文件**: `requirements.txt`

```txt
# 添加 arxiv 包
arxiv>=2.1.0
```

**文件**: `Dockerfile.qa` (QA 离线模式需预下载 wheel)

```bash
# 在 packages/wheels/ 预下载
pip download arxiv>=2.1.0 -d packages/wheels/
```

---

### 3.9 E2R-09 — SSL 证书验证失败（LOW）

**错误日志**:
```
WARNING:src.skills.researcher.scrapers.trafilatura_scraper:Trafilatura 抓取失败 https://m.hzcmer.com/ykyzxkp/2934.html: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1010)
```

**根因分析**:
- 部分网站使用自签名或不被系统 CA 信任的 SSL 证书
- `httpx.AsyncClient(verify=True)`（默认）拒绝连接

**修复方案**:

**方案: 使用 certifi + 降级策略**

**文件**: `src/skills/researcher/scrapers/trafilatura_scraper.py`（及其他 scraper）

```python
import certifi
import httpx

# 使用 certifi CA 包
client = httpx.AsyncClient(
    verify=certifi.where(),  # 显式使用 certifi CA
    timeout=30.0,
    follow_redirects=True,
)

# SSL 失败时降级 (仅 dev/qa, 生产不降级)
try:
    response = await client.get(url)
except httpx.ConnectError as e:
    if "CERTIFICATE_VERIFY_FAILED" in str(e) and settings.env != "prod":
        logger.warning("SSL 验证失败, 降级 verify=False (dev/qa only): %s", url)
        client = httpx.AsyncClient(verify=False, timeout=30.0, follow_redirects=True)
        response = await client.get(url)
    else:
        raise
```

**文件**: `requirements.txt`

```txt
certifi>=2024.2.2
```

---

### 3.10 E2R-10 — PDF 被传入 HTML 解析器（LOW）

**错误日志**:
```
ERROR:trafilatura.utils:parsed tree length: 1, wrong data type or not valid HTML
ERROR:trafilatura.core:empty HTML tree: None
WARNING:trafilatura.core:discarding data: https://www.resci.cn/CN/PDF/10.18402/resci.2016.04.08
WARNING:trafilatura.core:discarding data: https://journal03.magtech.org.cn/Jweb_gyjsjj/EN/PDF/10.3969/j.issn.1004-910X.2025.06.005
```

**根因分析**:
- 搜索结果包含 PDF URL（如 `https://...PDF/...`）
- 抓取器将 PDF 内容传入 trafilatura（HTML 解析器），解析失败
- PDF 内容应交给 `document_loader.py` 处理

**修复方案**:

**文件**: `src/skills/researcher/scrapers/__init__.py` 或 `trafilatura_scraper.py`

```python
import re

_PDF_URL_PATTERN = re.compile(r"\.pdf($|\?)", re.IGNORECASE)

def _is_pdf_url(url: str) -> bool:
    """检测 URL 是否指向 PDF 资源."""
    return bool(_PDF_URL_PATTERN.search(url))

# 在 scrape_urls 中, 对 PDF URL 路由到 document_loader
async def scrape_url(url: str, ...) -> str | None:
    if _is_pdf_url(url):
        # PDF 走 document_loader (已有 PDF 解析能力)
        from src.skills.researcher.document_loader import load_document_from_url
        return await load_document_from_url(url)

    # HTML 走 trafilatura/bs_markdownify
    return await _scrape_html(url, ...)
```

---

### 3.11 E2R-11 — bs_markdownify 递归深度超限（LOW）

**错误日志**:
```
WARNING:src.skills.researcher.scrapers.bs_markdownify_scraper:BS+markdownify 抓取失败 https://www.resci.cn/CN/PDF/10.18402/resci.2016.04.08: maximum recursion depth exceeded
```

**根因分析**:
- 深层嵌套 HTML（或 PDF 被当作 HTML 解析）导致 BeautifulSoup 递归超限
- Python 默认递归深度 1000

**修复方案**:

**文件**: `src/skills/researcher/scrapers/bs_markdownify_scraper.py`

```python
import sys

# 在解析前提高递归深度限制 (仅对本协程生效)
old_limit = sys.getrecursionlimit()
try:
    sys.setrecursionlimit(2000)  # 1000 → 2000
    soup = BeautifulSoup(html, "html.parser")
    markdown = markdownify(str(soup))
finally:
    sys.setrecursionlimit(old_limit)
```

> **注意**: 此问题与 E2R-10 相关（PDF URL 被传入 HTML 解析器）。修复 E2R-10 后此问题大幅减少。

---

### 3.12 E2R-12 — MCP JSONRPC 解析错误残留（LOW）

**错误日志**:
```
ERROR:mcp.client.stdio:Failed to parse JSONRPC message from server
Traceback (most recent call last):
pydantic_core._pydantic_core.ValidationError: 1 validation error for JSONRPCMessage
```

**根因分析**:
- 第一轮 E01 修复**已生效**（添加了 server 名称日志）
- 此错误来自 E2R-01 中 mongodb-mcp-server 的 npx stderr 输出被误当作 JSONRPC 消息
- npx 失败时输出 npm error 日志到 stderr，MCP stdio client 尝试解析为 JSONRPC

**修复方案**:

依赖 E2R-01 修复（禁用 mongodb MCP）。禁用后 npx 不再启动，JSONRPC 解析错误自然消失。

如需进一步防护，可在 `mcp_coordinator.py` 中过滤非 JSONRPC stderr 输出：

```python
# 仅作参考, 实际由 langchain-mcp-adapters 库处理, 不建议修改库代码
# E2R-01 修复后此问题消失
```

---

## 4. 修复优先级排序

| 优先级 | 编号 | 修复内容 | 预计影响 |
|--------|------|---------|---------|
| P0 | E2R-01 | 禁用 mongodb MCP（占位符） | 消除 15+ 次 npx 失败循环 |
| P0 | E2R-02 | 禁用 chrome-mcp（无 Chrome） | 消除端口 3001 冲突 |
| P0 | E2R-03 | MCP 级熔断器（依赖 E2R-01/02） | 消除 TaskGroup 僵尸循环 |
| P1 | E2R-04 | SearXNG 禁用 Baidu/Sogou stealth | 减少 CAPTCHA 触发 |
| P1 | E2R-05 | 熔断器参数调优 | 缩短搜索空窗期 |
| P2 | E2R-06 | GDELT 间隔增加 + 429 保护 | 减少并行限流 |
| P2 | E2R-08 | 添加 arxiv 依赖 | 恢复 Arxiv 抓取 |
| P2 | E2R-10 | PDF URL 路由到 document_loader | 减少 HTML 解析错误 |
| P3 | E2R-09 | certifi CA + SSL 降级 | 减少抓取失败 |
| P3 | E2R-11 | 递归深度调优 | 减少深层 HTML 失败 |
| INFO | E2R-07 | Tavily 额度（无需代码修复） | 用户更换 API Key |
| INFO | E2R-12 | JSONRPC（依赖 E2R-01 修复） | 自动消失 |

---

## 5. 验证清单

修复完成后，执行以下验证：

### 5.1 容器日志验证

```bash
# 重新部署后, 执行并行研究, 检查日志
docker logs agentinsight-agent-researcher-1 --since 10m 2>&1 | Select-String -Pattern "ERROR|WARN"

# 验证以下错误不再出现:
# ✅ E2R-01: 无 "npm error ENOTEMPTY" / "mongodb-mcp-server" 相关错误
# ✅ E2R-02: 无 "EADDRINUSE: address already in use :::3001"
# ✅ E2R-03: 无 "MCP get_tools() 失败, 移除僵尸 client" (或大幅减少)
# ✅ E2R-12: 无 "Failed to parse JSONRPC message from server"
```

### 5.2 功能验证

```bash
# 1. 健康检查
curl http://127.0.0.1:8066/health

# 2. 单次研究
curl -X POST http://127.0.0.1:8066/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"测试研究"}],"stream":true}'

# 3. 并行研究 (验证无 TaskGroup 循环)
# 使用 temp/parallel_research.py (trust_env=False)
```

### 5.3 内存验证

```bash
# 研究完成后 5 分钟检查内存
docker stats agentinsight-agent-researcher-1 --no-stream

# 预期: 峰值 < 4.5 GB, 稳定 < 3.0 GB
```

---

## 附录: 第一轮修复验证状态

| 第一轮编号 | 描述 | 本轮验证状态 |
|-----------|------|------------|
| E01 | MCP JSONRPC 解析错误 | ✅ 已生效（server 名称已记录）, 残留由 E2R-01 引起 |
| E02 | obsidian MCP 路径错误 | ✅ 已修复（obsidian 已禁用） |
| E03 | git MCP 可执行文件缺失 | ✅ 已修复（Dockerfile 已安装 git） |
| E04 | sequential-thinking MCP 模块缺失 | ✅ 已移除 |
| E05 | supabase MCP 模块缺失 | ✅ 已移除 |
| E06 | filesystem MCP 目录不存在 | ✅ 已修复（目录已创建） |
| E07 | twitter MCP 凭据缺失 | ✅ 已禁用（占位符） |
| E08 | deepl MCP 鉴权失败 | ✅ 已禁用（占位符） |
| E09 | MCP TaskGroup 异常 | ✅ A6 修复已生效（但存在 E2R-03 重试循环） |
| E10 | Tavily 432 错误 | ✅ 代码处理正确（额度问题是外部因素） |
| E11 | GDELT 429 限流 | ✅ 5s 间隔已生效（并行需 E2R-06 优化） |
| E14 | Atlassian TOOLSETS 警告 | ✅ 已修复 |
| A1-A6 | MCPCoordinator 架构修复 | ✅ 全部生效 |
| C1 | 并发 stdio 进程倍增 | ✅ Lock + double-check 已生效 |
| P0-1~P0-11 | 内存泄漏修复 | ✅ 全部生效（峰值降 44%） |
| P1-12~P1-17 | 资源清理修复 | ✅ 全部生效 |
