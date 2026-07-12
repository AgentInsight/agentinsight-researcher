# 开源就绪度评估报告 | Open Source Readiness Assessment

> **评估对象**: `agentinsight-researcher` 项目
> **评估目的**: 评估项目放到 GitHub 上当开源项目的就绪度,识别缺口并给出优化建议
> **评估日期**: 2026-07-11
> **评估方法**: 实际读取项目文件,按 10 个维度逐项核查
> **说明**: 本报告仅做评估与建议,**未修改任何源文件**

---

## 一、总体评估

**总体就绪度: ⭐⭐⭐⭐☆ (4/5) — 高度就绪,具备少量可改进项**

项目在「必备文件」「CI/CD」「安全合规」「文档完善度」等核心维度上表现优秀,文件体系完整、双语(中英文)支持到位、规范严格(14 章项目规范),已具备直接开源的基础条件。主要改进空间集中在「社区建设」「代码质量工具链」「法律合规补充」「可发现性增强」四个方面。

### 总览评分表

| # | 评估维度 | 评分 | 关键发现 |
|---|---------|------|---------|
| 1 | 必备文件检查 | ✅ 完善 | 8 个必备文件全部存在,内容详实,中英双语 |
| 2 | 文档完善度 | ⚠️ 需改进 | 架构/API/测试文档优秀;缺独立部署/开发指南、FAQ、截图 |
| 3 | CI/CD | ⚠️ 需改进 | CI 流水线 4 Job 完整;缺 pre-commit、安全扫描、Release workflow |
| 4 | 安全性 | ⚠️ 需改进 | 安全策略完善;缺依赖安全扫描、密钥泄露扫描自动化 |
| 5 | 社区建设 | ❌ 缺失 | 缺 Roadmap、FAQ、Discord/Slack、FUNDING、贡献者认可 |
| 6 | 代码质量 | ⚠️ 需改进 | ruff/mypy/pytest 配置完善;缺 pre-commit hooks |
| 7 | 包管理 | ⚠️ 需改进 | pip-tools 锁定完善;pyproject.toml 未声明项目元数据依赖 |
| 8 | Docker/部署 | ✅ 完善 | 多阶段构建、健康检查、三套构建模式齐全 |
| 9 | 可发现性 | ⚠️ 需改进 | 徽章+Demo 齐全;缺项目截图、GitHub Topics 说明、项目视频 |
| 10 | 法律合规 | ⚠️ 需改进 | MIT+商标声明完善;缺 CITATION.cff、第三方许可说明、隐私声明 |

---

## 二、详细评估

### 维度 1: 必备文件检查 — ✅ 完善

| 文件 | 状态 | 评估 |
|------|------|------|
| `LICENSE` | ✅ | MIT License,版权声明 `Copyright (c) 2026 AgentInsight`,商业友好 |
| `README.md` | ✅ | 中英双语,2100+ 行,含徽章、Demo、API 文档、配置说明,极其详尽 |
| `CONTRIBUTING.md` | ✅ | 中英双语,涵盖开发环境、规范、提交规范、PR 流程、安全合规 |
| `CODE_OF_CONDUCT.md` | ✅ | 中英双语,基于 Contributor Covenant 2.1,含执行措施与举报渠道 |
| `CHANGELOG.md` | ✅ | 中英双语,Keep a Changelog 格式,语义化版本,首版 1.0.0 内容详尽 |
| `SECURITY.md` | ✅ | 中英双语,含漏洞报告渠道、响应 SLA、支持版本、安全合规红线 |
| `.gitignore` | ✅ | 完善,覆盖 Python/IDE/构建/环境变量/离线包/临时文件 |
| `.env.template` | ✅ | 完善,分类注释清晰,占位符规范,含所有必填项 |

**亮点**:
- 所有文件均提供中英双语,照顾中文开发者社区
- 文件间交叉引用完整(README ↔ CONTRIBUTING ↔ AGENTS ↔ SECURITY)
- `.gitignore` 显式排除 `.env` 并保留 `.env.template`,符合安全规范
- `CHANGELOG.md` 严格遵循 Keep a Changelog 与 SemVer 规范

**无需改进**,本维度已达到开源最佳实践。

---

### 维度 2: 文档完善度 — ⚠️ 需改进

| 文档项 | 状态 | 评估 |
|--------|------|------|
| 架构文档 `docs/architecture.md` | ✅ | 中英双语,含 5 个 Mermaid 图(系统概览/请求流/研究流/检索流/多 Agent/部署),详尽 |
| API 文档 | ✅ | README.md 含 19 个端点完整文档;FastAPI `/docs` Swagger(dev 模式) |
| 部署文档 | ⚠️ | 散落在 README + 项目部署规则 + `docker-build.sh`;缺独立 `docs/deployment.md` |
| 开发者文档 | ⚠️ | CONTRIBUTING.md 涵盖基础;缺独立 `docs/development.md`(环境搭建/调试/常见问题) |
| 使用示例 `examples/` | ✅ | 7 个可运行示例 + 独立 README,覆盖流式/非流式/多 Agent/SDK 兼容/文件上传/下载 |
| 测试文档 `tests/README.md` | ✅ | 极其详尽,7 层测试分层、执行命令、覆盖矩阵、MCP 测试清单 |
| FAQ | ❌ | 仅 `examples/README.md` 有少量 FAQ;缺独立 `docs/faq.md` |
| 项目截图 | ❌ | `docs/` 无 `assets/` 目录;README 无截图/GIF/视频 |
| 对比文档 | ✅ | `docs/` 含深度分析文档 |

**改进建议**:
1. **新增 `docs/deployment.md`**:整合三套构建模式(QA 离线/生产联网/生产离线)的完整部署指南,含端口规划、资源需求、故障排查、升级流程。当前部署信息分散在 README、项目部署规则、构建脚本中,新用户难以快速上手。
2. **新增 `docs/development.md`**:补充开发环境搭建、调试技巧(断点调试/日志级别/单节点调试)、常见开发任务(新增搜索器/抓取器/Agent 节点的模板)。
3. **新增 `docs/faq.md`**:将常见问题集中管理,涵盖:Demo 502 处理、401/429 错误、容器启动失败、Embeddings 模型下载慢、中文乱码、Qdrant 连接失败等。
4. **新增 `docs/assets/` 目录并补充截图**:在 README 顶部添加 1-2 张测试页面截图或 Demo GIF,显著提升项目第一印象。开源项目带截图的 README 平均 star 数比无截图高 30%+。

---

### 维度 3: CI/CD — ⚠️ 需改进

| 检查项 | 状态 | 评估 |
|--------|------|------|
| `.github/workflows/ci.yml` | ✅ | 4 个 Job:lint+unit / integration / evaluation / build&push GHCR |
| 自动化测试 | ✅ | 单元测试(构建期)+ 功能/API/回归/E2E(容器栈)分层执行 |
| 代码质量检查 | ✅ | ruff check + ruff format + mypy --strict |
| 评测门禁 | ✅ | RAGAS + DeepEval(pr 不跑,push to main 跑,continue-on-error) |
| Docker 镜像发布 | ✅ | 推送到 GHCR(ghcr.io),含 latest + sha-xxx 标签 |
| `dependabot.yml` | ✅ | pip / docker / github-actions 三生态,每周一检查 |
| PR 模板 | ✅ | 详尽,含质量门禁/架构边界/安全合规/数据隔离/可观测性自查清单 |
| Issue 模板 | ✅ | bug_report.yml + feature_request.yml + config.yml(含 contact_links) |
| 并发控制 | ✅ | `concurrency: cancel-in-progress: true` |
| pre-commit hooks | ❌ | 缺 `.pre-commit-config.yaml`,本地提交前无自动化检查 |
| 依赖安全扫描 | ❌ | 缺 CodeQL / pip-audit / safety workflow |
| Release workflow | ❌ | 缺自动化 Release(GitHub Release + changelog 生成 + tag) |
| 代码覆盖率上报 | ⚠️ | CI 有 `--cov` 但未上传到 codecov.io(README 已有 codecov 徽章) |

**改进建议**:
1. **新增 `.pre-commit-config.yaml`**:配置 ruff(check+format)、mypy、end-of-file-fixer、trailing-whitespace、check-yaml、check-added-large-files。让贡献者在本地提交前即可发现问题,减少 CI 往返。示例:
   ```yaml
   repos:
     - repo: https://github.com/astral-sh/ruff-pre-commit
       rev: v0.8.0
       hooks:
         - id: ruff
           args: [--fix]
         - id: ruff-format
     - repo: https://github.com/pre-commit/mirrors-mypy
       rev: v1.13.0
       hooks:
         - id: mypy
           args: [--strict, src/]
           additional_dependencies: [pydantic, pydantic-settings]
   ```
2. **新增 `.github/workflows/security.yml`**:加入 pip-audit / safety 依赖漏洞扫描 + GitHub CodeQL SAST 扫描。建议在 PR 与 push to main 时触发。
3. **新增 `.github/workflows/release.yml`**:基于 git tag 自动创建 GitHub Release,自动从 CHANGELOG.md 提取版本说明,并触发 Docker 镜像版本化构建。
4. **完善代码覆盖率上报**:在 `ci.yml` 的 unit test 步骤后,新增 `codecov/codecov-action@v4` 上传覆盖率到 codecov.io(README 徽章已就位,但实际数据未上报)。

---

### 维度 4: 安全性 — ⚠️ 需改进

| 检查项 | 状态 | 评估 |
|--------|------|------|
| `.env` 被 `.gitignore` 排除 | ✅ | `.env` / `.env.agent` / `.env.prod` / `.env.dev` / `.env.qa` 全部排除 |
| `!.env.template` 保留模板 | ✅ | 显式保留模板文件入仓 |
| 安全漏洞披露流程 | ✅ | SECURITY.md 含邮箱 + GitHub Security Advisory 双渠道 + SLA |
| 密钥管理规范 | ✅ | 仅环境变量注入,SHA256+BCrypt 双哈希,项目安全硬约束 |
| PII 保护 | ✅ | 会话内容加密存储,日志脱敏 |
| Prompt Injection 防护 | ✅ | Pydantic 校验 + 工具权限隔离 + 禁 eval/exec |
| CI 占位符密钥 | ✅ | ci.yml 使用 `sk-ci-placeholder` 占位符,非真实凭据 |
| 依赖安全扫描 | ❌ | 缺 pip-audit / safety / Dependabot security alerts 自动化 |
| 密钥泄露扫描 | ❌ | 缺 gitleaks / trufflehog 在 CI 中扫描提交历史 |
| 容器镜像扫描 | ❌ | 缺 Trivy / Grype 镜像漏洞扫描 |

**改进建议**:
1. **启用 GitHub Dependabot security alerts**:在仓库 Settings → Code security 中启用 Dependabot security updates(当前 dependabot.yml 仅做版本更新,未明确启用安全告警)。
2. **新增密钥泄露扫描**:在 CI 中加入 `gitleaks/gitleaks-action` 扫描每个 PR 与提交,防止密钥意外入仓。
3. **新增依赖漏洞扫描**:在 CI 中加入 `pip-audit` 扫描 `requirements.txt` 与 `requirements-dev.txt` 的已知 CVE。
4. **新增容器镜像扫描**:在 Docker 构建后加入 `aquasecurity/trivy-action` 扫描镜像漏洞,阻断高危漏洞合并。

---

### 维度 5: 社区建设 — ❌ 缺失

| 检查项 | 状态 | 评估 |
|--------|------|------|
| Discussions 区 | ⚠️ | config.yml 有 discussions 链接,但需在 GitHub 仓库实际开启 Discussions 功能 |
| Discord/Slack 链接 | ❌ | 无即时通讯社区入口 |
| 赞助/捐赠 `.github/FUNDING.yml` | ❌ | 缺 FUNDING.yml |
| 项目路线图 Roadmap | ❌ | CHANGELOG.md `[Unreleased]` 有 3 条计划,但缺独立 `docs/ROADMAP.md` |
| FAQ | ❌ | 缺独立 FAQ 文档 |
| 贡献者认可机制 | ❌ | 缺 All Contributors / CONTRIBUTORS.md / 贡献者徽章 |
| 联系方式 | ⚠️ | SECURITY.md 有 security 邮箱;无通用联系邮箱 |

**改进建议**:
1. **新增 `docs/ROADMAP.md`**:发布清晰的版本路线图(如 v0.2.0 / v0.3.0 / v1.0.0 计划),包含已规划功能、正在开发、待定功能三类。开源项目有 Roadmap 能让贡献者了解项目方向,提升参与意愿。可从 CHANGELOG.md `[Unreleased]` 扩展。
2. **新增 `.github/FUNDING.yml`**:如有接受赞助意愿,配置 GitHub Sponsors / Open Collective / 爱发电等平台。即便暂不接受赞助,留空文件也表明态度。
3. **新增 `docs/faq.md`**:集中常见问题,减少重复 Issue。建议覆盖:部署、配置、API 调用、报告格式、搜索引擎选择、MCP 配置、性能优化等高频问题。
4. **新增 `CONTRIBUTORS.md`**:列出所有贡献者(可用 `all-contributors` bot 自动维护),认可社区贡献。
5. **考虑新增 Discord/微信交流群入口**:在 README 添加社区入口链接(如 Discord 邀请链接或微信群二维码),降低交流门槛。
6. **在 GitHub 仓库实际开启 Discussions 功能**:config.yml 已引用,需在仓库 Settings → Features 勾选 Discussions。

---

### 维度 6: 代码质量 — ⚠️ 需改进

| 检查项 | 状态 | 评估 |
|--------|------|------|
| `pyproject.toml` ruff 配置 | ✅ | target-version=py312, line-length=100, 选 E/F/W/I/N/B/C4/UP/ASYNC |
| `pyproject.toml` mypy 配置 | ✅ | strict=true, warn_return_any, disallow_untyped_defs |
| `pyproject.toml` pytest 配置 | ✅ | asyncio_mode=auto, testpaths, markers, filterwarnings |
| 类型提示 | ✅ | mypy --strict 强制,CI 中执行 |
| pytest-cov 覆盖率 | ✅ | CI 中 `--cov=src --cov-report=term-missing` |
| 代码风格统一 | ✅ | ruff format,双引号,4 空格缩进 |
| pre-commit hooks | ❌ | 缺 `.pre-commit-config.yaml` |
| 覆盖率阈值 | ⚠️ | CI 未设 `--cov-fail-under=N` 阈值 |
| 代码覆盖率徽章 | ⚠️ | README 有 codecov 徽章,但实际未上报到 codecov.io |

**改进建议**:
1. **新增 `.pre-commit-config.yaml`**:见维度 3 建议。这是本维度最大缺口,本地无自动化检查导致贡献者提交前无法自查。
2. **设定覆盖率阈值**:在 `pyproject.toml` 或 CI 中设置 `--cov-fail-under=70`(或合理阈值),防止覆盖率下滑。
3. **集成 codecov 上报**:见维度 3 建议,让 README 的 codecov 徽章有真实数据。

---

### 维度 7: 包管理 — ⚠️ 需改进

| 检查项 | 状态 | 评估 |
|--------|------|------|
| `pyproject.toml` | ⚠️ | 存在,但仅配置工具(ruff/mypy/pytest),`[project]` 段缺 `dependencies` 与 `dev-dependencies` 声明 |
| pip-tools 使用 | ✅ | `requirements.in` + `requirements.txt`(锁定)+ `requirements-dev.in` + `requirements-dev.txt`(锁定) |
| dev 依赖分离 | ✅ | 运行时依赖在 requirements.txt,开发依赖追加在 requirements-dev.txt |
| 依赖版本锁定 | ✅ | 全部 `==` 精确锁定,注释清晰,按模块分组 |
| 构建后端声明 | ❌ | pyproject.toml 缺 `[build-system]` 段(未声明 setuptools/hatchling/pdm-backend) |
| 包可安装性 | ❌ | 缺 `__version__` 在 `src/__init__.py`,未配置 `pip install -e .` 能力 |

**现状分析**:
当前项目采用 `requirements.txt` + `pip install -r` 的传统模式,而非 `pyproject.toml` + `pip install .` 的现代 PEP 517 模式。这在「不发布到 PyPI 的应用项目」中是可接受的,但对开源项目而言,完善 `pyproject.toml` 能让贡献者用 `pip install -e .[dev]` 一键安装。

**改进建议**:
1. **完善 `pyproject.toml` 的 `[project]` 段**:将 `requirements.in` 的依赖迁移到 `[project.dependencies]`,`requirements-dev.in` 迁移到 `[project.optional-dependencies.dev]`。保留 `requirements.txt` 作为锁定文件(`pip-compile` 从 pyproject.toml 生成)。
   ```toml
   [project]
   name = "agentinsight-researcher"
   version = "1.0.0"
   dependencies = ["langgraph>=1.2", "litellm>=1.6", ...]

   [project.optional-dependencies]
   dev = ["pytest>=8.3", "ruff>=0.8", "mypy>=1.13", ...]
   ```
2. **新增 `[build-system]` 段**:声明构建后端(如 `hatchling` 或 `setuptools`),使 `pip install -e .` 可用。
3. **保持 `requirements.txt` 锁定文件**:pip-compile 可从 pyproject.toml 生成锁定文件,兼顾现代声明与可复现性。

> ⚠️ 此项变更属「架构偏好」,需与维护者确认是否从 requirements.txt 模式迁移到 pyproject.toml 模式。当前模式可正常工作,仅为现代化建议。

---

### 维度 8: Docker/部署 — ✅ 完善

| 检查项 | 状态 | 评估 |
|--------|------|------|
| `Dockerfile` | ✅ | 多阶段构建(builder + runtime),python:3.12-slim,非 root 用户 |
| `docker-compose.yml` | ✅ | 7 服务编排,depends_on service_healthy,命名卷,健康检查 |
| 多阶段构建 | ✅ | builder 装依赖,runtime 仅复制产物 |
| 健康检查 | ✅ | 所有服务配置 healthcheck(interval/timeout/retries/start_period) |
| `.dockerignore` | ✅ | 排除 .venv/.git/tests/evals 等,减小构建上下文 |
| 三套构建模式 | ✅ | QA 离线 / 生产联网 / 生产离线,构建脚本内置 `-p agentinsight` |
| 非 root 运行 | ✅ | `USER agent`,符合容器安全最佳实践 |
| 镜像版本锁定 | ✅ | `postgres:17` / `redis:8` / `qdrant:v1.18.0`,无 `latest` |
| 端口最小暴露 | ✅ | postgres/redis/qdrant gRPC 绑定 127.0.0.1 |
| 资源限制 | ⚠️ | 仅 searxng 配置 mem_limit/cpus;其他服务未限资源 |

**亮点**:
- 多阶段构建 + 非 root 用户,符合容器安全最佳实践
- 三套构建模式覆盖开源社区(QA/联网/离线)全场景
- depends_on service_healthy 保证依赖顺序
- 镜像版本全部锁定,无 latest 漂移风险

**改进建议**:
1. **补充资源限制**:为 agent / embeddings / qdrant 等关键服务添加 `deploy.resources.limits`(内存/CPU),防止单服务异常耗尽宿主机资源。在 `docker-compose.yml` 中添加:
   ```yaml
   deploy:
     resources:
       limits:
         memory: 2G
         cpus: '2.0'
   ```
2. **考虑添加 `.env.example` 到 compose `env_file`**:当前 `env_file: [.env]`,若用户未创建 `.env` 会导致 compose 报错。可在文档中强化提示。

本维度已基本完善,上述建议为锦上添花。

---

### 维度 9: 可发现性 — ⚠️ 需改进

| 检查项 | 状态 | 评估 |
|--------|------|------|
| README 徽章 | ✅ | CI / codecov / release / issues / stars / license / Python / FastAPI / LangGraph / ruff 共 10 个 |
| 项目描述 | ✅ | "中文优先的研究分析智能体",中英双语,简洁清晰 |
| 在线 Demo | ✅ | 官方 Demo 测试页面(http://43.139.209.145/...),含 30 秒上手指南 |
| GitHub Topics | ❌ | 仓库设置层面的 topics(如 `ai-agent`/`langgraph`/`rag`/`research`),需在 GitHub 仓库手动添加,无法文件化 |
| 项目截图/GIF | ❌ | README 无截图,docs/ 无 assets/ 目录 |
| 项目视频 | ❌ | 无 Demo 视频/GIF |
| Social Preview | ❌ | 需在 GitHub 仓库 Settings 上传社交预览图(opengraph image) |
| SEO 关键词 | ⚠️ | README 缺 "Chinese-first AI research agent" / "LangGraph tutorial" 等英文 SEO 关键词 |

**改进建议**:
1. **在 README 顶部添加项目截图或 GIF**:截图是最有效的可发现性提升手段。建议添加:
   - 测试页面主界面截图(展示会话管理 + 流式渲染)
   - 研究报告生成过程的 GIF(展示流式输出 + 工具调用展示)
   - 多 Agent 协作流程图(可从 architecture.md 的 Mermaid 导出)
2. **在 GitHub 仓库添加 Topics**:建议添加 `ai-agent` / `langgraph` / `rag` / `research-agent` / `mcp` / `fastapi` / `qdrant` / `openai-compatible` / `chinese` 等 topics,提升 GitHub 搜索可发现性。
3. **上传 Social Preview 图**:在仓库 Settings → Social preview 上传一张 1280x640 的项目预览图,分享到社交媒体时有预览图。
4. **补充英文 SEO 关键词**:在 README English 段落补充 "Chinese-first AI research agent" / "LangGraph tutorial" 等可搜索关键词。

---

### 维度 10: 法律合规 — ⚠️ 需改进

| 检查项 | 状态 | 评估 |
|--------|------|------|
| MIT License | ✅ | 商业友好,与依赖兼容(LangGraph MIT / LiteLLM MIT / FastAPI MIT / Qdrant Apache 2.0 等) |
| 商标声明 | ✅ | README 末尾有 "AgentInsight" 商标声明,规范 |
| 第三方代码引用说明 | ❌ | 缺 `NOTICE` / `THIRD_PARTY_LICENSES.md`,未声明参考实现的第三方代码 |
| 数据隐私说明 | ❌ | SECURITY.md 提及 PII 保护,但缺独立隐私声明 |
| CITATION.cff | ❌ | 缺学术引用文件 |
| 许可证兼容性 | ⚠️ | 未显式声明依赖许可证兼容性审查;`curl_cffi` 是 LGPL,`onnxruntime` 是 MIT,需确认 |
| 依赖清单 | ⚠️ | requirements.txt 注释清晰,但未标注各依赖的许可证 |

**改进建议**:
1. **新增 `CITATION.cff`**:方便学术用户引用本项目。示例:
   ```yaml
   cff-version: 1.2.0
   title: agentinsight-researcher
   message: "If you use this software, please cite it as below."
   type: software
   authors:
     - given-names: AgentInsight
       family-names: Team
   repository-code: https://github.com/AgentInsight/agentinsight-researcher
   license: MIT
   version: 1.0.0
   date-released: 2026-07-04
   ```
2. **新增 `NOTICE` 或 `THIRD_PARTY_LICENSES.md`**:声明项目参考实现的第三方代码,列出关键第三方依赖及其许可证。可用 `pip-licenses` 工具自动生成。
3. **新增 `docs/PRIVACY.md`**:独立的数据隐私声明,说明:
   - 收集哪些用户数据(会话内容/上传文件/使用日志)
   - 数据如何存储与加密(会话内容加密存储)
   - 数据保留策略(TTL 30 天)
   - 用户数据权利(删除/导出)
   - Demo 环境的数据处理说明
4. **进行依赖许可证兼容性审查**:运行 `pip-licenses --from=classifier --format=markdown` 生成依赖许可证清单,确认全部与 MIT 兼容。重点关注 `curl_cffi`(LGPL)、`onnxruntime`(MIT)、`playwright`(Apache 2.0)等。

---

## 三、优化建议优先级排序

按「影响度 × 紧急度」排序,分为 P0(必须)、P1(推荐)、P2(锦上添花)三级。

### P0 — 必须修复(开源前阻断项)

| # | 建议 | 维度 | 理由 |
|---|------|------|------|
| 1 | 新增 `.pre-commit-config.yaml` | 3/6 | 本地无自动化检查,贡献者提交前无法自查,CI 往返成本高 |
| 2 | 启用 GitHub Dependabot security alerts | 4 | 依赖漏洞无自动告警,安全风险 |
| 3 | 新增密钥泄露扫描(gitleaks) | 4 | 防止密钥意外入仓,项目安全硬约束的自动化保障 |
| 4 | 新增 `CITATION.cff` + `NOTICE`/`THIRD_PARTY_LICENSES.md` | 10 | 法律合规硬要求,缺第三方引用说明有法律风险 |

### P1 — 推荐修复(开源后 1-2 周内)

| # | 建议 | 维度 | 理由 |
|---|------|------|------|
| 5 | 新增 `docs/ROADMAP.md` | 5 | 提升贡献者参与意愿,展示项目方向 |
| 6 | 新增 `docs/faq.md` | 2/5 | 减少重复 Issue,降低维护成本 |
| 7 | 新增 `docs/deployment.md` | 2 | 部署信息分散,新用户上手门槛高 |
| 8 | README 添加项目截图/GIF | 9 | 提升第一印象,显著增加 star 数 |
| 9 | 新增依赖安全扫描(pip-audit) + 容器镜像扫描(Trivy) | 4 | 纵深防御,多层安全扫描 |
| 10 | 完善 codecov 上报 + 设覆盖率阈值 | 3/6 | README 徽章已就位但无数据;设阈值防下滑 |
| 11 | 新增 `docs/PRIVACY.md` | 10 | 独立隐私声明,合规透明 |
| 12 | 新增 `.github/workflows/release.yml` | 3 | 自动化 Release 流程,降低维护负担 |

### P2 — 锦上添花(开源后按需)

| # | 建议 | 维度 | 理由 |
|---|------|------|------|
| 13 | 完善 `pyproject.toml` 的 `[project]` 与 `[build-system]` | 7 | 现代化包管理,支持 `pip install -e .[dev]` |
| 14 | 新增 `CONTRIBUTORS.md` | 5 | 认可贡献者,提升社区活跃度 |
| 15 | 新增 `.github/FUNDING.yml` | 5 | 接受赞助入口(即便暂不启用) |
| 16 | 新增 Discord/微信群入口 | 5 | 降低交流门槛 |
| 17 | 补充服务资源限制 | 8 | 防单服务耗尽宿主机资源 |
| 18 | 新增 `docs/development.md` | 2 | 开发者指南细化 |
| 19 | 添加英文 SEO 关键词 + Social Preview | 9 | 提升英文社区可发现性 |
| 20 | GitHub 仓库添加 Topics | 9 | 提升搜索可发现性(仓库设置层面,非文件) |

---

## 四、与同类开源项目对比

| 维度 | 本项目 | 同类开源项目(对标) | 评价 |
|------|--------|---------------------|------|
| 必备文件 | 全部双语 | 单语(英文) | ✅ 优于 |
| 项目规范 | 14 章详尽 | 无 | ✅ 优于 |
| 架构文档 | Mermaid 图 + 双语 | 有架构图 | ✅ 相当 |
| API 文档 | 19 端点 + 示例 | 基础 | ✅ 优于 |
| CI/CD | 4 Job + 评测门禁 | 基础 CI | ✅ 优于 |
| 安全策略 | SECURITY.md + SLA | 无 | ✅ 优于 |
| 社区建设 | 缺 Roadmap/FAQ | 有 Roadmap | ❌ 待补齐 |
| 项目截图 | 缺 | 有 | ❌ 待补齐 |
| 在线 Demo | 有 | 有 | ✅ 相当 |

**结论**:项目在工程规范、文档体系、CI/CD、安全策略上已**优于同类对标项目**;主要差距在社区建设( Roadmap / FAQ / 截图)与法律合规补充(CITATION / NOTICE)。

---

## 五、总结

`agentinsight-researcher` 项目在工程化、文档化、规范化方面表现优秀,已具备直接开源的基础条件。**P0 级 4 项建议**建议在开源前完成( pre-commit 配置、安全告警、密钥扫描、法律文件);**P1 级 8 项建议**建议在开源后 1-2 周内逐步完成;**P2 级 8 项建议**按需推进。

完成 P0 + P1 后,项目开源就绪度可达 ⭐⭐⭐⭐⭐ (5/5)。

---

> 本报告基于 2026-07-11 项目实际文件状态评估。如项目后续有变更,建议定期复评。
