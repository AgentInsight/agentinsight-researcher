// lib/auth-store.ts
import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * 认证状态管理
 * - SELF_HOST=true: user 为 null, isAuthed() 始终返回 true
 * - SELF_HOST=false: user 存储 token + 用户信息, 持久化到 localStorage
 *
 * 注意: token 同时存储在 httpOnly cookie (由 /api/auth/login 设置) 和 localStorage 中
 *       - httpOnly cookie: 供 middleware.ts 路由守卫读取 (服务端)
 *       - localStorage: 供客户端 API 调用读取 (Authorization Bearer 头)
 */
interface AuthUser {
  id: string;
  name: string;
  mobile: string;
  token: string;
}

interface AuthState {
  user: AuthUser | null;
  setUser: (user: AuthUser | null) => void;
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
      setUser: (user) => set({ user }),
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
        // SELF_HOST=true: 始终视为已认证
        if (process.env.NEXT_PUBLIC_SELF_HOST === "true") return true;
        // SELF_HOST=false: 需要 user.token
        return !!get().user?.token;
      },
    }),
    { name: "auth-storage" }
  )
);
