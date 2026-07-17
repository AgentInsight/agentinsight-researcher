// components/auth/auth-layout.tsx
"use client";

import { ReactNode } from "react";
import { useRouter } from "next/navigation";
import { GlowBackground } from "./glow-background";

/**
 * 认证页共用布局 (响应式, 适配手机/平板/桌面)
 *
 * 布局:
 * - 全屏居中, 光晕背景动画 (GlowBackground)
 * - 品牌标题区: 小标题 + 大标题 + 官网引导 + 切换提示
 * - 表单区: 最大 400px 宽, 小屏自适应
 * - 底部版权
 *
 * 响应式断点:
 * - 手机 (<sm, 640px): 标题缩小, padding 减小, 版权移至流式布局
 * - 平板 (sm-md, 640-768px): 适中
 * - 桌面 (md+, 768px+): 原始尺寸
 *
 * 使用 100dvh (动态视口高度) 替代 100vh, 解决移动端地址栏遮挡问题
 */
export function AuthLayout({
  children,
  mode,
  switchHref,
}: {
  children: ReactNode;
  mode: "login" | "register";
  switchHref: string;
}) {
  const router = useRouter();
  return (
    <div
      className="relative flex flex-col items-center justify-center min-h-[100dvh] overflow-hidden px-4 py-8"
      style={{ backgroundColor: "var(--bg-page)" }}
    >
      {/* 光晕背景 */}
      <GlowBackground />

      {/* 主内容容器 */}
      <div className="relative z-10 flex flex-col items-center w-full max-w-[440px]">
        {/* 品牌标题区 */}
        <div className="flex flex-col items-start mb-6 sm:mb-8 w-full">
          {/* 小标题 "欢迎使用" */}
          <h1
            className="mb-1 text-xl sm:text-2xl"
            style={{
              opacity: 0.5,
              color: "var(--text-secondary)",
              fontWeight: 400,
            }}
          >
            欢迎使用
          </h1>

          {/* 大标题 AgentInsight */}
          <h1
            className="flex items-center text-2xl sm:text-3xl font-bold flex-wrap"
            style={{ color: "var(--text-primary)" }}
          >
            <span style={{ color: "var(--brand-primary)" }}>A</span>
            gent
            <span style={{ color: "var(--brand-primary)" }}>I</span>
            nsight 智能体演示平台
          </h1>

          {/* 官网引导 */}
          <p
            className="mt-2 flex items-center gap-1 text-xs sm:text-sm flex-wrap"
            style={{ color: "var(--text-secondary)" }}
          >
            <span>新用户可先访问</span>
            <a
              href="https://agentinsight.goldebridge.com"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:underline"
              style={{ color: "var(--brand-primary)" }}
            >官网
            </a>
            <span>了解详情</span>
          </p>

          {/* 切换提示 */}
          <div className="mt-6 sm:mt-8 flex items-center gap-2 text-xs sm:text-sm">
            <span style={{ color: "var(--text-secondary)" }}>
              {mode === "register" ? "已有账号?" : "没有账号吗?"}
            </span>
            <span
              onClick={() => router.push(switchHref)}
              className="cursor-pointer hover:underline"
              style={{ color: "var(--text-primary)" }}
            >
              {mode === "register" ? "登录" : "注册新账号"}
            </span>
          </div>
        </div>

        {/* 表单区 */}
        {children}
      </div>

      {/* 底部版权 */}
      <footer
        className="relative mt-8 text-xs sm:text-sm text-center z-10"
        style={{ color: "var(--text-secondary)" }}
      >
        Copyright @ 2026 AgentInsight. All Rights Reserved
      </footer>
    </div>
  );
}
