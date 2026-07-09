"""单元测试: Embeddings 模型迁移验证 (bge-large-zh-v1.5 → bge-base-zh-v1.5).

验证 1024维 → 768维 模型迁移的完整性与一致性:
1. settings.py SSOT: qdrant_vector_size=768 / embeddings_model="BAAI/bge-base-zh-v1.5"
2. Docker Compose 三套构建文件: MODEL_ID/bind mount 注释均含 bge-base-zh-v1.5
3. requirements.txt: 含 bge-base-zh-v1.5 引用 (无 bge-large-zh-v1.5 残留)
4. AGENTS.md: 含 bge-base-zh-v1.5 引用 (技术栈表 + Qdrant 约定)
5. fastembed_client.py: FastEmbed 仍用 bge-small-zh-v1.5 (上下文压缩专用, 与 TEI 隔离)
6. 源码无 bge-large-zh-v1.5 残留 (排除 requirements.in 等历史文件)

AGENTS.md 第 13 章: 单元测试在构建期执行, 不依赖外部服务.
AGENTS.md 第 7 章硬约束:
- 远程 TEI (bge-base-zh-v1.5, 768维) 仅用于私有数据 Qdrant 索引/检索
- 上下文压缩统一用 FastEmbed (bge-small-zh-v1.5, 512维), 不依赖远程 TEI
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SETTINGS_PY = _PROJECT_ROOT / "src" / "config" / "settings.py"
_COMPOSE_QA = _PROJECT_ROOT / "docker-compose-qa.yaml"
_COMPOSE_ONLINE = _PROJECT_ROOT / "docker-compose.yml"
_COMPOSE_OFFLINE = _PROJECT_ROOT / "docker-compose-offline.yaml"
_REQUIREMENTS = _PROJECT_ROOT / "requirements.txt"
_AGENTS_MD = _PROJECT_ROOT / "AGENTS.md"
_FASTEMBED_CLIENT = _PROJECT_ROOT / "src" / "rag" / "fastembed_client.py"


# ========== SSOT 配置验证 (settings.py) ==========


def test_settings_qdrant_vector_size_is_768(monkeypatch: pytest.MonkeyPatch) -> None:
    """settings.py: qdrant_vector_size == 768 (bge-base-zh-v1.5 维度)."""
    # 隔离环境变量干扰 (与 test_config.py 一致)
    for key in ("EMBEDDINGS_MODEL", "QDRANT_VECTOR_SIZE"):
        monkeypatch.delenv(key, raising=False)
    from src.config.settings import Settings

    settings = Settings(_env_file=None)
    assert settings.qdrant_vector_size == 768, (
        f"qdrant_vector_size 应为 768 (bge-base-zh-v1.5), "
        f"实际: {settings.qdrant_vector_size}"
    )


def test_settings_embeddings_model_is_bge_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """settings.py: embeddings_model == 'BAAI/bge-base-zh-v1.5'."""
    # 隔离环境变量干扰 (与 test_config.py 一致)
    for key in ("EMBEDDINGS_MODEL", "QDRANT_VECTOR_SIZE"):
        monkeypatch.delenv(key, raising=False)
    from src.config.settings import Settings

    settings = Settings(_env_file=None)
    assert settings.embeddings_model == "BAAI/bge-base-zh-v1.5", (
        f"embeddings_model 应为 'BAAI/bge-base-zh-v1.5', "
        f"实际: {settings.embeddings_model}"
    )


def test_settings_fastembed_uses_bge_small(monkeypatch: pytest.MonkeyPatch) -> None:
    """settings.py: fastembed_model_name 仍为 bge-small-zh-v1.5 (上下文压缩专用).

    AGENTS.md 第 7 章硬约束: FastEmbed (bge-small-zh-v1.5, 512维) 与远程 TEI
    (bge-base-zh-v1.5, 768维) 完全隔离, 不应被本次迁移影响.
    """
    for key in ("FASTEMBED_MODEL_NAME", "FASTEMBED_DIMENSION"):
        monkeypatch.delenv(key, raising=False)
    from src.config.settings import Settings

    settings = Settings(_env_file=None)
    assert settings.fastembed_model_name == "BAAI/bge-small-zh-v1.5"
    assert settings.fastembed_dimension == 512


def test_settings_source_file_contains_768_comment() -> None:
    """settings.py 源码含 768 维注释 (bge-base-zh-v1.5)."""
    content = _SETTINGS_PY.read_text(encoding="utf-8")
    assert "768" in content, "settings.py 应含 768 维度注释"
    assert "bge-base-zh-v1.5" in content, "settings.py 应含 bge-base-zh-v1.5 引用"


def test_settings_source_file_no_large_residual() -> None:
    """settings.py 源码无 bge-large-zh-v1.5 残留 (迁移完整性)."""
    content = _SETTINGS_PY.read_text(encoding="utf-8")
    assert "bge-large-zh-v1.5" not in content, (
        "settings.py 不应残留 bge-large-zh-v1.5 (已迁移到 bge-base-zh-v1.5)"
    )
    # qdrant_vector_size 不应为 1024 (旧 bge-large 维度)
    assert "qdrant_vector_size: int = 1024" not in content, (
        "settings.py 不应残留 qdrant_vector_size = 1024 (已迁移到 768)"
    )


# ========== Docker Compose 三套构建文件验证 ==========


def test_compose_qa_uses_bge_base() -> None:
    """docker-compose-qa.yaml: MODEL_ID 与 bind mount 均为 bge-base-zh-v1.5."""
    if not _COMPOSE_QA.exists():
        pytest.skip("docker-compose-qa.yaml 不存在 (gitignored)")
    content = _COMPOSE_QA.read_text(encoding="utf-8")
    assert "/data/bge-base-zh-v1.5" in content, (
        "docker-compose-qa.yaml 应含 MODEL_ID=/data/bge-base-zh-v1.5"
    )
    assert "bge-base-zh-v1.5:/data/bge-base-zh-v1.5:ro" in content, (
        "docker-compose-qa.yaml 应含 bge-base-zh-v1.5 bind mount"
    )
    assert "768" in content, "docker-compose-qa.yaml 应含 768 维注释"


def test_compose_qa_no_large_residual() -> None:
    """docker-compose-qa.yaml 无 bge-large-zh-v1.5 残留."""
    if not _COMPOSE_QA.exists():
        pytest.skip("docker-compose-qa.yaml 不存在 (gitignored)")
    content = _COMPOSE_QA.read_text(encoding="utf-8")
    assert "bge-large-zh-v1.5" not in content, (
        "docker-compose-qa.yaml 不应残留 bge-large-zh-v1.5"
    )


def test_compose_online_uses_bge_base() -> None:
    """docker-compose.yml: MODEL_ID 为 bge-base-zh-v1.5."""
    if not _COMPOSE_ONLINE.exists():
        pytest.skip("docker-compose.yml 不存在")
    content = _COMPOSE_ONLINE.read_text(encoding="utf-8")
    assert "/data/bge-base-zh-v1.5" in content
    assert "768" in content


def test_compose_online_no_large_residual() -> None:
    """docker-compose.yml 无 bge-large-zh-v1.5 残留."""
    if not _COMPOSE_ONLINE.exists():
        pytest.skip("docker-compose.yml 不存在")
    content = _COMPOSE_ONLINE.read_text(encoding="utf-8")
    assert "bge-large-zh-v1.5" not in content


def test_compose_offline_uses_bge_base() -> None:
    """docker-compose-offline.yaml: MODEL_ID 与 bind mount 均为 bge-base-zh-v1.5."""
    if not _COMPOSE_OFFLINE.exists():
        pytest.skip("docker-compose-offline.yaml 不存在 (gitignored)")
    content = _COMPOSE_OFFLINE.read_text(encoding="utf-8")
    assert "/data/bge-base-zh-v1.5" in content
    assert "bge-base-zh-v1.5:/data/bge-base-zh-v1.5:ro" in content
    assert "768" in content


def test_compose_offline_no_large_residual() -> None:
    """docker-compose-offline.yaml 无 bge-large-zh-v1.5 残留."""
    if not _COMPOSE_OFFLINE.exists():
        pytest.skip("docker-compose-offline.yaml 不存在 (gitignored)")
    content = _COMPOSE_OFFLINE.read_text(encoding="utf-8")
    assert "bge-large-zh-v1.5" not in content


# ========== requirements.txt 验证 ==========


def test_requirements_mentions_bge_base() -> None:
    """requirements.txt 含 bge-base-zh-v1.5 引用 (注释或依赖)."""
    if not _REQUIREMENTS.exists():
        pytest.skip("requirements.txt 不存在")
    content = _REQUIREMENTS.read_text(encoding="utf-8")
    # requirements.txt 可能在注释中提及模型名 (作为文档说明)
    assert "bge-base-zh-v1.5" in content or "BAAI/bge-base-zh-v1.5" in content, (
        "requirements.txt 应含 bge-base-zh-v1.5 引用"
    )


# ========== AGENTS.md 验证 ==========


def test_agents_md_mentions_bge_base() -> None:
    """AGENTS.md 含 bge-base-zh-v1.5 引用 (技术栈表 + Qdrant 约定)."""
    if not _AGENTS_MD.exists():
        pytest.skip("AGENTS.md 不存在")
    content = _AGENTS_MD.read_text(encoding="utf-8")
    assert "bge-base-zh-v1.5" in content, (
        "AGENTS.md 应含 bge-base-zh-v1.5 引用 (技术栈表/Qdrant 约定)"
    )


def test_agents_md_qdrant_vector_size_768() -> None:
    """AGENTS.md Qdrant 集合约定: vector_size=1024 → 768 (bge-base-zh-v1.5)."""
    if not _AGENTS_MD.exists():
        pytest.skip("AGENTS.md 不存在")
    content = _AGENTS_MD.read_text(encoding="utf-8")
    # AGENTS.md 第 7 章 Qdrant 集合约定应更新为 768
    assert "768" in content, "AGENTS.md 应含 768 维度 (Qdrant 集合约定)"


# ========== FastEmbed 隔离验证 (不应被迁移影响) ==========


def test_fastembed_client_still_uses_bge_small() -> None:
    """fastembed_client.py 仍使用 bge-small-zh-v1.5 (上下文压缩专用).

    AGENTS.md 第 7 章硬约束: FastEmbed 与远程 TEI 完全隔离.
    本次迁移仅影响远程 TEI (bge-large → bge-base), 不应影响 FastEmbed.
    """
    if not _FASTEMBED_CLIENT.exists():
        pytest.skip("fastembed_client.py 不存在")
    content = _FASTEMBED_CLIENT.read_text(encoding="utf-8")
    assert "bge-small-zh-v1.5" in content, (
        "fastembed_client.py 应仍使用 bge-small-zh-v1.5 (上下文压缩专用)"
    )
    # FastEmbed 不应被改为 bge-base (与远程 TEI 隔离原则)
    assert "BAAI/bge-base-zh-v1.5" not in content, (
        "fastembed_client.py 不应使用 bge-base-zh-v1.5 (应保持 bge-small-zh-v1.5)"
    )


# ========== 源码全局残留检查 ==========


def test_no_bge_large_residual_in_source() -> None:
    """src/ 目录下无 bge-large-zh-v1.5 残留 (迁移完整性).

    排除 .venv/ 与 __pycache__/ 等非源码目录.
    """
    src_dir = _PROJECT_ROOT / "src"
    if not src_dir.exists():
        pytest.skip("src/ 目录不存在")
    residuals: list[str] = []
    for py_file in src_dir.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "bge-large-zh-v1.5" in content:
            residuals.append(str(py_file.relative_to(_PROJECT_ROOT)))
    assert not residuals, (
        f"src/ 目录下 {len(residuals)} 个文件残留 bge-large-zh-v1.5: {residuals}"
    )


def test_qdrant_collection_768_dim_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """运行时验证: Settings 实例化的 qdrant_vector_size 与 bge-base 维度一致.

    端到端契约: settings.qdrant_vector_size (768) == bge-base-zh-v1.5 实际维度 (768).
    """
    for key in ("EMBEDDINGS_MODEL", "QDRANT_VECTOR_SIZE"):
        monkeypatch.delenv(key, raising=False)
    from src.config.settings import Settings

    settings = Settings(_env_file=None)
    # bge-base-zh-v1.5 官方维度为 768 (HuggingFace 模型卡)
    expected_dim = 768
    assert settings.qdrant_vector_size == expected_dim, (
        f"Qdrant 集合维度 ({settings.qdrant_vector_size}) "
        f"应与 bge-base-zh-v1.5 维度 ({expected_dim}) 一致"
    )
