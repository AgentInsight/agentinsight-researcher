# tests/ 测试目录

> AGENTS.md 第 13 章测试规则落地. 本目录承载 agentinsight-researcher 全部测试用例.

## 1. 测试分层结构

按执行环境与触发时机分 7 层 (与 AGENTS.md 第 13 章 7 类测试对齐), 每层独立目录:

| 类型 | 目录 | 执行环境 | 触发时机 | pytest mark |
|------|------|---------|---------|-------------|
| 冒烟 (Smoke) | `unit/test_smoke.py` / `functional/test_smoke_functional.py` | 本地 / 容器栈 | 每次提交、容器启动后 | `unit` / `functional` |
| 单元 (Unit) | `unit/` | 本地 / 构建期 | 每次 commit、Docker build | `unit` |
| 功能 (Functional) | `functional/` | 部署后容器栈 | 容器栈 `service_healthy` 后 | `functional` |
| 回归 (Regression) | `regression/` | 部署后容器栈 | 合并 main 前门禁 | `regression` |
| API 端到端 (API/E2E) | `api/` / `e2e/` | 部署后容器栈 | 容器栈健康后 / 合并 main 前 | `api` / `e2e` |
| 探索性 (Exploratory) | `exploratory/` | 部署后容器栈 | 边界/降级场景, 非门禁 | `exploratory` |
| 性能 (Performance) | `performance/` | 部署后容器栈 | 延迟/吞吐/负载 | `performance` |

> **集成测试** 跨分层落地: `unit/test_*_integration.py` (mock 化集成, 构建期) 与
> `functional/test_*_e2e.py` (容器栈端到端, 部署后) 均承载集成场景.

## 2. 7 类测试目录映射

```
tests/
├── unit/           # 单元测试 (构建期, 不依赖外部服务)
│   ├── test_smoke.py                       # 冒烟: 模块导入 + 配置加载
│   ├── test_v4p3_integration.py            # 集成: V4-P3 三层路由 (mock 化)
│   ├── test_skills_context_manager.py      # ContextManager 静态纯函数
│   ├── test_skills_mcp_coordinator.py      # MCPCoordinator 缓存键生成
│   ├── test_bm25_filter.py / test_bm25_integration.py
│   ├── test_llmlingua_compressor.py
│   ├── test_tei_circuit_breaker.py         # 安全: TEI 熔断器
│   ├── test_api_security.py                # 安全: API 鉴权/注入
│   ├── test_security_injection.py          # 安全: Prompt Injection
│   └── ...
├── functional/     # 功能测试 (部署后容器栈)
│   ├── test_smoke_functional.py            # 冒烟: 容器栈健康检查
│   ├── test_container_health.py            # 容器健康检查
│   ├── test_qdrant_service.py / test_embeddings_service.py
│   ├── test_mcp_research_flow.py           # MCP HTTP 端到端 (真实容器)
│   └── test_mcp_research_e2e.py            # MCP 协调器端到端 (mock MCP Server)
├── regression/     # 回归测试 (合并 main 前门禁)
│   ├── test_research_basic_report.py
│   ├── test_session_persistence.py
│   └── test_short_query.py
├── api/            # API 端到端 (OpenAI 兼容接口)
│   ├── test_chat_completions.py            # /v1/chat/completions 流式 + 非流式
│   ├── test_mcp_endpoints.py               # /v1/mcp CRUD
│   ├── test_security.py                    # 安全: Bearer JWT 身份解析
│   ├── test_security_injection.py          # 安全: 注入防护
│   ├── test_feedback.py / test_files.py
├── e2e/            # 端到端 (完整链路)
│   ├── test_api_flow.py                    # 提问 → 检索 → 工具 → 流式 → 持久化
│   └── test_page_flow.py                   # 前端测试页面联调
├── exploratory/    # 探索性 (边界/降级)
│   ├── test_degradation.py                 # 降级场景
│   └── test_edge_cases.py                  # 边界用例
├── performance/    # 性能 (延迟/吞吐/负载)
│   ├── test_latency.py / test_throughput.py / test_load.py
│   ├── test_performance_baseline.py
│   └── test_performance_rag.py
├── conftest.py     # 全局配置: .env 加载 + 容器栈可达性自动跳过
└── REPORT.md       # 测试报告
```

## 3. 测试执行命令

### 3.1 全量执行

```bash
# 全量测试 (含单元/功能/回归/API/e2e/性能)
pytest tests/ -v

# 仅单元测试 (构建期, 不依赖容器栈)
pytest tests/unit/ -v -m unit

# 仅功能测试 (需容器栈 healthy)
pytest tests/functional/ -v -m functional

# 回归门禁 (合并 main 前)
pytest tests/regression/ -v -m regression

# API + E2E (合并 main 前/发布前)
pytest tests/api/ tests/e2e/ -v

# 性能测试
pytest tests/performance/ -v -m performance
```

### 3.2 按目标执行

```bash
# V4-P3 三层路由集成测试 (mock 化, 不依赖容器栈)
pytest tests/unit/test_v4p3_integration.py -v --no-header

# MCP 协调器端到端 (mock MCP Server, 不依赖容器栈)
pytest tests/functional/test_mcp_research_e2e.py -v --no-header

# MCP HTTP 端到端 (需容器栈 healthy)
pytest tests/functional/test_mcp_research_flow.py -v -m functional

# 质量门禁 (构建期)
pytest tests/unit/ -q && ruff check . && ruff format --check . && mypy src/ --strict

# 评测门禁 (CI 强制)
python -m evals.rag.run --dataset evals/rag/dataset.json
python -m evals.agent.run --dataset evals/agent/dataset.json
```

### 3.3 容器栈依赖测试 (功能/回归/API/e2e/性能)

```bash
# 1. 启动容器栈 (生产联网模式)
docker compose -p agentinsight up -d --wait

# 2. 注入测试目标地址 (宿主机访问)
set AGENT_URL=http://127.0.0.1:8066

# 3. 执行依赖容器栈的测试
pytest tests/functional/ tests/api/ tests/e2e/ tests/regression/ -v

# 4. 清理
docker compose -p agentinsight down -v
```

> `tests/conftest.py` 在容器栈未运行时自动跳过 `functional`/`regression`/`api`/`e2e`/
> `performance`/`exploratory` 标记的测试, 避免大量连接失败噪声.

## 4. V4-P3 三层方案测试覆盖矩阵

V4-P3 三层路由 (`src/skills/researcher/context_manager.py:get_similar_content`) 测试
集中在 `tests/unit/test_v4p3_integration.py`, 全部 mock 化, 构建期可执行.

### 4.1 三层路由边界 (TestV4P3LayerRouting)

| 测试用例 | 触发条件 | 期望路由 | 验证点 |
|---------|---------|---------|--------|
| `test_layer1_fast_path_small_docs` | total_chars < 8000 ∧ doc_count ≤ max_results | Layer 1 Fast Path | 直接拼接原文, 不调 `_bm25_filter` / `_embeddings_filter` |
| `test_layer2_bm25_medium_docs` | 8000 ≤ total_chars < 50000 | Layer 2 BM25Filter | 调用 `_bm25_filter`, 不调 `_embeddings_filter` |
| `test_layer3_embeddings_large_docs` | total_chars ≥ 50000 | Layer 3 EmbeddingsFilter | 调用 `_embeddings_filter`, 不调 `_bm25_filter` |

### 4.2 L1 抓取降级链 (TestV4P3L1FallbackChain)

| 测试用例 | 场景 | 期望行为 |
|---------|------|---------|
| `test_fallback_chain_tf_to_bs_to_playwright` | Trafilatura 失败 → BS+markdownify 失败 → Playwright 成功 | 三级 scraper 依次实例化, 返回 Playwright 结果 |
| `test_fallback_chain_stops_at_first_success` | Trafilatura 成功 (≥100 chars) | 不触发 BS+markdownify/Playwright 降级 |
| `test_fallback_chain_lightweight_skips_playwright` | lightweight 模式 + Trafilatura 失败 | 立即返回, 跳过 BS+markdownify 与 Playwright |

### 4.3 L2 串联 (TestV4P3L2Chain)

| 测试用例 | 场景 | 期望行为 |
|---------|------|---------|
| `test_bm25_output_then_post_filter` | bm25_filter_enabled=True + BM25 输出 chunks | _post_filter_compress 被调用, 结果经去重和截断 |

### 4.4 降级策略 (TestV4P3DegradeStrategy)

| 测试用例 | 故障注入 | 降级路径 |
|---------|---------|---------|
| `test_bm25_filter_timeout_degrades_to_keyword_match` | BM25Filter.filter 抛 TimeoutError | `_keyword_fallback_split` (返回 list[str]) |
| `test_embeddings_circuit_open_degrades_to_keyword_match` | `is_circuit_open()=True` (TEI 熔断) | `_keyword_fallback` (返回 str), 不调 bm25/embeddings filter |
| `test_embeddings_filter_exception_degrades_to_keyword_match` | EmbeddingsFilter.filter 抛 RuntimeError | `_keyword_fallback_split` (返回 list[str]) |

## 5. MCP 调用测试清单

MCP 工具调用 (`src/skills/researcher/mcp_coordinator.py`) 测试分布在两个文件:

### 5.1 单元层: 缓存键生成 (`tests/unit/test_skills_mcp_coordinator.py`)

验证 `_make_cache_key` 输出 sha256(query + tool_name + json(args)) 格式, 相同输入生成
相同 key, 不同输入生成不同 key (12 个用例).

### 5.2 功能层: 协调器端到端 (`tests/functional/test_mcp_research_e2e.py`)

> 全部 mock 化 (mock MCP Server / MultiServerMCPClient / LLMClient), 不实际启动
> MCP Server, 不依赖容器栈. 使用 `unit` mark 保证构建期可执行.

#### TestMCPCoordinatorEndToEnd (5 用例)

| 测试用例 | 验证点 |
|---------|--------|
| `test_mcp_tool_selection_and_call` | LLM 选 search_tool → ainvoke 被调用 → 结果进 contexts; 未选工具不被调用 |
| `test_mcp_result_injected_into_research` | MCP 工具返回的"行业数据"字符串出现在 contexts 列表 |
| `test_mcp_failure_degrades_gracefully` | tool.ainvoke 抛异常 → conduct_research 返回 [] (不抛异常) |
| `test_mcp_no_configs_returns_empty` | mcp_configs=[] → 早期返回 [] |
| `test_mcp_disabled_strategy_returns_empty` | strategy=disabled → 直接返回 [], 不连 MCP Server |

#### TestMCPResearchFlow (6 用例)

| 测试用例 | 验证点 |
|---------|--------|
| `test_research_with_mcp_enabled` | strategy=fast + 有配置 → 透传 conduct_research 结果 |
| `test_research_with_mcp_disabled` | strategy=disabled → 早期返回 [], 不调协调器 |
| `test_research_with_no_user_configs_returns_empty` | 用户无启用配置 → 早期返回 [], 不调 conduct_research |
| `test_mcp_call_traced_in_span` | trace_tool span 被进入 (name=mcp-research), span.update 记录 success=True |
| `test_mcp_failure_traced_with_success_false` | _execute_mcp 抛异常 → span.update 记录 success=False (不抛异常) |
| `test_mcp_fast_strategy_caches_result` | 同 query 二次调用 → tool.ainvoke 仅触发一次 (fast 缓存复用) |

### 5.3 HTTP 端到端 (`tests/functional/test_mcp_research_flow.py`)

> 需容器栈 healthy, 通过 httpx 调用真实 `/v1/mcp` 与 `/v1/chat/completions` 端点.
> 验证 MCP 配置 CRUD + 数据隔离 + 不可达 MCP 降级 + clear_cache 失效.

| 测试用例 | 验证点 |
|---------|--------|
| `test_mcp_config_crud_end_to_end` | 创建 → 列出 → 更新 → 删除 (agent_id+user_id 隔离) |
| `test_mcp_config_data_isolation_per_user` | 同一用户多次列出结果一致 |
| `test_mcp_tool_called_in_research_flow` | 不可达 MCP → 研究流程不崩溃, 报告正常生成 |
| `test_mcp_disabled_strategy_skips_call` | enabled=False → 研究流程不受影响 |
| `test_mcp_config_update_invalidates_cache` | PUT 切换 enabled → clear_cache → 下次研究流程不命中过期缓存 |
| `test_mcp_tool_timeout_does_not_block_research` | 黑洞 IP MCP → 研究流程总时长 < 5 分钟 |
| `test_mcp_clone_system_config` | POST /v1/mcp/system/{id}/clone → is_system=False, enabled=False |
| `test_mcp_test_endpoint_returns_result` | POST /v1/mcp/test → {success, message, tools_count, latency_ms} |
| `test_mcp_test_endpoint_missing_command_returns_error` | stdio 缺 command → 422 |
| `test_mcp_test_endpoint_missing_url_returns_error` | sse 缺 server_url → 422 |

## 6. 测试约定

- **数据隔离**: Qdrant `namespace=test_*` + `user_id=test_*` + `session_id=test_*`, 测试结束清理
  (AGENTS.md 第 13 章, 第 11 章 PII 安全硬约束).
- **目标地址**: 测试目标地址从环境变量 `AGENT_URL` 注入 (默认 `http://127.0.0.1:8066`),
  禁止硬编码.
- **独立性**: 测试用例独立可重复运行, 不依赖执行顺序; 用例间通过 fixture 清理状态.
- **mock 化**: 单元测试不依赖外部服务 (Postgres/Qdrant/Redis/LLM), 全部 mock.
- **容器栈依赖**: 功能/回归/API/e2e/性能测试在 `docker compose up -d` 且全部容器
  `service_healthy` 后执行; 容器栈未运行时自动跳过 (`conftest.py` 钩子).
- **CI 流水线顺序**:
  1. 构建镜像 + 单元测试 (失败即终止)
  2. `docker compose up -d` + 等待全部健康检查通过
  3. 功能 → API → 回归 → e2e (按序, 前者失败后者不执行)
  4. 任一环节失败阻断合并; 全部通过后 `docker compose down -v` 清理
