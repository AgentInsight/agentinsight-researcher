# agentinsight-researcher 最终需求规格说明书

> **文档版本**：v1.0 (锁定版)
> **生成日期**：2026-07-02
> **状态**：待用户最终确认；确认后进入阶段 1 实施
> **依据**：用户原始 14 项需求 + AGENTS.md 硬约束 + GPT Researcher / AgentInsightService 参考研究

---

## 一、项目定位

**agentinsight-researcher** 是一个**中文优先**的研究分析智能体，对标 GPT Researcher 的 Planner→Researcher→Reviewer→Writer 多阶段研究流水线架构，基于 AGENTS.md 规定的技术栈（LangGraph + LiteLLM + Qdrant + Postgres + AgentInsight SDK + 6 容器离线部署）实现，支持 GICS 68 行业专家化研究、MCP/文件多数据源、Markdown/HTML/PDF 多格式报告输出。

**核心差异点**（相对 GPT Researcher）：
1. 中文优先检索（国内/国外双引擎路由）
2. GICS 68 行业 × 157 子行业专家提示词族
3. 强制遵循 AGENTS.md 合规约束（LangGraph 唯一编排、PostgresSaver、LiteLLM、6 类 trace、6 容器部署）
4. 复用 AgentInsightService 的 RAG 双 namespace 数据隔离模式

---

## 二、AI 专家团队（10+ 角色，用于组织开发任务）

> **说明**：这 12 个角色是**组织本任务开发的虚拟专家团队**，负责不同模块的设计与实现，**不是智能体内部节点**。智能体内部角色见第三章。

| # | 专家角色 | 负责模块 | 对标 GPT Researcher 分析师 |
|---|---|---|---|
| 1 | 项目架构师 | 整体架构、LangGraph 图、目录边界 | 第 1 位 |
| 2 | Skills 架构师 | skills/ 技能组件协作机制 | 第 2 位 |
| 3 | 信息检索专家 | 国内/国外检索器、中文优先策略 | 第 3 位 |
| 4 | 网页抓取专家 | scraper 多后端、WorkerPool、限流 | 第 4 位 |
| 5 | LLM 与配置专家 | LiteLLM 网关、三级模型、Config SSOT | 第 5 位 |
| 6 | MCP 协议专家 | MCP 工具选择与执行、三策略 | 第 6 位 |
| 7 | 后端 API 专家 | FastAPI、OpenAI 兼容端点、SSE 流式 | 第 7 位 |
| 8 | RAG 与可观测性专家 | Qdrant 双 namespace、6 类 trace、RAGAS 门禁 | 第 12 位 |
| 9 | 提示词工程专家 | 通用提示词 + 68 行业专家提示词族 | 第 11 位 |
| 10 | DevOps 与质量保障专家 | Docker 6 容器、离线包、5 层测试、DeepEval 门禁 | 第 10 位 |
| 11 | 行业知识工程师 | GICS 68 行业 + 157 子行业数据整理与向量化 | （新增） |
| 12 | 安全合规专家 | JWT 身份解析、PII 脱敏、Prompt Injection 防御 | 第 12 位补充 |

---

## 三、智能体内部角色（GPT Researcher 风格）

> **说明**：参照 GPT Researcher `gpt_researcher/skills/` 实际架构，采用**单 Agent + Skills 组合**模式（外层用 LangGraph StateGraph 包装以满足 AGENTS.md 第 5 章），**不是 12 个独立节点**。

### 3.1 核心 Skill 组件（6+1 个）

| Skill 节点 | 对标 GPT Researcher | 职责 |
|---|---|---|
| **ResearchConductor 研究总指挥** | `skills/researcher.py` | 查询规划、检索调度、上下文聚合（含 Planner+Researcher 职责） |
| **ContextManager 上下文管理者** | `skills/context_manager.py` | EmbeddingsFilter 压缩 + 跨子主题去重，Token 优化核心 |
| **BrowserManager 浏览器管理者** | `skills/browser.py` | 并行抓取 URL、WorkerPool 限流、图片筛选 |
| **SourceCurator 来源策展师** | `skills/curator.py` | LLM 评估来源可信度与相关性（Reviewer 职责，可选启用） |
| **ReportGenerator 报告生成器** | `skills/writer.py` | 按行业模板合成长报告（Writer 职责） |
| **Publisher 发布器** | `multi_agents/agents/publisher.py` | Markdown/HTML/PDF 输出、引用规范化 |
| **DeepResearcher 深度研究员**（可选，v2） | `skills/deep_research.py` | 递归树状探索（breadth × depth） |

### 3.2 内部前置步骤（非独立节点）

| 步骤 | 职责 |
|---|---|
| **IndustryClassifier 行业分类** | ResearchConductor 内部前置：Qdrant 检索 GICS 知识库 → LLM 兜底 → 加载对应行业 prompt_family |

### 3.3 行业专家提示词族

- 68 套 `prompt_family` YAML 配置（位于 `config/researcher/industry_prompts/`）
- 被 ResearchConductor / SourceCurator / ReportGenerator 共享复用
- 对标 GPT Researcher 的 `prompt_family` 机制，不增加节点复杂度

### 3.4 架构图

```
入口（OpenAI 兼容端点 /v1/chat/completions）
  │
  ▼
ResearchConductor（研究总指挥）
  ├─ IndustryClassifier（内部步骤：Qdrant 检索 GICS → LLM 兜底，加载行业 prompt_family）
  ├─ plan_research（Planner：按行业提示词拆解子查询）
  └─ asyncio.gather 并行 _process_sub_query：
       ├─ BrowserManager.browse_urls（抓取）
       ├─ ContextManager.get_similar_content（压缩+去重）
       └─ MCP（可选，fast/deep/disabled）
  │
  ▼
SourceCurator（来源策展，可选，cfg.CURATE_SOURCES=True 时启用）
  │
  ▼
ReportGenerator（报告生成）
  └─ generate_report（按行业模板 + tone + format）
  │
  ▼
Publisher（发布：Markdown / HTML / PDF）
```

---

## 四、技术选型（锁定）

| 维度 | 选型 | 依据 |
|---|---|---|
| **编排内核** | LangGraph StateGraph + PostgresSaver + functools.partial 注入 | AGENTS.md 第 5 章硬约束 + AgentInsightService insight/graph.py 模式 |
| **LLM 网关** | LiteLLM（替换 openai SDK） + 三级模型（fast/smart/strategic） | AGENTS.md 第 9 章硬约束 + GPT Researcher 模式 |
| **检索** | HybridRetriever（BM25+jieba + bge-large-zh-v1.5 + RRF k=60） + 双 namespace | AGENTS.md 第 7 章 + AgentInsightService/common/retriever.py |
| **重排** | bge-reranker-v2-m3 Top-K 后 rerank | AGENTS.md 第 7 章 |
| **行业识别** | Qdrant 向量检索（GICS 知识库，namespace=agent_id）→ LLM 兜底 | 用户需求 4 |
| **国内搜索** | 博查搜索（Bocha）为主 + DuckDuckGo 兜底 | 用户需求 5（中文优先） |
| **国外搜索** | Tavily + arxiv + Semantic Scholar | GPT Researcher 模式 |
| **抓取** | BeautifulSoup（默认）+ Playwright（JS 渲染）+ PyMuPDF（PDF）+ ArxivScraper | GPT Researcher scraper 体系 |
| **报告输出** | Markdown（默认）→ HTML（jinja2）→ PDF（WeasyPrint） | 用户需求 6 |
| **MCP** | langchain-mcp-adapters + LLM 智能选工具 + 三策略（fast/deep/disabled） | GPT Researcher mcp/ 模块 |
| **Token 优化** | EmbeddingsFilter（sim=0.35）+ Word Limit（25k）+ 三级 LLM + MCP 缓存 + sub_queries≤3 | GPT Researcher context/ 模式 |
| **文件上传** | PDF/DOCX/MD/TXT/HTML/CSV/XLSX/PPTX | 用户需求 8 |
| **可观测** | 6 类 trace_xxx 异步上下文管理器 + _NoopSpan 降级 | AGENTS.md 第 10 章 |
| **部署** | 6 容器 Docker Compose + 离线 packages/ | AGENTS.md 第 12 章 |
| **测试** | 5 层（unit/functional/regression/api/e2e）+ RAGAS + DeepEval | AGENTS.md 第 13 章 |

---

## 五、报告类型（首版范围）

| 类型 | 范围 | 对标 GPT Researcher |
|---|---|---|
| **basic_report 基础报告** | 首版实现 | `backend/report_type/basic_report/` |
| **detailed_report 详细报告** | 首版实现 | `backend/report_type/detailed_report/` |
| **deep_research 深度研究** | v2（首版预留接口） | `backend/report_type/deep_research/` |

---

## 六、目录结构（严格遵循 AGENTS.md 第 3 章）

```
agentinsight-researcher/
├── src/
│   ├── graph/                    # LangGraph 唯一编排入口
│   │   ├── state.py              # ResearcherState TypedDict
│   │   ├── nodes.py              # 节点纯函数 + functools.partial 注入
│   │   ├── edges.py              # 条件路由
│   │   └── builder.py            # build_researcher_graph + PostgresSaver
│   ├── agents/
│   │   └── researcher/
│   │       ├── orchestrator.py   # 单一编排入口
│   │       └── prompts.py        # 通用提示词（行业无关）
│   ├── common/                   # 公用基础（不得依赖 agents/ 或业务模块）
│   │   ├── llm.py                # ★ 重写为 LiteLLM 网关
│   │   ├── memory.py             # ★ 改为 PostgresSaver 包装
│   │   ├── tracing.py            # 6 类 trace_xxx
│   │   ├── config.py / context.py / prompt_manager.py / cache_manager.py
│   │   └── semantic_router.py    # 行业分类用
│   ├── config/
│   │   ├── settings.py           # 全局 pydantic-settings Settings SSOT
│   │   └── researcher/           # 子智能体配置
│   │       ├── gics_industries.yaml       # GICS 68 行业 + 157 子行业
│   │       ├── search_providers.yaml      # 国内/国外搜索配置
│   │       └── industry_prompts/          # 68 套行业专家提示词族
│   │           ├── software.yaml
│   │           ├── finance.yaml
│   │           └── ... (68 个文件)
│   ├── skills/
│   │   └── researcher/
│   │       ├── research_conductor.py     # 对标 skills/researcher.py
│   │       ├── context_manager.py        # 对标 skills/context_manager.py
│   │       ├── browser_manager.py        # 对标 skills/browser.py
│   │       ├── source_curator.py         # 对标 skills/curator.py
│   │       ├── report_generator.py       # 对标 skills/writer.py
│   │       ├── deep_research.py          # 对标 skills/deep_research.py（v2）
│   │       ├── industry_classifier.py    # GICS 行业识别
│   │       ├── publisher.py              # MD/HTML/PDF 输出
│   │       └── mcp_coordinator.py        # MCP 数据源
│   ├── tools/
│   │   ├── registry.py           # MCP 工具注册中心
│   │   └── mcp/                  # MCP Server 封装
│   ├── rag/                      # 自研 RAG 层
│   │   ├── embeddings.py         # 双 namespace（共享+用户私有）
│   │   ├── retriever.py          # HybridRetriever + RRF
│   │   └── document_source.py
│   ├── llm/                      # LiteLLM 网关封装
│   │   └── client.py             # LLMClient + 三级模型
│   ├── memory/                   # Postgres Checkpointer
│   │   └── checkpointer.py
│   ├── observability/
│   │   └── tracing.py            # 6 类 trace_xxx
│   └── api/
│       ├── routes.py             # OpenAI 兼容端点 + SSE
│       └── middleware.py         # JWT 身份解析 + 安全响应头
├── static/
│   └── index.html                # 前端测试页面（AGENTS.md 第 14 章）
├── tests/
│   ├── unit/                     # 单元测试（构建期）
│   ├── functional/               # 功能测试（部署后）
│   ├── regression/               # 回归测试（合并门禁）
│   ├── api/                      # API 测试
│   └── e2e/                      # 端到端测试
├── evals/
│   ├── rag/                      # RAGAS 门禁
│   └── agent/                    # DeepEval 门禁
├── packages/                     # 离线部署包
│   ├── wheels/                   # 预下载 Python wheel
│   ├── debs/                     # 预下载系统 .deb
│   ├── models/                   # 预下载 BGE 模型权重
│   ├── images/                   # 预下载 Docker 镜像 tarball
│   ├── uploads/                  # 用户上传文件
│   └── sql/
│       └── init.sql              # 数据库初始化
├── server.py                     # FastAPI 入口
├── Dockerfile                    # 多阶段构建
├── docker-compose.yml            # 6 容器编排
├── requirements.txt              # 依赖清单
├── pyproject.toml                # ruff/mypy/pytest 配置
├── .env.template                 # 环境变量模板
└── AGENTS.md                     # 项目规则（已存在）
```

---

## 七、GICS 行业数据方案

| 项 | 方案 |
|---|---|
| **数据来源** | 预先抓取 GICS 2024/2025 最新标准（11 Sectors → 25 Industry Groups → 74 Industries → 163 Sub-Industries，实际数字以最新标准为准） |
| **存储形式** | `config/researcher/gics_industries.yaml` 静态文件随仓库分发 |
| **向量化** | Docker 构建时嵌入 Qdrant（namespace=agent_id，共享知识库） |
| **更新机制** | 提供 `scripts/update_gics.py` 脚本可手动刷新 |
| **运行时依赖** | 无联网依赖，离线部署友好（AGENTS.md 第 12 章） |
| **行业识别流程** | Qdrant 向量检索 → 命中失败时 LLM 兜底 → 加载对应 prompt_family |

---

## 八、68 行业专家提示词族

| 项 | 方案 |
|---|---|
| **数量** | 68 套（对应 GICS 68 行业） |
| **位置** | `config/researcher/industry_prompts/{industry}.yaml` |
| **每套内容** | industry_code / industry_name / planner_prompt / researcher_prompt / reviewer_prompt / writer_prompt / key_dimensions / data_sources_preference |
| **复用机制** | 被 ResearchConductor / SourceCurator / ReportGenerator 共享（prompt_family 模式） |
| **数据来源** | 搜索网络上对 68 行业研究分析专家的角色描述，结合 GPT Researcher 提示词模板整理 |

---

## 九、搜索策略（中文优先）

### 9.1 路由逻辑

```
用户查询
  │
  ├─ IndustryClassifier 识别行业
  │
  ▼
查询语言/关键词分析
  ├─ 中文查询 / 国内行业 → 国内搜索引擎（博查优先 + DuckDuckGo 兜底）
  ├─ 英文查询 / 国外行业 → 国外搜索引擎（Tavily + arxiv + Semantic Scholar）
  └─ 混合 → 双引擎并行 + RRF 融合
```

### 9.2 内部数据源

- Qdrant 向量检索（共享知识库 namespace=agent_id + 用户私有 namespace={agent_id}:{user_id}）
- 用户上传文件（向量化后入 Qdrant 用户私有 namespace）

### 9.3 MCP 数据源

- 用户可配置 MCP Server 作为数据源
- 三策略：fast（默认，仅对原查询运行一次缓存复用）/ deep（每子查询都运行）/ disabled

---

## 十、报告输出

| 格式 | 实现 | 默认 |
|---|---|---|
| **Markdown** | mistune 解析 + APA 引用格式 | ✅ 默认 |
| **HTML** | jinja2 模板 + 内联 CSS | 可选 |
| **PDF** | WeasyPrint（Markdown→HTML→PDF，纯 Python，中文支持好） | 可选 |

**报告约束**：
- 至少 `TOTAL_WORDS=1200` 字
- 强制 Markdown + APA 格式
- `# ## ###` 结构化标题
- Web 源必须超链接引用：`([in-text citation](url))`
- 末尾附参考文献列表
- 注入当前日期
- 支持 tone 语气控制（objective / analytical / opinionated / casual）

---

## 十一、Token 优化策略（GPT Researcher 模式）

| 策略 | 参数 | 说明 |
|---|---|---|
| **EmbeddingsFilter 压缩** | `SIMILARITY_THRESHOLD=0.35` | 按相似度过滤文档块，低于阈值丢弃 |
| **小内容快速路径** | `COMPRESSION_THRESHOLD=8000` | 低于此字符数跳过压缩，直接返回 |
| **Word Limit 截断** | `MAX_CONTEXT_WORDS=25000` | DeepResearch 上下文词数上限 |
| **三级 LLM 分工** | fast/smart/strategic | 快速任务用 fast，报告写作用 smart，规划用 strategic |
| **MCP 缓存** | fast 策略 | 仅对原查询运行一次 MCP，子查询复用缓存 |
| **子查询数量限制** | `MAX_ITERATIONS=3` | 限制子查询数量 |
| **Token 计数** | tiktoken | 精确计数，避免超限 |
| **跨子主题去重** | WrittenContentCompressor | 已写章节相似度过滤，避免重复内容 |

---

## 十二、部署方案（6 容器，AGENTS.md 第 12 章）

| 服务 | 镜像 | 端口 | 健康检查 |
|---|---|---|---|
| `postgres` | `postgres:16-alpine` | 5432（127.0.0.1） | `pg_isready` |
| `redis` | `redis:7-alpine` | 6379（127.0.0.1） | `redis-cli ping` |
| `qdrant` | `qdrant/qdrant:v1.18.0` | 6333/6334（127.0.0.1） | `/healthz` |
| `embeddings` | `text-embeddings-inference:cpu-1.5`（bge-large-zh-v1.5） | 8100（127.0.0.1） | `/health` |
| `rerank` | `text-embeddings-inference:cpu-1.5`（bge-reranker-v2-m3） | 8101（127.0.0.1） | `/health` |
| `agent` | 本仓 Dockerfile（python:3.12-slim） | 8066（对外） | `GET /health` |

**离线部署硬约束**：
- 所有镜像预下载为 tarball（`packages/images/`），部署机 `docker load` 导入
- Python 依赖预下载 wheel 到 `packages/wheels/`，构建时 `--no-index --find-links`
- 系统依赖预下载 .deb 到 `packages/debs/`，构建时 `dpkg -i`
- BGE 模型权重预下载到 `packages/models/`
- 依赖顺序：postgres → redis → qdrant → embeddings → rerank → agent
- `depends_on` 必须用 `condition: service_healthy`
- 生产仅 `agent:8066` 对外暴露，其余绑定 `127.0.0.1`

---

## 十三、测试方案（AGENTS.md 第 13 章）

### 13.1 测试分层

| 类型 | 目录 | 执行环境 | 触发时机 |
|---|---|---|---|
| 单元测试 | `tests/unit/` | 本地 / 构建期 | 每次 commit、Docker build |
| 功能测试 | `tests/functional/` | 部署后容器栈 | 容器栈健康后 |
| 回归测试 | `tests/regression/` | 部署后容器栈 | 合并 main 前 |
| API 测试 | `tests/api/` | 部署后容器栈 | 容器栈健康后 |
| 端到端测试 | `tests/e2e/` | 部署后容器栈 | 合并 main 前、发布前 |

### 13.2 评测门禁（CI 强制）

| 工具 | 指标 | 阈值 |
|---|---|---|
| **RAGAS** | faithfulness | ≥0.8 |
| **RAGAS** | answer_relevancy | ≥0.8 |
| **RAGAS** | context_precision | ≥0.7 |
| **DeepEval** | 任务完成率 | ≥0.9 |
| **DeepEval** | 工具调用正确率 | ≥0.95 |
| **DeepEval** | 幻觉率 | ≤0.1 |

### 13.3 测试报告

- 输出 `tests/REPORT.md` Markdown 格式详细测试报告
- 包含各层测试用例数、通过率、失败详情、评测门禁结果

### 13.4 e2e 必覆盖场景

- 提问 → 检索 → 工具调用 → 流式响应 → 会话持久化
- 打开测试页面 → 新建会话 → 发送提问 → 验证流式渲染 → 验证工具调用展示 → 切换会话验证隔离
- 携带 Bearer JWT Token 与不携带两种场景（验证身份解析与数据隔离）

---

## 十四、前端测试页面（AGENTS.md 第 14 章）

- 单文件 `static/index.html`，FastAPI StaticFiles 挂载到 `/`
- 原生 HTML + 原生 JS，禁 React/Vue/构建工具
- 配置注入：API BaseURL / 模型名 / 会话 ID / Bearer JWT Token
- 会话管理：新建 / 切换 / 清空
- 流式渲染：fetch + ReadableStream 解析 SSE，逐块追加
- 工具调用展示：折叠面板显示工具名+参数+结果
- 检索来源展示：折叠面板显示召回片段（source + score）
- 生产可通过 `ENABLE_TEST_PAGE=false` 关闭

---

## 十五、实施阶段划分

| 阶段 | 内容 | 状态 |
|---|---|---|
| **阶段 1：项目骨架** | 目录结构、AGENTS.md 合规配置、Docker 6 容器 compose、依赖锁定、packages/ 离线包占位、.env.template、Config SSOT、server.py 骨架、单元测试骨架 | 待启动 |
| **阶段 2：核心引擎** | LangGraph 图+节点+PostgresSaver、LiteLLM 网关、Qdrant 双 namespace、RAG 混合检索、6 类 trace、common 基础模块 | 待启动 |
| **阶段 3：研究流水线** | IndustryClassifier+GICS、ResearchConductor(Planner+并行 Researcher)、BrowserManager(中文优先搜索+爬取)、SourceCurator、ReportGenerator、Publisher(MD/HTML/PDF) | 待启动 |
| **阶段 4：扩展能力** | MCP 数据源、文件上传、68 行业专家提示词族、Skills 体系、Token 优化、前端测试页面 | 待启动 |
| **阶段 5：测试与门禁** | 5 层测试用例 + RAGAS + DeepEval + Markdown 测试报告 | 待启动 |

---

## 十六、合规性约束清单（AGENTS.md 硬约束）

| # | 约束 | 来源 |
|---|---|---|
| 1 | LangGraph StateGraph 为唯一编排范式，禁 AgentExecutor/手写 ReAct | 第 5 章 |
| 2 | State 必须为 TypedDict，跨节点共享字段用 Annotated[T, reducer] | 第 5 章 |
| 3 | 节点为纯函数，禁止原地修改入参 State，返回 delta dict | 第 5 章 |
| 4 | 生产 StateGraph 必须挂 PostgresSaver | 第 5 章 |
| 5 | 全部 LLM 调用经 LiteLLM，禁厂商 SDK 直连 | 第 9 章 |
| 6 | 所有外部工具通过 MCP Server 暴露，注册集中在 tools/registry.py | 第 9 章 |
| 7 | 统一使用 AgentInsight SDK，异步上下文管理器 trace_xxx，禁 @observe | 第 10 章 |
| 8 | 业务代码禁直接调用 opentelemetry-sdk 原生 API | 第 10 章 |
| 9 | 会话隔离键 thread_id 由请求上下文注入，禁客户端自造 | 第 6 章 |
| 10 | agent_id = agent_name，数据隔离键全局唯一 | 第 7 章 |
| 11 | Qdrant 单集合 agents，payload namespace 隔离（共享+用户私有双 namespace） | 第 7 章 |
| 12 | Redis 键必须加前缀 {agent_id}:{user_id}: | 第 7 章 |
| 13 | RAG 必须 BM25+向量混合，rerank 必经 bge-reranker-v2-m3 | 第 7 章 |
| 14 | JWT 验证在 API 入口中间件完成，禁业务节点内重复解析 | 第 8 章 |
| 15 | 密钥仅环境变量注入，禁入仓/硬编码/日志 | 第 11 章 |
| 16 | 禁 eval/exec 求值用户输入 | 第 11 章 |
| 17 | CORS 禁 *，安全响应头中间件不可绕过 | 第 11 章 |
| 18 | 6 容器 Docker Compose，离线部署，depends_on service_healthy | 第 12 章 |
| 19 | 5 层测试分层，CI 流水线顺序强制 | 第 13 章 |
| 20 | 前端测试页面单文件，原生 HTML+JS，禁框架 | 第 14 章 |

---

## 十七、待用户确认事项

请审阅以上方案，回复以下任意一项：

1. **「确认执行」** — 我立刻进入阶段 1 实施
2. **指出需要调整的项** — 如「国内搜索改用讯飞」「首版只做 basic_report」「PDF 改用 LibreOffice」等
3. **补充其他要求**

---

**文档结束**
