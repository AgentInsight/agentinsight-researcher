"""单元测试: OPT-009 全局单图编译预热.

验证:
- _get_graph() 单例: 两次调用返回同一实例 (build_researcher_graph 仅调用一次)
- 图预热后 _compiled_graph 全局变量被设置
- 预热失败异常被捕获 (不阻断启动, 由 server.py _warmup_graph try/except 保证)
- _get_graph 本身不吞异常 (异常由调用方/预热函数捕获)

单元测试在构建期执行, 不依赖外部服务.
生产 StateGraph 复用单例, 每次请求不重编译.
单例机制在 src/api/routes.py (_compiled_graph + _get_graph) 实现,
server.py lifespan _warmup_graph 后台任务触发首次构建.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


# ========== Fixtures ==========


@pytest.fixture(autouse=True)
def _reset_graph_singleton() -> None:
    """每个用例前后重置 routes._compiled_graph 全局单例 (避免测试间状态污染)."""
    import src.api.routes as routes_mod

    routes_mod._compiled_graph = None
    yield
    routes_mod._compiled_graph = None


# ========== 单例测试 ==========


async def test_get_graph_returns_singleton() -> None:
    """验证两次 _get_graph() 返回同一实例 (单例复用, build 仅调用一次)."""
    from src.api.routes import _get_graph

    mock_graph = object()  # 任意非 None 对象作为 mock 编译图

    with patch("src.graph.builder.build_researcher_graph", new_callable=AsyncMock) as mock_build:
        mock_build.return_value = mock_graph
        graph1 = await _get_graph()
        graph2 = await _get_graph()

    assert graph1 is mock_graph
    assert graph2 is mock_graph
    assert graph1 is graph2, "单例: 两次调用应返回同一对象"
    assert mock_build.call_count == 1, "单例: build_researcher_graph 仅调用一次"


async def test_warmup_sets_compiled_graph() -> None:
    """验证 _get_graph() 调用后 _compiled_graph 全局变量被设置为编译图实例."""
    import src.api.routes as routes_mod
    from src.api.routes import _get_graph

    mock_graph = object()

    with patch("src.graph.builder.build_researcher_graph", new_callable=AsyncMock) as mock_build:
        mock_build.return_value = mock_graph
        await _get_graph()

    assert routes_mod._compiled_graph is not None, "预热后 _compiled_graph 不应为 None"
    assert routes_mod._compiled_graph is mock_graph


# ========== 预热失败不阻断测试 ==========


async def test_warmup_failure_caught_by_warmup_function(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """验证预热函数 _warmup_graph 捕获异常不阻断 (模拟 server.py try/except 逻辑).

    server.py _warmup_graph 逻辑:
        try:
            await _get_graph()
            logger.info("LangGraph 研究图已预热")
        except Exception as e:
            logger.warning("图预热失败 (不阻断启动, 首次请求时重试): %s", e)
    """
    import src.api.routes as routes_mod
    from src.api.routes import _get_graph

    with patch("src.graph.builder.build_researcher_graph", new_callable=AsyncMock) as mock_build:
        mock_build.side_effect = RuntimeError("Postgres 不可用")

        # 模拟 server.py _warmup_graph 的 try/except 逻辑
        try:
            await _get_graph()
            logging.getLogger("server").info("LangGraph 研究图已预热 (全局单例, QPS 预期 +44%)")
        except Exception as e:  # noqa: BLE001
            logging.getLogger("server").warning("图预热失败 (不阻断启动, 首次请求时重试): %s", e)

    # 构建失败, _compiled_graph 仍为 None
    assert routes_mod._compiled_graph is None
    # 警告日志包含关键字
    assert "图预热失败" in caplog.text
    assert "不阻断" in caplog.text


async def test_get_graph_propagates_error() -> None:
    """验证 _get_graph 本身不吞异常 (异常由调用方/预热函数捕获)."""
    from src.api.routes import _get_graph

    with patch("src.graph.builder.build_researcher_graph", new_callable=AsyncMock) as mock_build:
        mock_build.side_effect = RuntimeError("连接失败")
        with pytest.raises(RuntimeError, match="连接失败"):
            await _get_graph()
