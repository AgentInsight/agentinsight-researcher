// components/chat/sources-panel.tsx
"use client";

import { memo, useState } from "react";
import { ChevronDown, ChevronRight, FileText } from "lucide-react";
import type { Source } from "@/lib/types";

/**
 * 检索来源折叠面板 (Linear Indigo 设计风格)
 * 显示召回片段 (source + score)
 *
 * P1-17: 用 React.memo 包裹, 默认浅比较 sources 数组引用
 */
export const SourcesPanel = memo(function SourcesPanel({ sources }: { sources: Source[] }) {
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
        <FileText className="h-3 w-3" />
        检索来源 ({sources.length})
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          {sources.map((src, idx) => (
            <div
              key={idx}
              className="text-xs p-2"
              style={{
                backgroundColor: "var(--bg-muted)",
                borderRadius: "var(--radius-sm)",
              }}
            >
              <div className="flex justify-between">
                <span
                  className="font-medium"
                  style={{ color: "var(--text-primary)" }}
                >
                  {src.title}
                </span>
                <span style={{ color: "var(--text-tertiary)" }}>
                  score: {src.score.toFixed(3)}
                </span>
              </div>
              {src.url && (
                <a
                  href={src.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs hover:underline"
                  style={{ color: "var(--brand-primary)" }}
                >
                  {src.url}
                </a>
              )}
              <div
                className="mt-1 line-clamp-2"
                style={{ color: "var(--text-secondary)" }}
              >
                {src.content}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
});
