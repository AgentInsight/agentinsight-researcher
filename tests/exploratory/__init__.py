"""探索性测试: 模拟人工测试的边界场景与降级路径.

- 探索性测试在 docker compose up -d 且全部容器 service_healthy 后执行
- 测试目标地址从环境变量 AGENT_URL 注入
- 测试数据隔离: session_id=test_explore_*

本目录用 marker=exploratory, 与 functional/api/e2e 区分:
- 边界场景测试 (空查询/超长查询/特殊字符/并发请求)
- 降级路径测试 (Qdrant 不可用/Redis 不可用/LLM 超时/TEI 限流)

注意: 探索性测试可能因环境抖动偶发失败, 不强制为合并门禁.
"""
