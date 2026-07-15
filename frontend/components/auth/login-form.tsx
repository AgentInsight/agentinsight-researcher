// components/auth/login-form.tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/lib/auth-store";
import { Captcha } from "./captcha";
import {
  Smartphone,
  Lock,
  Eye,
  EyeOff,
  ShieldCheck,
} from "lucide-react";

/**
 * 登录表单组件 (完整复制自 traceability-platform)
 * - Tab 切换: 密码登录 / 短信登录
 * - 密码登录: 手机号 + 密码 + 图片验证码 + 记住账号 + 忘记密码
 * - 短信登录: 手机号 + 短信验证码 + 发送按钮(60s倒计时)
 * - 忘记密码弹窗: 手机号 + 新密码 + 确认密码 + 短信验证码
 * - 注册成功后自动登录
 */
type LoginMode = "password" | "phone";

export function LoginForm() {
  const router = useRouter();
  const setUser = useAuthStore((s) => s.setUser);

  // 表单状态
  const [mode, setMode] = useState<LoginMode>("password");
  const [mobile, setMobile] = useState("");
  const [password, setPassword] = useState("");
  const [showPsw, setShowPsw] = useState(false);
  const [captchaId, setCaptchaId] = useState("");
  const [captchaCode, setCaptchaCode] = useState("");
  const [smsCode, setSmsCode] = useState("");
  const [smsCaptchaId, setSmsCaptchaId] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // 短信倒计时
  const [countDown, setCountDown] = useState(0);
  const [smsSending, setSmsSending] = useState(false);

  // 忘记密码弹窗
  const [showForgetDialog, setShowForgetDialog] = useState(false);
  const [resetLoading, setResetLoading] = useState(false);
  const [resetMobile, setResetMobile] = useState("");
  const [resetPassword, setResetPassword] = useState("");
  const [resetConfirmPassword, setResetConfirmPassword] = useState("");
  const [resetCaptchaCode, setResetCaptchaCode] = useState("");
  const [resetCaptchaId, setResetCaptchaId] = useState("");
  const [resetCountDown, setResetCountDown] = useState(0);

  // 倒计时定时器
  useEffect(() => {
    if (countDown <= 0) return;
    const timer = setInterval(() => {
      setCountDown((prev) => Math.max(0, prev - 1));
    }, 1000);
    return () => clearInterval(timer);
  }, [countDown]);

  useEffect(() => {
    if (resetCountDown <= 0) return;
    const timer = setInterval(() => {
      setResetCountDown((prev) => Math.max(0, prev - 1));
    }, 1000);
    return () => clearInterval(timer);
  }, [resetCountDown]);

  const phoneReg = /^1[3-9]\d{9}$/;

  const switchMode = (newMode: LoginMode) => {
    if (mode === newMode) return;
    setMode(newMode);
    setCaptchaId("");
    setCaptchaCode("");
    setSmsCaptchaId("");
    setSmsCode("");
    setError("");
  };

  // 发送短信验证码
  const sendSmsCode = useCallback(async () => {
    if (!mobile) {
      setError("请先输入手机号");
      return;
    }
    if (!phoneReg.test(mobile)) {
      setError("请输入正确的手机号");
      return;
    }
    setSmsSending(true);
    setError("");
    try {
      const res = await fetch(`/api/auth/sms?phone=${mobile}`);
      const data = await res.json();
      const item = Array.isArray(data.data) ? data.data[0] : data.data;
      if (item?.id) {
        setSmsCaptchaId(item.id);
        setCountDown(60);
      }
    } catch {
      setError("验证码发送失败");
    } finally {
      setSmsSending(false);
    }
  }, [mobile]);

  // 发送重置密码短信
  const sendResetSms = async () => {
    if (!resetMobile) {
      setError("请先输入手机号");
      return;
    }
    if (!phoneReg.test(resetMobile)) {
      setError("请输入正确的手机号");
      return;
    }
    try {
      const res = await fetch(`/api/auth/sms?phone=${resetMobile}`);
      const data = await res.json();
      const item = Array.isArray(data.data) ? data.data[0] : data.data;
      if (item?.id) {
        setResetCaptchaId(item.id);
        setResetCountDown(60);
      }
    } catch {
      setError("发送验证码失败");
    }
  };

  // 密码登录
  const handlePasswordLogin = async () => {
    if (!mobile) {
      setError("手机号必填");
      return;
    }
    if (!password) {
      setError("密码必填");
      return;
    }
    if (!captchaCode) {
      setError("图片验证码必填");
      return;
    }

    setLoading(true);
    setError("");
    try {
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
        setUser({ id: u.id, name: u.name, mobile: u.mobile, token: u.token });
        router.push("/agent/researcher/chat");
      } else {
        setError(data.message || "登录失败, 请检查账号密码");
      }
    } catch {
      setError("网络错误, 请稍后重试");
    } finally {
      setLoading(false);
    }
  };

  // 短信登录
  const handleSmsLogin = async () => {
    if (!mobile) {
      setError("手机号必填");
      return;
    }
    if (!smsCode) {
      setError("验证码必填");
      return;
    }
    if (!smsCaptchaId) {
      setError("请先发送短信验证码");
      return;
    }

    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mobile,
          captchaid: smsCaptchaId,
          captchacode: smsCode,
          logintype: 2,
        }),
      });
      const data = await res.json();

      if (data.errorcode === 0 && data.data?.[0]) {
        const u = data.data[0];
        setUser({ id: u.id, name: u.name, mobile: u.mobile, token: u.token });
        router.push("/agent/researcher/chat");
      } else {
        setError(data.message || "登录失败");
      }
    } catch {
      setError("网络错误, 请稍后重试");
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (mode === "password") {
      handlePasswordLogin();
    } else {
      handleSmsLogin();
    }
  };

  // 重置密码
  const handlePasswordReset = async () => {
    if (!resetMobile || !resetPassword || !resetConfirmPassword || !resetCaptchaCode) {
      setError("请填写完整信息");
      return;
    }
    if (resetPassword !== resetConfirmPassword) {
      setError("两次输入的密码不一致");
      return;
    }
    if (resetPassword.length < 6) {
      setError("密码长度至少6位");
      return;
    }

    setResetLoading(true);
    setError("");
    try {
      const res = await fetch("/api/auth/resetpassword", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mobile: resetMobile,
          captchaid: resetCaptchaId,
          captchacode: resetCaptchaCode,
          password: resetPassword,
        }),
      });
      const data = await res.json();
      if (data.errorcode === 0) {
        setShowForgetDialog(false);
        setResetMobile("");
        setResetPassword("");
        setResetConfirmPassword("");
        setResetCaptchaCode("");
        setError("");
      } else {
        setError(data.message || "密码重置失败");
      }
    } catch {
      setError("网络错误");
    } finally {
      setResetLoading(false);
    }
  };

  // 输入框样式 (半透明, 适配光晕背景)
  const inputStyle: React.CSSProperties = {
    backgroundColor: "rgba(255, 255, 255, 0.08)",
    borderColor: "rgba(255, 255, 255, 0.15)",
    color: "var(--text-primary)",
    borderRadius: "var(--radius-md)",
    borderWidth: "1px",
    borderStyle: "solid",
  };

  return (
    <div className="w-[400px] max-w-full">
      {/* 错误提示 */}
      {error && (
        <div
          className="mb-4 p-3 text-sm rounded-md"
          style={{
            color: "var(--color-danger)",
            backgroundColor: "var(--color-danger-bg)",
            border: "1px solid var(--color-danger-border)",
          }}
        >
          {error}
        </div>
      )}

      {/* Tab 切换 */}
      <div
        className="grid grid-cols-2 gap-1 p-1 mb-4 rounded-lg"
        style={{
          backgroundColor: "rgba(255, 255, 255, 0.08)",
          border: "1px solid rgba(255, 255, 255, 0.12)",
        }}
      >
        <button
          type="button"
          onClick={() => switchMode("password")}
          className="h-8 rounded-md text-sm transition-all"
          style={{
            color: mode === "password" ? "#fff" : "var(--text-secondary)",
            backgroundColor: mode === "password" ? "var(--brand-primary)" : "transparent",
          }}
        >
          密码登录
        </button>
        <button
          type="button"
          onClick={() => switchMode("phone")}
          className="h-8 rounded-md text-sm transition-all"
          style={{
            color: mode === "phone" ? "#fff" : "var(--text-secondary)",
            backgroundColor: mode === "phone" ? "var(--brand-primary)" : "transparent",
          }}
        >
          短信登录
        </button>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        {/* 手机号 */}
        <div className="relative">
          <Smartphone
            className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 z-10"
            style={{ color: "var(--text-tertiary)" }}
          />
          <input
            type="tel"
            value={mobile}
            onChange={(e) => setMobile(e.target.value)}
            maxLength={11}
            placeholder="请输入手机号"
            className="w-full pl-10 pr-3 py-2.5 text-sm outline-none transition-colors focus:border-[var(--brand-primary)]"
            style={inputStyle}
            required
          />
        </div>

        {mode === "password" ? (
          <>
            {/* 密码 */}
            <div className="relative">
              <Lock
                className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 z-10"
                style={{ color: "var(--text-tertiary)" }}
              />
              <input
                type={showPsw ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="请输入登录密码"
                className="w-full pl-10 pr-10 py-2.5 text-sm outline-none transition-colors focus:border-[var(--brand-primary)]"
                style={inputStyle}
                required
              />
              <button
                type="button"
                onClick={() => setShowPsw(!showPsw)}
                className="absolute right-3 top-1/2 -translate-y-1/2"
                style={{ color: "var(--text-tertiary)" }}
              >
                {showPsw ? <Eye className="w-4 h-4" /> : <EyeOff className="w-4 h-4" />}
              </button>
            </div>

            {/* 图片验证码 */}
            <div className="flex items-center gap-2">
              <div className="relative flex-1">
                <ShieldCheck
                  className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 z-10"
                  style={{ color: "var(--text-tertiary)" }}
                />
                <input
                  type="text"
                  value={captchaCode}
                  onChange={(e) => setCaptchaCode(e.target.value)}
                  placeholder="请输入图片验证码"
                  className="w-full pl-10 pr-3 py-2.5 text-sm outline-none transition-colors focus:border-[var(--brand-primary)]"
                  style={inputStyle}
                  required
                />
              </div>
              <Captcha
                onCaptchaId={setCaptchaId}
                mobile={mobile}
              />
            </div>

            {/* 记住账号 + 忘记密码 */}
            <div className="flex items-center justify-between text-sm">
              <label className="flex items-center gap-1.5 cursor-pointer" style={{ color: "var(--text-secondary)" }}>
                <input
                  type="checkbox"
                  checked={remember}
                  onChange={(e) => setRemember(e.target.checked)}
                  className="w-3.5 h-3.5"
                />
                记住账号
              </label>
              <span
                onClick={() => setShowForgetDialog(true)}
                className="cursor-pointer hover:underline"
                style={{ color: "var(--brand-primary)" }}
              >
                忘记密码
              </span>
            </div>
          </>
        ) : (
          <>
            {/* 短信验证码 */}
            <div className="flex items-center gap-2">
              <div className="relative flex-1">
                <ShieldCheck
                  className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 z-10"
                  style={{ color: "var(--text-tertiary)" }}
                />
                <input
                  type="text"
                  value={smsCode}
                  onChange={(e) => setSmsCode(e.target.value)}
                  placeholder="请输入验证码"
                  className="w-full pl-10 pr-3 py-2.5 text-sm outline-none transition-colors focus:border-[var(--brand-primary)]"
                  style={inputStyle}
                  required
                />
              </div>
              <button
                type="button"
                onClick={sendSmsCode}
                disabled={countDown > 0 || smsSending}
                className="px-4 py-2.5 text-sm whitespace-nowrap transition-opacity disabled:opacity-50"
                style={{
                  backgroundColor: "transparent",
                  color: "var(--brand-primary)",
                  border: "1px solid var(--border-color)",
                  borderRadius: "var(--radius-md)",
                }}
              >
                {countDown === 0 ? "发送验证码" : `${countDown}秒后可重发`}
              </button>
            </div>
          </>
        )}

        {/* 登录按钮 */}
        <button
          type="submit"
          disabled={loading}
          className="w-full py-2.5 text-sm font-medium transition-opacity hover:opacity-90 disabled:opacity-50"
          style={{
            backgroundColor: "var(--brand-primary)",
            color: "var(--text-on-brand)",
            borderRadius: "var(--radius-md)",
          }}
        >
          {loading ? "登录中..." : "登录"}
        </button>
      </form>

      {/* 忘记密码弹窗 */}
      {showForgetDialog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ backgroundColor: "var(--overlay-bg)" }}
          onClick={() => setShowForgetDialog(false)}
        >
          <div
            className="w-[500px] max-w-[90vw] p-6 rounded-lg"
            style={{
              backgroundColor: "var(--bg-card)",
              boxShadow: "var(--shadow-lg)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-semibold mb-4" style={{ color: "var(--text-primary)" }}>
              忘记密码
            </h3>
            <div className="space-y-3">
              <div>
                <label className="block text-sm mb-1" style={{ color: "var(--text-secondary)" }}>
                  手机号
                </label>
                <input
                  type="tel"
                  value={resetMobile}
                  onChange={(e) => setResetMobile(e.target.value)}
                  placeholder="请输入手机号"
                  className="w-full px-3 py-2 text-sm outline-none"
                  style={{
                    backgroundColor: "var(--bg-card)",
                    color: "var(--text-primary)",
                    border: "1px solid var(--border-color)",
                    borderRadius: "var(--radius-sm)",
                  }}
                />
              </div>
              <div>
                <label className="block text-sm mb-1" style={{ color: "var(--text-secondary)" }}>
                  新密码
                </label>
                <input
                  type="password"
                  value={resetPassword}
                  onChange={(e) => setResetPassword(e.target.value)}
                  placeholder="请输入新密码"
                  className="w-full px-3 py-2 text-sm outline-none"
                  style={{
                    backgroundColor: "var(--bg-card)",
                    color: "var(--text-primary)",
                    border: "1px solid var(--border-color)",
                    borderRadius: "var(--radius-sm)",
                  }}
                />
              </div>
              <div>
                <label className="block text-sm mb-1" style={{ color: "var(--text-secondary)" }}>
                  确认密码
                </label>
                <input
                  type="password"
                  value={resetConfirmPassword}
                  onChange={(e) => setResetConfirmPassword(e.target.value)}
                  placeholder="请再次输入新密码"
                  className="w-full px-3 py-2 text-sm outline-none"
                  style={{
                    backgroundColor: "var(--bg-card)",
                    color: "var(--text-primary)",
                    border: "1px solid var(--border-color)",
                    borderRadius: "var(--radius-sm)",
                  }}
                />
              </div>
              <div>
                <label className="block text-sm mb-1" style={{ color: "var(--text-secondary)" }}>
                  验证码
                </label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={resetCaptchaCode}
                    onChange={(e) => setResetCaptchaCode(e.target.value)}
                    placeholder="请输入验证码"
                    className="flex-1 px-3 py-2 text-sm outline-none"
                    style={{
                      backgroundColor: "var(--bg-card)",
                      color: "var(--text-primary)",
                      border: "1px solid var(--border-color)",
                      borderRadius: "var(--radius-sm)",
                    }}
                  />
                  <button
                    type="button"
                    onClick={sendResetSms}
                    disabled={resetCountDown > 0}
                    className="px-4 py-2 text-sm whitespace-nowrap transition-opacity disabled:opacity-50"
                    style={{
                      backgroundColor: "transparent",
                      color: "var(--brand-primary)",
                      border: "1px solid var(--border-color)",
                      borderRadius: "var(--radius-sm)",
                    }}
                  >
                    {resetCountDown === 0 ? "发送验证码" : `${resetCountDown}秒后重发`}
                  </button>
                </div>
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-5">
              <button
                type="button"
                onClick={() => setShowForgetDialog(false)}
                className="px-4 py-2 text-sm transition-colors hover:bg-hover"
                style={{
                  color: "var(--text-secondary)",
                  borderRadius: "var(--radius-sm)",
                }}
              >
                取消
              </button>
              <button
                type="button"
                onClick={handlePasswordReset}
                disabled={resetLoading}
                className="px-4 py-2 text-sm transition-opacity hover:opacity-90 disabled:opacity-50"
                style={{
                  backgroundColor: "var(--brand-primary)",
                  color: "var(--text-on-brand)",
                  borderRadius: "var(--radius-sm)",
                }}
              >
                {resetLoading ? "重置中..." : "重置密码"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
