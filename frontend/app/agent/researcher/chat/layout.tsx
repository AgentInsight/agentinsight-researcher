// app/agent/researcher/chat/layout.tsx
/**
 * 聊天布局 (3 栏布局)
 *
 * 桌面端 (md+):
 * - 第 1 栏: 左侧导航栏 (AgentNav) - 平台名/模式切换/用户信息
 * - 第 2 栏: 智能体导航栏 (AgentListNav) - 标题/搜索框/智能体列表
 * - 第 3 栏: 主内容区 (ModeAwareContent) - 根据路由渲染 ChatPage 或 McpPage
 *   - mode=agent: ChatPage 内部集成了会话导航栏 (由顶部图标控制显示/隐藏)
 *   - mode=mcp: McpPage (MCP 服务设置, 无会话导航栏)
 *
 * 移动端: 抽屉导航 (MobileSidebar)
 */
import { AgentNav } from "@/components/agent/agent-nav";
import { AgentListNav } from "@/components/nav/agent-list-nav";
import { MobileSidebar } from "@/components/chat/mobile-sidebar";
import { ModeAwareContent } from "@/components/nav/mode-aware-content";

export default function ChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div
      className="flex h-[100dvh]"
      style={{ backgroundColor: "var(--bg-page)" }}
    >
      {/* ===== 桌面端 (md+): 3 栏布局 ===== */}

      {/* 第 1 栏: 左侧导航栏 */}
      <div className="hidden md:flex flex-shrink-0">
        <AgentNav />
      </div>

      {/* 第 2 栏: 智能体导航栏 (可收缩) */}
      <div className="hidden md:flex flex-shrink-0">
        <AgentListNav />
      </div>

      {/* 第 3 栏: 主内容区 (路由同步 mode, 会话导航栏集成在 ChatPage 内部) */}
      <ModeAwareContent>{children}</ModeAwareContent>

      {/* 移动端抽屉导航 */}
      <MobileSidebar />
    </div>
  );
}
