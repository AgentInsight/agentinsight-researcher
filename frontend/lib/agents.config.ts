// lib/agents.config.ts
/**
 * 多 Agent 配置 (支持两种部署模式)
 *
 * 部署模式 (由 NEXT_PUBLIC_DEPLOYMENT_MODE 环境变量控制):
 *   - server (默认): 服务器模式, 通过 Nginx 按 agentName 路径分发
 *       HTTP/SSE: /agent/{agentName}/* → Nginx → 对应后端
 *       WebSocket: /v1/ws/{agentName}/{sessionId} → Nginx → 对应后端
 *   - local: 本地开发模式, 浏览器直连后端端口 (无 Nginx)
 *       HTTP/SSE: http://localhost:{localPort}/*
 *       WebSocket: ws://localhost:{localPort}/v1/ws/{sessionId} (不含 agentName 段)
 *
 * 扩展步骤 (多 Agent):
 * 1. 在此数组追加 Agent 配置 (含 agentName + localPort)
 * 2. 在 docker-compose.yml 新增对应 Agent 服务
 * 3. 服务器: Nginx 配置在 map $agent_backend 新增对应端口映射
 * 4. 本地: 确保对应端口已映射到宿主机
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
   * 本地开发模式直连端口 (DEPLOYMENT_MODE=local 时使用)
   * 浏览器直接访问 http://localhost:{localPort}
   * 多 Agent 各自独立端口 (如 8066, 8067, 8068)
   */
  localPort: number;
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
      localPort: 8066,
    },
    // 未来扩展示例 (取消注释即启用, 需同步配置 docker-compose.yml 和 nginx.conf):
    // {
    //   name: "agentinsight-asksql",
    //   displayName: "SQL 查询智能体",
    //   description: "自然语言转 SQL 查询型 AI Agent",
    //   enabled: true,
    //   localPort: 8067,
    // },
  ],
  defaultAgent: "agentinsight-researcher",
};

/**
 * 部署模式 (构建时内联, 由 NEXT_PUBLIC_DEPLOYMENT_MODE 环境变量控制)
 * - server: 服务器模式, 通过 Nginx 按 agentName 路径分发
 * - local: 本地开发模式, 浏览器直连后端端口
 */
export const DEPLOYMENT_MODE: "server" | "local" =
  process.env.NEXT_PUBLIC_DEPLOYMENT_MODE === "local" ? "local" : "server";

/** 获取启用的 Agent 列表 */
export const getEnabledAgents = (): AgentConfig[] =>
  AGENTS_CONFIG.agents.filter((a) => a.enabled);

/** 根据 name 获取 Agent 配置 */
export const getAgentByName = (name: string): AgentConfig | undefined =>
  AGENTS_CONFIG.agents.find((a) => a.name === name);

/** 当前是否多 Agent (用于决定是否显示切换器) */
export const isMultiAgent = (): boolean => getEnabledAgents().length > 1;
