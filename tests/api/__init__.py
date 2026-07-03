"""API 测试 (OpenAI 兼容端点).

AGENTS.md 第 13 章: 必须覆盖 /v1/chat/completions 流式 SSE + 非流式 + 错误码.
包含携带 Bearer JWT Token 与不携带两种场景 (验证第 8 章身份解析与数据隔离).
"""
