// components/chat/chat-messages.tsx
"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ToolCallPanel } from "./tool-call-panel";
import { SourcesPanel } from "./sources-panel";
import type { ChatMessage, ToolCall, Source } from "@/lib/types";

/**
 * 消息列表组件
 * - 区分 user/assistant 消息
 * - assistant 消息支持 Markdown 渲染
 * - 流式响应期间显示"生成中"状态
 * - 工具调用/检索来源折叠面板
 */
export function ChatMessages({
  messages,
  toolCalls,
  sources,
  isStreaming,
}: {
  messages: ChatMessage[];
  toolCalls: ToolCall[];
  sources: Source[];
  isStreaming: boolean;
}) {
  return (
    <div className="max-w-3xl mx-auto p-4 space-y-4">
      {messages.length === 0 && (
        <div className="text-center text-gray-400 mt-20">
          开始新的对话...
        </div>
      )}
      {messages.map((msg, idx) => (
        <div
          key={idx}
          className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
        >
          <div
            className={`max-w-[80%] rounded-lg p-3 ${
              msg.role === "user"
                ? "bg-blue-600 text-white"
                : "bg-gray-100 text-gray-900"
            }`}
          >
            {msg.role === "assistant" ? (
              <>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {msg.content || (isStreaming && idx === messages.length - 1 ? "生成中..." : "")}
                </ReactMarkdown>
                {/* 工具调用折叠面板 (仅最后一条 assistant 消息显示) */}
                {idx === messages.length - 1 && toolCalls.length > 0 && (
                  <ToolCallPanel toolCalls={toolCalls} />
                )}
                {/* 检索来源折叠面板 (仅最后一条 assistant 消息显示) */}
                {idx === messages.length - 1 && sources.length > 0 && (
                  <SourcesPanel sources={sources} />
                )}
              </>
            ) : (
              <div className="whitespace-pre-wrap">{msg.content}</div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
