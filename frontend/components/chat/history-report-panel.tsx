// components/chat/history-report-panel.tsx
"use client";

import { useEffect, useState } from "react";
import { apiClient } from "@/lib/api-client";
import { useAuthStore } from "@/lib/auth-store";
import { useSessionStore } from "@/lib/session-store";
import { useNavStore } from "@/lib/nav-store";
import { TTLCache } from "@/lib/cache";
import { X, RefreshCw, FileText, Loader2, Download } from "lucide-react";
import { Tooltip } from "@/components/ui/tooltip";

/**
 * 历史报告面板 (slide-in 方式)
 *
 * 参考 static/index.html 的 #reports-panel 实现:
 * - 从右侧滑入的模态面板
 * - 显示当前会话的所有报告列表
 * - 每项报告卡片含 5 种格式下载链接 (markdown/html/pdf/docx/json)
 * - 点击遮罩或关闭按钮关闭
 *
 * 后端 API:
 * - GET /v1/reports/session/{session_id}  获取当前会话报告列表
 * - GET /v1/reports/{report_id}/download?format=  下载指定格式
 */
type ReportItem = {
  report_id: string;
  session_id: string;
  query: string;
  report_format: string;
  created_at: string;
  agent_role?: string;
};

/**
 * 任务2: 历史报告模块级缓存 (session 维度)
 * 避免每次打开面板都重新 fetch, 采用 stale-while-revalidate 策略
 *
 * TTL + LRU 控制: 防止模块级 Map 永不清理导致内存泄漏 (P0-10)
 * - TTL=2 分钟 (报告列表相对低频变化), maxSize=20
 */
const reportCache = new TTLCache<string, ReportItem[]>(2 * 60 * 1000, 20);

export function HistoryReportPanel() {
  const { slideInPanel, closePanel } = useNavStore();
  const { getToken } = useAuthStore();
  const { currentSessionId, sessions } = useSessionStore();
  const [reports, setReports] = useState<ReportItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isOpen = slideInPanel === "history";

  // 当前会话标题 (替代 ID 显示)
  const currentSessionTitle = sessions.find(
    (s) => s.session_id === currentSessionId
  )?.title || "未命名会话";

  // 任务2: 打开面板时用缓存即时渲染, 后台静默刷新
  useEffect(() => {
    if (!isOpen) return;
    if (!currentSessionId) {
      setReports([]);
      setError(null);
      return;
    }
    // 1. 用缓存即时填充 (避免 loading 闪烁)
    const cached = reportCache.get(currentSessionId);
    if (cached) setReports(cached);
    // 2. 后台静默刷新
    loadReports(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, currentSessionId]);

  const loadReports = async (silent = false) => {
    if (!currentSessionId) return;
    // 无缓存时才显示 loading
    if (!silent && !reportCache.has(currentSessionId)) setLoading(true);
    setError(null);
    try {
      const token = getToken();
      const data = await apiClient.getSessionReports(currentSessionId, token);
      const arr = Array.isArray(data) ? data : [];
      setReports(arr);
      // 更新缓存
      reportCache.set(currentSessionId, arr);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载报告列表失败");
    } finally {
      setLoading(false);
    }
  };

  // P2-5: 内联 handleDownload, 用 console.error 替代 alert 避免阻塞主线程
  // 任务4补充: 失败时解析 HTTP 错误详情 (含状态码和响应体), 便于诊断后端 500 根因
  const handleDownload = async (reportId: string, format: string) => {
    const token = getToken();
    const url = apiClient.getReportDownloadUrl(reportId, format);
    try {
      const res = await fetch(url, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) {
        // 解析响应体错误详情 (后端 FastAPI HTTPException 返回 {"detail": "..."} JSON)
        const text = await res.text().catch(() => "");
        let detail = text;
        try {
          const j = JSON.parse(text);
          detail = j.detail || j.message || text;
        } catch {
          /* 非 JSON 保持原文 */
        }
        throw new Error(`下载失败 (HTTP ${res.status}): ${detail || res.statusText}`);
      }
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `report-${reportId.slice(0, 8)}.${format}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(a.href);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "下载失败";
      console.error("下载失败:", msg);
      setError(msg);
    }
  };

  if (!isOpen) return null;

  const formats = [
    { label: "Markdown", value: "markdown" },
    { label: "HTML", value: "html" },
    { label: "PDF", value: "pdf" },
    { label: "DOCX", value: "docx" },
    { label: "JSON", value: "json" },
  ];

  return (
    <>
      {/* 遮罩 */}
      <div
        onClick={closePanel}
        className="fixed inset-0 z-40 overlay"
        style={{ backgroundColor: "var(--overlay-bg)" }}
      />

      {/* 面板 (从右侧滑入) */}
      <aside
        className="fixed right-0 top-0 bottom-0 z-50 flex flex-col slide-in-panel"
        style={{
          width: "480px",
          maxWidth: "90vw",
          backgroundColor: "var(--bg-card)",
          borderLeft: "1px solid var(--border-color)",
          boxShadow: "var(--shadow-lg)",
        }}
      >
        {/* 头部 */}
        <div
          className="flex items-center justify-between px-5 py-4 border-b"
          style={{ borderColor: "var(--border-color)" }}
        >
          <div className="flex items-center gap-2">
            <FileText
              className="h-5 w-5"
              style={{ color: "var(--brand-primary)" }}
            />
            <h2
              className="text-base font-semibold"
              style={{ color: "var(--text-primary)" }}
            >
              历史报告
            </h2>
          </div>
          <div className="flex items-center gap-2">
            <Tooltip content="刷新报告列表">
              <button
                onClick={() => loadReports()}
                disabled={loading}
                className="p-1.5 rounded-md hover:bg-hover transition-colors"
                style={{ color: "var(--text-secondary)" }}
                aria-label="刷新"
              >
                <RefreshCw
                  className={`h-4 w-4 ${loading ? "animate-spin" : ""}`}
                />
              </button>
            </Tooltip>
            <Tooltip content="关闭">
              <button
                onClick={closePanel}
                className="p-1.5 rounded-md hover:bg-hover transition-colors"
                style={{ color: "var(--text-secondary)" }}
                aria-label="关闭"
              >
                <X className="h-4 w-4" />
              </button>
            </Tooltip>
          </div>
        </div>

        {/* 工具栏 */}
        <div
          className="px-5 py-2 border-b text-xs"
          style={{
            borderColor: "var(--border-color)",
            color: "var(--text-tertiary)",
          }}
        >
          {currentSessionId
            ? `当前会话: ${currentSessionTitle} (共 ${reports.length} 份报告)`
            : "未选择会话"}
        </div>

        {/* 报告列表 */}
        <div className="flex-1 overflow-y-auto p-5">
          {error && (
            <div
              className="px-3 py-2 text-sm rounded-md border mb-3"
              style={{
                color: "var(--color-danger)",
                backgroundColor: "var(--color-danger-bg)",
                borderColor: "var(--color-danger-border)",
              }}
            >
              {error}
            </div>
          )}

          {loading && reports.length === 0 && (
            <div
              className="flex items-center justify-center gap-2 text-sm py-12"
              style={{ color: "var(--text-tertiary)" }}
            >
              <Loader2 className="h-4 w-4 animate-spin" />
              加载中...
            </div>
          )}

          {!loading && reports.length === 0 && !error && (
            <div
              className="text-center text-sm py-12"
              style={{ color: "var(--text-tertiary)" }}
            >
              <FileText className="h-10 w-10 mx-auto mb-3 opacity-40" />
              <p>暂无报告</p>
              <p className="text-xs mt-1">完成一次研究后自动刷新</p>
            </div>
          )}

          <div className="space-y-3">
            {reports.map((r) => {
              const created = r.created_at
                ? new Date(r.created_at).toLocaleString("zh-CN")
                : "—";
              const queryShort =
                (r.query || "").substring(0, 80) +
                ((r.query || "").length > 80 ? "..." : "");
              return (
                <div
                  key={r.report_id}
                  className="p-3 rounded-md border"
                  style={{
                    borderColor: "var(--border-color)",
                    backgroundColor: "var(--bg-muted)",
                  }}
                >
                  <div className="flex justify-between items-start mb-2">
                    <Tooltip content={r.query || ""}>
                      <div
                        className="text-sm font-medium flex-1 min-w-0 truncate"
                        style={{ color: "var(--text-primary)" }}
                      >
                        {queryShort || "(无标题)"}
                      </div>
                    </Tooltip>
                    <div
                      className="text-xs ml-2 flex-shrink-0"
                      style={{ color: "var(--text-tertiary)" }}
                    >
                      {created}
                    </div>
                  </div>

                  {r.agent_role && (
                    <div
                      className="inline-block px-2 py-0.5 text-[10px] rounded mb-2"
                      style={{
                        backgroundColor: "var(--brand-primary-light)",
                        color: "var(--brand-primary)",
                      }}
                    >
                      {r.agent_role}
                    </div>
                  )}

                  <div className="flex flex-wrap gap-1.5">
                    {formats.map((f) => (
                      <Tooltip key={f.value} content={`下载 ${f.label} 格式`}>
                        <button
                          onClick={() => handleDownload(r.report_id, f.value)}
                          className="inline-flex items-center gap-1 px-2.5 py-1 text-xs rounded-md transition-colors hover:opacity-80"
                          style={{
                            backgroundColor: "var(--brand-primary-light)",
                            color: "var(--brand-primary)",
                            border: "1px solid var(--border-color)",
                          }}
                        >
                          <Download className="h-3 w-3" />
                          {f.label}
                        </button>
                      </Tooltip>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </aside>
    </>
  );
}
