// components/chat/chat-sidebar.tsx
"use client";

import { useEffect, useState } from "react";
import { apiClient } from "@/lib/api-client";
import { useAuthStore } from "@/lib/auth-store";
import { useAgentStore } from "@/lib/agent-store";
import type { Session } from "@/lib/types";

/**
 * 会话列表侧边栏
 * - 监听 currentAgent 变化, 刷新会话列表 (per-Agent 隔离)
 * - 切换 Agent 时刷新会话列表
 */
export function ChatSidebar() {
  const { getToken } = useAuthStore();
  const { currentAgent } = useAgentStore();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(false);

  // 监听 currentAgent 变化, 刷新会话列表 (per-Agent 隔离)
  useEffect(() => {
    const loadSessions = async () => {
      setLoading(true);
      try {
        const token = getToken();
        const data = await apiClient.getSessions(token);
        setSessions(data.sessions || []);
      } catch {
        // 忽略错误
      } finally {
        setLoading(false);
      }
    };
    loadSessions();
  }, [currentAgent, getToken]); // 切换 Agent 或 token 变化时刷新

  const handleNewSession = async () => {
    const token = getToken();
    const data = await apiClient.createSession(token, "新会话");
    if (data.session) {
      setSessions((prev) => [data.session, ...prev]);
    }
  };

  const handleDeleteSession = async (sessionId: string) => {
    const token = getToken();
    await apiClient.deleteSession(sessionId, token);
    setSessions((prev) => prev.filter((s) => s.session_id !== sessionId));
  };

  return (
    <div className="flex-1 overflow-y-auto p-2">
      <button
        onClick={handleNewSession}
        className="w-full mb-2 px-3 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
      >
        + 新会话
      </button>
      {loading && <div className="text-center text-gray-400 text-sm">加载中...</div>}
      {sessions.map((s) => (
        <div
          key={s.session_id}
          className="group px-3 py-2 hover:bg-gray-50 rounded cursor-pointer flex justify-between items-center"
        >
          <span className="text-sm truncate flex-1">{s.title || s.session_id}</span>
          <button
            onClick={(e) => {
              e.stopPropagation();
              handleDeleteSession(s.session_id);
            }}
            className="opacity-0 group-hover:opacity-100 text-red-500 text-xs ml-2"
          >
            删除
          </button>
        </div>
      ))}
    </div>
  );
}
