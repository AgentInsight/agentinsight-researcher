// components/chat/chat-input.tsx
"use client";

import { useRef, useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Send, Loader2, Paperclip, Square, X, FileText, ChevronDown, ChevronUp } from "lucide-react";
import { useSessionStore } from "@/lib/session-store";
import { useStreamStore } from "@/lib/stream-store";
import { useAuthStore } from "@/lib/auth-store";
import { apiClient } from "@/lib/api-client";
import { Tooltip } from "@/components/ui/tooltip";

/**
 * 聊天输入组件 (ChatGPT/Claude 风格)
 * - 附件/发送按钮内嵌在输入框容器内 (统一视觉单元)
 * - Enter 发送, Shift+Enter 换行
 * - 流式响应期间显示停止按钮 (替代发送按钮)
 * - 自适应高度
 * - 输入框草稿按 sessionId 隔离 (切换会话时保留各自草稿)
 * - 文件上传按 sessionId 隔离 (切换会话时保留各自文件列表)
 * - 发送按钮 disabled 状态按会话隔离 (切换会话立即恢复)
 *
 * 布局:
 * ┌───────────────────────────────────────┐
 * │  [已上传文件列表 (折叠面板)]          │
 * ├───────────────────────────────────────┤
 * │  [textarea]              [📎] [➤/⬛]  │
 * └───────────────────────────────────────┘
 */
export function ChatInput({
  onSend,
  onStop,
  placeholder = "输入消息... (Enter 发送, Shift+Enter 换行)",
}: {
  onSend: (content: string) => void;
  onStop: () => void;
  placeholder?: string;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { getToken } = useAuthStore();
  // 性能优化 (P0-7): 细粒度 selector, 只订阅当前 session 的数据
  // 避免订阅整个 drafts/files 对象 (任意 session 的 draft/files 变化都触发重渲)
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const draftKey = currentSessionId ?? "default";
  const input = useSessionStore((s) => s.drafts[draftKey] ?? "");
  // useShallow 对数组做浅比较, 避免 s.files[draftKey] 不存在时每次 store 变化都创建新 [] 触发重渲
  const currentFiles = useSessionStore(useShallow((s) => s.files[draftKey] ?? []));
  // actions 引用稳定, 分别用细粒度 selector 取
  const setDraft = useSessionStore((s) => s.setDraft);
  const clearDraft = useSessionStore((s) => s.clearDraft);
  const addFiles = useSessionStore((s) => s.addFiles);
  const removeFile = useSessionStore((s) => s.removeFile);
  const toggleFileSelected = useSessionStore((s) => s.toggleFileSelected);

  // 从 stream-store 读取当前会话的流式状态 (per-session 隔离)
  const isStreaming = useStreamStore((state) =>
    currentSessionId ? state.streams[currentSessionId]?.isStreaming ?? false : false
  );
  const hasReviewRequest = useStreamStore((state) =>
    currentSessionId ? !!state.streams[currentSessionId]?.reviewRequest : false
  );

  const [uploading, setUploading] = useState(false);
  const [showFileList, setShowFileList] = useState(false);

  const selectedCount = currentFiles.filter((f) => f.selected).length;

  const disabled = isStreaming || hasReviewRequest || !currentSessionId;

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`;
    }
  }, [input]);

  // 有文件时自动展开文件列表
  useEffect(() => {
    if (currentFiles.length > 0 && !showFileList) {
      setShowFileList(true);
    }
  }, [currentFiles.length]);

  const handleSubmit = () => {
    if (!input.trim() || disabled) return;
    onSend(input);
    clearDraft(draftKey);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleAttach = () => {
    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.multiple = true;
    fileInput.onchange = async (e) => {
      const fileList = (e.target as HTMLInputElement).files;
      if (!fileList || fileList.length === 0) return;
      const token = getToken();
      setUploading(true);
      try {
        for (const file of Array.from(fileList)) {
          try {
            const resp = await apiClient.uploadFile(file, token);
            addFiles(draftKey, [{
              file_id: resp.file_id,
              filename: resp.filename,
              size_bytes: file.size,
              selected: true,
            }]);
          } catch {
            // 单个文件失败不影响其他文件
          }
        }
      } finally {
        setUploading(false);
      }
    };
    fileInput.click();
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes}B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
  };

  return (
    <div
      className="px-4 py-3"
      style={{ backgroundColor: "var(--bg-card)" }}
    >
      <div className="max-w-3xl mx-auto">
        {/* 已上传文件列表 (折叠面板) */}
        {currentFiles.length > 0 && (
          <div
            className="mb-2 rounded-lg overflow-hidden"
            style={{ border: "1px solid var(--border-color-light)" }}
          >
            <button
              onClick={() => setShowFileList(!showFileList)}
              className="w-full flex items-center justify-between px-3 py-1.5 text-xs transition-colors hover:bg-hover"
              style={{ color: "var(--text-secondary)" }}
            >
              <span className="flex items-center gap-1.5">
                <Paperclip className="h-3 w-3" />
                {currentFiles.length} 个文件 ({selectedCount} 选中)
              </span>
              {showFileList ? (
                <ChevronUp className="h-3 w-3" />
              ) : (
                <ChevronDown className="h-3 w-3" />
              )}
            </button>
            {showFileList && (
              <div className="px-2 py-1 space-y-1 max-h-40 overflow-y-auto">
                {currentFiles.map((f) => (
                  <div
                    key={f.file_id}
                    className="flex items-center gap-2 px-2 py-1.5 rounded text-xs"
                    style={{ backgroundColor: "var(--bg-muted)" }}
                  >
                    <input
                      type="checkbox"
                      checked={f.selected}
                      onChange={() => toggleFileSelected(draftKey, f.file_id)}
                      className="h-3 w-3 rounded"
                    />
                    <FileText className="h-3 w-3 flex-shrink-0" style={{ color: "var(--text-tertiary)" }} />
                    <span className="flex-1 truncate" style={{ color: "var(--text-primary)" }}>
                      {f.filename}
                    </span>
                    <span style={{ color: "var(--text-tertiary)" }}>
                      {formatFileSize(f.size_bytes)}
                    </span>
                    <button
                      onClick={() => removeFile(draftKey, f.file_id)}
                      className="p-0.5 rounded hover:bg-hover transition-colors"
                      style={{ color: "var(--text-tertiary)" }}
                      aria-label="移除文件"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* 输入框容器 (包含 textarea + 附件按钮 + 发送/停止按钮) */}
        <div
          className="input-container flex items-end gap-2 px-3 py-2 rounded-lg"
          style={{
            backgroundColor: "var(--bg-muted)",
          }}
        >
          {/* 输入框 */}
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setDraft(draftKey, e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            className="flex-1 resize-none bg-transparent border-none outline-none text-sm max-h-48 py-1"
            style={{ color: "var(--text-primary)" }}
            rows={1}
            disabled={disabled}
          />

          {/* 附件按钮 (内嵌在输入框内) */}
          <Tooltip content="上传附件">
            <button
              onClick={handleAttach}
              disabled={disabled || uploading}
              className="flex items-center justify-center w-8 h-8 rounded-lg transition-colors disabled:opacity-50 hover:bg-hover flex-shrink-0 relative"
              style={{ color: "var(--text-tertiary)" }}
              aria-label="上传附件"
            >
              {uploading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <>
                  <Paperclip className="h-4 w-4" />
                  {selectedCount > 0 && (
                    <span
                      className="absolute -top-1 -right-1 h-4 min-w-4 px-1 rounded-full text-[10px] flex items-center justify-center"
                      style={{
                        backgroundColor: "var(--brand-primary)",
                        color: "var(--text-on-brand)",
                      }}
                    >
                      {selectedCount}
                    </span>
                  )}
                </>
              )}
            </button>
          </Tooltip>

          {/* 发送按钮 / 停止按钮 (互斥) */}
          {isStreaming ? (
            <Tooltip content="停止生成">
              <button
                onClick={onStop}
                className="flex items-center justify-center w-8 h-8 rounded-lg transition-colors hover:opacity-90 flex-shrink-0"
                style={{
                  backgroundColor: "var(--color-danger)",
                  color: "var(--text-on-brand)",
                }}
                aria-label="停止生成"
              >
                <Square className="h-3.5 w-3.5 fill-current" />
              </button>
            </Tooltip>
          ) : (
            <Tooltip content="发送">
              <button
                onClick={handleSubmit}
                disabled={disabled || !input.trim()}
                className="flex items-center justify-center w-8 h-8 rounded-lg transition-colors disabled:opacity-40 flex-shrink-0"
                style={{
                  backgroundColor: "var(--brand-primary)",
                  color: "var(--text-on-brand)",
                }}
                aria-label="发送"
              >
                {disabled ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
              </button>
            </Tooltip>
          )}
        </div>
      </div>
    </div>
  );
}
