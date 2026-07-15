// components/chat/chat-messages.tsx
"use client";

import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import { ToolCallPanel } from "./tool-call-panel";
import { SourcesPanel } from "./sources-panel";
import { ReportProgress } from "./report-progress";
import type { ChatMessage, ToolCall, Source } from "@/lib/types";

/**
 * 消息列表组件 (ChatGPT/Claude 风格)
 * - 用户消息右对齐, 宽度接近满行 (max-w-[95%], 从右向左填充)
 * - AI 消息左对齐, 占满宽度以展示 Markdown
 * - 流式响应期间显示进度提示 + 流式光标
 * - 工具调用/检索来源/节点进度折叠面板
 * - 报告下载链接 (report_id)
 *
 * 性能优化 (P0-5):
 * - 模块级 plugins 数组避免每次 render 重建 (ReactMarkdown 重解析)
 * - MarkdownContent 拆分为 memo 组件, content 不变时跳过 Markdown 解析
 * - MessageItem 拆分为 memo 组件, 单条消息 props 不变时跳过重渲
 * - ChatMessages 用 memo 包裹, 避免父组件无关 state 变化触发全列表重渲
 * - key 用 role+idx 组合, 避免纯 idx (分页加载时 idx 错位会导致组件复用错误)
 *   不使用 content.length (流式时 content.length 变化会导致重新挂载, 性能下降)
 */

// 模块级 plugins 数组, 避免 inline 数组每次 render 都创建新引用导致 ReactMarkdown 重解析
const REMARK_PLUGINS = [remarkGfm];
const REHYPE_PLUGINS = [rehypeRaw];

export interface ChatMessagesProps {
  messages: ChatMessage[];
  toolCalls: ToolCall[];
  sources: Source[];
  isStreaming: boolean;
  /** 节点进度提示 (流式生成中显示) */
  progress?: string | null;
  /** 报告 ID (用于下载) */
  reportId?: string | null;
  /** 报告格式 */
  reportFormat?: string | null;
  /** 报告文件路径 (PDF/DOCX 等二进制格式) */
  filePath?: string | null;
}

/**
 * Markdown 内容渲染 (memo)
 * - content 不变时跳过 ReactMarkdown 解析 (O(n) 解析开销)
 * - 使用模块级 plugins 数组, 避免 inline 数组导致 memo 失效
 */
const MarkdownContent = React.memo(function MarkdownContent({
  content,
}: {
  content: string;
}) {
  return (
    <ReactMarkdown
      remarkPlugins={REMARK_PLUGINS}
      rehypePlugins={REHYPE_PLUGINS}
    >
      {content}
    </ReactMarkdown>
  );
});

/**
 * 单条消息渲染所需的 props
 * - 包含单条消息的所有渲染依赖, 父组件传入时确保引用稳定
 */
interface MessageItemProps {
  msg: ChatMessage;
  isLast: boolean;
  isStreaming: boolean;
  progress: string | null;
  toolCalls: ToolCall[];
  sources: Source[];
  reportId?: string | null;
  reportFormat?: string | null;
  filePath?: string | null;
}

/**
 * 单条消息项 (memo)
 * - 浅比较 props, 任一 prop 引用变化才重渲
 * - 流式时只有最后一条 assistant 消息的 content/isStreaming 变化, 其他消息零重渲
 */
const MessageItem = React.memo(function MessageItem({
  msg,
  isLast,
  isStreaming,
  progress,
  toolCalls,
  sources,
  reportId,
  reportFormat,
  filePath,
}: MessageItemProps) {
  const isLastAssistant = isLast && msg.role === "assistant";
  const isStreamingLast = isStreaming && isLastAssistant;
  const hasContent = msg.content && msg.content.length > 0;

  return (
    <div
      className={`flex gap-3 fade-in ${msg.role === "user" ? "justify-end" : "justify-start"}`}
    >
      {/* AI 头像 */}
      {msg.role === "assistant" && (
        <div
          className="flex-shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-medium mt-0.5"
          style={{
            backgroundColor: "var(--brand-primary)",
            color: "var(--text-on-brand)",
          }}
        >
          AI
        </div>
      )}

      <div className={`flex flex-col ${msg.role === "user" ? "items-end flex-1 min-w-0" : "items-start flex-1 min-w-0"}`}>
        {/* 消息气泡 */}
        {msg.role === "user" ? (
          // 用户消息: 右对齐, 宽度接近满行 (max-w-[95%]), 从右向左填充直到满一行才换行
          <div
            className="max-w-[95%] px-4 py-2.5 rounded-lg rounded-tr-sm"
            style={{
              backgroundColor: "var(--brand-primary)",
              color: "var(--text-on-brand)",
            }}
          >
            <div className="whitespace-pre-wrap break-words text-sm leading-relaxed">{msg.content}</div>
          </div>
        ) : (
          // AI 消息: 占满宽度以展示 Markdown
          <div className="w-full">
            {/* 流式进度提示 (无内容时显示) */}
            {isStreamingLast && !hasContent && progress && (
              <div className="flex items-center gap-2 text-sm mb-2" style={{ color: "var(--text-secondary)" }}>
                <span className="streaming-dot"></span>
                <span>{progress}</span>
              </div>
            )}

            {/* Markdown 内容 */}
            {hasContent ? (
              <div className="prose max-w-none">
                <MarkdownContent content={msg.content} />
                {isStreamingLast && (
                  <span className="streaming-cursor"></span>
                )}
              </div>
            ) : isStreamingLast && !progress ? (
              <div className="flex items-center gap-2 text-sm" style={{ color: "var(--text-tertiary)" }}>
                <span className="streaming-dot"></span>
                <span>正在思考...</span>
              </div>
            ) : null}

            {/* 工具调用折叠面板 (仅最后一条 assistant 消息显示) */}
            {isLastAssistant && toolCalls.length > 0 && (
              <ToolCallPanel toolCalls={toolCalls} />
            )}

            {/* 检索来源折叠面板 (仅最后一条 assistant 消息显示) */}
            {isLastAssistant && sources.length > 0 && (
              <SourcesPanel sources={sources} />
            )}

            {/* 报告下载 (report_id 存在时显示) */}
            {isLastAssistant && reportId && !isStreaming && (
              <ReportProgress
                reportId={reportId}
                reportFormat={reportFormat}
                filePath={filePath}
              />
            )}
          </div>
        )}
      </div>

      {/* 用户头像 */}
      {msg.role === "user" && (
        <div
          className="flex-shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-medium mt-0.5"
          style={{
            backgroundColor: "var(--text-secondary)",
            color: "var(--text-on-brand)",
          }}
        >
          我
        </div>
      )}
    </div>
  );
});

export const ChatMessages = React.memo(function ChatMessages({
  messages,
  toolCalls,
  sources,
  isStreaming,
  progress,
  reportId,
  reportFormat,
  filePath,
}: ChatMessagesProps) {
  return (
    <div className="max-w-3xl mx-auto px-4 py-6 space-y-5">
      {messages.length === 0 && !isStreaming && (
        <div className="text-center mt-20 fade-in" style={{ color: "var(--text-tertiary)" }}>
          <div className="text-4xl mb-3">👋</div>
          <h3 className="text-lg font-medium mb-1" style={{ color: "var(--text-secondary)" }}>开始新的对话</h3>
          <p className="text-sm">在下方输入框中输入您的问题</p>
        </div>
      )}

      {messages.map((msg, idx) => {
        const isLast = idx === messages.length - 1;
        return (
          <MessageItem
            // ChatMessage 类型无 id 字段, 用 role+idx 组合作为稳定 key
            // 避免纯 idx (分页加载时 idx 错位会导致组件复用错误)
            // 不使用 content.length (流式时 content.length 变化会导致重新挂载, 性能下降)
            key={`${msg.role}-${idx}`}
            msg={msg}
            isLast={isLast}
            isStreaming={isStreaming}
            progress={progress ?? null}
            toolCalls={toolCalls}
            sources={sources}
            reportId={reportId ?? null}
            reportFormat={reportFormat ?? null}
            filePath={filePath ?? null}
          />
        );
      })}
    </div>
  );
});
