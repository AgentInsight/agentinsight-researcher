// components/agent/agent-nav.tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useNavStore } from "@/lib/nav-store";
import { useAuthStore } from "@/lib/auth-store";
import { useStreamStore } from "@/lib/stream-store";
import { Bot, Blocks, PanelLeftClose, PanelLeftOpen, User, Sparkles, LogOut, ChevronUp } from "lucide-react";
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
    leftSidebarCollapsed,
    toggleLeftSidebar,
  } = useNavStore();
  const { user, userIp, selfHost, fetchUserIp, fetchConfig, logout } = useAuthStore();

  const collapsed = leftSidebarCollapsed;
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
      {/* ===== 顶部: 平台名 + 展开图标 ===== */}
      <div className="flex-none flex items-center justify-between px-3 py-3.5">
        {collapsed ? (
          <Tooltip content="展开导航栏">
            <button
              onClick={toggleLeftSidebar}
              className="mx-auto p-1.5 rounded-md hover:bg-hover transition-colors"
              style={{ color: "var(--text-secondary)" }}
              aria-label="展开导航栏"
            >
              <PanelLeftOpen className="h-4 w-4" />
            </button>
          </Tooltip>
        ) : (
          <>
            <div className="flex-1 min-w-0 flex items-center gap-2">
              <div
                className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                style={{
                  backgroundColor: "var(--brand-primary)",
                  color: "var(--text-on-brand)",
                }}
              >
                <Sparkles className="h-4 w-4" />
              </div>
              <div className="min-w-0">
                <div
                  className="text-sm font-semibold truncate"
                  style={{ color: "var(--text-primary)" }}
                >
                  AgentInsight
                </div>
                <div
                  className="text-xs truncate"
                  style={{ color: "var(--text-tertiary)" }}
                >
                  智能体演示平台
                </div>
              </div>
            </div>
            <Tooltip content="折叠导航栏">
              <button
                onClick={toggleLeftSidebar}
                className="p-1.5 rounded-md hover:bg-hover transition-colors"
                style={{ color: "var(--text-tertiary)" }}
                aria-label="折叠导航栏"
              >
                <PanelLeftClose className="h-4 w-4" />
              </button>
            </Tooltip>
          </>
        )}
      </div>

      {/* ===== 中间: 模式切换 (flex-1 自适应) ===== */}
      <div className="flex-1 flex flex-col justify-start p-2 gap-0.5 overflow-y-auto min-h-0">
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
        className={`w-full flex items-center gap-2 px-2.5 py-2 rounded-md text-sm transition-colors ${
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
