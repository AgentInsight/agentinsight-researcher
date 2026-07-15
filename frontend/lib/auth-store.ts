// lib/auth-store.ts
import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * 认证状态管理
 * - SELF_HOST=true: user 为 null, isAuthed() 始终返回 true; 显示客户端 IP
 * - SELF_HOST=false: user 存储 token + 用户信息, 持久化到 localStorage; 显示手机/邮箱
 *
 * 注意: token 同时存储在 httpOnly cookie (由 /api/auth/login 设置) 和 localStorage 中
 *       - httpOnly cookie: 供 middleware.ts 路由守卫读取 (服务端)
 *       - localStorage: 供客户端 API 调用读取 (Authorization Bearer 头)
 *
 * selfHost 通过 /api/config 运行时获取 (非 NEXT_PUBLIC_ 前缀, 不被构建时内联):
 * - middleware.ts 直接读取 process.env.SELF_HOST (Edge Runtime 运行时变量)
 * - 客户端组件通过 /api/config API 获取 (客户端无法直接读取非 NEXT_PUBLIC_ 变量)
 */
interface AuthUser {
  id: string;
  name: string;
  mobile: string;
  token: string;
}

interface AuthState {
  user: AuthUser | null;
  /** 客户端 IP (SELF_HOST 模式下显示, 通过 /api/user-info 获取) */
  userIp: string;
  /** 运行时获取的 SELF_HOST 配置 (通过 /api/config 获取, 初始 undefined 表示未加载) */
  selfHost: boolean | null;
  setUser: (user: AuthUser | null) => void;
  /** 获取客户端 IP (调用 /api/user-info) */
  fetchUserIp: () => Promise<void>;
  /** 获取运行时配置 (调用 /api/config, 获取 selfHost 等) */
  fetchConfig: () => Promise<void>;
  logout: () => void;
  /** 获取当前 token (供 API 调用使用) */
  getToken: () => string | undefined;
  /** SELF_HOST=true 时无需 user, 返回 true */
  isAuthed: () => boolean;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      userIp: "",
      selfHost: null,
      setUser: (user) => set({ user }),
      fetchUserIp: async () => {
        try {
          const res = await fetch("/api/user-info");
          if (res.ok) {
            const data = await res.json();
            if (data.ip && data.ip !== "unknown") {
              set({ userIp: data.ip });
            }
          }
        } catch {
          // 忽略错误, 保持空字符串
        }
      },
      fetchConfig: async () => {
        try {
          const res = await fetch("/api/config");
          if (res.ok) {
            const data = await res.json();
            set({ selfHost: data.selfHost === true });
          }
        } catch {
          // 获取失败, 默认按非 SELF_HOST 模式处理 (安全降级)
          set({ selfHost: false });
        }
      },
      logout: () => {
        // 清除 localStorage 状态
        set({ user: null });
        // 清除 httpOnly cookie (通过 API Route)
        fetch("/api/auth/logout", { method: "POST" }).catch(() => {
          // 忽略错误, 客户端状态已清除
        });
      },
      getToken: () => get().user?.token,
      isAuthed: () => {
        const { selfHost, user } = get();
        // SELF_HOST=true: 始终视为已认证
        if (selfHost === true) return true;
        // SELF_HOST=false 或未加载: 需要 user.token
        return !!user?.token;
      },
    }),
    {
      name: "auth-storage",
      // selfHost 不持久化到 localStorage (每次启动重新从 API 获取, 避免配置变更后残留旧值)
      // userIp 不持久化到 localStorage (避免 IP 变更后残留陈旧值, 每次启动重新获取) (P2-2)
      partialize: (state) => ({ user: state.user }),
    }
  )
);
