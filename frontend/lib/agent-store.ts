// lib/agent-store.ts
import { create } from "zustand";
import { persist } from "zustand/middleware";
import { AGENTS_CONFIG, getEnabledAgents, getAgentByName, type AgentConfig } from "./agents.config";

/**
 * 当前选中 Agent 状态
 * - 持久化到 localStorage (跨刷新保持)
 * - 当前仅 1 个 Agent 时, 选中固定为 defaultAgent
 *
 * 注意: 使用 skipHydration 避免水合不匹配
 * (Zustand persist 在 SSR 时无法读取 localStorage, 需客户端 hydrate)
 */
interface AgentState {
  currentAgent: string;
  setAgent: (name: string) => void;
  getCurrentAgent: () => AgentConfig;
}

export const useAgentStore = create<AgentState>()(
  persist(
    (set, get) => ({
      currentAgent: AGENTS_CONFIG.defaultAgent,
      setAgent: (name) => {
        // 仅允许切换到已启用的 Agent
        if (getAgentByName(name)?.enabled) {
          set({ currentAgent: name });
        }
      },
      getCurrentAgent: () => {
        const name = get().currentAgent;
        return getAgentByName(name) || getEnabledAgents()[0];
      },
    }),
    {
      name: "agent-storage",
      // 仅在客户端 persist, 避免 SSR 水合问题
      skipHydration: false,
    }
  )
);
