// app/agent/researcher/chat/page.tsx
"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { ChatInput } from "@/components/chat/chat-input";
import { ChatMessages } from "@/components/chat/chat-messages";
import { ChatSidebar } from "@/components/chat/chat-sidebar";
import { ReviewPanel } from "@/components/review/review-panel";
import { HistoryReportPanel } from "@/components/chat/history-report-panel";
import { SettingsPanel } from "@/components/chat/settings-panel";
import { ToastContainer, useToast } from "@/components/ui/toast";
import { useAuthStore } from "@/lib/auth-store";
import { useSessionStore } from "@/lib/session-store";
import { useAgentStore } from "@/lib/agent-store";
import { useNavStore } from "@/lib/nav-store";
import { useStreamStore, MAX_BACKGROUND_STREAMS } from "@/lib/stream-store";
import { WsClient } from "@/lib/ws-client";
import { apiClient } from "@/lib/api-client";
import type { ChatMessage } from "@/lib/types";
import type { ToolCall, Source } from "@/lib/types";
import {
  ClipboardList,
  Settings as SettingsIcon,
  Menu,
  X,
  Bot,
} from "lucide-react";
import { Tooltip } from "@/components/ui/tooltip";

/**
 * 模块级空数组常量 (引用稳定)
 *
 * 关键修复: useShallow 的 shallow() 对对象字段做 Object.is 比较,
 * 内联 [] 字面量每次创建新引用, Object.is([], []) → false,
 * 导致 shallow 认为对象变化, useSyncExternalStore 无限重渲染 → React #185.
 * 用模块级常量确保引用稳定: Object.is(EMPTY_MESSAGES, EMPTY_MESSAGES) → true.
 */
const EMPTY_MESSAGES: ChatMessage[] = [];
const EMPTY_TOOL_CALLS: ToolCall[] = [];
const EMPTY_SOURCES: Source[] = [];

/**
 * 聊天主页 (主对话区域)
 *
 * 整体结构: 会话导航栏 (可选) + 主对话区 (横向 flex)
 *
 * 主对话区 3 区域 Flexbox 布局 (顶部/底部基于内容, 中间 flex-1 自适应):
 * - 顶部 (flex-none): 左侧会话列表切换图标 + 居中智能体名 + 右侧历史报告/设置图标
 * - 中间 (flex-1 min-h-0): 对话区域 (Markdown 渲染 + 流式显示)
 * - 底部 (flex-none): 输入框, 最右边附件 + 发送图标按钮
 *
 * 多会话研究并发 (任务3):
 * - 最多 1 前台 + 1 后台 = 2 个并发研究实例 (MAX_BACKGROUND_STREAMS = 1)
 * - 切换会话时旧会话的流式请求不中断 (后台继续, 写入 stream-store)
 * - 新会话发请求时若后台流已满, 弹 window.confirm 提示用户是否停止最早的请求
 * - 用户确认后中止最早的请求并开始新请求; 取消则不开始
 *
 * Per-session 隔离 (任务2):
 * - 流式状态 (messages/isStreaming/toolCalls/sources/progress/reportId 等) 按 sessionId 隔离
 * - 输入框草稿/文件列表按 sessionId 隔离 (见 session-store)
 * - 切换会话仅切换 currentSessionId, 不动其他会话状态
 *
 * WebSocket (人在回路):
 * - 按 currentSessionId 维护单一 WsClient 实例
 * - 收到 human_feedback_request 时写入 stream-store 的 reviewRequest
 */
export default function ChatPage() {
  const { getToken } = useAuthStore();
  // 细粒度 selector: 只订阅 currentSessionId (P0-6), 避免整个 session store 变化触发重渲
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  // 细粒度 selector: reportConfig 和 setReportConfig (P0-6)
  const reportConfig = useSessionStore((s) => s.reportConfig);
  const setReportConfig = useSessionStore((s) => s.setReportConfig);
  const { getCurrentAgent } = useAgentStore();
  const { togglePanel, slideInPanel, sessionNavVisible, toggleSessionNav } = useNavStore();

  // 从 stream-store 读取当前会话的流式上下文 (per-session 隔离)
  // 切换会话时自动切换到新会话的 stream, 旧会话的流在后台继续
  // 细粒度 selector + useShallow (P0-6): 仅在任一字段实际变化时触发重渲
  // useShallow 对返回对象做浅比较, 避免 selector 每次返回新对象导致重渲
  const {
    messages,
    isStreaming,
    toolCalls,
    sources,
    progress,
    reportId,
    reportFormat,
    filePath,
    reviewRequest,
    loadingHistory,
  } = useStreamStore(
    useShallow((s) => {
      const st = currentSessionId ? s.streams[currentSessionId] : undefined;
      return {
        messages: st?.messages ?? EMPTY_MESSAGES,
        isStreaming: st?.isStreaming ?? false,
        toolCalls: st?.toolCalls ?? EMPTY_TOOL_CALLS,
        sources: st?.sources ?? EMPTY_SOURCES,
        progress: st?.progress ?? null,
        reportId: st?.reportId ?? null,
        reportFormat: st?.reportFormat ?? null,
        filePath: st?.filePath ?? null,
        reviewRequest: st?.reviewRequest ?? null,
        loadingHistory: st?.loadingHistory ?? false,
      };
    })
  );

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const wsClientRef = useRef<WsClient | null>(null);
  const { toasts, addToast, removeToast } = useToast();

  // 任务5: 消息分页 — 每次只显示最近 N 条, 上滚加载更多
  const INITIAL_VISIBLE = 10;
  const LOAD_MORE_COUNT = 10;
  const [visibleCount, setVisibleCount] = useState(INITIAL_VISIBLE);
  const [loadingMore, setLoadingMore] = useState(false);
  const prevScrollHeightRef = useRef(0);

  // 兼容包装: 将原 setError 调用转为 Toast 错误提示
  const setError = useCallback(
    (msg: string | null) => {
      if (msg) addToast({ type: "error", message: msg });
    },
    [addToast]
  );

  const currentAgent = getCurrentAgent();

  // 确保当前会话的 StreamContext 存在
  useEffect(() => {
    if (currentSessionId) {
      useStreamStore.getState().ensureStream(currentSessionId);
    }
  }, [currentSessionId]);

  // 首次加载: 获取会话列表, 默认选择第一个; 无会话则自动新建一个
  useEffect(() => {
    const initSession = async () => {
      // 如果已有选中会话, 不重复初始化
      if (useSessionStore.getState().currentSessionId) return;

      try {
        const token = getToken();
        const data = await apiClient.getSessions(token);
        useSessionStore.getState().setSessions(data);

        if (data && data.length > 0) {
          // 有会话, 选中第一个
          useSessionStore.getState().setCurrentSession(data[0].session_id);
        } else if (!useSessionStore.getState().isCreatingSession) {
          // 无会话, 自动新建
          useSessionStore.getState().setCreatingSession(true);
          try {
            const session = await apiClient.createSession(token, "新会话");
            if (session && session.session_id) {
              useSessionStore.getState().addSession(session);
            }
          } finally {
            useSessionStore.getState().setCreatingSession(false);
          }
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "初始化会话失败");
      }
    };
    initSession();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentAgent]);

  // 切换会话时加载历史消息 (仅首次进入该会话时加载, 不中断后台流)
  // 任务3: 添加 AbortController + cleanup, 避免切换会话时旧请求失败显示 network error
  // 性能优化 (新建会话卡死修复): 减少 set() 调用次数, 404 不显示错误
  useEffect(() => {
    if (!currentSessionId) return;

    const state = useStreamStore.getState();
    const existing = state.getStream(currentSessionId);

    // 已加载过历史 或 正在流式 (后台流在跑) → 不重复加载, 避免覆盖流式中的 messages
    if (existing?.historyLoaded || existing?.isStreaming) return;

    // 任务3: AbortController 用于切换会话时取消旧请求
    const abortCtrl = new AbortController();
    // 记录当前会话 ID, 用于 catch 块判断是否是过期请求
    const targetSessionId = currentSessionId;

    const loadHistory = async () => {
      useStreamStore.getState().setLoadingHistory(targetSessionId, true);
      try {
        const token = getToken();
        const [msgResp, config] = await Promise.all([
          apiClient.getSessionMessages(targetSessionId, token, 50, 0),
          apiClient.getSessionConfig(targetSessionId, token).catch(() => null),
        ]);

        // 任务3: 请求完成后再校验会话是否已切换 (双重防护竞态)
        if (abortCtrl.signal.aborted) return;
        if (useSessionStore.getState().currentSessionId !== targetSessionId) return;

        const historyMessages: ChatMessage[] = msgResp.messages.map((m) => ({
          role: m.role,
          content: m.content,
        }));
        // 性能优化: 省略 clearStreamingState (新建会话/首次加载历史时无 streaming state 需要清除)
        // 减少 set() 调用次数, 避免多次 set 触发 ChatPage 全量重渲导致卡死
        useStreamStore.getState().setMessages(targetSessionId, historyMessages);

        if (config) {
          setReportConfig(config);
        }
        useStreamStore.getState().setHistoryLoaded(targetSessionId, true);
      } catch (err) {
        // 任务3: 已 abort 的请求不显示错误 (避免 network error)
        if (abortCtrl.signal.aborted) return;
        // 会话已切换, 旧请求的错误不显示在新会话上下文
        if (useSessionStore.getState().currentSessionId !== targetSessionId) return;
        // 性能优化: 404 (空会话, 后端无消息记录) 不显示错误, 直接标记 historyLoaded
        // 避免新建空会话时显示 "加载历史消息失败" 错误, 并阻止重复加载
        const is404 = err instanceof Error && err.message.includes("HTTP 404");
        if (is404) {
          useStreamStore.getState().setHistoryLoaded(targetSessionId, true);
          return;
        }
        setError(err instanceof Error ? err.message : "加载历史消息失败");
      } finally {
        useStreamStore.getState().setLoadingHistory(targetSessionId, false);
      }
    };

    loadHistory();

    // 任务3: cleanup — 切换会话或组件卸载时 abort 旧请求
    return () => {
      abortCtrl.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentSessionId]);

  // WebSocket 连接 (人在回路, 按 currentSessionId 维护单一实例)
  // agentName 从 agent-store 获取, 用于 Nginx 按 agent 路径分发 (方案B)
  useEffect(() => {
    if (!currentSessionId) return;

    // 关闭旧连接
    if (wsClientRef.current) {
      wsClientRef.current.close();
      wsClientRef.current = null;
    }

    // 从 store 获取当前 agent name (用于 WebSocket URL 路由)
    const agentName = useAgentStore.getState().getCurrentAgent().name;

    const ws = new WsClient(currentSessionId, (msg) => {
      if (msg.type === "human_feedback_request") {
        useStreamStore.getState().setReviewRequest(currentSessionId, {
          node: "审核",
          content: msg.message,
        });
      } else if (msg.type === "error") {
        setError(msg.message);
      }
    }, agentName);
    ws.connect();
    wsClientRef.current = ws;

    return () => {
      ws.close();
      wsClientRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentSessionId]);

  // 任务5: 切换会话时重置分页
  useEffect(() => {
    setVisibleCount(INITIAL_VISIBLE);
    setLoadingMore(false);
  }, [currentSessionId]);

  // 任务5: 消息分页 — 只渲染最近 visibleCount 条
  // 性能优化 (P1-13): useMemo 缓存, 避免 messages 引用未变时每次 render 都 slice 新数组
  const visibleMessages = useMemo(
    () => messages.slice(Math.max(0, messages.length - visibleCount)),
    [messages, visibleCount]
  );
  const hasMore = messages.length > visibleCount;

  // 任务5: 上滚加载更多
  const handleScroll = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container || loadingMore || !hasMore) return;

    // 滚动到顶部附近 (scrollTop < 50px) 时加载更多
    if (container.scrollTop < 50) {
      setLoadingMore(true);
      prevScrollHeightRef.current = container.scrollHeight;

      // 模拟异步加载 (避免同步 setState 导致滚动跳动)
      requestAnimationFrame(() => {
        setVisibleCount((prev: number) => prev + LOAD_MORE_COUNT);
        setLoadingMore(false);
      });
    }
  }, [loadingMore, hasMore]);

  // 任务5: 加载更多后保持滚动位置 (避免跳到顶部)
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container || !loadingMore) return;
    // 恢复滚动位置: 新 scrollHeight - 旧 scrollHeight = 新增内容高度
    const newScrollHeight = container.scrollHeight;
    const diff = newScrollHeight - prevScrollHeightRef.current;
    if (diff > 0) {
      container.scrollTop = diff;
    }
  }, [visibleMessages, loadingMore]);

  // 自动滚动到底部 (仅当用户已在底部附近时, 避免上滚查看历史时被拉回)
  const isNearBottomRef = useRef(true);
  // 性能优化 (P1-14): 依赖改为原始值 (messages.length, lastMsgLen, progress)
  // 避免依赖 messages 数组引用 (流式时每个 token 都创建新数组引用, 但内容长度变化更小)
  // 行为不变: 消息条数变化 / 最后一条消息内容变化 / 进度变化时触发自动滚动
  const lastMsgLen = messages[messages.length - 1]?.content.length ?? 0;
  useEffect(() => {
    // 仅当用户在底部附近 (距底 < 100px) 时自动滚动
    if (!isNearBottomRef.current) return;
    // 流式时用 auto 避免平滑滚动追赶不上 token 速度
    messagesEndRef.current?.scrollIntoView({ behavior: "auto" });
  }, [messages.length, lastMsgLen, progress]);

  // 任务5: 监听滚动位置, 更新 isNearBottomRef
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    const onScroll = () => {
      const distFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
      isNearBottomRef.current = distFromBottom < 100;
    };
    container.addEventListener("scroll", onScroll, { passive: true });
    return () => container.removeEventListener("scroll", onScroll);
  }, []);

  // 注意: 不在组件卸载时调用 abortAllStreams()
  // 原因: 用户切换到 MCP 页面时 ChatPage 卸载, 但流式研究应在后台继续
  // (stream-store 为全局 zustand store, 跨页面共享)
  // 浏览器关闭标签页时会自动中止 fetch 请求, 无需手动清理

  const handleSend = useCallback(
    async (content: string) => {
      // 从 store 获取最新 currentSessionId (而非闭包捕获值)
      // 这样切换会话不会重建 handleSend, 旧的 async 执行完全脱离组件生命周期
      const sid = useSessionStore.getState().currentSessionId;
      if (!sid) {
        // 任务2: 不应出现此情况 (初始化/删除逻辑会自动选择或新建会话)
        // 若短暂出现, 提示加载中而非要求用户操作
        setError("会话正在加载中, 请稍候");
        return;
      }

      const streamState = useStreamStore.getState();
      const currentStream = streamState.getStream(sid);
      const currentIsStreaming = currentStream?.isStreaming ?? false;

      // 当前会话已在流式 → 直接返回 (按钮应已是停止状态)
      if (currentIsStreaming) {
        return;
      }

      // 任务3: 多会话研究并发限制
      // 总并发上限 = MAX_BACKGROUND_STREAMS + 1 = 2 (1前台 + 1后台)
      // 当前会话将成为前台; 其他正在流式的会话是后台
      // 若后台流数 > MAX_BACKGROUND_STREAMS (即已有2个其他会话在跑), 提示用户
      const activeCount = streamState.activeStreamCount();
      if (activeCount > MAX_BACKGROUND_STREAMS) {
        const oldestSid = streamState.oldestActiveStreamSessionId();
        if (oldestSid && oldestSid !== sid) {
          const confirmed = window.confirm(
            `当前已有 ${MAX_BACKGROUND_STREAMS + 1} 个研究在进行中，是否停止最早的研究并开始新的？`
          );
          if (!confirmed) {
            return; // 用户取消, 不开始新请求
          }
          streamState.abortStream(oldestSid);
        }
      }

      // 任务1: 新会话首次输入时, 自动将会话名改为输入内容的前32个字符
      const currentSession = useSessionStore.getState().sessions.find(
        (s) => s.session_id === sid
      );
      const currentMsgs =
        useStreamStore.getState().getStream(sid)?.messages ?? [];
      const isNewSessionFirstMessage =
        currentSession?.title === "新会话" && currentMsgs.length === 0;
      if (isNewSessionFirstMessage) {
        const newTitle = content.slice(0, 32).trim() || "新会话";
        // 先更新本地状态 (即时反馈, 侧边栏立即显示新标题)
        useSessionStore.getState().updateSessionTitle(sid, newTitle);
        // 异步同步到后端 (fire-and-forget, 不阻塞主聊天流程)
        void (async () => {
          try {
            const token = getToken();
            await apiClient.renameSession(sid, newTitle, token);
          } catch {
            // 静默失败, 不影响主流程 (下次刷新会从后端重新加载)
          }
        })();
      }

      // 追加用户消息 + assistant 占位消息到 stream-store
      const userMessage: ChatMessage = { role: "user", content };
      const assistantMessage: ChatMessage = { role: "assistant", content: "" };
      streamState.appendMessage(sid, userMessage);
      streamState.appendMessage(sid, assistantMessage);

      // 开始流式 (创建 AbortController, 设置 isStreaming=true)
      const abortCtrl = streamState.startStreaming(sid);

      // 获取选中的上传文件 ID (任务1: 文件上传全链路)
      const { files } = useSessionStore.getState();
      const selectedFileIds = (files[sid] || [])
        .filter((f) => f.selected)
        .map((f) => f.file_id);

      // 从 store 获取最新 reportConfig (而非闭包捕获值)
      const currentReportConfig = useSessionStore.getState().reportConfig;

      try {
        const token = getToken();
        // 构建消息列表 (含刚追加的 user message)
        const allMessages = [...currentMsgs, userMessage];

        const options: {
          report_type?: string;
          report_format?: string;
          language?: string;
          uploaded_files?: string[];
          signal?: AbortSignal;
        } = {};
        if (currentReportConfig.report_type) options.report_type = currentReportConfig.report_type;
        if (currentReportConfig.report_format) options.report_format = currentReportConfig.report_format;
        if (currentReportConfig.language) options.language = currentReportConfig.language;
        if (selectedFileIds.length > 0) options.uploaded_files = selectedFileIds;
        options.signal = abortCtrl.signal;

        for await (const sseEvent of apiClient.chatStream(
          allMessages,
          sid,
          token,
          options
        )) {
          useStreamStore.getState().handleSSEEvent(sid, sseEvent);
        }
      } catch (err) {
        // 判断是否是真正的用户主动中断 (仅两种合法路径):
        //   1. 用户点击"停止生成"按钮 → handleStop → abortStream(sid) → aborted=true
        //   2. 第3个研究请求触发 LRU 淘汰 → abortStream(oldestSid) → oldestSid 的 aborted=true
        // 其他所有情况 (网络波动 / 服务器关闭连接 / SSE 解析错误等) 均不视为中断,
        // 按正常错误处理, 避免误显示"研究已被中断"
        const currentStream = useStreamStore.getState().getStream(sid);
        const isAborted =
          (err instanceof Error && err.name === "AbortError") ||
          currentStream?.aborted === true;

        if (isAborted) {
          // 真正的中断 (用户停止 / LRU 淘汰), 保留已有内容并追加中断提示
          const lastMsg = currentStream?.messages[currentStream.messages.length - 1];
          if (lastMsg && lastMsg.role === "assistant") {
            if (!lastMsg.content || lastMsg.content.length === 0) {
              useStreamStore.getState().setLastMessageContent(
                sid,
                "（研究已被中断）"
              );
            } else {
              // 已有部分内容: 追加中断提示, 保留已生成内容
              useStreamStore.getState().setLastMessageContent(
                sid,
                lastMsg.content + "\n\n---\n\n*（研究已被中断）*"
              );
            }
          }
        } else {
          const errorMsg = err instanceof Error ? err.message : String(err);
          setError(errorMsg);
          useStreamStore.getState().setLastMessageContent(
            sid,
            `⚠️ 错误: ${errorMsg}`
          );
        }
      } finally {
        // 性能优化 (P0-2): 用 completeStreaming 替代 stopStreaming
        // completeStreaming 清理 AbortController 并设置 isStreaming=false, streamStartedAt=null
        // 但不清除 progress (保留最后的进度提示), 流正常结束的清理路径
        useStreamStore.getState().completeStreaming(sid);
      }
    },
    [getToken, setError]
  );

  // 任务1: 停止生成 (用户点击停止按钮)
  const handleStop = useCallback(() => {
    const sid = useSessionStore.getState().currentSessionId;
    if (sid) {
      useStreamStore.getState().abortStream(sid);
    }
  }, []);

  const handleFeedback = useCallback(
    async (feedback: string) => {
      if (!currentSessionId) return;
      const token = getToken();
      await apiClient.submitFeedback(currentSessionId, feedback, token);
      useStreamStore.getState().setReviewRequest(currentSessionId, null);
    },
    [currentSessionId, getToken]
  );

  return (
    <div className="flex h-full overflow-hidden">
      {/* ===== 会话导航栏 (由顶部图标控制显示/隐藏, 默认隐藏) ===== */}
      {sessionNavVisible && <ChatSidebar />}

      {/* ===== 主对话区 (3 区域 Flexbox 布局, 统一 --bg-card 白色消除割裂感) ===== */}
      <div
        className="flex-1 flex flex-col min-w-0"
        style={{ backgroundColor: "var(--bg-card)" }}
      >
        {/* ===== 顶部: 左侧菜单图标 + 居中标题 + 右侧历史/设置图标 (淡化边界, 融合整体) ===== */}
        <div
          className="header-blend flex-none flex items-center justify-between px-3 py-2.5"
          style={{
            backgroundColor: "var(--bg-card)",
          }}
        >
          {/* 左侧: 菜单图标 (切换会话列表) */}
          <div className="flex items-center gap-1 w-20">
            <Tooltip content={sessionNavVisible ? "隐藏会话列表" : "显示会话列表"}>
              <button
                onClick={toggleSessionNav}
                className="p-2 rounded-md hover:bg-hover transition-colors"
                style={{
                  color: sessionNavVisible
                    ? "var(--brand-primary)"
                    : "var(--text-secondary)",
                  backgroundColor: sessionNavVisible
                    ? "var(--bg-active)"
                    : "transparent",
                }}
                aria-label={sessionNavVisible ? "隐藏会话列表" : "显示会话列表"}
              >
                {sessionNavVisible ? (
                  <X className="h-4 w-4" />
                ) : (
                  <Menu className="h-4 w-4" />
                )}
              </button>
            </Tooltip>
          </div>

          {/* 居中标题: 图标 + 智能体名字 */}
          <Tooltip content={currentAgent?.displayName || ""}>
            <h1
              className="text-sm font-semibold truncate flex-1 text-center px-2 flex items-center justify-center gap-1.5"
              style={{ color: "var(--text-primary)" }}
            >
              <Bot
                className="h-4 w-4 flex-shrink-0"
                style={{ color: "var(--brand-primary)" }}
              />
              <span className="truncate">{currentAgent?.displayName || "智能体"}</span>
            </h1>
          </Tooltip>

          {/* 右侧: 历史报告图标 + 设置图标 */}
          <div className="flex items-center gap-1 w-20 justify-end">
            <Tooltip content="查看历史报告">
              <button
                onClick={() => togglePanel("history")}
                className="p-2 rounded-md hover:bg-hover transition-colors"
                style={{
                  color:
                    slideInPanel === "history"
                      ? "var(--brand-primary)"
                      : "var(--text-secondary)",
                  backgroundColor:
                    slideInPanel === "history"
                      ? "var(--bg-active)"
                      : "transparent",
                }}
                aria-label="查看历史报告"
              >
                <ClipboardList className="h-4 w-4" />
              </button>
            </Tooltip>
            <Tooltip content="打开设置">
              <button
                onClick={() => togglePanel("settings")}
                className="p-2 rounded-md hover:bg-hover transition-colors"
                style={{
                  color:
                    slideInPanel === "settings"
                      ? "var(--brand-primary)"
                      : "var(--text-secondary)",
                  backgroundColor:
                    slideInPanel === "settings"
                      ? "var(--bg-active)"
                      : "transparent",
                }}
                aria-label="打开设置"
              >
                <SettingsIcon className="h-4 w-4" />
              </button>
            </Tooltip>
          </div>
        </div>

        {/* ===== 中间: 对话区域 (flex-1 自适应填充) ===== */}
        <div
          ref={scrollContainerRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto min-h-0"
        >
          {/* Toast 提示容器 (右上角, 替代内联错误提示) */}
          <ToastContainer toasts={toasts} onClose={removeToast} />

          {/* 任务5: 上滚加载更多提示 */}
          {hasMore && (
            <div className={`load-more-trigger ${loadingMore ? "loading" : ""}`}>
              {loadingMore ? "加载中..." : "↑ 向上滚动加载更多历史消息"}
            </div>
          )}

          {loadingHistory ? (
            <div
              className="text-center text-sm py-8"
              style={{ color: "var(--text-tertiary)" }}
            >
              加载历史消息...
            </div>
          ) : (
            <ChatMessages
              messages={visibleMessages}
              toolCalls={toolCalls}
              sources={sources}
              isStreaming={isStreaming}
              progress={progress}
              reportId={reportId}
              reportFormat={reportFormat}
              filePath={filePath}
            />
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* 未选择会话提示 (仅加载中短暂显示, 自动选择/新建逻辑会尽快填充 currentSessionId) */}
        {!currentSessionId && !isStreaming && (
          <div
            className="px-4 py-1.5 text-center text-xs border-t"
            style={{
              color: "var(--text-tertiary)",
              backgroundColor: "var(--bg-muted)",
              borderColor: "var(--border-color-light)",
            }}
          >
            正在加载会话...
          </div>
        )}

        {/* 人在回路审核 */}
        {reviewRequest && (
          <ReviewPanel request={reviewRequest} onSubmit={handleFeedback} />
        )}

        {/* ===== 底部: 输入框 (固定高度, 基于内容) ===== */}
        <div className="flex-none">
          <ChatInput onSend={handleSend} onStop={handleStop} />
        </div>
      </div>

      {/* ===== Slide-in 面板 ===== */}
      <HistoryReportPanel />
      <SettingsPanel />
    </div>
  );
}
