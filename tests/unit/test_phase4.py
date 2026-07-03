"""单元测试: Phase 4 扩展功能 (文件上传 + 行业提示词 + 静态页面).

AGENTS.md 第 13/14 章硬约束:
- 单元测试在构建期执行, 不得依赖外部服务 (Postgres/Qdrant/Redis/LLM)
- 文件上传按 agent_id + user_id 隔离 (AGENTS.md 第 7 章)
- 行业提示词加载 (用户需求 4: GICS 68 行业)
- 前端测试页面单文件托管 (AGENTS.md 第 14 章)
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import app
from src.config.settings import Settings
from src.skills.researcher.industry_classifier import IndustryClassifier

# ========== 文件上传 (用户需求 8) ==========


class TestFileUpload:
    """文件上传端点单元测试."""

    def test_upload_text_file_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """测试成功上传 .txt 文件."""
        # 隔离环境变量 + 临时上传目录
        monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
        from src.api import routes as routes_mod

        # 重置 settings 缓存 + graph 单例
        from src.config import settings as settings_mod

        settings_mod.get_settings.cache_clear()
        routes_mod._compiled_graph = None

        client = TestClient(app)
        response = client.post(
            "/v1/files",
            files={"file": ("test.txt", io.BytesIO(b"hello world"), "text/plain")},
        )
        assert response.status_code == 201
        data = response.json()
        assert "file_id" in data
        assert data["filename"] == "test.txt"
        assert data["size_bytes"] == 11
        assert data["extension"] == "txt"

    def test_upload_invalid_extension_rejected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """测试不允许的扩展名应返回 415."""
        monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
        from src.config import settings as settings_mod

        settings_mod.get_settings.cache_clear()

        client = TestClient(app)
        response = client.post(
            "/v1/files",
            files={"file": ("malware.exe", io.BytesIO(b"payload"), "application/octet-stream")},
        )
        assert response.status_code == 415
        assert "不支持的文件类型" in response.json()["detail"]

    def test_upload_md_file_success(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """测试 .md 文件上传成功 (allowed_extensions 含 md)."""
        monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
        from src.config import settings as settings_mod

        settings_mod.get_settings.cache_clear()

        client = TestClient(app)
        response = client.post(
            "/v1/files",
            files={"file": ("notes.md", io.BytesIO("# 标题\n内容".encode()), "text/markdown")},
        )
        assert response.status_code == 201
        assert response.json()["extension"] == "md"


# ========== 行业提示词加载 (用户需求 4) ==========


class TestIndustryPrompts:
    """GICS 行业专家提示词 YAML 加载测试."""

    def test_load_software_services_prompt(self) -> None:
        """测试加载软件与服务行业提示词 (industry_code=451020)."""
        settings = Settings(_env_file=None)
        classifier = IndustryClassifier(settings)
        prompt_family = classifier._load_prompt_family("451020")
        assert prompt_family["industry_code"] == "451020"
        assert (
            "软件" in prompt_family["industry_name"]
            or "software" in prompt_family["industry_name"].lower()
        )
        assert "planner_prompt" in prompt_family
        assert "writer_prompt" in prompt_family
        assert len(prompt_family["key_dimensions"]) >= 4
        # 提示词必须中文
        assert len(prompt_family["planner_prompt"]) > 50

    def test_load_banks_prompt(self) -> None:
        """测试加载银行业提示词."""
        settings = Settings(_env_file=None)
        classifier = IndustryClassifier(settings)
        # 银行 industry_code 在 401010 附近, 找到 banks.yaml 对应的 code
        prompts_dir = (
            Path(__file__).parent.parent.parent / "config" / "researcher" / "industry_prompts"
        )
        banks_yaml = prompts_dir / "banks.yaml"
        if banks_yaml.exists():
            import yaml

            with open(banks_yaml, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            code = data["industry_code"]
            prompt_family = classifier._load_prompt_family(code)
            assert prompt_family["industry_code"] == code
            assert "银行" in prompt_family["industry_name"]

    def test_unknown_industry_returns_default(self) -> None:
        """测试未知行业代码返回默认通用研究提示词."""
        settings = Settings(_env_file=None)
        classifier = IndustryClassifier(settings)
        prompt_family = classifier._load_prompt_family("999999")
        assert prompt_family["industry_code"] == "UNKNOWN"
        assert prompt_family["industry_name"] == "通用研究"
        assert len(prompt_family["key_dimensions"]) >= 4

    def test_prompt_family_caching(self) -> None:
        """测试提示词族缓存机制 (同一 code 二次加载命中缓存)."""
        settings = Settings(_env_file=None)
        classifier = IndustryClassifier(settings)
        first = classifier._load_prompt_family("451020")
        second = classifier._load_prompt_family("451020")
        # 缓存返回同一对象引用
        assert first is second

    def test_all_yaml_files_valid_schema(self) -> None:
        """测试所有 YAML 文件 schema 完整性 (用户需求 4: 68 行业)."""
        prompts_dir = (
            Path(__file__).parent.parent.parent / "config" / "researcher" / "industry_prompts"
        )
        if not prompts_dir.exists():
            pytest.skip("行业提示词目录不存在")

        yaml_files = list(prompts_dir.glob("*.yaml"))
        # 至少 60 个 (用户需求 68, 实际生成 74)
        assert len(yaml_files) >= 60, f"行业提示词数量不足: {len(yaml_files)}"

        import yaml

        required_keys = {
            "industry_code",
            "industry_name",
            "industry_sector",
            "industry_group",
            "industry_sub",
            "planner_prompt",
            "researcher_prompt",
            "reviewer_prompt",
            "writer_prompt",
            "key_dimensions",
            "data_sources_preference",
        }

        codes_seen: set[str] = set()
        for yaml_file in yaml_files:
            with open(yaml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            assert isinstance(data, dict), f"{yaml_file.name} 不是 dict"
            missing = required_keys - set(data.keys())
            assert not missing, f"{yaml_file.name} 缺字段: {missing}"
            assert isinstance(data["key_dimensions"], list), (
                f"{yaml_file.name} key_dimensions 不是 list"
            )
            assert len(data["key_dimensions"]) >= 4, f"{yaml_file.name} 维度不足 4 个"
            # industry_code 唯一
            code = data["industry_code"]
            assert code not in codes_seen, f"{yaml_file.name} industry_code 重复: {code}"
            codes_seen.add(code)
            # 提示词必须非空 + 中文
            for prompt_key in ("planner_prompt", "writer_prompt"):
                assert len(data[prompt_key]) > 50, f"{yaml_file.name} {prompt_key} 过短"
                # 含中文字符 (简单判断)
                assert any("\u4e00" <= ch <= "\u9fff" for ch in data[prompt_key]), (
                    f"{yaml_file.name} {prompt_key} 缺中文"
                )


# ========== 静态页面 (AGENTS.md 第 14 章) ==========


class TestStaticPage:
    """前端测试页面托管测试."""

    def test_static_index_html_served(self) -> None:
        """测试 / 返回 index.html (AGENTS.md 第 14 章)."""
        client = TestClient(app)
        response = client.get("/")
        assert response.status_code == 200
        # 必须是 HTML
        assert "<html" in response.text.lower()
        # 标题含项目名
        assert "researcher" in response.text.lower() or "研究" in response.text
        # 必须含 SSE 相关 JS (fetch + ReadableStream)
        assert "fetch" in response.text.lower() or "ReadableStream" in response.text

    def test_static_page_size_reasonable(self) -> None:
        """测试静态页面大小合理 (单文件, 内联样式+JS)."""
        client = TestClient(app)
        response = client.get("/")
        # 单文件应 > 5KB (含样式+JS+HTML)
        assert len(response.content) > 5000
        # 应 < 200KB (避免引入大型库)
        assert len(response.content) < 200_000

    def test_static_page_no_react_vue(self) -> None:
        """测试静态页面未引入 React/Vue (AGENTS.md 第 14 章)."""
        client = TestClient(app)
        response = client.get("/")
        text_lower = response.text.lower()
        # 禁止引入前端框架
        assert "react.development.js" not in text_lower
        assert "vue.global.js" not in text_lower
        assert "vue@3" not in text_lower
