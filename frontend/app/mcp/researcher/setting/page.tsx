// app/mcp/researcher/setting/page.tsx
"use client";

import { useState } from "react";
import { useAgentStore } from "@/lib/agent-store";
import { useNavStore } from "@/lib/nav-store";
import { McpConfigPanel } from "@/components/settings/mcp-config-panel";
import { Blocks, ChevronRight } from "lucide-react";
import { Tooltip } from "@/components/ui/tooltip";

/**
 * MCP 服务设置主区域 (mode=mcp 时显示, 路由 /mcp/researcher/setting)
 *
 * 3 区域 Flexbox 布局:
 * - 顶部 (flex-none): 图标 + 智能体名字居中作为标题 (无边框, 用留白分隔)
 * - Tab 栏 (flex-none): 多 Tab 切换 (当前只有 "MCP 服务", 后续可扩展)
 * - Tab 内容区 (flex-1 min-h-0): 根据选中 Tab 渲染对应内容
 *   - "MCP 服务" Tab: McpConfigPanel (左侧内容 + 右侧竖向 "我的"/"仓库" Tab)
 *   - 后续可新增其他 Tab
 *
 * 与 mode=agent 时的主对话区域互斥 (由 layout.tsx 根据 mode 切换)
 */

/** 主区域 Tab 类型 (后续可扩展) */
type MainTab = "mcp";

export default function McpPage() {
  const { getCurrentAgent } = useAgentStore();
  const { agentListNavCollapsed, toggleAgentListNav } = useNavStore();
  const currentAgent = getCurrentAgent();
  const [activeTab, setActiveTab] = useState<MainTab>("mcp");

  return (
    <div className="flex flex-col h-full">
      {/* ===== 顶部: 图标 + 智能体名字居中作为标题 (无分隔线, 与中部融为一体) ===== */}
      {/* 任务1: 改用 flex justify-between + 左右 w-24 占位, 与 ChatPage 顶部栏布局完全一致 */}
      {/* 展开按钮在 flex 流内 (非 absolute), items-center 让按钮垂直居中, 中心 23px 与其他按钮对齐 */}
      <div
        className="flex-none flex items-center justify-between px-3 py-2.5"
        style={{
          backgroundColor: "var(--bg-card)",
        }}
      >
        {/* 左侧: 智能体导航栏展开按钮(收缩时), w-24 与 ChatPage 左侧占位一致 */}
        <div className="flex items-center gap-1 w-24">
          {agentListNavCollapsed && (
            <Tooltip content="展开智能体导航栏">
              <button
                onClick={toggleAgentListNav}
                className="p-1.5 rounded-md hover:bg-hover transition-colors"
                style={{ color: "var(--text-secondary)" }}
                aria-label="展开智能体导航栏"
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </Tooltip>
          )}
        </div>
        {/* 中间: 智能体名字居中 */}
        <Tooltip content={currentAgent?.displayName || ""}>
          <h1
            className="text-sm font-semibold truncate flex items-center gap-1.5"
            style={{ color: "var(--text-primary)" }}
          >
            <Blocks
              className="h-3.5 w-3.5 flex-shrink-0"
              style={{ color: "var(--brand-primary)" }}
            />
            <span>{currentAgent?.displayName || "智能体"}</span>
          </h1>
        </Tooltip>
        {/* 右侧: w-24 占位保持标题居中 */}
        <div className="w-24" />
      </div>

      {/* ===== Tab 栏 (横向, 后续可扩展) ===== */}
      <div
        className="flex-none flex items-center px-4 gap-1"
        style={{
          backgroundColor: "var(--bg-card)",
          borderBottom: "1px solid var(--border-color-light)",
        }}
      >
        <TabButton
          label="MCP服务"
          isActive={activeTab === "mcp"}
          onClick={() => setActiveTab("mcp")}
        />
        {/* 后续可在此新增其他 Tab 按钮 */}
      </div>

      {/* ===== Tab 内容区 (flex-1 自适应) ===== */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {activeTab === "mcp" && <McpConfigPanel />}
        {/* 后续可在此新增其他 Tab 内容 */}
      </div>
    </div>
  );
}

/** Tab 按钮 (Linear 风格: 底部指示条) */
function TabButton({
  label,
  isActive,
  onClick,
}: {
  label: string;
  isActive: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="relative px-4 py-2.5 text-sm font-medium transition-colors"
      style={{
        color: isActive ? "var(--brand-primary)" : "var(--text-secondary)",
      }}
    >
      {label}
      {isActive && (
        <span
          className="absolute left-0 right-0 bottom-0 h-0.5"
          style={{ backgroundColor: "var(--brand-primary)" }}
        />
      )}
    </button>
  );
}
