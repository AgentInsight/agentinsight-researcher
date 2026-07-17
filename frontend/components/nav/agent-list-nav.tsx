// components/nav/agent-list-nav.tsx
"use client";

import { useState, useMemo } from "react";
import { useAgentStore } from "@/lib/agent-store";
import { useNavStore } from "@/lib/nav-store";
import { getEnabledAgents, type AgentConfig } from "@/lib/agents.config";
import { Search, Bot, Blocks, ChevronLeft } from "lucide-react";
import { Tooltip } from "@/components/ui/tooltip";

/**
 * 智能体导航栏 (第 2 栏, 可收缩/展开)
 *
 * 底色: --bg-card (与主框体一致, 视觉一体)
 *
 * 收缩/展开:
 * - 展开态 (width: 240): 标题居中 + < 按钮 (absolute 右侧)
 * - 收缩态 (width: 0): 整个导航栏隐藏 (overflow: hidden)
 *   展开按钮浮在主框体顶部 (由 ChatPage/McpPage 顶部栏渲染)
 *
 * 3 区域 Flexbox 布局 (展开态):
 * - 顶部 (flex-none): 标题居中 + 收缩按钮 (absolute 右侧)
 * - 中间 (flex-none): 搜索框
 * - 底部 (flex-1 min-h-0): 智能体列表
 */
export function AgentListNav() {
  const { mode, agentListNavCollapsed, toggleAgentListNav } = useNavStore();
  const { currentAgent, setAgent } = useAgentStore();
  const [query, setQuery] = useState("");

  const agents = getEnabledAgents();

  const filteredAgents = useMemo(() => {
    if (!query.trim()) return agents;
    const q = query.toLowerCase().trim();
    return agents.filter(
      (a) =>
        a.displayName.toLowerCase().includes(q) ||
        a.name.toLowerCase().includes(q) ||
        a.description.toLowerCase().includes(q)
    );
  }, [agents, query]);

  const title = mode === "agent" ? "智能体" : "智能体配置";

  return (
    <div
      className="flex flex-col h-full overflow-hidden"
      style={{
        backgroundColor: "var(--bg-card)",
        width: agentListNavCollapsed ? 0 : 240,
        transition: "width 0.2s ease",
      }}
    >
      {/* ===== 顶部: 标题居中 + 收缩按钮 (grid 布局, py-2.5 与 ChatPage/AgentNav 顶部对齐) ===== */}
      {/* 任务1+2: Tooltip 返回 inline-flex div 作为 grid item, justify-self 必须写在外层 wrapper div 上, 否则按钮靠左贴标题 */}
      <div className="flex-none grid grid-cols-[1fr_auto_1fr] items-center gap-2 px-3 py-2.5">
        <div />
        <h2
          className="justify-self-center text-sm font-semibold"
          style={{ color: "var(--text-primary)" }}
        >
          {title}
        </h2>
        {/* 任务2: 按钮推到最右 — justify-self-end 写在 wrapper div 上 */}
        <div className="justify-self-end">
          <Tooltip content="折叠导航栏">
            <button
              onClick={toggleAgentListNav}
              className="p-1.5 rounded-md hover:bg-hover transition-colors"
              style={{ color: "var(--text-tertiary)" }}
              aria-label="折叠导航栏"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
            </button>
          </Tooltip>
        </div>
      </div>

      {/* ===== 搜索框 (任务4: 背景色改为透明继承导航栏 --bg-card, 用边框区分) ===== */}
      <div className="flex-none flex items-center px-3 pb-2.5">
        <div
          className="flex items-center gap-2 w-full px-2.5 py-1.5 rounded-md"
          style={{
            backgroundColor: "transparent",
            border: "1px solid var(--border-color-light)",
          }}
        >
          <Search
            className="h-3.5 w-3.5 flex-shrink-0"
            style={{ color: "var(--text-tertiary)" }}
          />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索智能体..."
            className="flex-1 bg-transparent border-none outline-none text-sm"
            style={{ color: "var(--text-primary)" }}
          />
          {query && (
            <Tooltip content="清空搜索">
              <button
                onClick={() => setQuery("")}
                className="text-xs"
                style={{ color: "var(--text-tertiary)" }}
                aria-label="清空"
              >
                ✕
              </button>
            </Tooltip>
          )}
        </div>
      </div>

      {/* ===== 智能体列表 (flex-1 自适应) ===== */}
      <div className="flex-1 overflow-y-auto px-2 pb-2 min-h-0">
        {filteredAgents.length === 0 ? (
          <div
            className="text-center text-sm py-8"
            style={{ color: "var(--text-tertiary)" }}
          >
            <Bot className="h-8 w-8 mx-auto mb-2 opacity-40" />
            <p>未找到匹配的智能体</p>
          </div>
        ) : (
          filteredAgents.map((agent) => (
            <AgentCard
              key={agent.name}
              agent={agent}
              isActive={agent.name === currentAgent}
              mode={mode}
              onClick={() => setAgent(agent.name)}
            />
          ))
        )}
      </div>
    </div>
  );
}

/** 智能体卡片 (单行, 中文 + 图标, 无子标题) */
function AgentCard({
  agent,
  isActive,
  mode,
  onClick,
}: {
  agent: AgentConfig;
  isActive: boolean;
  mode: "agent" | "mcp";
  onClick: () => void;
}) {
  const Icon = mode === "agent" ? Bot : Blocks;

  return (
    <button
      onClick={onClick}
      className="w-full flex items-center gap-2.5 px-2.5 py-2.5 rounded-md transition-colors text-left mb-0.5"
      style={{
        backgroundColor: isActive ? "var(--bg-active)" : "transparent",
      }}
    >
      <div
        className="w-8 h-8 rounded-md flex items-center justify-center text-xs font-medium flex-shrink-0"
        style={{
          backgroundColor: isActive
            ? "var(--brand-primary)"
            : "var(--text-tertiary)",
          color: "var(--text-on-brand)",
        }}
      >
        <Icon className="h-4 w-4" />
      </div>
      <div className="flex-1 min-w-0">
        <div
          className="text-sm font-medium truncate"
          style={{
            color: isActive ? "var(--brand-primary)" : "var(--text-primary)",
          }}
        >
          {agent.displayName}
        </div>
      </div>
    </button>
  );
}
