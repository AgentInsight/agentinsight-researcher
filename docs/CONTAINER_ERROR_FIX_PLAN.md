# 容器错误修复方案

> 文档版本: v1.0
> 生成日期: 2026-07-16
> 数据来源: `docker logs agentinsight-agent-1` 实测采集
> 适用范围: agentinsight-researcher 容器栈 (生产联网模式 / QA 模式 / 生产离线模式)

---

## 1. 概述

### 1.1 容器状态

agentinsight-researcher 容器栈由 7 个独立容器组成 (生产联网 / QA 模式) 或 6 个容器 (生产离线模式, PostgreSQL 外部托管):

| 服务 | 容器名 | 健康状态 | 备注 |
|------|--------|---------|------|
| `agent` | `agentinsight-agent-1` | ✅ healthy | 主进程运行, 但日志含大量 MCP/搜索错误 |
| `embeddings` | `agentinsight-embeddings-1` | ✅ healthy | TEI bge-base-zh-v1.5 正常 |
| `qdrant` | `agentinsight-qdrant-1` | ✅ healthy | 向量库正常 |
| `redis` | `agentinsight-redis-1` | ✅ healthy | 缓存正常 |
| `postgres` | `agentinsight-postgres-1` | ✅ healthy | 业务表已初始化 |
| `searxng` | `agentinsight-searxng-1` | ✅ healthy | 元搜索正常 |
| `rerank` (可选) | `agentinsight-rerank-1` | ✅ healthy | `rerank_enabled=True` 时启用 |

容器栈整体可用, 但 Agent 容器日志暴露出 **15 类错误** + **8 项 MCP 系统性架构问题**, 需分级修复。

### 1.2 错误总数与分类

- **错误类别总数**: 15 类 (E01-E15)
- **系统性架构问题**: 8 项 (A1-A6 + C1 + 附带项)
- **修复优先级分布**:
  - **P0 (阻断/资源泄漏)**: 7 项 — 必须修复, 否则容器长时间运行会资源耗尽
  - **P1 (功能不可用/降级)**: 10 项 — 影响核心功能, 应尽快修复
  - **P2 (噪声/第三方)**: 6 项 — 日志噪声或第三方库问题, 可延后

### 1.3 错误根因分类

| 根因类别 | 错误数 | 说明 |
|---------|-------|------|
| **MCP 系统性架构缺陷** | 8 项 | 缓存隔离失效/资源泄漏/并发竞态 (A1-A6, C1) |
| **MCP 配置占位符未替换** | 4 项 | init.sql 预置 args/env_vars 含 `/path/to/...` / `<your-*>` 占位符 |
| **MCP npm 包损坏/缺失** | 3 项 | sequential-thinking / supabase / git 依赖问题 |
| **搜索 API 配额/限流** | 2 项 | Tavily 432 / GDELT 429 |
| **网络/目标站不可达** | 1 项 | 网页抓取 DNS/SSL/403/500/404 (预期错误) |
| **第三方库警告** | 2 项 | LiteLLM 超时降级 / authlib 弃用警告 |

---

## 2. 错误清单总表

| 编号 | 错误类型 | 错误消息 (摘要) | 根因 | 严重程度 | 修复方案 | 涉及文件 |
|------|---------|----------------|------|---------|---------|---------|
| E01 | MCP JSONRPC 解析错误 | `Failed to parse JSONRPC message from server (11 validation errors for JSONRPCMessage)` | stdio MCP 子进程 stdout 输出非 JSON-RPC 内容 (npm warning/git error/shell 报错), pydantic 校验失败 | P1 | 见 §3.1 (init.sql 占位符修复 + mcp_coordinator 容错) | `scripts/init.sql`, `src/skills/researcher/mcp_coordinator.py` |
| E02 | obsidian MCP 路径错误 | `ENOENT: no such file or directory, stat '--vault-path=/path/to/vault'` | init.sql 预置 args 含占位符 `/path/to/vault`, 用户未克隆配置真实路径就启用 | P1 | 见 §3.2 (init.sql 改 `enabled=FALSE` + 前端引导) | `scripts/init.sql`, `src/api/mcp_routes.py`, `src/skills/researcher/mcp_coordinator.py` |
| E03 | git MCP 可执行文件缺失 | `ImportError: Bad git executable. All git commands will error` | Dockerfile 未安装 git, `mcp-server-git` 依赖系统 git 可执行文件 | P1 | 见 §3.3 (Dockerfile 安装 git) | `Dockerfile`, `scripts/init.sql` |
| E04 | sequential-thinking MCP 模块缺失 | `ERR_MODULE_NOT_FOUND: Cannot find module '@modelcontextprotocol/sdk/dist/esm/server/mcp.js'` | `@modelcontextprotocol/server-sequential-thinking` npm 包损坏, 依赖 `@modelcontextprotocol/sdk` 未声明 | P1 | 见 §3.4 (改用 PyPI 替代或禁用) | `scripts/init.sql` |
| E05 | supabase MCP 模块缺失 | `ERR_MODULE_NOT_FOUND: Cannot find module '@supabase/mcp-utils'` | `@supabase/mcp-server-supabase` npm 包内部 require `@supabase/mcp-utils` 但依赖未声明 | P1 | 见 §3.5 (改用社区 supabase MCP 或禁用) | `scripts/init.sql` |
| E06 | filesystem MCP 目录不存在 | `None of the specified directories are accessible` / `Cannot access directory /path/to/allowed/files` | init.sql 预置 args 含占位符 `/path/to/allowed/files` | P1 | 见 §3.6 (init.sql 改默认路径 + enabled=FALSE) | `scripts/init.sql` |
| E07 | twitter MCP 凭据缺失 | `TwitterServer构造失败: accessTokenSecret Required` | twitter MCP env_vars 含占位符 `<your-token>`, 未配置真实凭据 | P2 | 见 §3.7 (init.sql 改 enabled=FALSE) | `scripts/init.sql` |
| E08 | deepl MCP 鉴权失败 | `AuthorizationError: Authentication failed, provided API key is invalid` | deepl MCP env_vars 含占位符 `<your-key>`, 用占位符调 DeepL API 返回 401 | P2 | 见 §3.8 (init.sql 改 enabled=FALSE) | `scripts/init.sql` |
| E09 | MCP TaskGroup 异常 | `MCP 执行失败: unhandled errors in a TaskGroup (1 sub-exception)` | `MultiServerMCPClient.get_tools()` 用 TaskGroup 并发启动 stdio client, 任一失败导致 ExceptionGroup, client 留在 `_client_cache` 成为僵尸 | **P0** | 见 §3.9 + §4 (A6: 失败后移除缓存并 aclose) | `src/skills/researcher/mcp_coordinator.py` (行 344-389) |
| E10 | Tavily 搜索 432 错误 | `Tavily 搜索失败: Client error '432'` | Tavily API 配额耗尽或 API Key 无效, 返回 432 状态码 | P2 | 见 §3.10 (配置有效 API Key, 降级已正确) | `src/skills/researcher/searchers/tavily.py` |
| E11 | GDELT 搜索 429 限流 | `gdelt HTTP 429: Please limit requests to one every 5 seconds` | GDELT 免费 API 限流, 每 5 秒 1 次, 并发请求触发 429 | P1 | 见 §3.11 (加请求间隔限流) | `src/skills/researcher/searchers/gdelt.py` |
| E12 | LiteLLM model cost map 超时 | `Failed to fetch remote model cost map from https://raw.githubusercontent.com: timed out` | 容器无法访问 `raw.githubusercontent.com` (网络问题), LiteLLM 降级到本地备份 | P2 | 见 §3.12 (配置 `LITELLM_LOCAL_MODEL_COST_MAP=True`) | 无需代码修改 |
| E13 | 网页抓取失败 | DNS/SSL/403/500/404/`ERR_NAME_NOT_RESOLVED`/`ERR_CERT_DATE_INVALID`/`Server disconnected` | 目标网站不可访问 (DNS/SSL/权限/服务器问题), 属预期错误 | P2 | 见 §3.13 (无需修改, 快速失败机制已正确) | 无需代码修改 |
| E14 | mcp_atlassian TOOLSETS 警告 | `TOOLSETS is not set — currently defaults to all toolsets` | mcp-atlassian 启动时未设置 `TOOLSETS` 环境变量, 打印警告 | P2 | 见 §3.14 (init.sql confluence args 加 `--toolsets=confluence`) | `scripts/init.sql` |
| E15 | authlib 弃用警告 | `AuthlibDeprecationWarning: authlib.jose module is deprecated, please use joserfc instead` | fastmcp 依赖的 `authlib.jose` 模块已弃用 | P2 | 见 §3.15 (无需修改, 第三方库警告) | 无需代码修改 |

### MCP 系统性架构问题总表

| 编号 | 问题 | 根因 | 严重程度 | 修复方案 | 涉及文件 |
|------|------|------|---------|---------|---------|
| A1 | 多实例导致缓存隔离失效 | `deep_research.py:147` 和 `research_conductor.py:97` 各自构造 MCPCoordinator 实例, 而 `conduct_mcp_if_enabled` 走全局单例 | **P0** | 见 §4.1 (两处改用 `get_mcp_coordinator()` 单例) | `src/skills/researcher/deep_research.py`, `src/skills/researcher/research_conductor.py` |
| A2 | mcp_routes.py CRUD 后未清空缓存 | create/update/delete/clone MCP 配置后未调用 `clear_cache()` | **P0** | 见 §4.2 (4 个端点成功返回前调用 `clear_cache()`) | `src/api/mcp_routes.py` |
| A3 | `_test_mcp_config` 测试连接后未关闭 | 仅 `TimeoutError` 分支调用 `aclose`, 成功路径和其他异常路径未关闭 client | **P0** | 见 §4.3 (用 try/finally 统一清理) | `src/api/mcp_routes.py` (行 237-312) |
| A4 | `_client_cache` 无限增长 | `_client_cache` 字典只增不减, 每次修改 MCP 配置产生新 key, 旧 client 被孤立 | **P0** | 见 §4.4 (LRU 上限 8-16 + 淘汰时 aclose) | `src/skills/researcher/mcp_coordinator.py` (行 181, 219-221) |
| A5 | MCPCoordinator 缺失 `close()` 方法 | `server.py` lifespan 未清理 MCPCoordinator, stdio 子进程被 SIGKILL | **P0** | 见 §4.5 (新增 `close()` + lifespan 调用) | `src/skills/researcher/mcp_coordinator.py`, `server.py` |
| A6 | TaskGroup 异常导致资源泄漏 | `get_tools()` 失败时 client 留在缓存中成为僵尸, 后续请求复用必失败 | **P0** | 见 §4.6 (失败后从 `_client_cache` 移除并 aclose) | `src/skills/researcher/mcp_coordinator.py` (行 344-389) |
| C1 | 并发请求导致 stdio 进程倍增 | `_get_or_create_client` 同步方法无锁, 并发请求各自构造 client | **P0** | 见 §4.7 (改 async + `asyncio.Lock` + double-check) | `src/skills/researcher/mcp_coordinator.py` (行 194-221) |

---

## 3. 每类错误的详细修复方案

### 3.1 E01 — MCP JSONRPC 解析错误

**错误消息**:
```
ERROR:mcp.client.stdio:Failed to parse JSONRPC message from server (11 validation errors for JSONRPCMessage)
```

**根因分析**:
stdio MCP 子进程的 stdout 输出了非 JSON-RPC 内容 (npm warning / git error / shell 报错), 被 `langchain-mcp-adapters` 的 pydantic 校验拦截。根因是多个系统 MCP 启动失败 (E02-E08), 子进程报错信息混入 stdout。

**修复方案**:
本错误的根因是 E02-E08 的占位符/缺失依赖问题, 修复 E02-E08 后本错误自动消失。同时建议在 `mcp_coordinator.py` 增加 stdio 启动失败的细粒度日志, 便于定位是哪个 MCP 报错。

**涉及文件**:
- `scripts/init.sql` (修复 E02-E08 占位符)
- `src/skills/researcher/mcp_coordinator.py` (增加 MCP 名称到错误日志)

**代码修改 (mcp_coordinator.py 增强错误日志)**:

修改前 (`src/skills/researcher/mcp_coordinator.py` 行 387-389):
```python
except Exception as e:  # noqa: BLE001
    logger.warning("MCP 执行失败: %s", e)
    return []
```

修改后:
```python
except Exception as e:  # noqa: BLE001
    # 记录失败的 server_configs 名称, 便于定位是哪个 MCP 报错
    failed_names = list(server_configs.keys()) if 'server_configs' in locals() else []
    logger.warning("MCP 执行失败 (servers=%s): %s", failed_names, e)
    return []
```

**优先级**: P1

---

### 3.2 E02 — obsidian MCP 路径错误

**错误消息**:
```
Error accessing directory --vault-path=/path/to/vault: Error: ENOENT: no such file or directory, stat '--vault-path=/path/to/vault'
```

**根因分析**:
`scripts/init.sql` 预置 obsidian MCP 的 args 含占位符 `--vault-path=/path/to/vault`, 用户未克隆配置真实路径就启用, 导致 obsidian MCP 启动时尝试 stat 占位符路径失败。

**修复方案**:
将所有含占位符的系统 MCP 的 `enabled` 改为 `FALSE`, 由前端引导用户克隆后填写真实值再启用 (符合 `mcp_routes.py` 的克隆流程设计: 克隆后 `enabled=FALSE`, 用户填 Key 后测试通过才启用)。

**涉及文件**:
- `scripts/init.sql` (obsidian/filesystem/git 等 args 含占位符的 MCP 改 `enabled=FALSE`)

**代码修改 (init.sql)**:

修改前 (行 300-302):
```sql
(NULL, 'system', 'obsidian', NULL, 'stdio', 'npx',
 '["-y", "mcp-obsidian", "--vault-path=/path/to/vault"]'::jsonb, NULL,
 TRUE, TRUE, 3, 'Obsidian 知识库: Markdown 解析/双向链接/语义搜索 (核心保留, 需配置 vault 路径)'),
```

修改后:
```sql
(NULL, 'system', 'obsidian', NULL, 'stdio', 'npx',
 '["-y", "mcp-obsidian", "--vault-path=/path/to/vault"]'::jsonb, NULL,
 FALSE, TRUE, 4, 'Obsidian 知识库: Markdown 解析/双向链接/语义搜索 (核心保留, 需克隆后配置 vault 路径)'),
```

> 注: `enabled` 改为 `FALSE`, `version` 递增到 `4` (触发 ON CONFLICT DO UPDATE 更新已部署配置)。

**优先级**: P1

---

### 3.3 E03 — git MCP 可执行文件缺失

**错误消息**:
```
ImportError: Bad git executable. All git commands will error until this is rectified.
```

**根因分析**:
`Dockerfile` 未安装 git, 而 `mcp-server-git` (PyPI 包) 依赖系统 git 可执行文件。容器内 `git` 命令不存在, 导致 mcp-server-git 启动时 `ImportError`。

**修复方案**:
在 `Dockerfile` runtime 阶段的 apt-get 安装列表中增加 `git`。

**涉及文件**:
- `Dockerfile` (runtime 阶段 apt-get 增加 git)
- `scripts/init.sql` (git MCP 的 `--repository` 占位符路径, 同步改 `enabled=FALSE`)

**代码修改 (Dockerfile)**:

修改前 (行 40-64):
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libxml2 \
    libxslt1.1 \
    libpq5 \
    fonts-dejavu-core \
    fonts-noto-cjk \
    fonts-noto-cjk-extra \
    fonts-wqy-zenhei \
    fonts-wqy-microhei \
    ca-certificates \
    curl \
    gnupg \
    && mkdir -p /etc/apt/keyrings \
    ...
```

修改后:
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libxml2 \
    libxslt1.1 \
    libpq5 \
    fonts-dejavu-core \
    fonts-noto-cjk \
    fonts-noto-cjk-extra \
    fonts-wqy-zenhei \
    fonts-wqy-microhei \
    ca-certificates \
    curl \
    gnupg \
    git \
    && mkdir -p /etc/apt/keyrings \
    ...
```

**代码修改 (init.sql, git MCP 改 enabled=FALSE)**:

修改前 (行 330-332):
```sql
(NULL, 'system', 'git', NULL, 'stdio', 'uvx',
 '["mcp-server-git", "--repository", "/path/to/git/repo"]'::jsonb, NULL,
 TRUE, TRUE, 3,'Git 仓库读取/搜索/操作 (PyPI 实现 mcp-server-git, uvx 运行)'),
```

修改后:
```sql
(NULL, 'system', 'git', NULL, 'stdio', 'uvx',
 '["mcp-server-git", "--repository", "/path/to/git/repo"]'::jsonb, NULL,
 FALSE, TRUE, 4,'Git 仓库读取/搜索/操作 (PyPI 实现 mcp-server-git, uvx 运行, 需克隆后配置仓库路径)'),
```

**优先级**: P1

---

### 3.4 E04 — sequential-thinking MCP 模块缺失

**错误消息**:
```
Error [ERR_MODULE_NOT_FOUND]: Cannot find module '@modelcontextprotocol/sdk/dist/esm/server/mcp.js'
```

**根因分析**:
`@modelcontextprotocol/server-sequential-thinking` npm 包内部引用 `@modelcontextprotocol/sdk/dist/esm/server/mcp.js`, 但该路径在 sdk 新版本中已变更 (或 sdk 未作为依赖声明), 导致 `npx` 运行时 `ERR_MODULE_NOT_FOUND`。

**修复方案**:
方案 A (推荐): 改用 PyPI 实现的 sequential-thinking MCP (如有)。
方案 B: 将 sequential-thinking MCP 改为 `enabled=FALSE` 并标注"npm 包损坏, 待官方修复", 避免启动时报错。

**涉及文件**:
- `scripts/init.sql`

**代码修改 (init.sql, 采用方案 B)**:

修改前 (行 288-290):
```sql
(NULL, 'system', 'sequential-thinking', NULL, 'stdio', 'npx',
 '["-y", "@modelcontextprotocol/server-sequential-thinking"]'::jsonb, NULL,
 TRUE, TRUE, 3,'通过思维序列进行动态反思式问题求解 (官方 npm 实现, 核心保留)'),
```

修改后:
```sql
(NULL, 'system', 'sequential-thinking', NULL, 'stdio', 'npx',
 '["-y", "@modelcontextprotocol/server-sequential-thinking"]'::jsonb, NULL,
 FALSE, TRUE, 4,'通过思维序列进行动态反思式问题求解 (npm 包损坏: @modelcontextprotocol/sdk 路径变更, 待官方修复, 暂禁用)'),
```

**优先级**: P1

---

### 3.5 E05 — supabase MCP 模块缺失

**错误消息**:
```
Error [ERR_MODULE_NOT_FOUND]: Cannot find module '@supabase/mcp-utils'
```

**根因分析**:
`@supabase/mcp-server-supabase` npm 包内部 require `@supabase/mcp-utils`, 但该依赖未在 package.json 中声明 (npm 包发布缺陷), 导致运行时模块找不到。

**修复方案**:
将 supabase MCP 改为 `enabled=FALSE` 并标注"npm 包依赖缺陷, 待官方修复"。

**涉及文件**:
- `scripts/init.sql`

**代码修改 (init.sql)**:

修改前 (行 358-361):
```sql
(NULL, 'system', 'supabase', NULL, 'stdio', 'npx',
 '["-y", "@supabase/mcp-server-supabase"]'::jsonb,
 '{"SUPABASE_URL": "<your-url>", "SUPABASE_KEY": "<your-key>"}'::jsonb,
 TRUE, TRUE, 3,'Supabase: Postgres + Auth + Storage 一体化后端 (推荐, 需配置 SUPABASE_URL)'),
```

修改后:
```sql
(NULL, 'system', 'supabase', NULL, 'stdio', 'npx',
 '["-y", "@supabase/mcp-server-supabase"]'::jsonb,
 '{"SUPABASE_URL": "<your-url>", "SUPABASE_KEY": "<your-key>"}'::jsonb,
 FALSE, TRUE, 4,'Supabase: Postgres + Auth + Storage 一体化后端 (npm 包依赖缺陷: @supabase/mcp-utils 未声明, 待官方修复, 暂禁用)'),
```

**优先级**: P1

---

### 3.6 E06 — filesystem MCP 目录不存在

**错误消息**:
```
Error: None of the specified directories are accessible
Warning: Cannot access directory /path/to/allowed/files, skipping
```

**根因分析**:
`scripts/init.sql` 预置 filesystem MCP 的 args 含占位符 `/path/to/allowed/files`, 容器内该路径不存在, 导致 filesystem MCP 启动时拒绝挂载。

**修复方案**:
将 filesystem MCP 的默认路径改为容器内存在的合理路径 (如 `/tmp/uploads` 或 `/app/data`), 同时改 `enabled=FALSE` 由用户克隆后定制。

**涉及文件**:
- `scripts/init.sql`

**代码修改 (init.sql)**:

修改前 (行 285-287):
```sql
(NULL, 'system', 'filesystem', NULL, 'stdio', 'npx',
 '["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/files"]'::jsonb, NULL,
 TRUE, TRUE, 3,'安全文件操作, 可配置访问路径 (官方 npm 实现, 核心保留)'),
```

修改后:
```sql
(NULL, 'system', 'filesystem', NULL, 'stdio', 'npx',
 '["-y", "@modelcontextprotocol/server-filesystem", "/tmp/uploads"]'::jsonb, NULL,
 FALSE, TRUE, 4,'安全文件操作, 可配置访问路径 (官方 npm 实现, 核心保留, 默认 /tmp/uploads, 需克隆后定制路径)'),
```

**优先级**: P1

---

### 3.7 E07 — twitter MCP 凭据缺失

**错误消息**:
```
TwitterServer构造失败: accessTokenSecret Required
```

**根因分析**:
twitter MCP 的 env_vars 含占位符 `<your-token>` / `<your-secret>`, 未配置真实 Twitter API 凭据, 启动时校验失败。

**修复方案**:
将 twitter MCP 改为 `enabled=FALSE` (符合克隆流程: 用户克隆后填真实凭据, 测试通过才启用)。

**涉及文件**:
- `scripts/init.sql`

**代码修改 (init.sql)**:

修改前 (行 350-353):
```sql
(NULL, 'system', 'twitter', NULL, 'stdio', 'npx',
 '["-y", "@enescinar/twitter-mcp"]'::jsonb,
 '{"TWITTER_API_KEY": "<your-api-key>", "TWITTER_API_SECRET": "<your-secret>", "TWITTER_ACCESS_TOKEN": "<your-token>", "TWITTER_ACCESS_SECRET": "<your-secret>"}'::jsonb,
 TRUE, TRUE, 3,'Twitter/X: 推文发布/搜索/互动管理 (推荐, 需配置 Twitter API 凭据)'),
```

修改后:
```sql
(NULL, 'system', 'twitter', NULL, 'stdio', 'npx',
 '["-y", "@enescinar/twitter-mcp"]'::jsonb,
 '{"TWITTER_API_KEY": "<your-api-key>", "TWITTER_API_SECRET": "<your-secret>", "TWITTER_ACCESS_TOKEN": "<your-token>", "TWITTER_ACCESS_SECRET": "<your-secret>"}'::jsonb,
 FALSE, TRUE, 4,'Twitter/X: 推文发布/搜索/互动管理 (推荐, 需克隆后配置 Twitter API 凭据)'),
```

**优先级**: P2

---

### 3.8 E08 — deepl MCP 鉴权失败

**错误消息**:
```
AuthorizationError: Authentication failed, provided API key is invalid
```

**根因分析**:
deepl MCP 的 env_vars 含占位符 `<your-key>`, 用占位符字符串调 DeepL API 返回 401 鉴权失败。

**修复方案**:
将 deepl MCP 改为 `enabled=FALSE` (符合克隆流程)。

**涉及文件**:
- `scripts/init.sql`

**代码修改 (init.sql)**:

修改前 (行 324-327):
```sql
(NULL, 'system', 'deepl', NULL, 'stdio', 'npx',
 '["-y", "deepl-mcp-server"]'::jsonb,
 '{"DEEPL_API_KEY": "<your-key>"}'::jsonb,
 TRUE, TRUE, 3,'DeepL: 高质量机器翻译, 支持 30+ 语言 (核心保留, 需配置 DEEPL_API_KEY)'),
```

修改后:
```sql
(NULL, 'system', 'deepl', NULL, 'stdio', 'npx',
 '["-y", "deepl-mcp-server"]'::jsonb,
 '{"DEEPL_API_KEY": "<your-key>"}'::jsonb,
 FALSE, TRUE, 4,'DeepL: 高质量机器翻译, 支持 30+ 语言 (核心保留, 需克隆后配置 DEEPL_API_KEY)'),
```

**优先级**: P2

---

### 3.9 E09 — MCP TaskGroup 异常

**错误消息**:
```
WARNING:src.skills.researcher.mcp_coordinator:MCP 执行失败: unhandled errors in a TaskGroup (1 sub-exception)
```

**根因分析**:
`MultiServerMCPClient.get_tools()` 内部用 `asyncio.TaskGroup` 并发启动多个 stdio client, 任一 client 启动失败 (如 E02-E08 的占位符/缺失依赖问题) 会导致 `ExceptionGroup` 抛出。此时失败的 client 已被构造并放入 `_client_cache`, 后续请求复用该僵尸 client 必然再次失败, 形成永久性故障。

**修复方案**:
本错误根因是 E02-E08 的 MCP 启动失败 + A6 的缓存未清理。修复 A6 (见 §4.6) 后, 失败的 client 会从缓存移除并 aclose, 避免僵尸 client 复用。

**涉及文件**:
- `src/skills/researcher/mcp_coordinator.py` (行 344-389)

**详细修复见 §4.6 (A6)**。

**优先级**: P0

---

### 3.10 E10 — Tavily 搜索 432 错误

**错误消息**:
```
WARNING:src.skills.researcher.searchers.tavily:Tavily 搜索失败: Client error '432'
```

**根因分析**:
Tavily API 返回 432 状态码 (非标准 HTTP 状态码, Tavily 自定义), 表示 API 配额耗尽或 API Key 无效。

**修复方案**:
代码层面无需修改 — `tavily.py` 的 `except Exception` 分支已正确降级返回空列表 (行 95-98), 不阻断研究流程。仅需配置有效的 `TAVILY_API_KEY` 环境变量。

**涉及文件**:
- 无需代码修改
- `.env` / `.env.qa` 配置有效的 `TAVILY_API_KEY`

**可选增强 (tavily.py 增加 432 错误识别)**:

修改前 (行 68-75):
```python
response = await self._client.post(self._api_url, json=payload)
if response.status_code == 429:
    reset_at = self._calc_quota_reset(response)
    raise QuotaExceededError(
        engine="tavily",
        reset_at=reset_at,
        message="Tavily 月度额度已满",
    )
response.raise_for_status()
```

修改后:
```python
response = await self._client.post(self._api_url, json=payload)
if response.status_code == 429:
    reset_at = self._calc_quota_reset(response)
    raise QuotaExceededError(
        engine="tavily",
        reset_at=reset_at,
        message="Tavily 月度额度已满",
    )
if response.status_code == 432:
    # Tavily 自定义状态码: API Key 无效或配额耗尽
    logger.warning("Tavily API Key 无效或配额耗尽 (HTTP 432), 请检查 TAVILY_API_KEY")
    return []
response.raise_for_status()
```

**优先级**: P2

---

### 3.11 E11 — GDELT 搜索 429 限流

**错误消息**:
```
WARNING:src.skills.researcher.searchers.gdelt:gdelt HTTP 429: Please limit requests to one every 5 seconds
```

**根因分析**:
GDELT 免费 API 限流为每 5 秒 1 次请求, 当 Agent 并发执行多个子查询时, 短时间内多次调用 GDELT 触发 429 限流。

**修复方案**:
在 `GDELTSearcher` 中增加请求间隔限流 (最少 5 秒间隔), 用模块级时间戳 + `asyncio.Lock` 保证并发请求串行化并遵守间隔。

**涉及文件**:
- `src/skills/researcher/searchers/gdelt.py`

**代码修改 (gdelt.py 增加限流)**:

修改前 (完整文件):
```python
"""GDELT 新闻搜索 (gdeltproject.org, v1.1 新增).

40 年全球新闻事件数据库, 完全免费.
- cost_tier: free (完全免费)
- quality_score: 65.0
- region: AUTO (CN+GLOBAL 都可用)
- API: https://api.gdeltproject.org/api/v2/doc/doc
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config.settings import Settings
from src.skills.researcher.searchers import BaseSearcher, SearchRegion, register_searcher

logger = logging.getLogger(__name__)


@register_searcher("gdelt")
class GDELTSearcher(BaseSearcher):
    """GDELT 新闻搜索器.

    40 年全球新闻事件数据库, 完全免费.
    """

    name = "gdelt"
    region = SearchRegion.AUTO
    cost_tier = "free"
    quality_score = 65.0

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://api.gdeltproject.org/api/v2/doc/doc"

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """调用 GDELT API."""
        params = {
            "query": query,
            "mode": "ArtList",
            "maxrecords": str(max_results),
            "format": "json",
            "sort": "DateDesc",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(self.base_url, params=params)
        except Exception as e:
            logger.warning(f"gdelt 调用失败: {e}")
            return []

        if resp.status_code != 200:
            logger.warning(f"gdelt HTTP {resp.status_code}: {resp.text[:200]}")
            return []

        try:
            data = resp.json()
        except Exception as e:
            # GDELT 有时返回非标准 JSON, 降级处理
            logger.warning(f"gdelt JSON 解析失败: {e}")
            return []

        results: list[dict[str, Any]] = []
        for item in (data.get("articles") or [])[:max_results]:
            title = item.get("title") or ""
            url = item.get("url") or ""
            snippet = item.get("socialimage") or item.get("summary") or ""
            if url:
                results.append(self._normalize_result(title, url, snippet))

        return self._filter_by_domains(results, query_domains)
```

修改后:
```python
"""GDELT 新闻搜索 (gdeltproject.org, v1.1 新增).

40 年全球新闻事件数据库, 完全免费.
- cost_tier: free (完全免费)
- quality_score: 65.0
- region: AUTO (CN+GLOBAL 都可用)
- API: https://api.gdeltproject.org/api/v2/doc/doc
- 限流: 免费 API 每 5 秒 1 次, 模块级时间戳 + asyncio.Lock 强制间隔
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from src.config.settings import Settings
from src.skills.researcher.searchers import BaseSearcher, SearchRegion, register_searcher

logger = logging.getLogger(__name__)

# GDELT 免费 API 限流: 每 5 秒 1 次请求
_GDELT_MIN_INTERVAL = 5.0
# 模块级上次请求时间 (跨实例共享, 所有 GDELTSearcher 实例共用一个限流器)
_gdelt_last_request_time: float = 0.0
_gdelt_lock = asyncio.Lock()


@register_searcher("gdelt")
class GDELTSearcher(BaseSearcher):
    """GDELT 新闻搜索器.

    40 年全球新闻事件数据库, 完全免费.
    内置 5 秒间隔限流 (模块级, 跨实例共享), 避免 429.
    """

    name = "gdelt"
    region = SearchRegion.AUTO
    cost_tier = "free"
    quality_score = 65.0

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://api.gdeltproject.org/api/v2/doc/doc"

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        query_domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """调用 GDELT API (内置 5 秒间隔限流)."""
        params = {
            "query": query,
            "mode": "ArtList",
            "maxrecords": str(max_results),
            "format": "json",
            "sort": "DateDesc",
        }

        # 模块级限流: 保证并发请求串行化 + 5 秒间隔
        global _gdelt_last_request_time
        async with _gdelt_lock:
            elapsed = time.monotonic() - _gdelt_last_request_time
            if elapsed < _GDELT_MIN_INTERVAL:
                wait = _GDELT_MIN_INTERVAL - elapsed
                logger.debug("gdelt 限流等待 %.1fs", wait)
                await asyncio.sleep(wait)
            _gdelt_last_request_time = time.monotonic()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(self.base_url, params=params)
        except Exception as e:
            logger.warning(f"gdelt 调用失败: {e}")
            return []

        if resp.status_code != 200:
            logger.warning(f"gdelt HTTP {resp.status_code}: {resp.text[:200]}")
            return []

        try:
            data = resp.json()
        except Exception as e:
            # GDELT 有时返回非标准 JSON, 降级处理
            logger.warning(f"gdelt JSON 解析失败: {e}")
            return []

        results: list[dict[str, Any]] = []
        for item in (data.get("articles") or [])[:max_results]:
            title = item.get("title") or ""
            url = item.get("url") or ""
            snippet = item.get("socialimage") or item.get("summary") or ""
            if url:
                results.append(self._normalize_result(title, url, snippet))

        return self._filter_by_domains(results, query_domains)
```

**优先级**: P1

---

### 3.12 E12 — LiteLLM model cost map 超时

**错误消息**:
```
LiteLLM:WARNING: Failed to fetch remote model cost map from https://raw.githubusercontent.com: timed out. Falling back to local backup.
```

**根因分析**:
容器无法访问 `raw.githubusercontent.com` (内网环境或网络抖动), LiteLLM 启动时尝试获取远程 model cost map 超时, 自动降级到本地备份。

**修复方案**:
代码层面无需修改 — LiteLLM 的降级机制已正确实现 (超时后使用本地备份, 不影响功能)。可配置环境变量 `LITELLM_LOCAL_MODEL_COST_MAP=True` 跳过远程获取, 消除启动时的超时等待。

**涉及文件**:
- 无需代码修改
- `.env` / `.env.qa` 增加 `LITELLM_LOCAL_MODEL_COST_MAP=True`

**配置修改**:

在 `.env` / `.env.qa` 中增加:
```bash
# 跳过 LiteLLM 远程 model cost map 获取 (内网环境无法访问 raw.githubusercontent.com)
LITELLM_LOCAL_MODEL_COST_MAP=True
```

**优先级**: P2

---

### 3.13 E13 — 网页抓取失败 (DNS/SSL/403/500/404)

**错误消息**:
```
[Errno -2] Name or service not known                    # DNS 解析失败
[SSL: CERTIFICATE_VERIFY_FAILED] certificate has expired # SSL 证书过期
HTTP 403 (快速失败, 不降级)                              # 访问禁止
HTTP 500 (快速失败, 不降级)                              # 服务器错误
HTTP 404 (快速失败, 不降级)                              # 页面不存在
net::ERR_NAME_NOT_RESOLVED                               # DNS 解析失败 (Playwright)
net::ERR_CERT_DATE_INVALID                               # 证书过期 (Playwright)
Server disconnected without sending a response           # 服务器断连
```

**根因分析**:
目标网站本身不可访问 (域名不存在/证书过期/权限拒绝/服务器故障/页面删除), 属预期错误, 非代码缺陷。

**修复方案**:
无需代码修改 — 快速失败机制已正确实现 (Trafilatura/BS/Playwright 抓取失败时降级返回空结果或跳过, 不阻断研究流程)。

**涉及文件**:
- 无需代码修改

**优先级**: P2 (预期错误, 无需修复)

---

### 3.14 E14 — mcp_atlassian TOOLSETS 警告

**错误消息**:
```
WARNING - mcp_atlassian.utils.toolsets - TOOLSETS is not set — currently defaults to all toolsets
```

**根因分析**:
`mcp-atlassian` (Confluence MCP) 启动时未设置 `TOOLSETS` 环境变量, 默认加载所有 toolsets (Jira + Confluence), 但用户仅需 Confluence, 打印警告。

**修复方案**:
在 `init.sql` 的 confluence MCP args 中增加 `--toolsets=confluence`, 限制仅加载 Confluence toolset。

**涉及文件**:
- `scripts/init.sql`

**代码修改 (init.sql)**:

修改前 (行 303-306):
```sql
(NULL, 'system', 'confluence', NULL, 'stdio', 'uvx',
 '["mcp-atlassian", "--confluence-url", "<your-confluence-url>", "--confluence-username", "<your-email>", "--confluence-token", "<your-token>"]'::jsonb,
 '{"ATLASSIAN_SITE_NAME": "<your-site>", "ATLASSIAN_USER_EMAIL": "<your-email>", "ATLASSIAN_API_TOKEN": "<your-token>"}'::jsonb,
 TRUE, TRUE, 3, 'Confluence: 维基内容/空间/页面管理 (PyPI 实现 mcp-atlassian, 需配置 ATLASSIAN_API_TOKEN)'),
```

修改后:
```sql
(NULL, 'system', 'confluence', NULL, 'stdio', 'uvx',
 '["mcp-atlassian", "--confluence-url", "<your-confluence-url>", "--confluence-username", "<your-email>", "--confluence-token", "<your-token>", "--toolsets=confluence"]'::jsonb,
 '{"ATLASSIAN_SITE_NAME": "<your-site>", "ATLASSIAN_USER_EMAIL": "<your-email>", "ATLASSIAN_API_TOKEN": "<your-token>"}'::jsonb,
 FALSE, TRUE, 4, 'Confluence: 维基内容/空间/页面管理 (PyPI 实现 mcp-atlassian, 需克隆后配置 ATLASSIAN_API_TOKEN)'),
```

> 注: 同时改 `enabled=FALSE` (因含占位符凭据, 符合克隆流程)。

**优先级**: P2

---

### 3.15 E15 — authlib 弃用警告

**错误消息**:
```
AuthlibDeprecationWarning: authlib.jose module is deprecated, please use joserfc instead
```

**根因分析**:
`fastmcp` 依赖的 `authlib.jose` 模块已弃用, 官方推荐迁移到 `joserfc`。这是第三方库的弃用警告, 不影响功能。

**修复方案**:
无需代码修改 — 等待 `fastmcp` 上游升级到 `joserfc`。可通过 `warnings.filterwarnings` 过滤该警告 (可选)。

**涉及文件**:
- 无需代码修改

**可选增强 (过滤警告, server.py)**:

在 `server.py` 顶部增加 (可选):
```python
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning, module="authlib")
```

**优先级**: P2

---

## 4. MCP 系统性架构问题修复方案

### 4.1 A1 — 多实例导致缓存隔离失效

**问题描述**:
`deep_research.py:147` 和 `research_conductor.py:97` 各自构造 `MCPCoordinator` 实例 (`self._mcp = MCPCoordinator(self.settings, self._llm)`), 而 `conduct_mcp_if_enabled` (行 151) 走全局单例 `get_mcp_coordinator()`。导致:
- fast 策略缓存跨实例不复用 (同一 query 重复调用 MCP 工具)
- `_client_cache` 跨实例不共享 (重复构造 MultiServerMCPClient, 启动多个 stdio 子进程)
- `clear_cache()` 仅清当前实例, 其他实例缓存仍有效 (配置变更后命中过期缓存)

**修复方案**:
两处 `_get_mcp()` 改用 `get_mcp_coordinator()` 全局单例, 删除 `self._mcp` 字段。

**涉及文件**:
- `src/skills/researcher/deep_research.py` (行 121-148)
- `src/skills/researcher/research_conductor.py` (行 66-98)

**代码修改 (deep_research.py)**:

修改前 (行 121-148):
```python
    # MCPCoordinator 惰性初始化
    _mcp: MCPCoordinator | None

    def __init__(
        self,
        ...
    ) -> None:
        ...
        # MCPCoordinator 惰性初始化 (避免启动期构造开销)
        self._mcp = None

    def _get_mcp(self) -> MCPCoordinator:
        """惰性初始化 MCPCoordinator.

        复用 self._llm 单例, 避免重复构造 LLMClient 导致 step_costs 累计丢失.
        """
        if self._mcp is None:
            self._mcp = MCPCoordinator(self.settings, self._llm)
        return self._mcp
```

修改后:
```python
    def _get_mcp(self) -> MCPCoordinator:
        """获取全局 MCPCoordinator 单例.

        复用 get_mcp_coordinator() 全局单例, 确保 fast 策略缓存与 _client_cache
        跨调用方共享 (deep_research / research_conductor / conduct_mcp_if_enabled 共用同一实例).
        """
        from src.skills.researcher.mcp_coordinator import get_mcp_coordinator
        return get_mcp_coordinator()
```

> 注: 删除 `self._mcp` 字段声明与 `__init__` 中的初始化, 同时保留 `MCPCoordinator` 类型导入 (用于类型注解) 或改为 `Any` 类型。

**代码修改 (research_conductor.py)**:

修改前 (行 66-98):
```python
    # MCPCoordinator 惰性初始化
    _mcp: MCPCoordinator | None
    ...

    def _get_mcp(self) -> MCPCoordinator:
        """惰性初始化 MCPCoordinator.

        复用 self._llm 单例, 避免重复构造 LLMClient 导致 step_costs 累计丢失.
        """
        if self._mcp is None:
            self._mcp = MCPCoordinator(self.settings, self._llm)
        return self._mcp
```

修改后:
```python
    def _get_mcp(self) -> MCPCoordinator:
        """获取全局 MCPCoordinator 单例.

        复用 get_mcp_coordinator() 全局单例, 确保 fast 策略缓存与 _client_cache
        跨调用方共享 (deep_research / research_conductor / conduct_mcp_if_enabled 共用同一实例).
        """
        from src.skills.researcher.mcp_coordinator import get_mcp_coordinator
        return get_mcp_coordinator()
```

**优先级**: P0

---

### 4.2 A2 — mcp_routes.py CRUD 后未清空缓存

**问题描述**:
`mcp_routes.py` 的 create/update/delete/clone 4 个端点成功后未调用 `clear_cache()`, 导致用户修改 MCP 配置后, `MCPCoordinator` 的 `_client_cache` (MultiServerMCPClient 缓存) 与 `_MCP_CACHE` (工具调用结果 TTL 缓存) 仍命中旧配置, 用户感知"配置改了但没生效"。

**修复方案**:
在 4 个 CRUD 端点的成功返回前调用 `get_mcp_coordinator().clear_cache()`。

**涉及文件**:
- `src/api/mcp_routes.py` (create_mcp_config / update_mcp_config / delete_mcp_config / clone_system_mcp_config)

**代码修改 (mcp_routes.py, 4 处)**:

**1. create_mcp_config (行 528-529, return 前)**:

修改前:
```python
    saved["test_result"] = test_result
    return saved
```

修改后:
```python
    saved["test_result"] = test_result
    # 配置变更后清空 MCPCoordinator 缓存 (避免命中旧配置)
    from src.skills.researcher.mcp_coordinator import get_mcp_coordinator
    get_mcp_coordinator().clear_cache()
    return saved
```

**2. update_mcp_config (行 613, return 前)**:

修改前:
```python
    return _mcp_row_to_dict(row)
```

修改后:
```python
    # 配置变更后清空 MCPCoordinator 缓存 (避免命中旧配置)
    from src.skills.researcher.mcp_coordinator import get_mcp_coordinator
    get_mcp_coordinator().clear_cache()
    return _mcp_row_to_dict(row)
```

> 注: update_mcp_config 有两个 return 点 (测试失败拒绝启用 + 正常更新), 两处均需调用 clear_cache()。

**3. delete_mcp_config (行 631, return 前)**:

修改前:
```python
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="MCP 配置不存在或为系统配置 (不可删除)")
    return {"deleted": True}
```

修改后:
```python
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="MCP 配置不存在或为系统配置 (不可删除)")
    # 配置删除后清空 MCPCoordinator 缓存 (避免命中已删除配置)
    from src.skills.researcher.mcp_coordinator import get_mcp_coordinator
    get_mcp_coordinator().clear_cache()
    return {"deleted": True}
```

**4. clone_system_mcp_config (行 454, return 前)**:

修改前:
```python
    return _mcp_row_to_dict(row)
```

修改后:
```python
    # 克隆后清空 MCPCoordinator 缓存 (虽然克隆的配置 enabled=FALSE, 但仍需失效缓存)
    from src.skills.researcher.mcp_coordinator import get_mcp_coordinator
    get_mcp_coordinator().clear_cache()
    return _mcp_row_to_dict(row)
```

**优先级**: P0

---

### 4.3 A3 — `_test_mcp_config` 测试连接后未关闭

**问题描述**:
`_test_mcp_config` 仅在 `TimeoutError` 分支 (行 241-259) 调用 `aclose` 清理 client, 成功路径 (行 261-269) 和其他异常路径 (行 270-312) 未关闭 client。stdio 模式的 `MultiServerMCPClient` 持有子进程, 未关闭会导致子进程泄漏 (每次测试遗留一个 stdio 子进程)。

**修复方案**:
用 `try/finally` 统一清理 client, 确保所有路径都调用 `aclose`。

**涉及文件**:
- `src/api/mcp_routes.py` (行 237-312)

**代码修改 (mcp_routes.py)**:

修改前 (行 237-312):
```python
    # 测试连接 + 列工具 (30s 超时)
    try:
        client = MultiServerMCPClient(server_configs)
        try:
            tools = await asyncio.wait_for(client.get_tools(), timeout=_MCP_TEST_TIMEOUT)
        except TimeoutError:
            # 超时后清理子进程
            try:
                close_fn = getattr(client, "aclose", None) or getattr(client, "close", None)
                if close_fn:
                    result = close_fn()
                    if hasattr(result, "__await__"):
                        await result
            except Exception as cleanup_err:  # noqa: BLE001
                logger.debug("MCP client cleanup after timeout failed: %s", cleanup_err)
            return {
                "success": False,
                "message": f"测试超时 ({_MCP_TEST_TIMEOUT}s), MCP 服务未响应",
                "error_type": "timeout",
                "tools_count": 0,
                "tools": [],
                "latency_ms": int((time.time() - start) * 1000),
            }
        tool_names = [getattr(t, "name", "") for t in tools[:10]]
        latency_ms = int((time.time() - start) * 1000)
        return {
            "success": True,
            "message": f"连接成功, 发现 {len(tools)} 个工具",
            "error_type": None,
            "tools_count": len(tools),
            "tools": tool_names,
            "latency_ms": latency_ms,
        }
    except FileNotFoundError:
        ...
    except Exception as e:  # noqa: BLE001
        ...
```

修改后:
```python
    # 测试连接 + 列工具 (30s 超时)
    # 使用 try/finally 确保所有路径都清理 client (stdio 子进程不泄漏)
    client = MultiServerMCPClient(server_configs)
    try:
        try:
            tools = await asyncio.wait_for(client.get_tools(), timeout=_MCP_TEST_TIMEOUT)
        except TimeoutError:
            return {
                "success": False,
                "message": f"测试超时 ({_MCP_TEST_TIMEOUT}s), MCP 服务未响应",
                "error_type": "timeout",
                "tools_count": 0,
                "tools": [],
                "latency_ms": int((time.time() - start) * 1000),
            }
        tool_names = [getattr(t, "name", "") for t in tools[:10]]
        latency_ms = int((time.time() - start) * 1000)
        return {
            "success": True,
            "message": f"连接成功, 发现 {len(tools)} 个工具",
            "error_type": None,
            "tools_count": len(tools),
            "tools": tool_names,
            "latency_ms": latency_ms,
        }
    except FileNotFoundError:
        # npx/uvx 命令不存在 (容器内未安装 Node.js 等)
        cmd = config.get("command", "")
        hint = ""
        if cmd in ("npx", "npx.cmd"):
            hint = " (容器未安装 Node.js, npx 类 MCP 不可用)"
        elif cmd in ("uvx",):
            hint = " (容器未安装 uvx, 请改用其他启动方式)"
        logger.warning("MCP 测试失败 (name=%s): 启动命令不存在 %s", name, cmd)
        return {
            "success": False,
            "message": f"启动命令不存在: {cmd}{hint}",
            "error_type": "command_not_found",
            "tools_count": 0,
            "tools": [],
            "latency_ms": int((time.time() - start) * 1000),
        }
    except Exception as e:  # noqa: BLE001
        err_msg = str(e)
        err_type = type(e).__name__
        logger.warning("MCP 测试失败 (name=%s, type=%s): %s", name, err_type, err_msg)
        error_type = "unknown"
        err_lower = err_msg.lower()
        if "e404" in err_lower or "not found" in err_lower or "404" in err_lower:
            error_type = "package_not_found"
        elif "econnrefused" in err_lower or "connection refused" in err_lower:
            error_type = "connection_refused"
        elif "etimedout" in err_lower or "timeout" in err_lower or "timed out" in err_lower:
            error_type = "timeout"
        elif "handshake" in err_lower or "protocol" in err_lower:
            error_type = "handshake_failed"
        display_msg = err_msg[:500] + "..." if len(err_msg) > 500 else err_msg
        return {
            "success": False,
            "message": f"连接失败: {display_msg}",
            "error_type": error_type,
            "tools_count": 0,
            "tools": [],
            "latency_ms": int((time.time() - start) * 1000),
        }
    finally:
        # 统一清理 client (成功/超时/异常所有路径都执行, stdio 子进程不泄漏)
        try:
            close_fn = getattr(client, "aclose", None) or getattr(client, "close", None)
            if close_fn:
                result = close_fn()
                if hasattr(result, "__await__"):
                    await result
        except Exception as cleanup_err:  # noqa: BLE001
            logger.debug("MCP client cleanup failed: %s", cleanup_err)
```

**优先级**: P0

---

### 4.4 A4 — `_client_cache` 无限增长

**问题描述**:
`_client_cache` 字典只增不减 (行 219-221), 每次修改 MCP 配置产生新 key (hash 变化), 旧 client 被孤立。长时间运行后 `_client_cache` 无限增长, 每个孤立的 `MultiServerMCPClient` 持有 stdio 子进程, 导致进程数泄漏。

**修复方案**:
用 `OrderedDict` 实现 LRU 淘汰, 上限 8-16 项, 淘汰时调用 `aclose` 释放子进程。

**涉及文件**:
- `src/skills/researcher/mcp_coordinator.py` (行 181, 194-221, 615-638)

**代码修改 (mcp_coordinator.py)**:

修改前 (行 181):
```python
    # MultiServerMCPClient 缓存, key = hash(server_configs)
    _client_cache: dict[str, Any]
```

修改后:
```python
    # MultiServerMCPClient 缓存, key = hash(server_configs)
    # OrderedDict 实现 LRU 淘汰, 上限 16 项, 淘汰时 aclose 释放 stdio 子进程
    _client_cache: OrderedDict[str, Any]
    _CLIENT_CACHE_MAX_SIZE = 16
```

修改前 (行 192):
```python
        self._client_cache = {}
```

修改后:
```python
        self._client_cache = OrderedDict()
```

修改前 (行 219-221, `_get_or_create_client` 中):
```python
        if key not in self._client_cache:
            self._client_cache[key] = MultiServerMCPClient(server_configs)
        return self._client_cache[key]
```

修改后:
```python
        if key not in self._client_cache:
            # LRU 淘汰: 超过上限时弹出最旧项, 并 aclose 释放 stdio 子进程
            while len(self._client_cache) >= self._CLIENT_CACHE_MAX_SIZE:
                old_key, old_client = self._client_cache.popitem(last=False)
                try:
                    close_fn = getattr(old_client, "aclose", None) or getattr(old_client, "close", None)
                    if close_fn:
                        result = close_fn()
                        if hasattr(result, "__await__"):
                            asyncio.get_event_loop().create_task(result)
                except Exception as e:  # noqa: BLE001
                    logger.debug("MCP client LRU 淘汰清理失败: %s", e)
            self._client_cache[key] = MultiServerMCPClient(server_configs)
        else:
            # 命中时移动到末尾 (LRU 最近使用)
            self._client_cache.move_to_end(key)
        return self._client_cache[key]
```

**clear_cache 增强 (行 636)**:

修改前:
```python
        # MultiServerMCPClient 缓存 (避免配置变更后复用旧客户端)
        self._client_cache.clear()
```

修改后:
```python
        # MultiServerMCPClient 缓存 (避免配置变更后复用旧客户端)
        # 同步 aclose 所有缓存的 client (释放 stdio 子进程)
        for client in self._client_cache.values():
            try:
                close_fn = getattr(client, "aclose", None) or getattr(client, "close", None)
                if close_fn:
                    result = close_fn()
                    if hasattr(result, "__await__"):
                        # clear_cache 是同步方法, 用 create_task 异步关闭
                        asyncio.get_event_loop().create_task(result)
            except Exception as e:  # noqa: BLE001
                logger.debug("MCP client clear_cache 清理失败: %s", e)
        self._client_cache.clear()
```

**优先级**: P0

---

### 4.5 A5 — MCPCoordinator 缺失 `close()` 方法

**问题描述**:
`MCPCoordinator` 没有 `close()` 方法, `server.py` lifespan 关闭时未清理 MCPCoordinator, 导致 `_client_cache` 中的 stdio 子进程被 SIGKILL (而非优雅退出), 可能产生僵尸进程或资源泄漏。

**修复方案**:
1. 在 `MCPCoordinator` 新增 `async def close()` 方法, aclose 所有 `_client_cache` 中的 client。
2. 在 `server.py` lifespan 的 yield 之后调用 `get_mcp_coordinator().close()`。

**涉及文件**:
- `src/skills/researcher/mcp_coordinator.py` (新增 close 方法)
- `server.py` (lifespan 调用 close)

**代码修改 (mcp_coordinator.py, 新增 close 方法, 在 clear_cache 之后)**:

新增:
```python
    async def close(self) -> None:
        """优雅关闭 MCPCoordinator (释放所有缓存的 stdio 子进程).

        server.py lifespan 关闭时调用, 确保 _client_cache 中的
        MultiServerMCPClient 被正确 aclose, 避免 stdio 子进程被 SIGKILL.

        幂等: 无实例时直接返回.
        """
        for key, client in list(self._client_cache.items()):
            try:
                close_fn = getattr(client, "aclose", None) or getattr(client, "close", None)
                if close_fn:
                    result = close_fn()
                    if hasattr(result, "__await__"):
                        await result
            except Exception as e:  # noqa: BLE001
                logger.debug("MCP client close 失败 (key=%s): %s", key[:8], e)
        self._client_cache.clear()
        logger.info("MCPCoordinator 已关闭 (释放 %d 个 MCP client)", len(self._client_cache))
```

**代码修改 (server.py, lifespan yield 之后, 在 close_redis_client 之前)**:

修改前 (行 136-141):
```python
    yield

    # 关闭全局 Redis 单例 (由 common.redis_client 统一工厂创建, lifespan 统一关闭)
    from src.common.redis_client import close_redis_client

    await close_redis_client()
```

修改后:
```python
    yield

    # 关闭全局 MCPCoordinator 单例 (释放所有缓存的 stdio 子进程, 避免被 SIGKILL)
    try:
        from src.skills.researcher.mcp_coordinator import get_mcp_coordinator

        await get_mcp_coordinator().close()
    except Exception as e:  # noqa: BLE001
        logger.warning("MCPCoordinator 关闭失败 (不阻断 shutdown): %s", e)

    # 关闭全局 Redis 单例 (由 common.redis_client 统一工厂创建, lifespan 统一关闭)
    from src.common.redis_client import close_redis_client

    await close_redis_client()
```

**优先级**: P0

---

### 4.6 A6 — TaskGroup 异常导致资源泄漏

**问题描述**:
`_execute_mcp` 中 `client.get_tools()` (行 348) 失败时 (如 TaskGroup 抛出 ExceptionGroup), client 仍留在 `_client_cache` 中成为僵尸, 后续请求复用该僵尸 client 必然再次失败, 形成永久性故障。

**修复方案**:
`get_tools()` 失败后, 从 `_client_cache` 移除该 client 并 aclose, 确保下次请求重新构造 client。

**涉及文件**:
- `src/skills/researcher/mcp_coordinator.py` (行 344-389)

**代码修改 (mcp_coordinator.py)**:

修改前 (行 343-389):
```python
            # 复用缓存的 MultiServerMCPClient (相同配置不重复构建)
            client = self._get_or_create_client(server_configs)
            if client is None:
                logger.warning("langchain-mcp-adapters 未安装, MCP 数据源不可用")
                return []
            tools = await client.get_tools()

            if not tools:
                logger.warning("MCP 未返回任何工具")
                return []

            # LLM 智能选工具 + 生成参数
            max_tools = self.settings.mcp_max_tools
            selected = await self._select_tool_with_llm(
                query,
                tools,
                max_tools,
                user_id=user_id,
                session_id=session_id,
            )

            # 并发执行工具调用 (asyncio.gather + 信号量, 默认并发 3)
            # 单个工具失败返回 None 不影响其他工具; 保留 TTL 缓存逻辑
            # 透传 agent_id + user_id 用于缓存 key 隔离 (避免跨 Agent / 跨用户缓存串扰)
            cache_enabled = self.settings.mcp_cache_enabled
            sem = asyncio.Semaphore(MCP_MAX_CONCURRENCY)
            results = await asyncio.gather(
                *[
                    self._call_single_tool(
                        tool,
                        args,
                        query,
                        cache_enabled,
                        sem,
                        agent_id=agent_id,
                        user_id=user_id,
                    )
                    for tool, args in selected
                ],
                return_exceptions=False,
            )
            contexts: list[str] = [r for r in results if r is not None]

            return contexts
        except Exception as e:  # noqa: BLE001
            logger.warning("MCP 执行失败: %s", e)
            return []
```

修改后:
```python
            # 复用缓存的 MultiServerMCPClient (相同配置不重复构建)
            client = self._get_or_create_client(server_configs)
            if client is None:
                logger.warning("langchain-mcp-adapters 未安装, MCP 数据源不可用")
                return []

            # get_tools() 可能抛 ExceptionGroup (TaskGroup 任一 stdio client 启动失败)
            # 失败后必须从 _client_cache 移除并 aclose, 避免僵尸 client 复用导致永久故障
            client_cache_key = None
            try:
                tools = await client.get_tools()
            except Exception as get_tools_err:  # noqa: BLE001
                logger.warning(
                    "MCP get_tools 失败 (清理僵尸 client): %s", get_tools_err
                )
                # 从 _client_cache 移除该 client (按值匹配)
                for k, v in list(self._client_cache.items()):
                    if v is client:
                        client_cache_key = k
                        break
                if client_cache_key is not None:
                    self._client_cache.pop(client_cache_key, None)
                # aclose 释放 stdio 子进程
                try:
                    close_fn = getattr(client, "aclose", None) or getattr(client, "close", None)
                    if close_fn:
                        result = close_fn()
                        if hasattr(result, "__await__"):
                            await result
                except Exception as cleanup_err:  # noqa: BLE001
                    logger.debug("MCP 僵尸 client 清理失败: %s", cleanup_err)
                return []

            if not tools:
                logger.warning("MCP 未返回任何工具")
                return []

            # LLM 智能选工具 + 生成参数
            max_tools = self.settings.mcp_max_tools
            selected = await self._select_tool_with_llm(
                query,
                tools,
                max_tools,
                user_id=user_id,
                session_id=session_id,
            )

            # 并发执行工具调用 (asyncio.gather + 信号量, 默认并发 3)
            # 单个工具失败返回 None 不影响其他工具; 保留 TTL 缓存逻辑
            # 透传 agent_id + user_id 用于缓存 key 隔离 (避免跨 Agent / 跨用户缓存串扰)
            cache_enabled = self.settings.mcp_cache_enabled
            sem = asyncio.Semaphore(MCP_MAX_CONCURRENCY)
            results = await asyncio.gather(
                *[
                    self._call_single_tool(
                        tool,
                        args,
                        query,
                        cache_enabled,
                        sem,
                        agent_id=agent_id,
                        user_id=user_id,
                    )
                    for tool, args in selected
                ],
                return_exceptions=False,
            )
            contexts: list[str] = [r for r in results if r is not None]

            return contexts
        except Exception as e:  # noqa: BLE001
            # 记录失败的 server_configs 名称, 便于定位是哪个 MCP 报错
            failed_names = list(server_configs.keys()) if 'server_configs' in locals() else []
            logger.warning("MCP 执行失败 (servers=%s): %s", failed_names, e)
            return []
```

**优先级**: P0

---

### 4.7 C1 — 并发请求导致 stdio 进程倍增

**问题描述**:
`_get_or_create_client` (行 194-221) 是同步方法, 无锁。当多个并发请求同时到达且 `_client_cache` 未命中时, 各自构造 `MultiServerMCPClient` 实例, 每个 stdio MCP 配置启动多个子进程 (进程倍增), 导致资源泄漏 + stdio 端口冲突。

**修复方案**:
将 `_get_or_create_client` 改为 async + `asyncio.Lock` + double-check 模式, 保证同一 server_configs 只构造一个 client。

**涉及文件**:
- `src/skills/researcher/mcp_coordinator.py` (行 194-221, 344)

**代码修改 (mcp_coordinator.py)**:

修改前 (行 194-221):
```python
    def _get_or_create_client(self, server_configs: dict[str, Any]) -> Any | None:
        """缓存并复用 MultiServerMCPClient.

        避免每次 conduct_research 都重新构建客户端 (含连接初始化).
        key = hash(server_configs JSON 序列化), 相同配置复用客户端.

        Args:
            server_configs: MCP Server 配置字典 (name -> config)

        Returns:
            MultiServerMCPClient 实例, langchain-mcp-adapters 未安装时返回 None.
        """
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
        except ImportError:
            return None

        try:
            key = hashlib.sha256(
                json.dumps(server_configs, sort_keys=True, default=str).encode()
            ).hexdigest()
        except (TypeError, ValueError):
            # 序列化失败时直接构建 (无缓存)
            return MultiServerMCPClient(server_configs)

        if key not in self._client_cache:
            self._client_cache[key] = MultiServerMCPClient(server_configs)
        return self._client_cache[key]
```

修改后:
```python
    # 并发锁: 保证 _get_or_create_client 同一 server_configs 只构造一个 client
    _client_lock: asyncio.Lock

    async def _get_or_create_client(self, server_configs: dict[str, Any]) -> Any | None:
        """缓存并复用 MultiServerMCPClient (并发安全).

        避免每次 conduct_research 都重新构建客户端 (含连接初始化).
        key = hash(server_configs JSON 序列化), 相同配置复用客户端.
        使用 asyncio.Lock + double-check 保证并发请求不重复构造 client
        (避免 stdio 子进程倍增).

        Args:
            server_configs: MCP Server 配置字典 (name -> config)

        Returns:
            MultiServerMCPClient 实例, langchain-mcp-adapters 未安装时返回 None.
        """
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
        except ImportError:
            return None

        try:
            key = hashlib.sha256(
                json.dumps(server_configs, sort_keys=True, default=str).encode()
            ).hexdigest()
        except (TypeError, ValueError):
            # 序列化失败时直接构建 (无缓存)
            return MultiServerMCPClient(server_configs)

        # double-check: 先无锁检查 (快路径, 命中直接返回)
        if key in self._client_cache:
            self._client_cache.move_to_end(key)
            return self._client_cache[key]

        # 慢路径: 加锁构造 (避免并发请求各自构造 client)
        async with self._client_lock:
            # double-check: 拿到锁后再次检查 (可能已被其他请求构造)
            if key in self._client_cache:
                self._client_cache.move_to_end(key)
                return self._client_cache[key]

            # LRU 淘汰: 超过上限时弹出最旧项, 并 aclose 释放 stdio 子进程
            while len(self._client_cache) >= self._CLIENT_CACHE_MAX_SIZE:
                old_key, old_client = self._client_cache.popitem(last=False)
                try:
                    close_fn = getattr(old_client, "aclose", None) or getattr(old_client, "close", None)
                    if close_fn:
                        result = close_fn()
                        if hasattr(result, "__await__"):
                            await result
                except Exception as e:  # noqa: BLE001
                    logger.debug("MCP client LRU 淘汰清理失败: %s", e)

            client = MultiServerMCPClient(server_configs)
            self._client_cache[key] = client
            return client
```

**`__init__` 增加 lock (行 192 后)**:

修改前:
```python
        self._client_cache = {}
```

修改后:
```python
        self._client_cache = OrderedDict()
        self._client_lock = asyncio.Lock()
```

**调用方改为 await (行 344)**:

修改前:
```python
            client = self._get_or_create_client(server_configs)
```

修改后:
```python
            client = await self._get_or_create_client(server_configs)
```

**优先级**: P0

---

## 5. 修复优先级矩阵

### 5.1 P0 — 必须修复 (资源泄漏/永久故障)

| 编号 | 问题 | 修复工作量 | 风险 | 修复后效果 |
|------|------|----------|------|-----------|
| A1 | 多实例导致缓存隔离失效 | 小 (2 处改单例) | 低 | fast 策略缓存跨调用方共享, _client_cache 统一管理 |
| A2 | mcp_routes.py CRUD 后未清空缓存 | 小 (4 处加 clear_cache) | 低 | 配置变更即时生效, 不命中过期缓存 |
| A3 | _test_mcp_config 未关闭 client | 中 (重构 try/finally) | 低 | 测试连接不泄漏 stdio 子进程 |
| A4 | _client_cache 无限增长 | 中 (LRU + 淘汰 aclose) | 中 | 缓存上限 16 项, 淘汰时释放子进程 |
| A5 | MCPCoordinator 缺失 close() | 小 (新增方法 + lifespan) | 低 | 容器关闭时优雅退出 stdio 子进程 |
| A6 | TaskGroup 异常导致资源泄漏 | 中 (失败后移除 + aclose) | 中 | 僵尸 client 不复用, 下次请求重新构造 |
| C1 | 并发请求 stdio 进程倍增 | 中 (async + Lock) | 中 | 同一配置只构造一个 client, 子进程不倍增 |
| E09 | MCP TaskGroup 异常 (根因 A6) | 随 A6 修复 | - | 僵尸 client 清理后不再永久故障 |

### 5.2 P1 — 影响核心功能 (MCP 不可用/搜索降级)

| 编号 | 问题 | 修复工作量 | 风险 | 修复后效果 |
|------|------|----------|------|-----------|
| E01 | MCP JSONRPC 解析错误 | 随 E02-E08 修复 | - | stdio 子进程正常输出 JSON-RPC |
| E02 | obsidian MCP 路径错误 | 小 (init.sql) | 低 | 占位符 MCP 不自动启用 |
| E03 | git MCP 可执行文件缺失 | 小 (Dockerfile + init.sql) | 低 | git MCP 可正常启动 |
| E04 | sequential-thinking 模块缺失 | 小 (init.sql 禁用) | 低 | 不再报 ERR_MODULE_NOT_FOUND |
| E05 | supabase 模块缺失 | 小 (init.sql 禁用) | 低 | 不再报 ERR_MODULE_NOT_FOUND |
| E06 | filesystem 目录不存在 | 小 (init.sql) | 低 | 默认路径改为 /tmp/uploads |
| E11 | GDELT 429 限流 | 中 (加限流) | 低 | 5 秒间隔限流, 不触发 429 |
| E14 | mcp_atlassian TOOLSETS 警告 | 小 (init.sql) | 低 | 仅加载 Confluence toolset |

### 5.3 P2 — 日志噪声/第三方/预期错误

| 编号 | 问题 | 修复工作量 | 风险 | 修复后效果 |
|------|------|----------|------|-----------|
| E07 | twitter 凭据缺失 | 小 (init.sql) | 低 | 占位符 MCP 不自动启用 |
| E08 | deepl 鉴权失败 | 小 (init.sql) | 低 | 占位符 MCP 不自动启用 |
| E10 | Tavily 432 错误 | 无 (配置 API Key) | 无 | 配置有效 Key 后正常 |
| E12 | LiteLLM cost map 超时 | 无 (配置环境变量) | 无 | 跳过远程获取, 无超时等待 |
| E13 | 网页抓取失败 | 无 (预期错误) | 无 | 快速失败机制已正确 |
| E15 | authlib 弃用警告 | 无 (第三方库) | 无 | 等待上游升级 |

---

## 6. 验证方法

### 6.1 P0 修复验证 (MCP 系统性架构问题)

#### 6.1.1 A1 验证 — 多实例缓存隔离

```bash
# 1. 启动容器栈
docker compose -p agentinsight -f docker-compose.yml --env-file .env up -d

# 2. 发送研究请求 (触发 MCP 调用)
curl -X POST http://localhost:8066/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"用 MCP 搜索 AI 最新进展"}],"stream":false}'

# 3. 检查日志, 确认只有 1 个 MCPCoordinator 实例 (无重复构造)
docker logs agentinsight-agent-1 2>&1 | grep -i "MCPCoordinator\|get_mcp_coordinator"

# 4. 验证 _client_cache 跨调用方共享 (deep_research 与 research_conductor 复用同一 client)
# 预期: 日志中无 "MultiServerMCPClient" 重复构造, stdio 子进程数稳定
docker exec agentinsight-agent-1 ps aux | grep -c "npx\|uvx"
```

#### 6.1.2 A2 验证 — CRUD 后清空缓存

```bash
# 1. 创建 MCP 配置
curl -X POST http://localhost:8066/v1/mcp \
  -H "Content-Type: application/json" \
  -d '{"name":"test-mcp","transport_type":"stdio","command":"npx","args":["-y","mcp-hacker-news"],"enabled":false}'

# 2. 检查日志, 确认 clear_cache 被调用
docker logs agentinsight-agent-1 2>&1 | grep -i "clear_cache\|client_cache"

# 3. 更新/删除配置, 同样验证 clear_cache 调用
curl -X PUT http://localhost:8066/v1/mcp/1 \
  -H "Content-Type: application/json" \
  -d '{"name":"test-mcp-updated","transport_type":"stdio","command":"npx","args":["-y","mcp-hacker-news"],"enabled":false}'

curl -X DELETE http://localhost:8066/v1/mcp/1

# 4. 每次操作后日志应显示缓存清理
```

#### 6.1.3 A3 验证 — 测试连接后关闭 client

```bash
# 1. 测试一个 MCP 配置 (多次测试, 观察子进程数不增长)
for i in 1 2 3 4 5; do
  curl -X POST http://localhost:8066/v1/mcp/test \
    -H "Content-Type: application/json" \
    -d '{"name":"test","transport_type":"stdio","command":"npx","args":["-y","mcp-hacker-news"],"enabled":false}'
done

# 2. 检查 stdio 子进程数 (预期: 测试完成后子进程全部退出, 不残留)
docker exec agentinsight-agent-1 ps aux | grep -c "mcp-hacker-news"
# 预期: 0 (所有测试 client 已 aclose)
```

#### 6.1.4 A4 验证 — _client_cache LRU 淘汰

```bash
# 1. 长时间运行, 多次变更 MCP 配置 (产生不同 hash key)
# 2. 监控 _client_cache 大小 (可通过日志或添加 metrics)
# 3. 预期: _client_cache 大小不超过 16 项, 淘汰时 aclose 旧 client

# 4. 检查 stdio 子进程数稳定 (不无限增长)
docker exec agentinsight-agent-1 ps aux | grep -c "npx\|uvx"
```

#### 6.1.5 A5 验证 — close() 方法

```bash
# 1. 启动容器, 发送 MCP 请求 (构造 client)
# 2. 优雅停止容器
docker compose -p agentinsight stop agent

# 3. 检查日志, 确认 "MCPCoordinator 已关闭"
docker logs agentinsight-agent-1 2>&1 | grep -i "MCPCoordinator.*关闭"

# 4. 检查无僵尸 stdio 子进程 (容器内 ps 应为空)
docker exec agentinsight-agent-1 ps aux | grep -c "npx\|uvx"
# 预期: 0 (所有子进程已优雅退出)
```

#### 6.1.6 A6 验证 — TaskGroup 失败后清理僵尸 client

```bash
# 1. 配置一个会失败的 MCP (如占位符路径)
# 2. 发送研究请求, 触发 get_tools() 失败
curl -X POST http://localhost:8066/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"用 MCP 搜索"}],"stream":false}'

# 3. 检查日志, 确认 "清理僵尸 client"
docker logs agentinsight-agent-1 2>&1 | grep -i "清理僵尸 client\|get_tools 失败"

# 4. 再次发送请求, 确认不命中僵尸 client (重新构造)
# 预期: 第二次请求日志显示重新构造 MultiServerMCPClient, 不复用僵尸
```

#### 6.1.7 C1 验证 — 并发请求不重复构造 client

```bash
# 1. 并发发送 10 个相同查询的研究请求
for i in $(seq 1 10); do
  curl -X POST http://localhost:8066/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"messages":[{"role":"user","content":"用 MCP 搜索 AI"}],"stream":false}' &
done
wait

# 2. 检查 stdio 子进程数 (预期: 仅 1 组 stdio 子进程, 不倍增)
docker exec agentinsight-agent-1 ps aux | grep -c "npx\|uvx"
# 预期: 等于单个 MCP 配置的子进程数 (非 10 倍)
```

### 6.2 P1 修复验证 (MCP 配置/搜索限流)

#### 6.2.1 E02-E08 验证 — 占位符 MCP 禁用

```bash
# 1. 重启容器 (触发 init.sql v4 更新)
docker compose -p agentinsight -f docker-compose.yml --env-file .env up -d

# 2. 查询系统 MCP 列表, 确认占位符 MCP 的 enabled=FALSE
curl http://localhost:8066/v1/mcp/system | python -m json.tool | grep -A2 "enabled"

# 3. 检查日志, 确认无 ENOENT / ERR_MODULE_NOT_FOUND / AuthorizationError
docker logs agentinsight-agent-1 2>&1 | grep -i "ENOENT\|ERR_MODULE_NOT_FOUND\|AuthorizationError"
# 预期: 无输出 (或仅用户克隆后未配置的 MCP)
```

#### 6.2.2 E03 验证 — git MCP 可用

```bash
# 1. 容器内确认 git 已安装
docker exec agentinsight-agent-1 git --version
# 预期: git version 2.x.x

# 2. 克隆 git MCP 并配置真实仓库路径, 测试可用性
curl -X POST http://localhost:8066/v1/mcp/system/{git_config_id}/clone
curl -X PUT http://localhost:8066/v1/mcp/{cloned_id} \
  -H "Content-Type: application/json" \
  -d '{"name":"git","transport_type":"stdio","command":"uvx","args":["mcp-server-git","--repository","/app"],"enabled":true}'

# 3. 检查日志无 "Bad git executable"
docker logs agentinsight-agent-1 2>&1 | grep -i "Bad git executable"
# 预期: 无输出
```

#### 6.2.3 E11 验证 — GDELT 限流

```bash
# 1. 并发发送多个触发 GDELT 搜索的请求
for i in $(seq 1 5); do
  curl -X POST http://localhost:8066/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"messages":[{"role":"user","content":"搜索全球 AI 新闻"}],"stream":false}' &
done
wait

# 2. 检查日志, 确认无 429 限流 (有 "限流等待" 日志)
docker logs agentinsight-agent-1 2>&1 | grep -i "gdelt"
# 预期: 有 "gdelt 限流等待 X.Xs" 日志, 无 "HTTP 429"
```

### 6.3 P2 修复验证 (日志噪声)

#### 6.3.1 E12 验证 — LiteLLM cost map

```bash
# 1. 确认 .env 含 LITELLM_LOCAL_MODEL_COST_MAP=True
grep LITELLM_LOCAL_MODEL_COST_MAP .env

# 2. 重启容器, 检查日志无 "Failed to fetch remote model cost map"
docker logs agentinsight-agent-1 2>&1 | grep -i "model cost map"
# 预期: 无输出
```

#### 6.3.2 E13 验证 — 网页抓取快速失败

```bash
# 1. 发送含不可达 URL 的研究请求
# 2. 检查日志, 确认抓取失败被降级处理 (不阻断研究流程)
docker logs agentinsight-agent-1 2>&1 | grep -i "抓取失败\|快速失败"
# 预期: 有降级日志, 但研究请求最终成功返回
```

### 6.4 整体回归验证

```bash
# 1. 完整重启容器栈
docker compose -p agentinsight down -v
docker compose -p agentinsight -f docker-compose.yml --env-file .env up -d

# 2. 等待全部健康检查通过
docker compose -p agentinsight ps
# 预期: 所有服务 healthy

# 3. 跑功能测试
pytest tests/functional/ -q

# 4. 跑 API 测试 (含 MCP 端点)
pytest tests/api/test_mcp_endpoints.py -q

# 5. 跑 e2e 测试 (含 MCP 工具调用展示)
pytest tests/e2e/ -q

# 6. 检查容器日志无 P0/P1 错误
docker logs agentinsight-agent-1 2>&1 | grep -iE "ERROR|TaskGroup|ENOENT|ERR_MODULE_NOT_FOUND|Bad git executable|AuthorizationError"
# 预期: 无 P0/P1 错误输出
```

---

## 附录: 涉及文件清单

| 文件路径 | 修改类型 | 涉及错误编号 |
|---------|---------|------------|
| `scripts/init.sql` | 修改 (version 升级到 4, 占位符 MCP 改 enabled=FALSE) | E02, E03, E04, E05, E06, E07, E08, E14 |
| `Dockerfile` | 修改 (apt-get 增加 git) | E03 |
| `src/skills/researcher/mcp_coordinator.py` | 修改 (LRU + close + Lock + 僵尸清理) | E01, E09, A1, A4, A5, A6, C1 |
| `src/api/mcp_routes.py` | 修改 (clear_cache + try/finally) | A2, A3 |
| `src/skills/researcher/deep_research.py` | 修改 (改用单例) | A1 |
| `src/skills/researcher/research_conductor.py` | 修改 (改用单例) | A1 |
| `src/skills/researcher/searchers/gdelt.py` | 修改 (加限流) | E11 |
| `src/skills/researcher/searchers/tavily.py` | 可选增强 (432 识别) | E10 |
| `server.py` | 修改 (lifespan 调用 close) | A5 |
| `.env` / `.env.qa` | 配置 (LITELLM_LOCAL_MODEL_COST_MAP + TAVILY_API_KEY) | E10, E12 |

---

## 附录: 修复实施顺序建议

1. **第一批 (P0, 架构修复)**: A1 → A2 → A3 → A4 → A5 → A6 → C1 (按此顺序, A1 先统一单例, 后续修改才能生效)
2. **第二批 (P1, 配置修复)**: E03 (Dockerfile) → E02/E04/E05/E06/E07/E08/E14 (init.sql 一次性修改) → E11 (gdelt 限流)
3. **第三批 (P2, 配置/噪声)**: E10/E12 (.env 配置) → E13/E15 (无需修改)
4. **验证**: 每批修复后跑 §6 对应验证项, 全部完成后跑 §6.4 整体回归
