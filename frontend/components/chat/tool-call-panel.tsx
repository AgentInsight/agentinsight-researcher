// components/chat/tool-call-panel.tsx
"use client";

import { memo, useState } from "react";
import { ChevronDown, ChevronRight, Wrench } from "lucide-react";
import type { ToolCall } from "@/lib/types";

/**
 * 工具调用折叠面板 (Linear Indigo 设计风格)
 * 显示工具名 + 参数 + 结果
 *
 * P1-17: 用 React.memo 包裹, 默认浅比较 toolCalls 数组引用
 * 父组件流式 setState 时若 toolCalls 引用未变, 则跳过重渲染
 */
export const ToolCallPanel = memo(function ToolCallPanel({ toolCalls }: { toolCalls: ToolCall[] }) {
  const [open, setOpen] = useState(false);

  return (
    <div
      className="mt-2 border-t pt-2"
      style={{ borderColor: "var(--border-color)" }}
    >
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-xs transition-colors"
        style={{ color: "var(--text-secondary)" }}
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <Wrench className="h-3 w-3" />
        工具调用 ({toolCalls.length})
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          {toolCalls.map((tc, idx) => (
            <div
              key={idx}
              className="text-xs p-2"
              style={{
                backgroundColor: "var(--bg-muted)",
                borderRadius: "var(--radius-sm)",
              }}
            >
              <div
                className="font-medium"
                style={{ color: "var(--text-primary)" }}
              >
                {tc.function.name}
              </div>
              <div
                className="mt-1"
                style={{ color: "var(--text-secondary)" }}
              >
                参数:{" "}
                <code
                  className="text-xs"
                  style={{
                    backgroundColor: "var(--bg-card)",
                    color: "var(--text-primary)",
                    padding: "0.1em 0.3em",
                    borderRadius: "3px",
                  }}
                >
                  {tc.function.arguments}
                </code>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
});
