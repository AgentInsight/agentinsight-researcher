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
 * 侧边栏折叠状态:
 * - leftSidebarCollapsed: 左侧导航栏是否折叠 (仅平台名+图标)
 * - sessionNavVisible: 会话导航栏是否显示 (默认 false, 集成在主对话区内, 由顶部图标切换)
 */
type NavMode = "agent" | "mcp";
type SlideInPanel = "history" | "settings" | null;

interface NavState {
  mode: NavMode;
  leftSidebarCollapsed: boolean;
  sessionNavVisible: boolean;
  slideInPanel: SlideInPanel;

  setMode: (mode: NavMode) => void;
  toggleLeftSidebar: () => void;
  toggleSessionNav: () => void;
  setSessionNavVisible: (visible: boolean) => void;
  openPanel: (panel: SlideInPanel) => void;
  closePanel: () => void;
  togglePanel: (panel: Exclude<SlideInPanel, null>) => void;
}

export const useNavStore = create<NavState>()((set, get) => ({
  mode: "agent",
  leftSidebarCollapsed: false,
  sessionNavVisible: false,
  slideInPanel: null,

  setMode: (mode) =>
    set({ mode, slideInPanel: null }),

  toggleLeftSidebar: () =>
    set((s) => ({ leftSidebarCollapsed: !s.leftSidebarCollapsed })),

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
