// lib/nav-store.ts
import { create } from "zustand";

/**
 * 导航模式与面板状态管理
 *
 * 模式 (mode):
 * - "agent": 智能体模式 → 智能体导航右侧为主对话区 (会话导航栏集成在主对话区内)
 * - "mcp": MCP 服务模式 → 智能体导航右侧为 MCP 服务设置 (无会话导航)
 *
 * 滑入面板 (slideInPanel):
 * - null: 无面板
 * - "history": 历史报告面板
 * - "settings": 设置面板
 *
 * 3 个导航栏折叠状态:
 * - agentNavCollapsed: 第 1 栏 (AgentNav) — 收缩时收缩所有 3 个导航栏, 展开时只展开自己
 * - agentListNavCollapsed: 第 2 栏 (AgentListNav) — 只收缩/展开自己
 * - sessionNavVisible: 第 3 栏 (ChatSidebar) — 默认 false, 集成在主对话区内, 由顶部图标切换
 *
 * 交互规则 (用户需求):
 * - AgentNav 展开态显示 <, 点击收缩所有 3 个导航栏
 * - AgentNav 收缩态显示 >, 点击只展开 AgentNav 自己 (不影响其他 2 栏)
 * - AgentListNav 展开态显示 <, 点击只收缩自己
 * - AgentListNav 收缩态显示 >, 点击只展开自己
 */
type NavMode = "agent" | "mcp";
type SlideInPanel = "history" | "settings" | null;

interface NavState {
  mode: NavMode;
  agentNavCollapsed: boolean;
  agentListNavCollapsed: boolean;
  sessionNavVisible: boolean;
  slideInPanel: SlideInPanel;

  setMode: (mode: NavMode) => void;
  toggleAgentNav: () => void;
  toggleAgentListNav: () => void;
  toggleSessionNav: () => void;
  setSessionNavVisible: (visible: boolean) => void;
  openPanel: (panel: SlideInPanel) => void;
  closePanel: () => void;
  togglePanel: (panel: Exclude<SlideInPanel, null>) => void;
}

export const useNavStore = create<NavState>()((set, get) => ({
  mode: "agent",
  agentNavCollapsed: false,
  agentListNavCollapsed: false,
  sessionNavVisible: false,
  slideInPanel: null,

  setMode: (mode) =>
    set({ mode, slideInPanel: null }),

  // AgentNav: 展开时收缩所有 3 个导航栏, 收缩时只展开自己
  toggleAgentNav: () => {
    const { agentNavCollapsed } = get();
    if (!agentNavCollapsed) {
      // 展开 → 收缩: 收缩所有 3 个导航栏
      set({ agentNavCollapsed: true, agentListNavCollapsed: true, sessionNavVisible: false });
    } else {
      // 收缩 → 展开: 只展开自己
      set({ agentNavCollapsed: false });
    }
  },

  // AgentListNav: 只收缩/展开自己
  toggleAgentListNav: () =>
    set((s) => ({ agentListNavCollapsed: !s.agentListNavCollapsed })),

  toggleSessionNav: () =>
    set((s) => ({ sessionNavVisible: !s.sessionNavVisible })),

  setSessionNavVisible: (visible) => set({ sessionNavVisible: visible }),

  openPanel: (panel) => set({ slideInPanel: panel }),
  closePanel: () => set({ slideInPanel: null }),
  togglePanel: (panel) =>
    set((s) => ({
      slideInPanel: s.slideInPanel === panel ? null : panel,
    })),
}));
