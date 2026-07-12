"""Researcher 技能组件.

Skill 组件清单:
- agent_creator.py      (LLM 动态角色生成器)
- research_conductor.py (研究总指挥)
- context_manager.py    (上下文管理器)
- source_curator.py     (来源策展器)
- report_generator.py   (报告生成器)
- publisher.py          (发布器)
- deep_research.py      (递归深度研究器)
- mcp_coordinator.py    (MCP 工具协调器)

行业适配采用 4 层机制:
- Prompt 层: AgentCreator.AUTO_AGENT_INSTRUCTIONS few-shot → LLM 动态生成角色
- Config 层: settings.agent_role 静态注入角色 persona (优先级高于 LLM)
- Retriever 层: searchers/ 含 arxiv/pubmed/semantic_scholar 等专业数据源
- MCP 层: MCP_SERVERS 注册行业专用工具服务器
禁止再引入基于行业分类器的实现 (YAML prompt 字典 / bootstrap 脚本 / if-else 行业分支).
"""
