// components/review/review-panel.tsx
"use client";

import { useState } from "react";
import { Check, PencilLine } from "lucide-react";

/**
 * 人在回路审核面板 (Linear Indigo 设计风格)
 * - 当收到 human_feedback_request 时显示
 * - 用户可接受或提供修订意见
 * - 通过 onSubmit 回调提交反馈 (由父组件调用 apiClient.submitFeedback)
 */
export function ReviewPanel({
  request,
  onSubmit,
}: {
  request: { node: string; content: string };
  onSubmit: (feedback: string) => void;
}) {
  const [feedback, setFeedback] = useState("");

  const handleAccept = () => {
    onSubmit("approve");
  };

  const handleReject = () => {
    onSubmit(feedback || "请修改");
  };

  return (
    <div
      className="p-5"
      style={{
        backgroundColor: "var(--color-warning-bg)",
        borderTop: "1px solid var(--border-color)",
      }}
    >
      <div className="max-w-3xl mx-auto space-y-3">
        <h3
          className="font-semibold flex items-center gap-1.5"
          style={{ color: "var(--color-warning)" }}
        >
          审核请求 ({request.node})
        </h3>
        <div
          className="p-3 max-h-60 overflow-y-auto"
          style={{
            backgroundColor: "var(--bg-card)",
            borderRadius: "var(--radius-sm)",
            border: "1px solid var(--border-color)",
          }}
        >
          <pre
            className="text-sm whitespace-pre-wrap"
            style={{ color: "var(--text-primary)" }}
          >
            {request.content}
          </pre>
        </div>
        <div>
          <textarea
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="修订意见 (留空表示接受)"
            className="w-full p-2 text-sm outline-none transition-colors focus:border-[var(--brand-primary)]"
            style={{
              backgroundColor: "var(--bg-card)",
              color: "var(--text-primary)",
              border: "1px solid var(--border-color)",
              borderRadius: "var(--radius-sm)",
            }}
            rows={3}
          />
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleAccept}
            className="inline-flex items-center gap-1.5 px-4 py-2 text-sm transition-opacity hover:opacity-90"
            style={{
              backgroundColor: "var(--color-success)",
              color: "var(--text-on-brand)",
              borderRadius: "var(--radius-md)",
            }}
          >
            <Check className="h-4 w-4" />
            接受
          </button>
          <button
            onClick={handleReject}
            className="inline-flex items-center gap-1.5 px-4 py-2 text-sm transition-opacity hover:opacity-90"
            style={{
              backgroundColor: "var(--brand-primary)",
              color: "var(--text-on-brand)",
              borderRadius: "var(--radius-md)",
            }}
          >
            <PencilLine className="h-4 w-4" />
            提交修订
          </button>
        </div>
      </div>
    </div>
  );
}
