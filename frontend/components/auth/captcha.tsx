// components/auth/captcha.tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { Tooltip } from "@/components/ui/tooltip";

/**
 * 图片验证码组件
 * - 仅在页面加载时请求一次验证码
 * - 用户点击验证码图片时刷新
 * - 其他时候不请求 (手机号变化不触发刷新)
 * - 从 /api/auth/captcha?mobile={mobile} 获取验证码图片 (base64)
 * - 验证码 ID 通过 onCaptchaId 回调传递给父组件
 * - 半透明样式适配光晕背景
 */
export function Captcha({
  onCaptchaId,
  mobile,
}: {
  onCaptchaId: (id: string) => void;
  mobile?: string;
}) {
  const [captchaImg, setCaptchaImg] = useState("");

  const refreshCaptcha = useCallback(async (currentMobile: string) => {
    try {
      const url = currentMobile
        ? `/api/auth/captcha?mobile=${encodeURIComponent(currentMobile)}`
        : `/api/auth/captcha`;
      const res = await fetch(url);
      const data = await res.json();
      // API 返回 data 为数组格式: [{ id, image }]
      const item = Array.isArray(data.data) ? data.data[0] : data.data;
      if (item?.id) {
        onCaptchaId(item.id);
        // 兼容后端返回的 base64 图片（可能带或不带 data:image 前缀）
        if (item.image && !item.image.startsWith("data:")) {
          setCaptchaImg(`data:image/png;base64,${item.image}`);
        } else {
          setCaptchaImg(item.image || "");
        }
      } else {
        onCaptchaId("");
        setCaptchaImg("");
      }
    } catch {
      onCaptchaId("");
      setCaptchaImg("");
    }
  }, [onCaptchaId]);

  // 仅在页面加载时请求一次 (不传 mobile, 避免依赖 mobile 变化)
  useEffect(() => {
    refreshCaptcha("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Tooltip content="点击刷新验证码">
      <div
        onClick={() => refreshCaptcha(mobile || "")}
        className="cursor-pointer flex items-center justify-center flex-shrink-0"
        style={{
          width: "110px",
          height: "42px",
          backgroundColor: "rgba(255, 255, 255, 0.08)",
          border: "1px solid rgba(255, 255, 255, 0.15)",
          borderRadius: "var(--radius-md)",
          overflow: "hidden",
        }}
      >
        {captchaImg ? (
          <img
            src={captchaImg}
            alt="验证码"
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
          />
        ) : (
          <span style={{ color: "var(--text-tertiary)", fontSize: "12px" }}>
            {mobile ? "点击刷新" : "验证码"}
          </span>
        )}
      </div>
    </Tooltip>
  );
}
