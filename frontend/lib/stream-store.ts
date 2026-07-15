// lib/stream-store.ts
import { create } from "zustand";
import type { ChatMessage, ToolCall, Source, SSEEvent } from "./types";

/**
 * 流式状态管理 (per-session 隔离)
 *
 * 解决问题:
 * - 切换会话时旧会话的流式请求不被中断 (后台继续)
 * - 保留最多 1 个前台 + 1 个后台研究实例 (MAX_BACKGROUND_STREAMS = 1)
 * - 新会话发请求时, 若后台流已满, 提示用户确认是否停止最早的请求
 * - 发送按钮/输入框 disabled 状态按会话隔离 (切换会话立即恢复)
 *
 * 设计:
 * - streams: Record<sessionId, StreamContext> 按 sessionId 索引
 * - AbortController 存于模块级 Map (非序列化, 不入 zustand state)
 * - 切换会话仅切换 currentSessionId, 不动其他会话的流式状态
 */

/** 单个会话的流式上下文 */
export interface StreamContext {
  /** 该会话的消息列表 (含流式中的 assistant 消息) */
  messages: ChatMessage[];
  /** 工具调用列表 */
  toolCalls: ToolCall[];
  /** 检索来源列表 */
  sources: Source[];
  /** 节点进度文案 */
  progress: string | null;
  /** 报告 ID */
  reportId: string | null;
  /** 报告格式 */
  reportFormat: string | null;
  /** 报告文件路径 */
  filePath: string | null;
  /** 是否正在流式 */
  isStreaming: boolean;
  /** 流开始时间戳 (LRU 淘汰依据) */
  streamStartedAt: number | null;
  /** 错误信息 */
  error: string | null;
  /** 是否已加载历史消息 (避免重复加载) */
  historyLoaded: boolean;
  /** 是否正在加载历史 */
  loadingHistory: boolean;
  /** 人在回路审核请求 */
  reviewRequest: { node: string; content: string } | null;
  /** 流是否被中断 (用户停止 / LRU淘汰 / 切换页面等) */
  aborted: boolean;
}

/** 创建空 StreamContext */
function createEmptyStream(): StreamContext {
  return {
    messages: [],
    toolCalls: [],
    sources: [],
    progress: null,
    reportId: null,
    reportFormat: null,
    filePath: null,
    isStreaming: false,
    streamStartedAt: null,
    error: null,
    historyLoaded: false,
    loadingHistory: false,
    reviewRequest: null,
    aborted: false,
  };
}

/** 后台流最大并发数 (1 前台 + 1 后台 = 2 个并发研究实例) */
export const MAX_BACKGROUND_STREAMS = 1;

/** 非流式 StreamContext 最大保留数 (LRU 驱逐阈值) */
const MAX_INACTIVE_STREAMS = 10;

/** 模块级 Map 存储 AbortController (非序列化, 不入 zustand state) */
const abortControllers = new Map<string, AbortController>();

/**
 * RAF batching 缓冲区 (P0-1: 流式渲染核心优化)
 * - pendingDeltas: 按 sessionId 缓冲 content delta, 等待下一帧合并写入
 * - rafScheduled: 标记是否已调度 RAF, 避免重复调度
 */
const pendingDeltas = new Map<string, string[]>();
let rafScheduled = false;

/**
 * 刷新所有待写入的 content delta (RAF 回调)
 * - 在单帧内合并多次 token 推送为一次 set(), 降低 setState 频率 ~50x
 * - 使用 useStreamStore.setState 直达, 因为 flushDeltas 在模块级声明
 */
function flushDeltas(): void {
  rafScheduled = false;
  if (pendingDeltas.size === 0) return;
  // 快照后立即清空, 避免在 set 同步执行期间再次 push 丢失
  const snapshots = Array.from(pendingDeltas.entries());
  pendingDeltas.clear();
  useStreamStore.setState((state) => {
    const next = { ...state.streams };
    let changed = false;
    for (const [sid, deltas] of snapshots) {
      const existing = next[sid];
      if (!existing || existing.messages.length === 0) continue;
      const last = existing.messages[existing.messages.length - 1];
      const merged = deltas.join("");
      next[sid] = {
        ...existing,
        messages: [
          ...existing.messages.slice(0, -1),
          { ...last, content: last.content + merged },
        ],
      };
      changed = true;
    }
    return changed ? { streams: next } : state;
  });
}

interface StreamStore {
  /** 按 sessionId 索引的流式上下文 */
  streams: Record<string, StreamContext>;

  /** 确保 sessionId 的 StreamContext 存在, 不存在则创建 */
  ensureStream: (sessionId: string) => StreamContext;
  /** 获取指定会话的 StreamContext */
  getStream: (sessionId: string) => StreamContext | undefined;
  /** 设置指定会话的消息列表 */
  setMessages: (sessionId: string, messages: ChatMessage[]) => void;
  /** 追加消息到指定会话 */
  appendMessage: (sessionId: string, message: ChatMessage) => void;
  /** 更新最后一条消息的内容 (流式追加) */
  appendContentDelta: (sessionId: string, delta: string) => void;
  /** 设置最后一条消息内容 (错误覆盖等) */
  setLastMessageContent: (sessionId: string, content: string) => void;
  /** 设置进度 */
  setProgress: (sessionId: string, progress: string | null) => void;
  /** 追加来源 */
  appendSources: (sessionId: string, sources: Source[]) => void;
  /** 设置工具调用列表 */
  setToolCalls: (sessionId: string, toolCalls: ToolCall[]) => void;
  /** 追加工具调用 */
  appendToolCall: (sessionId: string, toolCall: ToolCall) => void;
  /** 设置报告信息 */
  setReportInfo: (
    sessionId: string,
    info: { reportId?: string; reportFormat?: string; filePath?: string }
  ) => void;
  /** 设置错误 */
  setError: (sessionId: string, error: string | null) => void;
  /** 设置历史加载状态 */
  setHistoryLoaded: (sessionId: string, loaded: boolean) => void;
  setLoadingHistory: (sessionId: string, loading: boolean) => void;
  /** 设置审核请求 */
  setReviewRequest: (
    sessionId: string,
    request: { node: string; content: string } | null
  ) => void;
  /** 清空流式临时状态 (保留 messages 历史) */
  clearStreamingState: (sessionId: string) => void;
  /** 开始流式: 创建 AbortController, 设置 isStreaming */
  startStreaming: (sessionId: string) => AbortController;
  /** 结束流式: 清除 AbortController, 重置 isStreaming, 清除 progress */
  stopStreaming: (sessionId: string) => void;
  /** 完成流式 (正常结束): 仅清理 AbortController, 重置 isStreaming, 保留 progress */
  completeStreaming: (sessionId: string) => void;
  /** 中止指定会话的流 (用户主动停止) */
  abortStream: (sessionId: string) => void;
  /** 获取指定会话的 AbortController */
  getAbortController: (sessionId: string) => AbortController | undefined;
  /** 统计正在流式的会话数 (含前台+后台) */
  activeStreamCount: () => number;
  /** 找到最早的正在流式的会话 ID (LRU 淘汰候选) */
  oldestActiveStreamSessionId: () => string | null;
  /** 删除会话的流上下文 (删除会话时调用) */
  removeStream: (sessionId: string) => void;
  /** 中止所有流 (页面卸载时调用) */
  abortAllStreams: () => void;
  /** 处理 SSE 事件, 写入指定会话的流上下文 */
  handleSSEEvent: (sessionId: string, sseEvent: SSEEvent) => void;
}

export const useStreamStore = create<StreamStore>()((set, get) => ({
  streams: {},

  ensureStream: (sessionId) => {
    const existing = get().streams[sessionId];
    if (existing) return existing;
    const ctx = createEmptyStream();
    set((state) => {
      const next = { ...state.streams };
      // LRU 驱逐: 非流式 StreamContext 超过 MAX_INACTIVE_STREAMS 时, 删除最旧的
      const inactive = Object.entries(next).filter(([, s]) => !s.isStreaming);
      if (inactive.length >= MAX_INACTIVE_STREAMS) {
        // 按 streamStartedAt 升序 (null 视为 0, 即最旧), 取需要删除的条目
        const toRemove = inactive
          .sort(
            (a, b) =>
              (a[1].streamStartedAt ?? 0) - (b[1].streamStartedAt ?? 0)
          )
          .slice(0, inactive.length - MAX_INACTIVE_STREAMS + 1);
        for (const [sid] of toRemove) {
          delete next[sid];
          abortControllers.delete(sid);
        }
      }
      next[sessionId] = ctx;
      return { streams: next };
    });
    return ctx;
  },

  getStream: (sessionId) => get().streams[sessionId],

  setMessages: (sessionId, messages) =>
    set((state) => {
      const existing = state.streams[sessionId] || createEmptyStream();
      return {
        streams: { ...state.streams, [sessionId]: { ...existing, messages } },
      };
    }),

  appendMessage: (sessionId, message) =>
    set((state) => {
      const existing = state.streams[sessionId] || createEmptyStream();
      return {
        streams: {
          ...state.streams,
          [sessionId]: { ...existing, messages: [...existing.messages, message] },
        },
      };
    }),

  appendContentDelta: (sessionId, delta) => {
    // RAF batching (P0-1): 缓冲 delta 到 pendingDeltas, 下一帧合并写入
    // 避免每 token 一次 setState 导致流式渲染卡顿
    const arr = pendingDeltas.get(sessionId);
    if (arr) {
      arr.push(delta);
    } else {
      pendingDeltas.set(sessionId, [delta]);
    }
    if (!rafScheduled) {
      rafScheduled = true;
      requestAnimationFrame(flushDeltas);
    }
  },

  setLastMessageContent: (sessionId, content) =>
    set((state) => {
      const existing = state.streams[sessionId];
      if (!existing || existing.messages.length === 0) return state;
      const last = existing.messages[existing.messages.length - 1];
      const updated = {
        ...existing,
        messages: [
          ...existing.messages.slice(0, -1),
          { ...last, content },
        ],
      };
      return { streams: { ...state.streams, [sessionId]: updated } };
    }),

  setProgress: (sessionId, progress) =>
    set((state) => {
      const existing = state.streams[sessionId] || createEmptyStream();
      return {
        streams: { ...state.streams, [sessionId]: { ...existing, progress } },
      };
    }),

  appendSources: (sessionId, sources) =>
    set((state) => {
      const existing = state.streams[sessionId] || createEmptyStream();
      return {
        streams: {
          ...state.streams,
          [sessionId]: { ...existing, sources: [...existing.sources, ...sources] },
        },
      };
    }),

  setToolCalls: (sessionId, toolCalls) =>
    set((state) => {
      const existing = state.streams[sessionId] || createEmptyStream();
      return {
        streams: { ...state.streams, [sessionId]: { ...existing, toolCalls } },
      };
    }),

  appendToolCall: (sessionId, toolCall) =>
    set((state) => {
      const existing = state.streams[sessionId] || createEmptyStream();
      return {
        streams: {
          ...state.streams,
          [sessionId]: { ...existing, toolCalls: [...existing.toolCalls, toolCall] },
        },
      };
    }),

  setReportInfo: (sessionId, info) =>
    set((state) => {
      const existing = state.streams[sessionId] || createEmptyStream();
      return {
        streams: {
          ...state.streams,
          [sessionId]: {
            ...existing,
            ...(info.reportId !== undefined ? { reportId: info.reportId } : {}),
            ...(info.reportFormat !== undefined ? { reportFormat: info.reportFormat } : {}),
            ...(info.filePath !== undefined ? { filePath: info.filePath } : {}),
          },
        },
      };
    }),

  setError: (sessionId, error) =>
    set((state) => {
      const existing = state.streams[sessionId] || createEmptyStream();
      return {
        streams: { ...state.streams, [sessionId]: { ...existing, error } },
      };
    }),

  setHistoryLoaded: (sessionId, loaded) =>
    set((state) => {
      const existing = state.streams[sessionId] || createEmptyStream();
      return {
        streams: { ...state.streams, [sessionId]: { ...existing, historyLoaded: loaded } },
      };
    }),

  setLoadingHistory: (sessionId, loading) =>
    set((state) => {
      const existing = state.streams[sessionId] || createEmptyStream();
      return {
        streams: { ...state.streams, [sessionId]: { ...existing, loadingHistory: loading } },
      };
    }),

  setReviewRequest: (sessionId, request) =>
    set((state) => {
      const existing = state.streams[sessionId] || createEmptyStream();
      return {
        streams: { ...state.streams, [sessionId]: { ...existing, reviewRequest: request } },
      };
    }),

  clearStreamingState: (sessionId) =>
    set((state) => {
      const existing = state.streams[sessionId];
      if (!existing) return state;
      return {
        streams: {
          ...state.streams,
          [sessionId]: {
            ...existing,
            toolCalls: [],
            sources: [],
            progress: null,
            error: null,
          },
        },
      };
    }),

  startStreaming: (sessionId) => {
    const abortCtrl = new AbortController();
    abortControllers.set(sessionId, abortCtrl);
    set((state) => {
      const existing = state.streams[sessionId] || createEmptyStream();
      return {
        streams: {
          ...state.streams,
          [sessionId]: {
            ...existing,
            isStreaming: true,
            streamStartedAt: Date.now(),
            progress: null,
            reportId: null,
            reportFormat: null,
            filePath: null,
            error: null,
            toolCalls: [],
            sources: [],
            aborted: false,
          },
        },
      };
    });
    return abortCtrl;
  },

  stopStreaming: (sessionId) => {
    abortControllers.delete(sessionId);
    set((state) => {
      const existing = state.streams[sessionId];
      if (!existing) return state;
      return {
        streams: {
          ...state.streams,
          [sessionId]: {
            ...existing,
            isStreaming: false,
            streamStartedAt: null,
            progress: null,
          },
        },
      };
    });
  },

  completeStreaming: (sessionId) => {
    // 正常结束流式 (P0-2): 仅清理 AbortController, 重置 isStreaming
    // 不清除 progress (保留进度提示), 不设置 aborted (非中断)
    abortControllers.delete(sessionId);
    set((state) => {
      const existing = state.streams[sessionId];
      if (!existing) return state;
      return {
        streams: {
          ...state.streams,
          [sessionId]: {
            ...existing,
            isStreaming: false,
            streamStartedAt: null,
          },
        },
      };
    });
  },

  abortStream: (sessionId) => {
    const ctrl = abortControllers.get(sessionId);
    if (ctrl) {
      ctrl.abort();
      abortControllers.delete(sessionId);
    }
    set((state) => {
      const existing = state.streams[sessionId];
      if (!existing) return state;
      return {
        streams: {
          ...state.streams,
          [sessionId]: {
            ...existing,
            isStreaming: false,
            streamStartedAt: null,
            progress: null,
            aborted: true,
          },
        },
      };
    });
  },

  getAbortController: (sessionId) => abortControllers.get(sessionId),

  activeStreamCount: () => {
    const streams = get().streams;
    return Object.entries(streams).filter(([, s]) => s.isStreaming).length;
  },

  oldestActiveStreamSessionId: () => {
    const streams = get().streams;
    let oldest: { sid: string; startedAt: number } | null = null;
    for (const [sid, s] of Object.entries(streams)) {
      if (s.isStreaming && s.streamStartedAt != null) {
        if (!oldest || s.streamStartedAt < oldest.startedAt) {
          oldest = { sid, startedAt: s.streamStartedAt };
        }
      }
    }
    return oldest?.sid ?? null;
  },

  removeStream: (sessionId) => {
    abortControllers.delete(sessionId);
    set((state) => {
      const next = { ...state.streams };
      delete next[sessionId];
      return { streams: next };
    });
  },

  abortAllStreams: () => {
    for (const [, ctrl] of abortControllers) {
      try {
        ctrl.abort();
      } catch {
        // 忽略中止错误
      }
    }
    abortControllers.clear();
    set((state) => {
      const next: Record<string, StreamContext> = {};
      for (const [sid, s] of Object.entries(state.streams)) {
        next[sid] = { ...s, isStreaming: false, streamStartedAt: null, progress: null };
      }
      return { streams: next };
    });
  },

  handleSSEEvent: (sessionId, sseEvent) => {
    if (sseEvent.event === "message") {
      const data = sseEvent.data as {
        choices?: Array<{
          delta?: {
            content?: string;
            progress?: string;
            sources?: Source[];
            report_id?: string;
            file_path?: string;
            report_format?: string;
            tool_calls?: Array<{
              index?: number;
              id?: string;
              function?: { name?: string; arguments?: string };
            }>;
          };
          finish_reason?: string | null;
        }>;
      };
      const delta = data?.choices?.[0]?.delta;
      if (!delta) return;

      // content 走 RAF batching (P0-1 方案 A), 单独缓冲到 pendingDeltas
      if (delta.content) {
        get().appendContentDelta(sessionId, delta.content);
      }

      // 其他字段合并为单次 set (P0-1 方案 B), 避免多次触发订阅者重渲染
      const hasProgress = !!delta.progress;
      const hasSources = Array.isArray(delta.sources) && delta.sources.length > 0;
      const hasReportId = !!delta.report_id;
      const hasFilePath = !!delta.file_path;
      const hasReportFormat = !!delta.report_format;
      const hasToolCalls = Array.isArray(delta.tool_calls) && delta.tool_calls.length > 0;

      if (
        !hasProgress &&
        !hasSources &&
        !hasReportId &&
        !hasFilePath &&
        !hasReportFormat &&
        !hasToolCalls
      ) {
        return;
      }

      set((state) => {
        const existing = state.streams[sessionId];
        if (!existing) return state;

        const updated: StreamContext = { ...existing };

        if (hasProgress) {
          updated.progress = delta.progress!;
        }
        if (hasSources) {
          updated.sources = [...updated.sources, ...delta.sources!];
        }
        if (hasReportId) {
          updated.reportId = delta.report_id!;
        }
        if (hasFilePath) {
          updated.filePath = delta.file_path!;
        }
        if (hasReportFormat) {
          updated.reportFormat = delta.report_format!;
        }
        if (hasToolCalls) {
          // 处理 OpenAI 标准 delta.tool_calls (按 id 去重累加)
          const newToolCalls = [...updated.toolCalls];
          for (const tc of delta.tool_calls!) {
            if (tc.id && tc.function?.name) {
              newToolCalls.push({
                id: tc.id,
                type: "function",
                function: {
                  name: tc.function.name,
                  arguments: tc.function.arguments || "",
                },
              });
            }
          }
          updated.toolCalls = newToolCalls;
        }

        return { streams: { ...state.streams, [sessionId]: updated } };
      });
    } else if (sseEvent.event === "tool_call") {
      // 独立 tool_call 事件 (SSE event: tool_call) — 单独处理, 不频繁
      const data = sseEvent.data as {
        tool_name?: string;
        name?: string;
        input?: unknown;
        arguments?: string;
        output?: unknown;
      };
      const toolName = data.tool_name || data.name || "unknown";
      get().appendToolCall(sessionId, {
        id: `tc_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
        type: "function",
        function: {
          name: toolName,
          arguments:
            typeof data.arguments === "string"
              ? data.arguments
              : JSON.stringify(data.input ?? {}),
        },
      });
    }
  },
}));
