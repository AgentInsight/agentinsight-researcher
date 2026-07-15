// components/nav/mode-aware-content.tsx
"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { useNavStore } from "@/lib/nav-store";

/**
 * 模式感知内容容器
 *
 * 根据 nav-store 的 mode 切换内容:
 * - mode="agent": 显示主对话区 (children = ChatPage, 会话导航栏已集成在 ChatPage 内部)
 * - mode="mcp": 显示 MCP 服务设置页 (children 由路由切换为 /mcp)
 *
 * 路由同步: 监听 pathname 变化, 自动同步 mode 状态
 * - /agent/researcher/chat → mode="agent"
 * - /mcp/researcher/setting → mode="mcp"
 * 这解决了直接访问 URL (如刷新页面) 时 mode 与路由不一致的问题
 */
export function ModeAwareContent({ children }: { children: React.ReactNode }) {
  const { mode, setMode } = useNavStore();
  const pathname = usePathname();

  // 路由 → mode 同步 (解决刷新/直接访问 URL 时 mode 不一致问题)
  useEffect(() => {
    if (pathname?.startsWith("/mcp/") && mode !== "mcp") {
      setMode("mcp");
    } else if (pathname === "/agent/researcher/chat" && mode !== "agent") {
      setMode("agent");
    }
  }, [pathname, mode, setMode]);

  // 统一渲染: 会话导航栏已集成在 ChatPage 内部, 此处不再单独渲染
  return (
    <main className="flex-1 flex flex-col w-full md:w-auto overflow-hidden">
      {children}
    </main>
  );
}
