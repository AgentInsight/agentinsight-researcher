// components/chat/report-progress.tsx
"use client";

import { memo } from "react";
import { Download, FileText, CheckCircle } from "lucide-react";
import { apiClient } from "@/lib/api-client";
import { useAuthStore } from "@/lib/auth-store";

/**
 * 报告生成完成后的下载面板
 * - 显示报告生成成功状态
 * - 提供多格式下载链接 (markdown/html/pdf/docx/json)
 *
 * P1-17: 用 React.memo 包裹, 默认浅比较 reportId/reportFormat/filePath
 * P1-20: FORMATS 数组提到模块级, 避免每次 render 重建
 */
const FORMATS = [
  { label: "Markdown", value: "markdown" },
  { label: "HTML", value: "html" },
  { label: "PDF", value: "pdf" },
  { label: "DOCX", value: "docx" },
  { label: "JSON", value: "json" },
] as const;

export const ReportProgress = memo(function ReportProgress({
  reportId,
  reportFormat,
  filePath,
}: {
  reportId: string;
  reportFormat?: string | null;
  filePath?: string | null;
}) {
  const { getToken } = useAuthStore();
  const token = getToken();

  // 任务4补充: 失败时解析 HTTP 错误详情 (含状态码和响应体), 便于诊断后端 500 根因
  const handleDownload = async (format: string) => {
    const url = apiClient.getReportDownloadUrl(reportId, format);
    // 使用 fetch 带 Authorization 头下载 (避免暴露 token 在 URL)
    try {
      const res = await fetch(url, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) {
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
      alert(err instanceof Error ? err.message : "下载失败");
    }
  };

  return (
    <div className="mt-3 p-3 rounded-md border" style={{ backgroundColor: "var(--bg-muted)", borderColor: "var(--border-color)" }}>
      <div className="flex items-center gap-2 mb-2 text-sm" style={{ color: "var(--brand-primary)" }}>
        <CheckCircle className="h-4 w-4" />
        <span className="font-medium">报告生成完成</span>
        {reportFormat && (
          <span className="text-xs text-tertiary">格式: {reportFormat}</span>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        {FORMATS.map((f) => (
          <button
            key={f.value}
            onClick={() => handleDownload(f.value)}
            className="flex items-center gap-1 px-2.5 py-1 text-xs rounded border hover:bg-hover transition-colors"
            style={{ borderColor: "var(--border-color)" }}
          >
            <Download className="h-3 w-3" />
            {f.label}
          </button>
        ))}
      </div>
    </div>
  );
});
