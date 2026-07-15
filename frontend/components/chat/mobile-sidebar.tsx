// components/chat/mobile-sidebar.tsx
"use client";

import { useState } from "react";
import { Menu, X } from "lucide-react";
import { ChatSidebar } from "./chat-sidebar";
import { AgentNav } from "@/components/agent/agent-nav";
import { AgentListNav } from "@/components/nav/agent-list-nav";
import { useNavStore } from "@/lib/nav-store";

/**
 * 移动端抽屉导航 (响应式)
 *
 * 移动端 (<md) 折叠为汉堡菜单:
 * - 抽屉内显示: 左侧导航栏 (AgentNav) + 智能体导航栏 (AgentListNav) + 会话导航栏 (ChatSidebar, 仅 agent 模式)
 * - 桌面端 (md+) 始终显示侧边栏, 此组件不渲染
 */
export function MobileSidebar() {
  const [open, setOpen] = useState(false);
  const { mode } = useNavStore();

  return (
    <>
      {/* 移动端汉堡菜单按钮 */}
      <button
        onClick={() => setOpen(true)}
        className="md:hidden fixed top-3 left-3 z-30 p-2 rounded-md shadow-sm"
        style={{ backgroundColor: "var(--bg-card)", color: "var(--text-primary)" }}
        aria-label="打开菜单"
      >
        <Menu className="h-5 w-5" />
      </button>

      {/* 抽屉遮罩 */}
      {open && (
        <div
          className="md:hidden fixed inset-0 bg-black/50 z-40"
          onClick={() => setOpen(false)}
        />
      )}

      {/* 抽屉侧边栏 */}
      <aside
        className={`md:hidden fixed left-0 top-0 bottom-0 z-50 transform transition-transform ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
        style={{ backgroundColor: "var(--bg-sidebar)" }}
      >
        <div className="flex h-full">
          {/* 关闭按钮 (悬浮在右上角) */}
          <button
            onClick={() => setOpen(false)}
            className="absolute top-2 right-2 z-10 p-1.5 rounded-md hover:bg-hover"
            style={{ color: "var(--text-secondary)" }}
            aria-label="关闭菜单"
          >
            <X className="h-5 w-5" />
          </button>

          {/* 左侧导航栏 */}
          <AgentNav />

          {/* 智能体导航栏 */}
          <AgentListNav />

          {/* 会话导航栏 (仅 agent 模式) */}
          {mode === "agent" && <ChatSidebar />}
        </div>
      </aside>
    </>
  );
}
