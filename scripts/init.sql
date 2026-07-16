-- agentinsight-researcher 数据库初始化
-- 单库 agents, 业务表含 agent_id+user_id 双列复合索引
-- LangGraph PostgresSaver 表由官方 SDK 管理 (thread_id 已含会话隔离)
--
-- 设计原则:
-- 1. CREATE TABLE 定义与最终状态一致 (新部署直接正确)
-- 2. ALTER TABLE 保留作为旧表兜底 (CREATE TABLE IF NOT EXISTS 对已存在的表是 no-op, 不会添加新列)
-- 3. 所有 DDL 使用 IF NOT EXISTS / IF EXISTS / CREATE OR REPLACE 保证幂等 (Agent 启动时重复执行不出错)
-- 4. 唯一索引创建前清理重复数据 (防止历史脏数据导致索引创建失败)

-- 启用扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ========== LangGraph Checkpointer 表 ==========
-- 由 langgraph-checkpoint-postgres SDK 自动管理, 此处仅占位说明
-- 实际表名: checkpoints / writes / migrations (由 SDK 创建)

-- ========== 业务表: 研究会话 ==========
-- 合并: title 字段 + query/report_type/report_format 去掉 NOT NULL (原 ALTER 迁移, 现合并到 CREATE)
CREATE TABLE IF NOT EXISTS research_sessions (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,           -- 即 thread_id
    agent_id VARCHAR(64) NOT NULL,             -- agent_name, 全局唯一隔离键
    user_id VARCHAR(64) NOT NULL,              -- 用户隔离
    query TEXT,                                -- 原始研究请求 (允许空会话)
    report_type VARCHAR(32) DEFAULT 'detailed_report',  -- 空会话无报告类型 (任务1: 默认详细报告)
    report_format VARCHAR(16) DEFAULT 'markdown',    -- 空会话无报告格式
    language VARCHAR(8) DEFAULT 'zh',               -- 报告语言 (zh/en)
    agent_role VARCHAR(256),                   -- LLM 动态生成的角色 persona
    agent_role_server VARCHAR(64),             -- 角色简称 (如 financial_analyst)
    title VARCHAR(256),                        -- 会话列表显示标题
    status VARCHAR(32) NOT NULL DEFAULT 'pending',  -- pending/running/completed/failed
    total_cost_usd NUMERIC(12,6) DEFAULT 0,
    total_tokens BIGINT DEFAULT 0,
    client_ip VARCHAR(64),                     -- 客户端 IP 地址 (审计追溯用, PII)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '30 days')
);
-- 索引: 删除被复合索引前缀覆盖的冗余单列索引 (idx_research_sessions_agent_user, idx_research_sessions_session)
CREATE INDEX IF NOT EXISTS idx_research_sessions_expires ON research_sessions(expires_at);
-- 复合索引 (agent_id + user_id + updated_at DESC): 支持按用户列出会话列表 (按最近更新排序)
CREATE INDEX IF NOT EXISTS idx_research_sessions_agent_user_updated
    ON research_sessions(agent_id, user_id, updated_at DESC);

-- ========== 业务表: 研究报告存储 ==========
-- 合并: created_at/updated_at 加 NOT NULL 约束 (原 ALTER 迁移, 现合并到 CREATE)
CREATE TABLE IF NOT EXISTS research_reports (
    report_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    query TEXT NOT NULL,
    report_md TEXT NOT NULL,
    report_format VARCHAR(32) DEFAULT 'markdown',
    sources JSONB DEFAULT '[]'::jsonb,
    agent_role VARCHAR(256),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- 索引: 删除被复合索引前缀覆盖的冗余单列索引 (idx_research_reports_session, idx_research_reports_user, idx_research_reports_created)
-- 复合索引 (agent_id, user_id): 多 Agent 数据隔离
CREATE INDEX IF NOT EXISTS idx_research_reports_agent_user ON research_reports(agent_id, user_id);
-- 复合索引 (session_id, agent_id, user_id): 加速按会话检索报告
CREATE INDEX IF NOT EXISTS idx_research_reports_session_agent_user ON research_reports(session_id, agent_id, user_id);
-- 迁移: 已有 research_reports 表补充新增列 (PostgreSQL 9.6+, 旧表兜底)
ALTER TABLE IF EXISTS research_reports ADD COLUMN IF NOT EXISTS report_id UUID DEFAULT gen_random_uuid();
ALTER TABLE IF EXISTS research_reports ADD COLUMN IF NOT EXISTS query TEXT;
ALTER TABLE IF EXISTS research_reports ADD COLUMN IF NOT EXISTS report_format VARCHAR(32) DEFAULT 'markdown';
ALTER TABLE IF EXISTS research_reports ADD COLUMN IF NOT EXISTS agent_role VARCHAR(256);
ALTER TABLE IF EXISTS research_reports ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
-- 兜底唯一索引: 旧表无 report_id PK 时保证唯一 (新表已有 PK, DO 块条件化避免冗余)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE tablename = 'research_reports'
          AND indexname = 'research_reports_pkey'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE tablename = 'research_reports'
          AND indexname = 'idx_research_reports_report_id'
    ) THEN
        CREATE UNIQUE INDEX idx_research_reports_report_id ON research_reports(report_id);
    END IF;
END $$;

-- ========== 业务表: 搜索记录 (用于审计与质量分析) ==========
CREATE TABLE IF NOT EXISTS research_search_logs (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    sub_query TEXT NOT NULL,
    retriever VARCHAR(32) NOT NULL,            -- bocha/tavily/duckduckgo/arxiv/pubmed
    region VARCHAR(16) NOT NULL DEFAULT 'cn',  -- cn(国内) / global(国外)
    results_count INTEGER DEFAULT 0,
    latency_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_research_search_logs_agent_user ON research_search_logs(agent_id, user_id);
CREATE INDEX IF NOT EXISTS idx_research_search_logs_session ON research_search_logs(session_id);

-- ========== 业务表: 上传文件元数据 (用户需求 8) ==========
-- 合并: updated_at 字段 (原 ALTER 迁移, 现合并到 CREATE)
CREATE TABLE IF NOT EXISTS uploaded_files (
    id BIGSERIAL PRIMARY KEY,
    agent_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(64),
    file_name VARCHAR(256) NOT NULL,
    file_path VARCHAR(512) NOT NULL,
    file_size BIGINT NOT NULL,
    file_type VARCHAR(32),                     -- pdf/docx/md/txt/html/csv/xlsx/pptx
    content_hash VARCHAR(64),                  -- SHA256, 用于去重
    namespace VARCHAR(128),                    -- Qdrant namespace
    status VARCHAR(32) NOT NULL DEFAULT 'pending',  -- pending/embedded/failed
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_uploaded_files_agent_user ON uploaded_files(agent_id, user_id);
CREATE INDEX IF NOT EXISTS idx_uploaded_files_session ON uploaded_files(session_id);
CREATE INDEX IF NOT EXISTS idx_uploaded_files_hash ON uploaded_files(content_hash);

-- ========== 业务表: Token 使用记录 (用户需求 10, Token 优化审计) ==========
CREATE TABLE IF NOT EXISTS token_usage_logs (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    stage VARCHAR(64) NOT NULL,                -- planning/researching/reviewing/writing
    llm_tier VARCHAR(16) NOT NULL,             -- fast/smart/strategic
    model VARCHAR(64),
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd NUMERIC(10,6) DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_token_usage_logs_agent_user ON token_usage_logs(agent_id, user_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_logs_session ON token_usage_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_logs_stage ON token_usage_logs(stage);

-- ========== V7: 时间戳字段完整性修复 (P0) ==========
-- 1. 回填 research_reports 历史 NULL 值 (幂等: 二次执行无 NULL 行可更新)
UPDATE research_reports SET created_at = NOW() WHERE created_at IS NULL;
UPDATE research_reports SET updated_at = NOW() WHERE updated_at IS NULL;

-- 2. 加固 research_reports NOT NULL 约束 (旧表兜底, 新表 CREATE TABLE 已含 NOT NULL)
-- SET NOT NULL 在列已为 NOT NULL 时是 no-op, 幂等安全
ALTER TABLE IF EXISTS research_reports
    ALTER COLUMN created_at SET NOT NULL,
    ALTER COLUMN created_at SET DEFAULT NOW(),
    ALTER COLUMN updated_at SET NOT NULL,
    ALTER COLUMN updated_at SET DEFAULT NOW();

-- 3. 为 uploaded_files 补 updated_at 字段 (旧表兜底, 新表 CREATE TABLE 已含)
ALTER TABLE IF EXISTS uploaded_files
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- 4. 通用触发器函数: 自动维护 updated_at (幂等: CREATE OR REPLACE)
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 5. 为状态会变更的 4 张表挂触发器 (PostgreSQL 14+ 支持 CREATE OR REPLACE TRIGGER)
CREATE OR REPLACE TRIGGER trg_research_sessions_updated_at
    BEFORE UPDATE ON research_sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE OR REPLACE TRIGGER trg_research_reports_updated_at
    BEFORE UPDATE ON research_reports
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE OR REPLACE TRIGGER trg_uploaded_files_updated_at
    BEFORE UPDATE ON uploaded_files
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ========== 业务表: MCP 配置 (前端 MCP 配置 + Postgres 持久化) ==========
-- is_system=TRUE 为系统预置公用 MCP (用户不可编辑/删除, 可克隆到自己的列表)
CREATE TABLE IF NOT EXISTS mcp_configs (
    id BIGSERIAL PRIMARY KEY,
    agent_id VARCHAR(64),                     -- NULL = 系统公用 (所有 Agent 共享); 非 NULL = Agent 专属
    user_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    server_url TEXT,                           -- stdio 模式不需要 URL, 允许为空
    transport_type VARCHAR(32) NOT NULL DEFAULT 'stdio',
    command VARCHAR(512),
    args JSONB,
    env_vars JSONB,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    is_system BOOLEAN NOT NULL DEFAULT FALSE,
    version INTEGER NOT NULL DEFAULT 1,        -- 版本控制: 重大变更递增
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- 旧表兜底: 移除 server_url 的 NOT NULL 约束 (stdio 模式不需要 URL)
ALTER TABLE IF EXISTS mcp_configs ALTER COLUMN server_url DROP NOT NULL;
-- 旧表兜底: 添加 is_system 列 (IF NOT EXISTS, PostgreSQL 9.6+)
ALTER TABLE IF EXISTS mcp_configs ADD COLUMN IF NOT EXISTS is_system BOOLEAN NOT NULL DEFAULT FALSE;
-- 旧表兜底: 添加 version 列 (IF NOT EXISTS, 用于控制 MCP 配置更新)
-- ON CONFLICT DO UPDATE 仅当新 version > 旧 version 时更新配置字段 (避免覆盖用户克隆后的定制)
ALTER TABLE IF EXISTS mcp_configs ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;
-- 旧表兜底: 放宽 agent_id 约束 (新表 CREATE TABLE 已去掉 NOT NULL)
-- 系统公用 MCP 的 agent_id 为 NULL (所有 Agent 共享), 用户私有 MCP 的 agent_id 非 NULL
ALTER TABLE IF EXISTS mcp_configs ALTER COLUMN agent_id DROP NOT NULL;

-- 索引: 删除被复合索引前缀覆盖的冗余单列索引 (idx_mcp_configs_agent_user)
CREATE INDEX IF NOT EXISTS idx_mcp_configs_enabled ON mcp_configs (agent_id, user_id, enabled);
CREATE INDEX IF NOT EXISTS idx_mcp_configs_system ON mcp_configs (is_system) WHERE is_system = TRUE;

CREATE OR REPLACE TRIGGER trg_mcp_configs_updated_at
    BEFORE UPDATE ON mcp_configs
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ========== 预置系统公用 MCP 服务 ==========
-- 来源: modelcontextprotocol/servers + 国内外流行 MCP 服务
-- 用户可查看但不可编辑/删除, 可克隆到自己的列表后定制
-- 使用 ON CONFLICT (COALESCE(agent_id, ''), user_id, name) 保证幂等 (重复启动不重复插入)

-- 清理历史重复数据 (保留每个 name 的最小 id, 防止唯一索引创建失败)
-- 同时清理系统 MCP 和用户私有 MCP 的重复记录
DELETE FROM mcp_configs
WHERE id NOT IN (
    SELECT MIN(id) FROM mcp_configs
    GROUP BY agent_id, user_id, name
);

-- 旧表兜底: 系统公用 MCP 的 agent_id 改为 NULL (所有 Agent 共享)
-- 历史数据中系统 MCP 的 agent_id = 'agentinsight-researcher', 迁移为 NULL 表示共享
UPDATE mcp_configs SET agent_id = NULL WHERE is_system = TRUE AND agent_id = 'agentinsight-researcher';

-- 删除旧唯一索引 (agent_id, user_id, name), 改为 COALESCE 表达式索引支持 NULL
-- PostgreSQL 中 NULL != NULL, 用 COALESCE(agent_id, '') 将 NULL 视为 '' 保证系统 MCP 行唯一
DROP INDEX IF EXISTS idx_mcp_configs_unique_name;
-- 添加唯一索引 (COALESCE(agent_id, ''), user_id, name) 防止重复插入
-- 同一 agent + 同一 user 下 name 唯一 (系统 MCP agent_id=NULL 与用户私有 MCP 共用此约束)
CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_configs_unique_name
    ON mcp_configs (COALESCE(agent_id, ''), user_id, name);

-- 清理已移除的系统 MCP (从 130 精简至 21, 仅保留包名真实存在的核心+推荐档)
-- E04/E05: 移除 sequential-thinking (npm 包损坏) 和 supabase (npm 包依赖缺陷)
-- 用户私有克隆 (is_system=FALSE) 不受影响
DELETE FROM mcp_configs
WHERE is_system = TRUE
  AND user_id = 'system'
  AND name NOT IN (
    -- ===== 核心保留 11 个 (移除 sequential-thinking) =====
    'fetch', 'filesystem', 'github',
    'notion', 'obsidian', 'confluence', 'elasticsearch',
    'wikipedia', 'hackernews',
    'neo4j', 'deepl',
    -- ===== 推荐 10 个 (移除 supabase) =====
    'git', 'pdf-tools',
    'google-drive', 'youtube', 'twitter',
    'gitlab', 'mongodb', 'clickhouse',
    'chrome-mcp', 'aws-kb-retrieval'
  );

-- 预置系统公用 MCP 服务 (v4=2026-07-16 MCP 修复版, 修正占位符/损坏包/缺失依赖)
-- v3→v4 修复内容 (E01-E08, E14):
--   E04: 移除 sequential-thinking (npm 包 @modelcontextprotocol/sdk 路径变更, ERR_MODULE_NOT_FOUND)
--   E05: 移除 supabase (npm 包 @supabase/mcp-utils 未声明依赖, ERR_MODULE_NOT_FOUND)
--   E02: obsidian enabled=FALSE (args 含占位符 /path/to/vault, 启动失败)
--   E03: git enabled=FALSE (args 含占位符 /path/to/git/repo, 需克隆后配置; Dockerfile 已安装 git)
--   E06: filesystem 改默认路径 /tmp/uploads + enabled=FALSE (args 含占位符 /path/to/allowed/files)
--   E07: twitter enabled=FALSE (env_vars 含占位符 <your-*>)
--   E08: deepl enabled=FALSE (env_vars 含占位符 <your-key>, 鉴权 401)
--   E14: confluence 加 --toolsets=confluence + enabled=FALSE (消除 TOOLSETS 警告)
--   所有含占位符的 MCP 统一 enabled=FALSE, 由前端引导用户克隆后填写真实值再启用
-- v3 修复内容 (保留): 替换 8 个 npm 失效包为 PyPI/社区包 (官方已迁移 npm → PyPI)
--   fetch:        @modelcontextprotocol/server-fetch (npm 404) → mcp-server-fetch (PyPI, uvx)
--   git:          @modelcontextprotocol/server-git (npm 404) → mcp-server-git (PyPI, uvx, 需系统 git)
--   wikipedia:    @phuongcao/mcp-server-wikipedia (npm 404) → mcp-server-wikipedia (PyPI, uvx, 可执行文件名 wikipedia-mcp-server)
--   confluence:   @sooperset/mcp-atlassian (npm 404) → mcp-atlassian (PyPI, uvx)
--   neo4j:        @neo4j/mcp-server (npm 404) → mcp-server-neo4j (PyPI, uvx)
--   chrome-mcp:   @anthropic-ai/chrome-mcp (npm 404) → chrome-devtools-mcp (npm 社区替代)
--   clickhouse:   @clickhouse/mcp-server (npm 404) → clickhouse-mcp-server (npm 社区替代)
--   github:       保留 npx (npm 200, 超时为网络问题)
-- version=4: 触发 ON CONFLICT DO UPDATE 更新已部署的 v1/v2/v3 配置
INSERT INTO mcp_configs (agent_id, user_id, name, server_url, transport_type, command, args, env_vars, enabled, is_system, version, description) VALUES
    -- ===== 核心保留 11 个 (移除 sequential-thinking, 研究场景高价值、无冗余、合规无冲突) =====
    -- 1. Web 抓取与文件操作 (2 个, filesystem 因含占位符禁用)
    (NULL, 'system', 'fetch', NULL, 'stdio', 'uvx',
     '["mcp-server-fetch"]'::jsonb, NULL,
     TRUE, TRUE, 4,'Web 内容抓取与转换, 适合 LLM 使用 (官方 PyPI 实现 mcp-server-fetch, uvx 运行)'),
    (NULL, 'system', 'filesystem', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-filesystem", "/tmp/uploads"]'::jsonb, NULL,
     FALSE, TRUE, 4,'安全文件操作, 默认访问 /tmp/uploads (官方 npm 实现, 需克隆后配置访问路径)'),
    -- 2. 代码与知识库 (5 个, 全部因含占位符禁用)
    (NULL, 'system', 'github', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-github"]'::jsonb,
     '{"GITHUB_PERSONAL_ACCESS_TOKEN": "<your-token>"}'::jsonb,
     FALSE, TRUE, 4,'GitHub API: 仓库管理/文件操作 (核心保留, 需克隆后配置 GITHUB_PERSONAL_ACCESS_TOKEN)'),
    (NULL, 'system', 'notion', NULL, 'stdio', 'npx',
     '["-y", "@notionhq/notion-mcp-server"]'::jsonb,
     '{"OPENAPI_MCP_HEADERS": "{\"Authorization\":\"Bearer <your-token>\",\"Notion-Version\":\"2022-06-28\"}"}'::jsonb,
     FALSE, TRUE, 4,'Notion: 数据库/页面/协作工作空间管理 (核心保留, 需克隆后配置 Notion Integration Token)'),
    (NULL, 'system', 'obsidian', NULL, 'stdio', 'npx',
     '["-y", "mcp-obsidian", "--vault-path=/path/to/vault"]'::jsonb, NULL,
     FALSE, TRUE, 4, 'Obsidian 知识库: Markdown 解析/双向链接/语义搜索 (核心保留, 需克隆后配置 vault 路径)'),
    (NULL, 'system', 'confluence', NULL, 'stdio', 'uvx',
     '["mcp-atlassian", "--toolsets=confluence", "--confluence-url", "<your-confluence-url>", "--confluence-username", "<your-email>", "--confluence-token", "<your-token>"]'::jsonb,
     '{"ATLASSIAN_SITE_NAME": "<your-site>", "ATLASSIAN_USER_EMAIL": "<your-email>", "ATLASSIAN_API_TOKEN": "<your-token>"}'::jsonb,
     FALSE, TRUE, 4, 'Confluence: 维基内容/空间/页面管理 (PyPI 实现 mcp-atlassian, 需克隆后配置 ATLASSIAN_API_TOKEN; E14: 加 --toolsets=confluence 消除 TOOLSETS 警告)'),
    (NULL, 'system', 'elasticsearch', NULL, 'stdio', 'npx',
     '["-y", "@elastic/mcp-server-elasticsearch"]'::jsonb,
     '{"ES_URL": "http://localhost:9200", "ES_API_KEY": "<your-api-key>"}'::jsonb,
     FALSE, TRUE, 4,'Elasticsearch: 全文搜索/日志分析/实时索引 (核心保留, 需克隆后配置 ES_URL 与 ES_API_KEY)'),
    -- 3. 搜索与新闻信源 (2 个, 无需凭据, 保持启用)
    (NULL, 'system', 'wikipedia', NULL, 'stdio', 'uvx',
     '["--from", "mcp-server-wikipedia", "wikipedia-mcp-server"]'::jsonb, NULL,
     TRUE, TRUE, 4,'Wikipedia 维基百科: 多语言百科全书检索 (PyPI 实现 mcp-server-wikipedia, 可执行文件名 wikipedia-mcp-server)'),
    (NULL, 'system', 'hackernews', NULL, 'stdio', 'npx',
     '["-y", "mcp-hacker-news"]'::jsonb, NULL,
     TRUE, TRUE, 4,'Hacker News: YC 科技新闻与讨论区检索 (核心保留, 原生未覆盖)'),
    -- 4. 数据库 (1 个, neo4j 因含占位符禁用)
    (NULL, 'system', 'neo4j', NULL, 'stdio', 'uvx',
     '["mcp-server-neo4j"]'::jsonb,
     '{"NEO4J_URL": "bolt://localhost:7687", "NEO4J_USERNAME": "neo4j", "NEO4J_PASSWORD": "<your-password>"}'::jsonb,
     FALSE, TRUE, 4,'Neo4j: 图数据库查询与图算法 (PyPI 实现 mcp-server-neo4j, 需克隆后配置连接凭据)'),
    -- 5. 翻译 (1 个, deepl 因含占位符禁用)
    (NULL, 'system', 'deepl', NULL, 'stdio', 'npx',
     '["-y", "deepl-mcp-server"]'::jsonb,
     '{"DEEPL_API_KEY": "<your-key>"}'::jsonb,
     FALSE, TRUE, 4,'DeepL: 高质量机器翻译, 支持 30+ 语言 (核心保留, 需克隆后配置 DEEPL_API_KEY)'),
    -- ===== 推荐 10 个 (移除 supabase, 有价值但需用户按需配置 Key 或验证场景) =====
    -- 6. 开发与代码工具 (3 个, git/gitlab 因含占位符禁用)
    (NULL, 'system', 'git', NULL, 'stdio', 'uvx',
     '["mcp-server-git", "--repository", "/path/to/git/repo"]'::jsonb, NULL,
     FALSE, TRUE, 4,'Git 仓库读取/搜索/操作 (PyPI 实现 mcp-server-git, uvx 运行, 需克隆后配置仓库路径; Dockerfile 已安装 git)'),
    (NULL, 'system', 'gitlab', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-gitlab"]'::jsonb,
     '{"GITLAB_PERSONAL_ACCESS_TOKEN": "<your-token>", "GITLAB_API_URL": "https://gitlab.com/api/v4"}'::jsonb,
     FALSE, TRUE, 4,'GitLab: 仓库管理/项目/合并请求 (推荐, 需克隆后配置 GITLAB_PERSONAL_ACCESS_TOKEN)'),
    (NULL, 'system', 'chrome-mcp', NULL, 'stdio', 'npx',
     '["-y", "chrome-devtools-mcp"]'::jsonb, NULL,
     TRUE, TRUE, 4,'Chrome 浏览器控制: 通过 CDP 协议操控本地 Chrome (社区实现 chrome-devtools-mcp)'),
    -- 7. 知识库与协作 (1 个, google-drive 因含占位符禁用)
    (NULL, 'system', 'google-drive', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-gdrive"]'::jsonb,
     '{"GDRIVE_CLIENT_ID": "<your-client-id>", "GDRIVE_CLIENT_SECRET": "<your-client-secret>"}'::jsonb,
     FALSE, TRUE, 4,'Google Drive: 文件访问与搜索 (推荐, 需克隆后配置 OAuth 凭据)'),
    -- 8. 社交媒体与视频 (2 个, youtube/twitter 因含占位符禁用)
    (NULL, 'system', 'youtube', NULL, 'stdio', 'npx',
     '["-y", "@anaisbetts/mcp-youtube"]'::jsonb,
     '{"YOUTUBE_API_KEY": "<your-api-key>"}'::jsonb,
     FALSE, TRUE, 4,'YouTube: 视频管理/字幕提取/数据分析 (推荐, 需克隆后配置 YOUTUBE_API_KEY)'),
    (NULL, 'system', 'twitter', NULL, 'stdio', 'npx',
     '["-y", "@enescinar/twitter-mcp"]'::jsonb,
     '{"TWITTER_API_KEY": "<your-api-key>", "TWITTER_API_SECRET": "<your-secret>", "TWITTER_ACCESS_TOKEN": "<your-token>", "TWITTER_ACCESS_SECRET": "<your-secret>"}'::jsonb,
     FALSE, TRUE, 4,'Twitter/X: 推文发布/搜索/互动管理 (推荐, 需克隆后配置 Twitter API 凭据)'),
    -- 9. 数据库 (2 个, 移除 supabase; mongodb 无占位符保持启用; clickhouse 因含占位符禁用)
    (NULL, 'system', 'mongodb', NULL, 'stdio', 'npx',
     '["-y", "mongodb-mcp-server", "mongodb://localhost:27017/mydb"]'::jsonb, NULL,
     TRUE, TRUE, 4,'MongoDB: NoSQL 数据库交互与查询 (推荐, 需配置连接字符串)'),
    (NULL, 'system', 'clickhouse', NULL, 'stdio', 'npx',
     '["-y", "clickhouse-mcp-server"]'::jsonb,
     '{"CLICKHOUSE_HOST": "localhost", "CLICKHOUSE_PORT": "8123", "CLICKHOUSE_USER": "default", "CLICKHOUSE_PASSWORD": "<your-password>"}'::jsonb,
     FALSE, TRUE, 4,'ClickHouse: 列式数据库, 实时分析 (社区实现 clickhouse-mcp-server, 需克隆后配置连接凭据)'),
    -- 10. AWS (1 个, aws-kb-retrieval 因含占位符禁用)
    (NULL, 'system', 'aws-kb-retrieval', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-aws-kb-retrieval"]'::jsonb,
     '{"AWS_REGION": "<your-region>", "AWS_ACCESS_KEY_ID": "<your-key>", "AWS_SECRET_ACCESS_KEY": "<your-secret>"}'::jsonb,
     FALSE, TRUE, 4,'AWS Knowledge Base 检索: 使用 Bedrock Agent Runtime (推荐, 官方归档实现, 需克隆后配置 AWS 凭据)'),
    -- 11. 文档工具 (1 个, 无需凭据, 保持启用)
    (NULL, 'system', 'pdf-tools', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-pdf"]'::jsonb, NULL,
     TRUE, TRUE, 4,'PDF 工具: 合并/拆分/水印/元数据编辑 (推荐, 无需 API Key)')
-- v4 版本更新策略: ON CONFLICT DO UPDATE 仅当新 version > 旧 version 时更新配置字段
-- 避免每次启动都 UPDATE (旧 v1 DO NOTHING 无法更新已部署配置)
-- 避免覆盖用户克隆后的定制 (仅系统 MCP is_system=TRUE 且 version 落后时更新)
-- 注意: 用户私有克隆 (is_system=FALSE) 不受此 UPSERT 影响 (user_id 不同)
ON CONFLICT (COALESCE(agent_id, ''), user_id, name) DO UPDATE SET
    server_url = EXCLUDED.server_url,
    transport_type = EXCLUDED.transport_type,
    command = EXCLUDED.command,
    args = EXCLUDED.args,
    env_vars = EXCLUDED.env_vars,
    enabled = EXCLUDED.enabled,
    description = EXCLUDED.description,
    version = EXCLUDED.version,
    updated_at = NOW()
WHERE mcp_configs.version < EXCLUDED.version
  AND mcp_configs.is_system = TRUE;

-- 旧表兜底: research_reports 字段长度统一 VARCHAR(64) (与其他表一致)
-- 新表 CREATE TABLE 已是 VARCHAR(64), 旧表通过 ALTER 迁移
ALTER TABLE IF EXISTS research_reports ALTER COLUMN session_id TYPE VARCHAR(64);
ALTER TABLE IF EXISTS research_reports ALTER COLUMN user_id TYPE VARCHAR(64);
ALTER TABLE IF EXISTS research_reports ALTER COLUMN agent_id TYPE VARCHAR(64);

-- ========== 业务表: 对话消息存储 (以 UserId 为单位的会话持久化) ==========
-- 存储用户与 Agent 的完整对话内容 (user / assistant 消息)
-- 按 session_id + agent_id + user_id 三级隔离, 支持会话列表与消息分页加载
CREATE TABLE IF NOT EXISTS chat_messages (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,             -- 即 thread_id
    agent_id VARCHAR(64) NOT NULL,               -- agent_name, 全局唯一隔离键
    user_id VARCHAR(64) NOT NULL,                -- 用户隔离
    role VARCHAR(16) NOT NULL,                   -- user | assistant
    content TEXT NOT NULL,                       -- 消息内容
    message_metadata JSONB,                      -- 可选元数据 (如 sources, tool_calls)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- 复合索引: 按 session + agent + user 隔离, 按时间倒序分页查询
CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages(session_id, agent_id, user_id, created_at DESC);
-- 复合索引: 按 agent + user 查询消息统计
CREATE INDEX IF NOT EXISTS idx_chat_messages_agent_user
    ON chat_messages(agent_id, user_id, created_at DESC);

-- ========== research_sessions 表适配会话持久化需求 ==========
-- 旧表兜底: 新增 title 字段 (新表 CREATE TABLE 已含)
ALTER TABLE IF EXISTS research_sessions
    ADD COLUMN IF NOT EXISTS title VARCHAR(256);
-- 旧表兜底: 放宽 query 约束 (新表 CREATE TABLE 已去掉 NOT NULL)
ALTER TABLE IF EXISTS research_sessions ALTER COLUMN query DROP NOT NULL;
-- 旧表兜底: 放宽 report_type 约束 (新表 CREATE TABLE 已去掉 NOT NULL)
ALTER TABLE IF EXISTS research_sessions ALTER COLUMN report_type DROP NOT NULL;
-- 旧表兜底: 放宽 report_format 约束 (新表 CREATE TABLE 已去掉 NOT NULL)
ALTER TABLE IF EXISTS research_sessions ALTER COLUMN report_format DROP NOT NULL;
-- 旧表兜底: 新增 language 字段 (新表 CREATE TABLE 已含)
ALTER TABLE IF EXISTS research_sessions ADD COLUMN IF NOT EXISTS language VARCHAR(8) DEFAULT 'zh';
-- 旧表兜底: 新增 client_ip 字段 (新表 CREATE TABLE 已含, 审计追溯用)
ALTER TABLE IF EXISTS research_sessions ADD COLUMN IF NOT EXISTS client_ip VARCHAR(64);

-- 清理 research_sessions 历史重复数据 (保留每个 session 三元组的最小 id, 防止唯一索引创建失败)
-- 与 mcp_configs 的清理模式对齐 (P0 修复: 原脚本缺少此前置清理)
DELETE FROM research_sessions
WHERE id NOT IN (
    SELECT MIN(id) FROM research_sessions
    GROUP BY session_id, agent_id, user_id
);

-- 唯一索引 (session_id, agent_id, user_id): 支持 ON CONFLICT 幂等插入 (ensure_session 方法)
CREATE UNIQUE INDEX IF NOT EXISTS idx_research_sessions_unique_session
    ON research_sessions(session_id, agent_id, user_id);

-- ========== 业务表: 每日报告生成限额 (从环境变量迁移到数据库) ==========
-- 语义说明 (agent_id + user_id 三级默认):
--   (agent_id IS NULL, user_id IS NULL) → 全局默认 (所有 Agent 的兜底)
--   (agent_id = 'xxx',  user_id IS NULL) → Agent 级默认
--   (agent_id = 'xxx',  user_id = 'yyy') → 用户级专属
-- 用户有限额时用用户的, 没有则用 Agent 级, 再没有用全局 (非取较高者)
CREATE TABLE IF NOT EXISTS report_limits (
    id BIGSERIAL PRIMARY KEY,
    agent_id VARCHAR(64),                     -- NULL = 全局默认; 非 NULL = Agent 级/用户级
    user_id VARCHAR(64),                      -- NULL = (agent_id 级) 默认; 非 NULL = 用户专属
    daily_limit INTEGER NOT NULL DEFAULT 5,    -- 每日报告生成限额 (0 = 不限制)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- 旧表兜底: 新增 agent_id 列 (新表 CREATE TABLE 已含)
ALTER TABLE IF EXISTS report_limits ADD COLUMN IF NOT EXISTS agent_id VARCHAR(64);
-- 旧表兜底: 移除 user_id 列级 UNIQUE 约束 (改为复合唯一索引)
-- 列级 UNIQUE 约束名默认为 <table>_<column>_key
ALTER TABLE IF EXISTS report_limits DROP CONSTRAINT IF EXISTS report_limits_user_id_key;
-- 删除旧唯一索引 (user_id 单列), 改为 (agent_id, user_id) 复合唯一索引
DROP INDEX IF EXISTS idx_report_limits_user;
-- 唯一索引: (agent_id, user_id) 支持 ON CONFLICT 幂等 upsert
-- COALESCE 处理 NULL: PostgreSQL 中 NULL != NULL, 用 COALESCE 将 NULL 视为 '' 保证全局默认行唯一
CREATE UNIQUE INDEX IF NOT EXISTS idx_report_limits_agent_user
    ON report_limits (COALESCE(agent_id, ''), COALESCE(user_id, ''));
-- 触发器: 自动维护 updated_at
CREATE OR REPLACE TRIGGER trg_report_limits_updated_at
    BEFORE UPDATE ON report_limits
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
-- 预置全局默认限额 (agent_id IS NULL, user_id IS NULL, daily_limit = 5)
-- 清理历史重复记录 (旧 ON CONFLICT bug 导致每次启动新增一行, 保留 id 最小的一条)
-- 同时清理所有 (agent_id, user_id) 组合的重复行 (不仅限于全局默认)
DELETE FROM report_limits
WHERE id NOT IN (
    SELECT MIN(id) FROM report_limits
    GROUP BY COALESCE(agent_id, ''), COALESCE(user_id, '')
);
-- 用 WHERE NOT EXISTS 保证幂等: 已存在则不插入 (管理员可手动调整后不被重置)
-- 不用 ON CONFLICT (COALESCE(...)): PostgreSQL 推断用原始 NULL 值比较, NULL != NULL 导致冲突检测失败
INSERT INTO report_limits (agent_id, user_id, daily_limit)
SELECT NULL, NULL, 5
WHERE NOT EXISTS (
    SELECT 1 FROM report_limits
    WHERE agent_id IS NULL AND user_id IS NULL
);

-- ========== 业务表: 每日报告生成使用次数 (从 Redis 迁移到数据库) ==========
-- 按 agent_id + UserId + 日期 记录当日报告生成次数
CREATE TABLE IF NOT EXISTS daily_report_usage (
    id BIGSERIAL PRIMARY KEY,
    agent_id VARCHAR(64) NOT NULL,             -- agent_name, 全局唯一隔离键
    user_id VARCHAR(64) NOT NULL,              -- 用户 ID (IP-based 或 JWT 解析)
    usage_date DATE NOT NULL,                  -- 使用日期 (北京时间 YYYY-MM-DD)
    daily_count INTEGER NOT NULL DEFAULT 0,    -- 当日已生成次数
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- 旧表兜底: 新增 agent_id 列 (新表 CREATE TABLE 已含 NOT NULL)
ALTER TABLE IF EXISTS daily_report_usage ADD COLUMN IF NOT EXISTS agent_id VARCHAR(64);
-- 旧表兜底: 回填历史数据的 agent_id (默认归属当前 Agent)
UPDATE daily_report_usage SET agent_id = 'agentinsight-researcher' WHERE agent_id IS NULL;
-- 旧表兜底: 加固 agent_id NOT NULL 约束 (新表 CREATE TABLE 已含)
ALTER TABLE IF EXISTS daily_report_usage ALTER COLUMN agent_id SET NOT NULL;
-- 删除旧唯一索引 (user_id, usage_date), 改为 (agent_id, user_id, usage_date) 复合唯一索引
DROP INDEX IF EXISTS idx_daily_report_usage_user_date;
-- 唯一约束: (agent_id, user_id, usage_date) 支持 ON CONFLICT 幂等 upsert
CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_report_usage_agent_user_date
    ON daily_report_usage(agent_id, user_id, usage_date);
-- 复合索引: (agent_id, user_id) 支持按用户统计用量
CREATE INDEX IF NOT EXISTS idx_daily_report_usage_agent_user
    ON daily_report_usage(agent_id, user_id);
-- 触发器: 自动维护 updated_at
CREATE OR REPLACE TRIGGER trg_daily_report_usage_updated_at
    BEFORE UPDATE ON daily_report_usage
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
