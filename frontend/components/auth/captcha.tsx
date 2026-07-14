// components/auth/captcha.tsx
"use client";

import { useState, useEffect } from "react";

/**
 * 图片验证码组件
 * - 从 /api/auth/captcha 获取验证码图片 (base64)
 * - 验证码 ID 传递给父组件
 */
export function Captcha({
  onCaptchaId,
  captchaCode,
  setCaptchaCode,
}: {
  onCaptchaId: (id: string) => void;
  captchaCode: string;
  setCaptchaCode: (code: string) => void;
}) {
  const [captchaImg, setCaptchaImg] = useState("");
  const [captchaId, setCaptchaId] = useState("");

  const refreshCaptcha = async () => {
    try {
      const res = await fetch("/api/auth/captcha");
      const data = await res.json();
      if (data.id && data.data) {
        setCaptchaId(data.id);
        setCaptchaImg(data.data);
        onCaptchaId(data.id);
      }
    } catch {
      // 验证码获取失败, 忽略
    }
  };

  useEffect(() => {
    refreshCaptcha();
  }, []);

  return (
    <div>
      <label className="block text-sm font-medium text-gray-700">验证码</label>
      <div className="mt-1 flex gap-2">
        <input
          type="text"
          value={captchaCode}
          onChange={(e) => setCaptchaCode(e.target.value)}
          className="flex-1 rounded border p-2"
          placeholder="请输入验证码"
          required
        />
        {captchaImg && (
          <img
            src={captchaImg}
            alt="验证码"
            onClick={refreshCaptcha}
            className="cursor-pointer rounded border"
            title="点击刷新验证码"
          />
        )}
      </div>
    </div>
  );
}
