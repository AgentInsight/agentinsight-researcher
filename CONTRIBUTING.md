# 贡献指南 | Contributing Guide

[中文](#中文) | [English](#english)

---

## 中文

感谢你对 agentinsight-researcher 项目的关注!本文档描述了参与贡献的流程与规范。

---

## 行为准则

参与本项目即表示你同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。请在所有交流中保持尊重、包容、专业。

---

## 开发环境准备

### 1. Fork & Clone

```bash
# Fork 仓库到自己的 GitHub 账户后
git clone https://github.com/<你的用户名>/agentinsight-researcher.git
cd agentinsight-researcher
git remote add upstream https://github.com/<原始仓库>/agentinsight-researcher.git
```

### 2. 创建虚拟环境

```bash
python -m venv .venv
# Windows
.venv\Scripts\Activate.ps1
# Linux/macOS
source .venv/bin/activate

pip install -U pip -r requirements.txt
```

### 3. 配置环境变量

```bash
copy .env.template .env
# 编辑 .env 填入 APIKey(必填项见 README.md)
```

> ⚠️ **前置条件**:必须先到 [https://agentinsight.goldebridge.com/platform](https://agentinsight.goldebridge.com/platform) 注册账户并创建 APIKey,否则项目无法运行。

### 4. 启动容器栈(用于功能/API/e2e 测试)

```bash
docker compose -p agentinsight up -d
docker compose -p agentinsight ps  # 等待全部 (healthy)
```

---

## 开发规范

### AGENTS.md 是唯一权威

本项目所有开发规范集中定义在 [AGENTS.md](AGENTS.md)(14 章),贡献前**必须完整阅读**。以下为核心要点:

#### 三级行为边界

| 级别 | 触发场景 |
|------|---------|
| ✅ **Always** | 读/写 docs·tests·evals;跑 ruff+mypy+pytest;修 P2 及以下 bug;续接被截断输出 |
| ⚠️ **Ask first** | LangGraph 图结构变更;RAG 核心算法切换;密钥轮换;外部系统对接;连续 3 次修复失败 |
| ❌ **Never** | 安全合规红线(第 11 章);不推荐清单(第 4 章);`eval`/`exec` 求值用户输入;硬编码密钥 |

#### 技术栈约束(优先选择)

- **编排内核**:LangGraph ≥1.2(不推荐 AutoGen/CrewAI/AgentExecutor)
- **LLM 抽象**:LiteLLM ≥1.6(不推荐厂商 SDK 直连)
- **向量库**:Qdrant ≥1.18(不推荐 Pinecone/Milvus)
- **关系库**:PostgreSQL ≥16(不推荐 MySQL)
- **可观测性**:AgentInsight SDK(不推荐 LangSmith/Langfuse/OTel 原生 API)

> 完整「不推荐清单」见 [AGENTS.md 第 4 章](AGENTS.md#4-三级行为边界与不推荐清单)。如需选用不推荐方案,应说明理由并经用户确认。

### 代码风格

```bash
# 质量检查(必须通过)
ruff check .
ruff format --check .
mypy src/ --strict

# 自动修复
ruff check . --fix
ruff format .
```

**核心约定**:
- Python ≥3.11,类型注解必填(mypy `--strict`)
- 行长 100 字符
- 双引号字符串
- 4 空格缩进
- 异步优先(`async def`)
- 节点为纯函数,无副作用,返回 delta dict

### 提交规范

采用 [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <subject>

<body>

<footer>
```

**type** 取值:

| type | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档变更 |
| `style` | 代码格式(不影响功能) |
| `refactor` | 重构(无功能变更) |
| `perf` | 性能优化 |
| `test` | 测试相关 |
| `chore` | 构建/工具/依赖 |
| `ci` | CI 配置 |

**scope** 可选,建议取值:`api` / `graph` / `rag` / `llm` / `skills` / `observability` / `memory` / `config` / `deploy` / `test`

**示例**:

```
feat(rag): 新增 EmbeddingsFilter 递归分块支持

- RecursiveCharacterTextSplitter chunk_size=1000
- 修复 _merge_parts chunk_size 超限 bug
- 新增 chunk_overlap 兜底逻辑

Closes #123
```

### 分支策略

```
main              # 生产分支,保护分支,只接受 PR
└── feat/<name>   # 功能分支,从 main 切出
└── fix/<name>    # 修复分支
└── docs/<name>   # 文档分支
```

**命名约定**:`<type>/<简短描述>`,如 `feat/embeddings-filter`、`fix/qdrant-namespace`、`docs/readme`。

---

## 测试要求

### 测试分层(AGENTS.md 第 13 章)

| 类型 | 目录 | 执行环境 | 触发时机 |
|------|------|---------|---------|
| 单元测试 | `tests/unit/` | 本地 / 构建期 | 每次 commit |
| 功能测试 | `tests/functional/` | 部署后容器栈 | 容器栈健康后 |
| API 测试 | `tests/api/` | 部署后容器栈 | 容器栈健康后 |
| 回归测试 | `tests/regression/` | 部署后容器栈 | 合并 main 前 |
| 端到端测试 | `tests/e2e/` | 部署后容器栈 | 发布前 |

### 测试约定

- **单元测试不依赖外部服务**(Postgres/Qdrant/Redis/LLM),用 mock 或 faker
- **功能/API/e2e 测试在容器栈健康后执行**,不推荐本地直连或 mock 绕过
- **测试用例独立可重复**,不依赖执行顺序,用 fixture 清理状态
- **测试数据隔离**:Qdrant 用 `namespace=test_*`,会话用 `session_id=test_*`,测试结束清理
- **回归测试为合并门禁**,不推荐 `@skip`

### 运行测试

```bash
# 单元测试(本地)
pytest tests/unit/ -q

# 功能/API/e2e 测试(需容器栈)
pytest tests/functional/ tests/api/ tests/e2e/ -q

# 全量测试
pytest tests/ -q

# 生成覆盖率报告
pytest tests/ --cov=src --cov-report=html
```

### 评测门禁(AGENTS.md 第 10 章)

CI 强制评测门禁,不达标不推荐合并 main:

- **RAGAS**:faithfulness ≥0.8 / answer_relevancy ≥0.8 / context_precision ≥0.7
- **DeepEval**:任务完成率 ≥0.9 / 工具调用正确率 ≥0.95 / 幻觉率 ≤0.1

```bash
# RAG 评测
python -m evals.rag.run --dataset evals/rag/dataset.json

# Agent 行为评测
python -m evals.agent.run --dataset evals/agent/dataset.json
```

---

## PR 流程

### 1. 创建分支

```bash
git checkout -b feat/your-feature
```

### 2. 开发 + 测试

```bash
# 编写代码
# ... 

# 运行单元测试
pytest tests/unit/ -q

# 质量检查
ruff check . && ruff format --check . && mypy src/ --strict

# 全部通过后提交
git add <相关文件>
git commit -m "feat(scope): 简短描述"
```

### 3. 同步上游

```bash
git fetch upstream
git rebase upstream/main
# 解决冲突(如有)
pytest tests/unit/ -q  # 重新测试
```

### 4. 推送 + 创建 PR

```bash
git push origin feat/your-feature
# 在 GitHub 上创建 PR,目标分支 main
```

### 5. PR 审核清单

PR 创建后,请确认:

- [ ] 单元测试全部通过
- [ ] `ruff check .` 通过
- [ ] `ruff format --check .` 通过
- [ ] `mypy src/ --strict` 通过
- [ ] 新功能有对应单元测试
- [ ] 文档已更新(如涉及)
- [ ] 提交信息符合 Conventional Commits
- [ ] 无硬编码密钥/密码(AGENTS.md 第 11 章)
- [ ] 无 `eval`/`exec` 求值用户输入
- [ ] 无 `.env` / `.env.qa` 等含密钥文件入仓

### 6. CI 流水线

PR 创建后自动触发 CI:

1. **构建镜像 + 单元测试**(失败即终止)
2. `docker compose up -d` + 等待全部健康检查通过
3. 功能测试 → API 测试 → 回归测试 → e2e 测试(按序,前者失败后者不执行)
4. 任一环节失败阻断合并;全部通过后 `docker compose down -v` 清理

---

## 安全合规红线(AGENTS.md 第 11 章)

以下为**真正的硬约束**,涉及法律与合规底线,任何偏差需经安全评审并经用户显式确认:

- 🔑 **密钥**:仅环境变量注入,禁止入仓/硬编码/日志;API Key SHA256+BCrypt 双哈希;发现硬编码密钥即 P0 暂停
- 🔒 **PII**:用户会话内容加密存储+日志脱敏;API 响应禁止返回密码/密钥原文;最小化收集
- 🛡️ **Prompt Injection**:所有外部输入经 Pydantic 校验;工具调用权限隔离;禁止 `eval`/`exec` 求值用户输入
- 🌐 **传输与边界**:生产强制 HTTPS;CORS 禁 `*`;安全响应头中间件不可绕过;生产关闭 Debug

---

## 目录边界(AGENTS.md 第 3 章)

```
src/
├── graph/         # LangGraph 图定义(编排入口)
├── agents/        # 具体 Agent 实现(复用图)
├── skills/        # 技能组件
├── common/        # 公用基础(不依赖 agents/ 或业务模块)
├── config/        # 配置(Settings SSOT)
├── tools/         # MCP 工具注册中心
├── rag/           # 混合检索(BM25+向量+Rerank)
├── llm/           # LiteLLM 网关
├── memory/        # Postgres Checkpointer
├── observability/ # AgentInsight SDK 封装
└── api/           # FastAPI 路由 + middleware
```

**核心约定**:
- `graph/` 是首选编排入口;`agents/` 复用图,不推荐自建编排循环
- `tools/`、`rag/`、`llm/`、`memory/` 不推荐互相 import,共享逻辑下沉到 `common/`
- 依赖单向向内:`common/` 不应依赖 `agents/` 或业务模块
- 配置经 `config/` + 环境变量,业务代码不硬编码 URL/密钥
- 新增顶层目录建议经架构师评审

---

## 问题反馈

- 🐛 **Bug 报告**:使用 GitHub Issue,附复现步骤 + 日志 + 环境信息
- 💡 **功能建议**:使用 GitHub Issue,描述使用场景与期望行为
- ❓ **使用问题**:先查阅 [README.md](README.md) 和 [AGENTS.md](AGENTS.md),再提问

### Bug 报告模板

```markdown
**环境**:
- OS: [Windows 11 / Ubuntu 22.04 / macOS 14]
- Python: [3.12.x]
- Docker: [24.0.x]
- 项目版本: [git commit hash]

**复现步骤**:
1. ...
2. ...

**期望行为**:

**实际行为**:

**日志**:
```
<粘贴相关日志,注意脱敏,禁止包含 APIKey/token>
```
```

---

## 许可证

贡献的代码将遵循 [MIT License](LICENSE) 许可证。

---

## English

Thank you for your interest in the agentinsight-researcher project! This document describes the process and conventions for contributing.

---

## Code of Conduct

By participating in this project, you agree to abide by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Please be respectful, inclusive, and professional in all communications.

---

## Development Environment Setup

### 1. Fork & Clone

```bash
# After forking the repository to your GitHub account
git clone https://github.com/<your-username>/agentinsight-researcher.git
cd agentinsight-researcher
git remote add upstream https://github.com/<original-repo>/agentinsight-researcher.git
```

### 2. Create Virtual Environment

```bash
python -m venv .venv
# Windows
.venv\Scripts\Activate.ps1
# Linux/macOS
source .venv/bin/activate

pip install -U pip -r requirements.txt
```

### 3. Configure Environment Variables

```bash
copy .env.template .env
# Edit .env and fill in APIKey (required fields see README.md)
```

> ⚠️ **Prerequisite**: You must first register an account and create an APIKey at [https://agentinsight.goldebridge.com/platform](https://agentinsight.goldebridge.com/platform), otherwise the project cannot run.

### 4. Start Container Stack (for functional/API/e2e tests)

```bash
docker compose -p agentinsight up -d
docker compose -p agentinsight ps  # Wait for all (healthy)
```

---

## Development Conventions

### AGENTS.md is the Sole Authority

All development conventions for this project are defined centrally in [AGENTS.md](AGENTS.md) (14 chapters), which **must be read in full** before contributing. Key points:

#### Three-Tier Behavior Boundary

| Level | Trigger Scenarios |
|-------|-------------------|
| ✅ **Always** | Read/write docs·tests·evals; run ruff+mypy+pytest; fix P2 and below bugs; continue truncated output |
| ⚠️ **Ask first** | LangGraph structure changes; RAG core algorithm switch; key rotation; external system integration; 3 consecutive fix failures |
| ❌ **Never** | Security compliance red lines (Chapter 11); not recommended list (Chapter 4); `eval`/`exec` for user input; hardcoded keys |

#### Tech Stack Constraints (Preferred Choices)

- **Orchestration Kernel**: LangGraph ≥1.2 (AutoGen/CrewAI/AgentExecutor not recommended)
- **LLM Abstraction**: LiteLLM ≥1.6 (vendor SDK direct connection not recommended)
- **Vector Database**: Qdrant ≥1.18 (Pinecone/Milvus not recommended)
- **Relational Database**: PostgreSQL ≥16 (MySQL not recommended)
- **Observability**: AgentInsight SDK (LangSmith/Langfuse/OTel native API not recommended)

> For the complete "not recommended list", see [AGENTS.md Chapter 4](AGENTS.md#4-三级行为边界与不推荐清单). To use a not-recommended option, you should explain the reason and get user confirmation.

### Code Style

```bash
# Quality checks (must pass)
ruff check .
ruff format --check .
mypy src/ --strict

# Auto-fix
ruff check . --fix
ruff format .
```

**Core Conventions**:
- Python ≥3.11, type annotations required (mypy `--strict`)
- Line length 100 characters
- Double-quoted strings
- 4-space indentation
- Async-first (`async def`)
- Nodes are pure functions, no side effects, return delta dict

### Commit Conventions

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <subject>

<body>

<footer>
```

**type** values:

| type | Description |
|------|-------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation changes |
| `style` | Code formatting (no functional impact) |
| `refactor` | Refactoring (no functional changes) |
| `perf` | Performance optimization |
| `test` | Test-related |
| `chore` | Build/tools/dependencies |
| `ci` | CI configuration |

**scope** is optional, recommended values: `api` / `graph` / `rag` / `llm` / `skills` / `observability` / `memory` / `config` / `deploy` / `test`

**Example**:

```
feat(rag): add EmbeddingsFilter recursive chunking support

- Aligned with RecursiveCharacterTextSplitter chunk_size=1000
- Fix _merge_parts chunk_size overflow bug
- Add chunk_overlap fallback logic

Closes #123
```

### Branching Strategy

```
main              # Production branch, protected, accepts PRs only
└── feat/<name>   # Feature branch, cut from main
└── fix/<name>    # Fix branch
└── docs/<name>   # Documentation branch
```

**Naming convention**: `<type>/<brief-description>`, e.g., `feat/embeddings-filter`, `fix/qdrant-namespace`, `docs/readme`.

---

## Testing Requirements

### Test Tiers (AGENTS.md Chapter 13)

| Type | Directory | Execution Environment | Trigger |
|------|-----------|----------------------|---------|
| Unit | `tests/unit/` | Local / build | Every commit |
| Functional | `tests/functional/` | Deployed container stack | After stack healthy |
| API | `tests/api/` | Deployed container stack | After stack healthy |
| Regression | `tests/regression/` | Deployed container stack | Before merging to main |
| End-to-End | `tests/e2e/` | Deployed container stack | Before release |

### Testing Conventions

- **Unit tests do not depend on external services** (Postgres/Qdrant/Redis/LLM), use mock or faker
- **Functional/API/e2e tests run after container stack is healthy**, local direct connection or mock bypass not recommended
- **Test cases are independent and repeatable**, do not depend on execution order, use fixtures to clean state
- **Test data isolation**: Qdrant uses `namespace=test_*`, sessions use `session_id=test_*`, cleaned after testing
- **Regression tests are merge gates**, `@skip` not recommended

### Running Tests

```bash
# Unit tests (local)
pytest tests/unit/ -q

# Functional/API/e2e tests (requires container stack)
pytest tests/functional/ tests/api/ tests/e2e/ -q

# Full test suite
pytest tests/ -q

# Generate coverage report
pytest tests/ --cov=src --cov-report=html
```

### Evaluation Gates (AGENTS.md Chapter 10)

CI mandatory evaluation gates, failing gates cannot merge to main:

- **RAGAS**: faithfulness ≥0.8 / answer_relevancy ≥0.8 / context_precision ≥0.7
- **DeepEval**: task completion rate ≥0.9 / tool call accuracy ≥0.95 / hallucination rate ≤0.1

```bash
# RAG evaluation
python -m evals.rag.run --dataset evals/rag/dataset.json

# Agent behavior evaluation
python -m evals.agent.run --dataset evals/agent/dataset.json
```

---

## PR Process

### 1. Create Branch

```bash
git checkout -b feat/your-feature
```

### 2. Develop + Test

```bash
# Write code
# ...

# Run unit tests
pytest tests/unit/ -q

# Quality checks
ruff check . && ruff format --check . && mypy src/ --strict

# Commit after all pass
git add <relevant-files>
git commit -m "feat(scope): brief description"
```

### 3. Sync Upstream

```bash
git fetch upstream
git rebase upstream/main
# Resolve conflicts (if any)
pytest tests/unit/ -q  # Re-test
```

### 4. Push + Create PR

```bash
git push origin feat/your-feature
# Create PR on GitHub, target branch main
```

### 5. PR Review Checklist

After creating the PR, please confirm:

- [ ] All unit tests pass
- [ ] `ruff check .` passes
- [ ] `ruff format --check .` passes
- [ ] `mypy src/ --strict` passes
- [ ] New features have corresponding unit tests
- [ ] Documentation updated (if applicable)
- [ ] Commit messages follow Conventional Commits
- [ ] No hardcoded keys/passwords (AGENTS.md Chapter 11)
- [ ] No `eval`/`exec` for user input evaluation
- [ ] No `.env` / `.env.qa` or other files containing keys committed to repo

### 6. CI Pipeline

CI is automatically triggered after PR creation:

1. **Build image + unit tests** (failure terminates)
2. `docker compose up -d` + wait for all health checks to pass
3. Functional tests → API tests → regression tests → e2e tests (sequential, later ones skipped if earlier fails)
4. Any failure blocks merge; after all pass, `docker compose down -v` cleanup

---

## Security Compliance Red Lines (AGENTS.md Chapter 11)

The following are **true hard constraints** involving legal and compliance bottom lines. Any deviation requires security review and explicit user confirmation:

- 🔑 **Keys**: Environment variable injection only, committing to repo/hardcoding/logging is prohibited; API Key SHA256+BCrypt double hashing; finding hardcoded keys triggers P0 pause
- 🔒 **PII**: User session content encrypted storage + log desensitization; API responses must not return password/key plaintext; minimized collection
- 🛡️ **Prompt Injection**: All external input validated by Pydantic; tool invocation permission isolation; `eval`/`exec` for user input evaluation is prohibited
- 🌐 **Transmission & Boundary**: Production mandatory HTTPS; CORS not `*`; security response headers middleware cannot be bypassed; production disables Debug

---

## Directory Boundaries (AGENTS.md Chapter 3)

```
src/
├── graph/         # LangGraph definitions (orchestration entry)
├── agents/        # Specific Agent implementations (reuse graph)
├── skills/        # Skill components
├── common/        # Common utilities (do not depend on agents/ or business modules)
├── config/        # Configuration (Settings SSOT)
├── tools/         # MCP tool registry
├── rag/           # Hybrid retrieval (BM25+vector+Rerank)
├── llm/           # LiteLLM gateway
├── memory/        # Postgres Checkpointer
├── observability/ # AgentInsight SDK wrapper
└── api/           # FastAPI routes + middleware
```

**Core Conventions**:
- `graph/` is the preferred orchestration entry; `agents/` reuses graph, self-built orchestration loops not recommended
- `tools/`, `rag/`, `llm/`, `memory/` should not import each other, shared logic goes to `common/`
- Dependencies point inward: `common/` should not depend on `agents/` or business modules
- Configuration via `config/` + environment variables, business code should not hardcode URLs/keys
- Adding top-level directories requires architect review

---

## Feedback

- 🐛 **Bug reports**: Use GitHub Issue, include reproduction steps + logs + environment info
- 💡 **Feature suggestions**: Use GitHub Issue, describe use case and expected behavior
- ❓ **Usage questions**: First check [README.md](README.md) and [AGENTS.md](AGENTS.md), then ask

### Bug Report Template

```markdown
**Environment**:
- OS: [Windows 11 / Ubuntu 22.04 / macOS 14]
- Python: [3.12.x]
- Docker: [24.0.x]
- Project version: [git commit hash]

**Reproduction Steps**:
1. ...
2. ...

**Expected Behavior**:

**Actual Behavior**:

**Logs**:
```
<paste relevant logs here, ensure desensitization, must not include APIKey/token>
```
```

---

## License

Contributed code will be licensed under the [MIT License](LICENSE).
