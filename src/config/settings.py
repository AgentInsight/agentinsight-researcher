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
    # 三级 LLM 分层 (对标 GPTR FAST/SMART/STRATEGIC):
    # - FAST: 快速任务 (摘要/分类/JSON 解析)
    # - SMART: 复杂推理 (报告写作/章节生成/来源策展)
    # - STRATEGIC: 规划 (子主题拆解/agent 角色)
    #
    # V2-P0 推荐方案 H (DeepSeek 全栈 + 智谱免费层, 已应用为默认值):
    #   fast_llm = "zhipuai/glm-4-flash"         # 智谱免费层, 极致成本 (8 个调用点)
    #   smart_llm = "deepseek/deepseek-v4-flash"  # DeepSeek 轻量 (14 个调用点, 核心生成层)
    #   strategic_llm = "deepseek/deepseek-v4-pro"  # DeepSeek 思考模式 (4 个调用点)
    #
    # 单次研究报告成本 ~0.18 元, 真正 3 层分离.
    # ⚠️ 旧模型名 deepseek-chat / deepseek-reasoner 将于 2026-07-24 停用, 已迁移到 v4 命名.
    # ⚠️ 智谱 LiteLLM 路由前缀为 zhipuai/ (非 zhipu/).
    #
    # 备选方案 B (质量优先, 中文写作国产第一):
    #   fast_llm = "zhipuai/glm-4-flash"
    #   smart_llm = "dashscope/qwen-max"        # 中文写作最强 (单次研究 ~0.80 元)
    #   strategic_llm = "deepseek/deepseek-v4-pro"
    # 启用方案 B 需配置 DASHSCOPE_API_KEY.
    fast_llm: str = "zhipuai/glm-4-flash"
    smart_llm: str = "deepseek/deepseek-v4-flash"
    strategic_llm: str = "deepseek/deepseek-v4-pro"
    fast_token_limit: int = 3000
    smart_token_limit: int = 6000
    strategic_token_limit: int = 4000
    summary_token_limit: int = 700
    max_total_tokens: int = 128000  # 单次研究流程总 token 预算上限 (P1-02)
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
    # V2-P0: 智谱 AI OpenAI 兼容端点 (LiteLLM 1.90.2 不原生支持 zhipuai/ 前缀,
    # 通过 openai/ 前缀 + api_base 接入智谱 GLM 系列)
    zhipu_api_base: str = "https://open.bigmodel.cn/api/paas/v4"
    # V2-P0: DashScope (阿里通义 Qwen) API Key, 备选方案 B smart_llm=qwen-max 时启用
    dashscope_api_key: str | None = None

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

    # Qdrant HNSW 索引参数调优 (P0-03)
    qdrant_hnsw_m: int = 32  # HNSW 图连接数 (默认 16, 中文建议 32)
    qdrant_hnsw_ef_construct: int = 200  # 构建时搜索宽度 (默认 100, 建议 200)
    qdrant_hnsw_full_scan_threshold: int = 10000  # 全扫描阈值
    qdrant_quantization: str = "scalar"  # 量化方式 (scalar/int8/binary, 默认 scalar)

    # ========== Embeddings (AGENTS.md 第 1/7 章, 远程 TEI) ==========
    embeddings_base_url: str = "http://embeddings:8088"
    embeddings_model: str = "BAAI/bge-large-zh-v1.5"
    embeddings_dimension: int = 1024
    embeddings_api_key: str | None = None  # TEI API_KEY 鉴权 (AGENTS.md 第 7/12 章)
    embeddings_max_client_batch_size: int = 32  # 客户端单次 TEI 请求上限 (P1-1, 超过则分批并发)

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
    # Redis 缓存 LRU 淘汰 (P1-03)
    redis_cache_max_size: int = 1000  # LRU 最大缓存条目数 (超过时淘汰最久未访问)
    redis_cache_lru_enabled: bool = True  # 是否启用 LRU 淘汰 (默认 True, 关闭则仅 TTL)

    # ========== 可观测性 (AGENTS.md 第 10 章) ==========
    agentinsight_public_key: str | None = None
    agentinsight_secret_key: str | None = None
    agentinsight_host: str = "https://agentinsight.goldebridge.com"
    tracing_embedding_sample_rate: float = 0.5

    # ========== 用户身份解析 (AGENTS.md 第 8 章) ==========
    default_user_id: str = "anonymous"
    user_info_api_url: str = "https://agentinsight.goldebridge.com/api/user"
    user_info_api_timeout: int = 5

    # ========== 自托管模式 (SELF_HOST) ==========
    # True (默认): 自托管模式, JWT Token 可选, 不存在时走匿名用户路径 (现有逻辑),
    #   且跳过 AgentInsightService 点数校验/扣除 (独立部署不依赖 SaaS 后端)
    # False: 云托管模式, 强制校验 JWT Token, 不存在或取不到 User 信息时直接返回错误,
    #   且复用 AgentInsightService 的点数校验/扣除 API (见下方 agent_privilege_*)
    self_host: bool = True

    # ========== Agent 点数校验/扣除 (SELF_HOST=False 时启用, 对标 AgentInsightService) ==========
    # 仅在 self_host=False 时生效; self_host=True 时跳过校验/扣除
    # 对标 D:\Projects\Entrepreneurship\AIProjects\AgentInsightService\Agents\common\api_client.py
    agent_privilege_api_base_url: str = "https://agentinsight.goldebridge.com"
    agent_privilege_validate_path: str = "/api/user/privilege/agent/validate"
    agent_privilege_deduct_path: str = "/api/user/privilege/agent/deduct"
    agent_privilege_api_timeout: int = 5
    agent_privilege_fail_open: bool = True  # API 失败时放行 (降级策略)

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
    # 新增 5 个搜索引擎 (P2-Future-04, 对标 GPTR retrievers/)
    exa_api_key: str | None = None  # Exa 搜索 (全球, Bearer token)
    searchapi_api_key: str | None = None  # SearchAPI.io (全球, query param)
    searx_url: str = "http://localhost:8080"  # SearXNG 自托管实例 URL (无需 Key)
    openalex_email: str = ""  # OpenAlex polite pool 邮箱 (可选, 无需 Key)
    max_search_results_per_query: int = 5

    # ========== 抓取 (GPT Researcher 模式) ==========
    max_scraper_workers: int = 15
    scraper_rate_limit_delay: float = 0.0
    browse_chunk_max_length: int = 8192
    scraper: str = "bs"
    # Firecrawl (P1-Future-08)
    firecrawl_api_key: str | None = None
    firecrawl_api_url: str = "https://api.firecrawl.dev"
    # nodriver (P1-Future-08)
    nodriver_enabled: bool = False  # 默认关闭, 需要时手动启用

    # ========== Token 优化 (GPT Researcher 模式) ==========
    similarity_threshold: float = 0.35
    compression_threshold: int = 8000
    context_sliding_window: int = 5  # 滑动窗口大小: 保留最近 N 条原文 (V4-P1-04)
    max_context_words: int = 25_000
    max_iterations: int = 3  # Planner 拆解子查询数量 (非图迭代上限)
    graph_max_iterations: int = 10  # 图迭代硬上限 (AGENTS.md 第 5 章, P0-05 守卫用)
    max_subtopics: int = 3
    deep_research_breadth: int = 3
    deep_research_depth: int = 2
    deep_research_concurrency: int = 4
    deep_research_adaptive: bool = False  # 自适应深度开关 (V4-P2-02, 默认关闭)
    curate_sources: bool = False

    # ========== V2 对齐 GPTR 优化 (V2-P0/P1) ==========
    # WrittenContentCompressor 跨子主题去重阈值 (对标 GPTR WrittenContentCompressor threshold=0.5)
    # 旧版硬编码 0.5, V2 走 settings 配置 (V2-P1).
    written_content_similarity_threshold: float = 0.5
    # EmbeddingsFilter 分块参数 (对标 GPTR RecursiveCharacterTextSplitter chunk_size=1000)
    embeddings_filter_chunk_size: int = 1000
    embeddings_filter_chunk_overlap: int = 100
    # EmbeddingsFilter 返回 Top-K (对标 GPTR EmbeddingsFilter k=20)
    embeddings_filter_top_k: int = 20
    # detailed_report 章节字数下限/上限 (对标 GPTR 800-1200, V2-P1)
    # 旧版 500-1000, V2 提升到 800-1200 对齐 GPTR.
    detailed_section_word_min: int = 800
    detailed_section_word_max: int = 1200
    # detailed_report 引言/结论字数 (对标 GPTR 300-500, 保持)
    detailed_intro_word_min: int = 300
    detailed_intro_word_max: int = 500

    # ========== 评审与事实核查 (P0-Future-01/02) ==========
    max_revisions: int = 3  # Reviewer→Reviser 修订循环上限 (P0-Future-01 守卫)
    fact_check_enabled: bool = True  # FactChecker 事实核查开关 (P0-Future-02)

    # ========== 短查询保护 (P0-Future-05/06, 用户可配置回复语) ==========
    short_query_enabled: bool = True  # 短查询保护开关
    short_query_min_length: int = 2  # 最小有效查询长度(字符)
    short_query_reply: str = "您好！我是研究助手，请提供您想研究的主题，我将为您生成详细的研究报告。"  # 短查询回复语(用户可配置)
    # 语义匹配相似度阈值 (Embeddings + Qdrant 检测短查询, top-1 score > 阈值 → SHORT_QUERY)
    short_query_similarity_threshold: float = 0.85

    # ========== 闲聊/离题保护 (P1-Future-07, 对标 Rasa FallbackClassifier / Dify 失效回复 / NeMo topic rail) ==========
    # 非研究/分析类输入 (闲聊/问候/身份询问/娱乐/常识/私人问题) 统一导向固定回复, 零 LLM 成本.
    # 三层分类器 (规则→Embeddings 语义→LLM) 命中 OFF_TOPIC 即返回 off_topic_reply, 不走任何 graph.
    off_topic_enabled: bool = True  # 闲聊/离题保护开关
    off_topic_reply: str = (
        "您好！我是研究助手，专注于深度研究和分析。"
        "请提供您想研究的主题（例如'分析新能源汽车市场'），"
        "我将为您生成详细的研究报告。"
    )  # 离题回复语 (用户可配置)
    # 离题语义匹配阈值 (略低于短查询阈值, 因为闲聊句子更长, 语义距离更大)
    off_topic_similarity_threshold: float = 0.75
    # LLM 分类失败时的兜底意图 (业界标准: 走最轻路径, 避免误导向高成本研究流程)
    llm_classify_fallback: Literal["research", "off_topic"] = "off_topic"
    # CHAT 意图首轮保护: 无已有报告时降级 OFF_TOPIC (避免首轮闲聊消耗 SMART LLM)
    chat_requires_report: bool = True

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

    # WebSocket 安全加固 (V4-P0-03)
    # 防止 CSWSH (跨站 WebSocket 劫持) 攻击:
    #   - ws_auth_required: JWT token 鉴权开关 (prod 环境强制开启)
    #   - ws_origin_check: Origin 白名单校验开关 (prod 环境强制开启)
    # dev 环境可关闭但仍记录警告日志
    ws_auth_required: bool = True  # WebSocket JWT 鉴权开关
    ws_origin_check: bool = True  # WebSocket Origin 校验开关

    # ========== MCP (用户需求 9) ==========
    mcp_strategy: Literal["fast", "deep", "disabled"] = "fast"
    mcp_auto_tool_selection: bool = True
    mcp_max_tools: int = 3
    mcp_cache_ttl: int = 300  # MCP 工具调用结果缓存 TTL (秒, V4-P1-01)
    mcp_cache_enabled: bool = True  # MCP 缓存开关

    # ========== 报告输出 (用户需求 6) ==========
    default_report_format: Literal["markdown", "html", "pdf", "docx", "json"] = "markdown"
    default_report_type: Literal[
        "basic_report", "detailed_report", "deep_research", "summary", "subtopics"
    ] = "basic_report"
    total_words: int = 1200
    # P1-02: 引用格式风格 (APA/MLA/Chicago/GB7714), 默认 APA.
    # 在 report_generator._format_sources 中读取, 代码层实现真实格式化 (优于 GPTR 仅 LLM 生成).
    report_format_style: Literal["APA", "MLA", "Chicago", "GB7714"] = "APA"
    # V4-P2-01: 报告风格预设, 支持 academic/business/casual/news 4 种风格
    report_style: Literal["academic", "business", "casual", "news"] = "academic"
    # P2-05: 报告 YAML frontmatter 开关 (默认 False, 启用后在报告首部追加元信息块).
    # frontmatter 含 title/date/query/word_count/sources_count 字段, 便于下游解析.
    enable_frontmatter: bool = False

    # ========== 学术检索路由 (P1-03) ==========
    # 学术关键词命中时路由到 arxiv/pubmed/semantic_scholar/openalex 优先检索.
    # 关键词列表外提到配置, 避免硬编码在 detect_region 函数内.
    academic_route_enabled: bool = True
    academic_keywords: list[str] = [
        # 英文学术关键词
        "paper",
        "research",
        "arxiv",
        "pubmed",
        "scholar",
        "doi",
        "abstract",
        "citation",
        "journal",
        "conference",
        "thesis",
        "literature",
        "semanticscholar",
        "preprint",
        "peer-review",
        # 中文学术关键词
        "论文",
        "学术",
        "文献",
        "期刊",
        "会议",
        "学位论文",
        "引用",
        "摘要",
        "综述",
        "研究论文",
        "科研",
    ]

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

    # ========== AG2 框架 (P2-Future-06, 可选) ==========
    ag2_enabled: bool = False  # AG2 框架开关 (默认关闭, 启用后可用 AG2 替代 LangGraph)

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
        - 生产关闭 Debug
        """
        if self.env != "prod":
            return
        errors: list[str] = []
        if not self.agentinsight_public_key or not self.agentinsight_secret_key:
            errors.append("生产环境必须配置 AGENTINSIGHT_PUBLIC_KEY 和 AGENTINSIGHT_SECRET_KEY")
        if not self.postgres_password:
            errors.append("生产环境必须配置 POSTGRES_PASSWORD")
        if errors:
            raise ValueError("生产环境配置校验失败:\n" + "\n".join(f"  - {e}" for e in errors))


@lru_cache
def get_settings() -> Settings:
    """获取全局 Settings 单例."""
    settings = Settings()
    settings.validate_production()
    return settings
