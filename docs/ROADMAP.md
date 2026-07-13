# 项目路线图 | Project Roadmap

[中文](#中文) | [English](#english)

---

## 中文

## 项目愿景

**agentinsight-researcher** 致力于成为**中文优先的企业级 AI 研究分析智能体**，以 LangGraph 为编排内核、MCP 为工具协议、AgentInsight SDK 为可观测底座，对外暴露 OpenAI 兼容 API，让任何团队都能以最低成本构建深度研究能力。

我们在以下维度持续深耕：

- **中文优先**：中文搜索源（博查/秘塔）、中文分词（jieba）、中文嵌入（bge-base-zh-v1.5）、中文 Rerank（bge-reranker-v2-m3）
- **企业级安全**：三级数据隔离（agent_id × user_id × session_id）、JWT 身份解析、安全响应头、密钥环境变量注入
- **全链路可观测**：AgentInsight SDK 6 类 trace span，追踪从 Agent 入口到 Embedding 调用的完整链路
- **多 Agent 协作**：Supervisor 模式完整流水线（Researcher → Writer → FactChecker → Reviewer → Reviser → Visualizer → Publisher）

---

## 版本路线图

### v1.0.0（已发布 - 2026-07-04）✅

**首次发布**，奠定核心架构与基础能力。

| 领域 | 已交付能力 |
|------|-----------|
| 核心架构 | LangGraph ≥1.2 状态机编排 + PostgresSaver Checkpointer + OpenAI 兼容 API（SSE 流式） |
| 研究能力 | 5 种报告类型 / 5 种输出格式 / 4 种引用风格 / 5 种报告语言 / 4 种报告风格 / 17 种 Tone |
| 多 Agent | Supervisor 线性+条件边模式 + 人在回路审核 + 事实核查+评审修订循环 |
| LLM 网关 | LiteLLM ≥1.6 统一接入 100+ 模型 + 三级分层（FAST/SMART/STRATEGIC）+ 真实成本追踪 |
| RAG 检索 | BM25 + 向量混合检索 + RRF 融合 + 可选 Rerank + Qdrant namespace 隔离 |
| 搜索引擎 | 22 个搜索源（国内/国外/学术三区域）+ 区域自动检测 |
| 抓取器 | 9 种抓取器 + 并行抓取（15 worker） |
| MCP 工具 | MCP 协议支持（fast/deep/disabled）+ LLM 自动选工具 + 多工具并发 |
| 可观测性 | AgentInsight SDK 6 类 trace span + 异步上下文管理器 + Null Object 降级 |
| 部署 | 三套构建模式（QA离线/生产联网/生产离线）+ 7 容器编排 |
| 测试 | 五层测试（单元/功能/API/回归/e2e）+ 96 个单元测试用例 |

---

### v1.2.0（计划中 - 2026 Q3）🔧

**主题**：评测门禁 CI 化 + 用户体验增强

#### 正在开发

- [ ] **RAGAS/DeepEval 评测门禁 CI 化**
  - RAGAS：faithfulness ≥0.8 / answer_relevancy ≥0.8 / context_precision ≥0.7
  - DeepEval：任务完成率 ≥0.9 / 工具调用正确率 ≥0.95 / 幻觉率 ≤0.1
  - CI 流水线自动运行评测，不达标阻断合并
- [ ] **多模态支持（图片理解 + 表格抽取）**
  - 图片理解：支持上传图片作为研究数据源，多模态 LLM 识别图片内容
  - 表格抽取：从 PDF/DOCX 中提取结构化表格数据，保留行列关系
- [ ] **Agent Marketplace（技能热插拔）**
  - 技能市场：用户可浏览、安装、卸载技能包
  - 热插拔：运行时动态加载/卸载技能，无需重启服务
  - 技能分享：用户可发布自研技能到市场

#### 已规划功能

- [ ] 报告模板自定义（用户上传 Markdown 模板）
- [ ] 多语言报告增强（增加德语/西班牙语/阿拉伯语）
- [ ] 搜索引擎插件机制（用户自定义搜索源）
- [ ] 抓取器性能优化（headless 浏览器池化复用）
- [ ] WebSocket 消息压缩（permessage-deflate）

---

### v1.3.0（展望 - 2026 Q4）🎯

**主题**：规模化 + 生态拓展

#### 待定功能

- [ ] **多 Agent Swarm 模式**
  - 在 Supervisor 模式之外，增加 Swarm 去中心化协作模式
  - Agent 间动态委派任务，支持更复杂的研究场景
- [ ] **知识库管理增强**
  - 知识库可视化（向量分布、召回热力图）
  - 知识库增量更新与版本管理
  - 跨 Agent 知识库共享机制
- [ ] **报告协作**
  - 多用户协同编辑报告
  - 报告评论与批注
  - 版本历史与差异对比
- [ ] **评测 dashboard**
  - 可视化评测趋势（faithfulness/relevancy 历史曲线）
  - 按报告类型/行业维度分组统计
- [ ] **离线模型微调**
  - 基于用户反馈数据微调 Embedding/Rerank 模型
  - 行业专有词表扩展
- [ ] **kubectl 部署支持**
  - Helm Chart 封装
  - 水平自动扩缩容（HPA）

---

### v2.0.0（长期目标 - 2027）🌟

**主题**：生产级稳定 + 平台化

- [ ] **SLA 99.9% 可用性**
  - 多副本部署 + 故障自动转移
  - 灰度发布与金丝雀部署
- [ ] **多租户 SaaS 模式**
  - 租户隔离与资源配额
  - 计量计费（按 Token / 按报告 / 按存储）
- [ ] **可视化编排**
  - 拖拽式 Agent 流水线设计器
  - 节点参数可视化配置
- [ ] **插件生态**
  - 第三方搜索源/抓取器/工具插件 SDK
  - 插件市场与审核机制
- [ ] **合规认证**
  - 等保三级 / ISO 27001
  - 数据出境合规（GDPR / 个人信息保护法）
- [ ] **性能基准**
  - 单报告生成 < 60s（basic_report）
  - 并发支持 ≥ 100 QPS
  - 冷启动 < 10s

---

## 已规划功能列表

| 功能 | 目标版本 | 状态 | 优先级 |
|------|---------|------|--------|
| RAGAS/DeepEval 评测门禁 CI | v0.2.0 | 🔧 开发中 | P0 |
| 多模态支持（图片+表格） | v0.2.0 | 🔧 开发中 | P0 |
| Agent Marketplace | v0.2.0 | 🔧 开发中 | P0 |
| 报告模板自定义 | v0.2.0 | 📋 已规划 | P1 |
| 多语言报告增强 | v0.2.0 | 📋 已规划 | P2 |
| 搜索引擎插件机制 | v0.2.0 | 📋 已规划 | P1 |
| Swarm 多 Agent 模式 | v0.3.0 | 🔮 展望 | P1 |
| 知识库管理增强 | v0.3.0 | 🔮 展望 | P1 |
| 报告协作 | v0.3.0 | 🔮 展望 | P2 |
| 评测 dashboard | v0.3.0 | 🔮 展望 | P2 |
| k8s 部署支持 | v0.3.0 | 🔮 展望 | P1 |
| 多租户 SaaS | v1.0.0 | 🌟 长期 | P1 |
| 可视化编排 | v1.0.0 | 🌟 长期 | P2 |
| 合规认证 | v1.0.0 | 🌟 长期 | P1 |

---

## 版本发布节奏

- **Patch**（0.0.X）：每月按需发布，Bug 修复与小改进
- **Minor**（0.X.0）：每季度发布，向后兼容的功能新增
- **Major**（X.0.0）：按里程碑发布，不兼容的 API 变更

> 路线图仅供参考，实际发布计划可能根据社区反馈与优先级调整。欢迎在 [GitHub Issues](https://github.com/AgentInsight/agentinsight-researcher/issues) 提出功能建议。

---

## English

## Project Vision

**agentinsight-researcher** aims to be a **Chinese-first enterprise-grade AI research analysis agent**, using LangGraph as the orchestration kernel, MCP as the tool protocol, and AgentInsight SDK as the observability foundation, exposing an OpenAI-compatible API so any team can build deep research capabilities at minimal cost.

We continue to deepen our focus on:

- **Chinese-first**: Chinese search sources (Bocha/Metaso), Chinese tokenization (jieba), Chinese embeddings (bge-base-zh-v1.5), Chinese Rerank (bge-reranker-v2-m3)
- **Enterprise-grade security**: Three-tier data isolation (agent_id × user_id × session_id), JWT identity, security response headers, environment-variable-only key injection
- **Full-link observability**: AgentInsight SDK 6 types of trace spans, tracking from Agent entry to Embedding calls
- **Multi-agent collaboration**: Supervisor mode complete pipeline (Researcher → Writer → FactChecker → Reviewer → Reviser → Visualizer → Publisher)

---

## Version Roadmap

### v1.0.0 (Released - 2026-07-04) ✅

**Initial release**, establishing core architecture and foundational capabilities.

| Domain | Delivered Capabilities |
|--------|----------------------|
| Core Architecture | LangGraph ≥1.2 state machine orchestration + PostgresSaver Checkpointer + OpenAI-compatible API (SSE streaming) |
| Research Capabilities | 5 report types / 5 output formats / 4 citation styles / 5 report languages / 4 report styles / 17 tones |
| Multi-Agent | Supervisor linear + conditional edge mode + human-in-the-loop review + fact-checking + review revision loop |
| LLM Gateway | LiteLLM ≥1.6 unified access to 100+ models + three-tier layering (FAST/SMART/STRATEGIC) + real cost tracking |
| RAG Retrieval | BM25 + vector hybrid retrieval + RRF fusion + optional Rerank + Qdrant namespace isolation |
| Search Engines | 22 search sources (domestic/global/academic regions) + auto region detection |
| Scrapers | 9 scrapers + parallel scraping (15 workers) |
| MCP Tools | MCP protocol support (fast/deep/disabled) + LLM auto tool selection + multi-tool concurrency |
| Observability | AgentInsight SDK 6 types of trace spans + async context manager + Null Object degradation |
| Deployment | Three build modes (QA offline / production online / production offline) + 7-container orchestration |
| Testing | Five-tier testing (unit/functional/API/regression/e2e) + 96 unit test cases |

---

### v0.2.0 (Planned - 2026 Q3) 🔧

**Theme**: Evaluation gate CI integration + user experience enhancement

#### In Development

- [ ] **RAGAS/DeepEval evaluation gate CI integration**
  - RAGAS: faithfulness ≥0.8 / answer_relevancy ≥0.8 / context_precision ≥0.7
  - DeepEval: task completion rate ≥0.9 / tool call accuracy ≥0.95 / hallucination rate ≤0.1
  - CI pipeline auto-runs evaluation; blocks merge if below threshold
- [ ] **Multimodal support (image understanding + table extraction)**
  - Image understanding: support uploading images as research data source; multimodal LLM identifies image content
  - Table extraction: extract structured table data from PDF/DOCX, preserving row-column relationships
- [ ] **Agent Marketplace (hot-swappable skills)**
  - Skill marketplace: users can browse, install, and uninstall skill packs
  - Hot-swapping: dynamically load/unload skills at runtime without service restart
  - Skill sharing: users can publish self-developed skills to the marketplace

#### Planned Features

- [ ] Custom report templates (user-uploaded Markdown templates)
- [ ] Multi-language report enhancement (add German/Spanish/Arabic)
- [ ] Search engine plugin mechanism (user-defined search sources)
- [ ] Scraper performance optimization (headless browser pooling and reuse)
- [ ] WebSocket message compression (permessage-deflate)

---

### v0.3.0 (Outlook - 2026 Q4) 🎯

**Theme**: Scaling + ecosystem expansion

#### Tentative Features

- [ ] **Multi-Agent Swarm mode**
  - Add Swarm decentralized collaboration mode alongside Supervisor mode
  - Dynamic task delegation between agents, supporting more complex research scenarios
- [ ] **Knowledge base management enhancement**
  - Knowledge base visualization (vector distribution, recall heatmap)
  - Incremental updates and version management
  - Cross-agent knowledge base sharing
- [ ] **Report collaboration**
  - Multi-user collaborative editing
  - Report comments and annotations
  - Version history and diff comparison
- [ ] **Evaluation dashboard**
  - Visualized evaluation trends (faithfulness/relevancy historical curves)
  - Grouped statistics by report type/industry dimension
- [ ] **Offline model fine-tuning**
  - Fine-tune Embedding/Rerank models based on user feedback data
  - Industry-specific vocabulary expansion
- [ ] **kubectl deployment support**
  - Helm Chart packaging
  - Horizontal Pod Autoscaler (HPA)

---

### v1.0.0 (Long-term Goal - 2027) 🌟

**Theme**: Production-grade stability + platformization

- [ ] **SLA 99.9% availability**
  - Multi-replica deployment + automatic failover
  - Canary releases and gradual rollouts
- [ ] **Multi-tenant SaaS mode**
  - Tenant isolation and resource quotas
  - Metered billing (by Token / by report / by storage)
- [ ] **Visual orchestration**
  - Drag-and-drop Agent pipeline designer
  - Visual node parameter configuration
- [ ] **Plugin ecosystem**
  - Third-party search source/scraper/tool plugin SDK
  - Plugin marketplace and review mechanism
- [ ] **Compliance certification**
  - MLPS Level 3 / ISO 27001
  - Data cross-border compliance (GDPR / PIPL)
- [ ] **Performance benchmarks**
  - Single report generation < 60s (basic_report)
  - Concurrency support ≥ 100 QPS
  - Cold start < 10s

---

## Planned Feature List

| Feature | Target Version | Status | Priority |
|---------|---------------|--------|----------|
| RAGAS/DeepEval evaluation gate CI | v0.2.0 | 🔧 In Development | P0 |
| Multimodal support (image+table) | v0.2.0 | 🔧 In Development | P0 |
| Agent Marketplace | v0.2.0 | 🔧 In Development | P0 |
| Custom report templates | v0.2.0 | 📋 Planned | P1 |
| Multi-language report enhancement | v0.2.0 | 📋 Planned | P2 |
| Search engine plugin mechanism | v0.2.0 | 📋 Planned | P1 |
| Swarm multi-agent mode | v0.3.0 | 🔮 Outlook | P1 |
| Knowledge base management enhancement | v0.3.0 | 🔮 Outlook | P1 |
| Report collaboration | v0.3.0 | 🔮 Outlook | P2 |
| Evaluation dashboard | v0.3.0 | 🔮 Outlook | P2 |
| k8s deployment support | v0.3.0 | 🔮 Outlook | P1 |
| Multi-tenant SaaS | v1.0.0 | 🌟 Long-term | P1 |
| Visual orchestration | v1.0.0 | 🌟 Long-term | P2 |
| Compliance certification | v1.0.0 | 🌟 Long-term | P1 |

---

## Release Cadence

- **Patch** (0.0.X): Released monthly as needed; bug fixes and minor improvements
- **Minor** (0.X.0): Released quarterly; backward-compatible feature additions
- **Major** (X.0.0): Released by milestone; incompatible API changes

> The roadmap is for reference only; actual release plans may adjust based on community feedback and priorities. Feature suggestions are welcome at [GitHub Issues](https://github.com/AgentInsight/agentinsight-researcher/issues).
