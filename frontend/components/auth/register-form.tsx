// components/auth/register-form.tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Captcha } from "./captcha";

/**
 * 注册表单组件
 * - 用户名 + 手机号 + 密码 + 短信验证码 + 协议勾选
 */
export function RegisterForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [mobile, setMobile] = useState("");
  const [password, setPassword] = useState("");
  const [captchaId, setCaptchaId] = useState("");
  const [captchaCode, setCaptchaCode] = useState("");
  const [smsCode, setSmsCode] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const sendSms = async () => {
    if (!mobile) {
      setError("请先输入手机号");
      return;
    }
    try {
      await fetch(`/api/auth/sms?phone=${mobile}`);
    } catch {
      setError("短信发送失败");
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!agreed) {
      setError("请同意用户协议");
      return;
    }
    setError("");
    setLoading(true);

    try {
      const res = await fetch("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          mobile,
          password,
          captchaid: captchaId,
          captchacode: captchaCode,
          logintype: 1,
        }),
      });
      const data = await res.json();
      if (data.errorcode === 0) {
        router.push("/login");
      } else {
        setError(data.message || "注册失败");
      }
    } catch {
      setError("网络错误");
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="w-full max-w-md space-y-4 rounded-lg bg-white p-8 shadow-md">
      <h1 className="text-2xl font-bold text-center">注册</h1>
      {error && <div className="rounded bg-red-50 p-3 text-sm text-red-600">{error}</div>}
      <div>
        <label className="block text-sm font-medium text-gray-700">用户名</label>
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} className="mt-1 w-full rounded border p-2" required />
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700">手机号</label>
        <input type="tel" value={mobile} onChange={(e) => setMobile(e.target.value)} className="mt-1 w-full rounded border p-2" required />
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700">密码</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} className="mt-1 w-full rounded border p-2" required />
      </div>
      <Captcha onCaptchaId={setCaptchaId} captchaCode={captchaCode} setCaptchaCode={setCaptchaCode} />
      <div>
        <label className="block text-sm font-medium text-gray-700">短信验证码</label>
        <div className="mt-1 flex gap-2">
          <input type="text" value={smsCode} onChange={(e) => setSmsCode(e.target.value)} className="flex-1 rounded border p-2" required />
          <button type="button" onClick={sendSms} className="rounded bg-gray-200 px-4 py-2 text-sm">发送</button>
        </div>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
        我已阅读并同意用户协议
      </label>
      <button type="submit" disabled={loading} className="w-full rounded bg-blue-600 py-2 text-white hover:bg-blue-700 disabled:opacity-50">
        {loading ? "注册中..." : "注册"}
      </button>
    </form>
  );
}
