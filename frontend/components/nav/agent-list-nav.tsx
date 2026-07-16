// components/nav/agent-list-nav.tsx
"use client";

import { useState, useMemo } from "react";
import { useAgentStore } from "@/lib/agent-store";
import { useNavStore } from "@/lib/nav-store";
import { getEnabledAgents, type AgentConfig } from "@/lib/agents.config";
import { Search, Bot, Blocks } from "lucide-react";
import { Tooltip } from "@/components/ui/tooltip";

/**
 * 智能体导航栏 (无硬边框, 与左侧导航栏 / 会话侧边栏统一 --bg-sidebar 浅灰,
 * 共同作为侧边栏区域与主对话区 --bg-card 白色形成层次)
 *
 * 3 区域 Flexbox 布局:
 * - 顶部 (flex-none): 标题居中, 无子标题
 *   - mode=agent: "智能体"
 *   - mode=mcp: "智能体配置"
 * - 中间 (flex-none): 搜索框
 * - 底部 (flex-1 min-h-0): 智能体列表 (中文显示 + 图标, 无子标题)
 */
export function AgentListNav() {
  const { mode } = useNavStore();
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
      className="flex flex-col h-full"
      style={{
        backgroundColor: "var(--bg-sidebar)",
        width: 240,
      }}
    >
      {/* ===== 顶部: 标题居中 ===== */}
      <div
        className="flex-none flex items-center justify-center px-4 py-3.5"
      >
        <h2
          className="text-sm font-semibold text-center"
          style={{ color: "var(--text-primary)" }}
        >
          {title}
        </h2>
      </div>

      {/* ===== 搜索框 ===== */}
      <div className="flex-none flex items-center px-3 pb-2.5">
        <div
          className="flex items-center gap-2 w-full px-2.5 py-1.5 rounded-md"
          style={{
            backgroundColor: "var(--bg-muted)",
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
