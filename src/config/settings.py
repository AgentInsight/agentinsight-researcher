"""全局 Settings SSOT.

pydantic-settings Settings SSOT, 配置只经 config/ + 环境变量.
业务代码禁止硬编码 URL/密钥.
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

    # ========== 环境与部署 ==========
    env: Literal["dev", "prod"] = "dev"
    enable_test_page: bool = True
    log_level: str = "INFO"

    # ========== LLM 网关 (LiteLLM) ==========
    # 三级 LLM 分层:
    # - FAST: 快速任务 (摘要/分类/JSON 解析), 默认 zhipuai/glm-4-flash (智谱免费层)
    # - SMART: 复杂推理 (报告写作/章节生成), 默认 deepseek/deepseek-v4-flash
    # - STRATEGIC: 规划 (子主题拆解/agent 角色), 默认 deepseek/deepseek-v4-pro
    # 如需全 DeepSeek 栈: 将 fast_llm 改为 "deepseek/deepseek-v4-flash"
    fast_llm: str = "zhipuai/glm-4-flash"
    smart_llm: str = "deepseek/deepseek-v4-flash"
    strategic_llm: str = "deepseek/deepseek-v4-pro"
    fast_token_limit: int = 3000
    smart_token_limit: int = 6000
    strategic_token_limit: int = 4000
    summary_token_limit: int = 700
    max_total_tokens: int = 128000  # 单次研究流程总 token 预算上限
    temperature: float = 0.4
    llm_timeout: int = 120  # LLM 调用超时 (秒); deepseek-v4-pro 报告生成需 60s+
    llm_max_retries: int = 2
    # LLM 响应缓存 (Redis)
    # 仅缓存 temperature=0 的成功响应; 异常/错误响应绝不缓存; 流式响应不缓存.
    llm_response_cache_enabled: bool = True
    llm_response_cache_ttl: int = 3600  # 缓存 TTL (秒, 默认 1 小时)
    litellm_proxy_base_url: str | None = None
    litellm_proxy_api_key: str | None = None

    # 模型 API Key (按需启用)
    deepseek_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    zhipu_api_key: str | None = None
    # 智谱 AI OpenAI 兼容端点 (LiteLLM 1.90.2 不原生支持 zhipuai/ 前缀,
    # 通过 openai/ 前缀 + api_base 接入智谱 GLM 系列)
    zhipu_api_base: str = "https://open.bigmodel.cn/api/paas/v4"
    # DashScope (阿里通义 Qwen) API Key, smart_llm=qwen-max 时启用
    dashscope_api_key: str | None = None

    # ========== 图像生成 (报告配图, deepseek-v4-flash) ==========
    # 用户明确要求: 用 deepseek-v4-flash (非 gemini). 通过 LiteLLM aimage_generation 调用.
    # 注意: deepseek-v4-flash 图像生成能力假设支持, 实际以官方文档为准.
    image_generation_enabled: bool = True  # 启用报告生成配图
    image_model: str = "deepseek/deepseek-v4-flash"  # LiteLLM 路由前缀 (复用 deepseek_api_key)
    image_size: str = "1024x1024"
    image_quality: str = "standard"
    image_api_key: str | None = None  # 单独 Key (可选, 留空则复用 deepseek_api_key)
    # SVG 矢量配图 (DeepSeek V4 Flash 经 /chat/completions 生成 SVG 代码)
    # 关闭思考模式 (extra_body thinking=disabled), SVG 生成是创意输出任务不需要推理
    image_output_format: str = "svg"  # 图像输出格式: svg | url | b64
    image_svg_max_tokens: int = 16384  # SVG 生成 max_tokens (DeepSeek API 上限远超 8192, 16K 确保复杂 SVG 输出)
    image_svg_temperature: float = 0.7  # SVG 生成温度 (0.7 平衡创意与稳定)

    # ========== Qdrant ==========
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "agents"
    qdrant_vector_size: int = 768  # bge-base-zh-v1.5 固定维度

    # Qdrant HNSW 索引参数调优
    qdrant_hnsw_m: int = 32  # HNSW 图连接数 (默认 16, 中文建议 32)
    qdrant_hnsw_ef_construct: int = 200  # 构建时搜索宽度 (默认 100, 建议 200)
    qdrant_hnsw_full_scan_threshold: int = 10000  # 全扫描阈值
    qdrant_quantization: str = "scalar"  # 量化方式 (scalar/int8/binary, 默认 scalar)

    # ========== Embeddings (远程 TEI) ==========
    embeddings_base_url: str = "http://embeddings:8088"
    embeddings_model: str = "BAAI/bge-base-zh-v1.5"
    embeddings_api_key: str | None = None  # TEI API_KEY 鉴权
    embeddings_max_client_batch_size: int = (
        4  # 客户端单次 TEI 请求上限 (匹配 TEI CPU max_batch_requests=4)
    )
    # 4 texts/请求 → 1 推理批次 → 1 permit; Semaphore(3) × 1 = 3 permits < 4 可用 → 无 429
    embeddings_max_concurrent: int = 3  # 客户端并发限流 (3 HTTP 请求 × 1 permit = 3, 留 1 余量)
    # TEI 服务端并发 (compose 插值用, 与客户端 embeddings_max_concurrent 不同)
    # embeddings_max_concurrent: 客户端 HTTP 请求并发 (业务代码读取)
    # embeddings_tei_max_concurrent: TEI 服务端 MAX_CONCURRENT_REQUESTS (compose 插值, 业务代码不读取)
    embeddings_tei_max_concurrent: int = 16
    # 429 重试配置 (指数退避)
    embeddings_max_retries: int = 3
    embeddings_retry_base_delay: float = 0.5  # 基础延迟 (秒), 实际 = base * 2^attempt

    # ========== Rerank ==========
    rerank_enabled: bool = False  # 默认不启用, rerank_enabled=True 时启用 bge-reranker-v2-m3
    rerank_base_url: str = "http://rerank:8089"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_top_k: int = 5
    rerank_api_key: str | None = None  # TEI API_KEY 鉴权

    # ========== PostgreSQL ==========
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "agents"
    postgres_user: str = "agis"
    postgres_password: str | None = None
    postgres_connection_pool_size: int = 10
    # PostgresSaver AsyncConnectionPool 连接池 min/max 配置化 (按负载调整)
    # langgraph-checkpoint-postgres 1.x AsyncConnectionPool 支持 min_size/max_size 参数;
    # min_size 不超过 max_size (checkpointer 内部会做 clamp 校验).
    postgres_pool_min_size: int = 4
    postgres_pool_max_size: int = 20

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

    # ========== Redis ==========
    redis_url: str = "redis://redis:6379/0"
    redis_auth: str | None = None  # Redis 鉴权密码 (与 docker-compose REDIS_AUTH 对齐)
    # Redis 连接池调优 (socket_timeout/max_connections 从 .env 暴露, 业务代码读取)
    redis_socket_timeout: float = 5.0  # 命令执行超时 (秒), 超时抛 ConnectionError
    redis_max_connections: int = 50  # 连接池上限 (高并发场景调优)
    # Redis 缓存 LRU 淘汰
    redis_cache_max_size: int = 1000  # LRU 最大缓存条目数 (超过时淘汰最久未访问)
    redis_cache_lru_enabled: bool = True  # 是否启用 LRU 淘汰 (默认 True, 关闭则仅 TTL)

    # ========== 可观测性 ==========
    agentinsight_public_key: str | None = None
    agentinsight_secret_key: str | None = None
    agentinsight_host: str = "https://agent.goldebridge.com"
    tracing_embedding_sample_rate: float = 0.5

    # ========== 用户身份解析 ==========
    # 无 JWT Token 时, 按 IP 生成确定性 UserId (SHA256 哈希, 不存储原始 IP)
    user_info_api_url: str = "https://agentinsight.goldebridge.com/api/user"
    user_info_api_timeout: int = 5
    # 每日报告生成限额 (按 IP 限流, 默认 3 次/日, 仅报告成功才计数)
    # 0 = 不限制; 环境变量 IP_DAILY_REPORT_LIMIT 控制
    # 注意: 此配置已迁移到数据库 report_limits 表 (user_id IS NULL 的系统默认行)
    # 此环境变量仅作为数据库不可用时的降级 fallback
    ip_daily_report_limit: int = 3

    # ========== 自托管模式 (SELF_HOST) ==========
    # True (默认): 自托管模式, JWT Token 可选, 不存在时走 IP-based 用户身份解析,
    #   且跳过 AgentInsightService 点数校验/扣除 (独立部署不依赖 SaaS 后端)
    # False: 云托管模式, 强制校验 JWT Token, 不存在或取不到 User 信息时直接返回错误,
    #   且复用 AgentInsightService 的点数校验/扣除 API (见下方 agent_privilege_*)
    self_host: bool = True

    # ========== Agent 点数校验/扣除 (SELF_HOST=False 时启用) ==========
    # 仅在 self_host=False 时生效; self_host=True 时跳过校验/扣除
    # AgentType 枚举: 1=Assistant (MonthlyAgentRate), 2=Research (MonthlyResearchRate)
    # 本项目为研究型 Agent, 默认 type=2, 服务端从 JWT token 解析 OrgId
    agent_privilege_api_base_url: str = "https://agentinsight.goldebridge.com"
    agent_privilege_validate_path: str = "/api/user/privilege/agent/validate"
    agent_privilege_deduct_path: str = "/api/user/privilege/agent/deduct"
    agent_privilege_api_timeout: int = 5
    agent_privilege_fail_open: bool = True  # API 失败时放行 (降级策略)

    # ========== 会话与上下文 ==========
    context_max_chars: int = 800_000
    context_compressed_target: int = 200_000
    context_session_ttl: int = 2_592_000
    debounce_seconds: float = 1.0
    flush_interval_seconds: float = 0.5

    # ========== 数据隔离 ==========
    agent_name: str = "agentinsight-researcher"

    # ========== RAG 检索 ==========
    vector_weight: float = 0.7
    bm25_weight: float = 0.3
    score_threshold: float = 0.3
    rrf_k: int = 60
    bm25_k1: float = 1.5
    bm25_b: float = 0.75

    # ========== L2 BM25 分层过滤 (两层方案, 上下文压缩主路径) ==========
    # 两层路由: <8K Fast Path | >=8K BM25Filter (全量覆盖, 含 >50K 超长上下文)
    # 性能: 258 chunks BM25 本地计算 ~2s (远快于远程 TEI 推理)
    # 零新依赖 (rank-bm25 + jieba 已声明)
    bm25_filter_enabled: bool = True  # 默认启用, 上下文压缩主路径
    bm25_filter_char_threshold: int = (
        8000  # < 此值走 Fast Path 不压缩 (环境变量: BM25_FILTER_CHAR_THRESHOLD)
    )
    bm25_filter_char_upper: int = 50000  # 兼容字段, 保留供向后兼容
    bm25_filter_top_k: int = 20  # 返回 Top-K (与 embeddings_filter_top_k 对齐)
    bm25_filter_top_k_for_rerank: int = (
        50  # BM25 粗筛返回数量 (供 FastEmbed 精排用, 环境变量: BM25_FILTER_TOP_K_FOR_RERANK)
    )
    bm25_filter_score_threshold: float = (
        0.0  # BM25 分数阈值 (0.0=仅过滤零分; BM25 分数无上界, 不可与 cosine 阈值复用)
    )
    bm25_filter_chunk_size: int = 1000  # 分块大小 (与 embeddings_filter_chunk_size 一致)
    bm25_filter_chunk_overlap: int = 100  # 分块 overlap
    bm25_filter_timeout: float = 5.0  # 本地计算超时 (秒, 远快于旧 EmbeddingsFilter 15s)

    # ========== FastEmbed 精排 (两阶段检索第二阶段, 上下文压缩专用) ==========
    # 总 chunk 数 > embeddings_rerank_chunk_threshold 时, 启用 FastEmbed 精排:
    #   BM25 先召回 bm25_filter_top_k_for_rerank 个候选, FastEmbed 从中再选 embeddings_rerank_top_k 个
    # 总 chunk 数 <= embeddings_rerank_chunk_threshold 时, 直接返回 BM25 结果
    # 注: 此精排用 FastEmbed 本地 bge-small-zh-v1.5 (512维), 与私有数据 Qdrant 检索
    #     用的远程 TEI bge-base-zh-v1.5 (768维) 完全隔离, 不依赖远程 TEI 服务
    embeddings_rerank_top_k: int = (
        20  # FastEmbed 精排后返回 Top-K (环境变量: EMBEDDINGS_RERANK_TOP_K)
    )
    embeddings_rerank_chunk_threshold: int = (
        30  # 启用精排的 chunk 数量阈值 (环境变量: EMBEDDINGS_RERANK_CHUNK_THRESHOLD)
    )

    # ========== FastEmbed 本地 Embeddings (上下文压缩专用) ==========
    # 用于 FastEmbed 精排 + WrittenContentCompressor 跨子主题去重, 不依赖远程 TEI
    # 远程 TEI (bge-base-zh-v1.5, 768维) 仅用于私有数据 Qdrant 索引/检索
    # bge-small-zh-v1.5 ONNX INT8 模型, 输出 512 维向量
    fastembed_model_name: str = "BAAI/bge-small-zh-v1.5"
    fastembed_model_path: str = "./models/bge-small-zh-v1.5-onnx"  # ONNX 模型本地路径 (环境变量: FASTEMBED_MODEL_PATH)
    fastembed_dimension: int = 512  # bge-small-zh-v1.5 固定维度
    fastembed_max_length: int = 512  # 最大序列长度
    # ONNX Runtime 并行执行 (推理加速 2-4x)
    # intra_op_num_threads: 单操作内部并行线程数 (矩阵乘法等)
    # inter_op_num_threads: 操作间并行线程数 (独立操作并行)
    fastembed_onnx_intra_threads: int = (
        0  # 0=自动 (cpu_count), 环境变量: FASTEMBED_ONNX_INTRA_THREADS
    )
    fastembed_onnx_inter_threads: int = (
        0  # 0=自动 (cpu_count//2), 环境变量: FASTEMBED_ONNX_INTER_THREADS
    )
    # 搜索结果 Redis 缓存 TTL (相同 query+engine, 秒)
    search_cache_ttl: int = 300  # 5 分钟, 环境变量: SEARCH_CACHE_TTL

    # ========== 搜索引擎 (用户需求 5, 中文优先) ==========
    bocha_api_key: str | None = None
    tavily_api_key: str | None = None
    # 新增 6 个搜索引擎
    brave_api_key: str | None = None  # Brave Search (全球)
    bing_api_key: str | None = None  # Bing Web Search API (全球)
    serpapi_key: str | None = None  # SerpApi Google 代理 (全球)
    serper_api_key: str | None = None  # Serper.dev Google Search (全球)
    pubmed_email: str = ""  # PubMed NCBI 建议邮箱 (无需 Key)
    semantic_scholar_api_key: str | None = None  # Semantic Scholar Graph API (可选 Key)
    # 搜索引擎
    exa_api_key: str | None = None  # Exa 搜索 (全球, Bearer token)
    # 秘塔 AI 搜索 (国内 AI 搜索主力, freemium)
    metaso_api_key: str | None = None  # 秘塔 AI 搜索 API Key (访问 https://metaso.cn/api 获取)
    # GitHub 代码搜索 (可选 Token 提高配额)
    github_token: str | None = (
        None  # GitHub Personal Access Token (https://github.com/settings/tokens)
    )
    # 学术搜索引擎邮箱配置 (polite pool)
    crossref_mailto: str = ""  # CrossRef polite pool 邮箱 (可选, 50 req/s)
    unpaywall_email: str = ""  # Unpaywall 真实邮箱 (必填, 否则 HTTP 422 拒绝)
    searchapi_api_key: str | None = None  # SearchAPI.io (全球, query param)
    searx_url: str = "http://searxng:8099"  # SearXNG 自托管实例 URL (无需 Key, 容器内访问地址)
    openalex_email: str = ""  # OpenAlex polite pool 邮箱 (可选, 无需 Key)
    max_search_results_per_query: int = 5
    # 搜索引擎超时秒数 (SearXNG/DuckDuckGo 等免费引擎专用)
    search_timeout: float = 10.0
    # 自定义搜索引擎 (searchers/custom.py 读取): endpoint + 查询参数名
    custom_retriever_endpoint: str | None = None  # 自定义检索端点 URL, 留空则不启用
    custom_retriever_arg: str = "query"  # 自定义检索端点的查询参数名 (默认 query)

    # ========== 抓取 ==========
    # Agent 并发抓取数据的数量 (控制同时抓取多少个 URL, 影响内存/CPU/带宽)
    # 环境变量 MAX_SCRAPER_WORKERS 注入; deep_research.py / research_conductor.py 读取
    max_scraper_workers: int = 15
    scraper_rate_limit_delay: float = 0.0
    browse_chunk_max_length: int = 8192
    scraper: str = "bs"
    # 方案 E: 抓取模式
    # auto: BS 优先, 内容过短降级 Playwright (默认, 需 chromium)
    # lightweight: 仅 BS, 不安装 chromium (适合离线最小化部署)
    # playwright: 强制 Playwright (调试用)
    scraper_mode: str = "auto"
    # Playwright chromium 路径 (可选, 系统 chromium 时设置)
    playwright_chromium_executable_path: str | None = None
    # Playwright 浏览器目录 (离线模式预下载, 默认 ~/.cache/ms-playwright)
    playwright_browsers_path: str | None = None
    # Firecrawl
    firecrawl_api_key: str | None = None
    firecrawl_api_url: str = "https://api.firecrawl.dev"

    # ========== Token 优化 ==========
    similarity_threshold: float = 0.35
    compression_threshold: int = 8000
    context_sliding_window: int = 5  # 滑动窗口大小: 保留最近 N 条原文
    max_context_words: int = 25_000
    max_iterations: int = 3  # Planner 拆解子查询数量 (非图迭代上限)
    graph_max_iterations: int = 10  # 图迭代硬上限 (守卫用)
    max_subtopics: int = 3
    deep_research_breadth: int = 4  # 4+8=12 子查询
    deep_research_depth: int = 2
    deep_research_concurrency: int = 4
    deep_research_adaptive: bool = True  # 自适应深度开关 (默认开启, 自适应深度机制)
    # 支持 L9-L10 (breadth=5/depth=3, 5+10+20=35)
    deep_research_max_sub_queries: int = 42  # 递归树硬上限守卫 (L9-L10: 5+10+20=35)
    deep_research_num_learnings: int = 3  # 每子查询提取 learnings 数量上限 (功能 6)
    deep_research_reasoning_effort: str = "high"  # 规划/提取阶段推理强度 (功能 10)
    curate_sources: bool = True

    # ========== 上下文压缩与去重参数 ==========
    # WrittenContentCompressor 跨子主题去重阈值 (WrittenContentCompressor threshold=0.5)
    # 去重使用 FastEmbed (bge-small-zh-v1.5, 512维), 阈值可能需重新校准.
    written_content_similarity_threshold: float = 0.5
    # 递归分块参数 (chunk_size=1000 是 RecursiveCharacterTextSplitter 默认值)
    # 参数供 BM25Filter/WrittenContentCompressor 复用 recursive_split.
    embeddings_filter_chunk_size: int = 1000
    embeddings_filter_chunk_overlap: int = 100
    # 精排返回 Top-K (EmbeddingsFilter k=20)
    # 供 ContextManager._embeddings_rerank (FastEmbed 精排) 使用.
    embeddings_filter_top_k: int = 20
    # detailed_report 章节字数下限/上限 (800-1200)
    detailed_section_word_min: int = 800
    detailed_section_word_max: int = 1200
    # detailed_report 引言/结论字数 (300-500, 保持)
    detailed_intro_word_min: int = 300
    detailed_intro_word_max: int = 500

    # ========== 评审与事实核查 ==========
    max_revisions: int = 3  # Reviewer→Reviser 修订循环上限 (守卫)
    fact_check_enabled: bool = True  # FactChecker 事实核查开关

    # ========== 短查询保护 (用户可配置回复语) ==========
    short_query_enabled: bool = True  # 短查询保护开关
    short_query_min_length: int = 2  # 最小有效查询长度(字符)
    short_query_reply: str = "您好！我是研究助手，请提供您想研究的主题，我将为您生成详细的研究报告。"  # 短查询回复语(用户可配置)

    # ========== 闲聊/离题保护 ==========
    # 非研究/分析类输入 (闲聊/问候/身份询问/娱乐/常识/私人问题) 统一导向固定回复, 零 LLM 成本.
    # 两层分类器 (规则→LLM) 命中 OFF_TOPIC 即返回 off_topic_reply, 不走任何 graph.
    off_topic_enabled: bool = True  # 闲聊/离题保护开关
    off_topic_reply: str = (
        "您好！我是研究助手，专注于深度研究和分析。"
        "请提供您想研究的主题（例如'分析新能源汽车市场'），"
        "我将为您生成详细的研究报告。"
    )  # 离题回复语 (用户可配置)
    # LLM 分类失败时的兜底意图 (走最轻路径, 避免误导向高成本研究流程)
    llm_classify_fallback: Literal["research", "off_topic"] = "off_topic"
    # CHAT 意图首轮保护: 无已有报告时降级 OFF_TOPIC (避免首轮闲聊消耗 SMART LLM)
    chat_requires_report: bool = True

    # ========== 闲聊响应优化 ==========
    # 闲聊响应器: FAST_LLM 实时生成 + Persona + 三段式 + 多模板兜底
    # 四段式 system prompt + persona 一致性
    chitchat_config_dir: str = "src/config/researcher"  # 闲聊配置目录 (相对项目根)
    chitchat_temperature: float = 0.7  # 闲聊温度 (略高创意)
    chitchat_max_tokens: int = 1000  # 闲聊响应 max_tokens (推理模型需 ≥1000: 推理 500+回复 500)
    chitchat_stream_char_by_char: bool = True  # 流式是否逐字 yield
    chitchat_fallback_to_template: bool = True  # FAST 失败降级多模板

    # ChatAgent 配置
    chat_temperature: float = 0.4
    chat_max_tokens: int = 4000
    chat_history_limit: int = 10  # 历史消息上限
    chat_report_truncate_chars: int = 50_000  # 注入 prompt 的报告截断长度
    chat_simple_query_threshold_chars: int = 20  # 简单追问长度阈值 (cascade 路由)
    chat_complex_keywords: tuple[str, ...] = (
        "对比",
        "分析",
        "为什么",
        "展开",
        "评估",
        "论证",
    )  # 复杂追问关键词 (命中则走 SMART)

    # QueryClassifier 阈值
    query_classify_llm_max_tokens: int = 500  # LLM 分类 max_tokens (推理模型需 ≥500: 推理 400+分类 100)
    query_classify_llm_query_truncate: int = 1000  # LLM 分类 query 截断长度
    query_classify_single_word_max_chars: int = 6  # 单单词长度上限
    query_classify_trace_input_truncate: int = 200  # trace span input 截断长度
    # 分类结果 Redis 缓存
    # 启用后高频重复 query 直接命中缓存, 零 LLM 调用; Redis 不可用时降级为不缓存.
    query_classify_cache_enabled: bool = True  # 默认启用
    query_classify_cache_ttl: int = 86400  # 缓存 TTL (秒), 默认 24h

    # FAST LLM 场景化 max_tokens
    fast_classify_max_tokens: int = 200  # 复杂度评估/MCP 工具选择
    fast_summarize_max_tokens: int = 2000  # 上下文压缩/Mermaid
    fast_json_max_tokens: int = 64  # 意图分类 JSON
    fast_summary_mode_max_tokens: int = 1000  # 摘要模式

    # ========== 人在回路 (Human-in-the-loop) ==========
    # human_review_enabled=True 时, 多 Agent 图在 agent_creator 之后、supervisor 之前
    # 插入 human 节点: agent_creator → human → (accept → supervisor | revise → agent_creator)
    # HumanAgent 通过 WebSocket 推送计划给前端, 阻塞等待用户反馈 (asyncio.Future, 带超时).
    human_review_enabled: bool = False  # 默认关闭, 启用后需前端 WebSocket 配合
    human_review_timeout: int = 300  # 等待用户反馈超时 (秒), 超时自动通过
    graph_total_timeout: int = 300  # graph.ainvoke 总超时 (秒, 防止节点卡死永久挂起)
    max_plan_revisions: int = 3  # 研究计划修订上限, 达上限强制通过 (守卫防死循环)

    # ========== WebSocket 双向实时通信 ==========
    # SSE (/v1/chat/completions stream=true) 仍是主通道, WebSocket 是增强通道:
    #   1. 推送人在回路审核请求 (human_feedback_request) 给前端
    #   2. 接收用户反馈 (human_feedback) 提交到 feedback_queue
    #   3. 可选: 节点进度结构化推送 (node_progress)
    websocket_enabled: bool = True

    # WebSocket 安全加固
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
    mcp_cache_ttl: int = 300  # MCP 工具调用结果缓存 TTL (秒)
    mcp_cache_enabled: bool = True  # MCP 缓存开关

    # ========== 报告输出 (用户需求 6) ==========
    default_report_format: Literal["markdown", "html", "pdf", "docx", "json"] = "markdown"
    default_report_type: Literal[
        "basic_report", "detailed_report", "deep_research", "summary", "subtopics"
    ] = "basic_report"
    total_words: int = 1200
    # 报告语言 (zh|en|ja|ko|fr), 默认 zh; report_generator._get_language_instruction 读取
    report_language: str = "zh"
    # 引用格式风格 (APA/MLA/Chicago/GB7714), 默认 APA.
    # 在 report_generator._format_sources 中读取, 代码层实现真实格式化 (优于纯 LLM 生成).
    report_format_style: Literal["APA", "MLA", "Chicago", "GB7714"] = "APA"
    # 报告风格预设, 支持 academic/business/casual/news 4 种风格
    report_style: Literal["academic", "business", "casual", "news"] = "academic"
    # 报告 YAML frontmatter 开关 (默认 False, 启用后在报告首部追加元信息块).
    # frontmatter 含 title/date/query/word_count/sources_count 字段, 便于下游解析.
    enable_frontmatter: bool = False

    # ========== 学术检索路由 ==========
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

    # ========== PromptFamily 策略模式 ==========
    # 选择 prompt 策略: "default" 中文优先 | "english" 英文
    # 由 src/skills/researcher/prompts.py 的 get_prompt_family() 路由.
    prompt_family: Literal["default", "english"] = "default"

    # ========== 行业适配 4 层机制 ==========
    # AGENT_ROLE 配置 (config/variables/default.py:23):
    # 用户可注入行业 persona 字符串, 优先级高于 LLM 动态生成 (agent_creator.py).
    # 行业适配采用 4 层机制 (无行业分类器):
    #   1. Prompt 层: AgentCreator.AUTO_AGENT_INSTRUCTIONS few-shot → LLM 动态生成角色
    #   2. Config 层: 本字段 (agent_role) 静态注入角色 persona
    #   3. Retriever 层: searchers/ 含 arxiv/pubmed/semantic_scholar 等专业数据源
    #   4. MCP 层: MCP_SERVERS 注册行业专用工具服务器 (mcp_coordinator.py)
    agent_role: str | None = None

    # ========== 文件上传 (用户需求 8) ==========
    upload_dir: str = "/tmp/uploads"
    max_upload_size_mb: int = 50
    allowed_upload_extensions: str = "pdf,docx,md,txt,html,csv,xlsx,pptx"
    # Azure Blob Storage 连接串 (document_loader 读取, 留空则不启用 Azure 加载)
    azure_storage_connection_string: str | None = None

    # ========== AG2 框架 (可选) ==========
    ag2_enabled: bool = False  # AG2 框架开关 (默认关闭, 启用后可用 AG2 替代 LangGraph)

    # ========== CORS (禁 *) ==========
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
        """生产环境强制校验.

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
