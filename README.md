# agentinsight-researcher

> **中文优先的研究分析智能体** | **Chinese-first research analysis agent**

[![Python](https://img.shields.io/badge/Python-≥3.11-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-≥0.115-green.svg)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-≥1.2-purple.svg)](https://langchain-ai.github.io/langgraph/)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[中文](#中文) | [English](#english)

---

## 中文

## 🌐 在线体验(官方 Demo)

无需部署,直接访问官方在线 Demo **测试页面**即可体验全部能力:

| 入口 | 地址 | 说明 |
|------|------|------|
| **测试页面(推荐)** ⭐ | http://119.91.32.102/agent/agentinsight-researcher/ | 内置前端联调页面,支持会话管理、流式渲染、工具调用展示、文件上传、报告下载 |
| **健康检查** | http://119.91.32.102/agent/agentinsight-researcher/health | 容器与服务健康状态 |
| **Agent 发现** | http://119.91.32.102/agent/agentinsight-researcher/.well-known/agent-discovery.json | Agent Discovery Protocol 公开元信息 |

> 💡 **使用提示**:
> - **推荐通过测试页面试用**:打开页面即可零配置体验研究报告生成、流式渲染、多 Agent 协作等全部能力,无需手动构造 API 请求。
> - Demo 环境为 `SELF_HOST=False` 云托管模式,测试页面会引导鉴权与点数校验,无需手动处理 Bearer Token。
> - 若返回 502,通常是 Demo 后端临时维护,稍后重试即可。
> - **官方 Demo 仅建议通过页面试用,不建议直接调用 API**;API 直连调用(如 curl/SDK)请使用本地部署 `localhost:8066`(见下文「快速开始」)。

### 30 秒上手 Demo

1. **打开测试页面**:浏览器访问 http://119.91.32.102/agent/agentinsight-researcher/
2. **(可选)填写 Bearer JWT Token**:页面顶部 Token 输入框(从 [AgentInsight 平台](https://agentinsight.goldebridge.com/platform) 登录后获取);留空则以匿名用户身份试用。
3. **输入研究问题**:在消息输入框输入如 `分析2026年中国新能源汽车市场格局`,Enter 发送。
4. **观察流式渲染**:报告逐段流式输出,工具调用与检索来源以折叠面板展示。
5. **下载报告**:报告生成完成后,可通过页面下载 Markdown / HTML / PDF / DOCX / JSON 多种格式。

---

## ⚠️ 重要:开始使用前请先完成以下步骤

### 第 1 步:注册 AgentInsight 平台账户并创建 API Key

本项目使用 [AgentInsight](https://agentinsight.goldebridge.com) 作为可观测性后端,**必须先注册账户并获取 APIKey 才能正常运行**。

1. 访问 **[https://agentinsight.goldebridge.com/platform](https://agentinsight.goldebridge.com/platform)** 注册用户账户
2. 登录后进入「项目设置 → API Key 管理」页面,创建新的 APIKey
3. 创建后会获得一对密钥:
   - **PublicKey**(以 `pk-` 开头)
   - **SecretKey**(以 `sk-` 开头,仅创建时显示一次,请妥善保存)

### 第 2 步:配置环境变量

将获得的密钥配置到 `.env` 文件(从 `.env.template` 复制):

```bash
copy .env.template .env
```

编辑 `.env`,填入以下必填项:

```env
# AgentInsight 可观测性密钥 (必填)
AGENTINSIGHT_PUBLIC_KEY=pk-你的PublicKey
AGENTINSIGHT_SECRET_KEY=sk-你的SecretKey
AGENTINSIGHT_HOST=https://agentinsight.goldebridge.com

# LLM API Key (至少配置一个)
DEEPSEEK_API_KEY=sk-你的DeepSeek密钥
ZHIPU_API_KEY=你的智谱密钥

# 搜索引擎 API Key (至少配置一个)
BOCHA_API_KEY=sk-你的博查密钥

# 数据库密码 (生产环境必填)
POSTGRES_PASSWORD=你的Postgres密码
REDIS_AUTH=你的Redis密码

# Qdrant 静态 API Key (生产环境必填)
QDRANT_API_KEY=sk-你的Qdrant密钥
```

> 🔒 **安全提示**:密钥仅环境变量注入,禁止入仓/硬编码/日志。`.env` 文件已被 `.gitignore` 排除。

### 第 3 步:选择 LLM 分层方案(可选)

本项目默认采用 DeepSeek 全栈 + 智谱免费层方案,单次研究报告成本约 ¥0.18。如需切换为其他方案,修改 `.env` 中的 `FAST_LLM` / `SMART_LLM` / `STRATEGIC_LLM` 即可。

---

## 项目简介

**agentinsight-researcher** 是一个企业级 AI Agent 系统,对外暴露 OpenAI 兼容 API(SSE 流式),支持研究报告生成、混合 RAG 检索、多 Agent 协作、人在回路审核、全链路可观测等能力。

### 核心能力

- 📊 **研究报告生成** — 支持 `basic_report` / `detailed_report` / `deep_research` 三种报告类型,输出 Markdown / HTML / PDF / DOCX / JSON 五种格式
- 🔍 **混合 RAG 检索** — BM25 + 向量 + RRF 融合 + 可选 Rerank
- 🌐 **中文优先多搜索引擎** — 博查/Tavily/Brave/Bing/Google/PubMed/Arxiv 等 15+ 数据源
- 🤖 **多 Agent 协作** — Researcher → Writer → FactChecker → Reviewer → Reviser → Visualizer → Publisher 完整流水线
- 🔧 **MCP 工具协议** — 支持 stdio/sse/streamable_http 三种传输,LLM 自动选工具
- 👨‍💻 **人在回路审核** — WebSocket 实时推送研究计划,用户审核后才继续执行
- 📈 **全链路可观测** — AgentInsight SDK 6 类 trace span(agent/generation/tool/retriever/chain/embedding)
- 🛡️ **企业级安全** — JWT 身份解析 + 三级数据隔离(agent_id + user_id + session_id) + 安全响应头
- 🔌 **Agent 发现协议** — `/.well-known/agent-discovery.json` 公开元信息,支持客户端自动发现

---

## 快速开始

### 环境要求

- **Python** ≥3.11(推荐 3.12)
- **Docker** ≥24.0 + Docker Compose ≥2.20
- **系统**:Windows / macOS / Linux 均可

### 一键起栈

```bash
# 1. 克隆项目
git clone <仓库地址>
cd agentinsight-researcher

# 2. 创建虚拟环境并安装依赖
python -m venv .venv
# Windows
.venv\Scripts\Activate.ps1
# Linux/macOS
source .venv/bin/activate

pip install -U pip -r requirements.txt

# 3. 配置环境变量(必填项见上文「第 2 步」)
copy .env.template .env
# 编辑 .env 填入 APIKey

# 4. 启动容器栈
docker compose -p agentinsight up -d

# 5. 等待全部健康
docker compose -p agentinsight ps
# 全部显示 (healthy) 即可

# 6. 访问测试页面
# 浏览器打开 http://localhost:8066
```

---

## API 完整文档

> Base URL:
> - 本地部署:`http://localhost:8066`
> - 官方 Demo:`http://119.91.32.102/agent/agentinsight-researcher`
>
> 下文示例以 `${BASE_URL}` 表示,使用前请替换为对应地址:
> ```bash
> # Bash
> BASE_URL=http://localhost:8066
> # PowerShell
> $env:BASE_URL="http://localhost:8066"
> ```

### API 端点总览

| # | 端点 | 方法 | 鉴权 | 用途 |
|---|------|------|------|------|
| 1 | `/health` | GET | 匿名 | 健康检查 |
| 2 | `/.well-known/agent-discovery.json` | GET | 匿名 | Agent Discovery Protocol 公开元信息 |
| 3 | `/v1/models` | GET | 匿名 | OpenAI 兼容模型列表 |
| 4 | `/v1/chat/completions` | POST | Bearer(可选)* | OpenAI 兼容研究端点(流式 SSE + 非流式) |
| 5 | `/v1/files` | POST | Bearer(可选) | 文件上传(研究数据源) |
| 6 | `/v1/feedback` | POST | Bearer(可选) | 人在回路审核反馈提交 |
| 7 | `/v1/ws/{session_id}` | WS | Bearer(可选)** | WebSocket 双向实时通道 |
| 8 | `/v1/reports/session/{session_id}` | GET | Bearer(可选) | 列出会话内所有报告 |
| 9 | `/v1/reports/{report_id}/download` | GET | Bearer(可选) | 下载报告(markdown/html/pdf/docx/json) |
| 10 | `/v1/mcp` | GET | Bearer(可选) | 列出当前用户 MCP 配置 |
| 11 | `/v1/mcp` | POST | Bearer(可选) | 创建 MCP 配置(自动测试可用性) |
| 12 | `/v1/mcp/{config_id}` | PUT | Bearer(可选) | 更新 MCP 配置(启用时强制测试) |
| 13 | `/v1/mcp/{config_id}` | DELETE | Bearer(可选) | 删除 MCP 配置 |
| 14 | `/v1/mcp/test` | POST | Bearer(可选) | 测试未保存的 MCP 配置 |
| 15 | `/v1/mcp/{config_id}/test` | POST | Bearer(可选) | 测试已保存的 MCP 配置 |
| 16 | `/v1/mcp/system` | GET | Bearer(可选) | 列出系统公用 MCP 配置 |
| 17 | `/v1/mcp/system/{config_id}/clone` | POST | Bearer(可选) | 克隆系统 MCP 到用户私有列表 |
| 18 | `/` | GET | 匿名 | 前端测试页面(`ENABLE_TEST_PAGE=true`) |
| 19 | `/docs` | GET | 匿名 | Swagger 文档(仅 `ENV=dev`) |

> \* `SELF_HOST=True`(默认)时 token 可选,缺失降级 `DEFAULT_USER_ID`;`SELF_HOST=False` 时研究端点强制要求 Bearer Token + `org_id`/`project_id`。
> \*\* WebSocket 在 `ENV=prod` 强制 Origin + JWT 校验;`ENV=dev` 可放宽。

---

### 1. 健康检查 `GET /health`

**用途**:容器编排健康检查,负载均衡探活。返回服务状态与版本号。

**Request**

```http
GET /health
```

无请求体,无鉴权。

**Response** `200 OK`

```json
{
  "status": "ok",
  "service": "agentinsight-researcher",
  "version": "0.1.0"
}
```

**示例**

```bash
curl ${BASE_URL}/health
```

```powershell
curl.exe ${BASE_URL}/health
```

---

### 2. Agent 发现协议 `GET /.well-known/agent-discovery.json`

**用途**:Agent Discovery Protocol 公开元信息,供客户端自动发现 Agent 能力、服务清单与鉴权方式。

**Request**

```http
GET /.well-known/agent-discovery.json
```

无鉴权。

**Response** `200 OK`

```json
{
  "name": "agentinsight-researcher",
  "version": "0.1.0",
  "description": "中文优先的研究分析智能体, 对标 GPT Researcher",
  "services": [
    {"name": "research", "path": "/v1/chat/completions", "method": "POST", "description": "OpenAI 兼容研究端点 (流式 SSE + 非流式)"},
    {"name": "files", "path": "/v1/files", "method": "POST", "description": "文件上传端点 (作为研究数据源)"},
    {"name": "health", "path": "/health", "method": "GET", "description": "健康检查端点"},
    {"name": "feedback", "path": "/v1/feedback", "method": "POST", "description": "用户反馈端点"},
    {"name": "websocket", "path": "/v1/ws/{session_id}", "method": "WS", "description": "WebSocket 流式会话端点"}
  ],
  "capabilities": ["deep_research", "multi_agent", "hybrid_retrieval", "mcp_tools", "human_in_loop", "fact_check", "image_generation"],
  "auth": ["bearer_jwt", "none"]
}
```

**示例**

```bash
curl ${BASE_URL}/.well-known/agent-discovery.json
```

---

### 3. 模型列表 `GET /v1/models`

**用途**:OpenAI 兼容模型列表,客户端集成 SDK 时自动发现可用模型。

**Request**

```http
GET /v1/models
```

**Response** `200 OK`

```json
{
  "object": "list",
  "data": [
    {
      "id": "agentinsight-researcher",
      "object": "model",
      "created": 1783334100,
      "owned_by": "agentinsight"
    }
  ]
}
```

**示例**

```bash
curl ${BASE_URL}/v1/models
```

---

### 4. 研究端点 `POST /v1/chat/completions` ⭐ 核心

**用途**:OpenAI 兼容研究端点,支持流式 SSE 与非流式两种模式。根据查询意图自动路由:
- **RESEARCH**:走 LangGraph 研究流水线(报告生成)
- **CHAT**:走 chat graph(对话追问,复用会话历史)
- **SHORT_QUERY / OFF_TOPIC**:走 ChitchatResponder(FAST_LLM 人性化回复,不走 graph)

**Request Body**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `model` | string | 是 | 固定 `agentinsight-researcher` |
| `messages` | array | 是 | OpenAI 兼容消息列表,最后一条 `user` 消息作为查询 |
| `stream` | bool | 否 | `true`=SSE 流式,`false`=非流式(默认 false) |
| `temperature` | float | 否 | 采样温度(可选) |
| `max_tokens` | int | 否 | 最大生成 token(可选) |
| `report_type` | string | 否 | `basic_report`(默认) / `detailed_report` / `deep_research` / `summary` / `subtopics` |
| `report_format` | string | 否 | `markdown`(默认) / `html` / `pdf` / `docx` / `json` |
| `tone` | string | 否 | `objective`(默认) / `analytical` / `opinionated` / `casual` |
| `session_id` | string | 否 | 会话 ID(thread_id),不传则自动生成 UUID |
| `uploaded_files` | array | 否 | 已上传文件 ID 列表(来自 `POST /v1/files`) |
| `multi_agent` | bool | 否 | 是否启用多 Agent Supervisor 模式(默认 false) |
| `agent_role` | string | 否 | 自定义行业 persona(优先级高于 LLM 自动生成) |
| `query_domains` | array | 否 | 域名过滤白名单(仅检索这些域名) |
| `org_id` | string | 否 | 组织 ID(SELF_HOST=False 时点数校验用,优先于 project_id) |
| `project_id` | string | 否 | 项目 ID(SELF_HOST=False 时点数校验用) |

**请求头**

| Header | 说明 |
|--------|------|
| `Authorization: Bearer <jwt_token>` | 可选(SELF_HOST=False 时研究端点必需) |
| `Content-Type: application/json` | 必填 |
| `X-Session-Id: <session_id>` | 可选,会话 ID 透传 |

#### 4.1 非流式响应

**Response** `200 OK`

```json
{
  "id": "chatcmpl-3f8b2a1c4d5e6f7a8b9c0d1e2f3a4b5c",
  "object": "chat.completion",
  "created": 1783334100,
  "model": "agentinsight-researcher",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "# 2026年中国新能源汽车市场格局分析\n\n..."},
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 25,
    "completion_tokens": 1820,
    "total_tokens": 1845,
    "cost_usd": 0.0182
  },
  "sources": [
    {"title": "中汽协:2026年新能源汽车销量预测", "url": "https://example.com/news1", "snippet": "...", "score": 0.92}
  ],
  "report_format": "markdown",
  "file_path": null,
  "report_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

**示例(非流式)**

```bash
curl -X POST ${BASE_URL}/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agentinsight-researcher",
    "stream": false,
    "messages": [{"role": "user", "content": "分析2026年中国新能源汽车市场格局"}],
    "report_type": "basic_report",
    "report_format": "markdown",
    "tone": "analytical"
  }'
```

**PowerShell(避免 curl 别名陷阱)**

```powershell
curl.exe --% -X POST %BASE_URL%/v1/chat/completions -H "Content-Type: application/json" -d "{\"model\":\"agentinsight-researcher\",\"stream\":false,\"messages\":[{\"role\":\"user\",\"content\":\"分析2026年中国新能源汽车市场格局\"}]}"
```

#### 4.2 流式 SSE 响应

**Response** `200 OK` `Content-Type: text/event-stream`

SSE 帧格式(每帧以 `data: ` 前缀,以 `\n\n` 结尾):

```
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1783334100,"model":"agentinsight-researcher","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1783334100,"model":"agentinsight-researcher","choices":[{"index":0,"delta":{"content":"\n\n> **[生成研究角色]** 已生成研究角色: 金融分析师\n"},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1783334100,"model":"agentinsight-researcher","choices":[{"index":0,"delta":{"content":"# 2026年中国新能源汽车市场格局\n\n"},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1783334100,"model":"agentinsight-researcher","choices":[{"index":0,"delta":{"sources":[{"title":"...","url":"...","snippet":"...","score":0.92}]},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1783334100,"model":"agentinsight-researcher","choices":[{"index":0,"delta":{"report_id":"a1b2c3d4-..."},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1783334100,"model":"agentinsight-researcher","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

`delta` 字段可能含:`role` / `content` / `progress` / `sources` / `report_id` / `file_path` / `report_format`。

**示例(流式)**

```bash
curl -N -X POST ${BASE_URL}/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agentinsight-researcher",
    "stream": true,
    "messages": [{"role": "user", "content": "对比 React 与 Vue 3 在企业级应用的优劣"}],
    "report_type": "detailed_report"
  }'
```

#### 4.3 错误响应

| HTTP | 含义 | 触发条件 |
|------|------|---------|
| 400 | Bad Request | `messages` 无 user 消息 / 查询内容为空 |
| 401 | Unauthorized | SELF_HOST=False 时缺 Bearer Token 或 token 校验失败 |
| 429 | Too Many Requests | 本月 Agent 调用次数已达上限(SELF_HOST=False) |
| 413 | Payload Too Large | 文件上传超限(仅 `/v1/files`) |

```json
{"detail": "messages 必须包含至少一条 user 消息"}
```

#### 4.4 Python SDK 调用示例

```python
import httpx
import json

async with httpx.AsyncClient(base_url="http://localhost:8066", timeout=300) as client:
    # 流式请求
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "agentinsight-researcher",
            "stream": True,
            "messages": [{"role": "user", "content": "分析半导体行业 2026 年趋势"}],
            "report_type": "detailed_report",
            "report_format": "markdown",
            "tone": "analytical",
        },
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                chunk = json.loads(line[6:])
                delta = chunk["choices"][0]["delta"].get("content", "")
                print(delta, end="", flush=True)
```

#### 4.5 OpenAI Python SDK 兼容调用

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8066/v1",
    api_key="any-string-not-used-in-self-host-mode",
)

# 非流式
resp = client.chat.completions.create(
    model="agentinsight-researcher",
    messages=[{"role": "user", "content": "分析2026年中国新能源汽车市场格局"}],
    stream=False,
    extra_body={"report_type": "basic_report", "report_format": "markdown"},
)
print(resp.choices[0].message.content)
print(f"Sources: {resp.sources}")
print(f"Report ID: {resp.report_id}")

# 流式
for chunk in client.chat.completions.create(
    model="agentinsight-researcher",
    messages=[{"role": "user", "content": "对比 React 与 Vue 3"}],
    stream=True,
    extra_body={"report_type": "detailed_report"},
):
    delta = chunk.choices[0].delta.content or ""
    print(delta, end="", flush=True)
```

---

### 5. 文件上传 `POST /v1/files`

**用途**:上传文件作为研究数据源,文件 ID 可在 `/v1/chat/completions` 的 `uploaded_files` 字段引用。

**Request**

```http
POST /v1/files
Content-Type: multipart/form-data
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | file | 是 | 上传的文件,扩展名必须在白名单内 |

**支持扩展名白名单**:`pdf` / `docx` / `md` / `txt` / `html` / `csv` / `xlsx` / `pptx`

**大小限制**:由 `MAX_UPLOAD_SIZE_MB` 配置(默认 50MB),超限返回 413。

**Response** `201 Created`

```json
{
  "file_id": "agentinsight-researcher:user_abc123:def0123456789abc",
  "filename": "industry_report.pdf",
  "size_bytes": 5242880,
  "size_mb": 5.0,
  "extension": "pdf",
  "uploaded_at": 1783334100
}
```

**示例**

```bash
curl -X POST ${BASE_URL}/v1/files \
  -F "file=@/path/to/industry_report.pdf"
```

```powershell
curl.exe -X POST %BASE_URL%/v1/files -F "file=@C:\path\to\industry_report.pdf"
```

**错误响应**

| HTTP | 含义 |
|------|------|
| 413 | 文件大小超限 |
| 415 | 不支持的文件类型 |

---

### 6. 人在回路反馈 `POST /v1/feedback`

**用途**:提交对研究计划/大纲的审核反馈,解决 HumanAgent 节点的等待 Future。仅 `human_review_enabled=True` 时使用。

**Request Body**

```json
{
  "session_id": "your-session-id",
  "feedback": "approve"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | 是 | 会话 ID(thread_id),与研究请求一致 |
| `feedback` | string | 是 | 空字符串或 `approve`/`accept`/`通过` 等关键词表示接受;其他内容视为修订意见 |

**Response** `200 OK`

```json
{
  "session_id": "your-session-id",
  "submitted": true,
  "submitted_at": 1783334100
}
```

**错误响应** `404 Not Found`(无待处理的反馈请求)

```json
{"detail": "无待处理的反馈请求 (session_id 可能无效或反馈已提交)"}
```

**示例**

```bash
curl -X POST ${BASE_URL}/v1/feedback \
  -H "Content-Type: application/json" \
  -d '{"session_id":"your-session-id","feedback":"approve"}'
```

---

### 7. WebSocket 双向通道 `WS /v1/ws/{session_id}`

**用途**:WebSocket 增强通道,接收 8 类结构化消息,接收用户反馈。SSE 仍是主通道,WebSocket 用于人在回路审核请求推送与实时进度结构化推送。

**连接**

```
ws://${BASE_URL_HOST}/v1/ws/{session_id}
```

- 路径参数 `session_id` 即 thread_id,做会话隔离键
- `ENV=prod` 强制 Origin 校验(防 CSWSH)+ JWT Token 校验
- Token 可通过 query 参数 `?token=<jwt>` 或 `Authorization: Bearer <jwt>` 头传递

**服务端推送 8 类消息**

| `type` | 说明 |
|--------|------|
| `logs` | 日志信息 |
| `content` | 内容块(报告正文流式) |
| `node_progress` | 节点进度 |
| `sources` | 检索来源 |
| `tool_call` | 工具调用 |
| `report` | 完整报告 |
| `human_feedback_request` | 人在回路审核请求 |
| `error` | 错误信息 |

**客户端发送消息**

| `type` | 说明 |
|--------|------|
| `ping` | 心跳,服务端回 `{"type":"pong"}` |
| `human_feedback` | 提交反馈,`{"type":"human_feedback","feedback":"approve"}` |

**示例(JavaScript)**

```javascript
const ws = new WebSocket("ws://localhost:8066/v1/ws/your-session-id");

ws.onopen = () => {
  console.log("WebSocket 已连接");
  ws.send(JSON.stringify({ type: "ping" }));
};

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  console.log(`[${msg.type}]`, msg);

  if (msg.type === "human_feedback_request") {
    // 收到审核请求,用户确认后提交反馈
    ws.send(JSON.stringify({
      type: "human_feedback",
      feedback: "approve"  // 或修订意见
    }));
  }
};

ws.onclose = (event) => {
  console.log(`WebSocket 关闭: code=${event.code} reason=${event.reason}`);
};
```

**关闭码**

| Code | 含义 |
|------|------|
| 1000 | 正常关闭(被同 session 新连接替换) |
| 1008 | WebSocket 未启用(`websocket_enabled=False`) |
| 4001 | 缺少 token 或 token 无效(prod 强制) |
| 4003 | Origin 不在白名单(prod 强制) |

---

### 8. 列出会话报告 `GET /v1/reports/session/{session_id}`

**用途**:一个 session 可生成多个报告,返回按 `created_at DESC` 排序的报告列表(不含报告全文,减少传输)。

**Request**

```http
GET /v1/reports/session/{session_id}
```

**Response** `200 OK`

```json
[
  {
    "report_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "session_id": "your-session-id",
    "query": "分析2026年中国新能源汽车市场格局",
    "report_format": "markdown",
    "agent_role": "金融分析师",
    "created_at": "2026-07-06T10:30:00Z",
    "updated_at": "2026-07-06T10:35:00Z"
  }
]
```

**示例**

```bash
curl ${BASE_URL}/v1/reports/session/your-session-id
```

---

### 9. 下载报告 `GET /v1/reports/{report_id}/download`

**用途**:按 `report_id` 下载报告,支持 5 种格式实时转换。向后兼容:若 `report_id` 未匹配,会尝试作为 `session_id` 查询最新报告(响应头 `X-Deprecated: true` 提示迁移)。

**Request**

```http
GET /v1/reports/{report_id}/download?format=markdown
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `format` | string | 否 | `markdown`(默认) / `html` / `pdf` / `docx` / `json` |

**Response**

| format | Content-Type | Content-Disposition |
|--------|--------------|---------------------|
| `markdown` | `text/markdown` | `attachment; filename=report_{report_id}.md` |
| `html` | `text/html` | `attachment; filename=report_{report_id}.html` |
| `pdf` | `application/pdf` | `attachment; filename=report_{report_id}.pdf` |
| `docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | `attachment; filename=report_{report_id}.docx` |
| `json` | `application/json` | `attachment; filename=report_{report_id}.json` |

**示例**

```bash
# 下载 Markdown
curl -OJ ${BASE_URL}/v1/reports/a1b2c3d4-e5f6-7890-abcd-ef1234567890/download?format=markdown

# 下载 PDF
curl -OJ ${BASE_URL}/v1/reports/a1b2c3d4-e5f6-7890-abcd-ef1234567890/download?format=pdf

# 下载 DOCX
curl -OJ ${BASE_URL}/v1/reports/a1b2c3d4-e5f6-7890-abcd-ef1234567890/download?format=docx
```

**错误响应**

| HTTP | 含义 |
|------|------|
| 403 | 无权访问该报告(用户隔离校验失败) |
| 404 | 报告不存在 / PDF 生成失败 |
| 400 | 不支持的格式 |

---

### 10. MCP 配置管理 API

MCP(Model Context Protocol)工具配置管理,支持三种传输模式:

| 模式 | 说明 | 必填字段 |
|------|------|---------|
| `stdio` | 本地模式,通过 stdin/stdout 与本地进程通信 | `command` |
| `sse` | 远程模式,通过 SSE 连接远程 HTTP 服务器 | `server_url` |
| `streamable_http` | 远程模式,通过 HTTP 流连接远程服务器 | `server_url` |

#### 10.1 列出用户 MCP 配置 `GET /v1/mcp`

**Response** `200 OK`(数组,按 `created_at DESC` 排序,不含系统 MCP)

```json
[
  {
    "id": 1,
    "name": "my-git-mcp",
    "server_url": null,
    "transport_type": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-git"],
    "env_vars": {"GIT_REPO_PATH": "/tmp/repo"},
    "enabled": true,
    "is_system": false,
    "description": "Git 工具",
    "created_at": "2026-07-06T10:00:00Z",
    "updated_at": "2026-07-06T10:00:00Z"
  }
]
```

**示例**

```bash
curl ${BASE_URL}/v1/mcp
```

#### 10.2 创建 MCP 配置 `POST /v1/mcp`

**用途**:创建用户私有 MCP 配置。创建后自动测试可用性,若测试失败且 `enabled=true` 则自动改为 `false`(不阻止添加)。

**Request Body**

```json
{
  "name": "my-git-mcp",
  "transport_type": "stdio",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-git"],
  "env_vars": {"GIT_REPO_PATH": "/tmp/repo"},
  "enabled": true,
  "description": "Git 工具"
}
```

**Response** `200 OK`(返回保存后的配置,含 `id` 与 `test_result`)

```json
{
  "id": 1,
  "name": "my-git-mcp",
  "transport_type": "stdio",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-git"],
  "env_vars": {"GIT_REPO_PATH": "/tmp/repo"},
  "enabled": true,
  "is_system": false,
  "description": "Git 工具",
  "test_result": {
    "success": true,
    "message": "连接成功, 发现 3 个工具",
    "error_type": null,
    "tools_count": 3,
    "tools": ["git_status", "git_log", "git_diff"],
    "latency_ms": 2341
  }
}
```

**示例**

```bash
curl -X POST ${BASE_URL}/v1/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-git-mcp",
    "transport_type": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-git"],
    "env_vars": {"GIT_REPO_PATH": "/tmp/repo"},
    "enabled": true,
    "description": "Git 工具"
  }'
```

#### 10.3 更新 MCP 配置 `PUT /v1/mcp/{config_id}`

**用途**:更新用户私有 MCP 配置。从禁用切到启用时强制测试,失败则拒绝启用(其他字段仍更新,`enabled` 强制为 `false` 并附 `test_result`)。可加 `?skip_test=true` 跳过测试(前端已测试时使用)。

**Request Body**:同 10.2(不含 `id`/`is_system`)

**Response** `200 OK`(更新后的配置,启用失败时含 `test_result`)

**示例**

```bash
curl -X PUT "${BASE_URL}/v1/mcp/1?skip_test=true" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-git-mcp",
    "transport_type": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-git"],
    "env_vars": {"GIT_REPO_PATH": "/tmp/new-repo"},
    "enabled": true,
    "description": "Git 工具(更新)"
  }'
```

#### 10.4 删除 MCP 配置 `DELETE /v1/mcp/{config_id}`

**Response** `200 OK`

```json
{"deleted": true}
```

**示例**

```bash
curl -X DELETE ${BASE_URL}/v1/mcp/1
```

**错误响应** `404`(配置不存在或为系统配置,不可删除)

#### 10.5 测试未保存的 MCP 配置 `POST /v1/mcp/test`

**用途**:前端在保存前预先测试配置是否可用,不入库。30s 超时保护。

**Request Body**:同 10.2

**Response** `200 OK`

```json
{
  "success": true,
  "message": "连接成功, 发现 3 个工具",
  "error_type": null,
  "tools_count": 3,
  "tools": ["git_status", "git_log", "git_diff"],
  "latency_ms": 2341
}
```

**失败响应示例**

```json
{
  "success": false,
  "message": "启动命令不存在: npx (容器未安装 Node.js, npx 类 MCP 不可用)",
  "error_type": "command_not_found",
  "tools_count": 0,
  "tools": [],
  "latency_ms": 12
}
```

`error_type` 枚举:`package_not_found` / `connection_refused` / `timeout` / `handshake_failed` / `command_not_found` / `placeholder_env` / `missing_command` / `missing_url` / `dependency_missing` / `unknown`

**示例**

```bash
curl -X POST ${BASE_URL}/v1/mcp/test \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test-mcp",
    "transport_type": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-git"],
    "enabled": true
  }'
```

#### 10.6 测试已保存的 MCP 配置 `POST /v1/mcp/{config_id}/test`

**用途**:按 ID 查询已保存的配置(含系统 MCP)并测试。

**Response** `200 OK`(同 10.5)

**示例**

```bash
curl -X POST ${BASE_URL}/v1/mcp/1/test
```

#### 10.7 列出系统 MCP 配置 `GET /v1/mcp/system`

**用途**:列出所有系统公用 MCP 配置(用户可查看但不可编辑/删除),来源为 [MCP 官方参考实现](https://github.com/modelcontextprotocol/servers)。

**Response** `200 OK`(数组,按 `name` 排序,`is_system=true`)

**示例**

```bash
curl ${BASE_URL}/v1/mcp/system
```

#### 10.8 克隆系统 MCP `POST /v1/mcp/system/{config_id}/clone`

**用途**:克隆系统 MCP 到当前用户的私有列表(可编辑/删除)。克隆后 `enabled=false`:
- 需 Key 的 MCP:前端打开编辑表单让用户填 Key,保存时测试,通过才启用
- 无需 Key 的 MCP:前端调用 `/v1/mcp/{id}/test`,通过则 PUT `enabled=true`

**Response** `200 OK`(克隆后的用户私有配置,`is_system=false`、`enabled=false`)

**错误响应** `409 Conflict`(已存在同名配置)

```json
{"detail": "已存在同名配置 'git', 请先删除或重命名已有配置"}
```

**示例**

```bash
curl -X POST ${BASE_URL}/v1/mcp/system/5/clone
```

---

## 配置说明

所有配置经 `.env` + 环境变量注入,业务代码不硬编码。完整配置项见 [.env.template](.env.template)。

### 必填配置项

| 配置 | 说明 | 获取方式 |
|------|------|---------|
| `AGENTINSIGHT_PUBLIC_KEY` | AgentInsight PublicKey | [平台注册](https://agentinsight.goldebridge.com/platform) |
| `AGENTINSIGHT_SECRET_KEY` | AgentInsight SecretKey | [平台注册](https://agentinsight.goldebridge.com/platform) |
| `DEEPSEEK_API_KEY` | DeepSeek API Key | [DeepSeek 平台](https://platform.deepseek.com/) |
| `ZHIPU_API_KEY` | 智谱 API Key | [智谱开放平台](https://open.bigmodel.cn/) |
| `BOCHA_API_KEY` | 博查搜索 API Key (国内中文质量高) | [博查搜索](https://bochaai.com/) |
| `METASO_API_KEY` | 秘塔 AI 搜索 API Key (国内 AI 搜索主力, v1.1 新增) | [秘塔 AI 搜索](https://metaso.cn/) |
| `TAVILY_API_KEY` | Tavily 通用搜索 API Key (SimpleQA 第一) | [Tavily](https://tavily.com/) |
| `EXA_API_KEY` | Exa AI 语义搜索 API Key (AI 搜索兜底, v1.1 加入 CN 区域) | [Exa AI](https://exa.ai/) |
| `SERPAPI_KEY` | SerpApi Google 代理 API Key (多引擎) | [SerpApi](https://serpapi.com/) |
| `SERPER_API_KEY` | Serper.dev Google Search API Key (最便宜) | [Serper.dev](https://serper.dev/) |
| `SEARCHAPI_API_KEY` | SearchApi.io 多引擎 SERP API Key | [SearchApi.io](https://www.searchapi.io/) |
| `GITHUB_TOKEN` | GitHub Personal Access Token (代码搜索, 可选) | [GitHub Settings](https://github.com/settings/tokens) |
| `CROSSREF_MAILTO` | CrossRef polite pool 邮箱 (可选, 50 req/s, v1.1 新增) | 自定义邮箱 |
| `UNPAYWALL_EMAIL` | Unpaywall 真实邮箱 (必填, 否则 HTTP 422 拒绝, v1.1 新增) | 自定义邮箱 |
| `POSTGRES_PASSWORD` | PostgreSQL 密码 | 自定义 |
| `QDRANT_API_KEY` | Qdrant 静态 API Key | 自定义 |

### 关键可选配置

| 配置 | 默认 | 说明 |
|------|------|------|
| `SELF_HOST` | `True` | `True`=自托管(token 可选,降级 `DEFAULT_USER_ID`);`False`=云托管(强制 JWT + 点数校验) |
| `DEFAULT_USER_ID` | `anonymous` | 匿名降级用户 ID |
| `ENV` | `dev` | `dev` / `prod`(prod 关闭 docs/openapi) |
| `ENABLE_TEST_PAGE` | `dev=true / prod=false` | 是否挂载前端测试页面 |
| `WEBSOCKET_ENABLED` | `False` | 是否启用 WebSocket 端点 |
| `WS_AUTH_REQUIRED` | `False` | WebSocket 是否强制 JWT(prod 自动开启) |
| `WS_ORIGIN_CHECK` | `False` | WebSocket 是否校验 Origin(prod 自动开启) |
| `HUMAN_REVIEW_ENABLED` | `False` | 是否启用人在回路审核 |
| `DEFAULT_REPORT_TYPE` | `basic_report` | 默认报告类型 |
| `DEFAULT_REPORT_FORMAT` | `markdown` | 默认报告格式 |
| `MAX_UPLOAD_SIZE_MB` | `50` | 文件上传大小上限 |
| `CORS_ALLOW_ORIGINS` | `http://localhost:8066` | CORS 白名单(逗号分隔) |
| `CHAT_REQUIRES_REPORT` | `True` | CHAT 意图首轮无报告时降级 OFF_TOPIC |

---

## 常用命令

```bash
# 启动容器栈
docker compose -p agentinsight up -d

# 查看日志
docker compose -p agentinsight logs -f agent

# 停止
docker compose -p agentinsight down

# 停止并清理数据卷(慎用)
docker compose -p agentinsight down -v
```

> 💡 使用 `-p agentinsight` 项目名(容器名 `agentinsight-<service>-1`),与 AgentInsightService 项目共享命名空间,两个项目不并行运行。

---

## 贡献

欢迎贡献!请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 了解开发流程,遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) 行为准则。

---

## 许可证

[MIT License](LICENSE) — 商业友好,允许修改、分发、商用,但需保留版权声明和许可证。

---
---

## English

## 🌐 Online Demo (Official)

Try all capabilities without deployment via the official online demo **test page**:

| Entry | URL | Description |
|-------|-----|-------------|
| **Test Page (Recommended)** ⭐ | http://119.91.32.102/agent/agentinsight-researcher/ | Built-in frontend debug page with session management, streaming rendering, tool call display, file upload, report download |
| **Health Check** | http://119.91.32.102/agent/agentinsight-researcher/health | Container and service health status |
| **Agent Discovery** | http://119.91.32.102/agent/agentinsight-researcher/.well-known/agent-discovery.json | Agent Discovery Protocol public metadata |

> 💡 **Usage Notes**:
> - **Recommended to try via the test page**: open the page to experience research report generation, streaming rendering, multi-agent collaboration, and all capabilities with zero configuration, no need to manually construct API requests.
> - The demo runs in `SELF_HOST=False` cloud-hosted mode; the test page handles auth and quota validation, no need to manually handle Bearer Token.
> - If a 502 is returned, the demo backend is temporarily under maintenance; please retry later.
> - **The official demo is only recommended for page trial, not for direct API calls**; for direct API calls (curl/SDK), use local deployment `localhost:8066` (see "Quick Start" below).

### 30-Second Quick Start with Demo

1. **Open the test page**: visit http://119.91.32.102/agent/agentinsight-researcher/ in your browser
2. **(Optional) Fill in Bearer JWT Token**: in the Token input at the top of the page (obtained after logging into the [AgentInsight platform](https://agentinsight.goldebridge.com/platform)); leave empty to try as an anonymous user.
3. **Enter a research question**: type a query like `Analyze the 2026 China new energy vehicle market landscape` in the message input box and press Enter.
4. **Watch streaming rendering**: the report streams out paragraph by paragraph; tool calls and retrieval sources are shown in collapsible panels.
5. **Download the report**: after generation, download the report in Markdown / HTML / PDF / DOCX / JSON formats from the page.

---

## ⚠️ Important: Please complete the following steps before getting started

### Step 1: Register an AgentInsight platform account and create an APIKey

This project uses [AgentInsight](https://agentinsight.goldebridge.com) as the observability backend. **You must register an account and obtain an APIKey before the project can run.**

1. Visit **[https://agentinsight.goldebridge.com/platform](https://agentinsight.goldebridge.com/platform)** to register a user account
2. After logging in, go to "Project Settings → API Key Management" page to create a new APIKey
3. You will receive a pair of keys:
   - **PublicKey** (starts with `pk-`)
   - **SecretKey** (starts with `sk-`, shown only once at creation, please save it securely)

### Step 2: Configure environment variables

Configure the obtained keys in the `.env` file (copied from `.env.template`):

```bash
copy .env.template .env
```

Edit `.env` and fill in the following required fields:

```env
# AgentInsight observability keys (required)
AGENTINSIGHT_PUBLIC_KEY=pk-your-PublicKey
AGENTINSIGHT_SECRET_KEY=sk-your-SecretKey
AGENTINSIGHT_HOST=https://agentinsight.goldebridge.com

# LLM API Key (configure at least one)
DEEPSEEK_API_KEY=sk-your-DeepSeek-key
ZHIPU_API_KEY=your-Zhipu-key

# Search engine API Key (configure at least one)
BOCHA_API_KEY=sk-your-Bocha-key

# Database password (required for production)
POSTGRES_PASSWORD=your-Postgres-password
REDIS_AUTH=your-Redis-password

# Qdrant static API Key (required for production)
QDRANT_API_KEY=sk-your-Qdrant-key
```

> 🔒 **Security note**: Keys are injected via environment variables only. Hardcoding keys in code/committing to repo/logging is prohibited. The `.env` file is excluded by `.gitignore`.

### Step 3: Choose an LLM tiered plan (optional)

This project defaults to a DeepSeek stack + Zhipu free tier plan, with a single research report cost of approximately ¥0.18. To switch to another plan, modify `FAST_LLM` / `SMART_LLM` / `STRATEGIC_LLM` in `.env`.

---

## Project Introduction

**agentinsight-researcher** is an enterprise-grade AI Agent system that exposes an OpenAI-compatible API (SSE streaming), supporting research report generation, hybrid RAG retrieval, multi-agent collaboration, human-in-the-loop review, full-link observability, and more.

### Core Capabilities

- 📊 **Research Report Generation** — Supports `basic_report` / `detailed_report` / `deep_research` report types, output in Markdown / HTML / PDF / DOCX / JSON formats
- 🔍 **Hybrid RAG Retrieval** — BM25 + Vector + RRF fusion + optional Rerank
- 🌐 **Chinese-first Multi-Search Engine** — Bocha/Tavily/Brave/Bing/Google/PubMed/Arxiv and 15+ data sources
- 🤖 **Multi-Agent Collaboration** — Researcher → Writer → FactChecker → Reviewer → Reviser → Visualizer → Publisher pipeline
- 🔧 **MCP Tool Protocol** — Supports stdio/sse/streamable_http transports, LLM auto-selects tools
- 👨‍💻 **Human-in-the-Loop Review** — WebSocket real-time push of research plans, continues only after user approval
- 📈 **Full-Link Observability** — AgentInsight SDK 6 types of trace spans (agent/generation/tool/retriever/chain/embedding)
- 🛡️ **Enterprise-Grade Security** — JWT identity + three-tier data isolation (agent_id + user_id + session_id) + security response headers
- 🔌 **Agent Discovery Protocol** — `/.well-known/agent-discovery.json` public metadata for client auto-discovery

---

## Quick Start

### Requirements

- **Python** ≥3.11 (3.12 recommended)
- **Docker** ≥24.0 + Docker Compose ≥2.20
- **OS**: Windows / macOS / Linux

### One-Click Stack

```bash
# 1. Clone the project
git clone <repository-url>
cd agentinsight-researcher

# 2. Create virtual environment and install dependencies
python -m venv .venv
# Windows
.venv\Scripts\Activate.ps1
# Linux/macOS
source .venv/bin/activate

pip install -U pip -r requirements.txt

# 3. Configure environment variables (required fields in "Step 2" above)
copy .env.template .env
# Edit .env and fill in APIKey

# 4. Start the container stack
docker compose -p agentinsight up -d

# 5. Wait for all services to be healthy
docker compose -p agentinsight ps
# All showing (healthy) means ready

# 6. Access the test page
# Open http://localhost:8066 in your browser
```

---

## Complete API Documentation

> Base URL:
> - Local deployment: `http://localhost:8066`
> - Official demo: `http://119.91.32.102/agent/agentinsight-researcher`
>
> Examples below use `${BASE_URL}`; replace it before use:
> ```bash
> # Bash
> BASE_URL=http://localhost:8066
> # PowerShell
> $env:BASE_URL="http://localhost:8066"
> ```

### API Endpoint Overview

| # | Endpoint | Method | Auth | Purpose |
|---|----------|--------|------|---------|
| 1 | `/health` | GET | Anonymous | Health check |
| 2 | `/.well-known/agent-discovery.json` | GET | Anonymous | Agent Discovery Protocol public metadata |
| 3 | `/v1/models` | GET | Anonymous | OpenAI-compatible model list |
| 4 | `/v1/chat/completions` | POST | Bearer (optional)* | OpenAI-compatible research endpoint (streaming SSE + non-streaming) |
| 5 | `/v1/files` | POST | Bearer (optional) | File upload (research data source) |
| 6 | `/v1/feedback` | POST | Bearer (optional) | Human-in-the-loop feedback submission |
| 7 | `/v1/ws/{session_id}` | WS | Bearer (optional)** | WebSocket bidirectional real-time channel |
| 8 | `/v1/reports/session/{session_id}` | GET | Bearer (optional) | List all reports in a session |
| 9 | `/v1/reports/{report_id}/download` | GET | Bearer (optional) | Download report (markdown/html/pdf/docx/json) |
| 10 | `/v1/mcp` | GET | Bearer (optional) | List current user's MCP configs |
| 11 | `/v1/mcp` | POST | Bearer (optional) | Create MCP config (auto-test availability) |
| 12 | `/v1/mcp/{config_id}` | PUT | Bearer (optional) | Update MCP config (force test on enable) |
| 13 | `/v1/mcp/{config_id}` | DELETE | Bearer (optional) | Delete MCP config |
| 14 | `/v1/mcp/test` | POST | Bearer (optional) | Test unsaved MCP config |
| 15 | `/v1/mcp/{config_id}/test` | POST | Bearer (optional) | Test saved MCP config |
| 16 | `/v1/mcp/system` | GET | Bearer (optional) | List system public MCP configs |
| 17 | `/v1/mcp/system/{config_id}/clone` | POST | Bearer (optional) | Clone system MCP to user's private list |
| 18 | `/` | GET | Anonymous | Frontend test page (when `ENABLE_TEST_PAGE=true`) |
| 19 | `/docs` | GET | Anonymous | Swagger docs (only `ENV=dev`) |

> \* `SELF_HOST=True` (default): token optional, degrades to `DEFAULT_USER_ID` if missing; `SELF_HOST=False`: research endpoint strictly requires Bearer Token + `org_id`/`project_id`.
> \*\* WebSocket forces Origin + JWT validation in `ENV=prod`; can be relaxed in `ENV=dev`.

---

### 1. Health Check `GET /health`

**Purpose**: Container orchestration health check, load balancer probing. Returns service status and version.

**Request**

```http
GET /health
```

No body, no auth.

**Response** `200 OK`

```json
{
  "status": "ok",
  "service": "agentinsight-researcher",
  "version": "0.1.0"
}
```

**Example**

```bash
curl ${BASE_URL}/health
```

---

### 2. Agent Discovery Protocol `GET /.well-known/agent-discovery.json`

**Purpose**: Agent Discovery Protocol public metadata for client auto-discovery of agent capabilities, services, and auth.

**Request**

```http
GET /.well-known/agent-discovery.json
```

No auth.

**Response** `200 OK`

```json
{
  "name": "agentinsight-researcher",
  "version": "0.1.0",
  "description": "Chinese-first research analysis agent, benchmarked against GPT Researcher",
  "services": [
    {"name": "research", "path": "/v1/chat/completions", "method": "POST", "description": "OpenAI-compatible research endpoint (streaming SSE + non-streaming)"},
    {"name": "files", "path": "/v1/files", "method": "POST", "description": "File upload endpoint (research data source)"},
    {"name": "health", "path": "/health", "method": "GET", "description": "Health check endpoint"},
    {"name": "feedback", "path": "/v1/feedback", "method": "POST", "description": "User feedback endpoint"},
    {"name": "websocket", "path": "/v1/ws/{session_id}", "method": "WS", "description": "WebSocket streaming session endpoint"}
  ],
  "capabilities": ["deep_research", "multi_agent", "hybrid_retrieval", "mcp_tools", "human_in_loop", "fact_check", "image_generation"],
  "auth": ["bearer_jwt", "none"]
}
```

**Example**

```bash
curl ${BASE_URL}/.well-known/agent-discovery.json
```

---

### 3. Model List `GET /v1/models`

**Purpose**: OpenAI-compatible model list for client SDK auto-discovery.

**Request**

```http
GET /v1/models
```

**Response** `200 OK`

```json
{
  "object": "list",
  "data": [
    {
      "id": "agentinsight-researcher",
      "object": "model",
      "created": 1783334100,
      "owned_by": "agentinsight"
    }
  ]
}
```

**Example**

```bash
curl ${BASE_URL}/v1/models
```

---

### 4. Research Endpoint `POST /v1/chat/completions` ⭐ Core

**Purpose**: OpenAI-compatible research endpoint supporting both streaming SSE and non-streaming. Auto-routes by query intent:
- **RESEARCH**: LangGraph research pipeline (report generation)
- **CHAT**: chat graph (conversational follow-up, reuses session history)
- **SHORT_QUERY / OFF_TOPIC**: ChitchatResponder (FAST_LLM human-like reply, no graph)

**Request Body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | Yes | Fixed `agentinsight-researcher` |
| `messages` | array | Yes | OpenAI-compatible message list; last `user` message used as query |
| `stream` | bool | No | `true`=SSE streaming, `false`=non-streaming (default false) |
| `temperature` | float | No | Sampling temperature (optional) |
| `max_tokens` | int | No | Max generation tokens (optional) |
| `report_type` | string | No | `basic_report` (default) / `detailed_report` / `deep_research` / `summary` / `subtopics` |
| `report_format` | string | No | `markdown` (default) / `html` / `pdf` / `docx` / `json` |
| `tone` | string | No | `objective` (default) / `analytical` / `opinionated` / `casual` |
| `session_id` | string | No | Session ID (thread_id); auto-generated UUID if not provided |
| `uploaded_files` | array | No | Uploaded file ID list (from `POST /v1/files`) |
| `multi_agent` | bool | No | Enable multi-agent Supervisor mode (default false) |
| `agent_role` | string | No | Custom industry persona (priority over LLM auto-generation) |
| `query_domains` | array | No | Domain whitelist (only retrieve these domains) |
| `org_id` | string | No | Organization ID (for SELF_HOST=False quota validation, priority over project_id) |
| `project_id` | string | No | Project ID (for SELF_HOST=False quota validation) |

**Request Headers**

| Header | Description |
|--------|-------------|
| `Authorization: Bearer <jwt_token>` | Optional (required for research endpoint when SELF_HOST=False) |
| `Content-Type: application/json` | Required |
| `X-Session-Id: <session_id>` | Optional, session ID passthrough |

#### 4.1 Non-Streaming Response

**Response** `200 OK`

```json
{
  "id": "chatcmpl-3f8b2a1c4d5e6f7a8b9c0d1e2f3a4b5c",
  "object": "chat.completion",
  "created": 1783334100,
  "model": "agentinsight-researcher",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "# 2026 China NEV Market Landscape Analysis\n\n..."},
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 25,
    "completion_tokens": 1820,
    "total_tokens": 1845,
    "cost_usd": 0.0182
  },
  "sources": [
    {"title": "CAAM: 2026 NEV Sales Forecast", "url": "https://example.com/news1", "snippet": "...", "score": 0.92}
  ],
  "report_format": "markdown",
  "file_path": null,
  "report_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

**Example (non-streaming)**

```bash
curl -X POST ${BASE_URL}/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agentinsight-researcher",
    "stream": false,
    "messages": [{"role": "user", "content": "Analyze the 2026 China new energy vehicle market landscape"}],
    "report_type": "basic_report",
    "report_format": "markdown",
    "tone": "analytical"
  }'
```

#### 4.2 Streaming SSE Response

**Response** `200 OK` `Content-Type: text/event-stream`

SSE frame format (each frame prefixed with `data: `, ending with `\n\n`):

```
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1783334100,"model":"agentinsight-researcher","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1783334100,"model":"agentinsight-researcher","choices":[{"index":0,"delta":{"content":"\n\n> **[Generate Research Role]** Generated: Financial Analyst\n"},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1783334100,"model":"agentinsight-researcher","choices":[{"index":0,"delta":{"content":"# 2026 China NEV Market Landscape\n\n"},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1783334100,"model":"agentinsight-researcher","choices":[{"index":0,"delta":{"sources":[{"title":"...","url":"...","snippet":"...","score":0.92}]},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1783334100,"model":"agentinsight-researcher","choices":[{"index":0,"delta":{"report_id":"a1b2c3d4-..."},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1783334100,"model":"agentinsight-researcher","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

`delta` field may contain: `role` / `content` / `progress` / `sources` / `report_id` / `file_path` / `report_format`.

**Example (streaming)**

```bash
curl -N -X POST ${BASE_URL}/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agentinsight-researcher",
    "stream": true,
    "messages": [{"role": "user", "content": "Compare React and Vue 3 in enterprise applications"}],
    "report_type": "detailed_report"
  }'
```

#### 4.3 Error Responses

| HTTP | Meaning | Trigger |
|------|---------|---------|
| 400 | Bad Request | `messages` has no user message / empty query |
| 401 | Unauthorized | SELF_HOST=False missing Bearer Token or token validation failed |
| 429 | Too Many Requests | Monthly Agent call limit reached (SELF_HOST=False) |
| 413 | Payload Too Large | File upload exceeds limit (only `/v1/files`) |

```json
{"detail": "messages must contain at least one user message"}
```

#### 4.4 Python SDK Example

```python
import httpx
import json

async with httpx.AsyncClient(base_url="http://localhost:8066", timeout=300) as client:
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "agentinsight-researcher",
            "stream": True,
            "messages": [{"role": "user", "content": "Analyze semiconductor industry trends in 2026"}],
            "report_type": "detailed_report",
            "report_format": "markdown",
            "tone": "analytical",
        },
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                chunk = json.loads(line[6:])
                delta = chunk["choices"][0]["delta"].get("content", "")
                print(delta, end="", flush=True)
```

#### 4.5 OpenAI Python SDK Compatible Call

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8066/v1",
    api_key="any-string-not-used-in-self-host-mode",
)

# Non-streaming
resp = client.chat.completions.create(
    model="agentinsight-researcher",
    messages=[{"role": "user", "content": "Analyze the 2026 China NEV market landscape"}],
    stream=False,
    extra_body={"report_type": "basic_report", "report_format": "markdown"},
)
print(resp.choices[0].message.content)
print(f"Sources: {resp.sources}")
print(f"Report ID: {resp.report_id}")

# Streaming
for chunk in client.chat.completions.create(
    model="agentinsight-researcher",
    messages=[{"role": "user", "content": "Compare React and Vue 3"}],
    stream=True,
    extra_body={"report_type": "detailed_report"},
):
    delta = chunk.choices[0].delta.content or ""
    print(delta, end="", flush=True)
```

---

### 5. File Upload `POST /v1/files`

**Purpose**: Upload files as research data source; file IDs can be referenced in `uploaded_files` field of `/v1/chat/completions`.

**Request**

```http
POST /v1/files
Content-Type: multipart/form-data
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | file | Yes | Uploaded file; extension must be in whitelist |

**Supported Extension Whitelist**: `pdf` / `docx` / `md` / `txt` / `html` / `csv` / `xlsx` / `pptx`

**Size Limit**: Configured by `MAX_UPLOAD_SIZE_MB` (default 50MB); returns 413 if exceeded.

**Response** `201 Created`

```json
{
  "file_id": "agentinsight-researcher:user_abc123:def0123456789abc",
  "filename": "industry_report.pdf",
  "size_bytes": 5242880,
  "size_mb": 5.0,
  "extension": "pdf",
  "uploaded_at": 1783334100
}
```

**Example**

```bash
curl -X POST ${BASE_URL}/v1/files \
  -F "file=@/path/to/industry_report.pdf"
```

**Error Responses**

| HTTP | Meaning |
|------|---------|
| 413 | File size exceeds limit |
| 415 | Unsupported file type |

---

### 6. Human-in-the-Loop Feedback `POST /v1/feedback`

**Purpose**: Submit review feedback on research plan/outline to resolve the HumanAgent node's waiting Future. Only used when `human_review_enabled=True`.

**Request Body**

```json
{
  "session_id": "your-session-id",
  "feedback": "approve"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `session_id` | string | Yes | Session ID (thread_id), same as research request |
| `feedback` | string | Yes | Empty string or `approve`/`accept`/`通过` etc. means accept; other content means revision |

**Response** `200 OK`

```json
{
  "session_id": "your-session-id",
  "submitted": true,
  "submitted_at": 1783334100
}
```

**Error Response** `404 Not Found` (no pending feedback request)

```json
{"detail": "No pending feedback request (session_id may be invalid or feedback already submitted)"}
```

**Example**

```bash
curl -X POST ${BASE_URL}/v1/feedback \
  -H "Content-Type: application/json" \
  -d '{"session_id":"your-session-id","feedback":"approve"}'
```

---

### 7. WebSocket Bidirectional Channel `WS /v1/ws/{session_id}`

**Purpose**: WebSocket enhanced channel receiving 8 structured message types and user feedback. SSE remains the main channel; WebSocket is for human-in-the-loop review requests and real-time structured progress.

**Connection**

```
ws://${BASE_URL_HOST}/v1/ws/{session_id}
```

- Path parameter `session_id` is the thread_id for session isolation
- `ENV=prod` forces Origin validation (CSWSH prevention) + JWT Token validation
- Token can be passed via query parameter `?token=<jwt>` or `Authorization: Bearer <jwt>` header

**Server Pushes 8 Message Types**

| `type` | Description |
|--------|-------------|
| `logs` | Log info |
| `content` | Content chunk (streaming report body) |
| `node_progress` | Node progress |
| `sources` | Retrieval sources |
| `tool_call` | Tool invocation |
| `report` | Complete report |
| `human_feedback_request` | Human-in-the-loop review request |
| `error` | Error info |

**Client Sends**

| `type` | Description |
|--------|-------------|
| `ping` | Heartbeat; server responds with `{"type":"pong"}` |
| `human_feedback` | Submit feedback: `{"type":"human_feedback","feedback":"approve"}` |

**Example (JavaScript)**

```javascript
const ws = new WebSocket("ws://localhost:8066/v1/ws/your-session-id");

ws.onopen = () => {
  console.log("WebSocket connected");
  ws.send(JSON.stringify({ type: "ping" }));
};

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  console.log(`[${msg.type}]`, msg);

  if (msg.type === "human_feedback_request") {
    ws.send(JSON.stringify({
      type: "human_feedback",
      feedback: "approve"
    }));
  }
};

ws.onclose = (event) => {
  console.log(`WebSocket closed: code=${event.code} reason=${event.reason}`);
};
```

**Close Codes**

| Code | Meaning |
|------|---------|
| 1000 | Normal close (replaced by new connection with same session) |
| 1008 | WebSocket not enabled (`websocket_enabled=false`) |
| 4001 | Missing token or invalid token (prod enforced) |
| 4003 | Origin not in whitelist (prod enforced) |

---

### 8. List Session Reports `GET /v1/reports/session/{session_id}`

**Purpose**: One session can generate multiple reports; returns list sorted by `created_at DESC` (without full report content to reduce transfer).

**Request**

```http
GET /v1/reports/session/{session_id}
```

**Response** `200 OK`

```json
[
  {
    "report_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "session_id": "your-session-id",
    "query": "Analyze the 2026 China NEV market landscape",
    "report_format": "markdown",
    "agent_role": "Financial Analyst",
    "created_at": "2026-07-06T10:30:00Z",
    "updated_at": "2026-07-06T10:35:00Z"
  }
]
```

**Example**

```bash
curl ${BASE_URL}/v1/reports/session/your-session-id
```

---

### 9. Download Report `GET /v1/reports/{report_id}/download`

**Purpose**: Download report by `report_id` with real-time conversion to 5 formats. Backward compatible: if `report_id` doesn't match, tries as `session_id` for latest report (response header `X-Deprecated: true` prompts migration).

**Request**

```http
GET /v1/reports/{report_id}/download?format=markdown
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `format` | string | No | `markdown` (default) / `html` / `pdf` / `docx` / `json` |

**Response**

| format | Content-Type | Content-Disposition |
|--------|--------------|---------------------|
| `markdown` | `text/markdown` | `attachment; filename=report_{report_id}.md` |
| `html` | `text/html` | `attachment; filename=report_{report_id}.html` |
| `pdf` | `application/pdf` | `attachment; filename=report_{report_id}.pdf` |
| `docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | `attachment; filename=report_{report_id}.docx` |
| `json` | `application/json` | `attachment; filename=report_{report_id}.json` |

**Example**

```bash
# Download Markdown
curl -OJ ${BASE_URL}/v1/reports/a1b2c3d4-e5f6-7890-abcd-ef1234567890/download?format=markdown

# Download PDF
curl -OJ ${BASE_URL}/v1/reports/a1b2c3d4-e5f6-7890-abcd-ef1234567890/download?format=pdf

# Download DOCX
curl -OJ ${BASE_URL}/v1/reports/a1b2c3d4-e5f6-7890-abcd-ef1234567890/download?format=docx
```

**Error Responses**

| HTTP | Meaning |
|------|---------|
| 403 | No permission to access this report (user isolation check failed) |
| 404 | Report not found / PDF generation failed |
| 400 | Unsupported format |

---

### 10. MCP Configuration Management API

MCP (Model Context Protocol) tool configuration management supports three transport modes:

| Mode | Description | Required Fields |
|------|-------------|-----------------|
| `stdio` | Local mode, communicates with local process via stdin/stdout | `command` |
| `sse` | Remote mode, connects to remote HTTP server via SSE | `server_url` |
| `streamable_http` | Remote mode, connects to remote server via HTTP stream | `server_url` |

#### 10.1 List User MCP Configs `GET /v1/mcp`

**Response** `200 OK` (array, sorted by `created_at DESC`, excludes system MCPs)

```json
[
  {
    "id": 1,
    "name": "my-git-mcp",
    "server_url": null,
    "transport_type": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-git"],
    "env_vars": {"GIT_REPO_PATH": "/tmp/repo"},
    "enabled": true,
    "is_system": false,
    "description": "Git tool",
    "created_at": "2026-07-06T10:00:00Z",
    "updated_at": "2026-07-06T10:00:00Z"
  }
]
```

**Example**

```bash
curl ${BASE_URL}/v1/mcp
```

#### 10.2 Create MCP Config `POST /v1/mcp`

**Purpose**: Create user private MCP config. Auto-tests availability after creation; if test fails and `enabled=true`, automatically sets to `false` (does not block creation).

**Request Body**

```json
{
  "name": "my-git-mcp",
  "transport_type": "stdio",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-git"],
  "env_vars": {"GIT_REPO_PATH": "/tmp/repo"},
  "enabled": true,
  "description": "Git tool"
}
```

**Response** `200 OK` (saved config with `id` and `test_result`)

```json
{
  "id": 1,
  "name": "my-git-mcp",
  "transport_type": "stdio",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-git"],
  "env_vars": {"GIT_REPO_PATH": "/tmp/repo"},
  "enabled": true,
  "is_system": false,
  "description": "Git tool",
  "test_result": {
    "success": true,
    "message": "Connection successful, found 3 tools",
    "error_type": null,
    "tools_count": 3,
    "tools": ["git_status", "git_log", "git_diff"],
    "latency_ms": 2341
  }
}
```

**Example**

```bash
curl -X POST ${BASE_URL}/v1/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-git-mcp",
    "transport_type": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-git"],
    "env_vars": {"GIT_REPO_PATH": "/tmp/repo"},
    "enabled": true,
    "description": "Git tool"
  }'
```

#### 10.3 Update MCP Config `PUT /v1/mcp/{config_id}`

**Purpose**: Update user private MCP config. Force-tests when switching from disabled to enabled; rejects enable on failure (other fields still update, `enabled` forced to `false` with `test_result`). Add `?skip_test=true` to skip (when frontend already tested).

**Request Body**: Same as 10.2 (without `id`/`is_system`)

**Response** `200 OK` (updated config; includes `test_result` on enable failure)

**Example**

```bash
curl -X PUT "${BASE_URL}/v1/mcp/1?skip_test=true" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-git-mcp",
    "transport_type": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-git"],
    "env_vars": {"GIT_REPO_PATH": "/tmp/new-repo"},
    "enabled": true,
    "description": "Git tool (updated)"
  }'
```

#### 10.4 Delete MCP Config `DELETE /v1/mcp/{config_id}`

**Response** `200 OK`

```json
{"deleted": true}
```

**Example**

```bash
curl -X DELETE ${BASE_URL}/v1/mcp/1
```

**Error Response** `404` (config not found or is system config, cannot delete)

#### 10.5 Test Unsaved MCP Config `POST /v1/mcp/test`

**Purpose**: Frontend pre-tests config before saving; not stored. 30s timeout protection.

**Request Body**: Same as 10.2

**Response** `200 OK`

```json
{
  "success": true,
  "message": "Connection successful, found 3 tools",
  "error_type": null,
  "tools_count": 3,
  "tools": ["git_status", "git_log", "git_diff"],
  "latency_ms": 2341
}
```

**Failure Response Example**

```json
{
  "success": false,
  "message": "Start command not found: npx (container missing Node.js, npx MCPs unavailable)",
  "error_type": "command_not_found",
  "tools_count": 0,
  "tools": [],
  "latency_ms": 12
}
```

`error_type` enum: `package_not_found` / `connection_refused` / `timeout` / `handshake_failed` / `command_not_found` / `placeholder_env` / `missing_command` / `missing_url` / `dependency_missing` / `unknown`

**Example**

```bash
curl -X POST ${BASE_URL}/v1/mcp/test \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test-mcp",
    "transport_type": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-git"],
    "enabled": true
  }'
```

#### 10.6 Test Saved MCP Config `POST /v1/mcp/{config_id}/test`

**Purpose**: Query saved config by ID (including system MCPs) and test.

**Response** `200 OK` (same as 10.5)

**Example**

```bash
curl -X POST ${BASE_URL}/v1/mcp/1/test
```

#### 10.7 List System MCP Configs `GET /v1/mcp/system`

**Purpose**: List all system public MCP configs (users can view but not edit/delete), from [MCP official reference implementations](https://github.com/modelcontextprotocol/servers).

**Response** `200 OK` (array, sorted by `name`, `is_system=true`)

**Example**

```bash
curl ${BASE_URL}/v1/mcp/system
```

#### 10.8 Clone System MCP `POST /v1/mcp/system/{config_id}/clone`

**Purpose**: Clone system MCP to user's private list (editable/deletable). Cloned with `enabled=false`:
- MCPs requiring keys: frontend opens edit form for user to fill keys, tests on save, enables only on pass
- MCPs not requiring keys: frontend calls `/v1/mcp/{id}/test`, then PUT `enabled=true` on pass

**Response** `200 OK` (cloned user private config, `is_system=false`, `enabled=false`)

**Error Response** `409 Conflict` (config with same name already exists)

```json
{"detail": "Config with same name 'git' already exists, please delete or rename first"}
```

**Example**

```bash
curl -X POST ${BASE_URL}/v1/mcp/system/5/clone
```

---

## Configuration

All configurations are injected via `.env` + environment variables, no hardcoding in business code. For complete configuration items, see [.env.template](.env.template).

### Required Configuration

| Config | Description | How to obtain |
|--------|-------------|---------------|
| `AGENTINSIGHT_PUBLIC_KEY` | AgentInsight PublicKey | [Platform registration](https://agentinsight.goldebridge.com/platform) |
| `AGENTINSIGHT_SECRET_KEY` | AgentInsight SecretKey | [Platform registration](https://agentinsight.goldebridge.com/platform) |
| `DEEPSEEK_API_KEY` | DeepSeek API Key | [DeepSeek platform](https://platform.deepseek.com/) |
| `ZHIPU_API_KEY` | Zhipu API Key | [Zhipu open platform](https://open.bigmodel.cn/) |
| `BOCHA_API_KEY` | Bocha search API Key (high-quality Chinese results) | [Bocha search](https://bochaai.com/) |
| `METASO_API_KEY` | Metaso AI search API Key (primary domestic AI search, v1.1) | [Metaso AI](https://metaso.cn/) |
| `TAVILY_API_KEY` | Tavily general search API Key (SimpleQA #1) | [Tavily](https://tavily.com/) |
| `EXA_API_KEY` | Exa AI semantic search API Key (AI search fallback, v1.1 CN region) | [Exa AI](https://exa.ai/) |
| `SERPAPI_KEY` | SerpApi Google proxy API Key (multi-engine) | [SerpApi](https://serpapi.com/) |
| `SERPER_API_KEY` | Serper.dev Google Search API Key (cheapest) | [Serper.dev](https://serper.dev/) |
| `SEARCHAPI_API_KEY` | SearchApi.io multi-engine SERP API Key | [SearchApi.io](https://www.searchapi.io/) |
| `GITHUB_TOKEN` | GitHub Personal Access Token (code search, optional) | [GitHub Settings](https://github.com/settings/tokens) |
| `CROSSREF_MAILTO` | CrossRef polite pool email (optional, 50 req/s, v1.1) | Custom email |
| `UNPAYWALL_EMAIL` | Unpaywall real email (required, otherwise HTTP 422 rejected, v1.1) | Custom email |
| `POSTGRES_PASSWORD` | PostgreSQL password | Custom |
| `QDRANT_API_KEY` | Qdrant static API Key | Custom |

### Key Optional Configuration

| Config | Default | Description |
|--------|---------|-------------|
| `SELF_HOST` | `True` | `True`=self-hosted (token optional, degrades to `DEFAULT_USER_ID`); `False`=cloud-hosted (strict JWT + quota validation) |
| `DEFAULT_USER_ID` | `anonymous` | Anonymous degraded user ID |
| `ENV` | `dev` | `dev` / `prod` (prod disables docs/openapi) |
| `ENABLE_TEST_PAGE` | `dev=true / prod=false` | Whether to mount frontend test page |
| `WEBSOCKET_ENABLED` | `False` | Whether to enable WebSocket endpoint |
| `WS_AUTH_REQUIRED` | `False` | WebSocket strict JWT (auto-on in prod) |
| `WS_ORIGIN_CHECK` | `False` | WebSocket Origin validation (auto-on in prod) |
| `HUMAN_REVIEW_ENABLED` | `False` | Whether to enable human-in-the-loop review |
| `DEFAULT_REPORT_TYPE` | `basic_report` | Default report type |
| `DEFAULT_REPORT_FORMAT` | `markdown` | Default report format |
| `MAX_UPLOAD_SIZE_MB` | `50` | File upload size limit |
| `CORS_ALLOW_ORIGINS` | `http://localhost:8066` | CORS whitelist (comma-separated) |
| `CHAT_REQUIRES_REPORT` | `True` | CHAT intent degrades to OFF_TOPIC on first round without report |

---

## Common Commands

```bash
# Start the container stack
docker compose -p agentinsight up -d

# View logs
docker compose -p agentinsight logs -f agent

# Stop
docker compose -p agentinsight down

# Stop and clean data volumes (use with caution)
docker compose -p agentinsight down -v
```

> 💡 Use `-p agentinsight` project name (container name `agentinsight-<service>-1`), shared namespace with the AgentInsightService project, the two projects do not run in parallel.

---

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for the development process and follow the [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) code of conduct.

---

## License

[MIT License](LICENSE) — Business-friendly, allows modification, distribution, and commercial use, but requires retaining the copyright notice and license.
