// lib/use-chat.ts
"use client";

import { useState, useCallback, useRef } from "react";
import { apiClient } from "./api-client";
import { useAuthStore } from "./auth-store";
import type { ChatMessage, ToolCall, Source } from "./types";

/**
 * 聊天 Hook
 * - 封装 apiClient.chatStream 调用
 * - 管理 messages / toolCalls / sources / isStreaming 状态
 * - 解析 SSE 自定义事件
 */
export function useChat() {
  const { getToken } = useAuthStore();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [toolCalls, setToolCalls] = useState<ToolCall[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(async (content: string, sessionId: string) => {
    const userMessage: ChatMessage = { role: "user", content };
    const assistantMessage: ChatMessage = { role: "assistant", content: "" };
    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setIsStreaming(true);
    setError(null);
    setToolCalls([]);
    setSources([]);

    try {
      const token = getToken();
      const allMessages = [...messages, userMessage];

      for await (const sseEvent of apiClient.chatStream(allMessages, sessionId, token)) {
        if (sseEvent.event === "message") {
          const data = sseEvent.data as { choices?: Array<{ delta?: { content?: string } }> };
          const delta = data?.choices?.[0]?.delta?.content;
          if (delta) {
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              return [...prev.slice(0, -1), { ...last, content: last.content + delta }];
            });
          }
        } else if (sseEvent.event === "tool_call") {
          setToolCalls((prev) => [...prev, sseEvent.data as ToolCall]);
        } else if (sseEvent.event === "sources") {
          setSources((prev) => [...prev, ...(sseEvent.data as Source[])]);
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "未知错误");
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last.role === "assistant" && !last.content) {
          return [...prev.slice(0, -1), { ...last, content: `错误: ${err}` }];
        }
        return prev;
      });
    } finally {
      setIsStreaming(false);
    }
  }, [messages, getToken]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    setIsStreaming(false);
  }, []);

  const clear = useCallback(() => {
    setMessages([]);
    setToolCalls([]);
    setSources([]);
    setError(null);
  }, []);

  return { messages, isStreaming, toolCalls, sources, error, send, stop, clear };
}
