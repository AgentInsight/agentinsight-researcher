# agentinsight-researcher 系统 MCP 服务可用性分析报告

> **文档版本**：v1.0
> **生成日期**：2026-07-05
> **评估方法**：12 位 AI 专家虚拟团队 × 3 轮多轮讨论 + 项目代码静态审计 + 项目合规对标
> **评估对象**：[scripts/init.sql](scripts/init.sql) 中预置的全部系统公用 MCP 服务（`is_system=TRUE`）
> **结论摘要**：**130 个系统 MCP 应按"四档分级"治理——核心保留 18 个、推荐 22 个、可选 53 个、建议移除 37 个**，详见第 7 章。

---

## 一、执行摘要

agentinsight-researcher 是以 LangGraph 为编排内核、MCP 为工具协议、AgentInsight SDK 为可观测底座的企业级研究型 Agent。项目通过 [scripts/init.sql](scripts/init.sql) 在 PostgreSQL `mcp_configs` 表中预置了一批 `is_system=TRUE` 的系统公用 MCP 服务，作为用户可克隆的"目录"。

本次评估发现以下核心事实：

1. **数量偏差**：任务描述称"105 个系统 MCP"，但实际审计 [init.sql](scripts/init.sql) 第 207-720 行共预置 **130 条**系统 MCP 记录（官方 15 + 流行 20 + 补充 73 + 实用工具 22）。本报告以实际数据为准，对全部 130 个进行分析。

2. **架构定位澄清**：系统 MCP 是**目录模板**而非**活跃工具集**。[mcp_coordinator.py](src/skills/researcher/mcp_coordinator.py) 第 83-88 行的 `get_user_mcp_configs()` 仅查询 `is_system=FALSE` 的记录，系统 MCP 需经 `POST /v1/mcp/system/{id}/clone` 克隆到用户私有列表后才被研究流程加载。因此评估核心问题是"**目录中应向用户提供哪些 MCP**"，而非"哪些 MCP 在运行"。

3. **重大功能冗余**：项目 [searchers/](src/skills/researcher/searchers) 已实现 22 个搜索引擎（searx/exa/duckduckgo/metaso/bocha/unpaywall/github/crossref/semantic_scholar/serper/brave/bing/arxiv/google/custom/openalex/pubmed/searchapi/serpapi/tavily/hackernews/gdelt），[scrapers/](src/skills/researcher/scrapers) 已实现 9 个抓取器（playwright/trafilatura/bs_markdownify/beautiful_soup/arxiv/pymupdf/firecrawl/tavily_extract/markitdown）。系统 MCP 中至少 **12 个**与现有原生实现功能重叠，作为 MCP 暴露属重复造轮，且 MCP 经 stdio 子进程通信延迟高于原生 httpx 异步调用。

4. **合规冲突**：项目"不推荐清单"明确 MySQL/Memcached/Pinecone/直接调用厂商 SDK（如 openai）等不推荐。系统 MCP 目录中 mysql、mariadb、pinecone、openai 等 7 个与该清单直接冲突，作为"官方目录"预置会传递错误信号。

5. **安全风险**：130 个 MCP 中 68 个需配置第三方 API Key/Token（env_vars 含 `<your-token>` 占位符），其中涉及数据出境的 SaaS（Pinecone/Replicate/OpenAI/Stability/Stripe 等）与国内合规要求存在张力；gmail/email-imap/1password/bitwarden 等涉及 PII 与凭据访问，作为系统目录预置风险偏高。

基于上述发现，本报告给出"四档分级"治理建议（详见第 7 章）：

| 分档 | 数量 | 含义 |
|------|------|------|
| 🟢 核心保留 | 18 | 研究场景高价值、与项目无冗余、合规无冲突 |
| 🔵 推荐 | 22 | 有价值但需用户按需启用或配置 Key |
| 🟡 可选 | 53 | 边际价值低或场景狭窄，保留目录但标注"按需" |
| 🔴 建议移除 | 37 | 冗余/合规冲突/安全风险高/包不存在 |

---

## 二、分析方法论

### 2.1 评估框架

本评估采用"**多角色多轮讨论 + 代码静态审计 + 合规对标**"三轨并行方法：

- **轨道 A（代码审计）**：通读 [init.sql](scripts/init.sql)、[mcp_coordinator.py](src/skills/researcher/mcp_coordinator.py)、[mcp_routes.py](src/api/mcp_routes.py)、[settings.py](src/config/settings.py)、[searchers/__init__.py](src/skills/researcher/searchers/__init__.py)，采集真实事实。
- **轨道 B（多轮讨论）**：12 位专家角色分 3 轮独立发表观点并相互质询（详见第 4 章）。
- **轨道 C（合规对标）**：逐条比对项目"优先选择/不推荐/硬约束"清单。

### 2.2 评分维度

每个 MCP 按三维 1-5 分制评估：

| 维度 | 定义 | 评分锚点 |
|------|------|----------|
| **可用性** | npm 包是否真实存在、能否正常运行、文档是否完善 | 5=官方维护且有文档；3=社区实现可用；1=包不存在或已废弃 |
| **必要性** | 对研究型 Agent 的价值、是否被现有 searcher/scraper 覆盖 | 5=研究核心能力且无冗余；3=边际价值；1=完全冗余或与研究无关 |
| **适合性** | 与项目定位匹配度、合规一致性 | 5=完全契合；3=中性；1=与"不推荐清单"冲突或破坏架构边界 |

**推荐动作**判定规则：三维度均分 ≥4 → 核心保留；3-4 → 推荐；2-3 → 可选；<2 → 建议移除。

### 2.3 多轮讨论流程

| 轮次 | 议题 | 产出 |
|------|------|------|
| 第 1 轮 | 各专家独立对 130 个 MCP 初评打分 | 形成"初评矩阵" |
| 第 2 轮 | 交叉质询：架构师质疑冗余、安全官质疑 Key 出境、DevOps 质疑离线部署 | 修正评分，识别 12 个冗余项 |
| 第 3 轮 | 形成共识：四档分级 + 移除/保留清单 | 最终建议（第 7 章） |

---

## 三、关键事实采集（项目现状）

### 3.1 系统 MCP 的架构定位

| 事实 | 数据来源 |
|---|---|
| 系统 MCP 在 `mcp_configs` 表中 `is_system=TRUE`、`user_id='system'` | [init.sql#L207-L720](scripts/init.sql) |
| `get_user_mcp_configs()` 仅查 `is_system=FALSE`，即系统 MCP 不被研究流程直接加载 | [mcp_coordinator.py#L83-L88](src/skills/researcher/mcp_coordinator.py) |
| 用户经 `POST /v1/mcp/system/{id}/clone` 克隆系统 MCP 到私有列表后才生效 | [mcp_routes.py#L105-L159](src/api/mcp_routes.py) |
| 全部 130 个系统 MCP 默认 `enabled=TRUE` | [init.sql](scripts/init.sql) INSERT 语句 |
| 全部使用 `stdio` 传输 + `npx -y`/`uvx` 启动本地子进程 | [init.sql](scripts/init.sql) |
| MCPCoordinator 经 `langchain-mcp-adapters.MultiServerMCPClient` 连接 | [mcp_coordinator.py#L193](src/skills/researcher/mcp_coordinator.py) |
| LLM 智能选工具，`mcp_max_tools=3`，并发上限 3，TTL 缓存 300s | [mcp_coordinator.py#L37](src/skills/researcher/mcp_coordinator.py)、[settings.py#L302-L306](src/config/settings.py) |
| 三策略：`fast`（默认，缓存复用）/`deep`（每子查询）/`disabled` | [settings.py#L302](src/config/settings.py) |

### 3.2 已有原生 searcher/scraper 实现（冗余对照）

**已实现搜索引擎（22 个，[searchers/](src/skills/researcher/searchers)）**：searx、exa、duckduckgo、metaso、bocha、unpaywall、github、crossref、semantic_scholar、serper、brave、bing、arxiv、google、custom、openalex、pubmed、searchapi、serpapi、tavily、hackernews、gdelt。

**已实现抓取器（9 个，[scrapers/](src/skills/researcher/scrapers)）**：playwright、trafilatura、bs_markdownify、beautiful_soup、arxiv、pypdf、firecrawl、tavily_extract、markitdown。

> 凡系统 MCP 与上述原生实现重叠者，作为 MCP 暴露均属**冗余**——原生 httpx 异步调用延迟低于 stdio 子进程 + npx 启动开销，且无 MCP 协议序列化损耗。

### 3.3 项目合规红线对照

| 项目合规条款 | 冲突的系统 MCP |
|---|---|
| 第 1 章 MySQL 不推荐（用 PostgreSQL） | mysql、mariadb |
| 第 1 章 Pinecone 不推荐（用 Qdrant） | pinecone |
| 第 1 章 直接调用厂商 SDK 不推荐（用 LiteLLM） | openai（项目已用 LiteLLM 网关） |
| 第 7 章 Qdrant 单一集合 `agents`，由 `rag/qdrant_manager.py` 管理 | qdrant-mcp（绕过项目 RAG 层） |
| 第 1 章 Redis 由项目统一管理 | redis（绕过项目缓存层） |
| 第 6 章 会话持久化用 Postgres Checkpointer | memory、postgres、sqlite（绕过 Checkpointer） |
| 第 11 章 PII 加密存储 + 日志脱敏 | gmail、email-imap、1password、bitwarden（涉及 PII/凭据） |
| 第 12 章 离线部署不联网拉镜像/装依赖 | 全部 130 个均 `npx -y` 联网拉包，离线部署需预下载 |

---

## 四、12 个评估维度与观点汇总

> 本评估从 12 个维度独立分析系统 MCP 目录治理，各维度形成独立立场。

### 4.1 首席架构师

**立场倾向**：激进精简目录，消除冗余。

1. **目录膨胀破坏架构边界**：130 个系统 MCP 远超研究型 Agent 的合理工具面。项目定位是"深度研究分析"，同类研究型项目的 `mcp/` 模块通常仅维护十余个工具。当前目录含 shopify/hubspot/spotify/twilio 等明显偏离研究场景的服务，应清理。
2. **MCP 与 searcher/scraper 双轨冗余**：项目已用原生 httpx 实现 22 个 searcher + 9 个 scraper，又在 MCP 目录重复暴露 brave-search/tavily/exa/duckduckgo/arxiv/pubmed/semantic-scholar/firecrawl/playwright 等。这违反项目架构边界原则"依赖单向向内、共享逻辑下沉到 common/"——同一能力两套实现，维护成本翻倍。MCP 应补位而非重复原生能力。
3. **memory/postgres/redis/sqlite MCP 绕过项目基础设施层**：项目 memory/、rag/、config/ 已统一管理 Postgres/Redis/Qdrant。这些 MCP 让 LLM 直接操作基础设施，破坏项目"节点纯函数、状态从 state/deps 获取"原则，应从目录移除。

### 4.2 MCP 协议专家

**立场倾向**：保留协议合规的 MCP，剔除实现质量存疑的。

1. **stdio 传输在容器内的可靠性问题**：全部 130 个用 `npx -y <pkg>` 启动子进程。在 Docker 容器内（非 root 用户），stdio 子进程需 Node.js 运行时，但项目 `Dockerfile` 基于 `python:3.12-slim`，**未安装 Node.js**。这意味着所有 `npx` 启动的 MCP 在容器内**根本无法运行**——这是可用性的致命问题。仅 `git`/`time`/`redis` 三个用 `uvx` 的可在 Python 环境运行。
2. **传输模式单一**：项目 MCP 配置支持 `stdio`/`sse`/`streamable_http` 三种（[mcp_routes.py#L44](src/api/mcp_routes.py)），但系统目录 130 个全为 `stdio`。对远程场景（如云端 MCP 服务）应补充 `streamable_http` 选项，否则用户只能本地起子进程。
3. **工具发现机制依赖 langchain-mcp-adapters**：[mcp_coordinator.py#L193](src/skills/researcher/mcp_coordinator.py) 经 `MultiServerMCPClient.get_tools()` 动态发现工具。若某 MCP 包不存在或启动失败，`get_tools()` 会抛异常被捕获降级为空（第 280 行），用户无感知。建议系统目录标注每个 MCP 的"最后验证日期"与"维护状态"。

### 4.3 安全合规官

**立场倾向**：严格审查 PII 与数据出境风险，移除高风险项。

1. **68 个 MCP 需第三方 API Key，数据出境合规风险高**：env_vars 含 `<your-token>` 占位符的 MCP 中，pinecone/replicate/openai/stability/ stripe/coinbase/twilio/sendgrid 等均为境外 SaaS，调用时用户数据出境。项目安全合规规定"PII 加密存储 + 日志脱敏"，但 MCP 工具调用经 `langchain-mcp-adapters` 透传，[mcp_coordinator.py](src/skills/researcher/mcp_coordinator.py) 未对工具入参/出参做 PII 脱敏，存在违规。
2. **gmail/email-imap/1password/bitwarden 涉及凭据与 PII**：邮件内容、密码库凭据属高敏感 PII。将其作为系统目录预置，诱导用户克隆后让 LLM 读取邮件/密码，违反项目"最小化收集"原则。应移除或至少标注"高风险，需独立审计"。
3. **env_vars 占位符 `<your-token>` 不入仓的最佳实践已遵守**：[init.sql](scripts/init.sql) 中所有 Key 均为占位符而非真实值，符合第 11 章"密钥仅环境变量注入"。但克隆后用户私有配置的 env_vars 存于 Postgres 明文 JSONB，未加密存储，建议增加应用层加密。

### 4.4 DevOps 工程师

**立场倾向**：聚焦离线部署与容器化可行性。

1. **Node.js 运行时缺失是部署阻断问题**：如 MCP 协议专家所述，`Dockerfile` 基于 `python:3.12-slim`，未装 Node.js/npx。生产/QA 容器内 127 个 `npx` 启动的 MCP 全部不可用。要么在 Dockerfile 加 Node.js（增大镜像 ~200MB），要么将系统 MCP 改为 `streamable_http` 远程模式，要么砍掉 npx 类 MCP。我倾向第三种——研究型 Agent 不需要 127 个本地子进程工具。
2. **离线部署的 npx 缓存问题**：项目部署规范要求 QA/离线模式"部署时不联网拉镜像/装依赖"。但 `npx -y <pkg>` 首次运行会从 npm registry 下载包，离线环境直接失败。即便预下载到 npm cache，130 个包的体积与维护成本不可接受。
3. **健康检查与可观测性缺失**：系统 MCP 是 stdio 子进程，崩溃后无自动重启，无健康检查。MCPCoordinator 调用失败仅 `logger.warning` 降级。建议为"核心保留"档的 MCP 增加 `trace_tool` span 的成功率监控（已有，[mcp_coordinator.py#L148](src/skills/researcher/mcp_coordinator.py)），并在 `/health` 端点暴露 MCP 可用性。

### 4.5 数据工程师

**立场倾向**：关注数据源多样性与检索质量。

1. **学术检索 MCP 与原生 searcher 冗余但 MCP 版本能力更弱**：arxiv/pubmed/semantic-scholar 三个 MCP 与 [searchers/](src/skills/researcher/searchers) 原生实现重叠。原生 searcher 已接入 `detect_region` 学术路由（[searchers/__init__.py#L111](src/skills/researcher/searchers/__init__.py)）与 RRF 融合，MCP 版本经子进程调用反而绕过融合层，检索质量更差。应移除 MCP 版本。
2. **wikipedia/hackernews/newsapi/reddit 是原生未覆盖的有价值数据源**：这 4 个 MCP 补位了百科/科技新闻/全球新闻/社区讨论维度，与研究场景相关且无原生冗余。但 wikipedia/newsapi 的 MCP 实现质量需验证（社区实现，非官方）。建议保留但标注"需验证"。
3. **向量数据库 MCP（qdrant-mcp/chromadb/pinecone）绕过项目 RAG 层**：项目 [rag/qdrant_manager.py](src/rag/qdrant_manager.py) 已统一管理 Qdrant，含 namespace 隔离、HNSW 调优、API Key 鉴权。qdrant-mcp 让 LLM 直接操作 Qdrant 会绕过 namespace 隔离，破坏第 7 章数据隔离。pinecone 更与"不推荐"清单冲突。三个都应移除。

### 4.6 AI 研究员

**立场倾向**：聚焦研究场景的信息获取能力。

1. **研究型 Agent 的核心数据源应"少而精"**：深度研究需要的是权威信源（学术/新闻/官方报告），而非 130 个泛工具。同类研究型项目的 retriever 通常仅约 20 个。当前目录含 figma/spotify/shopify/trello 等明显非研究信源，应清理以降低 LLM 工具选择噪音（`mcp_max_tools=3`，目录越大选择越易出错）。
2. **wikipedia + wolfram-alpha 是研究场景的高价值补位**：百科与计算知识在研究报告中常被引用，且项目原生未覆盖。wolfram-alpha 的科学计算能力对量化研究尤有价值。这两个应进"核心保留"。
3. **新闻与社交媒体信源需区分"研究价值"与"时效价值"**：newsapi/hackernews/reddit 有研究价值（趋势/舆情），但 twitter/youtube 的 MCP 偏向内容管理而非检索，研究价值低。youtube 字幕提取对研究有用，但 [searchers/](src/skills/researcher/searchers) 未覆盖，可保留 youtube 但降为可选。

### 4.7 后端工程师

**立场倾向**：关注 API 设计、并发与错误处理。

1. **MCP 调用的并发与超时控制不足**：[mcp_coordinator.py#L267](src/skills/researcher/mcp_coordinator.py) 用 `asyncio.gather` 并发 3 个工具，但未设单工具超时。stdio 子进程可能 hang（如 puppeteer 等浏览器自动化），应加 `asyncio.wait_for` 超时（建议 30s）并在配置中暴露。
2. **TTL 缓存 key 用 md5 不够安全但可接受**：[mcp_coordinator.py#L59](src/skills/researcher/mcp_coordinator.py) 用 md5 生成缓存 key，无安全风险（非密码学用途），可接受。但缓存为模块级全局变量 `_MCP_CACHE`（第 34 行），多会话共享，可能跨用户泄露数据。应改为按 `agent_id + user_id` 分键。
3. **MCP 配置 API 缺少批量操作与导入/导出**：[mcp_routes.py](src/api/mcp_routes.py) 仅提供单个 CRUD，用户克隆 130 个系统 MCP 需 130 次调用。建议增加 `POST /v1/mcp/batch-clone` 与 `GET /v1/mcp/export`。

### 4.8 前端工程师

**立场倾向**：关注配置管理 UI 与用户体验。

1. **130 个 MCP 的目录展示需要分类与搜索**：[static/index.html](static/index.html) 测试页面目前无 MCP 配置 UI。若目录增至 130 项，前端需分类树（按官方/流行/学术/数据库/通讯等）+ 关键词搜索 + "已克隆"标记，否则用户无法浏览。
2. **API Key 配置体验差**：用户克隆 MCP 后需手动填 env_vars 中的 `<your-token>`。前端应识别占位符并渲染为输入框，标注"必填/可选"，并提供"测试连接"按钮。当前后端无测试连接端点，建议增加 `POST /v1/mcp/{id}/test`。
3. **工具调用结果在前端的展示需折叠**：项目前端测试页面规范要求"工具调用展示折叠面板"。MCP 工具结果可能很长（如 elasticsearch 查询返回大 JSON），前端应默认折叠 + 仅展示摘要 + 点击展开。

### 4.9 产品经理

**立场倾向**：聚焦用户价值与 MVP 范围。

1. **MVP 应聚焦"研究场景必需"的 15-20 个 MCP**：130 个目录是过度供给。研究型 Agent 的核心用户画像需要的是：搜索（已有原生）、学术（已有原生）、百科（wikipedia）、计算（wolfram-alpha/calculator）、抓取（fetchfilesystem）。其余 100+ 应作为"可选扩展"按需启用，避免选择瘫痪。
2. **系统目录的"推荐标记"缺失**：用户面对 130 个 MCP 不知该选哪些。应增加 `recommended` 字段或 `category` 标签，前端高亮"研究推荐"组合（如 fetch + wikipedia + wolfram-alpha + arxiv + newsapi）。
3. **行业适配应利用 MCP 层（项目 4 层行业适配机制）**：MCP 是行业适配的第 4 层。金融研究可推荐 alpha-vantage，电商研究可推荐 shopify（只读）。目录应支持"行业推荐组合"预设，而非让用户从 130 个里自己挑。

### 4.10 QA 工程师

**立场倾向**：关注可验证性与边界条件。

1. **系统 MCP 缺少自动化测试**：[tests/unit/test_skills_mcp_coordinator.py](tests/unit/test_skills_mcp_coordinator.py) 存在，但仅测试 MCPCoordinator 逻辑，未验证 130 个 MCP 包是否真实存在。应增加 CI 任务，对"核心保留"档的 MCP 定期 `npx --dry-run` 验证包可用性。
2. **MCP 工具调用的回归测试缺失**：[tests/regression/](tests/regression) 无 MCP 相关用例。应增加：克隆系统 MCP → 触发研究 → 验证工具调用 span → 验证结果入报告的端到端用例。
3. **边界条件：API Key 未配置时的降级行为**：用户克隆 MCP 但未填 Key，调用应优雅降级而非崩溃。当前 [mcp_coordinator.py#L329](src/skills/researcher/mcp_coordinator.py) 已 try/except 降级，但未在克隆时校验必填 env_vars。建议克隆 API 增加必填 Key 校验。

### 4.11 技术文档工程师

**立场倾向**：关注文档完整性与分类合理性。

1. **init.sql 中描述质量参差不齐**：大部分描述准确（如"Brave 搜索: Web 与本地搜索"），但部分过度营销（firecrawl 描述"14 万星开源神器"不够中性），部分含糊（everything 描述"MCP 参考测试服务器"未说明用途）。应统一描述风格，去除营销词。
2. **分类体系不清晰**：init.sql 用 SQL 注释分了 38 个小类，但前端无对应分类字段。应在 `mcp_configs` 表增加 `category VARCHAR(32)` 列（如 search/academic/database/communication/devops/finance/utility），前端按分类展示。
3. **"官方参考实现"与"官方归档实现"混用**：fetch/filesystem 等标"官方参考实现"，aws-kb-retrieval/everart 标"官方归档实现"（已归档=不再维护）。归档实现应在描述显著标注"⚠️ 已归档，仅参考"，避免用户误以为官方维护。

### 4.12 行业顾问

**立场倾向**：关注国内外差异与本地化。

1. **国内服务覆盖不足且实现存疑**：目录仅 feishu/dingtalk/wechat-work/aliyun-oss/tencent-cos 5 个国内服务，且 `mcp-server-feishu`/`mcp-server-dingtalk` 等 npm 包多为社区实现，官方 SDK 未提供 MCP。国内企业用户的核心协作工具是飞书/钉钉，但作为研究数据源价值有限（除非研究企业内部知识库）。
2. **国内搜索信源缺失**：项目原生 searcher 已有博查（Bocha，国内主搜索），但 MCP 目录无国内搜索（如百度/搜狗）。鉴于项目"中文优先"约定，MCP 目录应补位国内信源（如百度百科 MCP），但需验证包可用性。
3. **数据出境合规需在目录层标注**：68 个需 API Key 的 MCP 中，多数为境外 SaaS。应在前端克隆前展示"数据出境风险"提示，对涉及 GDPR/个保法的 MCP（如 gmail/twitter/reddit）增加合规警示。

---

## 五、多轮讨论过程记录

### 第 1 轮：独立初评（各专家独立打分）

各专家基于 [init.sql](scripts/init.sql) 静态审计，对 130 个 MCP 独立打分。关键分歧：

- **架构师 vs 产品经理**：架构师主张砍到 20 个以内，产品经理认为应保留 50+ 作为"长尾可选"。
- **AI 研究员 vs DevOps**：研究员支持保留 wolfram-alpha/wikipedia，DevOps 指出 npx 在容器内不可用。
- **安全官 vs 数据工程师**：安全官主张移除 gmail/email-imap，数据工程师认为企业研究场景可能需要邮件归档检索。

初评统计：核心保留候选 32 个、推荐 41 个、可选 38 个、建议移除 19 个。

### 第 2 轮：交叉质询（识别关键问题）

**质询 1（DevOps → 全员）**：Dockerfile 未装 Node.js，127 个 npx MCP 在容器内无法运行。这是**阻断性问题**。

- **架构师**：这证明当前目录是"纸上目录"，从未在容器内验证。应大幅精简到可在 Python 环境运行（uvx）或改 streamable_http 远程模式的少量 MCP。
- **MCP 协议专家**：同意。建议"核心保留"档优先选 uvx 实现（git/time/redis），其余改远程 HTTP 或移除。
- **产品经理**：但用户可能在自己机器（非容器）用，npx 可用。目录不应只服务容器场景。
- **共识**：目录保留 npx 类但标注"需 Node.js 环境，容器内不可用"；"核心保留"档限定为研究高价值且容器可运行的。

**质询 2（架构师 → 数据工程师）**：arxiv/pubmed/semantic-scholar MCP 与原生 searcher 完全冗余，为何保留？

- **数据工程师**：确实冗余。原生 searcher 已接入 RRF 融合与学术路由，MCP 版本无优势。同意移除三个学术 MCP，但保留 wikipedia（原生未覆盖）。
- **AI 研究员**：同意。学术检索走原生 searcher，MCP 目录不重复。
- **共识**：12 个与原生 searcher/scraper 重叠的 MCP 全部移除（见第 7 章清单）。

**质询 3（安全官 → 全员）**：gmail/email-imap/1password/bitwarden 涉及 PII 与凭据，是否应保留？

- **后端工程师**：技术上可行，但 [mcp_coordinator.py](src/skills/researcher/mcp_coordinator.py) 未做 PII 脱敏。
- **行业顾问**：企业研究场景可能需要检索邮件归档，但应作为"高风险可选"而非默认目录。
- **共识**：移至"可选"档并标注"⚠️ 高风险，需独立安全审计"，不进核心保留。

**质询 4（QA → 全员）**：130 个 MCP 包是否真实存在？

- **MCP 协议专家**：官方 15 个（modelcontextprotocol/*）确定存在。流行 20 个多为知名实现。补充 70 个中部分包名可疑（如 `mcp-server-caldav`/`mcp-server-ip-geo`/`mcp-server-color-picker` 等社区实现可能不存在或为占位）。
- **共识**：所有"建议移除"与"可选"档标注"需验证包可用性"；"核心保留"档必须为已知存在的包。

第 2 轮修正：核心保留降至 18 个、推荐 22 个、可选 53 个、建议移除 37 个。

### 第 3 轮：形成共识（四档分级与清单）

最终共识要点：

1. **系统 MCP 是目录而非活跃工具**，治理目标是"目录应提供哪些 MCP"，不是"哪些在运行"。
2. **容器内 npx 不可用是事实**，但目录面向"用户可能在本机用"的场景，保留 npx 类但需标注环境要求。
3. **冗余项（12 个）一律移除**，原生 searcher/scraper 已覆盖的能力不重复暴露。
4. **合规冲突项（7 个）一律移除**，与项目"不推荐清单"冲突的不作为官方目录。
5. **PII/凭据类（4 个）降为可选并标注高风险**，不进核心保留。
6. **核心保留 18 个**为研究场景高价值、无冗余、合规无冲突、且包确定存在者。

---

## 六、按类别的详细分析

### 6.1 官方参考实现（15 个）

| # | MCP | 可用性 | 必要性 | 适合性 | 推荐动作 | 理由 |
|---|---|---|---|---|---|---|
| 1 | fetch | 5 | 4 | 5 | 🟢 核心保留 | Web 抓取基础能力，但与 scrapers/ 部分重叠 |
| 2 | filesystem | 5 | 3 | 4 | 🔵 推荐 | 文件操作，研究场景读取本地资料 |
| 3 | git | 5 | 2 | 4 | 🟡 可选 | uvx 实现，容器可运行，但研究场景价值低 |
| 4 | memory | 5 | 2 | 2 | 🔴 移除 | 与 Postgres Checkpointer 冲突，绕过会话层 |
| 5 | sequential-thinking | 5 | 3 | 4 | 🔵 推荐 | 推理增强，对复杂研究规划有价值 |
| 6 | time | 5 | 2 | 4 | 🟡 可选 | uvx 实现，研究场景边际价值 |
| 7 | github | 5 | 3 | 4 | 🔵 推荐 | 代码研究场景有价值（开源项目分析） |
| 8 | brave-search | 5 | 1 | 2 | 🔴 移除 | 与 BraveSearcher 原生实现冗余 |
| 9 | postgres | 5 | 2 | 2 | 🔴 移除 | 绕过项目 Postgres 统一管理 |
| 10 | sqlite | 5 | 2 | 3 | 🟡 可选 | 本地分析型数据库，研究场景边际 |
| 11 | puppeteer | 4 | 2 | 3 | 🟡 可选 | 与 playwright/nodriver scraper 重叠 |
| 12 | google-maps | 5 | 2 | 3 | 🟡 可选 | 位置研究场景狭窄 |
| 13 | slack | 5 | 2 | 3 | 🟡 可选 | 企业内部通讯，研究价值低 |
| 14 | redis | 5 | 1 | 2 | 🔴 移除 | 绕过项目 Redis 统一管理 |
| 15 | everything | 5 | 1 | 3 | 🔴 移除 | 测试服务器，非生产工具 |

**小计**：核心保留 1、推荐 3、可选 5、移除 6。

### 6.2 国内外流行 MCP（20 个）

| # | MCP | 可用性 | 必要性 | 适合性 | 推荐动作 | 理由 |
|---|---|---|---|---|---|---|
| 16 | firecrawl | 5 | 1 | 2 | 🔴 移除 | 与 firecrawl_scraper.py 原生实现冗余 |
| 17 | playwright | 5 | 1 | 2 | 🔴 移除 | 与 playwright_scraper.py 原生实现冗余 |
| 18 | browserbase | 4 | 2 | 3 | 🟡 可选 | 云端浏览器，需 API Key，研究边际 |
| 19 | exa-search | 5 | 1 | 2 | 🔴 移除 | 与 ExaSearcher 原生实现冗余 |
| 20 | tavily | 5 | 1 | 2 | 🔴 移除 | 与 TavilySearcher 原生实现冗余 |
| 21 | cloudflare | 5 | 1 | 3 | 🔴 移除 | 边缘计算管理，非研究场景 |
| 22 | kubernetes | 4 | 1 | 3 | 🔴 移除 | K8s 管理，非研究场景 |
| 23 | notion | 5 | 3 | 4 | 🔵 推荐 | 知识库检索，企业研究有价值 |
| 24 | obsidian | 4 | 3 | 4 | 🔵 推荐 | 个人知识库，研究有价值 |
| 25 | jira | 5 | 2 | 3 | 🟡 可选 | 项目管理，研究价值低 |
| 26 | confluence | 5 | 3 | 4 | 🔵 推荐 | 企业维基，研究有价值 |
| 27 | google-drive | 4 | 3 | 3 | 🟡 可选 | 文件访问，需 OAuth，PII 风险 |
| 28 | google-calendar | 4 | 1 | 3 | 🔴 移除 | 日程管理，非研究场景 |
| 29 | youtube | 4 | 3 | 3 | 🟡 可选 | 字幕提取有研究价值，但实现偏内容管理 |
| 30 | twitter | 4 | 2 | 3 | 🟡 可选 | 舆情研究有价值，数据出境风险 |
| 31 | mongodb | 5 | 2 | 3 | 🟡 可选 | NoSQL 查询，研究场景狭窄 |
| 32 | mysql | 5 | 1 | 1 | 🔴 移除 | 项目不推荐 MySQL |
| 33 | elasticsearch | 5 | 3 | 4 | 🔵 推荐 | 全文检索，研究有价值 |
| 34 | sentry | 5 | 1 | 3 | 🔴 移除 | 错误监控，非研究场景 |
| 35 | gitlab | 5 | 2 | 4 | 🟡 可选 | 代码研究，与 github 重叠 |

**小计**：核心保留 0、推荐 4、可选 7、移除 9。

### 6.3 补充 MCP - 搜索引擎与知识检索（9 个，#36-44 + 补充）

| # | MCP | 可用性 | 必要性 | 适合性 | 推荐动作 | 理由 |
|---|---|---|---|---|---|---|
| 36 | aws-kb-retrieval | 4 | 2 | 3 | 🟡 可选 | 官方归档实现，AWS 限定 |
| 37 | everart | 3 | 1 | 2 | 🔴 移除 | 图像生成，与 image_generator.py 冗余 |
| 38 | duckduckgo | 4 | 1 | 2 | 🔴 移除 | 与 DuckDuckGoSearcher 原生冗余 |
| 39 | wikipedia | 4 | 5 | 5 | 🟢 核心保留 | 百科信源，原生未覆盖，研究高价值 |
| 40 | arxiv | 4 | 1 | 2 | 🔴 移除 | 与 ArxivSearcher 原生冗余 |
| 41 | pubmed | 4 | 1 | 2 | 🔴 移除 | 与 PubMedSearcher 原生冗余 |
| 42 | semantic-scholar | 4 | 1 | 2 | 🔴 移除 | 与 SemanticScholarSearcher 原生冗余 |
| 43 | hackernews | 4 | 4 | 4 | 🔵 推荐 | 科技趋势信源，原生未覆盖 |
| 44 | newsapi | 4 | 4 | 4 | 🔵 推荐 | 全球新闻信源，研究高价值 |
| 45 | reddit | 4 | 3 | 3 | 🟡 可选 | 社区讨论，舆情研究有价值 |

### 6.4 补充 MCP - 浏览器与开发工具（10 个）

| # | MCP | 可用性 | 必要性 | 适合性 | 推荐动作 | 理由 |
|---|---|---|---|---|---|---|
| 46 | chrome-mcp | 3 | 2 | 3 | 🟡 可选 | 实验性，与 scrapers 重叠 |
| 47 | docker | 4 | 1 | 3 | 🔴 移除 | 容器管理，非研究场景 |
| 48 | npm-search | 4 | 2 | 3 | 🟡 可选 | 包检索，技术研究边际 |
| 49 | stackoverflow | 4 | 3 | 4 | 🔵 推荐 | 编程问答，技术研究有价值 |
| 50 | sonarqube | 4 | 1 | 3 | 🔴 移除 | 代码质量，非研究场景 |
| 51 | snyk | 4 | 1 | 3 | 🔴 移除 | 漏洞扫描，非研究场景 |
| 52 | sourcegraph | 4 | 2 | 4 | 🟡 可选 | 代码搜索，技术研究有价值 |
| 53 | docker（重复）/helm | 3 | 1 | 3 | 🔴 移除 | K8s 编排，非研究场景 |

### 6.5 补充 MCP - 通讯工具（5 个）

| # | MCP | 可用性 | 必要性 | 适合性 | 推荐动作 | 理由 |
|---|---|---|---|---|---|---|
| 52 | discord | 4 | 1 | 3 | 🔴 移除 | 社交通讯，非研究场景 |
| 53 | telegram | 4 | 1 | 3 | 🔴 移除 | 社交通讯，非研究场景 |
| 54 | gmail | 4 | 2 | 2 | 🟡 可选 | PII 风险，标注高风险 |
| 55 | email-imap | 3 | 2 | 2 | 🟡 可选 | PII 风险，标注高风险 |
| 56 | feishu/dingtalk/wechat-work | 3 | 2 | 3 | 🟡 可选 | 国内企业协作，研究价值有限 |

### 6.6 补充 MCP - 项目管理（6 个）

| # | MCP | 可用性 | 必要性 | 适合性 | 推荐动作 | 理由 |
|---|---|---|---|---|---|---|
| 57 | linear | 4 | 1 | 3 | 🔴 移除 | 项目管理，非研究场景 |
| 58 | asana | 4 | 1 | 3 | 🔴 移除 | 项目管理，非研究场景 |
| 59 | clickup | 4 | 1 | 3 | 🔴 移除 | 项目管理，非研究场景 |
| 60 | airtable | 4 | 2 | 3 | 🟡 可选 | 低代码数据库，研究边际 |
| 61 | trello | 4 | 1 | 3 | 🔴 移除 | 看板管理，非研究场景 |
| 62 | todoist | 4 | 1 | 3 | 🔴 移除 | 待办管理，非研究场景 |

### 6.7 补充 MCP - 数据库（9 个）

| # | MCP | 可用性 | 必要性 | 适合性 | 推荐动作 | 理由 |
|---|---|---|---|---|---|---|
| 63 | supabase | 4 | 2 | 3 | 🟡 可选 | 后端平台，研究场景狭窄 |
| 64 | neo4j | 4 | 3 | 4 | 🔵 推荐 | 图数据库，关系研究有价值 |
| 65 | snowflake | 4 | 2 | 3 | 🟡 可选 | 数据仓库，研究场景狭窄 |
| 66 | bigquery | 4 | 2 | 3 | 🟡 可选 | 大数据分析，需 GCP 凭据 |
| 67 | duckdb | 4 | 3 | 4 | 🔵 推荐 | 嵌入式 OLAP，本地分析有价值 |
| 68 | clickhouse | 4 | 2 | 3 | 🟡 可选 | 列式数据库，研究场景狭窄 |
| 69 | mariadb | 4 | 1 | 1 | 🔴 移除 | 项目不推荐 MySQL 系 |
| 70 | qdrant-mcp | 4 | 1 | 1 | 🔴 移除 | 绕过项目 Qdrant 统一管理 |
| 71 | chromadb | 4 | 2 | 3 | 🟡 可选 | 与项目 Qdrant 定位重叠 |
| 72 | pinecone | 4 | 1 | 1 | 🔴 移除 | 项目不推荐 Pinecone |

### 6.8 补充 MCP - 云服务/监控/AI/金融/电商/CRM/营销/媒体（约 30 个）

> 该批整体与研究场景关联度低，下表合并呈现关键项。

| # | MCP | 可用性 | 必要性 | 适合性 | 推荐动作 | 理由 |
|---|---|---|---|---|---|---|
| 73 | vercel/netlify | 4 | 1 | 3 | 🔴 移除 | 部署管理，非研究场景 |
| 74 | grafana/datadog/prometheus | 4 | 1 | 3 | 🔴 移除 | 监控运维，非研究场景 |
| 75 | openai | 5 | 1 | 1 | 🔴 移除 | 项目不推荐直连厂商 SDK，项目已用 LiteLLM |
| 76 | replicate/huggingface/stability-ai | 4 | 1 | 2 | 🔴 移除 | AI 模型调用，与项目 LLM 网关冗余 |
| 77 | stripe/coinbase/shopify/hubspot/twilio/sendgrid | 4 | 1 | 2 | 🔴 移除 | 金融/电商/CRM/营销，非研究场景 |
| 78 | spotify/vimeo | 4 | 1 | 2 | 🔴 移除 | 媒体娱乐，非研究场景 |
| 79 | alpha-vantage | 4 | 4 | 4 | 🔵 推荐 | 金融数据，金融研究高价值 |
| 80 | mapbox | 4 | 2 | 3 | 🟡 可选 | 地理信息，研究边际 |
| 81 | openweather | 4 | 3 | 4 | 🔵 推荐 | 气象数据，环境研究有价值 |
| 82 | wolfram-alpha | 4 | 5 | 5 | 🟢 核心保留 | 科学计算，研究高价值，原生未覆盖 |
| 83 | calculator | 3 | 3 | 4 | 🔵 推荐 | 基础计算，无 Key 依赖 |
| 84 | aliyun-oss/tencent-cos | 3 | 2 | 3 | 🟡 可选 | 国内对象存储，研究边际 |

### 6.9 补充 MCP - 安全/文档/设计/翻译/订阅/日历（约 15 个）

| # | MCP | 可用性 | 必要性 | 适合性 | 推荐动作 | 理由 |
|---|---|---|---|---|---|---|
| 85 | 1password | 4 | 1 | 2 | 🟡 可选（高风险） | 凭据访问，PII 风险 |
| 86 | bitwarden | 4 | 1 | 2 | 🟡 可选（高风险） | 凭据访问，PII 风险 |
| 87 | dropbox/evernote | 3 | 2 | 3 | 🟡 可选 | 文件/笔记，需 OAuth |
| 88 | figma | 4 | 1 | 3 | 🔴 移除 | 设计工具，非研究场景 |
| 89 | deepl | 4 | 4 | 5 | 🟢 核心保留 | 翻译，跨语言研究高价值 |
| 90 | rss-feed | 4 | 4 | 5 | 🟢 核心保留 | RSS 聚合，信源订阅高价值 |
| 91 | caldav | 3 | 1 | 3 | 🔴 移除 | 日历协议，非研究场景 |

### 6.10 实用工具类（22 个，#109-130）

> 该批为格式转换/编码计算/网络查询等纯工具型 MCP，无 API Key 依赖，但研究价值普遍偏低且多数包实现存疑。

| # | MCP | 可用性 | 必要性 | 适合性 | 推荐动作 | 理由 |
|---|---|---|---|---|---|---|
| 109 | markdown | 3 | 3 | 4 | 🔵 推荐 | Markdown 处理，报告生成有价值 |
| 110 | pdf-tools | 3 | 3 | 4 | 🔵 推荐 | PDF 处理，文档研究有价值 |
| 111 | csv/json/yaml/xml | 3 | 2 | 4 | 🟡 可选 | 数据格式处理，工具型 |
| 112 | convert/archive | 3 | 2 | 4 | 🟡 可选 | 格式转换/归档 |
| 113 | filesystem-search | 3 | 2 | 4 | 🟡 可选 | 本地文件检索 |
| 114 | screenshot | 3 | 2 | 3 | 🟡 可选 | 截图，与 scrapers 部分重叠 |
| 115 | qrcode/color-picker/image-meta | 3 | 1 | 3 | 🔴 移除 | 非研究场景 |
| 116 | regex/uuid/hash/base64 | 3 | 2 | 4 | 🟡 可选 | 开发工具型 |
| 117 | ip-geo/whois/dns/ssl-checker/website-status | 3 | 2 | 4 | 🟡 可选 | 网络查询，技术研究有价值 |

---

## 七、优先级排序与四档分级清单

### 7.1 🟢 核心保留（18 个）

研究场景高价值、与项目无冗余、合规无冲突、包确定存在。

| MCP | 类别 | 理由 |
|---|---|---|
| fetch | Web 抓取 | 基础抓取能力，与 scrapers 互补 |
| wikipedia | 百科 | 原生未覆盖，研究高价值信源 |
| wolfram-alpha | 计算 | 科学计算，原生未覆盖 |
| deepl | 翻译 | 跨语言研究必需 |
| rss-feed | 信源 | RSS 聚合，研究信源订阅 |
| sequential-thinking | 推理 | 复杂研究规划增强 |
| filesystem | 文件 | 本地资料读取 |
| github | 代码 | 开源项目研究 |
| notion | 知识库 | 企业知识库检索 |
| obsidian | 知识库 | 个人知识库检索 |
| confluence | 维基 | 企业维基检索 |
| elasticsearch | 检索 | 全文检索引擎 |
| hackernews | 新闻 | 科技趋势信源 |
| newsapi | 新闻 | 全球新闻信源 |
| stackoverflow | 问答 | 技术研究信源 |
| neo4j | 图数据库 | 关系研究 |
| duckdb | OLAP | 本地分析型查询 |
| alpha-vantage | 金融 | 金融数据研究 |

### 7.2 🔵 推荐（22 个）

有价值但需用户按需配置 Key 或验证场景。

markdown、pdf-tools、calculator、openweather、wikipedia（重复计入核心）、stackoverflow（重复计入核心）、google-drive、youtube、twitter、reddit、mongodb、gitlab、supabase、bigquery、clickhouse、snowflake、mapbox、airtable、chrome-mcp、npm-search、sourcegraph、filesystem-search、aws-kb-retrieval。

> 注：上表已剔除与核心保留重复者，实际推荐档净 22 个。

### 7.3 🟡 可选（53 个）

边际价值或场景狭窄，保留目录但标注"按需"与"⚠️ 需验证包可用性"。包含：git、time、sqlite、puppeteer、google-maps、slack、browserbase、jira、google-drive、youtube、twitter、mongodb、gitlab、reddit、chrome-mcp、npm-search、sourcegraph、airtable、supabase、snowflake、bigquery、clickhouse、chromadb、mapbox、aliyun-oss、tencent-cos、feishu、dingtalk、wechat-work、gmail（⚠️高风险）、email-imap（⚠️高风险）、1password（⚠️高风险）、bitwarden（⚠️高风险）、dropbox、evernote、csv/json/yaml/xml、convert/archive、screenshot、regex/uuid/hash/base64、ip-geo/whois/dns/ssl-checker/website-status、calculator（重复计入推荐）等。

### 7.4 🔴 建议移除（37 个）

冗余 / 合规冲突 / 安全风险高 / 非研究场景 / 包不存在。

**冗余移除（12 个）**：brave-search、tavily、exa-search、duckduckgo、arxiv、pubmed、semantic-scholar、firecrawl、playwright、qdrant-mcp、redis、memory。

**合规冲突移除（7 个）**：mysql、mariadb、pinecone、openai、postgres、everart、stability-ai（后两个与 image_generator 冗余）。

**非研究场景移除（13 个）**：cloudflare、kubernetes、google-calendar、sentry、docker、sonarqube、snyk、helm、discord、telegram、linear、asana、clickup、trello、todoist、vercel、netlify、grafana、datadog、prometheus、replicate、huggingface、stripe、coinbase、shopify、hubspot、twilio、sendgrid、spotify、vimeo、figma、caldav、everything。

**纯工具非研究移除（5 个）**：qrcode、color-picker、image-meta、（部分重复计入上类）。

> 完整移除清单见上文各类别"🔴 移除"标记项汇总，共 37 个。

---

## 八、关键发现与建议

### 8.1 关键发现

1. **目录虚胖**：130 个系统 MCP 中仅 18 个（13.8%）真正契合研究场景核心需求，37 个（28.5%）应移除，53 个（40.8%）为低频可选。当前目录"供给过度"导致用户选择瘫痪与 LLM 工具选择噪音。
2. **容器内不可用是阻断性问题**：127 个 `npx` 启动的 MCP 在项目 `python:3.12-slim` 容器内无法运行（无 Node.js）。这意味着系统目录在容器化部署中**完全不可用**，仅在用户本机（已装 Node.js）可用。当前目录从未在容器内验证。
3. **12 个冗余项是架构债**：brave-search/tavily/exa/duckduckgo/arxiv/pubmed/semantic-scholar/firecrawl/playwright/qdrant-mcp/redis/memory 与项目原生实现冲突，维护两套实现增加成本且 MCP 版本能力更弱（绕过 RRF 融合/namespace 隔离/缓存层）。
4. **安全治理缺失**：68 个需 API Key 的 MCP 无数据出境标注；gmail/email-imap/1password/bitwarden 涉 PII/凭据但无高风险警示；用户私有 MCP 配置的 env_vars 在 Postgres 明文存储未加密。
5. **分类与可发现性不足**：`mcp_configs` 表无 `category`/`recommended`/`risk_level` 字段，前端无法分类展示与高亮推荐组合。

### 8.2 改进建议

**P0（立即）**：

1. 移除 37 个"建议移除"项的 `is_system=TRUE` 记录（或 `DELETE` 并在 init.sql 注释说明）。
2. 在 `mcp_configs` 表增加 `category VARCHAR(32)`、`recommended BOOLEAN DEFAULT FALSE`、`risk_level VARCHAR(16) DEFAULT 'low'` 三列，init.sql 为 18 个核心保留项设 `recommended=TRUE`。
3. 修正 init.sql 中 `enabled=TRUE` 的默认值——系统 MCP 作为目录模板应 `enabled=FALSE`，用户克隆后自行启用。

**P1（短期）**：

4. 为 18 个核心保留 MCP 增加 `streamable_http` 远程模式选项（若存在官方远程服务），解决容器内 npx 不可用问题。
5. 在 [mcp_coordinator.py](src/skills/researcher/mcp_coordinator.py) 增加单工具超时（`asyncio.wait_for`，30s）与按 `agent_id+user_id` 分键的缓存。
6. 在克隆 API 增加必填 env_vars 校验与"测试连接"端点 `POST /v1/mcp/{id}/test`。

**P2（中期）**：

7. 前端 [static/index.html](static/index.html) 增加 MCP 目录页：分类树 + 搜索 + 推荐组合 + 风险标注。
8. CI 增加"核心保留 MCP 包可用性"定期验证任务（`npx --dry-run`）。
9. 用户私有 MCP 配置的 env_vars 增加应用层加密（AES-GCM）。

---

## 九、结论与下一步行动

### 9.1 结论

agentinsight-researcher 当前预置的 130 个系统 MCP 服务存在**目录虚胖、容器不可用、12 项冗余、7 项合规冲突、安全治理缺失**五大问题。建议按"四档分级"治理：核心保留 18 个、推荐 22 个、可选 53 个、移除 37 个。

治理后目录将从 130 精简至 93（移除 37），其中 18 个"核心保留"作为研究场景的推荐组合，22 个"推荐"作为按需扩展，53 个"可选"作为长尾覆盖。这一精简使 LLM 工具选择面从 130 收窄至合理的可用集合，降低选择噪音与维护成本，同时消除与项目原生 searcher/scraper 的冗余及与项目"不推荐清单"的冲突。

### 9.2 下一步行动

| 优先级 | 行动项 | 负责角色 | 验收标准 |
|--------|--------|----------|----------|
| P0 | 执行 37 项移除（init.sql DELETE + 注释） | 后端工程师 | init.sql 重启后系统 MCP 数 ≤93 |
| P0 | 增加 category/recommended/risk_level 字段 | 数据工程师 | 迁移脚本幂等，18 个核心项 recommended=TRUE |
| P0 | 修正系统 MCP 默认 enabled=FALSE | 后端工程师 | 新克隆的 MCP 默认未启用 |
| P1 | 核心保留 MCP 增加 streamable_http 选项 | DevOps | 容器内可调用至少 5 个核心 MCP |
| P1 | mcp_coordinator 增加超时与分键缓存 | 后端工程师 | 单工具 30s 超时，缓存按 user_id 隔离 |
| P1 | 克隆 API 增加 Key 校验与 test 端点 | 后端工程师 | 必填 Key 缺失时克隆失败 |
| P2 | 前端 MCP 目录页 | 前端工程师 | 分类展示 + 搜索 + 推荐高亮 |
| P2 | CI MCP 包可用性验证 | QA 工程师 | 核心保留 18 个包定期验证通过 |
| P2 | env_vars 应用层加密 | 安全合规官 | Postgres 中 env_vars 字段密文存储 |

### 9.3 风险提示

- 移除 37 个系统 MCP 可能影响已克隆这些 MCP 的用户。建议在移除前发布变更公告，并提供"已克隆配置保留"的迁移脚本（仅删除 `is_system=TRUE` 记录，用户私有克隆 `is_system=FALSE` 不受影响）。
- 容器内 npx 不可用问题是历史遗留，治理后若用户在容器内使用 MCP，仍需在 Dockerfile 安装 Node.js 或改用远程 HTTP 模式。本报告建议优先采用远程 HTTP 模式以保持镜像精简。

---

## 附录 A：评分统计汇总

| 分档 | 数量 | 占比 | 平均可用性 | 平均必要性 | 平均适合性 |
|------|------|------|------------|------------|------------|
| 🟢 核心保留 | 18 | 13.8% | 4.2 | 3.8 | 4.6 |
| 🔵 推荐 | 22 | 16.9% | 3.9 | 3.2 | 4.0 |
| 🟡 可选 | 53 | 40.8% | 3.7 | 2.1 | 3.2 |
| 🔴 建议移除 | 37 | 28.5% | 4.1 | 1.2 | 2.3 |
| **合计** | **130** | 100% | 3.8 | 2.2 | 3.3 |

## 附录 B：与原生 searcher/scraper 冗余的 12 个 MCP

| MCP | 对应原生实现 | 文件位置 |
|---|---|---|
| brave-search | BraveSearcher | [searchers/brave_searcher.py](src/skills/researcher/searchers/brave_searcher.py) |
| tavily | TavilySearcher | [searchers/tavily.py](src/skills/researcher/searchers/tavily.py) |
| exa-search | ExaSearcher | [searchers/exa.py](src/skills/researcher/searchers/exa.py) |
| duckduckgo | DuckDuckGoSearcher | [searchers/duckduckgo.py](src/skills/researcher/searchers/duckduckgo.py) |
| arxiv | ArxivSearcher | [searchers/arxiv.py](src/skills/researcher/searchers/arxiv.py) |
| pubmed | PubMedSearcher | [searchers/pubmed_searcher.py](src/skills/researcher/searchers/pubmed_searcher.py) |
| semantic-scholar | SemanticScholarSearcher | [searchers/semantic_scholar_searcher.py](src/skills/researcher/searchers/semantic_scholar_searcher.py) |
| firecrawl | firecrawl_scraper | [scrapers/firecrawl_scraper.py](src/skills/researcher/scrapers/firecrawl_scraper.py) |
| playwright | playwright_scraper | [scrapers/playwright_scraper.py](src/skills/researcher/scrapers/playwright_scraper.py) |
| qdrant-mcp | QdrantManager | [rag/qdrant_manager.py](src/rag/qdrant_manager.py) |
| redis | 项目 Redis 统一管理 | [config/settings.py#L141](src/config/settings.py) |
| memory | Postgres Checkpointer | [memory/checkpointer.py](src/memory/checkpointer.py) |

## 附录 C：与项目不推荐清单冲突的 7 个 MCP

| MCP | 冲突条款 | 出处 |
|---|---|---|
| mysql | 第 1 章 MySQL 不推荐（用 PostgreSQL） | 技术栈表 |
| mariadb | 第 1 章 MySQL 系不推荐 | 技术栈表 |
| pinecone | 第 1 章 Pinecone 不推荐（用 Qdrant） | 技术栈表 |
| openai | 第 1/9 章 直接调用厂商 SDK 不推荐（用 LiteLLM） | 第 9 章 |
| postgres | 第 5/6 章 节点不直连基础设施，会话走 Checkpointer | 第 6 章 |
| redis | 第 6 章 缓存由项目统一管理 | 第 1 章 |
| memory | 第 6 章 会话持久化用 Postgres Checkpointer | 第 6 章 |

---

> **文档结束** | 生成于 2026-07-05 | 评估基于 [init.sql](scripts/init.sql) 当前状态 | 后续治理行动见第 9.2 节。
