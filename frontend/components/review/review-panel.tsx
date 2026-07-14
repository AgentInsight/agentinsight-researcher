// components/review/review-panel.tsx
"use client";

import { useState } from "react";

/**
 * 人在回路审核面板
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
    <div className="border-t p-4 bg-yellow-50">
      <div className="max-w-3xl mx-auto space-y-3">
        <h3 className="font-semibold text-yellow-800">
          审核请求 ({request.node})
        </h3>
        <div className="bg-white rounded p-3 max-h-60 overflow-y-auto">
          <pre className="text-sm whitespace-pre-wrap">{request.content}</pre>
        </div>
        <div>
          <textarea
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="修订意见 (留空表示接受)"
            className="w-full border rounded p-2 text-sm"
            rows={3}
          />
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleAccept}
            className="bg-green-600 text-white px-4 py-2 rounded text-sm"
          >
            接受
          </button>
          <button
            onClick={handleReject}
            className="bg-orange-500 text-white px-4 py-2 rounded text-sm"
          >
            提交修订
          </button>
        </div>
      </div>
    </div>
  );
}
