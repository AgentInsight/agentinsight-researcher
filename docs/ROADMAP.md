# 项目路线图 | Project Roadmap

[中文](#中文) | [English](#english)

---

## 中文

## 项目愿景

**agentinsight-researcher** 致力于成为**中文优先的企业级 AI 研究分析智能体**，以 LangGraph 为编排内核、MCP 为工具协议、AgentInsight SDK 为可观测底座，对外暴露 OpenAI 兼容 API，让任何团队都能以最低成本构建深度研究能力。

我们在以下维度持续深耕：

- **中文优先**：中文搜索源（博查/秘塔）、中文分词（jieba）、中文嵌入（bge-base-zh-v1.5）、中文 Rerank（bge-reranker-v2-m3）
- **企业级安全**：三级数据隔离（agent_id × user_id × session_id）、JWT 身份解析、IP-based 降级、安全响应头、密钥环境变量注入
- **全链路可观测**：AgentInsight SDK 6 类 trace span，追踪从 Agent 入口到 Embedding 调用的完整链路
- **多 Agent 协作**：Supervisor 模式完整流水线（Researcher → Writer → FactChecker → Reviewer → Reviser → Visualizer → Publisher）
- **生产级前端**：Next.js 15 + Vercel AI SDK + shadcn/ui 双层前端（测试页面 + 生产前端），支持多 Agent 路由、流式渲染、人在回路

---

## 版本路线图

### v1.0.0（已发布 - 2026-07-04）✅

**首次发布**，奠定核心架构与基础能力。

| 领域 | 已交付能力 |
|------|-----------|
| 核心架构 | LangGraph ≥1.2 状态机编排 + PostgresSaver Checkpointer + OpenAI 兼容 API（SSE 流式） |
| 研究能力 | 5 种报告类型 / 5 种输出格式 / 4 种引用风格 / 5 种报告语言 / 4 种报告风格 / 17 种 Tone |
| 多 Agent | Supervisor 线性+条件边模式 + 人在回路审核 + 事实核查+评审修订循环 |
| LLM 网关 | LiteLLM ≥1.6 统一接入 100+ 模型 + 三级分层（FAST/SMART/STRATEGIC）+ 真实成本追踪 |
| RAG 检索 | BM25 + 向量混合检索 + RRF 融合 + 可选 Rerank + Qdrant namespace 隔离 |
| 搜索引擎 | 22 个搜索源（国内/国外/学术三区域）+ 区域自动检测 |
| 抓取器 | 9 种抓取器 + 并行抓取（15 worker） |
| MCP 工具 | MCP 协议支持（fast/deep/disabled）+ LLM 自动选工具 + 多工具并发 |
| 可观测性 | AgentInsight SDK 6 类 trace span + 异步上下文管理器 + Null Object 降级 |
| 部署 | 三套构建模式（QA离线/生产联网/生产离线）+ 7 容器编排 |
| 测试 | 五层测试（单元/功能/API/回归/e2e）+ 96 个单元测试用例 |

---

### v1.2.0（已发布 - 2026-07-15）✅

**主题**：生产级前端 + 多 Agent 路由 + 性能优化 + 上下文压缩增强

#### ✨ 已交付

**生产级前端（Next.js 15 + React 19）**
- [x] 独立 `frontend/` 工程，基于 Next.js 15 + Vercel AI SDK + shadcn/ui + AI Elements
- [x] TypeScript strict 模式 + React Server Components + `"use client"` 标注
- [x] Zustand 状态管理（agent-store / session-store / stream-store / auth-store / nav-store）
- [x] Tailwind CSS + Linear Indigo 设计系统（CSS 变量 + light/dark 双主题）
- [x] Vercel AI SDK `useChat` hook + 原生 SSE 流式渲染
- [x] 响应式设计（移动端/平板/桌面端三档断点）

**认证与用户体系**
- [x] 登录/注册页面（`/login` + `/register`），密码 + 短信验证码 + 图片验证码
- [x] `SELF_HOST` 模式跳过登录（Next.js middleware 根据 `NEXT_PUBLIC_SELF_HOST` 跳过登录守卫）
- [x] Token 双重存储（httpOnly cookie + localStorage）
- [x] IP-based 用户身份解析（无 JWT 网关场景的降级方案）

**会话与对话管理**
- [x] 会话管理（新建/切换/删除/重命名），会话列表持久化到 localStorage
- [x] 会话级草稿隔离（切换会话时保留各自草稿）
- [x] 会话级文件管理（切换会话时保留各自文件列表）
- [x] 会话级流式状态隔离（per-session stream context）
- [x] 并发研究请求管理（MAX_BACKGROUND_STREAMS=1，LRU 驱逐 + 用户确认）
- [x] 对话记录分页（10 条最近，滚动加载更多）
- [x] 新建会话默认报告类型为 `detailed_report`

**流式渲染与交互**
- [x] 实时流式显示（逐字/逐块追加）+ "生成中"状态
- [x] 工具调用展示（折叠面板，SSE 自定义事件 `event: tool_call`）
- [x] 检索来源展示（折叠面板，SSE 自定义事件 `event: sources`）
- [x] 节点进度展示（8 类结构化 WebSocket 消息）
- [x] 人在回路审核（WebSocket `human_feedback_request` + 审核对话框）
- [x] 报告下载链接（PDF/DOCX/Markdown 等多格式）

**多 Agent 路由（方案B: Nginx 按路径分发）**
- [x] `agents.config.ts` 多 Agent 配置（当前 1 个 Agent，可扩展）
- [x] Nginx `map` 指令按 agentName 路由 SSE/WebSocket/HTTP
- [x] Next.js `/api/proxy/[...path]` route handler 代理后端 API
- [x] WebSocket `/v1/ws/{agentName}/{sessionId}` 路径格式
- [x] Nginx SSE 路径 `/api/proxy/v1/chat/completions`（`proxy_buffering off`）
- [x] Nginx 静态资源缓存（`max-age=31536000`）

**MCP 配置管理 UI**
- [x] `/mcp/researcher/setting` 页面，多 tab 布局（MCP 服务 + 智能体配置）
- [x] MCP 服务列表（我的服务 + 仓库），垂直 tab 布局
- [x] MCP 配置 CRUD（按 `agent_id` + `user_id` 隔离）
- [x] 系统级 MCP 预置（23 个系统 MCP，`is_system=TRUE`）

**报告配置与历史**
- [x] `/settings` 页面，报告格式/类型/语言/风格/Tone 配置
- [x] 历史报告列表（`history-report-panel`，模块级缓存 + stale-while-revalidate）
- [x] 报告下载与预览

**上下文压缩增强（V4-P3 两层路由架构）**
- [x] L1 Fast Path：单会话上下文 < 8K 字符时跳过压缩
- [x] L2 标准路径：≥ 8K 字符时 BM25 Top-50 召回 + 可选 FastEmbed Top-20 rerank
- [x] chunk 数量 > 30 时触发 FastEmbed rerank
- [x] FastEmbed 本地 Embeddings（bge-small-zh-v1.5 ONNX INT8，512 维）用于上下文压缩
- [x] 远程 TEI Embeddings（bge-base-zh-v1.5，768 维）仅用于 Qdrant 索引/检索
- [x] `WrittenContentCompressor` 跨子主题去重 + `ContextManager` 精排

**性能优化（52 项，已执行 43 项）**
- [x] P0: 流式渲染 RAF batching（避免 O(n²) 重渲）
- [x] P0: `useShallow` 浅比较 selector（避免全 store 订阅）
- [x] P0: 模块级空数组常量（避免 useSyncExternalStore 无限循环 #185）
- [x] P0: `React.memo` 包裹消息项（单条消息 props 不变时跳过重渲）
- [x] P0: 模块级 plugins 数组（避免 ReactMarkdown 重解析）
- [x] P1: 模块级缓存 + stale-while-revalidate（MCP 仓库列表/历史报告列表）
- [x] P1: `AbortController` 竞态保护（切换会话时取消未完成请求）
- [x] P1: `useCallback` + ref 保持 handler 引用稳定
- [x] P1: Tooltip 动态定位（基于视口边界）
- [x] P1: `skipHydration: true` 避免 SSR/客户端 hydration mismatch

**UI/UX 优化**
- [x] 主对话区背景配色统一（`--bg-card` 白色，消除割裂感）
- [x] 侧边栏统一浅灰（`--bg-sidebar`），与主对话区形成层次
- [x] 输入框容器透明背景，融入主对话区
- [x] 输入框 focus 状态无边框（`outline: none`）
- [x] 自定义 Tooltip 组件（替代原生 `title` 属性，10+ 文件统一）
- [x] 用户消息宽度优化（`max-w-[95%]`，从右向左填充）
- [x] 顶部标题 Bot 图标 + MCP 页面 Blocks 图标
- [x] 用户菜单（登出流程：abortAllStreams → logout → router.push）

**后端增强**
- [x] 会话级 `agent_id:` 前缀 thread_id（多 Agent 隔离）
- [x] 业务表 `updated_at` 触发器自动维护
- [x] 子查询 `LIMIT 1` 优化（IP 用户解析）
- [x] MCP 配置 JSONB 字段反序列化（`_mcp_row_to_dict`）

**部署增强**
- [x] 独立 `frontend` 容器（Node 20-alpine，多阶段构建，standalone 输出）
- [x] `HOSTNAME: "0.0.0.0"` 确保 Next.js standalone 监听所有接口
- [x] 健康检查用 `127.0.0.1`（避免 Alpine IPv6 解析问题）
- [x] 前端环境变量运行时注入（无 `NEXT_PUBLIC_` 前缀，通过 `/api/config` API Route 获取）
- [x] Nginx 反向代理（SSE/WebSocket/静态资源分离配置）

---

### v1.3.0（计划中 - 2026 Q3）🔧

**主题**：评测门禁 CI 化 + 多模态支持 + 性能深度优化

#### 正在开发

- [ ] **RAGAS/DeepEval 评测门禁 CI 化**
  - RAGAS：faithfulness ≥0.8 / answer_relevancy ≥0.8 / context_precision ≥0.7
  - DeepEval：任务完成率 ≥0.9 / 工具调用正确率 ≥0.95 / 幻觉率 ≤0.1
  - CI 流水线自动运行评测，不达标阻断合并
- [ ] **多模态支持（图片理解 + 表格抽取）**
  - 图片理解：支持上传图片作为研究数据源，多模态 LLM 识别图片内容
  - 表格抽取：从 PDF/DOCX 中提取结构化表格数据，保留行列关系
- [ ] **剩余性能优化（9/52 项）**
  - P1: `WrittenContentCompressor._chunk_cache` LRU 容量限制（避免无界增长）
  - P1: 启动期预热任务顺序化（避免 `_warmup_fastembed` 与 `_warmup_graph` 并发内存峰值）
  - P2: SSE `collected_content` + `final_state` 双重累积优化
  - P2: DeepResearch 递归树完整返回优化
  - P2: `_load_chitchat_history` 全量加载 messages 优化
  - P2: 流式 body pass-through（避免双缓冲）
  - P2: WebSocket 指数退避重连
  - P2: `useShallow` 细粒度 selector 覆盖剩余组件
  - P3: 主题切换动效优化

#### 已规划功能

- [ ] 报告模板自定义（用户上传 Markdown 模板）
- [ ] 多语言报告增强（增加德语/西班牙语/阿拉伯语）
- [ ] 搜索引擎插件机制（用户自定义搜索源）
- [ ] 抓取器性能优化（headless 浏览器池化复用）
- [ ] WebSocket 消息压缩（permessage-deflate）
- [ ] Agent 容器内存限制与 ONNX 线程数调优

---

### v1.4.0（展望 - 2026 Q4）🎯

**主题**：规模化 + 生态拓展

#### 待定功能

- [ ] **多 Agent Swarm 模式**
  - 在 Supervisor 模式之外，增加 Swarm 去中心化协作模式
  - Agent 间动态委派任务，支持更复杂的研究场景
- [ ] **Agent Marketplace（技能热插拔）**
  - 技能市场：用户可浏览、安装、卸载技能包
  - 热插拔：运行时动态加载/卸载技能，无需重启服务
  - 技能分享：用户可发布自研技能到市场
- [ ] **知识库管理增强**
  - 知识库可视化（向量分布、召回热力图）
  - 知识库增量更新与版本管理
  - 跨 Agent 知识库共享机制
- [ ] **报告协作**
  - 多用户协同编辑报告
  - 报告评论与批注
  - 版本历史与差异对比
- [ ] **评测 dashboard**
  - 可视化评测趋势（faithfulness/relevancy 历史曲线）
  - 按报告类型/行业维度分组统计
- [ ] **离线模型微调**
  - 基于用户反馈数据微调 Embedding/Rerank 模型
  - 行业专有词表扩展
- [ ] **kubectl 部署支持**
  - Helm Chart 封装
  - 水平自动扩缩容（HPA）

---

### v2.0.0（长期目标 - 2027）🌟

**主题**：生产级稳定 + 平台化

- [ ] **SLA 99.9% 可用性**
  - 多副本部署 + 故障自动转移
  - 灰度发布与金丝雀部署
- [ ] **多租户 SaaS 模式**
  - 租户隔离与资源配额
  - 计量计费（按 Token / 按报告 / 按存储）
- [ ] **可视化编排**
  - 拖拽式 Agent 流水线设计器
  - 节点参数可视化配置
- [ ] **插件生态**
  - 第三方搜索源/抓取器/工具插件 SDK
  - 插件市场与审核机制
- [ ] **合规认证**
  - 等保三级 / ISO 27001
  - 数据出境合规（GDPR / 个人信息保护法）
- [ ] **性能基准**
  - 单报告生成 < 60s（basic_report）
  - 并发支持 ≥ 100 QPS
  - 冷启动 < 10s

---

## 已规划功能列表

| 功能 | 目标版本 | 状态 | 优先级 |
|------|---------|------|--------|
| RAGAS/DeepEval 评测门禁 CI | v1.3.0 | 🔧 开发中 | P0 |
| 多模态支持（图片+表格） | v1.3.0 | 🔧 开发中 | P0 |
| 剩余性能优化（9/52 项） | v1.3.0 | 🔧 开发中 | P1 |
| 报告模板自定义 | v1.3.0 | 📋 已规划 | P1 |
| 多语言报告增强 | v1.3.0 | 📋 已规划 | P2 |
| 搜索引擎插件机制 | v1.3.0 | 📋 已规划 | P1 |
| Agent 容器内存调优 | v1.3.0 | 📋 已规划 | P1 |
| Agent Marketplace | v1.4.0 | 🔮 展望 | P1 |
| Swarm 多 Agent 模式 | v1.4.0 | 🔮 展望 | P1 |
| 知识库管理增强 | v1.4.0 | 🔮 展望 | P1 |
| 报告协作 | v1.4.0 | 🔮 展望 | P2 |
| 评测 dashboard | v1.4.0 | 🔮 展望 | P2 |
| k8s 部署支持 | v1.4.0 | 🔮 展望 | P1 |
| 多租户 SaaS | v2.0.0 | 🌟 长期 | P1 |
| 可视化编排 | v2.0.0 | 🌟 长期 | P2 |
| 合规认证 | v2.0.0 | 🌟 长期 | P1 |

---

## 版本发布节奏

- **Patch**（1.2.X）：每月按需发布，Bug 修复与小改进
- **Minor**（1.X.0）：每季度发布，向后兼容的功能新增
- **Major**（X.0.0）：按里程碑发布，不兼容的 API 变更

> 路线图仅供参考，实际发布计划可能根据社区反馈与优先级调整。欢迎在 [GitHub Issues](https://github.com/AgentInsight/agentinsight-researcher/issues) 提出功能建议。

---

## English

## Project Vision

**agentinsight-researcher** aims to be a **Chinese-first enterprise-grade AI research analysis agent**, using LangGraph as the orchestration kernel, MCP as the tool protocol, and AgentInsight SDK as the observability foundation, exposing an OpenAI-compatible API so any team can build deep research capabilities at minimal cost.

We continue to deepen our focus on:

- **Chinese-first**: Chinese search sources (Bocha/Metaso), Chinese tokenization (jieba), Chinese embeddings (bge-base-zh-v1.5), Chinese Rerank (bge-reranker-v2-m3)
- **Enterprise-grade security**: Three-tier data isolation (agent_id × user_id × session_id), JWT identity, IP-based fallback, security response headers, environment-variable-only key injection
- **Full-link observability**: AgentInsight SDK 6 types of trace spans, tracking from Agent entry to Embedding calls
- **Multi-agent collaboration**: Supervisor mode complete pipeline (Researcher → Writer → FactChecker → Reviewer → Reviser → Visualizer → Publisher)
- **Production-grade frontend**: Next.js 15 + Vercel AI SDK + shadcn/ui dual-layer frontend (test page + production frontend), supporting multi-agent routing, streaming rendering, human-in-the-loop

---

## Version Roadmap

### v1.0.0 (Released - 2026-07-04) ✅

**Initial release**, establishing core architecture and foundational capabilities.

| Domain | Delivered Capabilities |
|--------|----------------------|
| Core Architecture | LangGraph ≥1.2 state machine orchestration + PostgresSaver Checkpointer + OpenAI-compatible API (SSE streaming) |
| Research Capabilities | 5 report types / 5 output formats / 4 citation styles / 5 report languages / 4 report styles / 17 tones |
| Multi-Agent | Supervisor linear + conditional edge mode + human-in-the-loop review + fact-checking + review revision loop |
| LLM Gateway | LiteLLM ≥1.6 unified access to 100+ models + three-tier layering (FAST/SMART/STRATEGIC) + real cost tracking |
| RAG Retrieval | BM25 + vector hybrid retrieval + RRF fusion + optional Rerank + Qdrant namespace isolation |
| Search Engines | 22 search sources (domestic/global/academic regions) + auto region detection |
| Scrapers | 9 scrapers + parallel scraping (15 workers) |
| MCP Tools | MCP protocol support (fast/deep/disabled) + LLM auto tool selection + multi-tool concurrency |
| Observability | AgentInsight SDK 6 types of trace spans + async context manager + Null Object degradation |
| Deployment | Three build modes (QA offline / production online / production offline) + 7-container orchestration |
| Testing | Five-tier testing (unit/functional/API/regression/e2e) + 96 unit test cases |

---

### v1.2.0 (Released - 2026-07-15) ✅

**Theme**: Production-grade frontend + multi-agent routing + performance optimization + context compression enhancement

#### ✨ Delivered

**Production Frontend (Next.js 15 + React 19)**
- [x] Standalone `frontend/` project based on Next.js 15 + Vercel AI SDK + shadcn/ui + AI Elements
- [x] TypeScript strict mode + React Server Components + `"use client"` annotation
- [x] Zustand state management (agent-store / session-store / stream-store / auth-store / nav-store)
- [x] Tailwind CSS + Linear Indigo design system (CSS variables + light/dark themes)
- [x] Vercel AI SDK `useChat` hook + native SSE streaming rendering
- [x] Responsive design (mobile/tablet/desktop breakpoints)

**Authentication & User System**
- [x] Login/register pages (`/login` + `/register`), password + SMS code + image captcha
- [x] `SELF_HOST` mode skips login (Next.js middleware skips login guard based on `NEXT_PUBLIC_SELF_HOST`)
- [x] Token dual storage (httpOnly cookie + localStorage)
- [x] IP-based user identity resolution (fallback for non-JWT gateway scenarios)

**Session & Conversation Management**
- [x] Session management (new/switch/delete/rename), session list persisted to localStorage
- [x] Per-session draft isolation (drafts preserved when switching sessions)
- [x] Per-session file management (files preserved when switching sessions)
- [x] Per-session streaming state isolation (per-session stream context)
- [x] Concurrent research request management (MAX_BACKGROUND_STREAMS=1, LRU eviction + user confirmation)
- [x] Conversation record pagination (10 recent, scroll to load more)
- [x] New session default report type is `detailed_report`

**Streaming Rendering & Interaction**
- [x] Real-time streaming display (char/chunk append) + "generating" status
- [x] Tool call display (collapsible panel, SSE custom event `event: tool_call`)
- [x] Retrieval source display (collapsible panel, SSE custom event `event: sources`)
- [x] Node progress display (8 types of structured WebSocket messages)
- [x] Human-in-the-loop review (WebSocket `human_feedback_request` + review dialog)
- [x] Report download links (PDF/DOCX/Markdown and more formats)

**Multi-Agent Routing (Plan B: Nginx path-based dispatch)**
- [x] `agents.config.ts` multi-agent config (currently 1 agent, extensible)
- [x] Nginx `map` directive routing SSE/WebSocket/HTTP by agentName
- [x] Next.js `/api/proxy/[...path]` route handler proxying backend API
- [x] WebSocket `/v1/ws/{agentName}/{sessionId}` path format
- [x] Nginx SSE path `/api/proxy/v1/chat/completions` (`proxy_buffering off`)
- [x] Nginx static asset caching (`max-age=31536000`)

**MCP Configuration Management UI**
- [x] `/mcp/researcher/setting` page, multi-tab layout (MCP services + agent config)
- [x] MCP service list (my services + repository), vertical tab layout
- [x] MCP config CRUD (isolated by `agent_id` + `user_id`)
- [x] System-level MCP presets (23 system MCPs, `is_system=TRUE`)

**Report Configuration & History**
- [x] `/settings` page, report format/type/language/style/tone configuration
- [x] History report list (`history-report-panel`, module-level cache + stale-while-revalidate)
- [x] Report download and preview

**Context Compression Enhancement (V4-P3 Two-Layer Routing Architecture)**
- [x] L1 Fast Path: skip compression when single-session context < 8K chars
- [x] L2 Standard Path: BM25 Top-50 recall + optional FastEmbed Top-20 rerank when ≥ 8K chars
- [x] Trigger FastEmbed rerank when chunk count > 30
- [x] FastEmbed local embeddings (bge-small-zh-v1.5 ONNX INT8, 512 dims) for context compression
- [x] Remote TEI embeddings (bge-base-zh-v1.5, 768 dims) only for Qdrant indexing/retrieval
- [x] `WrittenContentCompressor` cross-subtopic deduplication + `ContextManager` reranking

**Performance Optimization (52 items, 43 executed)**
- [x] P0: Streaming rendering RAF batching (avoid O(n²) re-render)
- [x] P0: `useShallow` shallow comparison selector (avoid full store subscription)
- [x] P0: Module-level empty array constants (avoid useSyncExternalStore infinite loop #185)
- [x] P0: `React.memo` wrapped message items (skip re-render when single message props unchanged)
- [x] P0: Module-level plugins array (avoid ReactMarkdown re-parsing)
- [x] P1: Module-level cache + stale-while-revalidate (MCP repository list/history report list)
- [x] P1: `AbortController` race protection (cancel unfinished requests when switching sessions)
- [x] P1: `useCallback` + ref for stable handler references
- [x] P1: Tooltip dynamic positioning (based on viewport boundaries)
- [x] P1: `skipHydration: true` to avoid SSR/client hydration mismatch

**UI/UX Optimization**
- [x] Unified main conversation area background color (`--bg-card` white, eliminate fragmentation)
- [x] Unified sidebar light gray (`--bg-sidebar`), forming layers with main conversation area
- [x] Input container transparent background, blending into main conversation area
- [x] Input focus state borderless (`outline: none`)
- [x] Custom Tooltip component (replacing native `title` attribute, unified across 10+ files)
- [x] User message width optimization (`max-w-[95%]`, filling right-to-left)
- [x] Top title Bot icon + MCP page Blocks icon
- [x] User menu (logout flow: abortAllStreams → logout → router.push)

**Backend Enhancement**
- [x] Session-level `agent_id:` prefix thread_id (multi-agent isolation)
- [x] Business table `updated_at` trigger auto-maintenance
- [x] Subquery `LIMIT 1` optimization (IP user resolution)
- [x] MCP config JSONB field deserialization (`_mcp_row_to_dict`)

**Deployment Enhancement**
- [x] Standalone `frontend` container (Node 20-alpine, multi-stage build, standalone output)
- [x] `HOSTNAME: "0.0.0.0"` ensuring Next.js standalone listens on all interfaces
- [x] Health check using `127.0.0.1` (avoiding Alpine IPv6 resolution issues)
- [x] Frontend environment variables runtime injection (no `NEXT_PUBLIC_` prefix, fetched via `/api/config` API Route)
- [x] Nginx reverse proxy (SSE/WebSocket/static assets separation configuration)

---

### v1.3.0 (Planned - 2026 Q3) 🔧

**Theme**: Evaluation gate CI integration + multimodal support + deep performance optimization

#### In Development

- [ ] **RAGAS/DeepEval evaluation gate CI integration**
  - RAGAS: faithfulness ≥0.8 / answer_relevancy ≥0.8 / context_precision ≥0.7
  - DeepEval: task completion rate ≥0.9 / tool call accuracy ≥0.95 / hallucination rate ≤0.1
  - CI pipeline auto-runs evaluation; blocks merge if below threshold
- [ ] **Multimodal support (image understanding + table extraction)**
  - Image understanding: support uploading images as research data source; multimodal LLM identifies image content
  - Table extraction: extract structured table data from PDF/DOCX, preserving row-column relationships
- [ ] **Remaining performance optimizations (9/52 items)**
  - P1: `WrittenContentCompressor._chunk_cache` LRU capacity limit (avoid unbounded growth)
  - P1: Startup warmup task serialization (avoid `_warmup_fastembed` and `_warmup_graph` concurrent memory peak)
  - P2: SSE `collected_content` + `final_state` dual accumulation optimization
  - P2: DeepResearch recursive tree full return optimization
  - P2: `_load_chitchat_history` full messages loading optimization
  - P2: Streaming body pass-through (avoid double buffering)
  - P2: WebSocket exponential backoff reconnection
  - P2: `useShallow` fine-grained selector coverage for remaining components
  - P3: Theme switching animation optimization

#### Planned Features

- [ ] Custom report templates (user-uploaded Markdown templates)
- [ ] Multi-language report enhancement (add German/Spanish/Arabic)
- [ ] Search engine plugin mechanism (user-defined search sources)
- [ ] Scraper performance optimization (headless browser pooling and reuse)
- [ ] WebSocket message compression (permessage-deflate)
- [ ] Agent container memory limit and ONNX thread tuning

---

### v1.4.0 (Outlook - 2026 Q4) 🎯

**Theme**: Scaling + ecosystem expansion

#### Tentative Features

- [ ] **Multi-Agent Swarm mode**
  - Add Swarm decentralized collaboration mode alongside Supervisor mode
  - Dynamic task delegation between agents, supporting more complex research scenarios
- [ ] **Agent Marketplace (hot-swappable skills)**
  - Skill marketplace: users can browse, install, and uninstall skill packs
  - Hot-swapping: dynamically load/unload skills at runtime without service restart
  - Skill sharing: users can publish self-developed skills to the marketplace
- [ ] **Knowledge base management enhancement**
  - Knowledge base visualization (vector distribution, recall heatmap)
  - Incremental updates and version management
  - Cross-agent knowledge base sharing
- [ ] **Report collaboration**
  - Multi-user collaborative editing
  - Report comments and annotations
  - Version history and diff comparison
- [ ] **Evaluation dashboard**
  - Visualized evaluation trends (faithfulness/relevancy historical curves)
  - Grouped statistics by report type/industry dimension
- [ ] **Offline model fine-tuning**
  - Fine-tune Embedding/Rerank models based on user feedback data
  - Industry-specific vocabulary expansion
- [ ] **kubectl deployment support**
  - Helm Chart packaging
  - Horizontal Pod Autoscaler (HPA)

---

### v2.0.0 (Long-term Goal - 2027) 🌟

**Theme**: Production-grade stability + platformization

- [ ] **SLA 99.9% availability**
  - Multi-replica deployment + automatic failover
  - Canary releases and gradual rollouts
- [ ] **Multi-tenant SaaS mode**
  - Tenant isolation and resource quotas
  - Metered billing (by Token / by report / by storage)
- [ ] **Visual orchestration**
  - Drag-and-drop Agent pipeline designer
  - Visual node parameter configuration
- [ ] **Plugin ecosystem**
  - Third-party search source/scraper/tool plugin SDK
  - Plugin marketplace and review mechanism
- [ ] **Compliance certification**
  - MLPS Level 3 / ISO 27001
  - Data cross-border compliance (GDPR / PIPL)
- [ ] **Performance benchmarks**
  - Single report generation < 60s (basic_report)
  - Concurrency support ≥ 100 QPS
  - Cold start < 10s

---

## Planned Feature List

| Feature | Target Version | Status | Priority |
|---------|---------------|--------|----------|
| RAGAS/DeepEval evaluation gate CI | v1.3.0 | 🔧 In Development | P0 |
| Multimodal support (image+table) | v1.3.0 | 🔧 In Development | P0 |
| Remaining performance optimizations (9/52) | v1.3.0 | 🔧 In Development | P1 |
| Custom report templates | v1.3.0 | 📋 Planned | P1 |
| Multi-language report enhancement | v1.3.0 | 📋 Planned | P2 |
| Search engine plugin mechanism | v1.3.0 | 📋 Planned | P1 |
| Agent container memory tuning | v1.3.0 | 📋 Planned | P1 |
| Agent Marketplace | v1.4.0 | 🔮 Outlook | P1 |
| Swarm multi-agent mode | v1.4.0 | 🔮 Outlook | P1 |
| Knowledge base management enhancement | v1.4.0 | 🔮 Outlook | P1 |
| Report collaboration | v1.4.0 | 🔮 Outlook | P2 |
| Evaluation dashboard | v1.4.0 | 🔮 Outlook | P2 |
| k8s deployment support | v1.4.0 | 🔮 Outlook | P1 |
| Multi-tenant SaaS | v2.0.0 | 🌟 Long-term | P1 |
| Visual orchestration | v2.0.0 | 🌟 Long-term | P2 |
| Compliance certification | v2.0.0 | 🌟 Long-term | P1 |

---

## Release Cadence

- **Patch** (1.2.X): Released monthly as needed; bug fixes and minor improvements
- **Minor** (1.X.0): Released quarterly; backward-compatible feature additions
- **Major** (X.0.0): Released by milestone; incompatible API changes

> The roadmap is for reference only; actual release plans may adjust based on community feedback and priorities. Feature suggestions are welcome at [GitHub Issues](https://github.com/AgentInsight/agentinsight-researcher/issues).
