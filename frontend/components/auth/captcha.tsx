// components/auth/captcha.tsx
"use client";

import { useState, useEffect, useRef } from "react";
import { Tooltip } from "@/components/ui/tooltip";

/**
 * 图片验证码组件 (复制自 traceability-platform)
 * - 接受 mobile prop, 当手机号变化时自动刷新验证码
 * - 从 /api/auth/captcha?mobile={mobile} 获取验证码图片 (base64)
 * - 验证码 ID 通过 onCaptchaId 回调传递给父组件
 * - 点击图片可手动刷新
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
  const lastMobileRef = useRef<string | null>(null);

  const refreshCaptcha = async (currentMobile: string) => {
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
  };

  // 首次加载时刷新
  useEffect(() => {
    refreshCaptcha(mobile || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // mobile 变化时自动刷新
  useEffect(() => {
    if (mobile === lastMobileRef.current) return;
    lastMobileRef.current = mobile || "";
    refreshCaptcha(mobile || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mobile]);

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
