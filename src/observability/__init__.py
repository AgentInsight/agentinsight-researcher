"""AgentInsight SDK 封装.

AGENTS.md 第 10 章: 统一使用 agentinsight-sdk, 由 observability/tracing.py 统一封装.
追踪调用方式唯一: 异步上下文管理器 async with trace_xxx(...) as span.
禁用观察者模式; @agentinsight.observe 装饰器已弃用.
"""
