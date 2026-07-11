"""单元测试: ResearcherSupervisor 多 Agent 协作 Supervisor.

验证 src/agents/researcher/supervisor.py:
- route(): Supervisor 路由决策, 返回 "researcher"|"reviewer"|"writer"|"publisher"|"END"
- max_iterations 守卫: iteration_count >= graph_max_iterations → "END"
- 状态机路由: 按 status/contexts/curated_sources/report_md/report_format 决策下一节点

注意: Supervisor.route 返回值为 "researcher"|"reviewer"|"writer"|"publisher"|"END",
不含 "revision"/"publish". 修订循环 (revision) 由图级条件边处理 (见 multi_agent_builder),
发布 (publish) 对应 route() 返回 "publisher".

多 Agent 协作限 Supervisor 模式, max_iterations 硬上限.
单元测试不依赖外部服务.
"""

from __future__ import annotations

import pytest

from src.agents.researcher.supervisor import ResearcherSupervisor
from src.config.settings import Settings
from src.graph.state import ResearcherState

pytestmark = pytest.mark.unit


@pytest.fixture()
def settings() -> Settings:
    """构造最小 Settings (跳过 .env 加载, curate_sources 默认 False)."""
    return Settings(_env_file=None)


@pytest.fixture()
def base_state() -> ResearcherState:
    """基础研究状态 (含隔离键)."""
    return {
        "query": "分析新能源汽车市场",
        "session_id": "test-session",
        "user_id": "test-user",
        "agent_id": "agentinsight-researcher",
    }


# ========== ResearcherSupervisor.route ==========


def test_supervisor_routes_to_end(
    settings: Settings,
    base_state: ResearcherState,
) -> None:
    """测试 iteration_count >= graph_max_iterations 时强制返回 "END".

    max_iterations 为硬上限, 由节点计数器 + 条件边强制,
    不可软超时. Supervisor.route 首先检查此守卫.
    """
    # 设置 iteration_count 达到上限 (默认 graph_max_iterations=10)
    base_state["iteration_count"] = settings.graph_max_iterations
    base_state["status"] = "running"
    base_state["contexts"] = ["上下文1"]
    base_state["report_md"] = "# 报告"

    supervisor = ResearcherSupervisor(settings=settings)
    route = supervisor.route(base_state)

    assert route == "END"


def test_supervisor_routes_to_revision(
    base_state: ResearcherState,
) -> None:
    """测试有上下文但无报告时路由到 "writer" (修订/生成步骤).

    Supervisor.route 不返回 "revision"; 修订循环由图级条件边处理.
    当有 contexts 但无 report_md 时, route 返回 "writer" (报告生成/修订节点),
    这是修订循环的入口. 此测试验证修订路径的路由条件.
    """
    settings = Settings(_env_file=None, curate_sources=False)
    base_state["status"] = "running"
    base_state["contexts"] = ["上下文1", "上下文2"]
    base_state["report_md"] = ""  # 无报告 → 需生成/修订

    supervisor = ResearcherSupervisor(settings=settings)
    route = supervisor.route(base_state)

    assert route == "writer"


def test_supervisor_routes_to_publish(
    base_state: ResearcherState,
) -> None:
    """测试有报告且 report_format != "markdown" 时路由到 "publisher".

    Supervisor.route 不返回 "publish"; 发布由 "publisher" 节点处理.
    当 report_md 存在且 report_format 为非 markdown (如 pdf/html) 时,
    route 返回 "publisher" (格式转换节点).
    """
    settings = Settings(_env_file=None, curate_sources=True)
    # 有报告, report_format=pdf, 且有 curated_sources → "publisher"
    base_state["status"] = "running"
    base_state["contexts"] = ["上下文1"]
    base_state["curated_sources"] = [{"title": "src1"}]
    base_state["report_md"] = "# 报告\n\n正文内容"
    base_state["report_format"] = "pdf"

    supervisor = ResearcherSupervisor(settings=settings)
    route = supervisor.route(base_state)

    assert route == "publisher"


def test_supervisor_routes_to_researcher_when_pending(
    settings: Settings,
    base_state: ResearcherState,
) -> None:
    """测试 status="pending" 时路由到 "researcher" (初始研究)."""
    base_state["status"] = "pending"

    supervisor = ResearcherSupervisor(settings=settings)
    route = supervisor.route(base_state)

    assert route == "researcher"


def test_supervisor_routes_to_end_when_report_markdown_complete(
    base_state: ResearcherState,
) -> None:
    """测试有 markdown 报告且无格式转换需求时返回 "END" (流程完成)."""
    settings = Settings(_env_file=None, curate_sources=True)
    base_state["status"] = "running"
    base_state["contexts"] = ["上下文1"]
    base_state["curated_sources"] = [{"title": "src1"}]
    base_state["report_md"] = "# 报告\n\n正文"
    base_state["report_format"] = "markdown"  # 默认格式, 无需转换

    supervisor = ResearcherSupervisor(settings=settings)
    route = supervisor.route(base_state)

    assert route == "END"
