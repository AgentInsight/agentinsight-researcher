// app/(chat)/layout.tsx (响应式版本)
import { ChatSidebar } from "@/components/chat/chat-sidebar";
import { AgentSwitcher } from "@/components/agent/agent-switcher";
import { MobileSidebar } from "@/components/chat/mobile-sidebar";

/**
 * 聊天布局 (响应式)
 * - 桌面端 (md+): 固定侧边栏 + 主内容区
 * - 移动端: 抽屉导航 + 全屏主内容区
 */
export default function ChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex h-screen">
      {/* 桌面端侧边栏 (md+ 显示) */}
      <aside className="hidden md:flex w-64 border-r flex-col">
        <div className="p-3 border-b">
          <AgentSwitcher />
        </div>
        <ChatSidebar />
      </aside>

      {/* 移动端抽屉导航 (md 以下显示) */}
      <MobileSidebar />

      {/* 主内容区 */}
      <main className="flex-1 flex flex-col w-full md:w-auto">
        {children}
      </main>
    </div>
  );
}
