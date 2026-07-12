"""AG2 (autogen) 框架多 Agent 实现.

作为 LangGraph 的可选替代方案, 默认关闭 (settings.ag2_enabled=False).

设计要点:
- AG2 (autogen) 是可选依赖, 未安装时本模块仍可导入.
- AG2 仅作为编排层 (GroupChat + GroupChatManager), 所有 LLM 调用经 LLMClient (LiteLLM).
- 复用现有 Skill 组件 (ResearchConductor / ReportGenerator / Reviewer / Publisher), 不重复实现.
- 不修改现有 LangGraph 代码, 与 LangGraph 并存.

模块结构:
- agents.py: 4 个角色 (Researcher/Writer/Reviewer/Publisher) 的 system_prompt 与消息协议.
- orchestrator.py: AG2Orchestrator, 用 ConversableAgent + GroupChat 编排 4 个角色.

注: AG2 默认关闭, 无生产调用.
    如需启用 AG2, 可直接实例化 AG2Orchestrator 并调用其 run 方法.
"""
