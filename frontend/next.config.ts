// next.config.ts
import type { NextConfig } from "next";

/**
 * 安全响应头 (生产强制 HTTPS, 与 AGENTS.md 第 11 章安全合规红线一致)
 * - X-Frame-Options: DENY 禁止点击劫持
 * - X-Content-Type-Options: nosniff 禁止 MIME 嗅探
 * - Referrer-Policy: strict-origin-when-cross-origin 限制 Referer 泄露
 * - Strict-Transport-Security: HSTS 强制 HTTPS
 * - Permissions-Policy: 禁用敏感设备能力
 */
async function headers() {
  return [
    {
      source: "/(.*)",
      headers: [
        { key: "X-Frame-Options", value: "DENY" },
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" },
        { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
      ],
    },
  ];
}

const nextConfig: NextConfig = {
  // standalone 输出 (独立容器, 仅复制最小化产物)
  output: "standalone",
  reactStrictMode: true,
  // 关闭 X-Powered-By 响应头, 避免暴露技术栈 (P2-11)
  poweredByHeader: false,
  // 无需 rewrites 配置:
  // - Agent API: server 模式通过 Nginx 按 agentName 路径分发 (/agent/{agentName}/*)
  //              local 模式浏览器直连 http://localhost:{port}/*
  // - Auth API: 浏览器跨域直连 https://agentinsight.goldebridge.com (需后端配置 CORS)
  headers,
  // 注意 (P1-21): 未启用 React Compiler (reactCompiler: true)
  //   React Compiler 仍是 RC 状态, 生产风险高, 待正式稳定后启用
  // 注意 (P1-22): 未启用 Turbopack (--turbo)
  //   Turbopack 在 Next.js 15.1 仍不稳定, 生产构建可能出错, 待社区验证后启用
  // 注意: experimental.optimizePackageImports 和 compiler.removeConsole 暂时禁用
  //   排查客户端 hydration 错误, 待确认根因后逐个启用
};

export default nextConfig;
