// lib/agents.config.ts
/**
 * 多 Agent 配置 (方案B: Nginx 按 agent 路径分发)
 * - 当前仅 1 个 Agent (agentinsight-researcher)
 * - 未来添加新 Agent 只需在 agents 数组追加条目
 * - SSE/HTTP 通过 /api/proxy/{agentName}/* 路由, Next.js proxy 根据 agentName 选择后端
 * - WebSocket 通过 /v1/ws/{agentName}/{sessionId} 路由, Nginx 根据 agentName 选择后端
 *
 * 扩展步骤 (多 Agent):
 * 1. 在此数组追加 Agent 配置 (含 agentName 和 apiUrl)
 * 2. 在 docker-compose.yml 新增对应 Agent 服务
 * 3. 在 .env.frontend 新增对应 AGENT_<NAME>_API_URL 运行时变量
 * 4. Nginx 配置新增对应 /v1/ws/<agentName>/ location 块
 */

export interface AgentConfig {
  /** Agent 唯一标识 (与后端 AGENT_NAME 一致, 用于路由) */
  name: string;
  /** 前端显示名称 */
  displayName: string;
  /** Agent 描述 */
  description: string;
  /** 是否启用 (false 时不在切换器显示) */
  enabled: boolean;
  /**
   * 后端 API 地址 (Docker compose 网络内服务名 + 端口)
   * 用于 Next.js /api/proxy route handler 路由到对应后端
   * 若留空, 则使用环境变量 AGENT_<NAME_UPPER>_API_URL
   */
  apiUrl?: string;
}

export const AGENTS_CONFIG: {
  agents: AgentConfig[];
  defaultAgent: string;
} = {
  agents: [
    {
      name: "agentinsight-researcher",
      displayName: "研究分析智能体",
      description: "深度研究型 AI Agent",
      enabled: true,
      // apiUrl 留空, 由 proxy route 从 AGENT_RESEARCHER_API_URL 环境变量读取
    },
    // 未来扩展示例 (取消注释即启用, 需同步配置 .env 和 docker-compose.yml 和 nginx.conf):
    // {
    //   name: "agentinsight-writer",
    //   displayName: "写作 Agent",
    //   description: "内容创作型 AI Agent",
    //   enabled: true,
    //   // apiUrl: "http://agent-writer:8067",  // 或由 AGENT_WRITER_API_URL 环境变量提供
    // },
    // {
    //   name: "agentinsight-analyst",
    //   displayName: "分析 Agent",
    //   description: "数据分析型 AI Agent",
    //   enabled: true,
    // },
  ],
  defaultAgent: "agentinsight-researcher",
};

/** 获取启用的 Agent 列表 */
export const getEnabledAgents = (): AgentConfig[] =>
  AGENTS_CONFIG.agents.filter((a) => a.enabled);

/** 根据 name 获取 Agent 配置 */
export const getAgentByName = (name: string): AgentConfig | undefined =>
  AGENTS_CONFIG.agents.find((a) => a.name === name);

/** 当前是否多 Agent (用于决定是否显示切换器) */
export const isMultiAgent = (): boolean => getEnabledAgents().length > 1;
