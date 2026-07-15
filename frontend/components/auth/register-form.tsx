// components/auth/register-form.tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/lib/auth-store";
import {
  User,
  Smartphone,
  Lock,
  Eye,
  EyeOff,
  ShieldCheck,
} from "lucide-react";

/**
 * 注册表单组件 (完整复制自 traceability-platform Register.vue)
 * - 用户名 + 手机号 + 密码 + 短信验证码 + 协议勾选
 * - 服务协议 / 隐私声明弹窗
 * - 注册成功后自动登录
 * - 半透明输入框样式适配光晕背景
 */

/** 协议内容（服务协议 & 隐私声明） */
const AGREEMENTS: Record<string, { title: string; content: string }> = {
  terms: {
    title: "服务协议",
    content: `
      <h3>一、总则</h3>
      <p>1.1 欢迎使用 AgentInsight 平台（以下简称"本平台"）。本协议是您与本平台之间关于使用本平台服务所订立的协议。</p>
      <p>1.2 请您在使用本平台服务前仔细阅读本协议的全部内容。您通过注册页面勾选"我已阅读并同意"并完成注册，即视为您已充分理解并接受本协议的全部条款。</p>
      <p>1.3 本平台有权根据需要不时修订本协议，修订后的协议将在本平台公布，一经公布即生效并取代原协议。</p>

      <h3>二、账号注册与管理</h3>
      <p>2.1 您在注册时应提供真实、准确、完整的个人信息，并在信息变更时及时更新。</p>
      <p>2.2 您应对您的账号安全负责，妥善保管账号和密码。因您自身原因导致账号被盗用或丢失的，由您自行承担责任。</p>
      <p>2.3 未经本平台书面同意，您不得以任何形式转让、出借或出售您的账号。</p>

      <h3>三、用户行为规范</h3>
      <p>3.1 您承诺在使用本平台服务过程中遵守所有适用的法律法规，不得利用本平台从事任何违法违规活动。</p>
      <p>3.2 您不得利用本平台传播任何违法、侵权、虚假、骚扰、诽谤、淫秽或其他不当内容。</p>
      <p>3.3 您不得干扰、破坏或试图干扰、破坏本平台的正常运行。</p>

      <h3>四、知识产权</h3>
      <p>4.1 本平台的所有内容，包括但不限于文字、图片、软件、界面设计等，均受知识产权相关法律保护。</p>
      <p>4.2 未经本平台或相关权利人书面许可，您不得以任何方式复制、修改、传播、使用上述内容。</p>

      <h3>五、免责声明</h3>
      <p>5.1 本平台按"现状"提供服务，不对服务的及时性、安全性、准确性做出任何保证。</p>
      <p>5.2 因不可抗力、系统维护、网络故障等原因导致的服务中断，本平台不承担责任。</p>

      <h3>六、其他</h3>
      <p>6.1 本协议的解释、效力及争议解决，均适用中华人民共和国法律。</p>
      <p>6.2 如本协议的任何条款被认定为无效或不可执行，其余条款仍然有效。</p>
    `,
  },
  privacy: {
    title: "隐私声明",
    content: `
      <h3>一、信息收集</h3>
      <p>1.1 当您注册本平台账号时，我们会收集您的用户名、手机号码等必要信息，用于创建和管理您的账号。</p>
      <p>1.2 当您使用本平台服务时，我们可能会自动收集设备信息、日志信息、操作记录等技术数据，以优化服务体验。</p>
      <p>1.3 我们仅在实现服务功能所必需的最小范围内收集您的个人信息。</p>

      <h3>二、信息使用</h3>
      <p>2.1 我们收集的信息将用于以下目的：</p>
      <ul>
        <li>提供、维护和改进本平台的服务；</li>
        <li>与您沟通，响应您的请求和反馈；</li>
        <li>保障平台安全，防范欺诈和滥用行为；</li>
        <li>履行法律法规规定的义务。</li>
      </ul>
      <p>2.2 我们不会将您的个人信息用于本声明未载明的其他用途，除非获得您的明确同意或法律要求。</p>

      <h3>三、信息存储与保护</h3>
      <p>3.1 我们采取业界通行的安全技术和组织措施保护您的个人信息，防止未经授权的访问、使用、修改或泄露。</p>
      <p>3.2 您的个人信息将存储在中国境内，我们不会将其传输至境外。</p>
      <p>3.3 我们仅在实现服务目的所必需的时间内保留您的个人信息，超出期限后将进行删除或匿名化处理。</p>

      <h3>四、信息共享</h3>
      <p>4.1 未经您的同意，我们不会向任何第三方共享您的个人信息，但以下情况除外：</p>
      <ul>
        <li>法律法规要求或政府部门依法要求；</li>
        <li>为保护本平台、用户或公众的合法权益所必需；</li>
        <li>在涉及合并、收购或资产出售等交易中，若涉及个人信息转让，我们将提前告知并要求受让方继续履行本声明。</li>
      </ul>

      <h3>五、您的权利</h3>
      <p>5.1 您有权查阅、更正、删除您的个人信息，也有权撤回已给予的同意。</p>
      <p>5.2 您可以通过本平台提供的功能自行管理您的个人信息，或联系客服行使您的权利。</p>

      <h3>六、未成年人保护</h3>
      <p>6.1 本平台主要面向成年人提供服务。若您未满18周岁，请在监护人指导下使用本平台。</p>

      <h3>七、更新与联系</h3>
      <p>7.1 我们可能会不时更新本隐私声明，更新后的版本将在本平台公布。</p>
      <p>7.2 如您对本隐私声明有任何疑问，请通过本平台公布的联系方式与我们取得联系。</p>
    `,
  },
};

export function RegisterForm() {
  const router = useRouter();
  const setUser = useAuthStore((s) => s.setUser);

  // 表单状态
  const [name, setName] = useState("");
  const [mobile, setMobile] = useState("");
  const [password, setPassword] = useState("");
  const [verifyCode, setVerifyCode] = useState("");
  const [showPsw, setShowPsw] = useState(false);
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // 短信验证码 ID（由 sendSmsCode 返回）
  const [smsCaptchaId, setSmsCaptchaId] = useState("");
  const [countDown, setCountDown] = useState(0);
  const [smsSending, setSmsSending] = useState(false);

  // 协议弹窗
  const [agreementVisible, setAgreementVisible] = useState(false);
  const [agreementTitle, setAgreementTitle] = useState("");
  const [agreementContent, setAgreementContent] = useState("");

  // 倒计时定时器
  useEffect(() => {
    if (countDown <= 0) return;
    const timer = setInterval(() => {
      setCountDown((prev) => Math.max(0, prev - 1));
    }, 1000);
    return () => clearInterval(timer);
  }, [countDown]);

  const phoneReg = /^1[3-9]\d{9}$/;

  /** 打开协议/隐私声明弹窗 */
  const openAgreement = (type: string) => {
    const agreement = AGREEMENTS[type];
    if (agreement) {
      setAgreementTitle(agreement.title);
      setAgreementContent(agreement.content);
      setAgreementVisible(true);
    }
  };

  /** 发送短信验证码 */
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

  /** 注册并自动登录 */
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!name) {
      setError("用户名必填");
      return;
    }
    if (!mobile) {
      setError("手机号必填");
      return;
    }
    if (!phoneReg.test(mobile)) {
      setError("请输入正确的手机号");
      return;
    }
    if (!password) {
      setError("密码必填");
      return;
    }
    if (!verifyCode) {
      setError("短信验证码必填");
      return;
    }
    if (!smsCaptchaId) {
      setError("请先发送短信验证码");
      return;
    }
    if (!agreed) {
      setError("请同意服务协议和隐私声明");
      return;
    }

    setLoading(true);
    setError("");
    try {
      // 1. 注册
      const regRes = await fetch("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          mobile,
          password,
          captchaid: smsCaptchaId,
          captchacode: verifyCode,
        }),
      });
      const regData = await regRes.json();

      if (regData.errorcode !== 0) {
        setError(regData.message || "注册失败");
        return;
      }

      // 2. 自动登录
      const loginRes = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mobile,
          password,
          captchaid: smsCaptchaId,
          captchacode: verifyCode,
          logintype: 1,
        }),
      });
      const loginData = await loginRes.json();

      if (loginData.errorcode === 0 && loginData.data?.[0]) {
        const u = loginData.data[0];
        setUser({ id: u.id, name: u.name, mobile: u.mobile, token: u.token });
        router.push("/agent/researcher/chat");
      } else {
        // 注册成功但自动登录失败，跳转到登录页
        setError(loginData.message || "注册成功，但自动登录失败，请手动登录");
        setTimeout(() => router.push("/login"), 1500);
      }
    } catch {
      setError("网络错误，请稍后重试");
    } finally {
      setLoading(false);
    }
  };

  // 输入框样式 (半透明, 适配光晕背景, 与 login-form 一致)
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

      <form onSubmit={handleSubmit} className="space-y-4">
        {/* 用户名 */}
        <div className="relative">
          <User
            className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 z-10"
            style={{ color: "var(--text-tertiary)" }}
          />
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="请输入用户名"
            className="w-full pl-10 pr-3 py-2.5 text-sm outline-none transition-colors focus:border-[var(--brand-primary)]"
            style={inputStyle}
            required
          />
        </div>

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
            placeholder="请输入您的手机号"
            className="w-full pl-10 pr-3 py-2.5 text-sm outline-none transition-colors focus:border-[var(--brand-primary)]"
            style={inputStyle}
            required
          />
        </div>

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

        {/* 短信验证码 */}
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <ShieldCheck
              className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 z-10"
              style={{ color: "var(--text-tertiary)" }}
            />
            <input
              type="text"
              value={verifyCode}
              onChange={(e) => setVerifyCode(e.target.value)}
              placeholder="请输入短信验证码"
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

        {/* 协议勾选 */}
        <label
          className="flex items-center gap-1.5 text-sm cursor-pointer"
          style={{ color: "var(--text-secondary)" }}
        >
          <input
            type="checkbox"
            checked={agreed}
            onChange={(e) => setAgreed(e.target.checked)}
            className="w-3.5 h-3.5"
          />
          <span>我已阅读并同意</span>
          <span
            onClick={(e) => {
              e.preventDefault();
              openAgreement("terms");
            }}
            className="cursor-pointer hover:underline"
            style={{ color: "var(--brand-primary)" }}
          >
            服务协议
          </span>
          <span>和</span>
          <span
            onClick={(e) => {
              e.preventDefault();
              openAgreement("privacy");
            }}
            className="cursor-pointer hover:underline"
            style={{ color: "var(--brand-primary)" }}
          >
            隐私声明
          </span>
        </label>

        {/* 注册按钮 */}
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
          {loading ? "注册中..." : "注册"}
        </button>
      </form>

      {/* 协议/隐私声明弹窗 */}
      {agreementVisible && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ backgroundColor: "var(--overlay-bg)" }}
          onClick={() => setAgreementVisible(false)}
        >
          <div
            className="w-[640px] max-w-[90vw] p-6 rounded-lg"
            style={{
              backgroundColor: "var(--bg-card)",
              boxShadow: "var(--shadow-lg)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-semibold mb-4" style={{ color: "var(--text-primary)" }}>
              {agreementTitle}
            </h3>
            <div
              className="agreement-content"
              style={{
                maxHeight: "60vh",
                overflowY: "auto",
                padding: "0 8px",
                lineHeight: 1.8,
                color: "var(--text-primary)",
              }}
              dangerouslySetInnerHTML={{ __html: agreementContent }}
            />
            <div className="flex justify-end mt-5">
              <button
                type="button"
                onClick={() => setAgreementVisible(false)}
                className="px-4 py-2 text-sm transition-opacity hover:opacity-90"
                style={{
                  backgroundColor: "var(--brand-primary)",
                  color: "var(--text-on-brand)",
                  borderRadius: "var(--radius-sm)",
                }}
              >
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
