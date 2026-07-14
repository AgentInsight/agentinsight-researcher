// components/chat/tool-call-panel.tsx
"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Wrench } from "lucide-react";
import type { ToolCall } from "@/lib/types";

/**
 * 工具调用折叠面板
 * 显示工具名 + 参数 + 结果
 */
export function ToolCallPanel({ toolCalls }: { toolCalls: ToolCall[] }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="mt-2 border-t pt-2">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700"
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <Wrench className="h-3 w-3" />
        工具调用 ({toolCalls.length})
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          {toolCalls.map((tc, idx) => (
            <div key={idx} className="text-xs bg-gray-50 rounded p-2">
              <div className="font-medium">{tc.function.name}</div>
              <div className="text-gray-600 mt-1">
                参数: <code className="text-xs">{tc.function.arguments}</code>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
