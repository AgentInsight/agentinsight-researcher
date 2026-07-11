"""冒烟测试: DeepResearcher 模块可导入 + 可实例化 + research() 最小调用.

验证 GPTR 深度研究功能的核心可调用性:
- DeepResearcher 类可导入无异常
- DeepResearcher 可实例化 (依赖全部 mock, 不连接外部服务)
- research() 最小调用 (mock LLM + mock 搜索) 不报错
- Settings 新增 deep_research_* 配置可读取

冒烟测试失败说明核心模块存在导入/实例化问题, 应在 CI 构建期就阻断.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import Settings

pytestmark = pytest.mark.unit


# ========== 模块可导入性 ==========


def test_deep_research_module_importable() -> None:
    """验证 src.skills.researcher.deep_research 模块可导入."""
    from src.skills.researcher.deep_research import DeepResearcher

    assert DeepResearcher is not None


def test_deep_researcher_class_has_required_methods() -> None:
    """验证 DeepResearcher 类含所有必要方法 (research/_assess_complexity/_generate_sub_queries)."""
    from src.skills.researcher.deep_research import DeepResearcher

    assert callable(getattr(DeepResearcher, "research", None))
    assert callable(getattr(DeepResearcher, "_assess_complexity", None))
    assert callable(getattr(DeepResearcher, "_generate_sub_queries", None))
    assert callable(getattr(DeepResearcher, "_research_sub_query", None))
    assert callable(getattr(DeepResearcher, "_process_research_results", None))
    assert callable(getattr(DeepResearcher, "_parse_search_queries", None))
    assert callable(getattr(DeepResearcher, "_parse_research_results", None))
    assert callable(getattr(DeepResearcher, "_build_next_query", None))
    assert callable(getattr(DeepResearcher, "_trim_context_to_word_limit", None))


# ========== 实例化 ==========


def test_deep_researcher_instantiable_with_mocks() -> None:
    """验证 DeepResearcher 可用 mock 依赖实例化."""
    from src.skills.researcher.deep_research import DeepResearcher

    settings = Settings(_env_file=None, mcp_strategy="disabled")
    mock_llm = MagicMock()
    mock_llm.achat = AsyncMock()
    mock_cm = MagicMock()
    mock_cm.get_similar_content = AsyncMock(return_value="ctx")

    researcher = DeepResearcher(
        settings=settings,
        llm=mock_llm,
        context_manager=mock_cm,
    )
    assert researcher is not None
    # 内部状态初始化
    assert researcher._visited_urls == set()
    assert researcher._learnings == set()
    assert researcher._citations == {}
    assert researcher._mcp is None


# ========== Settings 新增配置可读取 ==========


def test_deep_research_settings_readable() -> None:
    """验证 Settings 含 deep_research_* 配置字段且默认值正确."""
    settings = Settings(_env_file=None)

    # 功能 11: breadth=4 (对标 GPTR)
    assert settings.deep_research_breadth == 4
    # depth=2 默认
    assert settings.deep_research_depth == 2
    # concurrency=4
    assert settings.deep_research_concurrency == 4
    # 自适应深度默认开启
    assert settings.deep_research_adaptive is True
    # max_sub_queries 守卫: 42 (V4-P2-04, 支持 L9-L10: 5+10+20=35)
    assert settings.deep_research_max_sub_queries == 42
    # 每子查询 learnings 数量上限
    assert settings.deep_research_num_learnings == 3
    # reasoning_effort 默认 high
    assert settings.deep_research_reasoning_effort == "high"
    # max_context_words
    assert settings.max_context_words == 25_000


# ========== research() 最小调用不报错 ==========


@pytest.mark.asyncio
async def test_research_minimal_call_no_error() -> None:
    """验证 research() 最小调用 (mock LLM + mock 子查询) 不报错.

    使用 mock 替换 _generate_sub_queries 和 _research_sub_query,
    避免 LLM/搜索/抓取等外部依赖.
    """
    from src.skills.researcher.deep_research import DeepResearcher

    settings = Settings(_env_file=None, mcp_strategy="disabled")
    mock_llm = MagicMock()
    mock_llm.achat = AsyncMock()
    mock_cm = MagicMock()
    mock_cm.get_similar_content = AsyncMock(return_value="ctx")

    researcher = DeepResearcher(
        settings=settings,
        llm=mock_llm,
        context_manager=mock_cm,
    )

    async def mock_gen(query: str, breadth: int, **kwargs: Any) -> list[dict[str, str]]:
        return [{"query": f"q{i}", "researchGoal": f"g{i}"} for i in range(breadth)]

    async def mock_sub(sq: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "context": f"ctx-{sq}",
            "sources": [],
            "learnings": [],
            "followUpQuestions": [],
            "citations": {},
        }

    researcher._generate_sub_queries = mock_gen  # type: ignore[method-assign]
    researcher._research_sub_query = mock_sub  # type: ignore[method-assign]

    result = await researcher.research("smoke test query", breadth=2, depth=1)

    # 返回结构完整
    assert "query" in result
    assert "context" in result
    assert "sources" in result
    assert "learnings" in result
    assert "citations" in result
    assert "children" in result
    assert result["query"] == "smoke test query"
