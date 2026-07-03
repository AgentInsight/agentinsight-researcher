"""全局 Settings SSOT.

AGENTS.md 第 1/3 章: pydantic-settings Settings SSOT, 配置只经 config/ + 环境变量.
业务代码禁止硬编码 URL/密钥.
对标 AgentInsightService common/config.py 模式.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置 SSOT (Single Source of Truth).

    从 .env 加载, 生产环境用 .env.agent + .env.{env} 分层.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ========== 环境与部署 (AGENTS.md 第 12 章) ==========
    env: Literal["dev", "prod"] = "dev"
    enable_test_page: bool = True
    log_level: str = "INFO"

    # ========== LLM 网关 (AGENTS.md 第 9 章, LiteLLM) ==========
    fast_llm: str = "deepseek/deepseek-chat"
    smart_llm: str = "deepseek/deepseek-chat"
    strategic_llm: str = "deepseek/deepseek-reasoner"
    fast_token_limit: int = 3000
    smart_token_limit: int = 6000
    strategic_token_limit: int = 4000
    summary_token_limit: int = 700
    temperature: float = 0.4
    llm_timeout: int = 60
    llm_max_retries: int = 2
    litellm_proxy_base_url: str | None = None
    litellm_proxy_api_key: str | None = None

    # 模型 API Key (按需启用)
    deepseek_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    zhipu_api_key: str | None = None

    # ========== 图像生成 (P2-06 报告配图, deepseek-v4-flash) ==========
    # 用户明确要求: 用 deepseek-v4-flash (非 gemini). 通过 LiteLLM aimage_generation 调用.
    # 注意: deepseek-v4-flash 图像生成能力假设支持, 实际以官方文档为准.
    image_generation_enabled: bool = False  # 默认关闭, 启用后报告生成配图
    image_model: str = "deepseek/deepseek-v4-flash"  # LiteLLM 路由前缀 (复用 deepseek_api_key)
    image_size: str = "1024x1024"
    image_quality: str = "standard"
    image_api_key: str | None = None  # 单独 Key (可选, 留空则复用 deepseek_api_key)

    # ========== Qdrant (AGENTS.md 第 7/12 章) ==========
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "agents"
    qdrant_vector_size: int = 1024
    qdrant_distance: str = "Cosine"

    # ========== Embeddings (AGENTS.md 第 1/7 章, 远程 TEI) ==========
    embeddings_base_url: str = "http://embeddings:8088"
    embeddings_model: str = "BAAI/bge-large-zh-v1.5"
    embeddings_dimension: int = 1024
    embeddings_api_key: str | None = None  # TEI API_KEY 鉴权 (AGENTS.md 第 7/12 章)

    # ========== Rerank (AGENTS.md 第 7 章) ==========
    rerank_enabled: bool = False  # 默认不启用, rerank_enabled=True 时启用 bge-reranker-v2-m3
    rerank_base_url: str = "http://rerank:8089"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_top_k: int = 5
    rerank_api_key: str | None = None  # TEI API_KEY 鉴权 (AGENTS.md 第 7/12 章)

    # ========== PostgreSQL (AGENTS.md 第 6/12 章) ==========
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "agents"
    postgres_user: str = "agentinsight"
    postgres_password: str | None = None
    postgres_connection_pool_size: int = 10

    @property
    def postgres_dsn(self) -> str:
        """PostgreSQL 连接串 (asyncpg 格式)."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_dsn_psycopg(self) -> str:
        """PostgreSQL 连接串 (psycopg 格式, 用于 Checkpointer)."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ========== Redis (AGENTS.md 第 1/6 章) ==========
    redis_url: str = "redis://redis:6379/0"
    redis_auth: str | None = None  # Redis 鉴权密码 (与 docker-compose REDIS_AUTH 对齐)

    # ========== 可观测性 (AGENTS.md 第 10 章) ==========
    agentinsight_public_key: str | None = None
    agentinsight_secret_key: str | None = None
    agentinsight_host: str = "https://agentinsight.goldebridge.com"
    tracing_embedding_sample_rate: float = 0.5

    # ========== 用户身份解析 (AGENTS.md 第 8 章) ==========
    default_user_id: str = "anonymous"
    user_info_api_url: str = "https://agentinsight.goldebridge.com/api/user"
    user_info_api_timeout: int = 5

    # ========== 会话与上下文 (AGENTS.md 第 6 章) ==========
    context_max_chars: int = 800_000
    context_compressed_target: int = 200_000
    context_session_ttl: int = 2_592_000
    debounce_seconds: float = 1.0
    flush_interval_seconds: float = 0.5

    # ========== 数据隔离 (AGENTS.md 第 7 章) ==========
    agent_name: str = "agentinsight-researcher"

    # ========== RAG 检索 (AGENTS.md 第 7 章) ==========
    vector_weight: float = 0.7
    bm25_weight: float = 0.3
    score_threshold: float = 0.3
    rrf_k: int = 60
    bm25_k1: float = 1.5
    bm25_b: float = 0.75

    # ========== 搜索引擎 (用户需求 5, 中文优先) ==========
    bocha_api_key: str | None = None
    tavily_api_key: str | None = None
    # 新增 6 个搜索引擎 (P0-03 + P2-02)
    brave_api_key: str | None = None  # Brave Search (全球)
    bing_api_key: str | None = None  # Bing Web Search API (全球)
    serpapi_key: str | None = None  # SerpApi Google 代理 (全球)
    serper_api_key: str | None = None  # Serper.dev Google Search (全球)
    pubmed_email: str = ""  # PubMed NCBI 建议邮箱 (无需 Key)
    semantic_scholar_api_key: str | None = None  # Semantic Scholar Graph API (可选 Key)
    max_search_results_per_query: int = 5

    # ========== 抓取 (GPT Researcher 模式) ==========
    max_scraper_workers: int = 15
    scraper_rate_limit_delay: float = 0.0
    browse_chunk_max_length: int = 8192
    scraper: str = "bs"

    # ========== Token 优化 (GPT Researcher 模式) ==========
    similarity_threshold: float = 0.35
    compression_threshold: int = 8000
    max_context_words: int = 25_000
    max_iterations: int = 3  # Planner 拆解子查询数量 (非图迭代上限)
    graph_max_iterations: int = 10  # 图迭代硬上限 (AGENTS.md 第 5 章, P0-05 守卫用)
    max_subtopics: int = 3
    deep_research_breadth: int = 3
    deep_research_depth: int = 2
    deep_research_concurrency: int = 4
    curate_sources: bool = False

    # ========== 评审与事实核查 (P0-Future-01/02) ==========
    max_revisions: int = 3  # Reviewer→Reviser 修订循环上限 (P0-Future-01 守卫)
    fact_check_enabled: bool = True  # FactChecker 事实核查开关 (P0-Future-02)

    # ========== 人在回路 (P0-Future-03 Human-in-the-loop) ==========
    # human_review_enabled=True 时, 多 Agent 图在 agent_creator 之后、supervisor 之前
    # 插入 human 节点: agent_creator → human → (accept → supervisor | revise → agent_creator)
    # HumanAgent 通过 WebSocket 推送计划给前端, 阻塞等待用户反馈 (asyncio.Future, 带超时).
    human_review_enabled: bool = False  # 默认关闭, 启用后需前端 WebSocket 配合
    human_review_timeout: int = 300  # 等待用户反馈超时 (秒), 超时自动通过
    max_plan_revisions: int = 3  # 研究计划修订上限, 达上限强制通过 (守卫防死循环)

    # ========== WebSocket 双向实时通信 (P2-Future-02) ==========
    # SSE (/v1/chat/completions stream=true) 仍是主通道, WebSocket 是增强通道:
    #   1. 推送人在回路审核请求 (human_feedback_request) 给前端
    #   2. 接收用户反馈 (human_feedback) 提交到 feedback_queue
    #   3. 可选: 节点进度结构化推送 (node_progress)
    websocket_enabled: bool = True

    # ========== MCP (用户需求 9) ==========
    mcp_strategy: Literal["fast", "deep", "disabled"] = "fast"
    mcp_auto_tool_selection: bool = True
    mcp_max_tools: int = 3

    # ========== 报告输出 (用户需求 6) ==========
    default_report_format: Literal["markdown", "html", "pdf", "docx", "json"] = "markdown"
    default_report_type: Literal[
        "basic_report", "detailed_report", "deep_research", "summary", "subtopics"
    ] = "basic_report"
    total_words: int = 1200
    report_format_style: str = "APA"

    # ========== PromptFamily 策略模式 (P1-Future-04) ==========
    # 选择 prompt 策略: "default" 中文优先 | "english" 英文
    # 由 src/skills/researcher/prompts.py 的 get_prompt_family() 路由.
    prompt_family: Literal["default", "english"] = "default"

    # ========== 行业适配 GPTR 4 层机制 (对标 GPT Researcher) ==========
    # 对标 GPTR AGENT_ROLE 配置 (config/variables/default.py:23):
    # 用户可注入行业 persona 字符串, 优先级高于 LLM 动态生成 (agent_creator.py).
    # 行业适配采用 GPTR 风格 4 层机制, 不再使用行业分类器:
    #   1. Prompt 层: AgentCreator.AUTO_AGENT_INSTRUCTIONS few-shot → LLM 动态生成角色
    #   2. Config 层: 本字段 (agent_role) 静态注入角色 persona
    #   3. Retriever 层: searchers/ 含 arxiv/pubmed/semantic_scholar 等专业数据源
    #   4. MCP 层: MCP_SERVERS 注册行业专用工具服务器 (mcp_coordinator.py)
    agent_role: str | None = None

    # ========== 文件上传 (用户需求 8) ==========
    upload_dir: str = "/tmp/uploads"
    max_upload_size_mb: int = 50
    allowed_upload_extensions: str = "pdf,docx,md,txt,html,csv,xlsx,pptx"

    # ========== CORS (AGENTS.md 第 11 章, 禁 *) ==========
    cors_allow_origins: str = "http://localhost:3000,http://localhost:8066"

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS 允许的源列表."""
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def allowed_extensions_list(self) -> list[str]:
        """允许上传的扩展名列表."""
        return [e.strip().lower() for e in self.allowed_upload_extensions.split(",") if e.strip()]

    @field_validator("env")
    @classmethod
    def _validate_env(cls, v: str) -> str:
        """环境值校验."""
        if v not in ("dev", "prod"):
            raise ValueError(f"ENV 必须为 dev 或 prod, 实际: {v}")
        return v

    def validate_production(self) -> None:
        """生产环境强制校验 (AGENTS.md 第 10/11 章).

        - 密钥必须存在
        - CORS 禁 *
        - 生产关闭 Debug
        """
        if self.env != "prod":
            return
        errors: list[str] = []
        if not self.agentinsight_public_key or not self.agentinsight_secret_key:
            errors.append("生产环境必须配置 AGENTINSIGHT_PUBLIC_KEY 和 AGENTINSIGHT_SECRET_KEY")
        if not self.postgres_password:
            errors.append("生产环境必须配置 POSTGRES_PASSWORD")
        if "*" in self.cors_allow_origins:
            errors.append("生产环境 CORS 禁止 * (AGENTS.md 第 11 章)")
        if errors:
            raise ValueError("生产环境配置校验失败:\n" + "\n".join(f"  - {e}" for e in errors))


@lru_cache
def get_settings() -> Settings:
    """获取全局 Settings 单例."""
    settings = Settings()
    settings.validate_production()
    return settings
