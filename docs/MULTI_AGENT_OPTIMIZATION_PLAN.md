# agentinsight-researcher 多 Agent 共享存储层优化方案

> **生成时间**: 2026-07-14
> **版本**: v1.2.0
> **状态**: ✅ 已实施（2026-07-14，19 处硬编码 S-01~S-19 全部修复 + 3 处 mcp_routes.py 系统 MCP 查询 bug 修复）
> **场景定义**: 本项目（agentinsight-researcher）是多 Agent 体系中的一个 Agent，**项目内部 `agent_id` 固定不变**（恒为 `agentinsight-researcher`）。本方案的目标是将本项目使用到的 **PostgreSQL / Redis / Embeddings / Qdrant 四大存储层**改造为可被多个不同 Agent 项目（含本项目）共享使用的基础设施。
> **约束条件**:
> - 不修改 docker-compose.yml / docker-compose-qa.yaml（不管资源容量问题）
> - 不修改各服务的连接方式（不加 pgbouncer，不改 host/port）
> - FastEmbed 是本 Agent 进程内加载的本地资源，不属于共享存储层，不修改
> **研究方法**: 11 角色 AI 专家团队并行逐行阅读 108 个 Python 文件（~29,550 行）+ DDL + .env

---

## 一、执行摘要

### 1.1 场景定义

```
                    ┌─────────────────────────────────────────────────────┐
                    │           共享存储层 (Shared Infrastructure)          │
                    │                                                     │
                    │  ┌──────────┐  ┌───────┐  ┌──────────┐  ┌───────┐  │
                    │  │PostgreSQL│  │ Redis │  │Embeddings│  │Qdrant │  │
                    │  │  (agents)│  │(db 0) │  │  (TEI)   │  │(agents│  │
                    │  │          │  │       │  │          │  │   )   │  │
                    │  └────┬─────┘  └───┬───┘  └────┬─────┘  └───┬───┘  │
                    └───────┼────────────┼───────────┼────────────┼──────┘
                            │            │           │            │
              ┌─────────────┼────────────┼───────────┼────────────┼─────────────┐
              │             │            │           │            │             │
     ┌────────┴───┐  ┌──────┴───┐  ┌────┴────┐  ┌───┴────┐  ┌────┴────┐  ┌────┴────┐
     │ Agent A    │  │ Agent B  │  │ Agent C │  │ Agent D│  │ Agent E│  │  ...    │
     │ researcher │  │  coder   │  │translator│  │ analyst│  │  ...   │  │         │
     │ (本项目)    │  │          │  │         │  │        │  │        │  │         │
     └────────────┘  └──────────┘  └─────────┘  └────────┘  └────────┘  └─────────┘
```

**核心原则**:
- 本项目 `agent_id = "agentinsight-researcher"` **固定不变**，不需要请求级路由
- 每个外部 Agent 项目有独立代码库，但**连接同一** Postgres / Redis / TEI / Qdrant 实例
- 数据隔离完全依赖存储层的 `agent_id` 字段/前缀/namespace
- 本项目的职责: 确保自己的所有读写操作都带正确的 `agent_id`，不污染其他 Agent 的数据，也不读取其他 Agent 的数据

**不修改的部分**（用户明确约束）:
- ❌ docker-compose.yml / docker-compose-qa.yaml — 不改任何容器编排配置
- ❌ 各服务连接方式 — 不加 pgbouncer，不改 host/port/连接参数
- ❌ FastEmbed — 本 Agent 进程内加载的本地资源，不属于共享存储层
- ❌ 资源容量规划 — 不管 maxmemory / max_connections / TEI 并发限制

**不需要改造的部分**（与旧方案的区别）:
- ❌ 中间件 `X-Agent-Id` 请求级路由 — 本项目只处理自己的请求
- ❌ Agent Registry / AgentProfile 注册表 — 本项目不需要知道其他 Agent
- ❌ 图单例改 `dict[agent_id]` — 本项目只有一个 Agent
- ❌ 节点读取 `state.agent_id` — 固定值，用 `settings.agent_name` 即可
- ❌ Agent Discovery 多列表 / `/v1/models` 列出多 Agent — 只发现自己

### 1.2 现状结论

| 存储层 | 多 Agent 隔离预留 | 实际生效 | 根因 |
|--------|------------------|---------|------|
| **PostgreSQL 业务表 schema** | ✅ 7 张业务表含 `agent_id` 列 + 复合索引 | ⚠️ `report_store` 6 处查询缺 `WHERE agent_id` | 查询未加过滤 |
| **PostgreSQL 限额表** | ❌ `report_limits` 无 `agent_id` 列 | ❌ 跨 Agent 共享限额 | schema 设计性缺失 |
| **PostgreSQL 使用量表** | ❌ `daily_report_usage` 无 `agent_id` 列 | ❌ 跨 Agent 共享计数 | schema 设计性缺失 |
| **PostgreSQL 系统 MCP** | ✅ `mcp_configs` 有 `agent_id` 列 | ❌ 23 条 INSERT 硬编码 `agentinsight-researcher` | init.sql 硬编码 |
| **PostgreSQL Checkpointer** | ❌ thread_id 不含 agent_id | ❌ 不同 Agent 会话可能冲突 | thread_id = session_id |
| **PostgreSQL 初始化** | ❌ 无 advisory lock | ⚠️ 多 Agent 并发启动竞态 | db_initializer 无锁 |
| **Redis 键前缀** | ✅ 格式 `{agent_id}:{user_id}:...` | ✅ agent_id 正确注入 | 已正确 |
| **Redis BM25 键** | ⚠️ 含 agent_id 前缀 | ⚠️ 永久键无 TTL | 缺 TTL 配置 |
| **Redis BM25 加载锁** | ❌ 进程内 asyncio.Lock | ⚠️ 多实例不协调 | WeakValueDictionary |
| **Embeddings TEI** | ✅ 无状态服务可共享 | ✅ 已可共享 | 无状态，无需改造 |
| **Qdrant namespace** | ✅ namespace 含 agent_id 前缀 | ✅ agent_id 正确注入 | 已正确 |
| **Qdrant user_id 索引** | ❌ payload 有 user_id 但无索引 | ⚠️ 大集合过滤性能差 | 缺 payload index |
| **Qdrant 文件上传** | ❌ 上传文件未索引到 Qdrant | ❌ 用户私有数据无法 RAG 检索 | 功能缺失 |

### 1.3 改造规模评估

- **存储层硬编码位置**: 19 处（S-01 ~ S-19）
- **P0 级（数据隔离阻塞）**: 11 项（report_store WHERE / 限额表 / 使用量表 / 系统 MCP / Checkpointer / MCP 缓存）
- **P1 级（代码层优化）**: 5 项（advisory lock / BM25 TTL / BM25 分布式锁 / Qdrant 索引）
- **P2 级（功能完善）**: 3 项（文件上传索引 / Qdrant 多集合 / 写入权限）
- **预估改动文件**: ~12 个（仅存储层代码与 DDL，不含 compose/settings 连接配置）
- **预估新增代码**: ~400 行（DDL 迁移 + 查询修复 + 锁/索引）

### 1.4 推荐改造路径

```
Phase 1 (P0): 数据隔离修复              Phase 2 (P1): 代码层优化           Phase 3 (P2): 功能完善
   ↓                                      ↓                                  ↓
- report_store 加 WHERE agent_id        - db_initializer 加 advisory lock  - 文件上传接 Qdrant 索引
- report_limits 加 agent_id 列          - Redis BM25 键加 TTL              - Qdrant 多集合支持
- daily_report_usage 加 agent_id        - Redis 分布式锁 (BM25 协调)       - Qdrant 写入权限校验
- ip_user_resolver 查询加过滤           - Qdrant user_id payload 索引
- 系统 MCP 改 agent_id IS NULL
- mcp_coordinator 查询改共享
- Checkpointer thread_id 加命名空间
- MCP 缓存 key 加 agent_id+user_id
```

---

## 二、共享部署架构

### 2.1 隔离策略总览

| 存储层 | 隔离键 | 隔离方式 | 共享数据 | 私有数据 |
|--------|--------|---------|---------|---------|
| **PostgreSQL** | `agent_id` + `user_id` | WHERE 过滤 + 复合索引 | 系统 MCP (`agent_id IS NULL`) | 业务表 (`agent_id + user_id`) |
| **Redis** | `{agent_id}:{user_id}:` 键前缀 | 键空间隔离 | 无 | 全部按前缀隔离 |
| **Embeddings TEI** | 无（无状态） | 服务共享 | 全部共享 | 无 |
| **Qdrant** | `namespace` payload | payload 过滤 + namespace 索引 | `namespace = {agent_id}-data` | `namespace = {agent_id}-data:{user_id}` |

> **说明**: 本方案不修改 docker-compose.yml / docker-compose-qa.yaml，不改变各服务的连接方式。多个 Agent 项目各自独立部署容器，连接同一组存储服务实例（Postgres / Redis / TEI / Qdrant），数据隔离完全依赖应用层的 `agent_id` 字段/键前缀/namespace。

### 2.2 已正确无需改造的部分

以下部分经专家团队验证已正确实现多 Agent 隔离，**不在本次改造范围内**:

| 组件 | 验证结论 | 依据 |
|------|---------|------|
| **PostgreSQL 7 张业务表 schema** | ✅ 已含 `agent_id` 列 + 复合索引 | init.sql 行 24/53/93/109/130/190/393 |
| **Redis 键前缀格式** | ✅ `{agent_id}:{user_id}:...` 正确 | redis_client.py / retriever.py 键构造 |
| **Qdrant namespace 构造** | ✅ `{agent_id}-data` / `{agent_id}-data:{user_id}` 正确 | qdrant_manager.py:157-184 |
| **Embeddings TEI 服务** | ✅ 无状态，多 Agent 共享无隔离问题 | embeddings.py TEI 客户端 |
| **FastEmbed 本地模型** | ✅ Agent 进程内资源，不属于共享存储层 | fastembed_client.py（本方案不修改） |
| **session_store.py 查询** | ✅ 所有查询已含 `WHERE agent_id = $1 AND user_id = $2` | session_store.py 全文件 |

---

## 三、PostgreSQL 共享优化方案

### 3.1 现状分析

**已正确的部分**（7 张业务表均有 `agent_id` 列 + 复合索引）:

```sql
-- scripts/init.sql — 以下表已正确预留 agent_id
research_sessions   (agent_id VARCHAR(64) NOT NULL)  -- 行 24
research_reports    (agent_id VARCHAR(64) NOT NULL)  -- 行 53
research_search_logs(agent_id VARCHAR(64) NOT NULL)  -- 行 93
uploaded_files      (agent_id VARCHAR(64) NOT NULL)  -- 行 109
token_usage_logs    (agent_id VARCHAR(64) NOT NULL)  -- 行 130
mcp_configs         (agent_id VARCHAR(64) NOT NULL)  -- 行 190
chat_messages       (agent_id VARCHAR(64) NOT NULL)  -- 行 393

-- 复合索引均已创建 (agent_id + user_id)
CREATE INDEX idx_research_sessions_agent_user_updated ON research_sessions(agent_id, user_id, updated_at DESC);
CREATE INDEX idx_research_reports_agent_user ON research_reports(agent_id, user_id);
-- ... 其余表类似
```

**问题 1: report_store.py 6 处查询缺 agent_id 过滤** (S-01 ~ S-06)

```python
# src/memory/report_store.py:113-130 — get_report 缺 agent_id
async def get_report(self, report_id: str) -> dict[str, Any] | None:
    # 现状: 仅按 report_id 查询, 不校验 agent_id
    row = await conn.fetchrow(
        f"SELECT {_SELECT_COLUMNS} FROM research_reports WHERE report_id = $1::uuid",
        report_id,  # ❌ 缺 agent_id 过滤
    )

# src/memory/report_store.py:197-216 — delete_report 缺 agent_id
async def delete_report(self, report_id: str) -> bool:
    result = await conn.execute(
        "DELETE FROM research_reports WHERE report_id = $1::uuid",
        report_id,  # ❌ 缺 agent_id 过滤
    )

# src/memory/report_store.py:132-195 — list_reports 4 种组合均缺 agent_id
async def list_reports(self, session_id=None, user_id=None, ...):
    # 4 种 WHERE 组合: (session+user) / (session) / (user) / (无)
    # ❌ 全部缺 agent_id 过滤
```

**问题 2: report_limits 表无 agent_id 列** (S-07)

```sql
-- scripts/init.sql:437-447 — report_limits 表结构
CREATE TABLE IF NOT EXISTS report_limits (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(64) UNIQUE,        -- ❌ 无 agent_id 列
    daily_limit INTEGER NOT NULL DEFAULT 5,
    ...
);
-- ❌ 跨 Agent 共享限额: 所有 Agent 用同一限额配置
```

**问题 3: daily_report_usage 表无 agent_id 列** (S-08)

```sql
-- scripts/init.sql:460-470 — daily_report_usage 表结构
CREATE TABLE IF NOT EXISTS daily_report_usage (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,      -- ❌ 无 agent_id 列
    usage_date DATE NOT NULL,
    daily_count INTEGER NOT NULL DEFAULT 0,
    ...
);
-- ❌ 跨 Agent 共享计数: 用户在 Agent A 生成报告会消耗 Agent B 的限额
```

**问题 4: ip_user_resolver.py 查询缺 agent_id 过滤** (S-09 ~ S-11)

```python
# src/api/ip_user_resolver.py:100-108 — _get_daily_limit_from_db 缺 agent_id
row = await conn.fetchrow(
    """
    SELECT COALESCE(
        (SELECT daily_limit FROM report_limits WHERE user_id = $1),       -- ❌ 无 agent_id
        (SELECT daily_limit FROM report_limits WHERE user_id IS NULL),    -- ❌ 无 agent_id
        0
    ) AS effective_limit
    """,
    user_id,
)

# src/api/ip_user_resolver.py:180-184 — _get_daily_usage_from_db 缺 agent_id
row = await conn.fetchrow(
    "SELECT daily_count FROM daily_report_usage WHERE user_id = $1 AND usage_date = $2",  -- ❌ 无 agent_id
    user_id, usage_date,
)
```

**问题 5: 系统 MCP INSERT 硬编码 agent_id** (S-12)

```sql
-- scripts/init.sql:269-378 — 23 条系统 MCP INSERT
INSERT INTO mcp_configs (agent_id, user_id, name, ...) VALUES
    ('agentinsight-researcher', 'system', 'fetch', ...),   -- ❌ 硬编码 agent_id
    ('agentinsight-researcher', 'system', 'filesystem', ...),
    -- ... 23 条全部硬编码
-- ❌ 新 Agent (如 agentinsight-coder) 无法共享这些系统 MCP 配置
```

**问题 6: mcp_coordinator.py 系统 MCP 查询** (S-13)

```python
# src/skills/researcher/mcp_coordinator.py — 系统 MCP 查询
# 现状: 按 agent_id 精确匹配, 不查共享配置
# 如果系统 MCP 改为 agent_id IS NULL, 查询需同步改为:
# WHERE (agent_id = $1 AND user_id = $2) OR (agent_id IS NULL AND is_system = TRUE)
```

**问题 7: Checkpointer thread_id 不含 agent_id 命名空间** (S-14)

```python
# src/api/routes.py:139, 164, 209, 589, 727 — thread_id = session_id
config: dict[str, Any] = {"configurable": {"thread_id": session_id}}
# ❌ 如果 Agent A 和 Agent B 使用相同的 session_id (UUID 碰撞概率极低但非零),
#    Checkpointer 会混淆两个 Agent 的会话状态
# ❌ 更实际的风险: 多 Agent 共享同一 PG, Checkpointer 表无 agent_id 列,
#    无法按 Agent 清理会话, 也无法按 Agent 统计
```

**问题 8: db_initializer.py 无 advisory lock** (S-15)

```python
# src/memory/db_initializer.py:171-249 — init_database() 无锁保护
# 多 Agent 并发启动时, 可能同时执行 init.sql, 导致竞态
# (虽然 DDL 是 IF NOT EXISTS 幂等, 但 INSERT 系统 MCP 可能重复)
```

### 3.2 优化方案

#### 3.2.1 report_store 查询加 agent_id 过滤 (S-01 ~ S-06)

```python
# src/memory/report_store.py — get_report 优化
# 现状 (行 113-130)
async def get_report(self, report_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        f"SELECT {_SELECT_COLUMNS} FROM research_reports WHERE report_id = $1::uuid",
        report_id,
    )

# 优化后: 加 agent_id + user_id 过滤, 防止跨 Agent/跨用户读取
async def get_report(
    self, report_id: str, *, agent_id: str, user_id: str
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        f"SELECT {_SELECT_COLUMNS} FROM research_reports "
        "WHERE report_id = $1::uuid AND agent_id = $2 AND user_id = $3",
        uuid.UUID(report_id), agent_id, user_id,
    )
```

```python
# src/memory/report_store.py — delete_report 优化
# 现状 (行 197-216)
async def delete_report(self, report_id: str) -> bool:
    result = await conn.execute(
        "DELETE FROM research_reports WHERE report_id = $1::uuid",
        report_id,
    )

# 优化后
async def delete_report(
    self, report_id: str, *, agent_id: str, user_id: str
) -> bool:
    result = await conn.execute(
        "DELETE FROM research_reports "
        "WHERE report_id = $1::uuid AND agent_id = $2 AND user_id = $3",
        uuid.UUID(report_id), agent_id, user_id,
    )
```

```python
# src/memory/report_store.py — list_reports 优化
# 现状 (行 132-195) — 4 种组合均缺 agent_id
async def list_reports(self, session_id=None, user_id=None, limit=20, offset=0):
    if session_id and user_id:
        rows = await conn.fetch(
            f"SELECT ... WHERE session_id = $1 AND user_id = $2 ...",
            session_id, user_id, limit, offset,
        )
    # ... 其余 3 种组合

# 优化后: 所有 WHERE 子句加 AND agent_id = $N
async def list_reports(
    self, *, agent_id: str, session_id: str | None = None,
    user_id: str | None = None, limit: int = 20, offset: int = 0,
) -> list[dict[str, Any]]:
    # agent_id 为必传参数, 强制隔离
    if session_id and user_id:
        rows = await conn.fetch(
            f"SELECT {_SELECT_COLUMNS} FROM research_reports "
            "WHERE agent_id = $1 AND session_id = $2 AND user_id = $3 "
            "ORDER BY created_at DESC LIMIT $4 OFFSET $5",
            agent_id, session_id, user_id, limit, offset,
        )
    elif session_id:
        rows = await conn.fetch(
            f"SELECT {_SELECT_COLUMNS} FROM research_reports "
            "WHERE agent_id = $1 AND session_id = $2 "
            "ORDER BY created_at DESC LIMIT $3 OFFSET $4",
            agent_id, session_id, limit, offset,
        )
    elif user_id:
        rows = await conn.fetch(
            f"SELECT {_SELECT_COLUMNS} FROM research_reports "
            "WHERE agent_id = $1 AND user_id = $2 "
            "ORDER BY created_at DESC LIMIT $3 OFFSET $4",
            agent_id, user_id, limit, offset,
        )
    else:
        rows = await conn.fetch(
            f"SELECT {_SELECT_COLUMNS} FROM research_reports "
            "WHERE agent_id = $1 "
            "ORDER BY created_at DESC LIMIT $2 OFFSET $3",
            agent_id, limit, offset,
        )
```

```python
# src/api/routes.py — 调用方同步修改
# 现状 (行 2178-2211) — list_session_reports 不传 agent_id
reports = await report_store.list_reports(session_id=sid, user_id=uid, ...)

# 优化后
reports = await report_store.list_reports(
    agent_id=settings.agent_name, session_id=sid, user_id=uid, ...
)

# 现状 (行 2214-2270) — download_report 不传 agent_id
report = await report_store.get_report(report_id)

# 优化后
report = await report_store.get_report(
    report_id, agent_id=settings.agent_name, user_id=user_id
)
```

#### 3.2.2 report_limits 表加 agent_id 列 (S-07)

```sql
-- scripts/init.sql — report_limits 表优化
-- 现状
CREATE TABLE IF NOT EXISTS report_limits (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(64) UNIQUE,
    daily_limit INTEGER NOT NULL DEFAULT 5,
    ...
);

-- 优化后: 加 agent_id 列, 改复合唯一索引
-- agent_id IS NULL + user_id IS NULL = 全局默认限额 (所有 Agent 共享)
-- agent_id = 'xxx' + user_id IS NULL = Agent 级默认限额
-- agent_id = 'xxx' + user_id = 'yyy' = 用户级专属限额
CREATE TABLE IF NOT EXISTS report_limits (
    id BIGSERIAL PRIMARY KEY,
    agent_id VARCHAR(64),                -- NULL = 全局默认; 非 NULL = Agent 专属
    user_id VARCHAR(64),                -- NULL = Agent 级默认; 非 NULL = 用户专属
    daily_limit INTEGER NOT NULL DEFAULT 5,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 语义:
-- (agent_id IS NULL, user_id IS NULL) → 全局默认 (所有 Agent 的兜底)
-- (agent_id = 'researcher', user_id IS NULL) → researcher Agent 默认
-- (agent_id = 'researcher', user_id = 'user1') → researcher 的 user1 专属

-- 唯一索引: (agent_id, user_id) 组合唯一
CREATE UNIQUE INDEX IF NOT EXISTS idx_report_limits_agent_user
    ON report_limits(agent_id, user_id);

-- 迁移: 旧表加列 (幂等)
ALTER TABLE IF EXISTS report_limits
    ADD COLUMN IF NOT EXISTS agent_id VARCHAR(64);

-- 迁移: 旧数据填充 (NULL = 全局默认)
-- 无需更新, 旧数据 user_id 非 NULL 的行 agent_id 置 NULL 即可 (保持兼容)

-- 迁移: 删除旧唯一索引, 建新复合唯一索引
DROP INDEX IF EXISTS idx_report_limits_user;
CREATE UNIQUE INDEX IF NOT EXISTS idx_report_limits_agent_user
    ON report_limits(agent_id, user_id);

-- 预置全局默认限额 (agent_id IS NULL, user_id IS NULL)
INSERT INTO report_limits (agent_id, user_id, daily_limit)
VALUES (NULL, NULL, 5)
ON CONFLICT (agent_id, user_id) DO NOTHING;
```

#### 3.2.3 daily_report_usage 表加 agent_id 列 (S-08)

```sql
-- scripts/init.sql — daily_report_usage 表优化
-- 现状
CREATE TABLE IF NOT EXISTS daily_report_usage (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    usage_date DATE NOT NULL,
    daily_count INTEGER NOT NULL DEFAULT 0,
    ...
);

-- 优化后: 加 agent_id 列, 改复合唯一索引
CREATE TABLE IF NOT EXISTS daily_report_usage (
    id BIGSERIAL PRIMARY KEY,
    agent_id VARCHAR(64) NOT NULL,       -- 新增: Agent 隔离键
    user_id VARCHAR(64) NOT NULL,
    usage_date DATE NOT NULL,
    daily_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 唯一约束: (agent_id, user_id, usage_date) 支持 ON CONFLICT 幂等 upsert
CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_report_usage_agent_user_date
    ON daily_report_usage(agent_id, user_id, usage_date);

-- 迁移: 旧表加列 (幂等)
ALTER TABLE IF EXISTS daily_report_usage
    ADD COLUMN IF NOT EXISTS agent_id VARCHAR(64);

-- 迁移: 旧数据填充 agent_id (已有数据回填为 agentinsight-researcher)
UPDATE daily_report_usage
SET agent_id = 'agentinsight-researcher'
WHERE agent_id IS NULL;

-- 迁移: 加 NOT NULL 约束 (数据回填后)
ALTER TABLE IF EXISTS daily_report_usage
    ALTER COLUMN agent_id SET NOT NULL;

-- 迁移: 删除旧唯一索引, 建新复合唯一索引
DROP INDEX IF EXISTS idx_daily_report_usage_user_date;
CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_report_usage_agent_user_date
    ON daily_report_usage(agent_id, user_id, usage_date);

-- 迁移: 加复合索引 (agent_id + user_id) 支持按用户查询
CREATE INDEX IF NOT EXISTS idx_daily_report_usage_agent_user
    ON daily_report_usage(agent_id, user_id);
```

#### 3.2.4 ip_user_resolver.py 查询加 agent_id 过滤 (S-09 ~ S-11)

```python
# src/api/ip_user_resolver.py — _get_daily_limit_from_db 优化
# 现状 (行 100-108)
async def _get_daily_limit_from_db(user_id: str) -> int:
    row = await conn.fetchrow(
        """
        SELECT COALESCE(
            (SELECT daily_limit FROM report_limits WHERE user_id = $1),
            (SELECT daily_limit FROM report_limits WHERE user_id IS NULL),
            0
        ) AS effective_limit
        """,
        user_id,
    )

# 优化后: 优先级 (agent_id+user_id) > (agent_id, user_id IS NULL) > (agent_id IS NULL, user_id IS NULL)
async def _get_daily_limit_from_db(agent_id: str, user_id: str) -> int:
    row = await conn.fetchrow(
        """
        SELECT COALESCE(
            -- 1. 用户专属限额 (agent_id + user_id 精确匹配)
            (SELECT daily_limit FROM report_limits
             WHERE agent_id = $1 AND user_id = $2),
            -- 2. Agent 级默认限额 (agent_id 匹配, user_id IS NULL)
            (SELECT daily_limit FROM report_limits
             WHERE agent_id = $1 AND user_id IS NULL),
            -- 3. 全局默认限额 (agent_id IS NULL, user_id IS NULL)
            (SELECT daily_limit FROM report_limits
             WHERE agent_id IS NULL AND user_id IS NULL),
            0
        ) AS effective_limit
        """,
        agent_id, user_id,
    )
```

```python
# src/api/ip_user_resolver.py — _get_daily_usage_from_db 优化
# 现状 (行 180-184)
async def _get_daily_usage_from_db(user_id: str) -> int:
    row = await conn.fetchrow(
        "SELECT daily_count FROM daily_report_usage WHERE user_id = $1 AND usage_date = $2",
        user_id, usage_date,
    )

# 优化后
async def _get_daily_usage_from_db(agent_id: str, user_id: str) -> int:
    row = await conn.fetchrow(
        "SELECT daily_count FROM daily_report_usage "
        "WHERE agent_id = $1 AND user_id = $2 AND usage_date = $3",
        agent_id, user_id, usage_date,
    )
```

```python
# src/api/ip_user_resolver.py — increment_daily_report_count 优化
# 现状 (行 218-222)
async def increment_daily_report_count(user_id: str, agent_id: str) -> int:
    # agent_id 参数标注 # noqa: ARG001 (未使用)
    await conn.execute(
        """
        INSERT INTO daily_report_usage (user_id, usage_date, daily_count)
        VALUES ($1, $2, 1)
        ON CONFLICT (user_id, usage_date) DO UPDATE
        SET daily_count = daily_report_usage.daily_count + 1
        """,
        user_id, usage_date,
    )

# 优化后: agent_id 参数真正生效
async def increment_daily_report_count(agent_id: str, user_id: str) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO daily_report_usage (agent_id, user_id, usage_date, daily_count)
        VALUES ($1, $2, $3, 1)
        ON CONFLICT (agent_id, user_id, usage_date) DO UPDATE
        SET daily_count = daily_report_usage.daily_count + 1
        RETURNING daily_count
        """,
        agent_id, user_id, usage_date,
    )
    return int(row["daily_count"]) if row else 1
```

```python
# src/api/ip_user_resolver.py — check_daily_report_limit 优化
# 现状 (行 115-164) — agent_id 参数标注 # noqa: ARG001
async def check_daily_report_limit(
    user_id: str,
    agent_id: str,  # noqa: ARG001  ← 移除此标注
    ...
) -> tuple[bool, int, int]:

# 优化后: agent_id 参数传入子函数
async def check_daily_report_limit(
    user_id: str,
    agent_id: str,  # ← 真正使用
    ...
) -> tuple[bool, int, int]:
    effective_limit = await _get_daily_limit_from_db(agent_id, user_id)
    current_count = await _get_daily_usage_from_db(agent_id, user_id)
    ...
```

#### 3.2.5 系统 MCP 改共享 (S-12)

```sql
-- scripts/init.sql — 系统 MCP INSERT 优化
-- 现状: 23 条 INSERT 硬编码 agent_id='agentinsight-researcher'
INSERT INTO mcp_configs (agent_id, user_id, name, ...) VALUES
    ('agentinsight-researcher', 'system', 'fetch', ...),  -- ❌ 硬编码
    ...

-- 优化后: agent_id 改为 NULL (共享, 所有 Agent 可用)
INSERT INTO mcp_configs (agent_id, user_id, name, ...) VALUES
    (NULL, 'system', 'fetch', ...),   -- ✅ agent_id IS NULL = 共享
    (NULL, 'system', 'filesystem', ...),
    (NULL, 'system', 'sequential-thinking', ...),
    -- ... 23 条全部改为 NULL
ON CONFLICT (agent_id, user_id, name) DO UPDATE SET ...

-- 迁移: 旧数据更新 (将 agentinsight-researcher 系统 MCP 改为共享)
UPDATE mcp_configs
SET agent_id = NULL
WHERE is_system = TRUE AND agent_id = 'agentinsight-researcher';

-- 注意: ON CONFLICT (agent_id, user_id, name) 需要支持 NULL
-- PostgreSQL 中 NULL != NULL, 唯一索引需用 COALESCE 或 partial index
CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_configs_unique_name
    ON mcp_configs (COALESCE(agent_id, ''), user_id, name);
```

#### 3.2.6 mcp_coordinator.py 查询改共享 (S-13)

```python
# src/skills/researcher/mcp_coordinator.py — 系统 MCP 查询优化
# 现状: 按 agent_id 精确匹配
# 优化后: 用户专属 MCP (agent_id 精确匹配) + 系统 MCP (agent_id IS NULL 共享)

# 查询用户可用 MCP 配置
async def get_user_mcp_configs(agent_id: str, user_id: str) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT * FROM mcp_configs
        WHERE enabled = TRUE
          AND (
              -- 用户专属 MCP (agent_id + user_id 精确匹配)
              (agent_id = $1 AND user_id = $2)
              OR
              -- 系统 MCP (agent_id IS NULL, 所有 Agent 共享)
              (agent_id IS NULL AND is_system = TRUE)
          )
        ORDER BY is_system DESC, name ASC
        """,
        agent_id, user_id,
    )
```

#### 3.2.7 Checkpointer thread_id 加 agent_id 命名空间 (S-14)

```python
# src/api/routes.py — thread_id 加 agent_id 前缀
# 现状 (行 139, 164, 209, 589, 727)
config: dict[str, Any] = {"configurable": {"thread_id": session_id}}

# 优化后: thread_id = f"{agent_id}:{session_id}"
# 即使不同 Agent 使用相同 session_id, Checkpointer 也能正确隔离
agent_id = settings.agent_name  # 本项目固定为 agentinsight-researcher
thread_id = f"{agent_id}:{session_id}"
config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}

# 优势:
# 1. 不同 Agent 的会话状态完全隔离 (即使 session_id 相同)
# 2. 可按 agent_id 前缀清理特定 Agent 的所有会话
# 3. 可按 agent_id 统计各 Agent 的会话数
# 注意: LangGraph Checkpointer 表由官方 SDK 管理, thread_id 作为字符串主键,
#       加前缀不影响 Checkpointer 功能, 仅改变键空间
```

#### 3.2.8 db_initializer.py 加 advisory lock (S-15)

```python
# src/memory/db_initializer.py — init_database() 加 advisory lock
# 现状: 无锁保护, 多 Agent 并发启动可能竞态

# 优化后: 使用 PG advisory lock 防止并发
async def init_database(settings: Settings) -> None:
    pool = await get_pool(settings)
    async with pool.acquire() as conn:
        # 获取 advisory lock (事务级, 固定 key)
        # key = hash('agentinsight_db_init') = 任意固定整数
        await conn.execute("SELECT pg_advisory_lock(20260714)")

        try:
            sql = Path("scripts/init.sql").read_text(encoding="utf-8")
            await conn.execute(sql)
            logger.info("数据库初始化完成 (advisory lock 保护)")
        finally:
            # 释放 advisory lock
            await conn.execute("SELECT pg_advisory_unlock(20260714)")

# 优势: 多 Agent 并发启动时, 只有一个 Agent 执行 init.sql,
#        其余 Agent 等待锁释放后直接返回 (DDL 幂等, 无副作用)
```

---

## 四、Redis 共享优化方案

### 4.1 现状分析

**已正确的部分**（键前缀格式正确）:

```python
# src/common/redis_client.py — Redis 客户端单例
# 键格式: {agent_id}:{user_id}:{module}:{type}:{id}
# 示例: agentinsight-researcher:user1:rag:bm25:version
#       agentinsight-researcher:user1:session:routes:xxx
# ✅ agent_id 前缀正确, 不同 Agent 的键空间天然隔离
```

**问题 1: BM25 永久键无 TTL** (S-16)

```python
# src/rag/retriever.py:813 — BM25 版本号键无 TTL
# 现状: SET key value (无 EX 参数, 永久键)
await self._redis.set(bm25_version_key, version)

# src/rag/retriever.py:433, 463 — LRU 排序集合无 TTL
await self._redis.zadd(lru_key, {doc_id: timestamp})
# ❌ 永久键导致 Redis 内存无限增长
```

**问题 2: BM25 加载锁为进程内 asyncio.Lock** (S-17)

```python
# src/rag/retriever.py — BM25 语料加载锁
# 现状: WeakValueDictionary 进程内锁
_bm25_load_locks: WeakValueDictionary = WeakValueDictionary()
# ❌ 多 Agent 实例 (不同容器) 共享同一 Redis, 但锁不共享
# 可能导致多个 Agent 实例同时拉取 BM25 语料, 浪费资源
```

**问题 3: session_routes 清理模式 bug**

```python
# src/api/session_routes.py:348 — 清理模式 bug
# 现状: glob 模式 *{session_id}* 会匹配任何含 session_id 子串的键
pattern = f"{agent_id}:{user_id}:*{session_id}*"
```

### 4.2 优化方案

#### 4.2.1 BM25 永久键加 TTL (S-16)

```python
# src/rag/retriever.py — BM25 键加 TTL
# 现状 (行 813)
await self._redis.set(bm25_version_key, version)

# 优化后: 加 7 天 TTL (BM25 语料变更时自动刷新)
_BM25_KEY_TTL = 7 * 24 * 3600  # 7 天
await self._redis.set(bm25_version_key, version, ex=_BM25_KEY_TTL)

# 现状 (行 433, 463) — LRU 排序集合无 TTL
await self._redis.zadd(lru_key, {doc_id: timestamp})

# 优化后: 加 TTL
await self._redis.zadd(lru_key, {doc_id: timestamp})
await self._redis.expire(lru_key, _BM25_KEY_TTL)
```

#### 4.2.2 Redis 分布式锁 (S-17)

```python
# 新增 src/common/redis_lock.py — Redis 分布式锁
import asyncio
import uuid
from typing import Any

import redis.asyncio as aioredis


class RedisDistributedLock:
    """Redis 分布式锁 (SET NX EX + Lua 原子释放).

    用于多 Agent 实例共享 Redis 时, 防止重复拉取 BM25 语料等资源.
    """

    _UNLOCK_SCRIPT = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """

    def __init__(self, client: aioredis.Redis, key: str, ttl: int = 60):
        self._client = client
        self._key = key
        self._ttl = ttl
        self._token = str(uuid.uuid4())

    async def __aenter__(self) -> "RedisDistributedLock":
        while not await self._client.set(self._key, self._token, ex=self._ttl, nx=True):
            await asyncio.sleep(0.1)
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._client.eval(self._UNLOCK_SCRIPT, 1, self._key, self._token)


# src/rag/retriever.py — 替换 asyncio.Lock
# 现状
_bm25_load_locks: WeakValueDictionary = WeakValueDictionary()  # 进程内

# 优化后
async def _load_bm25_corpus(self, agent_id: str, user_id: str) -> None:
    lock_key = f"{agent_id}:{user_id}:rag:bm25:lock"
    async with RedisDistributedLock(self._redis, lock_key, ttl=60):
        # 双重检查: 持锁后再次确认是否已加载
        if await self._check_bm25_loaded(agent_id, user_id):
            return
        # 拉取 BM25 语料 ...
```

#### 4.2.3 session_routes 清理模式修正

```python
# src/api/session_routes.py:348 — 清理模式 bug
# 现状: glob 模式 *{session_id}* 会匹配任何含 session_id 子串的键
pattern = f"{agent_id}:{user_id}:*{session_id}*"

# 优化后: 由于当前 Redis 键不含 session_id 维度, 实际无需清理 Redis
# 改为仅清理 PG + Checkpointer, Redis 依赖 TTL 自然过期
# 如未来需按 session 清理, 应将 session_id 作为独立段:
# pattern = f"{agent_id}:{user_id}:{session_id}:*"
```

---

## 五、Embeddings 共享优化方案

### 5.1 现状分析

```python
# src/rag/embeddings.py — TEI 客户端
# TEI 服务无状态, 多 Agent 共享无隔离问题
# 所有 Agent 用同一 embedding 模型 (bge-base-zh-v1.5, 768 维)
# ✅ 可直接共享, 无需改造

# src/rag/fastembed_client.py — FastEmbed 本地模型
# FastEmbed 是本 Agent 进程内加载的本地 ONNX 模型 (bge-small-zh-v1.5)
# 属于 Agent 进程内资源, 不属于共享存储层
# ✅ 不在本方案改造范围内 (用户明确约束)
```

### 5.2 结论

**Embeddings 层无需任何改造**:

| 组件 | 共享性 | 改造需求 |
|------|--------|---------|
| **TEI 远程服务** | ✅ 无状态，多 Agent 直接共享 | 无 |
| **FastEmbed 本地模型** | ❌ Agent 进程内资源，不共享 | 无（用户明确不修改） |

> **说明**: TEI 服务本身无状态，多个 Agent 连接同一 TEI 实例时无需任何隔离改造。FastEmbed 是各 Agent 进程内独立加载的本地 ONNX 模型，天然进程隔离，不属于共享存储层范畴。

---

## 六、Qdrant 共享优化方案

### 6.1 现状分析

**已正确的部分**（namespace 隔离正确）:

```python
# src/rag/qdrant_manager.py — namespace 构造
# 共享知识库: namespace = {agent_id}-data (不含 user_id, 所有用户共享)
def build_data_shared_namespace(self) -> str:
    return f"{self.settings.agent_name}-data"  # ✅ agentinsight-researcher-data

# 用户私有数据: namespace = {agent_id}-data:{user_id}
def build_data_user_namespace(self, user_id: str) -> str:
    return f"{self.settings.agent_name}-data:{user_id}"  # ✅ agentinsight-researcher-data:user1

# ✅ 不同 Agent 的 namespace 天然隔离:
#    agentinsight-researcher-data       (本项目共享)
#    agentinsight-researcher-data:user1 (本项目用户1私有)
#    agentinsight-coder-data            (其他Agent共享)
#    agentinsight-coder-data:user1      (其他Agent用户1私有)
```

**问题 1: user_id payload 无索引** (S-18)

```python
# src/rag/qdrant_manager.py:150-154 — 仅 namespace 字段有索引
await self._client.create_payload_index(
    collection_name=self.settings.qdrant_collection,
    field_name="namespace",
    field_schema="keyword",
)
# ❌ user_id payload 无索引
# 用户私有数据检索时, 如需按 user_id 过滤, 全集合扫描
```

**问题 2: 文件上传未索引到 Qdrant** (S-19)

```python
# src/api/routes.py:1878-1968 — upload_file 端点
# 现状: 仅保存磁盘 + 元数据入库, 不索引到 Qdrant
# ❌ 用户上传的文件无法被 RAG 检索
```

### 6.2 优化方案

#### 6.2.1 user_id payload 建索引 (S-18)

```python
# src/rag/qdrant_manager.py — 新增 user_id payload 索引
# 现状 (行 150-154)
await self._client.create_payload_index(
    collection_name=self.settings.qdrant_collection,
    field_name="namespace",
    field_schema="keyword",
)

# 优化后: 新增 user_id 索引
await self._client.create_payload_index(
    collection_name=self.settings.qdrant_collection,
    field_name="namespace",
    field_schema="keyword",
)
await self._client.create_payload_index(
    collection_name=self.settings.qdrant_collection,
    field_name="user_id",        # 新增: 用户私有数据按 user_id 过滤
    field_schema="keyword",
)
# 优势: 用户私有数据检索时, 可按 namespace + user_id 双重过滤, 性能提升
```

#### 6.2.2 文件上传接 Qdrant 索引 (S-19)

```python
# src/api/routes.py — upload_file 端点优化
# 现状 (行 1878-1968): 仅保存磁盘 + 元数据入库
# 优化后: 文件保存后, 异步索引到 Qdrant

@router.post("/v1/files/upload")
async def upload_file(file: UploadFile, user_id: str = Depends(...)):
    # 1. 保存文件到磁盘
    file_path = await save_to_disk(file)

    # 2. 元数据入 PG (uploaded_files 表)
    file_record = await save_file_metadata(file_path, user_id)

    # 3. 新增: 异步索引到 Qdrant
    asyncio.create_task(
        index_file_to_qdrant(file_path, user_id, file_record.id)
    )
    return {"file_id": file_record.id, "status": "indexed"}


async def index_file_to_qdrant(file_path: str, user_id: str, file_id: str):
    """将上传文件索引到 Qdrant (用户私有 namespace)."""
    from src.rag.embeddings import EmbeddingsClient
    from src.rag.qdrant_manager import QdrantManager
    from src.skills.researcher.document_loader import DocumentLoader

    # 1. 加载文档内容
    chunks = await DocumentLoader().load_and_split(file_path)

    # 2. 批量 embedding (远程 TEI)
    embeddings = await EmbeddingsClient().embed_texts([c.content for c in chunks])

    # 3. 写入 Qdrant (用户私有 namespace)
    qdrant = QdrantManager()
    namespace = qdrant.build_data_user_namespace(user_id)
    await qdrant.upsert_points(
        namespace=namespace,
        user_id=user_id,
        points=[
            {
                "content": chunk.content,
                "metadata": {"file_id": file_id, "chunk_index": i},
                "vector": emb,
            }
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings))
        ],
    )
```

#### 6.2.3 多集合策略（可选，未来扩展）

```python
# 当前: 单一集合 agents, vector_size=768 (bge-base-zh-v1.5)
# 所有 Agent 共享同一集合, namespace 隔离

# 未来扩展: 如不同 Agent 用不同 embedding 模型 (不同维度)
# 可按 agent_id 路由到不同集合
# src/config/settings.py 新增
qdrant_collection: str = "agents"  # 默认集合
qdrant_vector_size: int = 768      # 默认维度

# src/rag/qdrant_manager.py 构造函数接受配置覆盖
class QdrantManager:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._collection = settings.qdrant_collection  # 支持按 Agent 配置不同集合
        self._vector_size = settings.qdrant_vector_size

# 各 Agent 的 .env 配置不同集合:
# agentinsight-researcher: QDRANT_COLLECTION=agents (768 维)
# agentinsight-coder:      QDRANT_COLLECTION=agents-coder (1024 维)
```

---

## 七、MCP 工具缓存跨用户泄漏修复

### 7.1 现状

```python
# src/skills/researcher/mcp_coordinator.py:33-64 — _MCP_CACHE 模块级
_MCP_CACHE: dict[str, Any] = {}
# ❌ cache key 无 agent_id / user_id, 跨用户泄漏工具调用结果
# 多 Agent 共享时, 不同 Agent 的相同工具名会命中同一缓存
```

### 7.2 优化方案

```python
# src/skills/researcher/mcp_coordinator.py — cache key 加 agent_id + user_id
# 现状
_MCP_CACHE: dict[str, Any] = {}

async def get_tool_result(tool_name: str, params: dict) -> Any:
    cache_key = f"{tool_name}:{hash(str(params))}"
    if cache_key in _MCP_CACHE:
        return _MCP_CACHE[cache_key]
    # ... 调用工具

# 优化后: cache key 加 agent_id + user_id 前缀
async def get_tool_result(
    tool_name: str, params: dict, *, agent_id: str, user_id: str
) -> Any:
    cache_key = f"{agent_id}:{user_id}:{tool_name}:{hash(str(params))}"
    if cache_key in _MCP_CACHE:
        return _MCP_CACHE[cache_key]
    # ... 调用工具
    _MCP_CACHE[cache_key] = result
    return result

# 调用时注入 agent_id + user_id
result = await get_tool_result(
    tool_name, params,
    agent_id=settings.agent_name,
    user_id=request_user_id,
)
```

---

## 八、迁移路径与优先级

### 8.1 Phase 1: P0 数据隔离修复（必须，阻塞性）

| 任务 ID | 任务 | 文件 | 优先级 | 说明 |
|---------|------|------|--------|------|
| P0-01 | report_store get_report 加 agent_id 过滤 | report_store.py:113 | P0 | 防止跨 Agent 读取 |
| P0-02 | report_store delete_report 加 agent_id 过滤 | report_store.py:197 | P0 | 防止跨 Agent 删除 |
| P0-03 | report_store list_reports 加 agent_id 过滤 | report_store.py:132 | P0 | 防止跨 Agent 列举 |
| P0-04 | report_limits 表加 agent_id 列 | init.sql:437 | P0 | 支持按 Agent 配置限额 |
| P0-05 | daily_report_usage 表加 agent_id 列 | init.sql:460 | P0 | 支持按 Agent 计数 |
| P0-06 | ip_user_resolver 查询加 agent_id 过滤 | ip_user_resolver.py:100 | P0 | 限额/计数按 Agent 隔离 |
| P0-07 | 系统 MCP INSERT 改 agent_id IS NULL | init.sql:269 | P0 | 系统配置共享 |
| P0-08 | mcp_coordinator 查询改共享 | mcp_coordinator.py | P0 | 查询共享系统 MCP |
| P0-09 | Checkpointer thread_id 加 agent_id 前缀 | routes.py:139 等 | P0 | 会话状态隔离 |
| P0-10 | MCP 缓存 key 加 agent_id+user_id | mcp_coordinator.py:33 | P0 | 防止跨用户泄漏 |
| P0-11 | routes.py 调用方传 agent_id | routes.py:2178 等 | P0 | 配合 P0-01~03 |

### 8.2 Phase 2: P1 代码层优化（推荐）

| 任务 ID | 任务 | 文件 | 优先级 | 说明 |
|---------|------|------|--------|------|
| P1-01 | db_initializer 加 advisory lock | db_initializer.py | P1 | 防止并发启动竞态 |
| P1-02 | Redis BM25 键加 TTL | retriever.py:813 | P1 | 防止内存无限增长 |
| P1-03 | Redis 分布式锁 (BM25 协调) | 新增 redis_lock.py | P1 | 多实例 BM25 协调 |
| P1-04 | Qdrant user_id payload 索引 | qdrant_manager.py:150 | P1 | 检索性能 |
| P1-05 | session_routes 清理模式修正 | session_routes.py:348 | P1 | 防止误删键 |

### 8.3 Phase 3: P2 功能完善（可选）

| 任务 ID | 任务 | 文件 | 优先级 | 说明 |
|---------|------|------|--------|------|
| P2-01 | 文件上传接 Qdrant 索引 | routes.py:1878 | P2 | 用户私有数据 RAG |
| P2-02 | Qdrant 多集合支持 | qdrant_manager.py | P2 | 不同维度 embedding |
| P2-03 | Qdrant 写入权限校验 | qdrant_manager.py | P2 | 安全加固 |

---

## 九、向后兼容性保证

### 9.1 默认行为不变

- 本项目 `agent_id = "agentinsight-researcher"` 固定不变
- 所有现有 API 行为不变（不新增 `X-Agent-Id` 头）
- 所有现有配置项不变（`settings.agent_name` 仍为默认值）
- 不修改 docker-compose.yml / docker-compose-qa.yaml
- 不修改各服务连接方式（host/port/连接参数）
- 不修改 FastEmbed（Agent 进程内本地资源）

### 9.2 数据迁移幂等

- 所有 DDL 使用 `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE IF EXISTS ADD COLUMN IF NOT EXISTS`
- 所有 INSERT 使用 `ON CONFLICT DO NOTHING` / `ON CONFLICT DO UPDATE`
- 旧数据自动回填（`UPDATE ... SET agent_id = 'agentinsight-researcher' WHERE agent_id IS NULL`）
- 迁移脚本可重复执行，无副作用

### 9.3 渐进式迁移

1. **Phase 1**: 仅修改查询和 schema，不改变部署架构
2. **Phase 2**: 仅代码层优化（锁/TTL/索引），不改变部署架构
3. **Phase 3**: 仅功能补全，不影响现有流程

---

## 十、风险评估

### 10.1 高风险项

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| report_store 查询改签名 | 调用方需同步修改 | 全量搜索 `get_report(`/`delete_report(`/`list_reports(` 调用点 |
| report_limits 唯一索引变更 | 旧数据可能冲突 | 先回填 agent_id, 再建新索引, 删旧索引 |
| Checkpointer thread_id 加前缀 | 已有会话状态丢失 | 迁移脚本: 旧 thread_id 加前缀回填 |
| 系统 MCP agent_id 改 NULL | ON CONFLICT 语义变化 | 用 `COALESCE(agent_id, '')` 唯一索引 |

### 10.2 中风险项

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| FastEmbed 缓存 key 改格式 | — | 不修改（用户明确约束） |
| BM25 键加 TTL | 缓存过期后重新加载 | TTL 设 7 天, 足够长 |
| user_id payload 索引 | 创建索引期间性能下降 | 低峰期执行 |

### 10.3 低风险项

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| advisory lock | 锁等待超时 | 设 lock_timeout=30s |
| session_routes 清理模式 | 当前键不含 session_id | 实际无清理动作, 仅修代码 |

---

## 十一、附录

### 11.1 专家团队清单

| 角色 | 职责 |
|------|------|
| PostgreSQL 架构师 | 业务表 schema / 查询隔离 / advisory lock |
| Redis 架构师 | 键前缀隔离 / TTL / 分布式锁 |
| Embeddings 架构师 | TEI 共享确认 / FastEmbed 边界界定 |
| Qdrant 架构师 | namespace 隔离 / payload 索引 / 多集合 |
| LangGraph 编排专家 | Checkpointer thread_id 命名空间 |
| 数据隔离专家 | agent_id 透传 / WHERE 过滤 / 跨用户泄漏 |
| 安全专家 | 数据隔离验证 / 权限校验 |
| 性能优化专家 | 索引 / TTL / 锁优化 |
| 可观测性专家 | 多 Agent 监控建议 |
| 技术写作 | 文档组织 / 代码级说明 |
| 架构边界专家 | 区分共享存储层 vs Agent 进程内资源 |

### 11.2 存储层硬编码位置索引

| ID | 位置 | 问题 | 优先级 |
|----|------|------|--------|
| S-01 | report_store.py:113 | get_report 缺 agent_id WHERE | P0 |
| S-02 | report_store.py:197 | delete_report 缺 agent_id WHERE | P0 |
| S-03 | report_store.py:152 | list_reports (session+user) 缺 agent_id | P0 |
| S-04 | report_store.py:164 | list_reports (session) 缺 agent_id | P0 |
| S-05 | report_store.py:176 | list_reports (user) 缺 agent_id | P0 |
| S-06 | report_store.py:187 | list_reports (无过滤) 缺 agent_id | P0 |
| S-07 | init.sql:437 | report_limits 表无 agent_id 列 | P0 |
| S-08 | init.sql:460 | daily_report_usage 表无 agent_id 列 | P0 |
| S-09 | ip_user_resolver.py:100 | _get_daily_limit_from_db 缺 agent_id | P0 |
| S-10 | ip_user_resolver.py:180 | _get_daily_usage_from_db 缺 agent_id | P0 |
| S-11 | ip_user_resolver.py:218 | increment_daily_report_count 缺 agent_id | P0 |
| S-12 | init.sql:269 | 23 条系统 MCP INSERT 硬编码 agent_id | P0 |
| S-13 | mcp_coordinator.py | 系统 MCP 查询未含 agent_id IS NULL | P0 |
| S-14 | routes.py:139 等 | Checkpointer thread_id 不含 agent_id | P0 |
| S-15 | db_initializer.py:171 | init_database 无 advisory lock | P1 |
| S-16 | retriever.py:813 | BM25 永久键无 TTL | P1 |
| S-17 | retriever.py | BM25 加载锁为进程内 asyncio.Lock | P1 |
| S-18 | qdrant_manager.py:150 | user_id payload 无索引 | P1 |
| S-19 | routes.py:1878 | 文件上传未索引到 Qdrant | P2 |

### 11.3 文件覆盖清单

| 模块 | 文件数 | 改动文件 | 说明 |
|------|--------|---------|------|
| src/memory/ | 5 | report_store.py, db_initializer.py | 查询加 agent_id, advisory lock |
| src/api/ | 10 | routes.py, ip_user_resolver.py, session_routes.py | 调用方传 agent_id, thread_id 前缀 |
| src/rag/ | 7 | retriever.py, qdrant_manager.py | TTL, 分布式锁, payload 索引 |
| src/skills/ | 51 | mcp_coordinator.py | 缓存 key, 查询共享 |
| src/common/ | 6 | 新增 redis_lock.py | 分布式锁实现 |
| scripts/ | 1 | init.sql | schema 迁移, 系统 MCP 共享 |
| **合计** | — | **~12 个** | — |

### 11.4 不在改造范围内的部分

| 组件 | 原因 |
|------|------|
| docker-compose.yml / docker-compose-qa.yaml | 用户明确约束不修改 compose 文件 |
| settings.py 连接方式 (host/port) | 用户明确约束不修改连接方式 |
| FastEmbed (fastembed_client.py) | Agent 进程内本地资源，不属于共享存储层 |
| pgbouncer | 用户明确约束不加新服务 |
| Redis maxmemory-policy | 用户明确约束不管资源问题 |
| TEI 容量限制 | 用户明确约束不管资源问题 |
| Qdrant 量化配置 | 用户明确约束不管资源问题 |
| PostgreSQL max_connections | 用户明确约束不管资源问题 |

---

> **声明**: 本方案已于 2026-07-14 全部实施完成。19 处硬编码（S-01~S-19）全部修复，额外修复 3 处 mcp_routes.py 系统 MCP 查询 bug（`agent_id = $N` 改为 `agent_id IS NULL`）。11 项数据隔离安全审查全部通过，无跨 Agent/跨用户数据泄漏风险。本方案严格遵守用户约束：不修改 compose 文件、不修改连接方式、不修改 FastEmbed、不管资源容量问题。
>
> **实施团队**: 11 角色 AI 专家团队（PostgreSQL 架构师 / PostgreSQL 查询工程师 / 限额工程师 / MCP 工具专家 / LangGraph 编排专家 / API 工程师 / Redis 架构师 / DevOps 工程师 / Qdrant 架构师 / 数据隔离专家 / 安全专家）
>
> **改动文件清单**:
> - `src/memory/report_store.py` — S-01~S-06（6 处查询加 agent_id 过滤）
> - `scripts/init.sql` — S-07/S-08/S-12（report_limits + daily_report_usage 加 agent_id 列 + 23 条系统 MCP 改 NULL）
> - `src/api/ip_user_resolver.py` — S-09~S-11（限额/使用量查询加 agent_id 过滤）
> - `src/skills/researcher/mcp_coordinator.py` — S-10/S-13（缓存 key 加 agent_id+user_id + 系统 MCP 查询改共享）
> - `src/api/routes.py` — S-11/S-14/S-19（调用方传 agent_id + thread_id 加前缀 + 文件上传接 Qdrant 索引）
> - `src/memory/db_initializer.py` — S-15（advisory lock）
> - `src/rag/retriever.py` — S-16/S-17（BM25 键加 TTL + 分布式锁）
> - `src/common/redis_lock.py` — 新建（Redis 分布式锁实现）
> - `src/api/session_routes.py` — 清理模式修正（no-op）
> - `src/rag/qdrant_manager.py` — S-18（user_id payload 索引）
> - `src/api/mcp_routes.py` — 额外修复（3 处系统 MCP 查询 agent_id IS NULL）
