// components/agent/agent-switcher.tsx
"use client";

import { useAgentStore } from "@/lib/agent-store";
import { getEnabledAgents, isMultiAgent } from "@/lib/agents.config";
import { useState } from "react";
import { ChevronDown, Bot } from "lucide-react";

/**
 * Agent 切换器组件
 * - 多 Agent 时显示下拉切换器
 * - 单 Agent 时简化展示 (不显示下拉箭头, 不可点击)
 */
export function AgentSwitcher() {
  const { currentAgent, setAgent, getCurrentAgent } = useAgentStore();
  const [open, setOpen] = useState(false);
  const agents = getEnabledAgents();
  const multi = isMultiAgent();
  const current = getCurrentAgent();

  // 单 Agent 时: 简化展示, 不可切换
  if (!multi) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-gray-50 rounded">
        <Bot className="h-4 w-4" />
        <span>{current.displayName}</span>
        <span className="text-xs text-gray-400">(当前唯一)</span>
      </div>
    );
  }

  // 多 Agent 时: 下拉切换器
  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 px-3 py-2 text-sm bg-white border rounded hover:bg-gray-50"
      >
        <Bot className="h-4 w-4" />
        <span>{current.displayName}</span>
        <ChevronDown className="h-3 w-3" />
      </button>
      {open && (
        <ul className="absolute z-10 mt-1 w-full bg-white border rounded shadow-lg">
          {agents.map((agent) => (
            <li key={agent.name}>
              <button
                onClick={() => {
                  setAgent(agent.name);
                  setOpen(false);
                  // 切换后刷新会话列表和 MCP 配置 (由上层组件监听 currentAgent 变化触发)
                }}
                className={`w-full text-left px-3 py-2 text-sm hover:bg-gray-50 ${
                  agent.name === currentAgent ? "bg-blue-50" : ""
                }`}
              >
                {agent.displayName}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
