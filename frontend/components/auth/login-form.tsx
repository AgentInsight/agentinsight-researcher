// components/auth/login-form.tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/lib/auth-store";
import { Captcha } from "./captcha";

/**
 * 登录表单组件
 * - 调用 /api/auth/login (Next.js API Route 代理)
 * - 登录成功后存储 user 信息到 Zustand store
 * - API Route 同时设置 httpOnly cookie
 */
export function LoginForm() {
  const router = useRouter();
  const setUser = useAuthStore((s) => s.setUser);
  const [mobile, setMobile] = useState("");
  const [password, setPassword] = useState("");
  const [captchaId, setCaptchaId] = useState("");
  const [captchaCode, setCaptchaCode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      // 调用 Next.js API Route 代理 (服务端转发到 AgentInsightService)
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mobile,
          password,
          captchaid: captchaId,
          captchacode: captchaCode,
          logintype: 1,
        }),
      });
      const data = await res.json();

      if (data.errorcode === 0 && data.data?.[0]) {
        const u = data.data[0];
        // 存储 user 信息到 Zustand (localStorage 持久化)
        // API Route 已设置 httpOnly cookie, 客户端无需手动设置
        setUser({ id: u.id, name: u.name, mobile: u.mobile, token: u.token });
        router.push("/chat");
      } else {
        setError(data.message || "登录失败, 请检查账号密码");
      }
    } catch {
      setError("网络错误, 请稍后重试");
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="w-full max-w-md space-y-4 rounded-lg bg-white p-8 shadow-md">
      <h1 className="text-2xl font-bold text-center">登录</h1>

      {error && (
        <div className="rounded bg-red-50 p-3 text-sm text-red-600">{error}</div>
      )}

      <div>
        <label className="block text-sm font-medium text-gray-700">手机号</label>
        <input
          type="tel"
          value={mobile}
          onChange={(e) => setMobile(e.target.value)}
          className="mt-1 w-full rounded border p-2"
          placeholder="请输入手机号"
          required
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700">密码</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mt-1 w-full rounded border p-2"
          placeholder="请输入密码"
          required
        />
      </div>

      <Captcha onCaptchaId={setCaptchaId} captchaCode={captchaCode} setCaptchaCode={setCaptchaCode} />

      <button
        type="submit"
        disabled={loading}
        className="w-full rounded bg-blue-600 py-2 text-white hover:bg-blue-700 disabled:opacity-50"
      >
        {loading ? "登录中..." : "登录"}
      </button>
    </form>
  );
}
