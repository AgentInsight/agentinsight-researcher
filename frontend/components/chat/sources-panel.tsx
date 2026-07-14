// components/chat/sources-panel.tsx
"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, FileText } from "lucide-react";
import type { Source } from "@/lib/types";

/**
 * 检索来源折叠面板
 * 显示召回片段 (source + score)
 */
export function SourcesPanel({ sources }: { sources: Source[] }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="mt-2 border-t pt-2">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700"
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <FileText className="h-3 w-3" />
        检索来源 ({sources.length})
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          {sources.map((src, idx) => (
            <div key={idx} className="text-xs bg-gray-50 rounded p-2">
              <div className="flex justify-between">
                <span className="font-medium">{src.title}</span>
                <span className="text-gray-500">score: {src.score.toFixed(3)}</span>
              </div>
              {src.url && (
                <a href={src.url} target="_blank" rel="noopener noreferrer" className="text-blue-500 hover:underline text-xs">
                  {src.url}
                </a>
              )}
              <div className="text-gray-600 mt-1 line-clamp-2">{src.content}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
