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

> **手动调试脚本** (`manual/`): 非自动化测试, 不纳入 CI 流水线, pytest 自动忽略
> (`addopts = "--ignore=tests/manual"`), 已加入 `.gitignore` 不入仓. 详见第 9 节.

## 2. 7 类测试目录映射

```
tests/
├── unit/           # 单元测试 (构建期, 不依赖外部服务, 全部 mock)
│   ├── conftest.py
│   │
│   │ # ── 冒烟 / 配置 ──
│   ├── test_smoke.py                       # 冒烟: 模块导入 + 配置加载
│   ├── test_config.py                      # Settings SSOT 配置校验
│   │
│   │ # ── API / 路由层 ──
│   ├── test_server.py                      # FastAPI app 装配 + lifespan
│   ├── test_routes.py                      # /v1/chat/completions 路由
│   ├── test_api_middleware.py              # 中间件 (CORS/安全头/限流)
│   ├── test_api_security.py                # 安全: API 鉴权
│   ├── test_api_websocket.py               # /v1/ws/{session_id} WebSocket
│   ├── test_api_mcp_routes.py              # /v1/mcp CRUD 路由
│   ├── test_api_agent_discovery.py         # Agent 发现端点
│   ├── test_api_feedback_queue.py          # /v1/feedback 人在回路队列
│   ├── test_ip_user_resolver.py            # IP-based 用户身份解析 (SHA256, 不存储原 IP)
│   ├── test_daily_report_limit.py          # 每日报告限额 (IP 限流)
│   │
│   │ # ── Graph / 编排层 ──
│   ├── test_graph_builder.py               # StateGraph 构建器
│   ├── test_graph_edges.py                 # 条件边 / 路由
│   ├── test_graph_nodes.py                 # 节点纯函数
│   ├── test_state.py                       # State schema + reducer
│   ├── test_multi_agent_builder.py         # 多 Agent 构建 (Supervisor/Swarm)
│   ├── test_agents_supervisor.py           # Supervisor 子图
│   ├── test_agents_reviewer.py             # Reviewer 子智能体
│   ├── test_agents_reviser.py              # Reviser 子智能体
│   ├── test_agents_fact_checker.py         # FactChecker 子智能体
│   ├── test_chat_builder.py                # ChatBuilder 消息组装
│   │
│   │ # ── Skills / 技能层 ──
│   ├── test_skills_context_manager.py      # ContextManager 静态纯函数
│   ├── test_skills_mcp_coordinator.py      # MCPCoordinator 缓存键生成
│   ├── test_skills_prompts.py              # PromptFamily 模板
│   ├── test_skills_query_classifier.py     # 查询分类器
│   ├── test_skills_searchers.py            # 搜索器基类
│   ├── test_skills_source_curator.py       # SourceCurator 可信度评分 (_score_credibility)
│   ├── test_source_curator.py              # SourceCurator.curate_sources 综合排序
│   ├── test_skills_report_generator.py     # ReportGenerator 报告生成
│   ├── test_skills_publisher.py            # Publisher 导出分发
│   ├── test_skills_image_generator.py      # ImageGenerator 图片生成
│   │
│   │ # ── RAG / 检索层 ──
│   ├── test_retriever.py                   # Retriever 混合检索
│   ├── test_rag_retriever_extended.py      # Retriever 扩展 (过滤/降级)
│   ├── test_rag_embeddings.py              # Embeddings (TEI 客户端)
│   ├── test_rag_qdrant_manager.py          # QdrantManager 集合/命名空间
│   ├── test_rag_fallback_integration.py    # RAG 降级集成
│   ├── test_qdrant_namespace.py            # namespace 隔离 (共享/私有)
│   ├── test_bm25_filter.py                 # BM25Filter 关键词过滤
│   ├── test_bm25_integration.py            # BM25 集成
│   ├── test_bm25_redis_cache.py            # BM25 Redis 缓存
│   ├── test_score_threshold.py             # 分数阈值过滤
│   ├── test_embeddings_migration.py        # Embeddings 模型迁移 (bge-large→bge-base 768维)
│   │
│   │ # ── Scrapers / 抓取层 ──
│   ├── test_scrapers.py                    # 抓取器基类
│   ├── test_scraper_routing.py             # 抓取器路由
│   ├── test_trafilatura_scraper.py         # Trafilatura 抓取器
│   ├── test_bs_markdownify_scraper.py      # BS+markdownify 抓取器
│   ├── test_normalize_markdown.py          # Markdown 归一化
│   ├── test_close_http_client.py           # 共享 httpx 客户端关闭 (lifespan shutdown)
│   ├── test_searcher_registry.py           # 搜索器注册表
│   ├── test_metaso_searcher.py             # 秘塔搜索: payload/headers/响应解析 (mock httpx)
│   ├── test_searxng_config.py              # SearXNG keep_only 21 引擎配置 (mwmbl 已移除)
│   ├── test_duckduckgo_removed.py          # DuckDuckGo 移除验证 (SearXNG 替代)
│   │
│   │ # ── LLM / 网关层 ──
│   ├── test_llm.py                         # LLMClient (LiteLLM 网关)
│   ├── test_llm_key_resolver.py            # LLM 密钥解析
│   ├── test_llm_classify_fallback.py       # LLM 分类降级
│   ├── test_token_budget.py                # Token 预算
│   │
│   │ # ── Memory / 会话层 ──
│   ├── test_memory_checkpointer.py         # PostgresSaver Checkpointer
│   ├── test_memory_db_initializer.py       # DB 初始化 (init.sql 幂等)
│   ├── test_memory_report_store.py         # 报告存储
│   ├── test_checkpointer_unified.py        # 统一 Checkpointer
│   │
│   │ # ── MCP / 工具层 ──
│   ├── test_mcp_research_integration.py    # MCP 研究集成
│   ├── test_mcp_transport_modes.py         # MCP 传输模式 (stdio/sse)
│   │
│   │ # ── 可观测性 ──
│   ├── test_tracing.py                     # AgentInsight 6 类 trace span
│   ├── test_tracing_extended.py            # 追踪扩展 (span 传播/降级)
│   ├── test_agentinsight_client.py         # AgentInsight 客户端
│   │
│   │ # ── 研究流程 / 性能优化 ──
│   ├── test_research_conductor.py          # ResearchConductor 主流程
│   ├── test_deep_research.py               # 深度研究
│   ├── test_v4p3_integration.py            # 集成: 三层路由 (mock 化)
│   ├── test_v2_alignment.py                # V2 对齐优化 7 项
│   ├── test_scraper_enhancements.py        # 抓取器增强: 池化/域名限流/图片评分
│   ├── test_phase4.py                      # Phase 4 (文件上传+动态角色+静态页)
│   ├── test_context_compression.py         # 上下文压缩
│   ├── test_fast_fail.py                   # 快速失败
│   ├── test_fast_tier.py                   # ReportGenerator FAST tier 降级
│   ├── test_chapter_parallel.py            # 章节并行
│   ├── test_pipeline_parallel.py           # 流水线并行化 (plan_research 与私有数据并行)
│   ├── test_publisher_export.py            # Publisher 导出
│   ├── test_human_node_integration.py      # 人在回路节点
│   ├── test_chitchat_responder.py          # 闲聊响应器
│   ├── test_redis_client.py                # Redis 客户端
│   ├── test_pdf_docx_fonts.py              # PDF/DOCX 字体: Dockerfile + debs 静态检查
│   ├── test_tei_circuit_breaker.py         # 安全: TEI 熔断器
│   └── __init__.py
├── functional/     # 功能测试 (部署后容器栈)
│   ├── test_smoke_functional.py            # 冒烟: 容器栈健康检查
│   ├── test_container_health.py            # 容器健康检查
│   ├── test_qdrant_service.py              # Qdrant 服务
│   ├── test_embeddings_service.py          # Embeddings 服务
│   ├── test_postgres_service.py            # PostgreSQL 服务
│   ├── test_redis_service.py               # Redis 服务
│   ├── test_openai_compat_endpoint.py      # OpenAI 兼容端点
│   ├── test_mcp_research_flow.py           # MCP HTTP 端到端 (真实容器)
│   ├── test_mcp_research_e2e.py            # MCP 协调器端到端 (mock MCP Server)
│   └── __init__.py
├── regression/     # 回归测试 (合并 main 前门禁)
│   ├── test_research_basic_report.py       # 基础研究报告
│   ├── test_session_persistence.py         # 会话持久化
│   ├── test_short_query.py                 # 短查询
│   └── __init__.py
├── api/            # API 端到端 (OpenAI 兼容接口)
│   ├── test_chat_completions.py            # /v1/chat/completions 流式 + 非流式
│   ├── test_mcp_endpoints.py               # /v1/mcp CRUD
│   ├── test_security.py                    # 安全: Bearer JWT 身份解析
│   ├── test_security_injection.py          # 安全: 注入防护
│   ├── test_feedback.py                    # /v1/feedback 人在回路
│   ├── test_files.py                       # 文件上传
│   ├── test_reports.py                     # 报告查询/下载
│   ├── test_tool_permissions.py            # 工具权限隔离 (read/write/execute/network)
│   └── __init__.py
├── e2e/            # 端到端 (完整链路)
│   ├── test_api_flow.py                    # 提问 → 检索 → 工具 → 流式 → 持久化
│   ├── test_page_flow.py                   # 前端测试页面联调
│   ├── test_human_in_loop.py               # 人在回路完整链路
│   ├── test_mcp_e2e_example.py             # MCP 端到端示例
│   └── __init__.py
├── exploratory/    # 探索性 (边界/降级)
│   ├── test_degradation.py                 # 降级场景
│   ├── test_edge_cases.py                  # 边界用例
│   ├── test_config_combinations.py         # 配置组合
│   └── __init__.py
├── performance/    # 性能 (延迟/吞吐/负载)
│   ├── conftest.py                         # performance 层 fixture
│   ├── test_latency.py                     # 延迟
│   ├── test_throughput.py                  # 吞吐
│   ├── test_load.py                        # 负载
│   ├── test_concurrent_load.py             # 并发负载
│   ├── test_performance_baseline.py        # 性能基线
│   ├── test_performance_rag.py             # RAG 性能
│   ├── test_v4p3_performance.py            # 三层路由性能
│   ├── test_context_compression_perf.py    # 上下文压缩性能
│   └── __init__.py
├── manual/         # 手动调试脚本 (非自动化测试, pytest --ignore 跳过, .gitignore 不入仓)
│   ├── README.md                           # 用途说明: 临时联调/排查脚本
│   ├── test_smoke.py                       # 手动冒烟脚本
│   ├── test_all_formats.py                 # 全格式导出验证
│   └── __init__.py
├── conftest.py     # 全局配置: .env 加载 + 容器栈可达性自动跳过
└── __init__.py
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
# 三层路由集成测试 (mock 化, 不依赖容器栈)
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

## 4. 三层方案测试覆盖矩阵

三层路由 (`src/skills/researcher/context_manager.py:get_similar_content`) 测试
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
- **CI 流水线顺序** (AGENTS.md 第 13 章):
  1. 构建镜像 + 单元测试 (失败即终止)
  2. `docker compose up -d` + 等待全部健康检查通过
  3. 功能 → API → 回归 → e2e (按序, 前者失败后者不执行)
  4. 任一环节失败阻断合并; 全部通过后 `docker compose down -v` 清理

## 7. 7 层测试分层执行命令与触发时机

> 与 AGENTS.md 第 13 章对齐, 每层独立目录, 执行环境与触发时机严格区分.

| 层级 | 目录 | 执行环境 | 触发时机 | 执行命令 |
|------|------|---------|---------|---------|
| 单元 | `unit/` | 本地 / 构建期 | 每次 commit、Docker build | `pytest tests/unit/ -v -m unit` |
| 功能 | `functional/` | 部署后容器栈 | 容器栈 `service_healthy` 后 | `pytest tests/functional/ -v -m functional` |
| API | `api/` | 部署后容器栈 | 容器栈健康后 | `pytest tests/api/ -v -m api` |
| 回归 | `regression/` | 部署后容器栈 | 合并 main 前门禁 | `pytest tests/regression/ -v -m regression` |
| e2e | `e2e/` | 部署后容器栈 | 合并 main 前 / 发布前 | `pytest tests/e2e/ -v -m e2e` |
| 性能 | `performance/` | 部署后容器栈 | 延迟/吞吐/负载验证 | `pytest tests/performance/ -v -m performance` |
| 探索性 | `exploratory/` | 部署后容器栈 | 边界/降级场景 (非门禁) | `pytest tests/exploratory/ -v -m exploratory` |

**容器栈依赖测试执行流程** (功能/回归/API/e2e/性能/探索性):

```bash
# 1. 启动容器栈 (优先使用构建脚本, AGENTS.md 第 12 章)
docker compose -p agentinsight -f docker-compose.yml up -d --wait
# QA 模式: docker-build.qa.bat
# 生产联网: docker-build.sh
# 生产离线: docker-build.offline.sh

# 2. 注入测试目标地址 (宿主机访问)
set AGENT_URL=http://127.0.0.1:8066

# 3. 执行容器栈依赖测试
pytest tests/functional/ tests/api/ tests/regression/ tests/e2e/ -v

# 4. 清理
docker compose -p agentinsight down -v
```

> 容器栈未运行时, `tests/conftest.py` 的 `pytest_collection_modifyitems` 钩子
> 自动跳过 `functional`/`regression`/`api`/`e2e`/`performance`/`exploratory`
> 标记的测试, 避免大量连接失败噪声.

## 8. 新增测试文件说明

### 8.1 test_metaso_searcher.py (秘塔搜索 METASO 修复验证)

**文件**: `tests/unit/test_metaso_searcher.py`
**验证目标**: `src/skills/researcher/searchers/metaso.py` 的 payload/headers 修复
**mark**: `unit` (构建期执行, mock httpx, 不依赖真实 API)

修复背景: 之前秘塔 API 调用错误使用 `{"num": int}` 且缺 `scope` 与 `Accept` 头,
导致 API 拒绝或返回非网页数据. 修复后对齐官方文档.

| 测试分组 | 用例数 | 验证点 |
|---------|--------|--------|
| api_key 未配置 | 1 | `api_key=None` → 返回 `[]`, 不发起 HTTP |
| payload 构造 | 5 | `scope="webpage"` / `size=str(max_results)` / `includeSummary=True` / `q` 字段 |
| headers 构造 | 3 | `Accept: application/json` / `Authorization: Bearer` / `Content-Type` |
| 响应解析 (result.webpages) | 3 | `{"result":{"webpages":[...]}}` 结构 + name/link 字段回退 |
| 响应解析 (裸结构) | 3 | `{"webpages":[...]}` / `{"results":[...]}` / `{"data":[...]}` 回退 |
| 结果归一化与截断 | 3 | max_results 截断 / 缺 url 跳过 / 5 字段归一化 |
| 额度已满 (429/402) | 3 | 抛 `QuotaExceededError` + Retry-After 头解析 |
| 其他 HTTP 错误 | 3 | 500/403 返回空列表 + JSON 解析失败返回空列表 |
| query_domains 过滤 | 1 | 后置按域名白名单过滤 |
| 网络异常降级 | 1 | httpx 抛异常返回空列表, 不向调用方抛 |

### 8.2 test_pdf_docx_fonts.py (PDF/DOCX 中文字体配置验证)

**文件**: `tests/unit/test_pdf_docx_fonts.py`
**验证目标**: 离线部署模式中文字体安装逻辑 (PDF/DOCX 报告不乱码)
**mark**: `unit` (静态文件检查, 不构建 Docker 镜像)
**skip 策略**: `Dockerfile.qa`/`Dockerfile.offline`/`docker-compose-qa.yaml`/
`packages/debs/` 均在 `.gitignore` 中 (AGENTS.md 第 12 章三套构建模式, 不入仓),
文件不存在时 `pytest.skip()` 而非失败.

| 测试分组 | 用例数 | 验证点 |
|---------|--------|--------|
| Dockerfile.qa 字体 | 4 | 含 `fonts-noto-cjk` 关键词 / `dpkg -i` 安装逻辑 / `fc-list` 验证 / `fonts-wqy` |
| Dockerfile.offline 字体 | 3 | 含 `fonts-noto-cjk` / `dpkg -i` / `fc-list` 验证 |
| packages/debs 字体包 | 3 | 含 `fonts-noto-cjk*.deb` / `fonts-wqy-*.deb` / 至少 2 个 wqy (zenhei+microhei) |
| compose-qa 健康检查 | 5 | embeddings/rerank 用 `CMD-SHELL` (非 CMD) + 含 curl + 非 CMD 形式 |

**背景**: TEI 镜像 `ENTRYPOINT=[text-embeddings-router]`, `CMD` 形式健康检查
会被拦截, 必须改用 `CMD-SHELL` + curl 绝对路径 (`/usr/bin/curl`) 绕过.

## 9. tests/manual/ 目录

> AGENTS.md 第 3 章临时文件管理约定: 手动调试脚本放在 `tests/manual/`.

**性质**: 手动调试脚本, **非 pytest 自动化测试用例**, 不纳入 CI 流水线.

**配置**: `pyproject.toml` 已配置 `addopts = "--ignore=tests/manual"`, pytest 自动忽略;
本目录已加入 `.gitignore` (不入仓), 避免临时脚本污染仓库.

**用途**:
- 临时联调脚本 (真实 API 调用验证、容器栈手动冒烟)
- 一次性问题排查脚本 (线上 bug 复现、日志分析)
- 开发期临时验证产物

**与自动化测试分层的区别**:

| 目录 | 性质 | CI 执行 | 触发时机 |
|------|------|---------|---------|
| `tests/unit/` ~ `tests/exploratory/` | 自动化测试 | 是 | CI 流水线自动触发 |
| `tests/manual/` | **手动调试脚本** | **否** | 开发者手动执行 |

**使用约定**: 详见 `tests/manual/README.md`. 正式测试用例应放在 `tests/` 对应分层,
不要长期沉淀在 `manual/`.
