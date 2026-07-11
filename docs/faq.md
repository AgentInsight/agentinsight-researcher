# 常见问题 | FAQ

[中文](#中文) | [English](#english)

---

## 中文

## 部署相关

### Q1: 容器启动失败，如何排查？

**A**: 按以下步骤排查：

1. **查看容器状态**：`docker compose -p agentinsight ps`，确认哪些容器未 healthy
2. **查看日志**：`docker compose -p agentinsight logs <service_name> --tail 100`
3. **常见原因**：
   - `.env` 文件缺失或必填项为空（如 `POSTGRES_PASSWORD`、`QDRANT_API_KEY`）
   - 端口被占用（见下方端口冲突问题）
   - Docker 内存不足（建议 ≥4GB，Embeddings 模型加载需要较多内存）
   - 依赖服务未就绪（`depends_on: service_healthy` 已保证顺序，但首次启动 Embeddings 模型下载可能超时）

### Q2: 端口冲突怎么办？8066/6333/5432 等端口被占用

**A**: 本项目涉及以下端口：

| 端口 | 服务 | 绑定方式 |
|------|------|---------|
| 8066 | agent | 0.0.0.0（对外） |
| 8088 | embeddings | 0.0.0.0（对外） |
| 8089 | rerank（可选） | 0.0.0.0（对外） |
| 6333 | qdrant HTTP | 0.0.0.0（对外） |
| 6334 | qdrant gRPC | 127.0.0.1（仅本机） |
| 5432 | postgres | 127.0.0.1（仅本机） |
| 6379 | redis | 127.0.0.1（仅本机） |
| 8099 | searxng | 127.0.0.1（仅本机） |

**解决方法**：
- 修改 `.env` 中的 `POSTGRES_PORT` 等可配置端口
- 或停止占用端口的进程
- 与 AgentInsightService 项目共享 8066 端口，切换项目时需先 `docker compose -p agentinsight down` 停止一方

### Q3: 模型下载太慢怎么办？

**A**: 生产联网模式下，Embeddings（bge-base-zh-v1.5）和 Rerank（bge-reranker-v2-m3）模型在首次启动时从 HuggingFace 下载到命名卷缓存：

- **首次启动**：Embeddings 容器 `start_period: 180s`，需耐心等待
- **加速方案**：使用 QA 离线模式或生产离线模式，预下载模型到 `packages/models/`
- **镜像加速**：配置 Docker Hub 镜像源或 HuggingFace 镜像（如 `HF_ENDPOINT=https://hf-mirror.com`）
- **查看下载进度**：`docker compose -p agentinsight logs -f embeddings`

### Q4: Agent 容器健康检查不通过

**A**: Agent 容器健康检查为 `GET /health`（30s start_period）。常见原因：

1. **依赖服务未就绪**：检查 `docker compose -p agentinsight ps` 是否全部 healthy
2. **业务表初始化失败**：查看 agent 日志中的 PostgreSQL 初始化错误
3. **AgentInsight SDK 初始化失败**：SDK 降级为 NoopSpan，不阻断启动；但需检查 `AGENTINSIGHT_PUBLIC_KEY`/`AGENTINSIGHT_SECRET_KEY` 是否配置
4. **Python 依赖问题**：确认 Docker 镜像构建成功，查看构建日志

---

## 配置相关

### Q5: `.env` 文件如何配置？必填项有哪些？

**A**: 从 `.env.template` 复制后编辑。必填项包括：

| 配置项 | 说明 | 获取方式 |
|--------|------|---------|
| `AGENTINSIGHT_PUBLIC_KEY` | AgentInsight PublicKey | [平台注册](https://agentinsight.goldebridge.com/platform) |
| `AGENTINSIGHT_SECRET_KEY` | AgentInsight SecretKey | 同上 |
| `DEEPSEEK_API_KEY` | DeepSeek API Key | [DeepSeek 平台](https://platform.deepseek.com/) |
| `BOCHA_API_KEY` | 博查搜索 API Key | [博查搜索](https://bochaai.com/) |
| `POSTGRES_PASSWORD` | PostgreSQL 密码 | 自定义 |
| `QDRANT_API_KEY` | Qdrant 静态 API Key | 自定义 |

> 至少配置一个 LLM API Key 和一个搜索引擎 API Key。完整配置项见 `.env.template`。

### Q6: AgentInsight API Key 如何获取？

**A**:
1. 访问 [https://agentinsight.goldebridge.com/platform](https://agentinsight.goldebridge.com/platform) 注册账户
2. 登录后进入「项目设置 → API Key 管理」
3. 创建 APIKey，获得：
   - **PublicKey**（`pk-` 开头）
   - **SecretKey**（`sk-` 开头，仅创建时显示一次）
4. 配置到 `.env` 的 `AGENTINSIGHT_PUBLIC_KEY` 和 `AGENTINSIGHT_SECRET_KEY`

### Q7: 如何切换 LLM 模型？

**A**: 修改 `.env` 中的三级 LLM 配置：

```env
FAST_LLM=zhipuai/glm-4-flash          # 快速响应（短查询/聊天）
SMART_LLM=deepseek/deepseek-v4-flash   # 智能分析（报告生成主力）
STRATEGIC_LLM=deepseek/deepseek-v4-pro # 战略决策（深度研究）
```

模型名使用 LiteLLM 路由前缀（如 `deepseek/`、`zhipuai/`、`openai/`）。当 STRATEGIC 不可用时自动降级到 SMART，再降级到 FAST。

### Q8: `SELF_HOST=True` 和 `SELF_HOST=False` 有什么区别？

**A**:
- **`SELF_HOST=True`（默认）**：自托管模式，Bearer Token 可选；无 Token 时按客户端 IP 生成确定性 user_id；适合内网部署
- **`SELF_HOST=False`**：云托管模式，研究端点强制要求 Bearer JWT Token + `org_id`/`project_id`；适合 SaaS 场景

---

## API 调用相关

### Q9: 如何通过 OpenAI SDK 调用？

**A**: 直接使用 OpenAI Python SDK，设置 `base_url` 即可：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8066/v1",
    api_key="any-string-not-used-in-self-host-mode",  # SELF_HOST=True 时随意填
)

resp = client.chat.completions.create(
    model="agentinsight-researcher",
    messages=[{"role": "user", "content": "分析2026年新能源汽车市场"}],
    stream=True,
    extra_body={"report_type": "detailed_report"},
)
for chunk in resp:
    delta = chunk.choices[0].delta.content or ""
    print(delta, end="", flush=True)
```

### Q10: 流式 SSE 响应格式是什么？

**A**: 流式响应为标准 SSE 格式，每帧以 `data: ` 前缀、`\n\n` 结尾：

```
data: {"choices":[{"delta":{"content":"报告内容..."}}]}
data: {"choices":[{"delta":{"sources":[...]}}]}
data: {"choices":[{"delta":{"report_id":"xxx"}}]}
data: {"choices":[{"delta":{},"finish_reason":"stop"}]}
data: [DONE]
```

`delta` 字段可能包含：`role` / `content` / `progress` / `sources` / `report_id` / `file_path` / `report_format`。

### Q11: 常见错误码及处理方式

| HTTP | 含义 | 处理方式 |
|------|------|---------|
| 400 | 请求参数错误 | 检查 `messages` 是否包含 user 消息 |
| 401 | 未授权 | `SELF_HOST=False` 时需携带 Bearer Token |
| 403 | 无权访问 | 报告/资源不属于当前用户（数据隔离校验失败） |
| 404 | 资源不存在 | 检查 report_id / session_id 是否正确 |
| 413 | 文件过大 | 调整 `MAX_UPLOAD_SIZE_MB` 或压缩文件 |
| 429 | 请求过多 | `SELF_HOST=False` 时本月调用次数已达上限 |
| 500 | 服务器错误 | 查看 agent 日志排查 |

### Q12: 请求超时怎么办？

**A**: 研究报告生成可能耗时 1-5 分钟（取决于报告类型与网络状况）：

- **流式请求**：建议设置 `timeout=300`（5 分钟），流式响应会持续发送数据保持连接
- **非流式请求**：建议设置 `timeout=600`（10 分钟）
- **WebSocket**：无超时限制，但建议定期发送 `ping` 保活

---

## 报告相关

### Q13: 支持哪些报告格式？

**A**: 支持 5 种输出格式，可在请求时通过 `report_format` 指定，或通过 `/v1/reports/{report_id}/download?format=xxx` 下载时实时转换：

| 格式 | 说明 | Content-Type |
|------|------|-------------|
| `markdown` | Markdown 格式（默认） | `text/markdown` |
| `html` | HTML 格式（含样式） | `text/html` |
| `pdf` | PDF 格式 | `application/pdf` |
| `docx` | Word 文档格式 | `application/vnd.openxmlformats...` |
| `json` | JSON 结构化格式 | `application/json` |

### Q14: 如何下载已生成的报告？

**A**: 两种方式：

1. **API 下载**：
   ```bash
   curl -OJ http://localhost:8066/v1/reports/{report_id}/download?format=pdf
   ```

2. **测试页面下载**：报告生成完成后，在测试页面消息气泡内点击下载按钮

3. **列出会话报告**：
   ```bash
   curl http://localhost:8066/v1/reports/session/{session_id}
   ```

### Q15: PDF 生成失败怎么办？

**A**: PDF 生成依赖报告内容的 HTML 渲染。常见原因：

- 报告内容含特殊字符导致 HTML 解析失败
- 报告内容过长（建议拆分为多个报告）
- 容器内存不足（PDF 渲染需要额外内存）

排查：查看 agent 日志中的 PDF 生成错误信息。返回 404 表示报告不存在或 PDF 生成失败。

---

## 搜索引擎相关

### Q16: SearXNG 如何配置？

**A**: SearXNG 为自托管元搜索引擎容器（端口 8099），聚合 Bing/Baidu/Brave 等 22 个搜索源：

- **配置文件**：`config/searxng/settings.yml`（引擎开关、limiter、redis 配置）
- **限流配置**：`config/searxng/limiter.toml`
- **自定义引擎**：`config/searxng/engines/`（含 sogou_stealth、baidu_stealth、quark_stealth）
- **`.env` 配置**：`SEARX_URL=http://searxng:8099`（容器内通过服务名访问）

### Q17: 搜索引擎 API Key 如何选择？

**A**: 根据使用场景选择：

| 场景 | 推荐引擎 | 说明 |
|------|---------|------|
| 国内中文研究 | 博查 Bocha（主）+ 秘塔 Metaso | 中文搜索质量最高 |
| 国际英文研究 | Tavily（主）+ Brave | SimpleQA 排名第一 |
| 学术研究 | PubMed + Arxiv + Semantic Scholar | 免费，无需 API Key |
| 低成本方案 | SearXNG（自托管）+ DuckDuckGo | 零 API Key 成本 |

> 至少配置一个搜索引擎 API Key。未配置的引擎自动跳过。

### Q18: 如何指定使用的搜索引擎？

**A**: 当前通过区域自动检测（中文字符比例 + 学术关键词）路由到 CN/GLOBAL/ACADEMIC 区域。如需手动指定：

- 在查询中包含学术关键词（如 "paper"、"论文"、"arxiv"）会自动路由到 ACADEMIC
- 中文查询自动路由到 CN 区域（博查/秘塔/DuckDuckGo）
- 英文查询路由到 GLOBAL 区域（Tavily/Brave/Bing 等）

---

## MCP 相关

### Q19: MCP 工具如何配置？

**A**: 通过 `/v1/mcp` API 或测试页面配置：

1. **查看系统 MCP**：`GET /v1/mcp/system` — 列出预置的官方 MCP 配置
2. **克隆系统 MCP**：`POST /v1/mcp/system/{config_id}/clone` — 克隆到用户私有列表
3. **创建自定义 MCP**：`POST /v1/mcp` — 支持三种传输模式：
   - `stdio`：本地模式（如 `npx @modelcontextprotocol/server-git`）
   - `sse`：远程 SSE 模式
   - `streamable_http`：远程 HTTP 流模式
4. **测试 MCP**：`POST /v1/mcp/test` — 保存前预测试可用性

### Q20: MCP 工具调用失败怎么办？

**A**: 常见原因与排查：

| 错误类型 | 原因 | 解决方案 |
|---------|------|---------|
| `command_not_found` | 容器未安装对应运行时（如 Node.js） | 使用 `sse`/`streamable_http` 远程模式，或在容器中安装 |
| `connection_refused` | 远程 MCP 服务不可达 | 检查 `server_url` 网络连通性 |
| `timeout` | MCP 服务响应超时（30s） | 优化 MCP 服务性能或检查网络 |
| `handshake_failed` | MCP 协议握手失败 | 确认 MCP 服务端版本兼容 |
| `placeholder_env` | 环境变量未替换占位符 | 填入真实的 API Key / 路径 |

### Q21: stdio 模式的 MCP 提示 command_not_found？

**A**: Agent 容器基于 `python:3.12-slim`，**未预装 Node.js/npm/npx**。因此 `npx` 类 MCP（如 `@modelcontextprotocol/server-git`）不可用。解决方案：

1. **改用远程模式**：将 MCP 部署为独立的 `sse`/`streamable_http` 服务
2. **自定义 Dockerfile**：在 Agent 镜像中安装 Node.js（不推荐，增加镜像体积）
3. **使用 Python MCP**：选择基于 Python 的 MCP Server 实现

---

## 性能相关

### Q22: 上下文压缩是如何工作的？

**A**: 单会话上下文上限为 `CONTEXT_MAX_CHARS = 800,000`（约 200K token）：

- **阈值检测**：每次写入会话前调用 `compress_if_needed()` 检查
- **压缩策略**：滑动窗口 + LLM 摘要，保留最近 25% 消息为原文，其余摘要化
- **异步执行**：压缩在后台 `asyncio.create_task` 执行，不阻塞用户响应
- **Embeddings**：压缩使用本地 FastEmbed（bge-small-zh-v1.5，512 维），不依赖远程 TEI

### Q23: Embeddings 缓存如何工作？

**A**: Embeddings 调用有两层缓存：

1. **Redis 缓存**：基于内容 hash 的 TTL 缓存，命中率高时大幅减少 TEI 调用
2. **LRU 淘汰**：Redis 内存不足时按 LRU 策略淘汰

此外，上下文压缩使用独立的 FastEmbed 本地模型（ONNX INT8 量化），避免与 Qdrant 索引的远程 TEI（768 维）竞争资源。

### Q24: 如何优化报告生成速度？

**A**:
- **选择合适的报告类型**：`basic_report` 最快（约 30-60s），`deep_research` 最慢（约 3-5 分钟）
- **启用 Rerank**：`rerank_enabled=true` 会增加检索质量但降低速度，按需开启
- **调整并发抓取**：`MAX_SCRAPER_WORKERS=15`（默认），网络好时可适当提高
- **使用 FAST_LLM**：对速度敏感的场景，将 `SMART_LLM` 设为较快的模型
- **缓存命中**：相同查询的 MCP 工具调用有 TTL 缓存

### Q25: 单次研究报告成本是多少？

**A**: 默认采用 DeepSeek 全栈 + 智谱免费层方案，单次研究报告成本约 **¥0.18**：

- `basic_report`：约 ¥0.05-0.10
- `detailed_report`：约 ¥0.15-0.25
- `deep_research`：约 ¥0.30-0.50

成本追踪通过 LiteLLM 25+ 模型定价表按 step 分步累计，可在响应的 `usage.cost_usd` 字段查看。

---

## English

## Deployment

### Q1: Container startup fails, how to troubleshoot?

**A**: Follow these steps:

1. **Check container status**: `docker compose -p agentinsight ps` to see which containers are not healthy
2. **View logs**: `docker compose -p agentinsight logs <service_name> --tail 100`
3. **Common causes**:
   - `.env` file missing or required fields empty (e.g., `POSTGRES_PASSWORD`, `QDRANT_API_KEY`)
   - Port conflicts (see port conflict question below)
   - Insufficient Docker memory (recommend ≥4GB; Embeddings model loading needs more memory)
   - Dependency services not ready (`depends_on: service_healthy` ensures order, but first-time Embeddings model download may timeout)

### Q2: Port conflict? Ports 8066/6333/5432 are occupied

**A**: This project uses the following ports:

| Port | Service | Binding |
|------|---------|---------|
| 8066 | agent | 0.0.0.0 (external) |
| 8088 | embeddings | 0.0.0.0 (external) |
| 8089 | rerank (optional) | 0.0.0.0 (external) |
| 6333 | qdrant HTTP | 0.0.0.0 (external) |
| 6334 | qdrant gRPC | 127.0.0.1 (localhost only) |
| 5432 | postgres | 127.0.0.1 (localhost only) |
| 6379 | redis | 127.0.0.1 (localhost only) |
| 8099 | searxng | 127.0.0.1 (localhost only) |

**Solutions**:
- Modify configurable ports in `.env` (e.g., `POSTGRES_PORT`)
- Or stop the process occupying the port
- Port 8066 is shared with the AgentInsightService project; stop one before starting the other with `docker compose -p agentinsight down`

### Q3: Model download is too slow

**A**: In production online mode, Embeddings (bge-base-zh-v1.5) and Rerank (bge-reranker-v2-m3) models are downloaded from HuggingFace on first startup to named volume cache:

- **First startup**: Embeddings container `start_period: 180s`; be patient
- **Acceleration**: Use QA offline mode or production offline mode with pre-downloaded models in `packages/models/`
- **Mirror**: Configure Docker Hub mirror or HuggingFace mirror (e.g., `HF_ENDPOINT=https://hf-mirror.com`)
- **Monitor progress**: `docker compose -p agentinsight logs -f embeddings`

### Q4: Agent container health check fails

**A**: Agent health check is `GET /health` (30s start_period). Common causes:

1. **Dependencies not ready**: Check if all services are healthy with `docker compose -p agentinsight ps`
2. **Business table init failure**: Check agent logs for PostgreSQL initialization errors
3. **AgentInsight SDK init failure**: SDK degrades to NoopSpan, doesn't block startup; but verify `AGENTINSIGHT_PUBLIC_KEY`/`AGENTINSIGHT_SECRET_KEY` are configured
4. **Python dependency issues**: Ensure Docker image build succeeded; check build logs

---

## Configuration

### Q5: How to configure `.env`? What are the required fields?

**A**: Copy from `.env.template` and edit. Required fields:

| Config | Description | How to obtain |
|--------|-------------|---------------|
| `AGENTINSIGHT_PUBLIC_KEY` | AgentInsight PublicKey | [Platform registration](https://agentinsight.goldebridge.com/platform) |
| `AGENTINSIGHT_SECRET_KEY` | AgentInsight SecretKey | Same as above |
| `DEEPSEEK_API_KEY` | DeepSeek API Key | [DeepSeek platform](https://platform.deepseek.com/) |
| `BOCHA_API_KEY` | Bocha search API Key | [Bocha search](https://bochaai.com/) |
| `POSTGRES_PASSWORD` | PostgreSQL password | Custom |
| `QDRANT_API_KEY` | Qdrant static API Key | Custom |

> At least one LLM API Key and one search engine API Key must be configured. See `.env.template` for complete configuration.

### Q6: How to obtain AgentInsight API Key?

**A**:
1. Visit [https://agentinsight.goldebridge.com/platform](https://agentinsight.goldebridge.com/platform) to register
2. After login, go to "Project Settings → API Key Management"
3. Create an APIKey to receive:
   - **PublicKey** (starts with `pk-`)
   - **SecretKey** (starts with `sk-`, shown only once at creation)
4. Configure in `.env` as `AGENTINSIGHT_PUBLIC_KEY` and `AGENTINSIGHT_SECRET_KEY`

### Q7: How to switch LLM models?

**A**: Modify the three-tier LLM config in `.env`:

```env
FAST_LLM=zhipuai/glm-4-flash          # Fast response (short queries/chat)
SMART_LLM=deepseek/deepseek-v4-flash   # Smart analysis (main report generation)
STRATEGIC_LLM=deepseek/deepseek-v4-pro # Strategic decisions (deep research)
```

Model names use LiteLLM routing prefixes (e.g., `deepseek/`, `zhipuai/`, `openai/`). Auto-degrades from STRATEGIC → SMART → FAST when unavailable.

### Q8: What's the difference between `SELF_HOST=True` and `SELF_HOST=False`?

**A**:
- **`SELF_HOST=True` (default)**: Self-hosted mode; Bearer Token optional; generates deterministic user_id from client IP when no Token; suitable for intranet deployment
- **`SELF_HOST=False`**: Cloud-hosted mode; research endpoint requires Bearer JWT Token + `org_id`/`project_id`; suitable for SaaS scenarios

---

## API Usage

### Q9: How to call via OpenAI SDK?

**A**: Use the OpenAI Python SDK directly with `base_url`:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8066/v1",
    api_key="any-string-not-used-in-self-host-mode",  # Arbitrary in SELF_HOST=True
)

resp = client.chat.completions.create(
    model="agentinsight-researcher",
    messages=[{"role": "user", "content": "Analyze the 2026 NEV market"}],
    stream=True,
    extra_body={"report_type": "detailed_report"},
)
for chunk in resp:
    delta = chunk.choices[0].delta.content or ""
    print(delta, end="", flush=True)
```

### Q10: What is the streaming SSE response format?

**A**: Standard SSE format, each frame prefixed with `data: ` and ending with `\n\n`:

```
data: {"choices":[{"delta":{"content":"report content..."}}]}
data: {"choices":[{"delta":{"sources":[...]}}]}
data: {"choices":[{"delta":{"report_id":"xxx"}}]}
data: {"choices":[{"delta":{},"finish_reason":"stop"}]}
data: [DONE]
```

`delta` field may contain: `role` / `content` / `progress` / `sources` / `report_id` / `file_path` / `report_format`.

### Q11: Common error codes and handling

| HTTP | Meaning | Handling |
|------|---------|----------|
| 400 | Bad request | Check if `messages` contains a user message |
| 401 | Unauthorized | Bearer Token required when `SELF_HOST=False` |
| 403 | Forbidden | Report/resource doesn't belong to current user (isolation check failed) |
| 404 | Not found | Check report_id / session_id |
| 413 | Payload too large | Adjust `MAX_UPLOAD_SIZE_MB` or compress file |
| 429 | Too many requests | Monthly call limit reached (`SELF_HOST=False`) |
| 500 | Server error | Check agent logs |

### Q12: Request timeout?

**A**: Research report generation may take 1-5 minutes (depending on report type and network):

- **Streaming**: Recommend `timeout=300` (5 min); streaming keeps connection alive
- **Non-streaming**: Recommend `timeout=600` (10 min)
- **WebSocket**: No timeout; recommend periodic `ping` for keepalive

---

## Reports

### Q13: What report formats are supported?

**A**: 5 output formats, specified via `report_format` in request or real-time conversion at download:

| Format | Description | Content-Type |
|--------|-------------|-------------|
| `markdown` | Markdown (default) | `text/markdown` |
| `html` | HTML with styles | `text/html` |
| `pdf` | PDF | `application/pdf` |
| `docx` | Word document | `application/vnd.openxmlformats...` |
| `json` | JSON structured | `application/json` |

### Q14: How to download generated reports?

**A**: Two ways:

1. **API download**:
   ```bash
   curl -OJ http://localhost:8066/v1/reports/{report_id}/download?format=pdf
   ```

2. **Test page download**: Click the download button in the message bubble after generation

3. **List session reports**:
   ```bash
   curl http://localhost:8066/v1/reports/session/{session_id}
   ```

### Q15: PDF generation fails?

**A**: PDF generation depends on HTML rendering of report content. Common causes:

- Special characters in report causing HTML parse failure
- Report content too long (consider splitting into multiple reports)
- Container memory insufficient (PDF rendering needs extra memory)

Troubleshoot: Check agent logs for PDF generation errors. 404 means report not found or PDF generation failed.

---

## Search Engines

### Q16: How to configure SearXNG?

**A**: SearXNG is a self-hosted meta search engine container (port 8099) aggregating 22 search sources:

- **Config file**: `config/searxng/settings.yml` (engine toggles, limiter, redis config)
- **Limiter config**: `config/searxng/limiter.toml`
- **Custom engines**: `config/searxng/engines/` (includes sogou_stealth, baidu_stealth, quark_stealth)
- **`.env` config**: `SEARX_URL=http://searxng:8099` (accessed via service name within compose network)

### Q17: How to choose search engine API Keys?

**A**: Based on use case:

| Scenario | Recommended Engines | Notes |
|----------|-------------------|-------|
| Chinese research | Bocha (primary) + Metaso | Highest Chinese search quality |
| English research | Tavily (primary) + Brave | SimpleQA #1 |
| Academic research | PubMed + Arxiv + Semantic Scholar | Free, no API Key needed |
| Low-cost option | SearXNG (self-hosted) + DuckDuckGo | Zero API Key cost |

> At least one search engine API Key must be configured. Unconfigured engines are automatically skipped.

### Q18: How to specify which search engine to use?

**A**: Currently auto-routed by region detection (Chinese character ratio + academic keywords) to CN/GLOBAL/ACADEMIC regions:

- Academic keywords in query (e.g., "paper", "arxiv") → ACADEMIC region
- Chinese query → CN region (Bocha/Metaso/DuckDuckGo)
- English query → GLOBAL region (Tavily/Brave/Bing etc.)

---

## MCP

### Q19: How to configure MCP tools?

**A**: Via `/v1/mcp` API or test page:

1. **View system MCPs**: `GET /v1/mcp/system` — lists preset official MCP configs
2. **Clone system MCP**: `POST /v1/mcp/system/{config_id}/clone` — clone to user's private list
3. **Create custom MCP**: `POST /v1/mcp` — supports three transport modes:
   - `stdio`: local mode (e.g., `npx @modelcontextprotocol/server-git`)
   - `sse`: remote SSE mode
   - `streamable_http`: remote HTTP stream mode
4. **Test MCP**: `POST /v1/mcp/test` — pre-test before saving

### Q20: MCP tool call fails?

**A**: Common causes and troubleshooting:

| Error Type | Cause | Solution |
|-----------|-------|----------|
| `command_not_found` | Runtime not installed in container (e.g., Node.js) | Use `sse`/`streamable_http` remote mode, or install in container |
| `connection_refused` | Remote MCP service unreachable | Check `server_url` network connectivity |
| `timeout` | MCP service response timeout (30s) | Optimize MCP service performance or check network |
| `handshake_failed` | MCP protocol handshake failure | Verify MCP server version compatibility |
| `placeholder_env` | Environment variables not replaced | Fill in real API Key / path |

### Q21: stdio MCP reports command_not_found?

**A**: The Agent container is based on `python:3.12-slim` and **does not pre-install Node.js/npm/npx**. Therefore `npx`-based MCPs (e.g., `@modelcontextprotocol/server-git`) are unavailable. Solutions:

1. **Use remote mode**: Deploy MCP as independent `sse`/`streamable_http` service
2. **Custom Dockerfile**: Install Node.js in Agent image (not recommended, increases image size)
3. **Use Python MCP**: Choose Python-based MCP Server implementations

---

## Performance

### Q22: How does context compression work?

**A**: Single session context limit is `CONTEXT_MAX_CHARS = 800,000` (~200K tokens):

- **Threshold detection**: `compress_if_needed()` called before each session write
- **Compression strategy**: Sliding window + LLM summary; keeps recent 25% messages as original, rest summarized
- **Async execution**: Compression runs in background `asyncio.create_task`, doesn't block user response
- **Embeddings**: Uses local FastEmbed (bge-small-zh-v1.5, 512 dims), doesn't depend on remote TEI

### Q23: How does Embeddings caching work?

**A**: Embeddings calls have two cache layers:

1. **Redis cache**: TTL cache based on content hash; high hit rate significantly reduces TEI calls
2. **LRU eviction**: LRU strategy when Redis memory is insufficient

Additionally, context compression uses a separate FastEmbed local model (ONNX INT8 quantized) to avoid competing with remote TEI (768 dims) used for Qdrant indexing.

### Q24: How to optimize report generation speed?

**A**:
- **Choose appropriate report type**: `basic_report` fastest (~30-60s), `deep_research` slowest (~3-5 min)
- **Enable Rerank**: `rerank_enabled=true` improves retrieval quality but slows down; enable as needed
- **Adjust concurrent scraping**: `MAX_SCRAPER_WORKERS=15` (default); increase if network is good
- **Use FAST_LLM**: For speed-sensitive scenarios, set `SMART_LLM` to a faster model
- **Cache hits**: Same-query MCP tool calls have TTL cache

### Q25: How much does a single research report cost?

**A**: Default DeepSeek stack + Zhipu free tier plan costs approximately **¥0.18** per report:

- `basic_report`: ~¥0.05-0.10
- `detailed_report`: ~¥0.15-0.25
- `deep_research`: ~¥0.30-0.50

Cost tracking uses LiteLLM's 25+ model pricing table accumulated per step; view in response's `usage.cost_usd` field.
