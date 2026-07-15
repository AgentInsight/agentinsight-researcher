// app/global-error.tsx
"use client";

import { useEffect } from "react";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // 打印完整错误信息到控制台 (包括不可枚举属性)
    console.error("[GlobalError] 完整错误对象:", error);
    console.error("[GlobalError] error.componentStack:", (error as any).componentStack);
    console.error("[GlobalError] error.cause:", (error as any).cause);
    console.error(
      "[GlobalError] error 自有属性名:",
      Object.getOwnPropertyNames(error)
    );
  }, [error]);

  // 尝试获取 React 组件堆栈 (不可枚举属性)
  const componentStack = (error as any).componentStack as string | undefined;
  const cause = (error as any).cause as unknown;

  return (
    <html lang="zh">
      <body
        style={{
          margin: 0,
          padding: "24px",
          fontFamily: "monospace",
          backgroundColor: "#1a1a1a",
          color: "#ff6b6b",
          fontSize: "13px",
          lineHeight: "1.6",
        }}
      >
        <div style={{ maxWidth: "1100px", margin: "0 auto" }}>
          <h2 style={{ color: "#ff6b6b", marginBottom: "16px" }}>
            客户端渲染错误 (global-error.tsx 捕获)
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

          {cause != null && (
            <div style={{ marginBottom: "12px" }}>
              <strong>Cause:</strong>{" "}
              <pre
                style={{
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  backgroundColor: "#0d0d0d",
                  padding: "12px",
                  borderRadius: "6px",
                  maxHeight: "200px",
                  overflow: "auto",
                }}
              >
                {typeof cause === "object" ? JSON.stringify(cause, null, 2) : String(cause)}
              </pre>
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

          <div style={{ marginBottom: "16px" }}>
            <strong>错误对象自有属性:</strong>
            <pre
              style={{
                whiteSpace: "pre-wrap",
                backgroundColor: "#0d0d0d",
                padding: "12px",
                borderRadius: "6px",
                color: "#88ccff",
              }}
            >
              {Object.getOwnPropertyNames(error).join(", ")}
            </pre>
          </div>

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
      </body>
    </html>
  );
}
