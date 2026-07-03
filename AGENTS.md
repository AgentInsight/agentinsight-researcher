# AGENTS.md — LangGraph AI Agent 执行规则

> AI 助手在执行任何任务前必须先读本文档。规则只列"不读就会写错"的硬约束。

## 1. 项目定位与技术栈

**定位**：以 LangGraph 为编排内核、MCP 为工具协议、OTel+Langfuse 为可观测底座的企业级 AI Agent 系统，对外暴露 OpenAI 兼容 API（SSE 流式）。
**能力边界**：仅承载对话/检索/工具调用三类智能体能力；不承担模型训练、数据标注、前端 SSR；多 Agent 协作限子图/Supervisor/Swarm 三模式。

| 类别 | 选型 / 版本 | 选型理由（一句话） | 替代方案（未选原因） |
|------|------------|-------------------|---------------------|
| 语言 | Python ≥3.11 | 异步生态成熟，类型完备 | Go/Node（生态不及） |
| 编排内核 | LangGraph ≥1.2 | 状态机+条件边+Checkpointer，生产首选 | AutoGen/CrewAI（控制流不透明） |
| LLM 抽象 | langchain-core ≥1.4 | 仅核心类型，禁 Chain/AgentExecutor | langchain 全家桶（耦合高，禁） |
| 模型网关 | LiteLLM ≥1.6 | 一次接入 100+ 模型，内置成本/限流/重试 | 厂商 SDK 直连（禁） |
| 工具协议 | MCP | 2026 事实标准，复用生态 | 自定义委派协议（禁） |
| 向量库 | Qdrant ≥1.18 | API 优雅、过滤强、部署简单 | Pinecone（禁）/Milvus（运维重） |
| 关系库 | PostgreSQL ≥16 | Checkpointer+业务元数据，分布式共享 | MySQL（禁） |
| 缓存 | Redis ≥7.0 | 热点缓存+限流+短期会话 | Memcached（禁） |
| Embeddings | bge-large-zh-v1.5 | 中文最强开源嵌入，本地零成本 | OpenAI embedding（数据出境） |
| Rerank | bge-reranker-v2-m3 | 中文 Rerank SOTA，本地部署 | Cohere Rerank（闭源收费） |
| BM25 | rank-bm25+jieba | 中文分词+IDF，混合检索必备 | 字符 2-gram（降级兜底） |
| Web 框架 | FastAPI+Uvicorn ≥0.115 | 异步原生、OpenAPI 自动、SSE 流式 | Flask/Django（异步弱/过重） |
| 数据校验 | Pydantic ≥2.10 | SSOT 配置+入出参校验 | dataclass（校验弱） |
| 可观测性 | AgentInsight Python SDK ≥0.1.5 | 自家产品，异步上下文管理器，6 类 span | LangSmith（禁）/OTel 手动埋点（禁）/Langfuse（禁） |
| 评测 | RAGAS+DeepEval ≥0.2/≥2.0 | RAG 质量+Agent 行为双轨门禁 | 仅人工测试（不可 CI 化） |
| 部署 | Docker Compose | 单机多容器编排，离线包支持 | 裸机（无法弹性扩缩）/k8s（暂不采用） |

> 技术栈变更需架构师评审。

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
├── common/        # 公用基础模块（不得依赖 agents/ 或业务模块）
├── config/        # 配置文件（全局 Settings + 子智能体专属配置）
│   ├── settings.py    # 全局 pydantic-settings Settings SSOT
│   └── <agent_name>/  # 子智能体专属配置（如有子智能体，按名称建子目录）
├── skills/        # 技能定义
│   └── <agent_name>/  # 子智能体专属技能（如有子智能体，按名称建子目录）
├── tools/         # MCP Server 封装 + registry 注册中心
├── rag/           # 自研 RAG 层（retriever/reranker/embeddings/bm25）
├── llm/           # LiteLLM 网关封装
├── memory/        # Postgres Checkpointer 配置
├── observability/ # AgentInsight SDK 封装（tracing.py，6 类 trace_xxx）
└── api/           # FastAPI 路由 + middleware
```

**架构边界（硬约束）**：
- `graph/` 是唯一编排入口；`agents/` 复用图，禁止自建编排循环。
- `tools/`、`rag/`、`llm/`、`memory/` 不得互相 import，共享逻辑下沉到 `common/`。
- 依赖单向向内：`common/` 不依赖 `agents/` 或业务模块。
- 配置只经 `config/` + 环境变量，业务代码禁止硬编码 URL/密钥。
- 子智能体代码必须按名称隔离在 `agents/<agent_name>/`、`config/<agent_name>/`、`skills/<agent_name>/` 下，禁止跨子智能体直接引用；共享能力下沉到 `common/` 或 `skills/` 顶层。
- 新增顶层目录需架构师评审通过。

## 4. 三级行为边界与禁用清单

| 级别 | 触发场景（可枚举可校验） |
|------|------------------------|
| ✅ Always | 读/写 docs·tests·evals；跑 ruff+mypy+pytest；跑 RAGAS+DeepEval；修 P2 及以下 bug；续接被截断输出；删项目内文件 |
| ⚠️ Ask first | LangGraph 图结构/State schema 变更；RAG 核心算法（RRF/Rerank/Embeddings）切换；密钥轮换；外部系统对接；连续 3 次修复失败；评测门禁不达标 |
| ❌ Never | 见禁用清单，违例即阻断合并并提 P0 |

**禁用清单（CI 机器校验 import 黑名单）**：
1. `langchain` 全家桶（仅 `langchain-core` 可用）
2. `AgentExecutor`（必须用 LangGraph StateGraph）
3. AutoGen / CrewAI（重复多 Agent 编排框架）
4. LlamaIndex（重复 RAG/编排框架）
5. LangSmith（专有 SaaS，数据出境合规风险）/Langfuse（已由 AgentInsight SDK 替代）
6. Pinecone 等闭源托管向量库（用 Qdrant）
7. 直接调用厂商 SDK（必须经 LiteLLM）
8. Memcached（用 Redis）
9. MySQL（用 PostgreSQL）
10. `pickle` 持久化 Agent 状态（用 Postgres Checkpointer）
11. 全局可变状态（节点纯函数，状态从 state/deps 获取）
12. `eval`/`exec` 求值用户输入（注入风险）
13. 直接使用 `opentelemetry-sdk` 原生 API（必须经 `observability/tracing.py` 封装的 6 类 `trace_xxx`）
14. `agentinsight.observe` 装饰器（已弃用，无法记录 `model`/`usage_details`/`cost_details`）
15. 观察者模式实现追踪（禁用 Subject/Observer、attach/notify 机制；追踪统一用异步上下文管理器）

## 5. Agent 编排核心规则

LangGraph ≥1.2 状态机为**唯一编排范式**；禁用 AgentExecutor / 手写 ReAct 循环。

**State**：必须为 `TypedDict`；跨节点共享字段用 `Annotated[T, reducer]` 声明（消息流用 `add_messages`）。节点禁止原地修改入参 State，必须返回 delta dict 由 reducer 合并。禁用全局可变状态；会话级数据走 Checkpoint。

**Node**：节点为纯函数 `async def node(state: State) -> dict`，单一职责、无副作用。节点内禁止直连厂商 LLM SDK，统一走 `llm/` 网关（LiteLLM）。每个节点必须包裹在 AgentInsight trace span 内（见第 10 章）。

**Edge**：路由必须显式 `add_conditional_edges(src, router_fn, mapping)`，禁止隐式跳转。每个图必须有终止节点；`max_iterations` 为硬上限，由节点计数器 + 条件边强制，不可软超时。

**Checkpoint**：生产 `StateGraph` 必须挂 `PostgresSaver`（PostgreSQL ≥16）；内存 Checkpoint 仅 `ENV=dev` 允许。`thread_id` 从请求上下文注入做会话隔离键，禁止客户端自造。

## 6. 会话与上下文管理

**多会话支持（硬约束）**：
- 会话隔离键为 `thread_id`（即 `session_id`），由请求上下文注入，禁止客户端自造。
- 每个 Agent 必须支持并发多会话；会话间状态通过 Postgres Checkpointer 隔离，禁止共享可变内存。
- 会话级数据（消息历史/中间状态/缓存）必须按 `agent_id` + `user_id` + `session_id` 三级分键存储，禁止全局共享；`agent_id = agent_name`（见第 7 章），`user_id` 由第 8 章身份解析获得。

**上下文窗口（硬约束）**：
- 单会话上下文上限 `CONTEXT_MAX_CHARS = 800_000`（约 200K token）。
- 写入会话前必须调用 `compress_if_needed()` 检查阈值；超限必须压缩，禁止直接丢弃。
- 上下文压缩策略：滑动窗口 + LLM 摘要，保留最近 25% 消息为原文，其余摘要化。
- 压缩必须异步后台执行（`asyncio.create_task`），不阻塞用户响应。

**会话生命周期（硬约束）**：
- 会话持久化到 Postgres Checkpointer；内存 Checkpointer 仅 `ENV=dev` 允许。
- 会话 TTL 默认 30 天（`CONTEXT_SESSION_TTL=2592000`），过期会话由定时任务清理。
- 会话删除必须级联清理：Checkpoint + Redis 缓存 + 业务元数据。
- 写入防抖 `DEBOUNCE_SECONDS = 1.0`，后台 flush 线程 `FLUSH_INTERVAL_SECONDS = 0.5`。

## 7. 数据隔离与检索核心规则

**多 Agent 数据隔离总则（硬约束）**：
- 每个 Agent 的数据隔离键为 `agent_id = agent_name`（Agent 名称），全局唯一。
- 所有持久化层（Qdrant/Postgres/Redis）必须以 `agent_id` 区分各 Agent；用户私有数据（Postgres 业务表/Redis 缓存/Qdrant 用户导入数据）进一步按 `user_id` 区分，禁止跨用户共享；Qdrant 共享知识库仅按 `agent_id` 区分，不与 `user_id` 挂钩。
- Agent 注册时必须声明 `agent_name`，由配置注入，禁止运行时硬编码。

**PostgreSQL 约定**：单一数据库 `agents`（供多 Agent 共享）。LangGraph Checkpointer 表由官方管理（`thread_id` 已含会话隔离）。业务表必须含 `agent_id` + `user_id` 双列（VARCHAR，建复合索引）区分各 Agent 各用户数据；查询必须显式 `WHERE agent_id = ... AND user_id = ...`，禁止无过滤的全表扫描。表名复数 snake_case（如 `sessions`、`messages`），不得按 Agent 或用户拆表。

**Qdrant 集合约定**：单一集合 `agents`，`distance=Cosine`，`vector_size=1024`（bge-large-zh-v1.5 固定维度）。按 payload `namespace` 字段隔离数据，分两类：
- **共享知识库**（非用户导入数据）：`namespace = agent_id`（即 `agent_name`），payload 不含 `user_id`，所有用户共享，检索时默认召回。
- **用户私有数据**（用户导入数据）：`namespace = {agent_id}:{user_id}`（即 `{agent_name}:{user_id}`），payload 含 `user_id` 字段，仅该用户可检索，禁止跨用户召回。

点 id 用 `uuid5(NAMESPACE_DNS, f"{namespace}:{content_hash}")` 幂等生成。写入 payload 必须含 `content`+`metadata`+`namespace` 三键（用户私有数据额外含 `user_id`）。检索时必须显式传目标 namespace 列表（共享 + 当前用户私有），禁止无 namespace 过滤的全集合扫描。

**Redis 约定**：所有键必须加前缀 `{agent_id}:{user_id}:`，完整键格式 `{agent_id}:{user_id}:{module}:{type}:{id}`。禁止使用无 `agent_id` 或 `user_id` 前缀的裸键。会话级数据按 `{agent_id}:{user_id}:{session_id}` 三级分键。TTL 强制，禁用永久键（配置数据除外）。

**RAG 流水线**：检索必须混合 BM25 + 向量（bge-large-zh-v1.5），默认 `vector_weight=0.7 / bm25_weight=0.3`。重排序默认不启用；当 `rerank_enabled=True` 时，重排序经 `bge-reranker-v2-m3`，Top-K 召回后 rerank，禁止直接用向量分数作最终排序。`score_threshold` 默认 0.3，低于阈值丢弃（仅当 rerank 启用时生效，RRF 融合分数不应用此阈值）。Embedding 调用统一走 `rag/embeddings.py`，禁止业务代码直连 API。Qdrant 不可用时降级内存检索仅限 `ENV=dev`；生产必须告警并失败转移。

## 8. 用户身份解析规则

**Bearer JWT Token 处理（硬约束）**：
- Agent 接受请求头 `Authorization: Bearer <jwt_token>`；token 可选，不存在时走匿名用户路径。
- token 存在时：必须同步调用 `GET https://agentinsight.goldebridge.com/api/user`（携带原 `Authorization` 头）获取 `user_id`；调用失败按无 token 处理并告警。
- token 不存在或调用失败时：使用环境变量 `DEFAULT_USER_ID` 作为 `user_id`；`DEFAULT_USER_ID` 必须在 `.env` 配置，禁止硬编码。
- 解析得到的 `user_id` 必须注入请求上下文，供后续节点、会话、数据持久化使用（见第 6/7 章）。

**安全约束**：
- JWT 验证与 `user_id` 获取必须在 API 入口中间件完成，禁止在业务节点内重复解析。
- `user_id` 获取 API 调用须设超时（默认 5s），超时降级 `DEFAULT_USER_ID` 并告警。
- 禁止将原始 JWT token 写入日志或持久化存储；仅保留解析后的 `user_id`。

## 9. 工具与模型网关规则

**工具（MCP）**：所有外部工具通过 MCP Server 暴露；节点内禁止定义 ad-hoc tool function。工具注册集中在 `tools/registry.py` 单一注册表，按智能体名分组授权。工具调用必须经 AgentInsight `trace_tool` span 包裹（见第 10 章），参数与结果入 span。敏感工具（写文件/执行命令）须显式声明权限，由中间件校验。

**模型网关（LiteLLM）**：全部 LLM 调用经 `llm/` 的 `LLMClient`（底层 LiteLLM ≥1.6）；禁止直接 `openai`/`anthropic` 等 SDK。模型名以 LiteLLM 路由前缀声明（如 `deepseek/deepseek-chat`），由配置注入，禁止硬编码。流式统一 `achat_stream`；同步 `chat` 仅用于非交互式批处理。

## 10. 可观测性与评测规则

**可观测性内核（硬约束）**：
- 统一使用 **AgentInsight Python SDK**（pip 名 `agentinsight-sdk`，导入名 `agentinsight`，版本 ≥0.1.5），底层依赖 OpenTelemetry SDK，由 `observability/tracing.py` 统一封装。
- **追踪调用方式唯一**：异步上下文管理器 `async with trace_xxx(...) as span`；**禁用观察者模式**（无 Subject/Observer、无 attach/notify）；`@agentinsight.observe` 装饰器已弃用。
- SDK 初始化在 `observability/tracing.py` 模块导入时一次性完成，参数从 `config.py` 注入：`AGENTINSIGHT_PUBLIC_KEY`/`AGENTINSIGHT_SECRET_KEY`/`AGENTINSIGHT_HOST`；生产环境强制校验密钥存在。
- **降级策略**：SDK 初始化失败或运行时异常时，所有 `trace_xxx` yield `_NoopSpan`（Null Object 模式），业务代码**禁止**判断 SDK 是否可用，`span.update()` 调用永远安全。
- 业务代码**禁止**直接调用 `agentinsight.init()`/`agentinsight.get_client()`/`client.flush()`；统一经 `observability/tracing.py` 封装的 6 类 `trace_xxx`。
- 业务代码**禁止**直接使用 `opentelemetry-sdk` 原生 API。

**6 类 trace span（签名与必带字段）**：

| trace 类型 | as_type | 必带字段 | 包裹位置 |
|---|---|---|---|
| `trace_agent` | `agent` | `name`/`input`/`metadata`(含 `session_id`/`intent`)/`session_id`/`user_id` | 编排器入口，包裹 `graph.ainvoke()`，建立根 span |
| `trace_generation` | `generation` | `name`/`model`/`model_parameters`/`usage_details`/`cost_details` | `llm/` 网关层（`LLMClient.achat`/`achat_stream`），业务节点层**不重复包裹** |
| `trace_tool` | `tool` | `name`/`input`/`output`(span.update)/`metadata`(含 `tool_name`/`success`) | MCP 工具调用节点 |
| `trace_retriever` | `retriever` | `name`/`input`/`output`/`metadata`(含 `matched`/`candidate_count`/`retriever_type`/`top_score`) | RAG 检索节点（BM25/Vector/Qdrant search） |
| `trace_chain` | `chain` | `name`/`input`/`output` | 多步骤链式调用（RAG 管道、子图编排） |
| `trace_embedding` | `embedding` | `name`/`model`/`usage_details`(含 `token_count`) | `rag/embeddings.py`，高频调用启用 head-based 采样 |

**跨节点 span 传播（硬约束）**：
- span 父子关系由 OpenTelemetry Context API 在同一 asyncio task 内**自动传播**，**禁止**手动传递 span 对象。
- 编排器必须用 `trace_agent` 包裹 `graph.ainvoke()` 作为根 span；LangGraph 节点内创建的子 span 自动关联到根 span。
- 认证上下文（token/user_id 等）**不得**用 span 上下文传递；认证信息通过 `contextvars` + State 字段在节点入口显式恢复。
- `span.update(output=...)` 可在 `with` 块内多次增量调用；`trace_id`/`id` 为只读属性。

**采样策略**：
- `trace_agent`/`trace_generation`/`trace_tool`/`trace_retriever`/`trace_chain`：全量 1.0 采样。
- `trace_embedding`：head-based 采样，默认 `tracing_embedding_sample_rate=0.5`（高频 embed 调用降采样减存储压力）。
- SDK 底层用 `BatchSpanProcessor` 后台线程批量导出，HTTP 上报失败不阻塞主流程；业务代码**禁止**调用 `client.flush()` 阻塞事件循环。

**评测门禁（CI 强制，不达标禁止合并 main）**：
- RAGAS：faithfulness ≥0.8 / answer_relevancy ≥0.8 / context_precision ≥0.7
- DeepEval：任务完成率 ≥0.9 / 工具调用正确率 ≥0.95 / 幻觉率 ≤0.1

## 11. 安全合规红线

- **密钥**：仅环境变量注入，禁止入仓/硬编码/日志；API Key SHA256+BCrypt 双哈希，仅创建时返回一次；密码 BCrypt(cost=12)；发现硬编码密钥即 P0 暂停并人工介入
- **PII**：用户会话内容加密存储+日志脱敏；API 响应禁止返回密码/密钥原文；最小化收集，按用途设保留期
- **Prompt Injection**：所有外部输入经 Pydantic 校验；工具调用权限隔离（`read`/`write`/`execute`/`network` 显式授权）；禁止 `eval`/`exec` 求值用户输入；LLM 输出经结构化校验后再入工具
- **传输与边界**：生产强制 HTTPS；CORS 禁 `*`；安全响应头中间件（nosniff/DENY/HSTS）不可绕过；生产关闭 Debug

## 12. 部署规则

**容器清单（6 个独立容器，全部支持离线部署）**：

| 服务 | 镜像/构建 | 端口 | 健康检查 |
|------|----------|------|---------|
| `agent` | 本仓 `Dockerfile`（Python 3.12-slim） | 8066 | `GET /health` |
| `embeddings` | BGE 服务镜像（bge-large-zh-v1.5） | 8100 | `GET /health` |
| `rerank`（可选，`rerank_enabled=True` 时启用） | BGE 服务镜像（bge-reranker-v2-m3） | 8101 | `GET /health` |
| `qdrant` | `qdrant/qdrant:≥1.18` | 6333/6334 | `/healthz` |
| `postgres` | `postgres:≥16` | 5432 | `pg_isready -U <user>` |
| `redis` | `redis:≥7` | 6379 | `redis-cli ping` |

**离线部署硬约束**（参考 AgentInsightService 模式）：
- 所有镜像必须预下载为 tarball（`docker save -o packages/images/<image>.tar`），部署机 `docker load` 导入，禁止部署时 `docker pull`。
- Python 依赖必须预下载 wheel 到 `packages/`，构建时 `pip install --no-index --find-links=/app/packages -r requirements.txt`，禁止联网安装。
- 系统依赖（.deb）必须预下载到 `packages/debs/`，构建时 `dpkg -i /tmp/debs/*.deb`，禁止 `apt-get update`。
- BGE 模型权重必须预下载到 `packages/models/`，构建时 `COPY` 进镜像，禁止运行时从 HuggingFace 下载。
- 环境变量强制 `LANG=C.UTF-8` / `PYTHONIOENCODING=utf-8`。

**容器编排硬约束**：
- `restart: always`（生产）/ `unless-stopped`（开发）。
- `depends_on` 必须用 `condition: service_healthy`，禁止裸依赖（无健康检查直连）。
- 依赖顺序：`postgres` → `redis` → `qdrant` → `embeddings` → `agent`；`rerank` 为可选容器（`rerank_enabled=True` 时通过 `profiles: [rerank]` 启用，插入 `embeddings` 与 `agent` 之间，`agent` 不强制依赖 `rerank`）。
- 健康检查 `interval ≤ 30s` / `timeout ≤ 10s` / `retries ≥ 3` / `start_period ≥ 10s`。
- 数据卷必须用 `driver: local`，命名卷 `postgres_data` / `redis_data` / `qdrant_data` / `session_data`。
- 端口绑定：生产仅 `agent:8066` 对外暴露，其余绑定 `127.0.0.1`。

**Agent 容器硬约束**：
- 基础镜像 `python:3.12-slim`，非 root 用户运行。
- 多阶段构建：builder 阶段装依赖，runtime 阶段仅复制产物。
- `EXPOSE 8066`，`CMD ["python", "server.py"]`。
- env_file 分层：`.env`（公共）+ `.env.agent`（Agent 专属）+ `.env.{env}`（环境覆盖）。

**双套构建模式**：
项目提供两套构建文件，按部署场景选择：

| 模式 | 构建文件 | 编排文件 | 构建脚本 | 适用场景 |
|------|---------|---------|---------|---------|
| 离线模式 | `Dockerfile.offline` | `docker-compose.offline.yml` | `scripts/build-offline.ps1` | 本地测试、离线部署、内网环境 |
| 联网模式 | `Dockerfile` | `docker-compose.yml` | `scripts/build-online.ps1` | 开源社区、CI、外网环境 |

- **离线模式**：所有文件宿主机预下载到 `packages/`（wheels/debs/models/images），构建时 `pip install --no-index` 离线安装，部署时 `docker load` 加载镜像 tarball，模型从本地 volume 加载。适用于无外网环境或本地测试。
- **联网模式**：构建时从 PyPI 下载 Python 依赖、从 Docker Hub 拉取基础镜像，无需预下载 `packages/`。适用于开源社区贡献者快速起栈。
- 离线模式相关文件已加入 `.gitignore`（不入仓）：`Dockerfile.offline`、`docker-compose.offline.yml`、`scripts/build-offline.ps1`、`packages/wheels/`、`packages/debs/`、`packages/models/`、`packages/images/`。
- "禁止部署时联网拉镜像/装依赖/下模型" 约束仅适用于**离线模式**；联网模式允许构建时联网。

**禁止**：
- 离线模式部署时联网拉镜像/装依赖/下模型。
- 单容器混装多服务（如 agent + qdrant 同容器）。
- 绕过 `depends_on: service_healthy` 直接连未就绪服务。
- 使用 `latest` 标签（必须锁版本）。
- 生产环境映射非必要端口到 0.0.0.0。

## 13. 测试规则

**测试分层（按执行环境）**：

| 类型 | 执行环境 | 目录 | 触发时机 |
|------|---------|------|---------|
| 单元测试 | 本地 / 构建期 | `tests/unit/` | 每次 commit、Docker build 阶段 |
| 功能测试 | 部署后容器栈 | `tests/functional/` | 容器栈健康后 |
| 回归测试 | 部署后容器栈 | `tests/regression/` | 合并 main 前 |
| API 测试 | 部署后容器栈 | `tests/api/` | 容器栈健康后 |
| 端到端测试 | 部署后容器栈 | `tests/e2e/` | 合并 main 前、发布前 |

**硬约束**：
- 单元测试在构建期执行（Docker build 或 CI build job），不得依赖外部服务（Postgres/Qdrant/Redis/LLM）。
- 功能/回归/API/e2e 测试必须在 `docker compose up -d` 且全部容器 `service_healthy` 后执行，禁止本地直连或 mock 绕过。
- 测试目标地址从环境变量 `AGENT_URL` 注入（默认 `http://agent:8066`），禁止硬编码。
- 测试用例必须独立可重复运行，不得依赖执行顺序；用例间通过 fixture 清理状态。
- 测试数据隔离：Qdrant 用 `namespace=test_*` + `user_id=test_*`，会话用 `session_id=test_*`，测试结束清理；禁止污染生产集合。
- e2e 必须覆盖完整链路：提问 → 检索 → 工具调用 → 流式响应 → 会话持久化。
- API 测试必须覆盖 OpenAI 兼容端点（`/v1/chat/completions` 流式 SSE + 非流式 + 错误码），并包含携带 Bearer JWT Token 与不携带两种场景（验证第 8 章身份解析与数据隔离）。
- 回归测试为合并门禁，不得跳过或 `@skip`。

**CI 流水线顺序（硬约束）**：
1. 构建镜像 + 单元测试（失败即终止）
2. `docker compose up -d` + 等待全部健康检查通过
3. 功能测试 → API 测试 → 回归测试 → e2e 测试（按序，前者失败后者不执行）
4. 任一环节失败阻断合并；全部通过后 `docker compose down -v` 清理

**禁止**：
- 在部署前跑功能/API/e2e 测试（无目标服务）。
- 测试用例跨用例共享可变状态。
- 用生产数据集做 e2e 测试。
- 跳过回归测试合并代码。

## 14. 前端测试页面规则

**定位**：Agent 必须内置一个自包含的前端测试页面，用于联调、演示与冒烟验证，不承担生产前端职责。

**技术标准（硬约束）**：
- 单文件 `static/index.html`，由 FastAPI `StaticFiles` 挂载到 `/`，禁止独立前端工程。
- 原生 HTML + 原生 JS，禁止引入 React/Vue/构建工具/Node 依赖。
- 样式用内联 `<style>` 或 CDN `<link>`，禁止本地 CSS 文件；CDN 资源须可离线（自托管或内联）。
- 流式响应用浏览器原生 `fetch` + `ReadableStream` 解析 SSE，禁止引入 EventSource polyfill 以外的库。
- 配置（API BaseURL/模型名/会话 ID/Bearer JWT Token）从页面顶部输入框注入，禁止硬编码后端地址。

**功能要求（硬约束）**：
- 会话管理：新建会话（生成 UUID）/切换会话/清空当前会话；会话 ID 显式显示可复制。
- 对话交互：消息输入框 + 发送按钮 + Enter 发送（Shift+Enter 换行）；消息列表区分 user/assistant，支持 Markdown 渲染。
- 流式渲染：必须实时流式显示（逐字/逐块追加），禁止等待完整响应再渲染；渲染期间显示"生成中"状态。
- 会话上下文：每次请求必须携带当前 `session_id`，验证后端多会话隔离与上下文压缩。
- 工具调用展示：当 Agent 触发 MCP 工具时，在消息气泡内显示工具名 + 参数 + 结果（折叠面板）。
- 检索来源展示：当 Agent 触发 RAG 时，在回答下方列出召回片段（source + score，折叠面板）。
- 错误处理：网络错误/超时/HTTP 非 2xx 必须在页面显式提示，禁止静默失败。

**API 调用约束**：
- 统一调用 OpenAI 兼容端点 `POST /v1/chat/completions`，请求体带 `stream: true`。
- 请求头 `Authorization: Bearer <jwt_token>`：若页面 Token 输入框非空则携带该值，为空则不发该头（后端按第 8 章降级 `DEFAULT_USER_ID`）。
- 禁止调用后端私有端点（如 `/internal/*`）；测试页面只能走对外 OpenAI 兼容接口。

**部署约束**：
- `static/index.html` 由 Agent 容器内 FastAPI 直接托管，不新增独立容器。
- 离线部署时该页面随 Agent 镜像分发，禁止运行时从 CDN 拉取 JS/CSS。
- 生产环境可通过环境变量 `ENABLE_TEST_PAGE=false` 关闭挂载，默认 `dev=true / prod=false`。

**测试要求**：
- e2e 测试必须包含一条用例：打开测试页面 → 新建会话 → 发送提问 → 验证流式渲染 → 验证工具调用展示 → 切换会话验证隔离。
- 禁止用测试页面替代 API 测试；API 测试仍须直接打 HTTP 接口。

**禁止**：
- 引入前端构建工具链（webpack/vite/rollup）。
- 引入前端框架（React/Vue/Svelte）。
- 在测试页面写业务逻辑（路由/鉴权/权限）；页面仅做联调展示。
- 测试页面调用除 `/v1/chat/completions`、`/health` 外的任何端点。
