# 数据隐私声明 | Privacy Policy

[中文](#中文) | [English](#english)

---

## 中文

> **最后更新**：2026-07-11
>
> 本声明适用于 **agentinsight-researcher** 项目及其官方 Demo 环境（`http://43.139.209.145/`）。

## 数据收集说明

### 1. 会话内容

**收集内容**：
- 用户输入的研究查询（`messages`）
- Agent 生成的报告内容
- 检索来源（URL、标题、摘要）
- 工具调用记录（MCP 工具名、参数、结果）

**收集目的**：
- 提供研究报告生成服务
- 维护会话上下文以支持多轮对话
- 生成可下载的报告文件

**存储位置**：PostgreSQL 业务表（`research_sessions`、`research_reports`）+ LangGraph Checkpointer 表

### 2. 上传文件

**收集内容**：
- 用户通过 `POST /v1/files` 上传的文件（PDF/DOCX/MD/TXT/HTML/CSV/XLSX/PPTX）
- 文件元数据（文件名、大小、扩展名、上传时间）

**收集目的**：
- 作为研究数据源参与报告生成
- 支持文件内容检索（RAG）

**存储位置**：`/tmp/uploads`（容器内）+ Qdrant 向量库（用户私有 namespace）

**文件大小限制**：默认 50 MB（`MAX_UPLOAD_SIZE_MB` 可配置）

### 3. 使用日志

**收集内容**：
- API 请求日志（时间戳、端点、HTTP 方法、响应码）
- Token 使用量（prompt_tokens / completion_tokens / total_tokens）
- 成本追踪（cost_usd）
- Trace span（AgentInsight SDK 6 类：agent/generation/tool/retriever/chain/embedding）

**收集目的**：
- 服务运维与故障排查
- 成本分析与资源优化
- 全链路可观测性

**存储位置**：PostgreSQL 日志表（`token_usage_logs`、`research_search_logs`）+ AgentInsight 平台

### 4. 身份信息

**收集内容**：
- Bearer JWT Token 解析后的 `user_id`（不存储原始 Token）
- 无 Token 时的 IP-based `user_id`（基于客户端 IP 的确定性哈希，不存储原始 IP）

**收集目的**：
- 用户身份识别与数据隔离
- 访问控制与权限校验

> **安全约束**：原始 JWT Token 禁止写入日志或持久化存储；仅保留解析后的 `user_id`。

---

## 数据存储与加密

### 加密存储

| 数据类型 | 存储方式 | 加密措施 |
|---------|---------|---------|
| 会话内容 | PostgreSQL | 加密存储 + 日志脱敏 |
| 上传文件 | 容器文件系统 + Qdrant | 文件系统权限控制 |
| 用户密码（AgentInsight 平台） | PostgreSQL | BCrypt(cost=12) |
| API Key | PostgreSQL | SHA256 + BCrypt 双哈希 |
| Trace 数据 | AgentInsight 平台 | 传输加密（HTTPS） |

### 数据隔离

采用三级分键隔离机制：

- **Agent 隔离**：`agent_id`（= agent_name）区分不同 Agent
- **用户隔离**：`user_id` 区分不同用户，用户私有数据不跨用户共享
- **会话隔离**：`session_id`（= thread_id）区分不同会话

PostgreSQL 业务表均含 `agent_id` + `user_id` 双列复合索引，查询显式 `WHERE agent_id = ... AND user_id = ...`。

Qdrant 向量库通过 namespace 隔离：
- 共享知识库：`namespace = agent_id`（所有用户共享）
- 用户私有数据：`namespace = {agent_id}:{user_id}`（仅该用户可检索）

### 传输安全

- **生产环境**：强制 HTTPS
- **安全响应头**：`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Strict-Transport-Security: HSTS`
- **CORS**：生产环境不推荐 `*`，推荐配置具体域名列表

---

## 数据保留策略

### 默认保留期

| 数据类型 | 保留期 | 清理方式 |
|---------|--------|---------|
| 会话数据（Checkpoint） | 30 天（`CONTEXT_SESSION_TTL=2592000`） | 定时任务自动清理 |
| 报告记录 | 30 天 | 定时任务自动清理 |
| 上传文件 | 随会话一起清理 | 会话删除时级联清理 |
| 使用日志 | 90 天 | 定时任务自动清理 |
| Trace 数据 | 由 AgentInsight 平台策略管理 | 平台自动清理 |

### 会话删除

会话删除时执行级联清理：
1. PostgreSQL Checkpoint 数据
2. Redis 缓存
3. 业务元数据（报告记录、搜索日志）
4. 上传文件引用

### 数据卷清理

管理员可手动清理所有数据：

```bash
# 停止并清理所有数据卷（慎用）
docker compose -p agentinsight down -v
```

---

## 用户数据权利

### 1. 数据访问

用户可通过以下 API 访问自己的数据：
- `GET /v1/reports/session/{session_id}` — 列出会话内所有报告
- `GET /v1/reports/{report_id}/download` — 下载报告
- `GET /v1/mcp` — 查看 MCP 配置

### 2. 数据导出

用户可通过 API 下载自己的报告（Markdown/HTML/PDF/DOCX/JSON 格式）。

### 3. 数据删除

- **会话级删除**：停止使用后，会话数据将在 TTL（30 天）后自动清理
- **主动删除**：联系管理员手动清理特定用户数据

### 4. 数据纠正

如发现数据有误，可通过 [GitHub Issues](https://github.com/AgentInsight/agentinsight-researcher/issues) 联系维护团队。

---

## Demo 环境数据处理说明

### 官方 Demo 环境

- **地址**：`http://43.139.209.145/`
- **模式**：`SELF_HOST=False`（云托管模式）
- **鉴权**：测试页面引导鉴权与点数校验

### Demo 数据特别说明

⚠️ **重要提示**：

1. **Demo 环境数据不保证隐私**：请勿在 Demo 环境输入敏感信息（个人隐私、商业机密等）
2. **数据可能被其他用户查看**：Demo 环境为共享环境，不保证数据隔离的绝对安全
3. **数据定期清理**：Demo 环境数据可能随时清理，不保证持久性
4. **不建议 API 直连**：官方 Demo 仅建议通过页面试用，不建议直接调用 API

### 建议做法

- **试用**：使用 Demo 环境体验功能
- **正式使用**：本地部署或私有化部署，确保数据安全

---

## 第三方服务数据流转

本项目在运行时会与以下第三方服务交互，请了解各服务的数据处理政策：

| 服务 | 用途 | 传输数据 | 数据政策 |
|------|------|---------|---------|
| LLM 提供商（DeepSeek/智谱等） | 报告生成 | 用户查询 + 检索内容 | 各提供商隐私政策 |
| 搜索引擎（博查/Tavily 等） | 网页搜索 | 搜索查询词 | 各引擎隐私政策 |
| AgentInsight 平台 | 可观测性 | Trace span（不含原始 JWT） | AgentInsight 隐私政策 |
| HuggingFace | 模型下载 | 仅下载请求，不上传用户数据 | HuggingFace 隐私政策 |

> 本项目不对第三方服务的隐私政策负责，请用户自行评估各服务的数据处理方式。

---

## 联系方式

如有隐私相关问题，可通过以下方式联系：

- **GitHub Issues**：[https://github.com/AgentInsight/agentinsight-researcher/issues](https://github.com/AgentInsight/agentinsight-researcher/issues)
- **AgentInsight 平台**：[https://agentinsight.goldebridge.com](https://agentinsight.goldebridge.com)

---

## English

> **Last Updated**: 2026-07-11
>
> This policy applies to the **agentinsight-researcher** project and its official demo environment (`http://43.139.209.145/`).

## Data Collection

### 1. Session Content

**Collected**:
- User-entered research queries (`messages`)
- Agent-generated report content
- Retrieval sources (URL, title, snippet)
- Tool call records (MCP tool name, parameters, results)

**Purpose**:
- Provide research report generation service
- Maintain session context for multi-turn conversations
- Generate downloadable report files

**Storage**: PostgreSQL business tables (`research_sessions`, `research_reports`) + LangGraph Checkpointer tables

### 2. Uploaded Files

**Collected**:
- Files uploaded via `POST /v1/files` (PDF/DOCX/MD/TXT/HTML/CSV/XLSX/PPTX)
- File metadata (filename, size, extension, upload time)

**Purpose**:
- Serve as research data source for report generation
- Support file content retrieval (RAG)

**Storage**: `/tmp/uploads` (in container) + Qdrant vector DB (user private namespace)

**File Size Limit**: Default 50 MB (`MAX_UPLOAD_SIZE_MB` configurable)

### 3. Usage Logs

**Collected**:
- API request logs (timestamp, endpoint, HTTP method, response code)
- Token usage (prompt_tokens / completion_tokens / total_tokens)
- Cost tracking (cost_usd)
- Trace spans (AgentInsight SDK 6 types: agent/generation/tool/retriever/chain/embedding)

**Purpose**:
- Service operations and troubleshooting
- Cost analysis and resource optimization
- Full-link observability

**Storage**: PostgreSQL log tables (`token_usage_logs`, `research_search_logs`) + AgentInsight platform

### 4. Identity Information

**Collected**:
- `user_id` parsed from Bearer JWT Token (raw Token is NOT stored)
- IP-based `user_id` when no Token (deterministic hash of client IP; raw IP is NOT stored)

**Purpose**:
- User identity recognition and data isolation
- Access control and permission validation

> **Security constraint**: Raw JWT Tokens are prohibited from being written to logs or persistent storage; only the parsed `user_id` is retained.

---

## Data Storage & Encryption

### Encrypted Storage

| Data Type | Storage | Encryption |
|-----------|---------|------------|
| Session content | PostgreSQL | Encrypted storage + log desensitization |
| Uploaded files | Container filesystem + Qdrant | Filesystem permission control |
| User passwords (AgentInsight platform) | PostgreSQL | BCrypt(cost=12) |
| API Keys | PostgreSQL | SHA256 + BCrypt double hashing |
| Trace data | AgentInsight platform | Transport encryption (HTTPS) |

### Data Isolation

Three-tier key partitioning mechanism:

- **Agent isolation**: `agent_id` (= agent_name) distinguishes different agents
- **User isolation**: `user_id` distinguishes different users; private data is not shared across users
- **Session isolation**: `session_id` (= thread_id) distinguishes different sessions

PostgreSQL business tables all contain `agent_id` + `user_id` dual-column composite indexes; queries explicitly use `WHERE agent_id = ... AND user_id = ...`.

Qdrant vector DB isolates via namespace:
- Shared knowledge base: `namespace = agent_id` (shared by all users)
- User private data: `namespace = {agent_id}:{user_id}` (only that user can retrieve)

### Transport Security

- **Production**: HTTPS enforced
- **Security headers**: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Strict-Transport-Security: HSTS`
- **CORS**: `*` not recommended for production; specific domain list recommended

---

## Data Retention Policy

### Default Retention

| Data Type | Retention | Cleanup |
|-----------|-----------|---------|
| Session data (Checkpoint) | 30 days (`CONTEXT_SESSION_TTL=2592000`) | Scheduled task auto-cleanup |
| Report records | 30 days | Scheduled task auto-cleanup |
| Uploaded files | Cleaned with session | Cascading cleanup on session deletion |
| Usage logs | 90 days | Scheduled task auto-cleanup |
| Trace data | Managed by AgentInsight platform policy | Platform auto-cleanup |

### Session Deletion

Session deletion performs cascading cleanup:
1. PostgreSQL Checkpoint data
2. Redis cache
3. Business metadata (report records, search logs)
4. Uploaded file references

### Volume Cleanup

Administrators can manually clean all data:

```bash
# Stop and clean all data volumes (use with caution)
docker compose -p agentinsight down -v
```

---

## User Data Rights

### 1. Data Access

Users can access their data via the following APIs:
- `GET /v1/reports/session/{session_id}` — List all reports in a session
- `GET /v1/reports/{report_id}/download` — Download report
- `GET /v1/mcp` — View MCP configurations

### 2. Data Export

Users can download their reports via API (Markdown/HTML/PDF/DOCX/JSON formats).

### 3. Data Deletion

- **Session-level deletion**: Session data is automatically cleaned after TTL (30 days) of inactivity
- **Active deletion**: Contact administrator to manually clean specific user data

### 4. Data Correction

If data is found to be incorrect, contact the maintenance team via [GitHub Issues](https://github.com/AgentInsight/agentinsight-researcher/issues).

---

## Demo Environment Data Handling

### Official Demo Environment

- **URL**: `http://43.139.209.145/`
- **Mode**: `SELF_HOST=False` (cloud-hosted mode)
- **Auth**: Test page handles authentication and quota validation

### Demo Data Special Notes

⚠️ **Important**:

1. **Demo environment data privacy is not guaranteed**: Do not enter sensitive information (personal privacy, trade secrets, etc.) in the demo
2. **Data may be viewed by other users**: Demo is a shared environment; absolute data isolation security is not guaranteed
3. **Data is periodically cleaned**: Demo data may be cleaned at any time; persistence is not guaranteed
4. **Direct API calls not recommended**: The official demo is recommended for page trial only, not for direct API calls

### Recommended Practices

- **Trial**: Use the demo environment to experience features
- **Production use**: Deploy locally or privately to ensure data security

---

## Third-Party Service Data Flow

This project interacts with the following third-party services during operation; please review each service's data handling policy:

| Service | Purpose | Data Transmitted | Data Policy |
|---------|---------|-----------------|-------------|
| LLM providers (DeepSeek/Zhipu etc.) | Report generation | User queries + retrieved content | Each provider's privacy policy |
| Search engines (Bocha/Tavily etc.) | Web search | Search query terms | Each engine's privacy policy |
| AgentInsight platform | Observability | Trace spans (no raw JWT) | AgentInsight privacy policy |
| HuggingFace | Model download | Download requests only; no user data uploaded | HuggingFace privacy policy |

> This project is not responsible for the privacy policies of third-party services; users should evaluate each service's data handling practices independently.

---

## Contact

For privacy-related questions, contact via:

- **GitHub Issues**: [https://github.com/AgentInsight/agentinsight-researcher/issues](https://github.com/AgentInsight/agentinsight-researcher/issues)
- **AgentInsight Platform**: [https://agentinsight.goldebridge.com](https://agentinsight.goldebridge.com)
