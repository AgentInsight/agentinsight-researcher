// components/ui/tooltip.tsx
"use client";

import { useState, useRef, useCallback, ReactNode } from "react";

/**
 * 自定义 Tooltip 组件 (Linear Indigo 设计系统)
 *
 * 替代浏览器原生 title 属性, 提供统一的视觉样式:
 * - 深色背景 + 白字 (Linear/Notion 风格)
 * - 6px 圆角 + 中等阴影
 * - 淡入动画 (0.2s, 仅 opacity, 不动画 transform 避免定位冲突)
 * - 延迟显示 (500ms, 与原生 title 一致)
 *
 * 定位策略 (用户需求):
 * - 提示框出现在鼠标下方
 * - 鼠标所在位置 = 提示框的右上角
 * - 鼠标移动时实时跟踪, 保证提示始终紧贴鼠标
 * - 视口边界检测: 下方空间不足时翻转到上方; 左侧溢出时贴近视口左侧
 *
 * 用法:
 * <Tooltip content="查看历史报告">
 *   <button>...</button>
 * </Tooltip>
 *
 * 无障碍: 保留 aria-label, 不依赖 tooltip 传递关键信息
 * 零依赖: 纯 React + CSS 变量, 无需 @radix-ui 等第三方库
 */
export function Tooltip({
  content,
  children,
  delay = 500,
}: {
  content: string;
  children: ReactNode;
  delay?: number;
}) {
  const [visible, setVisible] = useState(false);
  const [position, setPosition] = useState({ x: 0, y: 0, side: "bottom" as "top" | "bottom" });
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 跟踪鼠标最新位置 (onMouseMove 实时更新, show() 读取最新值)
  const mousePosRef = useRef({ x: 0, y: 0 });

  const show = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      const tooltipWidth = 200; // 预估宽度
      const tooltipHeight = 32; // 预估高度
      const gap = 8;
      const mouse = mousePosRef.current;

      // 定位: 鼠标位置 = 提示框右上角
      // x: 提示框右边缘对齐鼠标 x 坐标
      // y: 提示框在鼠标下方 (top = mouse.y + gap)
      let x = mouse.x - tooltipWidth;
      // 左侧溢出检测: 若右对齐后溢出左侧, 则贴近视口左侧
      if (x < 8) x = 8;

      // 下方空间不足时翻转到上方
      const actualSide: "top" | "bottom" =
        mouse.y + gap + tooltipHeight > window.innerHeight - 8 ? "top" : "bottom";

      let y: number;
      if (actualSide === "top") {
        // 上方: 提示框底部在鼠标上方
        y = mouse.y - gap;
      } else {
        // 下方: 提示框顶部在鼠标下方
        y = mouse.y + gap;
      }

      setPosition({ x, y, side: actualSide });
      setVisible(true);
    }, delay);
  }, [delay]);

  const hide = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setVisible(false);
  }, []);

  // 实时跟踪鼠标位置 (即使 tooltip 尚未显示, 也持续更新位置)
  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    mousePosRef.current = { x: e.clientX, y: e.clientY };
    // 如果 tooltip 已可见, 实时更新位置 (紧贴鼠标)
    if (visible) {
      const tooltipWidth = 200;
      const tooltipHeight = 32;
      const gap = 8;
      const mouse = mousePosRef.current;
      let x = mouse.x - tooltipWidth;
      if (x < 8) x = 8;
      const actualSide: "top" | "bottom" =
        mouse.y + gap + tooltipHeight > window.innerHeight - 8 ? "top" : "bottom";
      let y: number;
      if (actualSide === "top") {
        y = mouse.y - gap;
      } else {
        y = mouse.y + gap;
      }
      setPosition({ x, y, side: actualSide });
    }
  }, [visible]);

  return (
    <>
      <div
        onMouseEnter={show}
        onMouseLeave={hide}
        onMouseMove={handleMouseMove}
        onFocus={show}
        onBlur={hide}
        className="inline-flex"
        style={{ display: "inline-flex" }}
      >
        {children}
      </div>
      {visible && content && (
        <div
          className="fixed z-[9999] pointer-events-none fade-in"
          style={{
            position: "fixed",
            left: position.x,
            top: position.y,
            // side="top" 时用 translate(0, -100%) 将提示框移到 y 上方
            transform:
              position.side === "top"
                ? "translate(0, -100%)"
                : "translate(0, 0)",
            backgroundColor: "var(--text-primary)",
            color: "var(--bg-card)",
            padding: "5px 10px",
            borderRadius: "var(--radius-sm)",
            boxShadow: "var(--shadow-md)",
            fontSize: "12px",
            lineHeight: "1.4",
            maxWidth: "260px",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
          role="tooltip"
        >
          {content}
        </div>
      )}
    </>
  );
}
