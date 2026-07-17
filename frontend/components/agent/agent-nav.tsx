// components/agent/agent-nav.tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useNavStore } from "@/lib/nav-store";
import { useAuthStore } from "@/lib/auth-store";
import { useStreamStore } from "@/lib/stream-store";
import { Bot, Blocks, ChevronLeft, ChevronRight, User, LogOut, ChevronUp } from "lucide-react";
import { Tooltip } from "@/components/ui/tooltip";

// 模块级图标常量 (避免 ModeButton 每次渲染创建新的 JSX 元素, 引用稳定) (P1-19)
const BOT_ICON = <Bot className="h-4 w-4" />;
const BLOCKS_ICON = <Blocks className="h-4 w-4" />;

/**
 * 左侧导航栏 (3 区域 Flexbox 布局, 无硬边框, 用背景色差分隔)
 *
 * 区域:
 * - 顶部 (flex-none): 平台名 + 展开图标
 * - 中间 (flex-1): 智能体 / MCP 服务 模式切换
 * - 底部 (flex-none): 用户名 (登录→手机/邮箱, 未登录→真实 IP) + 退出登录菜单
 *
 * 折叠状态: 仅显示图标列
 */
export function AgentNav() {
  const router = useRouter();
  const {
    mode,
    setMode,
    agentNavCollapsed,
    toggleAgentNav,
  } = useNavStore();
  const { user, userIp, selfHost, fetchUserIp, fetchConfig, logout } = useAuthStore();

  const collapsed = agentNavCollapsed;
  const [menuOpen, setMenuOpen] = useState(false);
  const userAreaRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // 运行时获取配置 (selfHost 等, 非构建时内联)
    if (selfHost === null) {
      fetchConfig();
    }
    // SELF_HOST=true 模式下获取客户端 IP
    if (selfHost === true && !user && !userIp) {
      fetchUserIp();
    }
  }, [user, userIp, selfHost, fetchUserIp, fetchConfig]);

  // 点击外部关闭下拉菜单
  useEffect(() => {
    if (!menuOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (userAreaRef.current && !userAreaRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [menuOpen]);

  const handleSetMode = (newMode: "agent" | "mcp") => {
    setMode(newMode);
    if (newMode === "mcp") {
      router.push("/mcp/researcher/setting");
    } else {
      router.push("/agent/researcher/chat");
    }
  };

  const handleLogout = () => {
    setMenuOpen(false);
    // 中止所有流式请求 (清理后台流)
    useStreamStore.getState().abortAllStreams();
    // 清除认证状态 + httpOnly cookie
    logout();
    // 跳转到登录页 (SELF_HOST=true 时由 middleware 重定向回首页)
    router.push("/login");
  };

  const userDisplayName = user
    ? user.mobile || user.name || "已登录用户"
    : (userIp || "获取中...");

  return (
    <div
      className="flex flex-col h-full"
      style={{
        backgroundColor: "var(--bg-sidebar)",
        width: collapsed ? 56 : 220,
        transition: "width 0.2s ease",
      }}
    >
      {/* ===== 顶部: 平台 Logo + 标题 + 开源地址 + 收缩/展开按钮 ===== */}
      {/* 任务1+2: grid 三栏布局 [1fr_auto_1fr], 标题真正居中, 按钮推到最右 */}
      {/* 任务1: items-center 让按钮垂直居中与 AgentListNav 对齐; Tooltip wrapper 需外套 div 才能让 justify-self 生效 */}
      {/* 任务2: 开源地址行 mt 与下方功能列表 pt 间距一致 (mt-3 = pt-3 = 12px) */}
      <div className="flex-none grid grid-cols-[1fr_auto_1fr] items-center gap-2 px-3 py-2.5">
        {collapsed ? (
          <>
            <div></div>
            {/* 任务1: Tooltip 返回 inline-flex div 作为 grid item, justify-self 必须写在外层 wrapper div 上 */}
            <div className="justify-self-center">
              <Tooltip content="展开导航栏">
                <button
                  onClick={toggleAgentNav}
                  className="p-1.5 rounded-md hover:bg-hover transition-colors"
                  style={{ color: "var(--text-secondary)" }}
                  aria-label="展开导航栏"
                >
                  <ChevronRight className="h-3.5 w-3.5" />
                </button>
              </Tooltip>
            </div>
            <div></div>
          </>
        ) : (
          <>
            {/* 左侧占位 (1fr, 让标题居中) */}
            <div></div>
            {/* 中间: Logo + 标题 + 开源地址 (auto, 居中对齐) */}
            <div className="min-w-0 flex flex-col items-center">
              <div className="flex items-center gap-2">
                {/* 科技感 Logo: 六边形 + 中心节点 + 环绕节点 + 连线, 表达"智能体网络"语义 */}
                <div
                  className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                  style={{
                    backgroundColor: "var(--brand-primary)",
                    color: "var(--text-on-brand)",
                  }}
                >
                  <svg
                    width="20"
                    height="20"
                    viewBox="0 0 24 24"
                    fill="none"
                    xmlns="http://www.w3.org/2000/svg"
                    aria-hidden="true"
                  >
                    {/* 外六边形 (智能体网络边界) */}
                    <path
                      d="M12 2L20 7V17L12 22L4 17V7L12 2Z"
                      stroke="currentColor"
                      strokeWidth="1.5"
                      strokeLinejoin="round"
                      opacity="0.4"
                    />
                    {/* 中心节点到 6 个顶点的连线 (信息流动) */}
                    <path
                      d="M12 12L12 2M12 12L20 7M12 12L20 17M12 12L12 22M12 12L4 17M12 12L4 7"
                      stroke="currentColor"
                      strokeWidth="0.8"
                      opacity="0.5"
                    />
                    {/* 中心核心节点 (智能体核心) */}
                    <circle cx="12" cy="12" r="2.5" fill="currentColor" />
                    {/* 6 个外围节点 (多智能体协作) */}
                    <circle cx="12" cy="2" r="1.2" fill="currentColor" />
                    <circle cx="20" cy="7" r="1.2" fill="currentColor" />
                    <circle cx="20" cy="17" r="1.2" fill="currentColor" />
                    <circle cx="12" cy="22" r="1.2" fill="currentColor" />
                    <circle cx="4" cy="17" r="1.2" fill="currentColor" />
                    <circle cx="4" cy="7" r="1.2" fill="currentColor" />
                  </svg>
                </div>
                {/* 标题区: AgentInsight (与图标同高一行) + 智能体演示平台 (下方居中) */}
                <div className="min-w-0 flex flex-col">
                  <div
                    className="text-lg font-bold leading-8 truncate"
                    style={{ color: "var(--text-primary)", height: 32 }}
                  >
                    <span style={{ color: "var(--brand-primary)" }}>A</span>
                    gent
                    <span style={{ color: "var(--brand-primary)" }}>I</span>
                    nsight
                  </div>
                  <div
                    className="text-xs truncate text-center"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    智能体演示平台
                  </div>
                </div>
              </div>
              {/* 开源地址行 (标题下方, 新窗口打开; mt-3 与下方功能列表 pt-3 间距一致) */}
              <div
                className="flex items-center gap-1.5 text-xs mt-3"
                style={{ color: "var(--text-tertiary)" }}
              >
                <span>开源地址:</span>
                <a
                  href="https://gitcode.com/agentinsight-researcher"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:underline transition-colors"
                  style={{ color: "var(--brand-primary)" }}
                >
                  GitCode
                </a>
                <a
                  href="https://github.com/AgentInsight"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:underline transition-colors ml-0.5"
                  style={{ color: "var(--brand-primary)" }}
                >
                  GitHub
                </a>
              </div>
            </div>
            {/* 右侧: 折叠按钮 (1fr, justify-end 推到最右) */}
            {/* 任务1: self-start 让按钮贴 grid 顶部, 避免被中间列(Logo+标题+开源地址)的 76px 高度撑低 */}
            {/* 按钮中心 = py-2.5(10px) + 按钮半高(13px) = 23px, 与收缩态(23px)/AgentListNav(23px)/ChatPage(23px) 一致 */}
            <div className="flex justify-end self-start">
              <Tooltip content="折叠所有导航栏">
                <button
                  onClick={toggleAgentNav}
                  className="p-1.5 rounded-md hover:bg-hover transition-colors"
                  style={{ color: "var(--text-tertiary)" }}
                  aria-label="折叠所有导航栏"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                </button>
              </Tooltip>
            </div>
          </>
        )}
      </div>

      {/* ===== 中间: 模式切换 (flex-1 自适应, pt-3 与上方开源地址 mt-3 间距一致) ===== */}
      <div className="flex-1 flex flex-col justify-start pt-3 pb-3 px-3 gap-2 overflow-y-auto min-h-0">
        <ModeButton
          icon={BOT_ICON}
          label="智能体"
          isActive={mode === "agent"}
          collapsed={collapsed}
          onClick={() => handleSetMode("agent")}
        />
        <ModeButton
          icon={BLOCKS_ICON}
          label="配置"
          isActive={mode === "mcp"}
          collapsed={collapsed}
          onClick={() => handleSetMode("mcp")}
        />
      </div>

      {/* ===== 底部: 用户信息 (可点击展开退出登录菜单) ===== */}
      <div ref={userAreaRef} className="flex-none relative">
        {/* 下拉菜单 (向上展开) */}
        {menuOpen && (
          <div
            className="absolute bottom-full mb-1 rounded-md overflow-hidden"
            style={{
              backgroundColor: "var(--bg-card)",
              border: "1px solid var(--border-color-light)",
              boxShadow: "var(--shadow-md)",
              minWidth: 120,
              left: collapsed ? "50%" : 12,
              transform: collapsed ? "translateX(-50%)" : "none",
              right: collapsed ? "auto" : 12,
            }}
          >
            <button
              onClick={handleLogout}
              className="w-full flex items-center gap-2 px-3 py-2 text-sm transition-colors hover:bg-hover"
              style={{ color: "var(--text-secondary)" }}
            >
              <LogOut className="h-3.5 w-3.5 flex-shrink-0" />
              <span>退出登录</span>
            </button>
          </div>
        )}

        <button
          onClick={() => setMenuOpen((v) => !v)}
          className="w-full flex items-center gap-2 px-3 py-3 transition-colors hover:bg-hover"
          style={{ backgroundColor: "var(--bg-sidebar)" }}
          aria-label={menuOpen ? "关闭用户菜单" : "打开用户菜单"}
        >
          <div
            className="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0"
            style={{
              backgroundColor: "var(--brand-primary)",
              color: "var(--text-on-brand)",
            }}
          >
            <User className="h-3.5 w-3.5" />
          </div>
          {!collapsed && (
            <div className="flex-1 min-w-0 flex items-center justify-between gap-1">
              <Tooltip content={userDisplayName}>
                <div
                  className="text-xs font-medium truncate"
                  style={{ color: "var(--text-primary)" }}
                >
                  {userDisplayName}
                </div>
              </Tooltip>
              <ChevronUp
                className="h-3 w-3 flex-shrink-0 transition-transform"
                style={{
                  color: "var(--text-tertiary)",
                  transform: menuOpen ? "none" : "rotate(180deg)",
                }}
              />
            </div>
          )}
        </button>
      </div>
    </div>
  );
}

/** 模式切换按钮 */
function ModeButton({
  icon,
  label,
  isActive,
  collapsed,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  isActive: boolean;
  collapsed: boolean;
  onClick: () => void;
}) {
  return (
    <Tooltip content={collapsed ? label : ""}>
      <button
        onClick={onClick}
        className={`w-full flex items-center gap-2.5 px-3 py-2.5 rounded-md text-sm transition-colors ${
          collapsed ? "justify-center" : ""
        }`}
        style={{
          backgroundColor: isActive ? "var(--bg-active)" : "transparent",
          color: isActive ? "var(--brand-primary)" : "var(--text-secondary)",
          fontWeight: isActive ? 500 : 400,
        }}
      >
        <span className="flex-shrink-0">{icon}</span>
        {!collapsed && <span className="truncate">{label}</span>}
      </button>
    </Tooltip>
  );
}
