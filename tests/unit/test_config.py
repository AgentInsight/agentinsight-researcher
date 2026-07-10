"""单元测试: 配置 SSOT.

验证 Settings 可从环境变量加载, 不依赖外部服务.
AGENTS.md 第 13 章: 单元测试在构建期执行, 不得依赖外部服务.
"""

from __future__ import annotations

import pytest

from src.config.settings import Settings, get_settings

pytestmark = pytest.mark.unit


def test_settings_defaults(monkeypatch):
    """测试默认配置 (隔离环境变量干扰)."""
    # 清除可能干扰的环境变量, 验证代码内默认值
    for key in [
        "EMBEDDINGS_MODEL",
        "ENV",
        "AGENT_NAME",
        "QDRANT_COLLECTION",
        "QDRANT_VECTOR_SIZE",
        "DEFAULT_REPORT_FORMAT",
        "MCP_STRATEGY",
        "EMBEDDINGS_BASE_URL",
    ]:
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.env == "dev"
    assert settings.agent_name == "agentinsight-researcher"
    assert settings.qdrant_collection == "agents"
    assert settings.qdrant_vector_size == 768
    assert settings.embeddings_model == "BAAI/bge-base-zh-v1.5"
    assert settings.default_report_format == "markdown"
    assert settings.mcp_strategy == "fast"


def test_settings_from_env(monkeypatch):
    """测试从环境变量加载."""
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("AGENT_NAME", "test-agent")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("AGENTINSIGHT_PUBLIC_KEY", "pk_test")
    monkeypatch.setenv("AGENTINSIGHT_SECRET_KEY", "sk_test")
    settings = Settings(_env_file=None)
    assert settings.env == "prod"
    assert settings.agent_name == "test-agent"
    settings.validate_production()  # 不应抛异常


def test_settings_production_validation(monkeypatch):
    """测试生产环境校验 (缺密钥应抛错)."""
    # 清除所有生产密钥相关环境变量
    for key in [
        "AGENTINSIGHT_PUBLIC_KEY",
        "AGENTINSIGHT_SECRET_KEY",
        "POSTGRES_PASSWORD",
        "CORS_ALLOW_ORIGINS",
    ]:
        monkeypatch.delenv(key, raising=False)
    settings = Settings(env="prod", _env_file=None)
    with pytest.raises(ValueError) as exc_info:
        settings.validate_production()
    assert "AGENTINSIGHT_PUBLIC_KEY" in str(exc_info.value)
    assert "POSTGRES_PASSWORD" in str(exc_info.value)


def test_settings_cors_wildcard_allowed_in_prod(monkeypatch):
    """测试生产环境 CORS 允许 * (AGENTS.md 第 11 章 CORS * 限制已移除)."""
    for key in [
        "AGENTINSIGHT_PUBLIC_KEY",
        "AGENTINSIGHT_SECRET_KEY",
        "POSTGRES_PASSWORD",
    ]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENTINSIGHT_PUBLIC_KEY", "pk")
    monkeypatch.setenv("AGENTINSIGHT_SECRET_KEY", "sk")
    monkeypatch.setenv("POSTGRES_PASSWORD", "pwd")
    settings = Settings(env="prod", cors_allow_origins="*", _env_file=None)
    # CORS * 限制已移除, 不应抛出异常
    settings.validate_production()


def test_postgres_dsn():
    """测试 PostgreSQL DSN 生成."""
    settings = Settings(
        postgres_user="user",
        postgres_password="pass",
        postgres_host="host",
        postgres_port=5432,
        postgres_db="db",
        _env_file=None,
    )
    assert settings.postgres_dsn == "postgresql+asyncpg://user:pass@host:5432/db"
    assert settings.postgres_dsn_psycopg == "postgresql://user:pass@host:5432/db"


def test_cors_origins_list():
    """测试 CORS 源列表."""
    settings = Settings(cors_allow_origins="http://a.com, http://b.com", _env_file=None)
    assert settings.cors_origins_list == ["http://a.com", "http://b.com"]


def test_allowed_extensions_list():
    """测试允许上传的扩展名列表."""
    settings = Settings(_env_file=None)
    exts = settings.allowed_extensions_list
    assert "pdf" in exts
    assert "docx" in exts
    assert "md" in exts


def test_get_settings_cached():
    """测试 Settings 单例."""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


# ========== FastEmbed ONNX 线程配置 (P0, trace 4ad14970 优化) ==========


def test_fastembed_onnx_intra_threads_default(monkeypatch):
    """测试 fastembed_onnx_intra_threads 默认值为 0 (自动, 使用 cpu_count)."""
    monkeypatch.delenv("FASTEMBED_ONNX_INTRA_THREADS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.fastembed_onnx_intra_threads == 0


def test_fastembed_onnx_inter_threads_default(monkeypatch):
    """测试 fastembed_onnx_inter_threads 默认值为 0 (自动, 使用 cpu_count//2)."""
    monkeypatch.delenv("FASTEMBED_ONNX_INTER_THREADS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.fastembed_onnx_inter_threads == 0


def test_search_cache_ttl_default(monkeypatch):
    """测试 search_cache_ttl 默认值为 300 (5 分钟)."""
    monkeypatch.delenv("SEARCH_CACHE_TTL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.search_cache_ttl == 300


def test_fastembed_onnx_intra_threads_from_env(monkeypatch):
    """测试 FASTEMBED_ONNX_INTRA_THREADS 环境变量覆盖."""
    monkeypatch.setenv("FASTEMBED_ONNX_INTRA_THREADS", "8")
    settings = Settings(_env_file=None)
    assert settings.fastembed_onnx_intra_threads == 8


def test_fastembed_onnx_inter_threads_from_env(monkeypatch):
    """测试 FASTEMBED_ONNX_INTER_THREADS 环境变量覆盖."""
    monkeypatch.setenv("FASTEMBED_ONNX_INTER_THREADS", "4")
    settings = Settings(_env_file=None)
    assert settings.fastembed_onnx_inter_threads == 4


def test_search_cache_ttl_from_env(monkeypatch):
    """测试 SEARCH_CACHE_TTL 环境变量覆盖."""
    monkeypatch.setenv("SEARCH_CACHE_TTL", "600")
    settings = Settings(_env_file=None)
    assert settings.search_cache_ttl == 600
