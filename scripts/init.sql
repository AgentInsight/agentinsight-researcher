-- agentinsight-researcher 数据库初始化
-- 单库 agents, 业务表含 agent_id+user_id 双列复合索引
-- LangGraph PostgresSaver 表由官方 SDK 管理 (thread_id 已含会话隔离)

-- 启用扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ========== LangGraph Checkpointer 表 ==========
-- 由 langgraph-checkpoint-postgres SDK 自动管理, 此处仅占位说明
-- 实际表名: checkpoints / writes / migrations (由 SDK 创建)

-- ========== 业务表: 研究会话 ==========
CREATE TABLE IF NOT EXISTS research_sessions (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,           -- 即 thread_id
    agent_id VARCHAR(64) NOT NULL,             -- agent_name, 全局唯一隔离键
    user_id VARCHAR(64) NOT NULL,              -- 用户隔离
    query TEXT NOT NULL,                       -- 原始研究请求
    report_type VARCHAR(32) NOT NULL DEFAULT 'basic_report',
    report_format VARCHAR(16) NOT NULL DEFAULT 'markdown',
    agent_role VARCHAR(256),                   -- LLM 动态生成的角色 persona
    agent_role_server VARCHAR(64),             -- 角色简称 (如 financial_analyst)
    status VARCHAR(32) NOT NULL DEFAULT 'pending',  -- pending/running/completed/failed
    total_cost_usd NUMERIC(12,6) DEFAULT 0,
    total_tokens BIGINT DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '30 days')
);
CREATE INDEX IF NOT EXISTS idx_research_sessions_agent_user ON research_sessions(agent_id, user_id);
CREATE INDEX IF NOT EXISTS idx_research_sessions_session ON research_sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_research_sessions_expires ON research_sessions(expires_at);

-- ========== 业务表: 研究报告存储 ==========
-- report_id UUID 主键, 支持 save/get/list/delete 四类操作
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
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_research_reports_session ON research_reports(session_id);
CREATE INDEX IF NOT EXISTS idx_research_reports_user ON research_reports(user_id);
CREATE INDEX IF NOT EXISTS idx_research_reports_created ON research_reports(created_at DESC);
-- 复合索引 (agent_id + user_id), 多 Agent 数据隔离约定
CREATE INDEX IF NOT EXISTS idx_research_reports_agent_user ON research_reports(agent_id, user_id);
-- session + agent + user 三列复合索引, 加速按会话检索报告
CREATE INDEX IF NOT EXISTS idx_research_reports_session_agent_user ON research_reports(session_id, agent_id, user_id);
-- 迁移: 已有 research_reports 表补充新增列 (PostgreSQL 9.6+)
ALTER TABLE IF EXISTS research_reports ADD COLUMN IF NOT EXISTS report_id UUID DEFAULT gen_random_uuid();
ALTER TABLE IF EXISTS research_reports ADD COLUMN IF NOT EXISTS query TEXT;
ALTER TABLE IF EXISTS research_reports ADD COLUMN IF NOT EXISTS report_format VARCHAR(32) DEFAULT 'markdown';
ALTER TABLE IF EXISTS research_reports ADD COLUMN IF NOT EXISTS agent_role VARCHAR(256);
ALTER TABLE IF EXISTS research_reports ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
-- 兜底唯一索引: 旧表无 report_id PK 时保证唯一 (新表已有 PK, IF NOT EXISTS 跳过)
CREATE UNIQUE INDEX IF NOT EXISTS idx_research_reports_report_id ON research_reports(report_id);

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
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
-- 1. 回填 research_reports 历史 NULL 值
UPDATE research_reports SET created_at = NOW() WHERE created_at IS NULL;
UPDATE research_reports SET updated_at = NOW() WHERE updated_at IS NULL;

-- 2. 加固 research_reports NOT NULL 约束 (对齐 research_sessions)
ALTER TABLE IF EXISTS research_reports
    ALTER COLUMN created_at SET NOT NULL,
    ALTER COLUMN created_at SET DEFAULT NOW(),
    ALTER COLUMN updated_at SET NOT NULL,
    ALTER COLUMN updated_at SET DEFAULT NOW();

-- 3. 为 uploaded_files 补 updated_at 字段 (status 会变更, 需要追踪)
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

-- 5. 为状态会变更的 3 张表挂触发器 (PostgreSQL 14+ 支持 CREATE OR REPLACE TRIGGER)
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

-- 6. 新增 MCP 配置表 (前端 MCP 配置 + Postgres 持久化)
-- is_system=TRUE 为系统预置公用 MCP (用户不可编辑/删除, 可克隆到自己的列表)
CREATE TABLE IF NOT EXISTS mcp_configs (
    id BIGSERIAL PRIMARY KEY,
    agent_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    server_url TEXT,
    transport_type VARCHAR(32) NOT NULL DEFAULT 'stdio',
    command VARCHAR(512),
    args JSONB,
    env_vars JSONB,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    is_system BOOLEAN NOT NULL DEFAULT FALSE,
    version INTEGER NOT NULL DEFAULT 1,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- 兼容已有数据库: 移除 server_url 的 NOT NULL 约束 (stdio 模式不需要 URL)
ALTER TABLE mcp_configs ALTER COLUMN server_url DROP NOT NULL;
-- 兼容已有数据库: 添加 is_system 列 (IF NOT EXISTS, PostgreSQL 9.6+)
ALTER TABLE mcp_configs ADD COLUMN IF NOT EXISTS is_system BOOLEAN NOT NULL DEFAULT FALSE;
-- 兼容已有数据库: 添加 version 列 (IF NOT EXISTS, 用于控制 MCP 配置更新)
-- 版本号规则: 重大变更递增主版本 (如 v2=2026-07-06 MCP 修复), 小修复递增次版本
-- ON CONFLICT DO UPDATE 仅当新 version > 旧 version 时更新配置字段 (避免覆盖用户克隆后的定制)
ALTER TABLE mcp_configs ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS idx_mcp_configs_agent_user ON mcp_configs (agent_id, user_id);
CREATE INDEX IF NOT EXISTS idx_mcp_configs_enabled ON mcp_configs (agent_id, user_id, enabled);
CREATE INDEX IF NOT EXISTS idx_mcp_configs_system ON mcp_configs (is_system) WHERE is_system = TRUE;

CREATE OR REPLACE TRIGGER trg_mcp_configs_updated_at
    BEFORE UPDATE ON mcp_configs
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- 6.1 预置系统公用 MCP 服务 (is_system=TRUE, user_id='system')
-- 来源: https://github.com/modelcontextprotocol/servers 官方参考实现 + 国内外流行 MCP 服务
-- 用户可查看但不可编辑/删除, 可克隆到自己的列表后定制
-- 使用 ON CONFLICT (agent_id, user_id, name) 保证幂等 (重复启动不重复插入)

-- 6.1.1 清理历史重复数据 (保留每个 name 的最小 id, 防止唯一索引创建失败)
-- 同时清理系统 MCP 和用户私有 MCP 的重复记录
DELETE FROM mcp_configs
WHERE id NOT IN (
    SELECT MIN(id) FROM mcp_configs
    GROUP BY agent_id, user_id, name
);

-- 6.1.2 添加唯一索引 (agent_id, user_id, name) 防止重复插入
-- 同一 agent + 同一 user 下 name 唯一 (系统 MCP 与用户私有 MCP 共用此约束)
CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_configs_unique_name
    ON mcp_configs (agent_id, user_id, name);

-- 6.2 预置系统公用 MCP 服务 (is_system=TRUE, user_id='system')
-- 来源: https://github.com/modelcontextprotocol/servers 官方参考实现 + 国内外流行 MCP 服务
-- 用户可查看但不可编辑/删除, 可克隆到自己的列表后定制
-- 使用 ON CONFLICT (agent_id, user_id, name) 保证幂等 (重复启动不重复插入)
--
-- 精简治理 (2026-07-05): 经 12 角色 AI 专家团队多轮分析, 从原 130 个精简至 23 个
--   - 核心保留 12 个: 研究场景高价值、与项目无冗余、合规无冲突、npm 包真实存在
--   - 推荐保留 11 个: 有价值但需用户按需配置 Key 或验证场景、npm 包真实存在
--   - 已移除 107 个: 冗余/合规冲突/安全风险高/非研究场景/包名不存在于 npm registry
-- 详见 docs/system-mcp-analysis.md

-- 6.2.1 清理已移除的系统 MCP (从 130 精简至 23, 仅保留包名真实存在的核心+推荐档)
-- 用户私有克隆 (is_system=FALSE) 不受影响
DELETE FROM mcp_configs
WHERE is_system = TRUE
  AND user_id = 'system'
  AND name NOT IN (
    -- ===== 核心保留 12 个 =====
    'fetch', 'filesystem', 'sequential-thinking', 'github',
    'notion', 'obsidian', 'confluence', 'elasticsearch',
    'wikipedia', 'hackernews',
    'neo4j', 'deepl',
    -- ===== 推荐 11 个 =====
    'git', 'pdf-tools',
    'google-drive', 'youtube', 'twitter',
    'gitlab', 'supabase', 'mongodb', 'clickhouse',
    'chrome-mcp', 'aws-kb-retrieval'
  );

-- 6.3 预置系统公用 MCP 服务 (v3=2026-07-06 MCP 修复版, 修正 wikipedia 可执行文件名)
-- 修复内容: 替换 8 个 npm 失效包为 PyPI/社区包 (官方已迁移 npm → PyPI)
--   fetch:        @modelcontextprotocol/server-fetch (npm 404) → mcp-server-fetch (PyPI, uvx)
--   git:          @modelcontextprotocol/server-git (npm 404) → mcp-server-git (PyPI, uvx, 需系统 git)
--   wikipedia:    @phuongcao/mcp-server-wikipedia (npm 404) → mcp-server-wikipedia (PyPI, uvx, 可执行文件名 wikipedia-mcp-server)
--   confluence:   @sooperset/mcp-atlassian (npm 404) → mcp-atlassian (PyPI, uvx)
--   neo4j:        @neo4j/mcp-server (npm 404) → mcp-server-neo4j (PyPI, uvx)
--   chrome-mcp:   @anthropic-ai/chrome-mcp (npm 404) → chrome-devtools-mcp (npm 社区替代)
--   clickhouse:   @clickhouse/mcp-server (npm 404) → clickhouse-mcp-server (npm 社区替代)
--   github:       保留 npx (npm 200, 之前超时为网络问题)
-- version=3: 触发 ON CONFLICT DO UPDATE 更新已部署的 v1/v2 配置 (v2 wikipedia 可执行文件名错误)
INSERT INTO mcp_configs (agent_id, user_id, name, server_url, transport_type, command, args, env_vars, enabled, is_system, version, description) VALUES
    -- ===== 核心保留 12 个 (研究场景高价值、无冗余、合规无冲突) =====
    -- 1. Web 抓取与文件操作 (3 个)
    ('agentinsight-researcher', 'system', 'fetch', NULL, 'stdio', 'uvx',
     '["mcp-server-fetch"]'::jsonb, NULL,
     TRUE, TRUE, 3, 'Web 内容抓取与转换, 适合 LLM 使用 (官方 PyPI 实现 mcp-server-fetch, uvx 运行)'),
    ('agentinsight-researcher', 'system', 'filesystem', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/files"]'::jsonb, NULL,
     TRUE, TRUE, 3, '安全文件操作, 可配置访问路径 (官方 npm 实现, 核心保留)'),
    ('agentinsight-researcher', 'system', 'sequential-thinking', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-sequential-thinking"]'::jsonb, NULL,
     TRUE, TRUE, 3, '通过思维序列进行动态反思式问题求解 (官方 npm 实现, 核心保留)'),
    -- 2. 代码与知识库 (5 个)
    ('agentinsight-researcher', 'system', 'github', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-github"]'::jsonb,
     '{"GITHUB_PERSONAL_ACCESS_TOKEN": "<your-token>"}'::jsonb,
     TRUE, TRUE, 3, 'GitHub API: 仓库管理/文件操作 (核心保留, 需配置 GITHUB_PERSONAL_ACCESS_TOKEN)'),
    ('agentinsight-researcher', 'system', 'notion', NULL, 'stdio', 'npx',
     '["-y", "@notionhq/notion-mcp-server"]'::jsonb,
     '{"OPENAPI_MCP_HEADERS": "{\"Authorization\":\"Bearer <your-token>\",\"Notion-Version\":\"2022-06-28\"}"}'::jsonb,
     TRUE, TRUE, 3, 'Notion: 数据库/页面/协作工作空间管理 (核心保留, 需配置 Notion Integration Token)'),
    ('agentinsight-researcher', 'system', 'obsidian', NULL, 'stdio', 'npx',
     '["-y", "mcp-obsidian", "--vault-path", "/path/to/vault"]'::jsonb, NULL,
     TRUE, TRUE, 3, 'Obsidian 知识库: Markdown 解析/双向链接/语义搜索 (核心保留, 需配置 vault 路径)'),
    ('agentinsight-researcher', 'system', 'confluence', NULL, 'stdio', 'uvx',
     '["mcp-atlassian", "--confluence"]'::jsonb,
     '{"ATLASSIAN_SITE_NAME": "<your-site>", "ATLASSIAN_USER_EMAIL": "<your-email>", "ATLASSIAN_API_TOKEN": "<your-token>"}'::jsonb,
     TRUE, TRUE, 3, 'Confluence: 维基内容/空间/页面管理 (PyPI 实现 mcp-atlassian, 需配置 ATLASSIAN_API_TOKEN)'),
    ('agentinsight-researcher', 'system', 'elasticsearch', NULL, 'stdio', 'npx',
     '["-y", "@elastic/mcp-server-elasticsearch"]'::jsonb,
     '{"ES_URL": "http://localhost:9200", "ES_API_KEY": "<your-api-key>"}'::jsonb,
     TRUE, TRUE, 3, 'Elasticsearch: 全文搜索/日志分析/实时索引 (核心保留, 需配置 ES_URL 与 ES_API_KEY)'),
    -- 3. 搜索与新闻信源 (2 个)
    ('agentinsight-researcher', 'system', 'wikipedia', NULL, 'stdio', 'uvx',
     '["--from", "mcp-server-wikipedia", "wikipedia-mcp-server"]'::jsonb, NULL,
     TRUE, TRUE, 3, 'Wikipedia 维基百科: 多语言百科全书检索 (PyPI 实现 mcp-server-wikipedia, 可执行文件名 wikipedia-mcp-server)'),
    ('agentinsight-researcher', 'system', 'hackernews', NULL, 'stdio', 'npx',
     '["-y", "mcp-hacker-news"]'::jsonb, NULL,
     TRUE, TRUE, 3, 'Hacker News: YC 科技新闻与讨论区检索 (核心保留, 原生未覆盖)'),
    -- 4. 数据库 (1 个)
    ('agentinsight-researcher', 'system', 'neo4j', NULL, 'stdio', 'uvx',
     '["mcp-server-neo4j"]'::jsonb,
     '{"NEO4J_URL": "bolt://localhost:7687", "NEO4J_USERNAME": "neo4j", "NEO4J_PASSWORD": "<your-password>"}'::jsonb,
     TRUE, TRUE, 3, 'Neo4j: 图数据库查询与图算法 (PyPI 实现 mcp-server-neo4j, 需配置连接凭据)'),
    -- 5. 翻译 (1 个)
    ('agentinsight-researcher', 'system', 'deepl', NULL, 'stdio', 'npx',
     '["-y", "deepl-mcp-server"]'::jsonb,
     '{"DEEPL_API_KEY": "<your-key>"}'::jsonb,
     TRUE, TRUE, 3, 'DeepL: 高质量机器翻译, 支持 30+ 语言 (核心保留, 需配置 DEEPL_API_KEY)'),
    -- ===== 推荐 11 个 (有价值但需用户按需配置 Key 或验证场景) =====
    -- 6. 开发与代码工具 (3 个)
    ('agentinsight-researcher', 'system', 'git', NULL, 'stdio', 'uvx',
     '["mcp-server-git", "--repository", "/path/to/git/repo"]'::jsonb, NULL,
     TRUE, TRUE, 3, 'Git 仓库读取/搜索/操作 (PyPI 实现 mcp-server-git, uvx 运行)'),
    ('agentinsight-researcher', 'system', 'gitlab', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-gitlab"]'::jsonb,
     '{"GITLAB_PERSONAL_ACCESS_TOKEN": "<your-token>", "GITLAB_API_URL": "https://gitlab.com/api/v4"}'::jsonb,
     TRUE, TRUE, 3, 'GitLab: 仓库管理/项目/合并请求 (推荐, 需配置 GITLAB_PERSONAL_ACCESS_TOKEN)'),
    ('agentinsight-researcher', 'system', 'chrome-mcp', NULL, 'stdio', 'npx',
     '["-y", "chrome-devtools-mcp"]'::jsonb, NULL,
     TRUE, TRUE, 3, 'Chrome 浏览器控制: 通过 CDP 协议操控本地 Chrome (社区实现 chrome-devtools-mcp)'),
    -- 7. 知识库与协作 (1 个)
    ('agentinsight-researcher', 'system', 'google-drive', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-gdrive"]'::jsonb,
     '{"GDRIVE_CLIENT_ID": "<your-client-id>", "GDRIVE_CLIENT_SECRET": "<your-client-secret>"}'::jsonb,
     TRUE, TRUE, 3, 'Google Drive: 文件访问与搜索 (推荐, 需配置 OAuth 凭据)'),
    -- 8. 社交媒体与视频 (2 个)
    ('agentinsight-researcher', 'system', 'youtube', NULL, 'stdio', 'npx',
     '["-y", "@anaisbetts/mcp-youtube"]'::jsonb,
     '{"YOUTUBE_API_KEY": "<your-api-key>"}'::jsonb,
     TRUE, TRUE, 3, 'YouTube: 视频管理/字幕提取/数据分析 (推荐, 需配置 YOUTUBE_API_KEY)'),
    ('agentinsight-researcher', 'system', 'twitter', NULL, 'stdio', 'npx',
     '["-y", "@enescinar/twitter-mcp"]'::jsonb,
     '{"TWITTER_API_KEY": "<your-api-key>", "TWITTER_API_SECRET": "<your-secret>", "TWITTER_ACCESS_TOKEN": "<your-token>", "TWITTER_ACCESS_SECRET": "<your-secret>"}'::jsonb,
     TRUE, TRUE, 3, 'Twitter/X: 推文发布/搜索/互动管理 (推荐, 需配置 Twitter API 凭据)'),
    -- 9. 数据库 (3 个)
    ('agentinsight-researcher', 'system', 'mongodb', NULL, 'stdio', 'npx',
     '["-y", "mongodb-mcp-server", "mongodb://localhost:27017/mydb"]'::jsonb, NULL,
     TRUE, TRUE, 3, 'MongoDB: NoSQL 数据库交互与查询 (推荐, 需配置连接字符串)'),
    ('agentinsight-researcher', 'system', 'supabase', NULL, 'stdio', 'npx',
     '["-y", "@supabase/mcp-server-supabase"]'::jsonb,
     '{"SUPABASE_URL": "<your-url>", "SUPABASE_KEY": "<your-key>"}'::jsonb,
     TRUE, TRUE, 3, 'Supabase: Postgres + Auth + Storage 一体化后端 (推荐, 需配置 SUPABASE_URL)'),
    ('agentinsight-researcher', 'system', 'clickhouse', NULL, 'stdio', 'npx',
     '["-y", "clickhouse-mcp-server"]'::jsonb,
     '{"CLICKHOUSE_HOST": "localhost", "CLICKHOUSE_PORT": "8123", "CLICKHOUSE_USER": "default", "CLICKHOUSE_PASSWORD": "<your-password>"}'::jsonb,
     TRUE, TRUE, 3, 'ClickHouse: 列式数据库, 实时分析 (社区实现 clickhouse-mcp-server, 需配置连接凭据)'),
    -- 10. AWS (1 个)
    ('agentinsight-researcher', 'system', 'aws-kb-retrieval', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-aws-kb-retrieval"]'::jsonb,
     '{"AWS_REGION": "<your-region>", "AWS_ACCESS_KEY_ID": "<your-key>", "AWS_SECRET_ACCESS_KEY": "<your-secret>"}'::jsonb,
     TRUE, TRUE, 3, 'AWS Knowledge Base 检索: 使用 Bedrock Agent Runtime (推荐, 官方归档实现)'),
    -- 11. 文档工具 (1 个)
    ('agentinsight-researcher', 'system', 'pdf-tools', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-pdf"]'::jsonb, NULL,
     TRUE, TRUE, 3, 'PDF 工具: 合并/拆分/水印/元数据编辑 (推荐, 无需 API Key)')
-- v2 版本更新策略: ON CONFLICT DO UPDATE 仅当新 version > 旧 version 时更新配置字段
-- 避免每次启动都 UPDATE (旧 v1 DO NOTHING 无法更新已部署配置)
-- 避免覆盖用户克隆后的定制 (仅系统 MCP is_system=TRUE 且 version 落后时更新)
-- 注意: 用户私有克隆 (is_system=FALSE) 不受此 UPSERT 影响 (user_id 不同)
ON CONFLICT (agent_id, user_id, name) DO UPDATE SET
    server_url = EXCLUDED.server_url,
    transport_type = EXCLUDED.transport_type,
    command = EXCLUDED.command,
    args = EXCLUDED.args,
    env_vars = EXCLUDED.env_vars,
    description = EXCLUDED.description,
    version = EXCLUDED.version,
    updated_at = NOW()
WHERE mcp_configs.version < EXCLUDED.version
  AND mcp_configs.is_system = TRUE;

-- 7. research_reports 字段长度统一 VARCHAR(64) (与其他表一致)
-- 已有部署的 research_reports 表通过 ALTER 迁移, 新部署由 CREATE TABLE 直接正确
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

-- 8. research_sessions 表适配会话持久化需求
-- 8.1 新增 title 字段 (用于会话列表显示)
ALTER TABLE IF EXISTS research_sessions
    ADD COLUMN IF NOT EXISTS title VARCHAR(256);
-- 8.2 放宽 query 约束: 允许创建空会话 (首次对话前先创建 session 记录)
ALTER TABLE IF EXISTS research_sessions ALTER COLUMN query DROP NOT NULL;
-- 8.3 放宽 report_type 约束: 空会话无报告类型
ALTER TABLE IF EXISTS research_sessions ALTER COLUMN report_type DROP NOT NULL;
-- 8.4 放宽 report_format 约束: 空会话无报告格式
ALTER TABLE IF EXISTS research_sessions ALTER COLUMN report_format DROP NOT NULL;
-- 8.5 为 research_sessions 添加 (agent_id, user_id, updated_at DESC) 复合索引
-- 支持按用户列出会话列表 (按最近更新排序)
CREATE INDEX IF NOT EXISTS idx_research_sessions_agent_user_updated
    ON research_sessions(agent_id, user_id, updated_at DESC);
-- 8.6 为 research_sessions 添加 (session_id, agent_id, user_id) 唯一约束
-- 支持 ON CONFLICT (session_id, agent_id, user_id) 幂等插入 (ensure_session 方法)
CREATE UNIQUE INDEX IF NOT EXISTS idx_research_sessions_unique_session
    ON research_sessions(session_id, agent_id, user_id);
