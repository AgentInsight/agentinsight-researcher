// components/ui/toast.tsx
"use client";

import { useEffect, useState, useCallback } from "react";
import { CheckCircle, XCircle, AlertCircle, Info, X } from "lucide-react";

/**
 * Toast 提示组件 (轻量级, 参考业界实践)
 *
 * 使用方式:
 * 1. 在组件顶部调用 useToast() 获取 { toasts, addToast, removeToast }
 * 2. 调用 addToast({ type, message }) 显示提示
 * 3. 在 JSX 中渲染 <ToastContainer toasts={toasts} onClose={removeToast} />
 *
 * 4 种类型: success / error / warning / info
 * 自动消失 (默认 4 秒), 鼠标悬停暂停
 */

export type ToastType = "success" | "error" | "warning" | "info";

export interface Toast {
  id: number;
  type: ToastType;
  message: string;
  duration?: number;
}

export function useToast() {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const removeToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const addToast = useCallback(
    (params: { type: ToastType; message: string; duration?: number }) => {
      const id = Date.now() + Math.random();
      const toast: Toast = {
        id,
        type: params.type,
        message: params.message,
        duration: params.duration ?? 4000,
      };
      setToasts((prev) => [...prev, toast]);
    },
    []
  );

  return { toasts, addToast, removeToast };
}

/** Toast 图标 + 颜色配置 (使用 CSS 变量, 支持暗色模式自动切换) */
const TOAST_CONFIG: Record<
  ToastType,
  { icon: typeof CheckCircle; bg: string; color: string; border: string }
> = {
  success: {
    icon: CheckCircle,
    bg: "var(--color-success-bg)",
    color: "var(--color-success)",
    border: "var(--color-success-border)",
  },
  error: {
    icon: XCircle,
    bg: "var(--color-danger-bg)",
    color: "var(--color-danger)",
    border: "var(--color-danger-border)",
  },
  warning: {
    icon: AlertCircle,
    bg: "var(--color-warning-bg)",
    color: "var(--color-warning)",
    border: "var(--color-warning-border)",
  },
  info: {
    icon: Info,
    bg: "var(--color-info-bg)",
    color: "var(--color-info)",
    border: "var(--color-info-border)",
  },
};

/** 单个 Toast 项 */
function ToastItem({ toast, onClose }: { toast: Toast; onClose: (id: number) => void }) {
  const [visible, setVisible] = useState(false);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    // 入场动画
    const enterTimer = setTimeout(() => setVisible(true), 10);
    return () => clearTimeout(enterTimer);
  }, []);

  useEffect(() => {
    if (paused) return;
    const timer = setTimeout(() => onClose(toast.id), toast.duration ?? 4000);
    return () => clearTimeout(timer);
  }, [toast.id, toast.duration, paused, onClose]);

  const config = TOAST_CONFIG[toast.type];
  const Icon = config.icon;

  return (
    <div
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      className="flex items-start gap-2.5 px-4 py-3 transition-all duration-300 max-w-sm"
      style={{
        backgroundColor: config.bg,
        color: config.color,
        border: `1px solid ${config.border}`,
        borderRadius: "var(--radius-lg)",
        boxShadow: "var(--shadow-md)",
        transform: visible ? "translateX(0)" : "translateX(120%)",
        opacity: visible ? 1 : 0,
      }}
    >
      <Icon className="h-4 w-4 flex-shrink-0 mt-0.5" />
      <span className="text-sm flex-1 leading-relaxed">{toast.message}</span>
      <button
        onClick={() => onClose(toast.id)}
        className="flex-shrink-0 opacity-60 hover:opacity-100 transition-opacity"
        style={{ color: config.color }}
        aria-label="关闭提示"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

/** Toast 容器 (固定在右上角) */
export function ToastContainer({
  toasts,
  onClose,
}: {
  toasts: Toast[];
  onClose: (id: number) => void;
}) {
  return (
    <div className="fixed top-4 right-4 z-[9999] flex flex-col gap-2 pointer-events-none">
      {toasts.map((toast) => (
        <div key={toast.id} className="pointer-events-auto">
          <ToastItem toast={toast} onClose={onClose} />
        </div>
      ))}
    </div>
  );
}
