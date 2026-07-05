# agentinsight-researcher

> **中文优先的研究分析智能体** | **Chinese-first research analysis agent**

[![Python](https://img.shields.io/badge/Python-≥3.11-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[中文](#中文) | [English](#english)

---

## 中文

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
- 🔧 **MCP 工具协议** — 支持 fast/deep/disabled 三策略,LLM 自动选工具
- 👨‍💻 **人在回路审核** — WebSocket 实时推送研究计划,用户审核后才继续执行
- 📈 **全链路可观测** — AgentInsight SDK 6 类 trace span
- 🛡️ **企业级安全** — JWT 身份解析 + 三级数据隔离 + 安全响应头

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

### 首次请求示例

> 💡 **跨 Shell 兼容提示**:
> - **PowerShell**:使用 `curl.exe --%`(停止解析)语法,避免 `curl` 别名和双引号转义陷阱
> - **Bash / WSL / macOS**:无需 `--%`,直接使用单引号包裹 JSON
> - **CMD(传统 Windows)**:将 JSON 写入文件,使用 `curl -d "@payload.json"` 调用(最可靠)

**PowerShell**(Windows 原生终端,推荐):

```powershell
# 非流式请求
curl.exe --% -X POST http://localhost:8066/v1/chat/completions -H "Content-Type: application/json" -d "{\"model\":\"agentinsight-researcher\",\"stream\":false,\"messages\":[{\"role\":\"user\",\"content\":\"分析2026年中国新能源汽车市场格局\"}]}"

# 流式请求(SSE)
curl.exe --% -N -X POST http://localhost:8066/v1/chat/completions -H "Content-Type: application/json" -d "{\"model\":\"agentinsight-researcher\",\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"对比 React 与 Vue 3 在企业级应用的优劣\"}]}"
```

**Bash**(Linux / macOS / WSL):

```bash
# 非流式请求
curl -X POST http://localhost:8066/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"agentinsight-researcher","stream":false,"messages":[{"role":"user","content":"分析2026年中国新能源汽车市场格局"}]}'

# 流式请求(SSE)
curl -N -X POST http://localhost:8066/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"agentinsight-researcher","stream":true,"messages":[{"role":"user","content":"对比 React 与 Vue 3 在企业级应用的优劣"}]}'
```

<details>
<summary>📁 CMD 用户请展开(使用 JSON 文件方式,最可靠)</summary>

将以下内容保存为 `payload.json`:

```json
{"model":"agentinsight-researcher","stream":false,"messages":[{"role":"user","content":"分析2026年中国新能源汽车市场格局"}]}
```

然后执行:

```cmd
curl -X POST http://localhost:8066/v1/chat/completions ^
  -H "Content-Type: application/json" ^
  -d "@payload.json"
```

</details>

### Python SDK 调用示例

```python
import httpx

async with httpx.AsyncClient(base_url="http://localhost:8066", timeout=300) as client:
    # 流式请求
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "agentinsight-researcher",
            "stream": True,
            "messages": [{"role": "user", "content": "分析半导体行业 2026 年趋势"}],
            "report_type": "detailed_report",  # basic_report | detailed_report | deep_research
            "report_format": "markdown",         # markdown | html | pdf | docx | json
            "tone": "analytical",                # objective | analytical | opinionated | casual
        },
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                import json
                chunk = json.loads(line[6:])
                delta = chunk["choices"][0]["delta"].get("content", "")
                print(delta, end="", flush=True)
```

---

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/chat/completions` | POST | OpenAI 兼容研究端点(流式 SSE + 非流式) |
| `/v1/files` | POST | 文件上传(PDF/DOCX/MD/TXT/HTML/CSV/XLSX/PPTX) |
| `/v1/models` | GET | OpenAI 兼容模型列表 |
| `/v1/feedback` | POST | 人在回路反馈提交 |
| `/v1/ws/{session_id}` | WS | WebSocket 双向通道 |
| `/health` | GET | 健康检查 |
| `/` | Static | 前端测试页面(`ENABLE_TEST_PAGE=true` 时) |

### OpenAI 兼容请求扩展字段

```jsonc
{
  "model": "agentinsight-researcher",
  "messages": [{"role": "user", "content": "你的研究问题"}],
  "stream": true,
  // 扩展字段
  "report_type": "detailed_report",  // basic_report | detailed_report | deep_research
  "report_format": "markdown",        // markdown | html | pdf | docx | json
  "tone": "analytical",               // objective | analytical | opinionated | casual
  "session_id": "your-session-id",    // 会话 ID
  "uploaded_files": ["file_id_1"],    // 上传文件 ID 列表
  "multi_agent": false,               // 是否启用多 Agent 协作
  "agent_role": "金融分析师",          // 自定义行业角色
  "query_domains": ["example.com"]    // 域名白名单
}
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
| `BOCHA_API_KEY` | 博查搜索 API Key | [博查搜索](https://bochaai.com/) |
| `POSTGRES_PASSWORD` | PostgreSQL 密码 | 自定义 |
| `QDRANT_API_KEY` | Qdrant 静态 API Key | 自定义 |

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

## English

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
- 🔧 **MCP Tool Protocol** — Supports fast/deep/disabled strategies, LLM auto-selects tools
- 👨‍💻 **Human-in-the-Loop Review** — WebSocket real-time push of research plans, continues only after user approval
- 📈 **Full-Link Observability** — AgentInsight SDK 6 types of trace spans
- 🛡️ **Enterprise-Grade Security** — JWT identity resolution + three-tier data isolation + security response headers

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

### First Request Example

> 💡 **Cross-Shell Compatibility Notes**:
> - **PowerShell**:Use the `curl.exe --%` (stop-parsing) syntax to avoid the `curl` alias and double-quote escaping pitfalls
> - **Bash / WSL / macOS**:No `--%` needed; use single quotes to wrap the JSON directly
> - **CMD (legacy Windows)**:Save the JSON to a file and use `curl -d "@payload.json"` (most reliable)

**PowerShell** (Windows native terminal, recommended):

```powershell
# Non-streaming request
curl.exe --% -X POST http://localhost:8066/v1/chat/completions -H "Content-Type: application/json" -d "{\"model\":\"agentinsight-researcher\",\"stream\":false,\"messages\":[{\"role\":\"user\",\"content\":\"Analyze the 2026 China new energy vehicle market landscape\"}]}"

# Streaming request (SSE)
curl.exe --% -N -X POST http://localhost:8066/v1/chat/completions -H "Content-Type: application/json" -d "{\"model\":\"agentinsight-researcher\",\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"Compare React and Vue 3 in enterprise applications\"}]}"
```

**Bash** (Linux / macOS / WSL):

```bash
# Non-streaming request
curl -X POST http://localhost:8066/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"agentinsight-researcher","stream":false,"messages":[{"role":"user","content":"Analyze the 2026 China new energy vehicle market landscape"}]}'

# Streaming request (SSE)
curl -N -X POST http://localhost:8066/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"agentinsight-researcher","stream":true,"messages":[{"role":"user","content":"Compare React and Vue 3 in enterprise applications"}]}'
```

<details>
<summary>📁 CMD users please expand (use JSON file approach, most reliable)</summary>

Save the following as `payload.json`:

```json
{"model":"agentinsight-researcher","stream":false,"messages":[{"role":"user","content":"Analyze the 2026 China new energy vehicle market landscape"}]}
```

Then run:

```cmd
curl -X POST http://localhost:8066/v1/chat/completions ^
  -H "Content-Type: application/json" ^
  -d "@payload.json"
```

</details>

### Python SDK Example

```python
import httpx

async with httpx.AsyncClient(base_url="http://localhost:8066", timeout=300) as client:
    # Streaming request
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "agentinsight-researcher",
            "stream": True,
            "messages": [{"role": "user", "content": "Analyze semiconductor industry trends in 2026"}],
            "report_type": "detailed_report",  # basic_report | detailed_report | deep_research
            "report_format": "markdown",         # markdown | html | pdf | docx | json
            "tone": "analytical",                # objective | analytical | opinionated | casual
        },
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                import json
                chunk = json.loads(line[6:])
                delta = chunk["choices"][0]["delta"].get("content", "")
                print(delta, end="", flush=True)
```

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/chat/completions` | POST | OpenAI-compatible research endpoint (streaming SSE + non-streaming) |
| `/v1/files` | POST | File upload (PDF/DOCX/MD/TXT/HTML/CSV/XLSX/PPTX) |
| `/v1/models` | GET | OpenAI-compatible model list |
| `/v1/feedback` | POST | Human-in-the-loop feedback submission |
| `/v1/ws/{session_id}` | WS | WebSocket bidirectional channel |
| `/health` | GET | Health check |
| `/` | Static | Frontend test page (when `ENABLE_TEST_PAGE=true`) |

### OpenAI-Compatible Request Extended Fields

```jsonc
{
  "model": "agentinsight-researcher",
  "messages": [{"role": "user", "content": "your research question"}],
  "stream": true,
  // Extended fields
  "report_type": "detailed_report",  // basic_report | detailed_report | deep_research
  "report_format": "markdown",        // markdown | html | pdf | docx | json
  "tone": "analytical",               // objective | analytical | opinionated | casual
  "session_id": "your-session-id",    // Session ID
  "uploaded_files": ["file_id_1"],    // Uploaded file ID list
  "multi_agent": false,               // Whether to enable multi-agent collaboration
  "agent_role": "Financial Analyst",  // Custom industry role
  "query_domains": ["example.com"]    // Domain whitelist
}
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
| `BOCHA_API_KEY` | Bocha search API Key | [Bocha search](https://bochaai.com/) |
| `POSTGRES_PASSWORD` | PostgreSQL password | Custom |
| `QDRANT_API_KEY` | Qdrant static API Key | Custom |

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
