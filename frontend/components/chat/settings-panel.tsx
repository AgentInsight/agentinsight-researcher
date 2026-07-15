// components/chat/settings-panel.tsx
"use client";

import { useState, useEffect } from "react";
import { useAuthStore } from "@/lib/auth-store";
import { useSessionStore } from "@/lib/session-store";
import { useNavStore } from "@/lib/nav-store";
import { apiClient } from "@/lib/api-client";
import { X, FileText, Settings as SettingsIcon, Loader2, Check } from "lucide-react";
import { Tooltip } from "@/components/ui/tooltip";

/**
 * 设置面板 (slide-in 方式, 与历史报告面板同样方式)
 *
 * 内容:
 * 1. 会话报告配置 (与当前会话绑定)
 *    - 报告类型 / 报告格式 / 语言
 *
 * 替代原 /settings 页面 (作为 slide-in 面板集成到主对话区)
 */
export function SettingsPanel() {
  const { slideInPanel, closePanel } = useNavStore();
  const { getToken } = useAuthStore();
  const { currentSessionId, sessions, reportConfig, setReportConfig } = useSessionStore();

  const [reportType, setReportType] = useState(
    reportConfig.report_type || "detailed_report"
  );
  const [reportFormat, setReportFormat] = useState(
    reportConfig.report_format || "markdown"
  );
  const [language, setLanguage] = useState(reportConfig.language || "zh");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isOpen = slideInPanel === "settings";

  // 当前会话标题 (替代 ID 显示)
  const currentSessionTitle = sessions.find(
    (s) => s.session_id === currentSessionId
  )?.title || "未命名会话";

  // 切换会话时同步表单
  useEffect(() => {
    if (!isOpen) return;
    setReportType(reportConfig.report_type || "detailed_report");
    setReportFormat(reportConfig.report_format || "markdown");
    setLanguage(reportConfig.language || "zh");
  }, [reportConfig, isOpen]);

  const handleSave = async () => {
    setError(null);
    setSaving(true);
    try {
      const newConfig = {
        report_type: reportType,
        report_format: reportFormat,
        language,
      };
      setReportConfig(newConfig);

      if (currentSessionId) {
        const token = getToken();
        await apiClient.updateSessionConfig(currentSessionId, newConfig, token);
      }

      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  if (!isOpen) return null;

  return (
    <>
      {/* 遮罩 */}
      <div
        onClick={closePanel}
        className="fixed inset-0 z-40 overlay"
        style={{ backgroundColor: "var(--overlay-bg)" }}
      />

      {/* 面板 */}
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
            <SettingsIcon
              className="h-5 w-5"
              style={{ color: "var(--brand-primary)" }}
            />
            <h2
              className="text-base font-semibold"
              style={{ color: "var(--text-primary)" }}
            >
              设置
            </h2>
          </div>
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

        {/* 内容 */}
        <div className="flex-1 overflow-y-auto p-5 space-y-6">
          {/* 错误提示 */}
          {error && (
            <div
              className="px-3 py-2 text-sm rounded-md border"
              style={{
                color: "var(--color-danger)",
                backgroundColor: "var(--color-danger-bg)",
                borderColor: "var(--color-danger-border)",
              }}
            >
              {error}
            </div>
          )}

          {/* 1. 会话报告配置 */}
          <section
            className="rounded-lg border p-4 space-y-3"
            style={{
              borderColor: "var(--border-color)",
              backgroundColor: "var(--bg-muted)",
            }}
          >
            <div
              className="flex items-center gap-2 pb-2 border-b"
              style={{ borderColor: "var(--border-color)" }}
            >
              <FileText
                className="w-4 h-4"
                style={{ color: "var(--brand-primary)" }}
              />
              <h3
                className="text-sm font-semibold"
                style={{ color: "var(--text-primary)" }}
              >
                报告配置
              </h3>
            </div>

            <div
              className="px-2.5 py-1.5 text-xs rounded"
              style={{
                backgroundColor: currentSessionId
                  ? "var(--color-success-bg)"
                  : "var(--bg-card)",
                color: currentSessionId
                  ? "var(--color-success)"
                  : "var(--text-tertiary)",
              }}
            >
              {currentSessionId
                ? `当前会话: ${currentSessionTitle} (配置将保存到该会话)`
                : "未选择会话 (配置仅保存到本地)"}
            </div>

            <div>
              <label
                className="block text-xs font-medium mb-1"
                style={{ color: "var(--text-primary)" }}
              >
                报告类型
              </label>
              <select
                value={reportType}
                onChange={(e) => setReportType(e.target.value)}
                className="w-full rounded-md border px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--brand-primary)]"
                style={{
                  borderColor: "var(--border-color)",
                  backgroundColor: "var(--bg-card)",
                  color: "var(--text-primary)",
                }}
              >
                <option value="basic_report">基础报告</option>
                <option value="detailed_report">详细报告</option>
                <option value="deep_research">深度研究</option>
                <option value="summary">摘要</option>
                <option value="subtopics">子主题</option>
              </select>
            </div>

            <div>
              <label
                className="block text-xs font-medium mb-1"
                style={{ color: "var(--text-primary)" }}
              >
                报告格式
              </label>
              <select
                value={reportFormat}
                onChange={(e) => setReportFormat(e.target.value)}
                className="w-full rounded-md border px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--brand-primary)]"
                style={{
                  borderColor: "var(--border-color)",
                  backgroundColor: "var(--bg-card)",
                  color: "var(--text-primary)",
                }}
              >
                <option value="markdown">Markdown</option>
                <option value="html">HTML</option>
                <option value="pdf">PDF</option>
                <option value="docx">DOCX</option>
                <option value="json">JSON</option>
              </select>
            </div>

            <div>
              <label
                className="block text-xs font-medium mb-1"
                style={{ color: "var(--text-primary)" }}
              >
                报告语言
              </label>
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                className="w-full rounded-md border px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--brand-primary)]"
                style={{
                  borderColor: "var(--border-color)",
                  backgroundColor: "var(--bg-card)",
                  color: "var(--text-primary)",
                }}
              >
                <option value="zh">中文</option>
                <option value="en">English</option>
              </select>
            </div>

            <button
              onClick={handleSave}
              disabled={saving}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md transition-colors hover:opacity-90 disabled:opacity-50"
              style={{
                backgroundColor: "var(--brand-primary)",
                color: "var(--text-on-brand)",
              }}
            >
              {saving ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  保存中...
                </>
              ) : saved ? (
                <>
                  <Check className="h-3.5 w-3.5" />
                  已保存
                </>
              ) : (
                "保存"
              )}
            </button>
          </section>
        </div>
      </aside>
    </>
  );
}
