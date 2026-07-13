# Examples — agentinsight-researcher 示例代码

本目录提供可独立运行的端到端示例，帮助开源用户快速上手 agentinsight-researcher 的 OpenAI 兼容 API。

所有示例均使用 Python 标准库 + `httpx`（或 `openai` SDK），无需安装项目本身依赖。

---

## 前置条件

### 1. 启动容器栈

```bash
# 在项目根目录执行
docker compose -p agentinsight up -d

# 等待全部容器健康
docker compose -p agentinsight ps
# 全部显示 (healthy) 即可
```

### 2. 配置 `.env`

从 `.env.template` 复制并填入 API Key：

```bash
copy .env.template .env   # Windows
# cp .env.template .env   # Linux/macOS
```

至少需要配置：
- `AGENTINSIGHT_PUBLIC_KEY` / `AGENTINSIGHT_SECRET_KEY`（可观测性，必填）
- `DEEPSEEK_API_KEY` 或 `ZHIPU_API_KEY`（LLM，至少一个）
- `BOCHA_API_KEY` 或其他搜索引擎 Key（搜索，至少一个；默认已配置 SearXNG 自托管元搜索 `SEARX_URL`，可不配置外部 Key）

### 3. 验证服务可用

```bash
curl http://localhost:8066/health
# {"status":"ok","service":"agentinsight-researcher","version":"1.1.0"}
```

### 4. 安装示例依赖

```bash
pip install httpx openai
```

> 仅 `openai_sdk_compatible.py` 需要 `openai` SDK，其余示例仅需 `httpx`。

---

## 环境变量

所有示例支持以下环境变量覆盖默认值（无需修改代码即可切换部署地址）：

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `AGENT_BASE_URL` | `http://localhost:8066/v1` | API 基础 URL（含 `/v1`） |
| `AGENT_JWT_TOKEN` | （空） | Bearer JWT Token，可选；留空走匿名用户路径 |

> 🔒 安全提示：JWT Token 仅通过环境变量传入，示例代码不硬编码任何密钥。
> `SELF_HOST=True`（默认）时 Token 可选；`SELF_HOST=False` 时研究端点必需 Token。

---

## 示例索引

| # | 文件 | 用途 | 端点 | 流式 |
|---|------|------|------|------|
| 1 | [`quickstart.py`](quickstart.py) | 10 行代码快速开始（非流式） | `POST /v1/chat/completions` | ❌ |
| 2 | [`streaming_research.py`](streaming_research.py) | 流式研究报告生成（SSE 逐块打印） | `POST /v1/chat/completions` | ✅ |
| 3 | [`detailed_report.py`](detailed_report.py) | 多章节深度报告（detailed_report） | `POST /v1/chat/completions` | ✅ |
| 4 | [`openai_sdk_compatible.py`](openai_sdk_compatible.py) | OpenAI Python SDK 兼容调用 | `POST /v1/chat/completions` | ✅ |
| 5 | [`multi_agent_research.py`](multi_agent_research.py) | 多 Agent 协作模式（deep_research） | `POST /v1/chat/completions` | ✅ |
| 6 | [`download_report.py`](download_report.py) | 报告下载（markdown/pdf/docx） | `GET /v1/reports/{id}/download` | ❌ |
| 7 | [`file_upload_research.py`](file_upload_research.py) | 文件上传 + 基于文件研究 | `POST /v1/files` + `POST /v1/chat/completions` | ✅ |

---

## 快速运行

```bash
# 进入项目根目录后执行（示例相对路径为 examples/xxx.py）

# 1. 最简非流式示例
python examples/quickstart.py

# 2. 流式示例（实时看到逐块输出）
python examples/streaming_research.py

# 3. OpenAI SDK 兼容调用（证明 OpenAI 兼容性）
pip install openai
python examples/openai_sdk_compatible.py
```

---

## 报告类型与格式速查

### 报告类型（`report_type` 字段）

| 类型 | 说明 | 适用场景 |
|------|------|---------|
| `basic_report` | 基础报告（默认） | 单主题快速研究 |
| `detailed_report` | 多章节深度报告 | 复杂主题，分章节展开 |
| `deep_research` | 深度研究（多 Agent 协作） | 最深度研究，触发 Supervisor 多 Agent 流水线 |
| `summary` | 摘要 | 对已有内容做总结 |
| `subtopics` | 子主题列表 | 拆解主题子方向 |

### 输出格式（`report_format` 字段）

`markdown`（默认） / `html` / `pdf` / `docx` / `json`

> 下载端点 `GET /v1/reports/{report_id}/download?format=` 支持 markdown/html/pdf/docx/json；
> Publisher 层另支持 `latex`/`epub`（学术/电子书场景，仅经流式 `content` 推送，不提供下载端点）。

### 报告语言

通过 `agent_role` 自定义行业 persona 间接控制语言风格；默认中文优先。

---

## 常见问题

### Q: 请求超时？

研究报告生成涉及多轮搜索 + LLM 调用，`basic_report` 通常 30-90 秒，`deep_research` 可能 3-10 分钟。示例中 `timeout=300` 已留足余量。

### Q: 返回 401 Unauthorized？

`SELF_HOST=False`（云托管）模式下研究端点强制要求 Bearer Token。请设置环境变量：

```bash
# Bash
export AGENT_JWT_TOKEN="你的JWT"
# PowerShell
$env:AGENT_JWT_TOKEN="你的JWT"
```

### Q: 返回 429 Too Many Requests？

云托管模式下月度调用次数已达上限，请联系管理员或切换到本地部署（`SELF_HOST=True`）。

### Q: 如何获取已生成报告的 ID？

- 非流式响应：`response.json()["report_id"]`
- 流式响应：解析 SSE 帧中 `delta.report_id` 字段
- 列出会话所有报告：`GET /v1/reports/session/{session_id}`

### Q: 如何切换部署地址？

```bash
# 指向远程服务器
export AGENT_BASE_URL="https://your-server.com/v1"
python examples/quickstart.py
```

---

## 更多资源

- [项目 README](../README.md) — 完整 API 文档
- [Agent 发现协议](http://localhost:8066/.well-known/agent-discovery.json) — 自动发现 Agent 能力
- [测试页面](http://localhost:8066) — 内置前端联调页面（`ENABLE_TEST_PAGE=true` 时可用）
- [CONTRIBUTING.md](../CONTRIBUTING.md) — 贡献指南
