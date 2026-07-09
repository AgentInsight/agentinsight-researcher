## 变更描述

<!-- 简述本 PR 做了什么, 为什么 -->

## 变更类型

- [ ] feat (新功能)
- [ ] fix (Bug 修复)
- [ ] docs (文档)
- [ ] refactor (重构)
- [ ] perf (性能)
- [ ] test (测试)
- [ ] chore (构建/工具)

## 质量门禁自查 (AGENTS.md 第 13 章)

- [ ] 单元测试通过 (`pytest tests/unit/ -q`)
- [ ] `ruff check .` 通过
- [ ] `ruff format --check .` 通过
- [ ] `mypy src/ --strict` 通过
- [ ] 新功能有对应单元测试
- [ ] 文档已更新 (如涉及)

## 架构边界自查 (AGENTS.md 第 3 / 4 / 5 章)

- [ ] 依赖方向单向向内 (`common/` 不依赖 `agents/` 或业务模块)
- [ ] `tools/`/`rag/`/`llm/`/`memory/` 未互相 import (共享逻辑下沉 `common/`)
- [ ] 未引入 `AGENTS.md` 第 4 章"不推荐清单"中的方案 (如已引入, 已在备注说明优势理由)
- [ ] LangGraph 图结构/State schema 变更已与维护者确认 (属 "Ask first" 级变更)
- [ ] 子智能体代码按名称隔离在 `agents/<agent_name>/`、`config/<agent_name>/`、`skills/<agent_name>/` 下
- [ ] 临时文件已放入 `temp/` (未污染 `tests/` 正式分层或项目根目录)

## 安全合规自查 (AGENTS.md 第 11 章 - 硬约束)

- [ ] 无硬编码密钥/密码 (密钥仅环境变量注入)
- [ ] 无 `eval`/`exec` 求值用户输入
- [ ] 无 `.env` / `.env.qa` / 凭据文件入仓
- [ ] PII 已脱敏 (会话内容加密存储, 日志脱敏)
- [ ] 未通过 PowerShell 原生命令修改项目文件 (统一使用专用工具)
- [ ] JWT token 未写入日志或持久化存储 (仅保留解析后的 `user_id`)
- [ ] 工具调用权限已显式声明 (`read`/`write`/`execute`/`network`)
- [ ] LLM 输出经结构化校验后再入工具

## 数据隔离自查 (AGENTS.md 第 6 / 7 章)

- [ ] 持久化层以 `agent_id` (= `agent_name`) 区分各 Agent
- [ ] 用户私有数据按 `user_id` 区分 (Postgres 业务表 / Redis 缓存 / Qdrant 用户导入数据)
- [ ] Qdrant 检索显式传目标 namespace 列表 (共享 + 当前用户私有)
- [ ] 会话隔离键 `thread_id` 从请求上下文注入, 未由客户端自造
- [ ] 业务表查询显式 `WHERE agent_id = ... AND user_id = ...`

## 可观测性自查 (AGENTS.md 第 10 章)

- [ ] 节点包裹在 AgentInsight trace span 内 (未使用已弃用的 `@agentinsight.observe`)
- [ ] 未直接使用 `opentelemetry-sdk` 原生 API (统一经 `observability/tracing.py` 6 类 `trace_xxx`)
- [ ] 认证上下文未通过 span 上下文传递 (经 `contextvars` + State 字段)
- [ ] 未使用观察者模式 (Subject/Observer/attach/notify) 实现追踪

## 关联 Issue

Closes #<issue-number>

## 备注

<!-- 截图 / 测试结果 / 偏差说明 / 其他说明 -->
