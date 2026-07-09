# AGENTS.md — LangGraph AI Agent 执行规则

> AI 助手在执行任何任务前应先读本文档。规则分两类：
> - **优先选择**：除非该架构/工具不适合当前场景，否则应优先采用本文档推荐方案。如需改用其他方案，应说明理由并等待用户确认。
> - **不推荐**：除非该架构/工具在当前场景具有明显优势，否则不应采用。如确需选用，应说明优势理由并等待用户确认。
> - 第 11 章安全合规红线为真正的硬约束（涉及密钥/PII/注入/传输安全），不可放松。

## 1. 项目定位与技术栈

**定位**：以 LangGraph 为编排内核、MCP 为工具协议、AgentInsight SDK 为可观测底座的企业级 AI Agent 系统，对外暴露 OpenAI 兼容 API（SSE 流式）。
**能力边界**：仅承载对话/检索/工具调用三类智能体能力；不承担模型训练、数据标注、前端 SSR；多 Agent 协作优先采用子图/Supervisor/Swarm 三模式。

| 类别 | 选型 / 版本 | 选型理由（一句话） | 替代方案（不推荐原因） |
|------|------------|-------------------|---------------------|
| 语言 | Python ≥3.11 | 异步生态成熟，类型完备 | Go/Node（生态不及） |
| 编排内核 | LangGraph ≥1.2 | 状态机+条件边+Checkpointer，生产首选 | AutoGen/CrewAI（控制流不透明，不推荐） |
| LLM 抽象 | langchain-core ≥1.4 | 仅核心类型，不推荐 Chain/AgentExecutor | langchain 全家桶（耦合高，不推荐） |
| 模型网关 | LiteLLM ≥1.6 | 一次接入 100+ 模型，内置成本/限流/重试 | 厂商 SDK 直连（不推荐，无统一治理） |
| 工具协议 | MCP | 2026 事实标准，复用生态 | 自定义委派协议（不推荐，重复造轮） |
| 向量库 | Qdrant ≥1.18 | API 优雅、过滤强、部署简单 | Pinecone（不推荐，闭源）/Milvus（运维重） |
| 关系库 | PostgreSQL ≥16 | Checkpointer+业务元数据，分布式共享 | MySQL（不推荐，分布式弱） |
| 缓存 | Redis ≥8.0 | 热点缓存+限流+短期会话 | Memcached（不推荐，无持久化） |
| Embeddings | bge-base-zh-v1.5 | 中文最强开源嵌入，本地零成本 | OpenAI embedding（不推荐，数据出境） |
| Rerank | bge-reranker-v2-m3 | 中文 Rerank SOTA，本地部署 | Cohere Rerank（不推荐，闭源收费） |
| BM25 | rank-bm25+jieba | 中文分词+IDF，混合检索必备 | 字符 2-gram（降级兜底，非首选） |
| Web 框架 | FastAPI+Uvicorn ≥0.115 | 异步原生、OpenAPI 自动、SSE 流式 | Flask/Django（不推荐，异步弱/过重） |
| 数据校验 | Pydantic ≥2.10 | SSOT 配置+入出参校验 | dataclass（不推荐，校验弱） |
| 可观测性 | AgentInsight Python SDK ≥0.1.5 | 自家产品，异步上下文管理器，6 类 span | LangSmith（不推荐，数据出境）/OTel 手动埋点（不推荐，重复劳动）/Langfuse（不推荐，已由 AgentInsight 替代） |
| 评测 | RAGAS+DeepEval ≥0.2/≥2.0 | RAG 质量+Agent 行为双轨门禁 | 仅人工测试（不推荐，不可 CI 化） |
| 部署 | Docker Compose | 单机多容器编排，离线包支持 | 裸机（不推荐，无法弹性扩缩）/k8s（暂不采用） |

> 技术栈变更建议经架构师评审。如需改用"不推荐"方案，应说明当前场景的优势理由并等待用户确认。

## 2. 核心命令速查

```bash
# 首次准备（按序执行）
python -m venv .venv && .venv\Scripts\Activate.ps1
pip install -U pip -r requirements.txt
copy .env.template .env   # 填入 LLM/Qdrant/Embeddings/AgentInsight 凭据

# 启动 / 测试 / 质量门禁
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
pytest tests/ -q && ruff check . && ruff format --check . && mypy src/ --strict

# 评测（CI 门禁）
python -m evals.rag.run --dataset evals/rag/dataset.json
python -m evals.agent.run --dataset evals/agent/dataset.json

# 容器化起栈
docker compose build && docker compose up -d
```

## 3. 目录结构与架构边界

```
src/
├── graph/         # LangGraph 图定义（state/nodes/edges/builder）
├── agents/        # 具体 Agent 实现（复用图）
│   └── <agent_name>/  # 子智能体专属代码（如有子智能体，按名称建子目录）
├── common/        # 公用基础模块（不应依赖 agents/ 或业务模块）
├── config/        # 配置文件（全局 Settings + 子智能体专属配置）
│   ├── settings.py    # 全局 pydantic-settings Settings SSOT
│   └── <agent_name>/  # 子智能体专属配置（如有子智能体，按名称建子目录）
├── skills/        # 技能定义
│   └── <agent_name>/  # 子智能体专属技能（如有子智能体，按名称建子目录）
├── tools/         # MCP Server 封装（registry 待多 Agent 落地后引入）
├── rag/           # 自研 RAG 层（retriever/reranker/embeddings/bm25）
├── llm/           # LiteLLM 网关封装
├── memory/        # Postgres Checkpointer 配置
├── observability/ # AgentInsight SDK 封装（tracing.py，6 类 trace_xxx）
└── api/           # FastAPI 路由 + middleware
```

**架构边界（核心约定，优先选择）**：
- `graph/` 是首选编排入口；`agents/` 复用图，不推荐自建编排循环。
- `tools/`、`rag/`、`llm/`、`memory/` 不推荐互相 import，共享逻辑下沉到 `common/`。
- 依赖单向向内：`common/` 不应依赖 `agents/` 或业务模块。
- **临时文件管理（核心约定，优先选择）**：所有临时文件（含临时测试文件/脚本/代码/日志/验证产物等）应放入 `temp/` 目录，不推荐在项目根目录或 `tests/` 正式分层下放置临时文件；`temp/` 目录已加入 `.gitignore` 不入仓。正式测试用例应放在 `tests/` 对应分层（`unit/`/`functional/`/`api/`/`regression/`/`e2e/`），手动调试脚本放在 `tests/manual/`。
- 配置应经 `config/` + 环境变量，业务代码不应硬编码 URL/密钥（硬编码密钥属第 11 章硬约束）。
- 子智能体代码应按名称隔离在 `agents/<agent_name>/`、`config/<agent_name>/`、`skills/<agent_name>/` 下，不推荐跨子智能体直接引用；共享能力下沉到 `common/` 或 `skills/` 顶层。
- 新增顶层目录建议经架构师评审。

## 4. 三级行为边界与不推荐清单

| 级别 | 触发场景（可枚举可校验） |
|------|------------------------|
| ✅ Always | 读/写 docs·tests·evals；跑 ruff+mypy+pytest；跑 RAGAS+DeepEval；修 P2 及以下 bug；续接被截断输出；删项目内文件 |
| ⚠️ Ask first | LangGraph 图结构/State schema 变更；RAG 核心算法（RRF/Rerank/Embeddings）切换；密钥轮换；外部系统对接；连续 3 次修复失败；评测门禁不达标；**选用"不推荐"方案时** |
| ❌ Never | 见不推荐清单；安全合规红线（第 11 章）属真正硬约束，违例即阻断合并并提 P0 |

**不推荐清单（CI 机器校验 import 黑名单，选用需说明理由并等待用户确认）**：
1. `langchain` 全家桶（仅 `langchain-core` 推荐使用）
2. `AgentExecutor`（推荐用 LangGraph StateGraph）
3. AutoGen / CrewAI（重复多 Agent 编排框架，不推荐）
4. LlamaIndex（重复 RAG/编排框架，不推荐）
5. LangSmith（专有 SaaS，数据出境合规风险，不推荐）/Langfuse（已由 AgentInsight SDK 替代，不推荐）
6. Pinecone 等闭源托管向量库（推荐用 Qdrant）
7. 直接调用厂商 SDK（推荐经 LiteLLM）
8. Memcached（推荐用 Redis）
9. MySQL（推荐用 PostgreSQL）
10. `pickle` 持久化 Agent 状态（推荐用 Postgres Checkpointer）
11. 全局可变状态（推荐节点纯函数，状态从 state/deps 获取）
12. `eval`/`exec` 求值用户输入（注入风险，属第 11 章安全硬约束）
13. 直接使用 `opentelemetry-sdk` 原生 API（推荐经 `observability/tracing.py` 封装的 6 类 `trace_xxx`）
14. `agentinsight.observe` 装饰器（已弃用，无法记录 `model`/`usage_details`/`cost_details`，不推荐）
15. 观察者模式实现追踪（不推荐 Subject/Observer、attach/notify 机制；追踪统一用异步上下文管理器）

> 以上清单为"不推荐"而非"绝对禁止"。如某项在当前场景具有明显优势（如 AutoGen 在特定多 Agent 协作场景更合适），可说明理由并经用户确认后选用，但应在 PR 中标注偏差说明。

## 5. Agent 编排核心规则

LangGraph ≥1.2 状态机为**优先选择的编排范式**；不推荐 AgentExecutor / 手写 ReAct 循环。如需改用其他编排框架（如 AutoGen/CrewAI），应说明优势理由并等待用户确认。

**State**：应为 `TypedDict`；跨节点共享字段用 `Annotated[T, reducer]` 声明（消息流用 `add_messages`）。节点不应原地修改入参 State，应返回 delta dict 由 reducer 合并。不推荐全局可变状态；会话级数据走 Checkpoint。

**Node**：节点为纯函数 `async def node(state: State) -> dict`，单一职责、无副作用。节点内不推荐直连厂商 LLM SDK，统一走 `llm/` 网关（LiteLLM）。每个节点应包裹在 AgentInsight trace span 内（见第 10 章）。

**Edge**：路由应显式 `add_conditional_edges(src, router_fn, mapping)`，不推荐隐式跳转。每个图应有终止节点；`max_iterations` 为硬上限，由节点计数器 + 条件边强制，不可软超时。

**Checkpoint**：生产 `StateGraph` 应挂 `PostgresSaver`（PostgreSQL ≥16）；内存 Checkpoint 仅 `ENV=dev` 允许。`thread_id` 从请求上下文注入做会话隔离键，不推荐客户端自造。

## 6. 会话与上下文管理

**多会话支持（核心约定，优先选择）**：
- 会话隔离键为 `thread_id`（即 `session_id`），由请求上下文注入，不推荐客户端自造。
- 每个 Agent 应支持并发多会话；会话间状态通过 Postgres Checkpointer 隔离，不推荐共享可变内存。
- 会话级数据（消息历史/中间状态/缓存）应按 `agent_id` + `user_id` + `session_id` 三级分键存储，不推荐全局共享；`agent_id = agent_name`（见第 7 章），`user_id` 由第 8 章身份解析获得。

**上下文窗口（核心约定，优先选择）**：
- 单会话上下文上限 `CONTEXT_MAX_CHARS = 800_000`（约 200K token）。
- 写入会话前应调用 `compress_if_needed()` 检查阈值；超限应压缩，不推荐直接丢弃。
- 上下文压缩策略：滑动窗口 + LLM 摘要，保留最近 25% 消息为原文，其余摘要化。
- 压缩应异步后台执行（`asyncio.create_task`），不阻塞用户响应。

**会话生命周期（核心约定，优先选择）**：
- 会话持久化到 Postgres Checkpointer；内存 Checkpointer 仅 `ENV=dev` 允许。
- 会话 TTL 默认 30 天（`CONTEXT_SESSION_TTL=2592000`），过期会话由定时任务清理。
- 会话删除应级联清理：Checkpoint + Redis 缓存 + 业务元数据。
- 写入防抖 `DEBOUNCE_SECONDS = 1.0`，后台 flush 线程 `FLUSH_INTERVAL_SECONDS = 0.5`。

**启动时数据初始化（核心约定，优先选择）**：
- Agent 容器启动时（`server.py` lifespan）应执行 PostgreSQL 业务表初始化，失败不阻断启动（仅告警，`depends_on: service_healthy` 已保证依赖就绪）：
  - **PostgreSQL 业务表初始化**：`src/memory/db_initializer.py` 的 `init_database()` 读取 `scripts/init.sql` 并执行；所有 DDL 使用 `CREATE TABLE/INDEX IF NOT EXISTS`，天然幂等，支持重复启动；表结构变更需追加 `ALTER TABLE IF EXISTS ... ADD COLUMN IF NOT EXISTS ...`（PostgreSQL 9.6+）；触发器/函数使用 `CREATE OR REPLACE FUNCTION` + `CREATE OR REPLACE TRIGGER`（PostgreSQL 14+,项目要求 ≥17 满足）保证幂等。不推荐在 Docker 构建时通过 `Dockerfile.postgres` 内嵌 `init.sql` 执行 DDL，统一由 Agent 启动时触发。
- 行业适配采用 GPTR 风格 4 层机制（见第 5 章），不再 bootstrap GICS 行业知识库。

## 7. 数据隔离与检索核心规则

**多 Agent 数据隔离总则（核心约定，优先选择）**：
- 每个 Agent 的数据隔离键为 `agent_id = agent_name`（Agent 名称），全局唯一。
- 所有持久化层（Qdrant/Postgres/Redis）应以 `agent_id` 区分各 Agent；用户私有数据（Postgres 业务表/Redis 缓存/Qdrant 用户导入数据）进一步按 `user_id` 区分，不推荐跨用户共享；Qdrant 共享知识库仅按 `agent_id` 区分，不与 `user_id` 挂钩。
- Agent 注册时应声明 `agent_name`，由配置注入，不推荐运行时硬编码。

**PostgreSQL 约定**：单一数据库 `agents`（供多 Agent 共享）。LangGraph Checkpointer 表由官方管理（`thread_id` 已含会话隔离）。业务表应含 `agent_id` + `user_id` 双列（VARCHAR，建复合索引）区分各 Agent 各用户数据；查询应显式 `WHERE agent_id = ... AND user_id = ...`，不推荐无过滤的全表扫描。表名复数 snake_case（如 `sessions`、`messages`），不推荐按 Agent 或用户拆表。

**业务表时间戳字段约定（核心约定，优先选择）**：
- 所有业务表应含 `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`；状态会变更的表（如 `research_sessions`/`research_reports`/`uploaded_files`/`mcp_configs`）还应含 `updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`，并通过 `BEFORE UPDATE` 触发器（`CREATE OR REPLACE FUNCTION update_updated_at_column()` + `CREATE OR REPLACE TRIGGER`）自动维护，不推荐业务代码手动赋值。
- 纯日志表（如 `research_search_logs`/`token_usage_logs`）只需 `created_at`，不需要 `updated_at`（INSERT-only 设计）。
- `agent_id`/`user_id`/`session_id` 三列在所有业务表中应保持相同长度（推荐统一 VARCHAR(64)），避免跨表 JOIN 时类型/长度不一致引发隐式转换。

**Qdrant 集合约定**：单一集合 `agents`，`distance=Cosine`，`vector_size=768`（bge-base-zh-v1.5 固定维度）。按 payload `namespace` 字段隔离数据，分两类：
- **共享知识库**（非用户导入数据）：`namespace = agent_id`（即 `agent_name`），payload 不含 `user_id`，所有用户共享，检索时默认召回。
- **用户私有数据**（用户导入数据）：`namespace = {agent_id}:{user_id}`（即 `{agent_name}:{user_id}`），payload 含 `user_id` 字段，仅该用户可检索，不推荐跨用户召回。

点 id 用 `uuid5(NAMESPACE_DNS, f"{namespace}:{content_hash}")` 幂等生成。写入 payload 应含 `content`+`metadata`+`namespace` 三键（用户私有数据额外含 `user_id`）。检索时应显式传目标 namespace 列表（共享 + 当前用户私有），不推荐无 namespace 过滤的全集合扫描。Qdrant 服务端通过环境变量 `QDRANT__SERVICE__STATIC_API_KEY` 开启静态 API Key 鉴权，客户端（`rag/qdrant_manager.py`）应通过 `qdrant_api_key` 配置传递 API Key；API Key 仅在 `.env`/`.env.qa` 配置，不推荐硬编码（硬编码属第 11 章硬约束）。

**Redis 约定**：所有键应加前缀 `{agent_id}:{user_id}:`，完整键格式 `{agent_id}:{user_id}:{module}:{type}:{id}`。不推荐使用无 `agent_id` 或 `user_id` 前缀的裸键。会话级数据按 `{agent_id}:{user_id}:{session_id}` 三级分键。应设 TTL，不推荐永久键（配置数据除外）。

**RAG 流水线**：检索应混合 BM25 + 向量（bge-base-zh-v1.5），默认 `vector_weight=0.7 / bm25_weight=0.3`。重排序默认不启用；当 `rerank_enabled=True` 时，重排序经 `bge-reranker-v2-m3`，Top-K 召回后 rerank，不推荐直接用向量分数作最终排序。`score_threshold` 默认 0.3，低于阈值丢弃（仅当 rerank 启用时生效，RRF 融合分数不应用此阈值）。Embedding 调用统一走 `rag/embeddings.py`，不推荐业务代码直连 API。Qdrant 不可用时降级内存检索仅限 `ENV=dev`；生产应告警并失败转移。Embeddings/Rerank TEI 服务通过环境变量 `API_KEY` 开启鉴权，客户端（`rag/embeddings.py`/`rag/retriever.py`）应通过 `embeddings_api_key`/`rerank_api_key` 配置传递 `Authorization: Bearer <key>` 请求头；API Key 仅在 `.env`/`.env.qa` 配置，不推荐硬编码。

**行业适配 GPTR 4 层机制（核心约定，优先选择，对标 GPT Researcher）**：
行业适配刻意不引入 `IndustrySkill`，用 4 层隐形机制替代，不推荐业务代码 if-else 行业分支：
1. **Prompt 层**：`src/skills/researcher/agent_creator.py` 的 `AgentCreator.AUTO_AGENT_INSTRUCTIONS` 给 LLM few-shot 例子，让 LLM 运行时自主生成行业 persona（对标 GPTR `auto_agent_instructions()` + `choose_agent()`），无 if-else 行业分支。
2. **Config 层**：`Settings.agent_role` / `ChatRequest.agent_role` 可注入任意行业 persona 字符串（对标 GPTR `AGENT_ROLE` 配置），优先级高于 LLM 自动生成。
3. **Retriever 层**：`src/skills/researcher/searchers/` 下含 `arxiv`/`pubmed`/`semantic_scholar` 等专业数据源（对标 GPTR 20 个 retriever），可按区域路由组合。
4. **MCP 层**：`MCP_SERVERS` 注册行业专用工具服务器，`mcp_coordinator.py` 让 LLM 自动选工具（对标 GPTR `MCPToolSelector`）。

不推荐：新增 `IndustryClassifier` / `industry_prompts/*.yaml` / `knowledge_bootstrap.py` 等基于行业分类器的实现；节点内 `if industry == "xxx"` 分支；硬编码行业 prompt 字典。如确需分类器方案，应说明理由并等待用户确认。

## 8. 用户身份解析规则

**Bearer JWT Token 处理（核心约定，优先选择）**：
- Agent 接受请求头 `Authorization: Bearer <jwt_token>`；token 可选，不存在时走匿名用户路径。
- token 存在时：应同步调用 `GET https://agentinsight.goldebridge.com/api/user`（携带原 `Authorization` 头）获取 `user_id`；调用失败按无 token 处理并告警。
- token 不存在或调用失败时：使用环境变量 `DEFAULT_USER_ID` 作为 `user_id`；`DEFAULT_USER_ID` 应在 `.env` 配置，不推荐硬编码（硬编码属第 11 章硬约束）。
- 解析得到的 `user_id` 应注入请求上下文，供后续节点、会话、数据持久化使用（见第 6/7 章）。

**安全约束**：
- JWT 验证与 `user_id` 获取应在 API 入口中间件完成，不推荐在业务节点内重复解析。
- `user_id` 获取 API 调用应设超时（默认 5s），超时降级 `DEFAULT_USER_ID` 并告警。
- 禁止将原始 JWT token 写入日志或持久化存储；仅保留解析后的 `user_id`（属第 11 章安全硬约束）。

## 9. 工具与模型网关规则

**工具（MCP）**：外部工具优先通过 MCP Server 暴露；节点内不推荐定义 ad-hoc tool function。MCP 工具配置存储在 PostgreSQL `mcp_configs` 表（按 `agent_id` + `user_id` 隔离，见第 7 章），运行时由 `src/skills/researcher/mcp_coordinator.py` 加载用户启用配置并经 LLM 智能选工具；多 Agent 落地后再引入 `tools/registry.py` 集中授权。工具调用应经 AgentInsight `trace_tool` span 包裹（见第 10 章），参数与结果入 span。敏感工具（写文件/执行命令）应显式声明权限，由中间件校验。

**模型网关（LiteLLM）**：LLM 调用优先经 `llm/` 的 `LLMClient`（底层 LiteLLM ≥1.6）；不推荐直接 `openai`/`anthropic` 等 SDK（如需直连应说明理由并等待用户确认）。模型名以 LiteLLM 路由前缀声明（如 `deepseek/deepseek-chat`），由配置注入，不推荐硬编码。流式统一 `achat_stream`；同步 `chat` 仅用于非交互式批处理。

## 10. 可观测性与评测规则

**可观测性内核（核心约定，优先选择）**：
- 优先使用 **AgentInsight Python SDK**（pip 名 `agentinsight-sdk`，导入名 `agentinsight`，版本 ≥0.1.5），底层依赖 OpenTelemetry SDK，由 `observability/tracing.py` 统一封装。如需改用其他可观测后端（如 Langfuse/LangSmith），应说明理由并等待用户确认。
- **追踪调用方式优先**：异步上下文管理器 `async with trace_xxx(...) as span`；**不推荐观察者模式**（无 Subject/Observer、无 attach/notify）；`@agentinsight.observe` 装饰器已弃用。
- SDK 初始化在 `observability/tracing.py` 模块导入时一次性完成，参数从 `config.py` 注入：`AGENTINSIGHT_PUBLIC_KEY`/`AGENTINSIGHT_SECRET_KEY`/`AGENTINSIGHT_HOST`；生产环境应校验密钥存在。
- **降级策略**：SDK 初始化失败或运行时异常时，所有 `trace_xxx` yield `_NoopSpan`（Null Object 模式），业务代码不推荐判断 SDK 是否可用，`span.update()` 调用永远安全。
- 业务代码不推荐直接调用 `agentinsight.init()`/`agentinsight.get_client()`/`client.flush()`；统一经 `observability/tracing.py` 封装的 6 类 `trace_xxx`。
- 业务代码不推荐直接使用 `opentelemetry-sdk` 原生 API。

**6 类 trace span（签名与必带字段）**：

| trace 类型 | as_type | 必带字段 | 包裹位置 |
|---|---|---|---|
| `trace_agent` | `agent` | `name`/`input`/`metadata`(含 `session_id`/`intent`)/`session_id`/`user_id` | 编排器入口，包裹 `graph.ainvoke()`，建立根 span |
| `trace_generation` | `generation` | `name`/`model`/`model_parameters`/`usage_details`/`cost_details` | `llm/` 网关层（`LLMClient.achat`/`achat_stream`），业务节点层**不重复包裹** |
| `trace_tool` | `tool` | `name`/`input`/`output`(span.update)/`metadata`(含 `tool_name`/`success`) | MCP 工具调用节点 |
| `trace_retriever` | `retriever` | `name`/`input`/`output`/`metadata`(含 `matched`/`candidate_count`/`retriever_type`/`top_score`) | RAG 检索节点（BM25/Vector/Qdrant search） |
| `trace_chain` | `chain` | `name`/`input`/`output` | 多步骤链式调用（RAG 管道、子图编排） |
| `trace_embedding` | `embedding` | `name`/`model`/`usage_details`(含 `token_count`) | `rag/embeddings.py`，高频调用启用 head-based 采样 |

**跨节点 span 传播（核心约定，优先选择）**：
- span 父子关系由 OpenTelemetry Context API 在同一 asyncio task 内**自动传播**，不推荐手动传递 span 对象（破坏自动传播语义）。
- 编排器应用 `trace_agent` 包裹 `graph.ainvoke()` 作为根 span；LangGraph 节点内创建的子 span 自动关联到根 span。
- 认证上下文（token/user_id 等）不应用 span 上下文传递（属安全硬约束，见第 11 章）；认证信息应通过 `contextvars` + State 字段在节点入口显式恢复。
- `span.update(output=...)` 可在 `with` 块内多次增量调用；`trace_id`/`id` 为只读属性。

**采样策略**：
- `trace_agent`/`trace_generation`/`trace_tool`/`trace_retriever`/`trace_chain`：全量 1.0 采样。
- `trace_embedding`：head-based 采样，默认 `tracing_embedding_sample_rate=0.5`（高频 embed 调用降采样减存储压力）。
- SDK 底层用 `BatchSpanProcessor` 后台线程批量导出，HTTP 上报失败不阻塞主流程；业务代码不推荐调用 `client.flush()` 阻塞事件循环。

**评测门禁（CI 强制，不达标不推荐合并 main）**：
- RAGAS：faithfulness ≥0.8 / answer_relevancy ≥0.8 / context_precision ≥0.7
- DeepEval：任务完成率 ≥0.9 / 工具调用正确率 ≥0.95 / 幻觉率 ≤0.1

## 11. 安全合规红线（真正硬约束，不可放松）

> 本章为真正的硬约束，涉及密钥/PII/注入/传输安全等法律与合规底线，与第 1-10/12-14 章的"优先选择/不推荐"架构偏好不同，不可放松。任何偏差需经安全评审并经用户显式确认。

- **密钥**：仅环境变量注入，禁止入仓/硬编码/日志；API Key SHA256+BCrypt 双哈希，仅创建时返回一次；密码 BCrypt(cost=12)；发现硬编码密钥即 P0 暂停并人工介入
- **PII**：用户会话内容加密存储+日志脱敏；API 响应禁止返回密码/密钥原文；最小化收集，按用途设保留期
- **Prompt Injection**：所有外部输入经 Pydantic 校验；工具调用权限隔离（`read`/`write`/`execute`/`network` 显式授权）；禁止 `eval`/`exec` 求值用户输入；LLM 输出经结构化校验后再入工具
- **传输与边界**：生产强制 HTTPS；安全响应头中间件（nosniff/DENY/HSTS）不可绕过；生产关闭 Debug
- **Agent 操作约束（文件修改硬约束）**：禁止 Agent 通过 PowerShell（含 `Set-Content`/`Add-Content`/`Out-File`/`echo >`/`>>` 重定向/`[System.IO.File]::WriteAllText` 等 PS 原生写文件命令）修改任何项目文件；文件读取/创建/编辑/删除统一使用专用工具（Read/Write/Edit/DeleteFile/Glob/Grep）。理由：(1) 专用工具走审计链路，可追溯可回滚；(2) PS 写文件绕过权限校验与编码约定（易引入 BOM/CRLF 问题）；(3) 与 Trae IDE 工具规范一致。违例即阻断合并并提 P0。仅允许在 RunCommand 中使用 PowerShell 执行与文件修改无关的命令（如 `git`/`docker`/`python -m pytest`/`docker compose` 等系统命令）。
- **CORS（不推荐 `*`，但不禁；属架构偏好非硬约束）**：`CORS_ALLOW_ORIGINS=*` 不推荐用于生产（暴露面过大，违反最小权限原则），但并非第 11 章硬约束；生产推荐配置具体域名列表（如 `https://your-domain.com,http://localhost:8066`），开发/QA 环境可酌情放宽；如确需 `*`（如开源社区快速起栈），应说明理由并经用户确认

## 12. 部署规则

**容器清单（生产联网模式 6 个独立容器含本地 PostgreSQL；QA 模式 6 个独立容器含本地 PostgreSQL；生产离线模式 5 个独立容器 + 外部 PostgreSQL）**：

> 生产联网模式 PostgreSQL 由本地 `postgres` 容器提供（见 `docker-compose.yml`），与 redis/qdrant/embeddings 同属 compose 编排；业务表由 Agent 启动时执行 `scripts/init.sql` 创建（幂等）。
> QA 模式（离线）保留本地 `postgres` 容器（见 `docker-compose-qa.yaml`），与 redis/qdrant/embeddings 同属 compose 编排，便于无外部数据库的离线测试环境。
> 生产离线模式 PostgreSQL 由外部托管服务提供（env 配置 `POSTGRES_HOST`/`PORT`/`USER`/`PASSWORD`/`DB`），不在 compose 内构建；业务表由 Agent 启动时执行 `scripts/init.sql` 创建（幂等）。

| 服务 | 镜像/构建 | 端口 | 健康检查 |
|------|----------|------|---------|
| `agent` | 本仓 `Dockerfile`（Python 3.12-slim） | 8066 | `GET /health` |
| `embeddings` | BGE 服务镜像（bge-base-zh-v1.5） | 8088 | `GET /health` |
| `rerank`（可选，`rerank_enabled=True` 时启用） | BGE 服务镜像（bge-reranker-v2-m3） | 8089 | `GET /health` |
| `qdrant` | `qdrant/qdrant:≥1.18` | 6333/6334 | `/healthz` |
| `redis` | `redis:≥8` | 6379 | `redis-cli ping` |
| `postgres`（生产联网模式 + QA 模式） | `postgres:≥17`（业务表由 Agent 启动时执行 `scripts/init.sql` 创建） | 5432 | `pg_isready -U <user>` |

**APIKey 鉴权（核心约定，优先选择；密钥硬编码属第 11 章硬约束）**：
- Qdrant 通过 `QDRANT__SERVICE__STATIC_API_KEY` 环境变量开启静态 API Key 鉴权；客户端通过 `QDRANT_API_KEY` 传递。
- Embeddings/Rerank TEI 服务通过 `API_KEY` 环境变量开启鉴权；客户端通过 `EMBEDDINGS_API_KEY`/`RERANK_API_KEY` 传递 `Authorization: Bearer <key>` 请求头。
- 所有 API Key 仅在 `.env`/`.env.qa` 配置，禁止硬编码（属第 11 章安全硬约束）；compose 文件通过 `${VAR:-}` 插值引用。

**QA 部署核心约定（优先选择，参考 AgentInsightService 模式）**：
- 所有镜像应预下载为 tarball（`docker save -o packages/images/<image>.tar`），部署机 `docker load` 导入，不推荐部署时 `docker pull`。
- Python 依赖应预下载 wheel 到 `packages/`，构建时 `pip install --no-index --find-links=/app/packages -r requirements.txt`，不推荐联网安装。
- 系统依赖（.deb）应预下载到 `packages/debs/`，构建时 `dpkg -i /tmp/debs/*.deb`，不推荐 `apt-get update`。
- BGE 模型权重应预下载到 `packages/models/`（`bge-base-zh-v1.5`/`bge-reranker-v2-m3`），不推荐运行时从 HuggingFace 下载。embeddings/rerank 容器（第三方 TEI 镜像）通过 bind mount 挂载到容器内 `/data/<model_name>:ro`（只读），compose 文件中与命名卷 `embeddings_models`/`rerank_models`（可写 HF cache）并列配置。
- 环境变量推荐 `LANG=C.UTF-8` / `PYTHONIOENCODING=utf-8`。

**容器编排核心约定（优先选择）**：
- `restart: always`（生产）/ `unless-stopped`（开发）。
- `depends_on` 应用 `condition: service_healthy`，不推荐裸依赖（无健康检查直连）。
- 依赖顺序：生产联网模式 `postgres` → `redis` → `qdrant` → `embeddings` → `agent`；QA 模式 `postgres` → `redis` → `qdrant` → `embeddings` → `agent`；生产离线模式 `redis` → `qdrant` → `embeddings` → `agent`（PostgreSQL 由外部托管，不在 `depends_on` 内）；`rerank` 为可选容器（`rerank_enabled=True` 时通过 `profiles: [rerank]` 启用，插入 `embeddings` 与 `agent` 之间，`agent` 不强制依赖 `rerank`）。
- 健康检查 `interval ≤ 30s` / `timeout ≤ 10s` / `retries ≥ 3` / `start_period ≥ 10s`。
- 数据卷应用 `driver: local`，命名卷 `redis_data` / `qdrant_data` / `session_data` / `embeddings_models` / `rerank_models` / `uploads_data`（生产联网模式与 QA 模式额外含 `postgres_data`）。
- 端口绑定：生产仅 `agent:8066`/`rerank:8089`/`embeddings:8088`/`qdrant:6333` 对外暴露，其余（`postgres:5432`/`redis:6379`/`qdrant:6334` gRPC）绑定 `127.0.0.1`。

**Agent 容器核心约定（优先选择）**：
- 基础镜像 `python:3.12-slim`，非 root 用户运行。
- 多阶段构建：builder 阶段装依赖，runtime 阶段仅复制产物。
- `EXPOSE 8066`，`CMD ["python", "server.py"]`。
- env_file 分层：生产 `.env`（公共）+ `.env.agent`（Agent 专属）+ `.env.{env}`（环境覆盖）；QA `.env.qa`（QA 专属，全离线配置）；生产离线 `.env`（与生产联网共享配置）。

**三套构建模式**：
项目提供三套构建文件，按部署场景选择：

| 模式 | 构建文件 | 编排文件 | 环境文件 | 构建脚本 | 适用场景 |
|------|---------|---------|---------|---------|---------|
| QA 模式（离线） | `Dockerfile.qa` | `docker-compose-qa.yaml` | `.env.qa` | `docker-build.qa.bat` | QA 测试、内网环境 |
| 生产模式（联网） | `Dockerfile` | `docker-compose.yml` | `.env` | `docker-build.sh` | 开源社区、CI、外网环境 |
| 生产模式（离线） | `Dockerfile.offline` | `docker-compose-offline.yaml` | `.env` | `docker-build.offline.sh` | 内网生产环境、离线部署 |

- **QA 模式（离线）**：所有文件宿主机预下载到 `packages/`（wheels/debs/models/images），构建时 `pip install --no-index` 离线安装，部署时 `docker load` 加载镜像 tarball，模型从本地 volume 加载。适用于 QA 测试。所有端口绑定 `127.0.0.1`，仅本机访问。
- **生产模式（联网）**：构建时从 PyPI 下载 Python 依赖、从 Docker Hub 拉取基础镜像（含 `postgres` 容器），无需预下载 `packages/`。适用于开源社区贡献者快速起栈。仅 `agent:8066`/`rerank:8089`/`embeddings:8088`/`qdrant:6333` 对外暴露，其余（`postgres:5432`/`redis:6379`/`qdrant:6334` gRPC）绑定 `127.0.0.1`。
- **生产模式（离线）**：所有文件宿主机预下载到 `packages/`（wheels/debs/models/images），构建时 `pip install --no-index` 离线安装，部署时 `docker load` 加载镜像 tarball，模型从本地 volume 加载。镜像版本由 `packages/images/` 中的 tarball 决定，compose 文件中硬编码以确保匹配。适用于内网生产环境或离线部署。仅 `agent:8066`/`rerank:8089`/`embeddings:8088`/`qdrant:6333` 对外暴露，其余（`postgres:5432`/`redis:6379`/`qdrant:6334` gRPC）绑定 `127.0.0.1`。
- QA 模式相关文件已加入 `.gitignore`（不入仓）：`Dockerfile.qa`、`docker-compose-qa.yaml`、`docker-build.qa.bat`、`packages/wheels/`、`packages/debs/`、`packages/models/`、`packages/images/`。
- 生产离线模式相关文件已加入 `.gitignore`（不入仓）：`Dockerfile.offline`、`docker-compose-offline.yaml`、`docker-build.offline.sh`。
- "不推荐部署时联网拉镜像/装依赖/下模型" 约束适用于**QA 模式**和**生产离线模式**；生产联网模式允许构建时联网。

**不推荐（选用需说明理由并等待用户确认）**：
- QA 模式和生产离线模式部署时联网拉镜像/装依赖/下模型（破坏离线部署前提）。
- 单容器混装多服务（如 agent + qdrant 同容器，破坏单一职责与可独立扩缩容）。
- 绕过 `depends_on: service_healthy` 直接连未就绪服务（破坏依赖顺序保证）。
- 使用 `latest` 标签（应锁版本，避免镜像漂移）。
- 生产环境映射非必要端口到 0.0.0.0（破坏最小暴露原则）。

**容器命名约定（核心约定，优先选择）**：
- 容器编排应使用 `-p agentinsight` 项目名（即 `docker compose -p agentinsight -f <compose-file> --env-file <env-file> up -d`），不推荐使用默认项目名 `agentinsight-researcher`（即直接 `docker compose up -d`）。
- 三套构建脚本（`docker-build.qa.bat` / `docker-build.sh` / `docker-build.offline.sh`）均已内置 `-p agentinsight`，应优先使用脚本而非裸 `docker compose up -d`。
- **部署务必使用脚本而非裸 `docker compose` 命令（核心约定，优先选择）**：QA 环境必须使用 `docker-build.qa.bat` 构建/更新容器，不推荐新建其他构建脚本；生产联网模式用 `docker-build.sh`；生产离线模式用 `docker-build.offline.sh`。如需调整构建参数，应修改现有脚本而非新建。
- 理由：与 AgentInsightService 项目共享 `agentinsight` 命名空间，容器名统一为 `agentinsight-<service>-1`（如 `agentinsight-agent-1`），不携带 `-researcher` 后缀；两个项目不并行运行，通过停止一方容器释放端口后再启动另一方。
- 端口冲突处理：AgentInsightService 与本项目共享 8066 端口，切换项目时应先 `docker compose -p agentinsight down` 停止一方，再启动另一方，不推荐同时运行。

## 13. 测试规则

**测试分层（按执行环境）**：

| 类型 | 执行环境 | 目录 | 触发时机 |
|------|---------|------|---------|
| 单元测试 | 本地 / 构建期 | `tests/unit/` | 每次 commit、Docker build 阶段 |
| 功能测试 | 部署后容器栈 | `tests/functional/` | 容器栈健康后 |
| 回归测试 | 部署后容器栈 | `tests/regression/` | 合并 main 前 |
| API 测试 | 部署后容器栈 | `tests/api/` | 容器栈健康后 |
| 端到端测试 | 部署后容器栈 | `tests/e2e/` | 合并 main 前、发布前 |

**核心约定（优先选择）**：
- 单元测试在构建期执行（Docker build 或 CI build job），不应依赖外部服务（Postgres/Qdrant/Redis/LLM）。
- 功能/回归/API/e2e 测试应在 `docker compose up -d` 且全部容器 `service_healthy` 后执行，不推荐本地直连或 mock 绕过。
- 测试目标地址从环境变量 `AGENT_URL` 注入（默认 `http://agent:8066`），不推荐硬编码。
- 测试用例应独立可重复运行，不应依赖执行顺序；用例间通过 fixture 清理状态。
- 测试数据隔离：Qdrant 用 `namespace=test_*` + `user_id=test_*`，会话用 `session_id=test_*`，测试结束清理；不推荐污染生产集合（数据隔离属第 11 章安全硬约束）。
- e2e 应覆盖完整链路：提问 → 检索 → 工具调用 → 流式响应 → 会话持久化。
- API 测试应覆盖 OpenAI 兼容端点（`/v1/chat/completions` 流式 SSE + 非流式 + 错误码），并包含携带 Bearer JWT Token 与不携带两种场景（验证第 8 章身份解析与数据隔离）。
- 回归测试为合并门禁，不推荐跳过或 `@skip`。

**CI 流水线顺序（核心约定，优先选择）**：
1. 构建镜像 + 单元测试（失败即终止）
2. `docker compose up -d` + 等待全部健康检查通过
3. 功能测试 → API 测试 → 回归测试 → e2e 测试（按序，前者失败后者不执行）
4. 任一环节失败阻断合并；全部通过后 `docker compose down -v` 清理

**不推荐（选用需说明理由并等待用户确认）**：
- 在部署前跑功能/API/e2e 测试（无目标服务，结果不可信）。
- 测试用例跨用例共享可变状态（破坏独立性）。
- 用生产数据集做 e2e 测试（属第 11 章 PII 安全硬约束）。
- 跳过回归测试合并代码（破坏门禁保证）。

## 14. 前端测试页面规则

**定位**：Agent 应内置一个自包含的前端测试页面，用于联调、演示与冒烟验证，不承担生产前端职责。

**技术标准（核心约定，优先选择）**：
- 单文件 `static/index.html`，由 FastAPI `StaticFiles` 挂载到 `/`，不推荐独立前端工程。
- 原生 HTML + 原生 JS，不推荐引入 React/Vue/构建工具/Node 依赖。
- 样式用内联 `<style>` 或 CDN `<link>`，不推荐本地 CSS 文件；CDN 资源应可离线（自托管或内联）。
- 流式响应用浏览器原生 `fetch` + `ReadableStream` 解析 SSE，不推荐引入 EventSource polyfill 以外的库。
- 配置（API BaseURL/模型名/会话 ID/Bearer JWT Token）应从页面顶部输入框注入，不推荐硬编码后端地址。

**功能要求（核心约定，优先选择）**：
- 会话管理：新建会话（生成 UUID）/切换会话/清空当前会话；会话 ID 显式显示可复制。
- 对话交互：消息输入框 + 发送按钮 + Enter 发送（Shift+Enter 换行）；消息列表区分 user/assistant，支持 Markdown 渲染。
- 流式渲染：应实时流式显示（逐字/逐块追加），不推荐等待完整响应再渲染；渲染期间显示"生成中"状态。
- 会话上下文：每次请求应携带当前 `session_id`，验证后端多会话隔离与上下文压缩。
- 工具调用展示：当 Agent 触发 MCP 工具时，在消息气泡内显示工具名 + 参数 + 结果（折叠面板）。
- 检索来源展示：当 Agent 触发 RAG 时，在回答下方列出召回片段（source + score，折叠面板）。
- 错误处理：网络错误/超时/HTTP 非 2xx 应在页面显式提示，不推荐静默失败。

**API 调用约束**：
- 统一调用 OpenAI 兼容端点 `POST /v1/chat/completions`，请求体带 `stream: true`。
- 请求头 `Authorization: Bearer <jwt_token>`：若页面 Token 输入框非空则携带该值，为空则不发该头（后端按第 8 章降级 `DEFAULT_USER_ID`）。
- 不推荐调用后端私有端点（如 `/internal/*`）；测试页面应只走对外 OpenAI 兼容接口。
- 人在回路端点（P0-Future-03，仅 `human_review_enabled=True` 时使用）：
  - `POST /v1/feedback`：提交研究计划/大纲审核反馈，请求体 `{"session_id": str, "feedback": str}`；`feedback` 为空字符串或 `approve`/`accept`/`通过` 等关键词表示接受，其他内容视为修订意见。
  - `WS /v1/ws/{session_id}`：WebSocket 双向通道，接收 `{"type":"ping"}`（回 pong）、`{"type":"human_feedback","feedback":"..."}`（提交反馈）；服务端推送 8 类结构化消息（logs/content/node_progress/sources/tool_call/report/human_feedback_request/error）。
  - SSE（`/v1/chat/completions stream=true`）仍是主通道，WebSocket 是增强通道（仅人在回路审核请求推送与反馈接收）。

**部署约束**：
- `static/index.html` 由 Agent 容器内 FastAPI 直接托管，不推荐新增独立容器。
- 离线部署时该页面随 Agent 镜像分发，不推荐运行时从 CDN 拉取 JS/CSS。
- 生产环境可通过环境变量 `ENABLE_TEST_PAGE=false` 关闭挂载，默认 `dev=true / prod=false`。

**测试要求**：
- e2e 测试应包含一条用例：打开测试页面 → 新建会话 → 发送提问 → 验证流式渲染 → 验证工具调用展示 → 切换会话验证隔离。
- 不推荐用测试页面替代 API 测试；API 测试仍应直接打 HTTP 接口。

**不推荐（选用需说明理由并等待用户确认）**：
- 引入前端构建工具链（webpack/vite/rollup，违背自包含原则）。
- 引入前端框架（React/Vue/Svelte，违背原生 JS 约定）。
- 在测试页面写业务逻辑（路由/鉴权/权限）；页面仅做联调展示。
- 测试页面调用除 `/v1/chat/completions`、`/health`、`/v1/feedback`、`/v1/ws/{session_id}` 外的任何端点。`/v1/feedback` 与 `/v1/ws/{session_id}` 仅用于人在回路审核反馈（P0-Future-03），未启用 `human_review_enabled` 时前端不应调用。
