// app/error.tsx
"use client";

import { useEffect } from "react";

/**
 * App Router 错误边界 (page 级别)
 * - 捕获 page 渲染时的错误 (包括 #185 Maximum update depth exceeded)
 * - 显示 React 组件堆栈 (componentStack), 定位具体出错的组件
 * - 不替换 root layout (与 global-error.tsx 不同)
 */
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("[AppError]", error);
    console.error("[AppError] componentStack:", (error as any).componentStack);
  }, [error]);

  const componentStack = (error as any).componentStack as string | undefined;

  return (
    <div
      style={{
        padding: "24px",
        fontFamily: "monospace",
        backgroundColor: "#1a1a1a",
        color: "#ff6b6b",
        fontSize: "13px",
        lineHeight: "1.6",
        minHeight: "100vh",
      }}
    >
      <div style={{ maxWidth: "1100px", margin: "0 auto" }}>
        <h2 style={{ color: "#ff6b6b", marginBottom: "16px" }}>
          页面渲染错误 (app/error.tsx 捕获)
        </h2>

        <div style={{ marginBottom: "12px" }}>
          <strong>错误名称:</strong> {error.name}
        </div>
        <div style={{ marginBottom: "12px" }}>
          <strong>错误消息:</strong> {error.message}
        </div>
        {error.digest && (
          <div style={{ marginBottom: "12px" }}>
            <strong>Digest:</strong> {error.digest}
          </div>
        )}

        <div style={{ marginBottom: "16px" }}>
          <strong>堆栈:</strong>
          <pre
            style={{
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              backgroundColor: "#0d0d0d",
              padding: "12px",
              borderRadius: "6px",
              maxHeight: "300px",
              overflow: "auto",
            }}
          >
            {error.stack || "(无堆栈信息)"}
          </pre>
        </div>

        {componentStack && (
          <div style={{ marginBottom: "16px" }}>
            <strong style={{ color: "#ffcc66" }}>React 组件堆栈 (关键!):</strong>
            <pre
              style={{
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                backgroundColor: "#0d0d0d",
                padding: "12px",
                borderRadius: "6px",
                maxHeight: "400px",
                overflow: "auto",
                color: "#ffcc66",
              }}
            >
              {componentStack}
            </pre>
          </div>
        )}

        <button
          onClick={() => reset()}
          style={{
            padding: "8px 16px",
            backgroundColor: "#5E6AD2",
            color: "#fff",
            border: "none",
            borderRadius: "6px",
            cursor: "pointer",
            fontSize: "13px",
          }}
        >
          重试
        </button>
      </div>
    </div>
  );
}
