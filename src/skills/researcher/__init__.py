"""Researcher 技能组件.

设计参考 skills/ 的 6+1 个 Skill:
- agent_creator.py      (设计参考: actions/agent_creator.py + prompts.py auto_agent_instructions)
- research_conductor.py (设计参考: skills/researcher.py)
- context_manager.py    (设计参考: skills/context_manager.py)
- browser_manager.py    (设计参考: skills/browser.py)
- source_curator.py     (设计参考: skills/curator.py)
- report_generator.py   (设计参考: skills/writer.py)
- publisher.py          (设计参考: multi_agents/agents/publisher.py)
- deep_research.py      (设计参考: skills/deep_research.py, v2)
- mcp_coordinator.py    (设计参考: mcp/)

行业适配采用 4 层机制:
- Prompt 层: AgentCreator.AUTO_AGENT_INSTRUCTIONS few-shot → LLM 动态生成角色
- Config 层: settings.agent_role 静态注入角色 persona (优先级高于 LLM)
- Retriever 层: searchers/ 含 arxiv/pubmed/semantic_scholar 等专业数据源
- MCP 层: MCP_SERVERS 注册行业专用工具服务器
禁止再引入基于行业分类器的实现 (YAML prompt 字典 / bootstrap 脚本 / if-else 行业分支).
"""
