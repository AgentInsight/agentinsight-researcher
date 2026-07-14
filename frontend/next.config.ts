// next.config.ts
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 方案 B: 独立容器, 使用 standalone 输出
  output: "standalone",
  reactStrictMode: true,
  // 注意: 不使用 rewrites 代理多 Agent API
  // 多 Agent 时客户端直接调用各 Agent 的 apiUrl (通过 CORS 配置允许)
  // 原因: rewrites 静态配置无法动态区分多 Agent, 客户端直接调用更灵活
};

export default nextConfig;
