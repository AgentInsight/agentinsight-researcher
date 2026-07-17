// lib/session-store.ts
import { create } from "zustand";
import type { Session, ReportConfig } from "./types";

/** 每用户每智能体最多可创建的会话数 (与后端 settings.max_sessions_per_user 保持一致) */
export const MAX_SESSIONS_PER_USER = 10;

/** 已上传文件信息 (per-session 隔离) */
export interface UploadedFile {
  file_id: string;
  filename: string;
  size_bytes: number;
  /** 是否选中参与本次研究请求 */
  selected: boolean;
}

/**
 * 会话状态管理 (全局 Store)
 * - sessions: 当前 Agent 的会话列表
 * - currentSessionId: 当前选中会话 (聊天页使用)
 * - reportConfig: 当前会话报告配置 (与后端 /v1/sessions/{id}/config 同步)
 * - drafts: 输入框草稿 (按 sessionId 隔离, 切换会话时保留各自草稿)
 * - files: 已上传文件列表 (按 sessionId 隔离, 切换会话时保留各自文件)
 *
 * 替代 chat/page.tsx 中的 useState(() => crypto.randomUUID()),
 * 使侧边栏与聊天页共享会话状态, 支持切换会话.
 */
interface SessionState {
  sessions: Session[];
  currentSessionId: string | null;
  reportConfig: ReportConfig;
  /** 输入框草稿 (按 sessionId 隔离, 切换会话时保留各自草稿) */
  drafts: Record<string, string>;
  /** 已上传文件列表 (按 sessionId 隔离, 切换会话时保留各自文件) */
  files: Record<string, UploadedFile[]>;
  isLoadingSessions: boolean;
  isCreatingSession: boolean;

  setSessions: (sessions: Session[]) => void;
  addSession: (session: Session) => void;
  removeSession: (sessionId: string) => void;
  updateSessionTitle: (sessionId: string, title: string) => void;

  setCurrentSession: (sessionId: string) => void;
  /** 新建会话 (仅设置本地状态, 实际 API 调用在 ChatSidebar) */
  clearCurrentSession: () => void;

  setReportConfig: (config: ReportConfig) => void;

  /** 设置指定会话的输入框草稿 */
  setDraft: (sessionId: string, content: string) => void;
  /** 获取指定会话的输入框草稿 (无则返回空字符串) */
  getDraft: (sessionId: string) => string;
  /** 清空指定会话的草稿 (发送后调用) */
  clearDraft: (sessionId: string) => void;

  /** 追加已上传文件到指定会话 */
  addFiles: (sessionId: string, files: UploadedFile[]) => void;
  /** 获取指定会话的已上传文件列表 */
  getFiles: (sessionId: string) => UploadedFile[];
  /** 移除指定会话的某个文件 */
  removeFile: (sessionId: string, fileId: string) => void;
  /** 切换文件选中状态 */
  toggleFileSelected: (sessionId: string, fileId: string) => void;
  /** 清空指定会话的所有文件 */
  clearFiles: (sessionId: string) => void;

  setLoadingSessions: (loading: boolean) => void;
  setCreatingSession: (creating: boolean) => void;
}

const DEFAULT_REPORT_CONFIG: ReportConfig = {
  report_type: "detailed_report",
  report_format: "markdown",
  language: "zh",
};

export const useSessionStore = create<SessionState>()((set, get) => ({
  sessions: [],
  currentSessionId: null,
  reportConfig: DEFAULT_REPORT_CONFIG,
  drafts: {},
  files: {},
  isLoadingSessions: false,
  isCreatingSession: false,

  setSessions: (sessions) => set({ sessions }),

  addSession: (session) =>
    set((state) => ({
      sessions: [session, ...state.sessions.filter((s) => s.session_id !== session.session_id)],
      currentSessionId: session.session_id,
    })),

  removeSession: (sessionId) =>
    set((state) => {
      const remaining = state.sessions.filter((s) => s.session_id !== sessionId);
      // 任务2: 删除当前会话时自动切换到剩余列表的第一个会话
      // 避免出现 currentSessionId=null 的空状态 (无剩余会话时由删除逻辑兜底新建)
      const nextCurrent =
        state.currentSessionId === sessionId
          ? (remaining.length > 0 ? remaining[0].session_id : null)
          : state.currentSessionId;
      // 仅在 drafts/files 中确实存在 sessionId 时才重建 (避免不必要的对象引用变化)
      const draftsChanged = sessionId in state.drafts;
      const filesChanged = sessionId in state.files;
      return {
        sessions: remaining,
        currentSessionId: nextCurrent,
        drafts: draftsChanged
          ? Object.fromEntries(
              Object.entries(state.drafts).filter(([k]) => k !== sessionId)
            )
          : state.drafts,
        files: filesChanged
          ? Object.fromEntries(
              Object.entries(state.files).filter(([k]) => k !== sessionId)
            )
          : state.files,
      };
    }),

  updateSessionTitle: (sessionId, title) =>
    set((state) => ({
      sessions: state.sessions.map((s) =>
        s.session_id === sessionId ? { ...s, title, updated_at: new Date().toISOString() } : s
      ),
    })),

  setCurrentSession: (sessionId) => set({ currentSessionId: sessionId }),

  clearCurrentSession: () =>
    set({ currentSessionId: null, reportConfig: DEFAULT_REPORT_CONFIG }),

  setReportConfig: (config) => set({ reportConfig: config }),

  setDraft: (sessionId, content) =>
    set((state) => ({
      drafts: { ...state.drafts, [sessionId]: content },
    })),

  getDraft: (sessionId) => get().drafts[sessionId] ?? "",

  clearDraft: (sessionId) =>
    set((state) => {
      const next = { ...state.drafts };
      delete next[sessionId];
      return { drafts: next };
    }),

  addFiles: (sessionId, newFiles) =>
    set((state) => ({
      files: {
        ...state.files,
        [sessionId]: [...(state.files[sessionId] || []), ...newFiles],
      },
    })),

  getFiles: (sessionId) => get().files[sessionId] ?? [],

  removeFile: (sessionId, fileId) =>
    set((state) => ({
      files: {
        ...state.files,
        [sessionId]: (state.files[sessionId] || []).filter((f) => f.file_id !== fileId),
      },
    })),

  toggleFileSelected: (sessionId, fileId) =>
    set((state) => ({
      files: {
        ...state.files,
        [sessionId]: (state.files[sessionId] || []).map((f) =>
          f.file_id === fileId ? { ...f, selected: !f.selected } : f
        ),
      },
    })),

  clearFiles: (sessionId) =>
    set((state) => {
      const next = { ...state.files };
      delete next[sessionId];
      return { files: next };
    }),

  setLoadingSessions: (loading) => set({ isLoadingSessions: loading }),
  setCreatingSession: (creating) => set({ isCreatingSession: creating }),
}));
