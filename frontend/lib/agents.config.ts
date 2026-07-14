// lib/agents.config.ts
/**
 * 多 Agent 配置
 * - 当前仅 1 个 Agent (agentinsight-researcher)
 * - 未来添加新 Agent 只需在 agents 数组追加条目
 * - Agent API 地址从环境变量读取, 不可在前端修改
 *
 * 重要: 环境变量必须使用 NEXT_PUBLIC_ 前缀, 否则客户端组件无法读取
 * (agents.config.ts 会在客户端组件中被引用)
 *
 * 扩展步骤:
 * 1. 在此数组追加 Agent 配置
 * 2. 在 docker-compose.yml 新增对应 Agent 服务
 * 3. 在 .env 新增对应 NEXT_PUBLIC_AGENT_<NAME>_API_URL
 * 4. 在 Dockerfile 构建时通过 ARG 注入新的 NEXT_PUBLIC_ 变量
 */

export interface AgentConfig {
  /** Agent 唯一标识 (与后端 AGENT_NAME 一致) */
  name: string;
  /** 前端显示名称 */
  displayName: string;
  /** Agent API 地址 (从 NEXT_PUBLIC_ 环境变量读取, 客户端可见) */
  apiUrl: string;
  /** Agent 描述 */
  description: string;
  /** 是否启用 (false 时不在切换器显示) */
  enabled: boolean;
}

export const AGENTS_CONFIG: {
  agents: AgentConfig[];
  defaultAgent: string;
} = {
  agents: [
    {
      name: "agentinsight-researcher",
      displayName: "研究 Agent",
      // 必须使用 NEXT_PUBLIC_ 前缀, 客户端组件可读取
      apiUrl: process.env.NEXT_PUBLIC_AGENT_RESEARCHER_API_URL || "http://localhost:8066",
      description: "深度研究型 AI Agent",
      enabled: true,
    },
    // 未来扩展示例 (取消注释即启用, 需同步配置 .env 和 docker-compose.yml):
    // {
    //   name: "agentinsight-writer",
    //   displayName: "写作 Agent",
    //   apiUrl: process.env.NEXT_PUBLIC_AGENT_WRITER_API_URL || "http://localhost:8067",
    //   description: "内容创作型 AI Agent",
    //   enabled: true,
    // },
    // {
    //   name: "agentinsight-analyst",
    //   displayName: "分析 Agent",
    //   apiUrl: process.env.NEXT_PUBLIC_AGENT_ANALYST_API_URL || "http://localhost:8068",
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
