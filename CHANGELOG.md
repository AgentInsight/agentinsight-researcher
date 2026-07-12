# 变更日志 | Changelog

[中文](#中文) | [English](#english)

---

## 中文

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/) 语义化版本规范。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

---

## [Unreleased]

### 计划中

- RAGAS/DeepEval 评测门禁 CI 化
- 多模态支持(图片理解 + 表格抽取)
- Agent Marketplace(技能热插拔)

---

## [1.0.0] - 2026-07-12

### 首个正式发布版本

- 完成全项目注释清理和文档脱钩
- 配置默认值对齐 .env.template
- 版本号统一为 1.0.0

---

## [1.0.0] - 2026-07-04

### 首次发布

#### ✨ 新增

**核心架构**
- 基于 LangGraph ≥1.2 的状态机编排内核(StateGraph + 条件边 + PostgresSaver Checkpointer)
- OpenAI 兼容 API(`/v1/chat/completions` 流式 SSE + 非流式)
- WebSocket 双向通信通道(`/v1/ws/{session_id}`),支持人在回路审核
- Agent Discovery Protocol(`/.well-known/agent-discovery.json`)
- FastAPI + Uvicorn 异步原生 Web 框架,自动 OpenAPI 文档

**研究能力**
- 5 种报告类型:`basic_report` / `detailed_report` / `deep_research` / `summary` / `subtopics`
- 5 种输出格式:Markdown / HTML / PDF / DOCX / JSON
- 4 种引用风格:APA / MLA / Chicago / GB7714
- 5 种报告语言:中文 / 英文 / 日语 / 韩语 / 法语
- 4 种报告风格预设:academic / business / casual / news
- 17 种 Tone 语气:objective / analytical / opinionated / casual 等
- YAML frontmatter 元信息块支持
- 报告配图生成(deepseek-v4-flash)

**多 Agent 协作**
- Supervisor 线性+条件边模式:Researcher → Writer → FactChecker → Reviewer → Reviser → Visualizer → Publisher
- 子图复用机制(reviewer↔reviser 循环封装为可复用子图)
- 人在回路审核(WebSocket 推送 + FeedbackQueue 阻塞等待,带 300s 超时)
- 事实核查 + 评审修订循环(守卫防死循环:`max_revisions=3`、`max_plan_revisions=3`)

**LLM 网关**
- LiteLLM ≥1.6 统一接入 100+ 模型
- 三级 LLM 分层:FAST / SMART / STRATEGIC
- 降级链:STRATEGIC → SMART → FAST
- 真实成本追踪(25+ 模型定价表,按 step 分步累计)
- TokenBudgetAllocator 预算分配器
- 智谱 AI OpenAI 兼容端点适配(`_adapt_zhipu` 方法)

**RAG 检索**
- 混合检索:BM25(jieba 分词)+ 向量(bge-base-zh-v1.5)
- RRF 倒数排名融合(`vector_weight=0.7`、`bm25_weight=0.3`)
- 可选 Rerank(bge-reranker-v2-m3,TEI 服务)
- Qdrant 单集合 + namespace 隔离(共享知识库 + 用户私有数据)
- HNSW 参数调优(`m=32`、`ef_construct=200`、Scalar INT8 量化)
- Redis 缓存 + LRU 淘汰
- 内容 hash 去重
- 新增 FastEmbed 本地 Embeddings(bge-small-zh-v1.5,512 维)用于上下文压缩

**搜索引擎矩阵**
- 国内(CN):博查 Bocha(主)、秘塔 Metaso、DuckDuckGo(兜底)
- 国外(GLOBAL):Tavily、Brave、Bing、Google、SerpApi、Serper、Exa、SearchAPI.io、SearXNG、Custom、HackerNews、GDELT、GitHub
- 学术(ACADEMIC):PubMed、Semantic Scholar、Arxiv、OpenAlex、CrossRef、Unpaywall
- 区域自动检测(中文字符比例 + 学术关键词)

**抓取器矩阵**
- 9 种抓取器:bs(默认)、playwright、nodriver、firecrawl、tavily_extract、arxiv_scraper、pymupdf、markitdown、trafilatura
- 并行抓取(15 worker)+ 限流 + 图片筛选

**工具协议**
- MCP 协议支持(fast / deep / disabled 三策略)
- LLM 自动工具选择(对标业界实践 `MCPToolSelector`)
- 多工具并发调用(信号量限流,默认并发 3)
- TTL 缓存(key = md5(query + tool_name + tool_args))

**可观测性**
- AgentInsight Python SDK 6 类 trace span:Agent / Generation / Tool / Retriever / Chain / Embedding
- 异步上下文管理器调用方式(`async with trace_xxx(...) as span`)
- 跨节点 span 自动传播(OpenTelemetry Context API)
- Null Object 降级模式(SDK 异常时业务不阻断)
- head-based 采样(Embedding 默认 0.5,其他全量 1.0)

**数据隔离**
- 三级分键:`agent_id`(= agent_name)× `user_id` × `session_id`(= thread_id)
- PostgreSQL 业务表(agent_id + user_id 双列复合索引)
- Qdrant namespace 隔离(共享:agent_id;私有:agent_id:user_id)
- Redis 键前缀(agent_id:user_id:)

**身份解析**
- Bearer JWT Token 可选认证
- 调用 `GET https://agentinsight.goldebridge.com/api/user` 获取 user_id
- 超时降级(5s)到 DEFAULT_USER_ID
- 新增 IP-based 用户身份解析(无 JWT 网关场景的降级方案)
- WebSocket Origin 校验 + JWT 校验(防 CSWSH)

**安全合规**
- 安全响应头中间件(nosniff / DENY / HSTS)
- Pydantic 校验所有外部输入
- 工具调用权限隔离(read / write / execute / network)
- 生产环境强制校验(密钥存在、CORS 禁 `*`、关闭 Debug)

**部署**
- 三套构建模式:QA 离线 / 生产联网 / 生产离线
- 7 容器编排:agent + embeddings + qdrant + redis + postgres + searxng + 可选 rerank
- 新增 SearXNG 自托管元搜索引擎容器(端口 8099),聚合 22 个搜索源
- 多阶段 Docker 构建(python:3.12-slim,非 root 用户)
- 启动时业务表幂等初始化(`CREATE TABLE IF NOT EXISTS`)
- 短查询 + 离题种子向量预热

**测试页面**
- 自包含单文件 `static/index.html`(原生 HTML + JS)
- 会话管理(新建/切换/清空)
- 流式渲染(fetch + ReadableStream 解析 SSE)
- 工具调用展示(折叠面板)
- 检索来源展示(折叠面板)
- light/dark 双主题

**测试**
- 五层测试:单元 / 功能 / API / 回归 / 端到端
- 96 个单元测试用例
- 完整 CI 流水线(构建 → 单元 → 容器栈 → 功能 → API → 回归 → e2e)

#### 🔒 安全
- 密钥仅环境变量注入,禁止入仓/硬编码/日志
- API Key SHA256+BCrypt 双哈希
- 用户会话内容加密存储 + 日志脱敏
- 禁止 `eval`/`exec` 求值用户输入
- 生产强制 HTTPS + CORS 禁 `*` + 安全响应头

---

## 版本号说明

- **Major**(X.0.0):不兼容的 API 变更
- **Minor**(0.X.0):向后兼容的功能新增
- **Patch**(0.0.X):向后兼容的 Bug 修复

详细变更请参考 [GitHub Releases](https://github.com/AgentInsight/agentinsight-researcher/releases) 或 git log。

---

## English

This project follows [Semantic Versioning](https://semver.org/).

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Planned

- RAGAS/DeepEval evaluation gate CI integration
- Multimodal support (image understanding + table extraction)
- Agent Marketplace (hot-swappable skills)

---

## [1.0.0] - 2026-07-12

### First Official Release

- Completed project-wide comment cleanup and documentation decoupling
- Aligned configuration defaults with .env.template
- Unified version number to 1.0.0

---

## [1.0.0] - 2026-07-04

### Initial Release

#### ✨ Added

**Core Architecture**
- State machine orchestration kernel based on LangGraph ≥1.2 (StateGraph + conditional edges + PostgresSaver Checkpointer)
- OpenAI-compatible API (`/v1/chat/completions` streaming SSE + non-streaming)
- WebSocket bidirectional communication channel (`/v1/ws/{session_id}`), supporting human-in-the-loop review
- Agent Discovery Protocol (`/.well-known/agent-discovery.json`)
- FastAPI + Uvicorn async-native web framework with automatic OpenAPI documentation

**Research Capabilities**
- 5 report types: `basic_report` / `detailed_report` / `deep_research` / `summary` / `subtopics`
- 5 output formats: Markdown / HTML / PDF / DOCX / JSON
- 4 citation styles: APA / MLA / Chicago / GB7714
- 5 report languages: Chinese / English / Japanese / Korean / French
- 4 report style presets: academic / business / casual / news
- 17 Tone options: objective / analytical / opinionated / casual, etc.
- YAML frontmatter metadata block support
- Report image generation (deepseek-v4-flash)

**Multi-Agent Collaboration**
- Supervisor linear + conditional edge mode: Researcher → Writer → FactChecker → Reviewer → Reviser → Visualizer → Publisher
- Subgraph reuse mechanism (reviewer↔reviser loop encapsulated as reusable subgraph)
- Human-in-the-loop review (WebSocket push + FeedbackQueue blocking wait, with 300s timeout)
- Fact-checking + review revision loop (guards against infinite loops: `max_revisions=3`, `max_plan_revisions=3`)

**LLM Gateway**
- LiteLLM ≥1.6 unified access to 100+ models
- Three-tier LLM layering: FAST / SMART / STRATEGIC
- Degradation chain: STRATEGIC → SMART → FAST
- Real cost tracking (25+ model pricing table, accumulated per step)
- TokenBudgetAllocator budget allocator
- Zhipu AI OpenAI-compatible endpoint adaptation (`_adapt_zhipu` method)

**RAG Retrieval**
- Hybrid retrieval: BM25 (jieba tokenization) + Vector (bge-base-zh-v1.5)
- RRF Reciprocal Rank Fusion (`vector_weight=0.7`, `bm25_weight=0.3`)
- Optional Rerank (bge-reranker-v2-m3, TEI service)
- Qdrant single collection + namespace isolation (shared knowledge base + user private data)
- HNSW parameter tuning (`m=32`, `ef_construct=200`, Scalar INT8 quantization)
- Redis cache + LRU eviction
- Content hash deduplication
- Added FastEmbed local Embeddings (bge-small-zh-v1.5, 512 dims) for context compression

**Search Engine Matrix**
- Domestic (CN): Bocha (primary), Metaso, DuckDuckGo (fallback)
- Global (GLOBAL): Tavily, Brave, Bing, Google, SerpApi, Serper, Exa, SearchAPI.io, SearXNG, Custom, HackerNews, GDELT, GitHub
- Academic (ACADEMIC): PubMed, Semantic Scholar, Arxiv, OpenAlex, CrossRef, Unpaywall
- Auto region detection (Chinese character ratio + academic keywords)

**Scraper Matrix**
- 9 scrapers: bs (default), playwright, nodriver, firecrawl, tavily_extract, arxiv_scraper, pymupdf, markitdown, trafilatura
- Parallel scraping (15 workers) + rate limiting + image filtering

**Tool Protocol**
- MCP protocol support (fast / deep / disabled strategies)
- LLM automatic tool selection (aligned with industry practice `MCPToolSelector`)
- Multi-tool concurrent invocation (semaphore rate limiting, default concurrency 3)
- TTL cache (key = md5(query + tool_name + tool_args))

**Observability**
- AgentInsight Python SDK 6 types of trace spans: Agent / Generation / Tool / Retriever / Chain / Embedding
- Async context manager invocation (`async with trace_xxx(...) as span`)
- Cross-node span automatic propagation (OpenTelemetry Context API)
- Null Object degradation pattern (business not blocked when SDK exceptions occur)
- Head-based sampling (Embedding default 0.5, others full 1.0)

**Data Isolation**
- Three-tier key partitioning: `agent_id` (= agent_name) × `user_id` × `session_id` (= thread_id)
- PostgreSQL business tables (agent_id + user_id dual-column composite index)
- Qdrant namespace isolation (shared: agent_id; private: agent_id:user_id)
- Redis key prefix (agent_id:user_id:)

**Identity Resolution**
- Bearer JWT Token optional authentication
- Call `GET https://agentinsight.goldebridge.com/api/user` to obtain user_id
- Timeout degradation (5s) to DEFAULT_USER_ID
- Added IP-based user identity resolution (fallback for scenarios without JWT gateway)
- WebSocket Origin validation + JWT validation (CSWSH protection)

**Security Compliance**
- Security response headers middleware (nosniff / DENY / HSTS)
- Pydantic validation for all external inputs
- Tool invocation permission isolation (read / write / execute / network)
- Production environment mandatory validation (keys exist, CORS not `*`, Debug disabled)

**Deployment**
- Three build modes: QA offline / Production online / Production offline
- 7-container orchestration: agent + embeddings + qdrant + redis + postgres + searxng + optional rerank
- Added SearXNG self-hosted meta search engine container (port 8099), aggregating 22 search sources
- Multi-stage Docker build (python:3.12-slim, non-root user)
- Idempotent business table initialization at startup (`CREATE TABLE IF NOT EXISTS`)
- Short query + off-topic seed vector warmup

**Test Page**
- Self-contained single file `static/index.html` (native HTML + JS)
- Session management (new/switch/clear)
- Streaming rendering (fetch + ReadableStream parsing SSE)
- Tool invocation display (collapsible panel)
- Retrieval source display (collapsible panel)
- light/dark dual theme

**Testing**
- Five-tier testing: Unit / Functional / API / Regression / End-to-End
- 96 unit test cases
- Complete CI pipeline (build → unit → container stack → functional → API → regression → e2e)

#### 🔒 Security
- Keys injected via environment variables only, committing to repo/hardcoding/logging is prohibited
- API Key SHA256+BCrypt double hashing
- User session content encrypted storage + log desensitization
- Prohibited `eval`/`exec` for user input evaluation
- Production mandatory HTTPS + CORS not `*` + security response headers

---

## Version Numbering

- **Major** (X.0.0): Incompatible API changes
- **Minor** (0.X.0): Backward-compatible feature additions
- **Patch** (0.0.X): Backward-compatible bug fixes

For detailed changes, refer to [GitHub Releases](https://github.com/AgentInsight/agentinsight-researcher/releases) or git log.
