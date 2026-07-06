-- agentinsight-researcher 数据库初始化
-- 严格遵循 AGENTS.md 第 7 章: 单库 agents, 业务表含 agent_id+user_id 双列复合索引
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
    agent_role VARCHAR(256),                   -- LLM 动态生成的角色 persona (对标 GPTR agent_role)
    agent_role_server VARCHAR(64),             -- 角色简称 (对标 GPTR server, 如 financial_analyst)
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

-- ========== 业务表: 研究报告存储 (P1-Future-09) ==========
-- 对标 GPTR backend/server/report_store.py
-- report_id UUID 主键, 支持 save/get/list/delete 四类操作
CREATE TABLE IF NOT EXISTS research_reports (
    report_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id VARCHAR(256) NOT NULL,
    user_id VARCHAR(256) NOT NULL,
    agent_id VARCHAR(256) NOT NULL,
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
-- 迁移: 已有 research_reports 表补充 P1-Future-09 新增列 (PostgreSQL 9.6+)
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

-- 6. 新增 MCP 配置表 (任务7: 前端 MCP 配置 + Postgres 持久化)
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
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- 兼容已有数据库: 移除 server_url 的 NOT NULL 约束 (stdio 模式不需要 URL)
ALTER TABLE mcp_configs ALTER COLUMN server_url DROP NOT NULL;
-- 兼容已有数据库: 添加 is_system 列 (IF NOT EXISTS, PostgreSQL 9.6+)
ALTER TABLE mcp_configs ADD COLUMN IF NOT EXISTS is_system BOOLEAN NOT NULL DEFAULT FALSE;

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

INSERT INTO mcp_configs (agent_id, user_id, name, server_url, transport_type, command, args, env_vars, enabled, is_system, description) VALUES
    -- ===== 核心保留 12 个 (研究场景高价值、无冗余、合规无冲突) =====
    -- 1. Web 抓取与文件操作 (3 个)
    ('agentinsight-researcher', 'system', 'fetch', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-fetch"]'::jsonb, NULL,
     TRUE, TRUE, 'Web 内容抓取与转换, 适合 LLM 使用 (官方参考实现, 核心保留)'),
    ('agentinsight-researcher', 'system', 'filesystem', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/files"]'::jsonb, NULL,
     TRUE, TRUE, '安全文件操作, 可配置访问路径 (官方参考实现, 核心保留)'),
    ('agentinsight-researcher', 'system', 'sequential-thinking', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-sequential-thinking"]'::jsonb, NULL,
     TRUE, TRUE, '通过思维序列进行动态反思式问题求解 (官方参考实现, 核心保留)'),
    -- 2. 代码与知识库 (5 个)
    ('agentinsight-researcher', 'system', 'github', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-github"]'::jsonb,
     '{"GITHUB_PERSONAL_ACCESS_TOKEN": "<your-token>"}'::jsonb,
     TRUE, TRUE, 'GitHub API: 仓库管理/文件操作 (核心保留, 需配置 GITHUB_PERSONAL_ACCESS_TOKEN)'),
    ('agentinsight-researcher', 'system', 'notion', NULL, 'stdio', 'npx',
     '["-y", "@notionhq/notion-mcp-server"]'::jsonb,
     '{"OPENAPI_MCP_HEADERS": "{\"Authorization\":\"Bearer <your-token>\",\"Notion-Version\":\"2022-06-28\"}"}'::jsonb,
     TRUE, TRUE, 'Notion: 数据库/页面/协作工作空间管理 (核心保留, 需配置 Notion Integration Token)'),
    ('agentinsight-researcher', 'system', 'obsidian', NULL, 'stdio', 'npx',
     '["-y", "mcp-obsidian", "--vault-path", "/path/to/vault"]'::jsonb, NULL,
     TRUE, TRUE, 'Obsidian 知识库: Markdown 解析/双向链接/语义搜索 (核心保留, 需配置 vault 路径)'),
    ('agentinsight-researcher', 'system', 'confluence', NULL, 'stdio', 'npx',
     '["-y", "@sooperset/mcp-atlassian", "--confluence"]'::jsonb,
     '{"ATLASSIAN_SITE_NAME": "<your-site>", "ATLASSIAN_USER_EMAIL": "<your-email>", "ATLASSIAN_API_TOKEN": "<your-token>"}'::jsonb,
     TRUE, TRUE, 'Confluence: 维基内容/空间/页面管理 (核心保留, 需配置 ATLASSIAN_API_TOKEN)'),
    ('agentinsight-researcher', 'system', 'elasticsearch', NULL, 'stdio', 'npx',
     '["-y", "@elastic/mcp-server-elasticsearch"]'::jsonb,
     '{"ES_URL": "http://localhost:9200", "ES_API_KEY": "<your-api-key>"}'::jsonb,
     TRUE, TRUE, 'Elasticsearch: 全文搜索/日志分析/实时索引 (核心保留, 需配置 ES_URL 与 ES_API_KEY)'),
    -- 3. 搜索与新闻信源 (2 个)
    ('agentinsight-researcher', 'system', 'wikipedia', NULL, 'stdio', 'npx',
     '["-y", "@phuongcao/mcp-server-wikipedia"]'::jsonb, NULL,
     TRUE, TRUE, 'Wikipedia 维基百科: 多语言百科全书检索 (核心保留, 原生未覆盖)'),
    ('agentinsight-researcher', 'system', 'hackernews', NULL, 'stdio', 'npx',
     '["-y", "mcp-hacker-news"]'::jsonb, NULL,
     TRUE, TRUE, 'Hacker News: YC 科技新闻与讨论区检索 (核心保留, 原生未覆盖)'),
    -- 4. 数据库 (1 个)
    ('agentinsight-researcher', 'system', 'neo4j', NULL, 'stdio', 'npx',
     '["-y", "@neo4j/mcp-server"]'::jsonb,
     '{"NEO4J_URL": "bolt://localhost:7687", "NEO4J_USERNAME": "neo4j", "NEO4J_PASSWORD": "<your-password>"}'::jsonb,
     TRUE, TRUE, 'Neo4j: 图数据库查询与图算法 (核心保留, 需配置连接凭据)'),
    -- 5. 翻译 (1 个)
    ('agentinsight-researcher', 'system', 'deepl', NULL, 'stdio', 'npx',
     '["-y", "deepl-mcp-server"]'::jsonb,
     '{"DEEPL_API_KEY": "<your-key>"}'::jsonb,
     TRUE, TRUE, 'DeepL: 高质量机器翻译, 支持 30+ 语言 (核心保留, 需配置 DEEPL_API_KEY)'),
    -- ===== 推荐 11 个 (有价值但需用户按需配置 Key 或验证场景) =====
    -- 6. 开发与代码工具 (3 个)
    ('agentinsight-researcher', 'system', 'git', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-git", "--repository", "/path/to/git/repo"]'::jsonb, NULL,
     TRUE, TRUE, 'Git 仓库读取/搜索/操作 (推荐, npx 实现, 统一 Node.js 运行时)'),
    ('agentinsight-researcher', 'system', 'gitlab', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-gitlab"]'::jsonb,
     '{"GITLAB_PERSONAL_ACCESS_TOKEN": "<your-token>", "GITLAB_API_URL": "https://gitlab.com/api/v4"}'::jsonb,
     TRUE, TRUE, 'GitLab: 仓库管理/项目/合并请求 (推荐, 需配置 GITLAB_PERSONAL_ACCESS_TOKEN)'),
    ('agentinsight-researcher', 'system', 'chrome-mcp', NULL, 'stdio', 'npx',
     '["-y", "@anthropic-ai/chrome-mcp"]'::jsonb, NULL,
     TRUE, TRUE, 'Chrome 浏览器控制: 通过 CDP 协议操控本地 Chrome (推荐, 实验性)'),
    -- 7. 知识库与协作 (1 个)
    ('agentinsight-researcher', 'system', 'google-drive', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-gdrive"]'::jsonb,
     '{"GDRIVE_CLIENT_ID": "<your-client-id>", "GDRIVE_CLIENT_SECRET": "<your-client-secret>"}'::jsonb,
     TRUE, TRUE, 'Google Drive: 文件访问与搜索 (推荐, 需配置 OAuth 凭据)'),
    -- 8. 社交媒体与视频 (2 个)
    ('agentinsight-researcher', 'system', 'youtube', NULL, 'stdio', 'npx',
     '["-y", "@anaisbetts/mcp-youtube"]'::jsonb,
     '{"YOUTUBE_API_KEY": "<your-api-key>"}'::jsonb,
     TRUE, TRUE, 'YouTube: 视频管理/字幕提取/数据分析 (推荐, 需配置 YOUTUBE_API_KEY)'),
    ('agentinsight-researcher', 'system', 'twitter', NULL, 'stdio', 'npx',
     '["-y", "@enescinar/twitter-mcp"]'::jsonb,
     '{"TWITTER_API_KEY": "<your-api-key>", "TWITTER_API_SECRET": "<your-secret>", "TWITTER_ACCESS_TOKEN": "<your-token>", "TWITTER_ACCESS_SECRET": "<your-secret>"}'::jsonb,
     TRUE, TRUE, 'Twitter/X: 推文发布/搜索/互动管理 (推荐, 需配置 Twitter API 凭据)'),
    -- 9. 数据库 (3 个)
    ('agentinsight-researcher', 'system', 'mongodb', NULL, 'stdio', 'npx',
     '["-y", "mongodb-mcp-server", "mongodb://localhost:27017/mydb"]'::jsonb, NULL,
     TRUE, TRUE, 'MongoDB: NoSQL 数据库交互与查询 (推荐, 需配置连接字符串)'),
    ('agentinsight-researcher', 'system', 'supabase', NULL, 'stdio', 'npx',
     '["-y", "@supabase/mcp-server-supabase"]'::jsonb,
     '{"SUPABASE_URL": "<your-url>", "SUPABASE_KEY": "<your-key>"}'::jsonb,
     TRUE, TRUE, 'Supabase: Postgres + Auth + Storage 一体化后端 (推荐, 需配置 SUPABASE_URL)'),
    ('agentinsight-researcher', 'system', 'clickhouse', NULL, 'stdio', 'npx',
     '["-y", "@clickhouse/mcp-server"]'::jsonb,
     '{"CLICKHOUSE_HOST": "localhost", "CLICKHOUSE_PORT": "8123", "CLICKHOUSE_USER": "default", "CLICKHOUSE_PASSWORD": "<your-password>"}'::jsonb,
     TRUE, TRUE, 'ClickHouse: 列式数据库, 实时分析 (推荐, 需配置连接凭据)'),
    -- 10. AWS (1 个)
    ('agentinsight-researcher', 'system', 'aws-kb-retrieval', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-aws-kb-retrieval"]'::jsonb,
     '{"AWS_REGION": "<your-region>", "AWS_ACCESS_KEY_ID": "<your-key>", "AWS_SECRET_ACCESS_KEY": "<your-secret>"}'::jsonb,
     TRUE, TRUE, 'AWS Knowledge Base 检索: 使用 Bedrock Agent Runtime (推荐, 官方归档实现)'),
    -- 11. 文档工具 (1 个)
    ('agentinsight-researcher', 'system', 'pdf-tools', NULL, 'stdio', 'npx',
     '["-y", "@modelcontextprotocol/server-pdf"]'::jsonb, NULL,
     TRUE, TRUE, 'PDF 工具: 合并/拆分/水印/元数据编辑 (推荐, 无需 API Key)')
ON CONFLICT (agent_id, user_id, name) DO UPDATE SET
    server_url = EXCLUDED.server_url,
    transport_type = EXCLUDED.transport_type,
    command = EXCLUDED.command,
    args = EXCLUDED.args,
    env_vars = EXCLUDED.env_vars,
    description = EXCLUDED.description,
    updated_at = NOW();

-- 7. 字段长度统一: agent_id/user_id/session_id 统一 VARCHAR(64)
-- (research_reports 当前是 VARCHAR(256), 留待后续数据迁移, 此处仅记录)
