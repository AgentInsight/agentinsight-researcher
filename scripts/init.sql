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
CREATE TABLE IF NOT EXISTS mcp_configs (
    id BIGSERIAL PRIMARY KEY,
    agent_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    server_url TEXT NOT NULL,
    transport_type VARCHAR(32) NOT NULL DEFAULT 'stdio',
    command VARCHAR(512),
    args JSONB,
    env_vars JSONB,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mcp_configs_agent_user ON mcp_configs (agent_id, user_id);
CREATE INDEX IF NOT EXISTS idx_mcp_configs_enabled ON mcp_configs (agent_id, user_id, enabled);

CREATE OR REPLACE TRIGGER trg_mcp_configs_updated_at
    BEFORE UPDATE ON mcp_configs
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- 7. 字段长度统一: agent_id/user_id/session_id 统一 VARCHAR(64)
-- (research_reports 当前是 VARCHAR(256), 留待后续数据迁移, 此处仅记录)
