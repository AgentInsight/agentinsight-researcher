// components/chat/mobile-sidebar.tsx
"use client";

import { useState } from "react";
import { Menu, X } from "lucide-react";
import { ChatSidebar } from "./chat-sidebar";
import { AgentSwitcher } from "@/components/agent/agent-switcher";

/**
 * 移动端抽屉导航
 * - 移动端折叠为汉堡菜单
 * - 桌面端 (md+) 始终显示侧边栏
 */
export function MobileSidebar() {
  const [open, setOpen] = useState(false);

  return (
    <>
      {/* 移动端汉堡菜单按钮 */}
      <button
        onClick={() => setOpen(true)}
        className="md:hidden fixed top-4 left-4 z-30 p-2 bg-white rounded shadow"
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
        className={`md:hidden fixed left-0 top-0 bottom-0 w-64 bg-white z-50 transform transition-transform ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="p-3 border-b flex justify-between items-center">
          <AgentSwitcher />
          <button onClick={() => setOpen(false)}>
            <X className="h-5 w-5" />
          </button>
        </div>
        <ChatSidebar />
      </aside>
    </>
  );
}
