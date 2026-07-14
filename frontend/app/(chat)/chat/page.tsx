// app/(chat)/chat/page.tsx
"use client";

import { useState, useCallback } from "react";
import { ChatInput } from "@/components/chat/chat-input";
import { ChatMessages } from "@/components/chat/chat-messages";
import { ReviewPanel } from "@/components/review/review-panel";
import { useAuthStore } from "@/lib/auth-store";
import { apiClient } from "@/lib/api-client";
import type { ChatMessage, ToolCall, Source } from "@/lib/types";

/**
 * 聊天主页
 * - 通过 apiClient 调用后端, apiClient 自动按当前选中 Agent 切换 baseUrl
 * - 解析 SSE 自定义事件: tool_call / sources
 */
export default function ChatPage() {
  const { getToken } = useAuthStore();
  const [sessionId] = useState(() => crypto.randomUUID());
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [toolCalls, setToolCalls] = useState<ToolCall[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [reviewRequest, setReviewRequest] = useState<{ node: string; content: string } | null>(null);

  const handleSend = useCallback(async (content: string) => {
    const userMessage: ChatMessage = { role: "user", content };
    setMessages((prev) => [...prev, userMessage]);
    setIsStreaming(true);

    // 添加空的 assistant 消息, 用于流式追加
    const assistantMessage: ChatMessage = { role: "assistant", content: "" };
    setMessages((prev) => [...prev, assistantMessage]);

    try {
      const token = getToken();
      const allMessages = [...messages, userMessage];

      for await (const sseEvent of apiClient.chatStream(allMessages, sessionId, token)) {
        // 标准消息事件: 追加内容
        if (sseEvent.event === "message") {
          const data = sseEvent.data as { choices?: Array<{ delta?: { content?: string } }> };
          const delta = data?.choices?.[0]?.delta?.content;
          if (delta) {
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              return [...prev.slice(0, -1), { ...last, content: last.content + delta }];
            });
          }
        }
        // 工具调用事件
        else if (sseEvent.event === "tool_call") {
          const toolCall = sseEvent.data as ToolCall;
          setToolCalls((prev) => [...prev, toolCall]);
        }
        // 检索来源事件
        else if (sseEvent.event === "sources") {
          const sourcesData = sseEvent.data as Source[];
          setSources((prev) => [...prev, ...sourcesData]);
        }
        // 人在回路审核请求
        else if (sseEvent.event === "human_feedback_request") {
          const req = sseEvent.data as { node: string; content: string };
          setReviewRequest(req);
        }
      }
    } catch (err) {
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        return [...prev.slice(0, -1), { ...last, content: `错误: ${err}` }];
      });
    } finally {
      setIsStreaming(false);
    }
  }, [messages, sessionId, getToken]);

  const handleFeedback = useCallback(async (feedback: string) => {
    const token = getToken();
    await apiClient.submitFeedback(sessionId, feedback, token);
    setReviewRequest(null);
  }, [sessionId, getToken]);

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto">
        <ChatMessages messages={messages} toolCalls={toolCalls} sources={sources} isStreaming={isStreaming} />
      </div>
      {reviewRequest && (
        <ReviewPanel request={reviewRequest} onSubmit={handleFeedback} />
      )}
      <ChatInput onSend={handleSend} disabled={isStreaming || !!reviewRequest} />
    </div>
  );
}
