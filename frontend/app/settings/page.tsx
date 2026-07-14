// app/settings/page.tsx
"use client";

import { useState } from "react";
import { useAuthStore } from "@/lib/auth-store";

/**
 * 设置页面精简
 * - 移除 Agent 名称设置 (只读, 从环境变量读取)
 * - 移除 Agent API 地址设置 (只读, 从环境变量读取)
 * - 仅保留: 报告格式/类型/语言/主题/JWT Token
 */
export default function SettingsPage() {
  const { user } = useAuthStore();
  const [reportFormat, setReportFormat] = useState("markdown");
  const [reportType, setReportType] = useState("basic_report");
  const [language, setLanguage] = useState("zh");
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [jwtToken, setJwtToken] = useState("");
  const [saved, setSaved] = useState(false);

  const handleSave = () => {
    // 保存到 localStorage
    localStorage.setItem("user-settings", JSON.stringify({
      reportFormat,
      reportType,
      language,
      theme,
      jwtToken,
    }));
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  return (
    <div className="max-w-2xl mx-auto p-6 space-y-8">
      <h1 className="text-2xl font-bold">设置</h1>

      {/* 报告配置 */}
      <section className="space-y-4">
        <h2 className="text-lg font-semibold">报告配置</h2>
        <div>
          <label className="block text-sm font-medium text-gray-700">报告格式</label>
          <select value={reportFormat} onChange={(e) => setReportFormat(e.target.value)} className="mt-1 w-full rounded border p-2">
            <option value="markdown">Markdown</option>
            <option value="html">HTML</option>
            <option value="pdf">PDF</option>
            <option value="docx">DOCX</option>
            <option value="json">JSON</option>
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700">报告类型</label>
          <select value={reportType} onChange={(e) => setReportType(e.target.value)} className="mt-1 w-full rounded border p-2">
            <option value="basic_report">基础报告</option>
            <option value="detailed_report">详细报告</option>
            <option value="deep_research">深度研究</option>
            <option value="summary">摘要</option>
            <option value="subtopics">子主题</option>
          </select>
        </div>
      </section>

      {/* 界面偏好 */}
      <section className="space-y-4">
        <h2 className="text-lg font-semibold">界面偏好</h2>
        <div>
          <label className="block text-sm font-medium text-gray-700">语言</label>
          <select value={language} onChange={(e) => setLanguage(e.target.value)} className="mt-1 w-full rounded border p-2">
            <option value="zh">中文</option>
            <option value="en">English</option>
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700">主题</label>
          <div className="mt-1 flex gap-2">
            <button onClick={() => setTheme("light")} className={`px-4 py-2 rounded ${theme === "light" ? "bg-blue-600 text-white" : "bg-gray-200"}`}>亮色</button>
            <button onClick={() => setTheme("dark")} className={`px-4 py-2 rounded ${theme === "dark" ? "bg-blue-600 text-white" : "bg-gray-200"}`}>暗色</button>
          </div>
        </div>
      </section>

      {/* 认证 (可选) */}
      <section className="space-y-4">
        <h2 className="text-lg font-semibold">认证 (可选)</h2>
        <div>
          <label className="block text-sm font-medium text-gray-700">JWT Token</label>
          <input
            type="password"
            value={jwtToken}
            onChange={(e) => setJwtToken(e.target.value)}
            placeholder="SELF_HOST=true 时可不填"
            className="mt-1 w-full rounded border p-2"
          />
          {user && (
            <p className="mt-1 text-xs text-gray-500">当前登录用户: {user.name} ({user.mobile})</p>
          )}
        </div>
      </section>

      <button onClick={handleSave} className="bg-blue-600 text-white px-4 py-2 rounded">
        {saved ? "已保存" : "保存"}
      </button>
    </div>
  );
}
