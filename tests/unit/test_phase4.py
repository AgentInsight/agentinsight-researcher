"""单元测试: Phase 4 扩展功能 (文件上传 + 动态角色 + 静态页面).

AGENTS.md 第 13/14 章硬约束:
- 单元测试在构建期执行, 不得依赖外部服务 (Postgres/Qdrant/Redis/LLM)
- 文件上传按 agent_id + user_id 隔离 (AGENTS.md 第 7 章)
- 动态角色生成 (对标 GPTR choose_agent, AgentCreator LLM 动态生成行业 persona)
- 前端测试页面单文件托管 (AGENTS.md 第 14 章)

行业适配采用 GPTR 风格 4 层机制 (对标 GPT Researcher):
- Prompt 层: AgentCreator.AUTO_AGENT_INSTRUCTIONS few-shot → LLM 动态生成角色
- Config 层: settings.agent_role 静态注入角色 persona (优先级高于 LLM)
- Retriever 层: searchers/ 含 arxiv/pubmed/semantic_scholar 等专业数据源
- MCP 层: MCP_SERVERS 注册行业专用工具服务器
不再使用 IndustryClassifier / industry_prompts/*.yaml / knowledge_bootstrap.py.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import app
from src.config.settings import Settings

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


# ========== 动态角色配置 (对标 GPTR AGENT_ROLE, AGENTS.md 第 5/7 章) ==========


class TestAgentRoleConfig:
    """AgentCreator 动态角色配置测试 (对标 GPTR AGENT_ROLE).

    行业适配采用 GPTR 4 层机制, 不再使用 IndustryClassifier.
    仅测试配置项与默认值, 不测试 LLM 调用 (依赖外部服务).
    """

    def test_agent_role_default_is_none(self) -> None:
        """测试 settings.agent_role 默认为 None (对标 GPTR 默认无 AGENT_ROLE)."""
        settings = Settings(_env_file=None)
        assert settings.agent_role is None

    def test_agent_role_can_be_injected(self) -> None:
        """测试 settings.agent_role 可注入行业 persona 字符串 (对标 GPTR AGENT_ROLE)."""
        settings = Settings(
            _env_file=None, agent_role="你是一位资深金融分析师, 擅长财务建模与投资研究."
        )
        assert settings.agent_role is not None
        assert "金融分析师" in settings.agent_role

    def test_chat_request_supports_agent_role_field(self) -> None:
        """测试 ChatCompletionRequest 支持 agent_role 字段 (Config 层注入点)."""
        from src.api.routes import ChatCompletionRequest

        req = ChatCompletionRequest(
            messages=[{"role": "user", "content": "AI 对医药供应链的影响"}],
            agent_role="你是一位资深医药行业研究专家.",
        )
        assert req.agent_role is not None
        assert "医药" in req.agent_role

    def test_chat_request_agent_role_optional(self) -> None:
        """测试 ChatCompletionRequest agent_role 字段可选 (默认 None, 走 LLM 动态生成)."""
        from src.api.routes import ChatCompletionRequest

        req = ChatCompletionRequest(messages=[{"role": "user", "content": "查询"}])
        assert req.agent_role is None


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
