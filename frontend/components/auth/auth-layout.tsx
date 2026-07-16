// components/auth/auth-layout.tsx
"use client";

import { ReactNode } from "react";
import { useRouter } from "next/navigation";
import { GlowBackground } from "./glow-background";

/**
 * 认证页共用布局 (复制自 traceability-platform)
 * - 全屏居中式布局 (非卡片式)
 * - 光晕背景动画 (GlowBackground)
 * - 品牌元素: 小标题"欢迎使用" + 大标题 AgentInsight + 副标题 + 官网链接
 * - 切换提示: 登录↔注册
 * - 底部版权
 *
 * 背景色保持 frontend 现有 (--bg-page)
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
      className="relative flex flex-col items-center justify-center min-h-screen overflow-hidden"
      style={{ backgroundColor: "var(--bg-page)" }}
    >
      {/* 光晕背景 */}
      <GlowBackground />

      {/* 主内容容器 */}
      <div className="relative z-10 flex flex-col items-center min-h-[500px] px-4">
        {/* 品牌标题区 */}
        <div className="flex flex-col items-start mb-8">
          {/* 小标题 "欢迎使用" */}
          <h1
            className="mb-1"
            style={{
              fontSize: "160%",
              opacity: 0.5,
              color: "var(--text-secondary)",
              fontWeight: 400,
            }}
          >
            欢迎使用
          </h1>

          {/* 大标题 AgentInsight */}
          <h1
            className="flex items-center text-3xl font-bold"
            style={{ color: "var(--text-primary)" }}
          >
            <span style={{ color: "var(--brand-primary)" }}>A</span>
            gent
            <span style={{ color: "var(--brand-primary)" }}>I</span>
            nsight 智能体演示平台
          </h1>

          {/* 官网引导 */}
          <p
            className="mt-2 flex items-center gap-1 text-sm"
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
          <div className="mt-8 flex items-center gap-2 text-sm">
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
        className="absolute bottom-16 left-1/2 -translate-x-1/2 text-sm z-10"
        style={{ color: "var(--text-secondary)" }}
      >
        Copyright @ 2026 AgentInsight. All Rights Reserved
      </footer>
    </div>
  );
}
