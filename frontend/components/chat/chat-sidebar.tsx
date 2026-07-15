// components/chat/chat-sidebar.tsx
"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { apiClient } from "@/lib/api-client";
import { useAuthStore } from "@/lib/auth-store";
import { useAgentStore } from "@/lib/agent-store";
import { useSessionStore } from "@/lib/session-store";
import { useStreamStore } from "@/lib/stream-store";
import { useToast, ToastContainer } from "@/components/ui/toast";
import {
  Plus,
  Trash2,
  Pencil,
  MessageSquare,
  Check,
  X,
} from "lucide-react";
import { Tooltip } from "@/components/ui/tooltip";
import type { Session } from "@/lib/types";

/**
 * 会话导航栏 (集成在主对话区内, 无硬边框)
 *
 * 显示/隐藏由父组件通过条件渲染控制, 本组件只负责会话列表内容:
 * - "+ 新建会话" 按钮
 * - 会话列表 (每个会话右侧 2 个图标: 改名 / 删除)
 *
 * 性能优化 (P1-15/P1-16):
 * - SessionItem 拆分为 React.memo 组件, 单条会话 props 不变时跳过重渲
 * - 6 个 handler 用 useCallback 包裹, 引用稳定, 确保 SessionItem memo 有效
 * - handleSelectSession/handleConfirmRename 用 ref 避免 renamingId/renameValue 依赖
 *   确保 handler 引用稳定, 不会因输入框内容变化导致所有 SessionItem 重渲
 */

interface SessionItemProps {
  session: Session;
  isActive: boolean;
  isRenaming: boolean;
  renameValue: string;
  onSelect: (sessionId: string) => void;
  onStartRename: (e: React.MouseEvent, sessionId: string, currentTitle: string) => void;
  onDelete: (e: React.MouseEvent, sessionId: string) => void;
  onConfirmRename: (sessionId: string) => void;
  onCancelRename: () => void;
  onRenameValueChange: (value: string) => void;
}

/**
 * 单条会话项 (memo)
 * - 浅比较 props, 任一 prop 引用变化才重渲
 * - 重命名输入时只有正在重命名的会话 renameValue 变化, 其他会话零重渲
 */
const SessionItem = React.memo(function SessionItem({
  session,
  isActive,
  isRenaming,
  renameValue,
  onSelect,
  onStartRename,
  onDelete,
  onConfirmRename,
  onCancelRename,
  onRenameValueChange,
}: SessionItemProps) {
  return (
    <div
      onClick={() => onSelect(session.session_id)}
      className="group px-2.5 py-2.5 rounded-md cursor-pointer flex justify-between items-center mb-0.5 transition-colors"
      style={{
        backgroundColor: isActive ? "var(--bg-active)" : "transparent",
      }}
    >
      <div className="flex items-center gap-2 flex-1 min-w-0">
        <MessageSquare
          className="h-4 w-4 flex-shrink-0"
          style={{
            color: isActive
              ? "var(--brand-primary)"
              : "var(--text-tertiary)",
          }}
        />
        {isRenaming ? (
          <input
            type="text"
            value={renameValue}
            onChange={(e) => onRenameValueChange(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Enter") onConfirmRename(session.session_id);
              if (e.key === "Escape") onCancelRename();
            }}
            autoFocus
            className="flex-1 text-sm bg-transparent border-b outline-none"
            style={{
              borderColor: "var(--brand-primary)",
              color: "var(--text-primary)",
            }}
          />
        ) : (
          <span
            className="text-sm truncate flex-1"
            style={{
              color: isActive
                ? "var(--brand-primary)"
                : "var(--text-primary)",
              fontWeight: isActive ? 500 : 400,
            }}
          >
            {session.title || "新会话"}
          </span>
        )}
      </div>

      {isRenaming ? (
        <div className="flex items-center gap-1 ml-2">
          <Tooltip content="确认">
            <button
              onClick={(e) => {
                e.stopPropagation();
                onConfirmRename(session.session_id);
              }}
              className="p-1 rounded hover:bg-hover"
              style={{ color: "var(--brand-primary)" }}
              aria-label="确认重命名"
            >
              <Check className="h-3.5 w-3.5" />
            </button>
          </Tooltip>
          <Tooltip content="取消">
            <button
              onClick={(e) => {
                e.stopPropagation();
                onCancelRename();
              }}
              className="p-1 rounded hover:bg-hover"
              style={{ color: "var(--text-tertiary)" }}
              aria-label="取消重命名"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </Tooltip>
        </div>
      ) : (
        <div className="flex items-center gap-1 ml-2 opacity-0 group-hover:opacity-100 transition-opacity">
          <Tooltip content="重命名">
            <button
              onClick={(e) => onStartRename(e, session.session_id, session.title || "")}
              className="p-1 rounded hover:bg-hover"
              style={{ color: "var(--text-tertiary)" }}
              aria-label="重命名"
            >
              <Pencil className="h-3.5 w-3.5" />
            </button>
          </Tooltip>
          <Tooltip content="删除">
            <button
              onClick={(e) => onDelete(e, session.session_id)}
              className="p-1 rounded hover:bg-hover"
              style={{ color: "var(--text-tertiary)" }}
              aria-label="删除会话"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </Tooltip>
        </div>
      )}
    </div>
  );
});

export function ChatSidebar() {
  const { getToken } = useAuthStore();
  const { currentAgent } = useAgentStore();
  const {
    sessions,
    currentSessionId,
    isLoadingSessions,
    isCreatingSession,
    setSessions,
    addSession,
    removeSession,
    updateSessionTitle,
    setCurrentSession,
    setLoadingSessions,
    setCreatingSession,
  } = useSessionStore();
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const { toasts, addToast, removeToast } = useToast();

  // 用 ref 保存 renamingId/renameValue 当前值, 让依赖它们的 handler 引用稳定 (P1-16)
  // 避免 handler 每次输入框内容变化都重建, 进而触发所有 SessionItem 重渲
  const renamingIdRef = useRef<string | null>(renamingId);
  renamingIdRef.current = renamingId;
  const renameValueRef = useRef(renameValue);
  renameValueRef.current = renameValue;

  // 兼容包装: 将原 setError 调用转为 Toast 错误提示
  // useCallback 确保 setError 引用稳定, 进而确保依赖它的 handler 引用稳定 (P1-16)
  const setError = useCallback(
    (msg: string | null) => {
      if (msg) addToast({ type: "error", message: msg });
    },
    [addToast]
  );

  useEffect(() => {
    const loadSessions = async () => {
      setLoadingSessions(true);
      setError(null);
      try {
        const token = getToken();
        const data = await apiClient.getSessions(token);
        setSessions(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载会话失败");
      } finally {
        setLoadingSessions(false);
      }
    };
    loadSessions();
  }, [currentAgent, getToken, setSessions, setLoadingSessions, setError]);

  // P1-16: 6 个 handler 全部用 useCallback 包裹, 引用稳定确保 SessionItem memo 有效
  const handleNewSession = useCallback(async () => {
    if (isCreatingSession) return;
    setCreatingSession(true);
    setError(null);
    try {
      const token = getToken();
      const session = await apiClient.createSession(token, "新会话");
      if (session && session.session_id) {
        addSession(session);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建会话失败");
    } finally {
      setCreatingSession(false);
    }
  }, [isCreatingSession, getToken, addSession, setCreatingSession, setError]);

  const handleDeleteSession = useCallback(async (e: React.MouseEvent, sessionId: string) => {
    e.stopPropagation();
    if (!confirm("确定删除该会话?")) return;
    try {
      const token = getToken();
      await apiClient.deleteSession(sessionId, token);
      // 先中止该会话的后台流 (如有), 再清理 stream context
      useStreamStore.getState().abortStream(sessionId);
      useStreamStore.getState().removeStream(sessionId);
      removeSession(sessionId);

      // 任务2: 删除后若无剩余会话, 自动新建一个, 避免空状态
      // removeSession 已将 currentSessionId 切换到剩余第一个 (或 null)
      if (!useSessionStore.getState().currentSessionId && !useSessionStore.getState().isCreatingSession) {
        useSessionStore.getState().setCreatingSession(true);
        try {
          const newSession = await apiClient.createSession(token, "新会话");
          if (newSession && newSession.session_id) {
            useSessionStore.getState().addSession(newSession);
          }
        } finally {
          useSessionStore.getState().setCreatingSession(false);
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除会话失败");
    }
  }, [getToken, removeSession, setError]);

  // 用 ref 读取 renamingId, 避免依赖 renamingId state 导致 handler 每次重命名变化都重建
  const handleSelectSession = useCallback((sessionId: string) => {
    if (renamingIdRef.current === sessionId) return;
    setCurrentSession(sessionId);
  }, [setCurrentSession]);

  const handleStartRename = useCallback((e: React.MouseEvent, sessionId: string, currentTitle: string) => {
    e.stopPropagation();
    setRenamingId(sessionId);
    setRenameValue(currentTitle || "新会话");
  }, []);

  const handleCancelRename = useCallback(() => {
    setRenamingId(null);
    setRenameValue("");
  }, []);

  // 用 ref 读取 renameValue, 避免依赖 renameValue state 导致 handler 每次输入都重建
  const handleConfirmRename = useCallback(async (sessionId: string) => {
    const newTitle = renameValueRef.current.trim();
    if (!newTitle) {
      setRenamingId(null);
      setRenameValue("");
      return;
    }
    // 先更新本地状态 (即时反馈)
    updateSessionTitle(sessionId, newTitle);
    // 异步同步到后端
    try {
      const token = getToken();
      await apiClient.renameSession(sessionId, newTitle, token);
    } catch (err) {
      setError(err instanceof Error ? err.message : "重命名失败");
    } finally {
      setRenamingId(null);
      setRenameValue("");
    }
  }, [updateSessionTitle, getToken, setError]);

  const handleRenameValueChange = useCallback((value: string) => {
    setRenameValue(value);
  }, []);

  return (
    <div
      className="flex flex-col h-full"
      style={{
        backgroundColor: "var(--bg-sidebar)",
        width: 260,
        flexShrink: 0,
      }}
    >
      {/* ===== 会话列表 ===== */}
      <div className="flex-1 flex flex-col overflow-hidden min-h-0">
        {/* 新建会话按钮 */}
        <div className="p-2.5">
          <button
            onClick={handleNewSession}
            disabled={isCreatingSession}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-md text-sm font-medium transition-colors disabled:opacity-50"
            style={{
              backgroundColor: "var(--brand-primary)",
              color: "var(--text-on-brand)",
            }}
          >
            <Plus className="h-4 w-4" />
            {isCreatingSession ? "创建中..." : "新建会话"}
          </button>
        </div>

        {/* Toast 提示容器 (右上角, 替代内联错误提示) */}
        <ToastContainer toasts={toasts} onClose={removeToast} />

        {/* 会话列表 */}
        <div className="flex-1 overflow-y-auto px-2 pb-2">
          {isLoadingSessions && sessions.length === 0 && (
            <div
              className="text-center text-sm py-8"
              style={{ color: "var(--text-tertiary)" }}
            >
              加载中...
            </div>
          )}

          {!isLoadingSessions && sessions.length === 0 && (
            <div
              className="text-center text-sm py-8"
              style={{ color: "var(--text-tertiary)" }}
            >
              <MessageSquare className="h-8 w-8 mx-auto mb-2 opacity-40" />
              <p>正在创建会话...</p>
            </div>
          )}

          {sessions.map((s) => {
            const isActive = s.session_id === currentSessionId;
            const isRenaming = renamingId === s.session_id;
            return (
              <SessionItem
                key={s.session_id}
                session={s}
                isActive={isActive}
                isRenaming={isRenaming}
                renameValue={renameValue}
                onSelect={handleSelectSession}
                onStartRename={handleStartRename}
                onDelete={handleDeleteSession}
                onConfirmRename={handleConfirmRename}
                onCancelRename={handleCancelRename}
                onRenameValueChange={handleRenameValueChange}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}
