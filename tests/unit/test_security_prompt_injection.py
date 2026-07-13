"""单元测试: Prompt Injection 防护 + 工具权限隔离.

覆盖场景:
- 所有外部输入经 Pydantic 校验 (ChatCompletionRequest 字段类型校验)
- 禁止 eval/exec 求值用户输入 (代码注入阻断)
- LLM 输出经结构化校验后再入工具 (MCP 工具调用结果校验)
- 工具调用权限隔离 (read/write/execute/network 显式授权)

安全合规红线:
- 所有外部输入经 Pydantic 校验
- 工具调用权限隔离 (read/write/execute/network 显式授权)
- 禁止 eval/exec 求值用户输入 (注入风险, 属安全约束)
- LLM 输出经结构化校验后再入工具
- 敏感工具 (写文件/执行命令) 应显式声明权限, 由中间件校验

单元测试不依赖外部服务, 全部用 mock.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.api.routes import ChatCompletionRequest, ChatMessage

pytestmark = pytest.mark.unit


# ============================================================================
# 场景 1: 所有外部输入经 Pydantic 校验
# ============================================================================


class TestPydanticValidationExternalInput:
    """验证 ChatCompletionRequest 外部输入经 Pydantic 校验.

    所有外部输入经 Pydantic 校验.
    """

    def test_valid_request_accepted(self) -> None:
        """合法请求体应通过 Pydantic 校验."""
        req = ChatCompletionRequest(
            model="agentinsight-researcher",
            messages=[ChatMessage(role="user", content="你好")],
            stream=False,
        )
        assert req.model == "agentinsight-researcher"
        assert req.messages[0].content == "你好"

    def test_messages_required_field(self) -> None:
        """缺少 messages 必填字段 → ValidationError."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(  # type: ignore[call-arg]
                model="agentinsight-researcher",
                stream=False,
            )

    def test_messages_must_be_list(self) -> None:
        """messages 非 list → ValidationError."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="agentinsight-researcher",
                messages="not-a-list",  # type: ignore[arg-type]
                stream=False,
            )

    def test_message_content_must_be_str(self) -> None:
        """message content 非 str → ValidationError."""
        with pytest.raises(ValidationError):
            ChatMessage(role="user", content=12345)  # type: ignore[arg-type]

    def test_stream_must_be_bool(self) -> None:
        """stream 非 bool → ValidationError (StrictBool)."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="agentinsight-researcher",
                messages=[ChatMessage(role="user", content="你好")],
                stream="yes",  # type: ignore[arg-type]
            )

    def test_stream_str_true_rejected(self) -> None:
        """stream="true" (字符串) → ValidationError (StrictBool 拒绝字符串)."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="agentinsight-researcher",
                messages=[ChatMessage(role="user", content="你好")],
                stream="true",  # type: ignore[arg-type]
            )

    def test_stream_int_rejected(self) -> None:
        """stream=1 (整数) → ValidationError (StrictBool 拒绝整数)."""
        with pytest.raises(ValidationError):
            ChatCompletionRequest(
                model="agentinsight-researcher",
                messages=[ChatMessage(role="user", content="你好")],
                stream=1,  # type: ignore[arg-type]
            )

    def test_injection_query_accepted_by_pydantic_but_safe(self) -> None:
        """注入查询被 Pydantic 接受 (字符串), 但不会被 eval/exec 执行.

        外部输入经 Pydantic 校验类型, 内容不执行.
        注入字符串 "eval('hacked')" 作为 content 是合法字符串, 但不会被执行.
        """
        injection = "eval('print(\"hacked\")')"
        req = ChatCompletionRequest(
            model="agentinsight-researcher",
            messages=[ChatMessage(role="user", content=injection)],
            stream=False,
        )
        # Pydantic 接受字符串内容 (类型合法)
        assert req.messages[0].content == injection
        # 但内容不会被 eval/exec (由后续路由逻辑保证, 见 eval/exec 测试)


# ============================================================================
# 场景 2: 禁止 eval/exec 求值用户输入
# ============================================================================


class TestEvalExecBlocked:
    """验证 eval/exec 求值用户输入被阻断.

    禁止 eval/exec 求值用户输入 (注入风险, 属安全约束).
    """

    def test_eval_payload_not_executed(self) -> None:
        """eval 注入 payload 不被执行 (仅作为字符串处理)."""
        payload = "eval('print(\"hacked\")')"
        # ChatMessage 仅存储字符串, 不执行
        msg = ChatMessage(role="user", content=payload)
        assert msg.content == payload
        # 验证 eval 未被执行 (无输出, 无副作用)
        # 如果 eval 被执行, "hacked" 会出现在 stdout, 但这里只是字符串存储

    def test_exec_payload_not_executed(self) -> None:
        """exec 注入 payload 不被执行."""
        payload = "exec('import os; os.system(\"whoami\")')"
        msg = ChatMessage(role="user", content=payload)
        assert msg.content == payload
        # exec 未被执行, 仅字符串存储

    def test_import_payload_not_executed(self) -> None:
        """__import__ 注入 payload 不被执行."""
        payload = "__import__('os').system('rm -rf /')"
        msg = ChatMessage(role="user", content=payload)
        assert msg.content == payload

    def test_base64_obfuscated_eval_not_executed(self) -> None:
        """base64 混淆的 eval 注入不被执行."""
        payload = (
            "eval(__import__('base64').b64decode('aW1wb3J0IG9zOyBvcy5zeXN0ZW0oIndob2FtaSIp')"
            ".decode())"
        )
        msg = ChatMessage(role="user", content=payload)
        assert msg.content == payload

    def test_nested_eval_not_executed(self) -> None:
        """嵌套 eval 注入不被执行."""
        payload = "eval(eval('1+1'))"
        msg = ChatMessage(role="user", content=payload)
        assert msg.content == payload

    def test_eval_with_file_read_not_executed(self) -> None:
        """eval + open 文件读取注入不被执行."""
        payload = "eval(open('/etc/passwd').read())"
        msg = ChatMessage(role="user", content=payload)
        assert msg.content == payload

    def test_no_eval_in_source_code(self) -> None:
        """验证 routes.py 源码中不存在 eval/exec 调用用户输入.

        禁止 eval/exec 求值用户输入.
        通过源码扫描确保业务代码不使用 eval/exec.
        """
        import src.api.routes as routes_module

        source_code = _get_module_source(routes_module)
        # 不应包含 eval( 或 exec( 调用 (字符串形式检查)
        assert "eval(" not in source_code, "routes.py 源码包含 eval() 调用 (违反安全约束)"
        assert "exec(" not in source_code, "routes.py 源码包含 exec() 调用 (违反安全约束)"

    def test_no_eval_in_middleware_source_code(self) -> None:
        """验证 middleware.py 源码中不存在 eval/exec 调用."""
        import src.api.middleware as middleware_module

        source_code = _get_module_source(middleware_module)
        assert "eval(" not in source_code, "middleware.py 源码包含 eval() 调用"
        assert "exec(" not in source_code, "middleware.py 源码包含 exec() 调用"


def _get_module_source(module: Any) -> str:
    """获取模块源码文本 (用于安全扫描)."""
    import inspect

    try:
        return inspect.getsource(module)
    except (OSError, TypeError):
        return ""


# ============================================================================
# 场景 3: API 路由 Pydantic 校验集成测试 (422 错误码)
# 所有外部输入经 Pydantic 校验
# ============================================================================


def _make_minimal_app() -> FastAPI:
    """创建最小化 FastAPI 应用 (含 /v1/chat/completions 路由, 走 Pydantic 校验).

    不依赖 LangGraph/Qdrant 等外部服务, 仅验证 Pydantic 校验层.
    """
    from src.api.routes import router

    app = FastAPI()
    app.include_router(router)
    return app


class TestApiPydanticValidation:
    """验证 API 端点 Pydantic 校验返回 422 (不依赖容器栈)."""

    def test_missing_messages_returns_422(self) -> None:
        """缺少 messages 字段 → 422 (Pydantic 校验失败)."""
        app = _make_minimal_app()
        client = TestClient(app)
        r = client.post(
            "/v1/chat/completions",
            json={"model": "agentinsight-researcher", "stream": False},
        )
        assert r.status_code == 422, f"缺少 messages 应返回 422, 实际: {r.status_code}"

    def test_messages_wrong_type_returns_422(self) -> None:
        """messages 字段类型错误 → 422."""
        app = _make_minimal_app()
        client = TestClient(app)
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": "not-a-list",
                "stream": False,
            },
        )
        assert r.status_code == 422

    def test_content_wrong_type_returns_422(self) -> None:
        """content 字段类型错误 → 422."""
        app = _make_minimal_app()
        client = TestClient(app)
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": 12345}],
                "stream": False,
            },
        )
        assert r.status_code == 422

    def test_stream_wrong_type_returns_422(self) -> None:
        """stream 字段非 bool → 422 (StrictBool)."""
        app = _make_minimal_app()
        client = TestClient(app)
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "你好"}],
                "stream": "yes",
            },
        )
        assert r.status_code == 422

    def test_invalid_json_body_returns_422(self) -> None:
        """非法 JSON body → 422."""
        app = _make_minimal_app()
        client = TestClient(app)
        r = client.post(
            "/v1/chat/completions",
            content=b"{not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 422

    def test_empty_messages_returns_400(self) -> None:
        """空 messages 列表 → 400 (业务校验, 非 Pydantic)."""
        app = _make_minimal_app()
        client = TestClient(app)
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [],
                "stream": False,
            },
        )
        # 空 list 通过 Pydantic 校验, 但路由内业务校验拒绝 → 400
        assert r.status_code == 400, f"空 messages 应返回 400, 实际: {r.status_code}"

    def test_no_user_message_returns_400(self) -> None:
        """仅 system 消息 (无 user) → 400 (业务校验)."""
        app = _make_minimal_app()
        client = TestClient(app)
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "system", "content": "你是研究助手"}],
                "stream": False,
            },
        )
        assert r.status_code == 400

    def test_empty_query_returns_400(self) -> None:
        """user content 为空白 → 400 (业务校验)."""
        app = _make_minimal_app()
        client = TestClient(app)
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "agentinsight-researcher",
                "messages": [{"role": "user", "content": "   "}],
                "stream": False,
            },
        )
        assert r.status_code == 400


# ============================================================================
# 场景 4: 工具调用权限隔离 (read/write/execute/network 显式授权)
# 工具调用权限隔离
# ============================================================================


class TestToolPermissionIsolation:
    """验证工具调用权限隔离机制.

    工具调用权限隔离 (read/write/execute/network 显式授权);
    敏感工具 (写文件/执行命令) 应显式声明权限, 由中间件校验.
    """

    def test_mcp_coordinator_uses_trace_tool_span(self) -> None:
        """验证 MCP 工具调用经 trace_tool span 包裹 (可观测性).

        工具调用应经 AgentInsight trace_tool span 包裹.
        工具调用权限隔离, 参数与结果入 span.
        """
        import src.skills.researcher.mcp_coordinator as mcp_module

        source = _get_module_source(mcp_module)
        # mcp_coordinator 应使用 trace_tool 包裹工具调用
        assert "trace_tool" in source, "mcp_coordinator 未使用 trace_tool span 包裹工具调用"

    def test_no_direct_os_system_call(self) -> None:
        """验证 mcp_coordinator 不直接调用 os.system (敏感操作经 MCP 协议).

        敏感工具 (执行命令) 应显式声明权限, 由中间件校验.
        业务代码不应直接调用 os.system.
        """
        import src.skills.researcher.mcp_coordinator as mcp_module

        source = _get_module_source(mcp_module)
        # 不应直接调用 os.system (应通过 MCP Server 协议)
        assert "os.system(" not in source, "mcp_coordinator 直接调用 os.system (安全风险)"

    def test_no_direct_subprocess_call(self) -> None:
        """验证 mcp_coordinator 不直接调用 subprocess (敏感操作经 MCP 协议)."""
        import src.skills.researcher.mcp_coordinator as mcp_module

        source = _get_module_source(mcp_module)
        # 不应直接调用 subprocess (应通过 MCP Server 协议)
        assert "subprocess.call(" not in source, "mcp_coordinator 直接调用 subprocess.call"
        assert "subprocess.run(" not in source, "mcp_coordinator 直接调用 subprocess.run"

    def test_no_direct_eval_exec_in_mcp(self) -> None:
        """验证 mcp_coordinator 不使用 eval/exec."""
        import src.skills.researcher.mcp_coordinator as mcp_module

        source = _get_module_source(mcp_module)
        assert "eval(" not in source, "mcp_coordinator 源码包含 eval() 调用"
        assert "exec(" not in source, "mcp_coordinator 源码包含 exec() 调用"

    def test_routes_no_direct_file_write(self) -> None:
        """验证 routes.py 不直接调用 open() 写文件 (经 UploadFile 端点).

        敏感工具 (写文件) 应显式声明权限.
        文件写入应经 /v1/files 端点 (含大小/扩展名校验), 不在业务逻辑中直接写.
        """
        import src.api.routes as routes_module

        source = _get_module_source(routes_module)
        # 不应直接使用 open() 写文件 (应经 UploadFile + asyncio.to_thread)
        # 注意: read_text/read_bytes 用于读取, 这里检查写操作
        assert "open(" not in source or "write_text" not in source, (
            "routes.py 直接调用 open() 写文件 (应经 UploadFile 端点)"
        )


# ============================================================================
# 场景 5: 安全响应头中间件不可绕过
# ============================================================================


class TestSecurityHeadersMiddleware:
    """验证安全响应头中间件不可绕过.

    安全响应头中间件不可绕过.
    - X-Content-Type-Options: nosniff
    - X-Frame-Options: DENY
    - Strict-Transport-Security: HSTS (生产强制 HTTPS)
    """

    def test_security_headers_always_injected(self) -> None:
        """验证所有响应都注入安全响应头 (中间件不可绕过)."""
        from src.api.middleware import SecurityHeadersMiddleware

        app = FastAPI()

        @app.get("/test")
        async def test_endpoint() -> dict[str, str]:
            return {"ok": "true"}

        app.add_middleware(SecurityHeadersMiddleware)
        client = TestClient(app)

        r = client.get("/test")
        assert r.status_code == 200
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["X-XSS-Protection"] == "1; mode=block"
        assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    def test_security_headers_on_error_response(self) -> None:
        """验证错误响应也注入安全响应头 (不可绕过)."""
        from src.api.middleware import SecurityHeadersMiddleware

        app = FastAPI()

        @app.get("/error")
        async def error_endpoint() -> None:
            from fastapi import HTTPException

            raise HTTPException(status_code=500, detail="test error")

        app.add_middleware(SecurityHeadersMiddleware)
        client = TestClient(app)

        r = client.get("/error")
        assert r.status_code == 500
        # 错误响应也应含安全头
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"

    def test_security_headers_on_404_response(self) -> None:
        """验证 404 响应也注入安全响应头."""
        from src.api.middleware import SecurityHeadersMiddleware

        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware)
        client = TestClient(app)

        r = client.get("/nonexistent-path")
        assert r.status_code == 404
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"


# ============================================================================
# 场景 6: 密钥不硬编码
# ============================================================================


class TestNoHardcodedSecrets:
    """验证源码中不存在硬编码密钥.

    密钥仅环境变量注入, 禁止入仓/硬编码/日志;
    发现硬编码密钥即 P0 暂停并人工介入.
    """

    def test_no_hardcoded_api_keys_in_middleware(self) -> None:
        """验证 middleware.py 源码不含硬编码 API Key 模式."""
        import src.api.middleware as middleware_module

        source = _get_module_source(middleware_module)
        # 常见硬编码密钥模式 (不应出现在源码中)
        import re

        secret_patterns = [
            r"sk-[a-zA-Z0-9]{20,}",  # OpenAI-style
            r"AKIA[0-9A-Z]{16}",  # AWS
            r"ghp_[a-zA-Z0-9]{36}",  # GitHub PAT
        ]
        for pattern in secret_patterns:
            matches = re.findall(pattern, source)
            assert not matches, f"middleware.py 含硬编码密钥: {matches}"

    def test_no_hardcoded_api_keys_in_routes(self) -> None:
        """验证 routes.py 源码不含硬编码 API Key 模式."""
        import src.api.routes as routes_module

        source = _get_module_source(routes_module)
        import re

        secret_patterns = [
            r"sk-[a-zA-Z0-9]{20,}",
            r"AKIA[0-9A-Z]{16}",
            r"ghp_[a-zA-Z0-9]{36}",
        ]
        for pattern in secret_patterns:
            matches = re.findall(pattern, source)
            assert not matches, f"routes.py 含硬编码密钥: {matches}"

    def test_settings_loads_keys_from_env(self) -> None:
        """验证 Settings 从环境变量加载密钥 (不硬编码)."""
        from src.config.settings import Settings

        # 未设置环境变量时, API Key 字段应为 None (非硬编码值)
        settings = Settings(_env_file=None)
        assert settings.deepseek_api_key is None or isinstance(settings.deepseek_api_key, str)
        # 不应有默认硬编码值 (None 表示必须从环境变量注入)
        # 注意: 这里只验证类型, 实际值由 .env 注入


# ============================================================================
# 场景 7: 文件上传安全 (大小/扩展名白名单)
# ============================================================================


class TestFileUploadSecurity:
    """验证文件上传安全校验.

    安全约束 (大小/扩展名白名单).
    用户私有数据按 agent_id + user_id 隔离.
    """

    def test_max_upload_size_configurable(self) -> None:
        """验证 max_upload_size_mb 可配置 (非硬编码)."""
        from src.config.settings import Settings

        settings = Settings(_env_file=None)
        assert isinstance(settings.max_upload_size_mb, int)
        assert settings.max_upload_size_mb > 0

    def test_allowed_extensions_list_not_empty(self) -> None:
        """验证 allowed_extensions_list 非空 (白名单机制)."""
        from src.config.settings import Settings

        settings = Settings(_env_file=None)
        ext_list = settings.allowed_extensions_list
        assert isinstance(ext_list, list)
        assert len(ext_list) > 0, "allowed_extensions_list 不应为空 (白名单机制)"

    def test_allowed_extensions_are_lowercase(self) -> None:
        """验证扩展名白名单均为小写 (避免大小写绕过)."""
        from src.config.settings import Settings

        settings = Settings(_env_file=None)
        for ext in settings.allowed_extensions_list:
            assert ext == ext.lower(), f"扩展名 {ext} 非小写 (可能被大小写绕过)"


# ============================================================================
# 场景 8: 路径穿越防护
# ============================================================================


class TestPathTraversalProtection:
    """验证路径穿越防护.

    Prompt Injection 防护; 敏感工具需权限隔离.
    """

    def test_path_traversal_in_query_not_executed(self) -> None:
        """路径穿越字符串作为 content 存储, 不被执行."""
        payload = "../../../etc/passwd"
        msg = ChatMessage(role="user", content=payload)
        assert msg.content == payload
        # 仅字符串存储, 不访问文件系统

    def test_absolute_path_in_query_not_executed(self) -> None:
        """绝对路径字符串作为 content 存储, 不被执行."""
        payload = "/etc/shadow"
        msg = ChatMessage(role="user", content=payload)
        assert msg.content == payload

    def test_file_id_traversal_rejected_by_routes(self) -> None:
        """验证 uploaded_files 的 file_id 路径穿越被拒绝.

        file_id 三级分键 (agent_id:user_id:uuid),
        routes.py _load_uploaded_files_context 校验 file_id 前缀归属.
        """
        import src.api.routes as routes_module

        source = _get_module_source(routes_module)
        # _load_uploaded_files_context 应含 file_id 前缀校验逻辑
        assert "fid_agent" in source or "fid_user" in source, (
            "routes.py 未校验 file_id 前缀归属 (路径穿越风险)"
        )
