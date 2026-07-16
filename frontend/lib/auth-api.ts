// lib/auth-api.ts
/**
 * 认证 API 客户端工具 (前端直连 AgentInsightService, 绕过 Next.js 转发)
 *
 * 架构 (支持两种部署环境):
 *   - 本地 Docker (无 Nginx): 浏览器直连 https://agentinsight.goldebridge.com (跨域, 需 CORS)
 *   - 服务器 (有 Nginx): 浏览器访问 /auth-api/* (Nginx 同源代理, 避免 CORS) 或直连
 *   两种环境通过 NEXT_PUBLIC_AUTH_API_BASE 环境变量切换, 代码无需改动
 *
 * Token 流转:
 *   1. 前端直接 fetch AgentInsightService 登录接口
 *   2. 从响应体/头提取 JWT Token (多源容错)
 *   3. Token 存 localStorage (zustand persist, 供客户端 API 调用 Authorization 头)
 *   4. 调用 /api/auth/cookie route 设置 httpOnly cookie (供 middleware.ts 服务端路由守卫)
 */

/**
 * 获取认证 API 基地址
 * - 默认直连: https://agentinsight.goldebridge.com (跨域, 需后端 CORS)
 * - Nginx 同源代理: /auth-api (服务器环境, 避免 CORS)
 * - 由 NEXT_PUBLIC_AUTH_API_BASE 环境变量控制 (构建时内联到客户端 bundle)
 */
export const AUTH_API_BASE =
  process.env.NEXT_PUBLIC_AUTH_API_BASE || "https://agentinsight.goldebridge.com";

/**
 * 从多个来源提取 JWT Token, 并去除 "Bearer " 前缀
 *
 * 兼容后端不同返回格式:
 * 1. 响应体 data[0].token (原始数组格式)
 * 2. 响应体 data.token / data.access_token (对象格式)
 * 3. 扁平 body.token / body.access_token
 * 4. 响应头 Authorization: Bearer <token>
 * 5. 响应头 x-auth-token
 *
 * @returns 清理后的 token (无 "Bearer " 前缀), 未找到返回 undefined
 */
export function extractToken(
  body: unknown,
  headers: Headers
): string | undefined {
  let token: string | undefined;

  // 1. 响应体: data[0].token (原始数组格式)
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    const dataField = b.data;
    // data 为数组: [{ token }]
    if (Array.isArray(dataField) && dataField[0]) {
      const item = dataField[0] as Record<string, unknown>;
      token = item.token as string | undefined;
      if (!token) token = item.access_token as string | undefined;
    }
    // data 为对象: { token }
    if (!token && dataField && typeof dataField === "object" && !Array.isArray(dataField)) {
      const d = dataField as Record<string, unknown>;
      token = d.token as string | undefined;
      if (!token) token = d.access_token as string | undefined;
    }
    // 扁平格式: { token } / { access_token }
    if (!token) {
      token = b.token as string | undefined;
      if (!token) token = b.access_token as string | undefined;
    }
  }

  // 2. 响应头: Authorization: Bearer <token>
  if (!token) {
    const authHeader =
      headers.get("Authorization") || headers.get("authorization");
    if (authHeader) {
      token = authHeader.startsWith("Bearer ")
        ? authHeader.substring(7)
        : authHeader;
    }
  }

  // 3. 响应头: x-auth-token
  if (!token) {
    token = headers.get("x-auth-token") || undefined;
  }

  // 4. 去除 "Bearer " 前缀 (如果 token 本身带了前缀)
  if (token && token.startsWith("Bearer ")) {
    token = token.substring(7);
  }

  return token;
}

/**
 * 设置 httpOnly cookie (调用极简 /api/auth/cookie route)
 *
 * 前端从登录响应提取 token 后, 调用此函数设置 httpOnly cookie,
 * 供 middleware.ts 服务端路由守卫读取。
 *
 * 注意: 此函数不阻塞用户跳转, cookie 设置失败不影响登录流程
 *       (最坏情况: middleware 检测不到 cookie, 但 localStorage 有 token, 客户端仍可用)
 *
 * @param token JWT Token (无 "Bearer " 前缀)
 */
export async function setAuthTokenCookie(token: string): Promise<void> {
  try {
    await fetch("/api/auth/cookie", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
  } catch {
    // 忽略错误: cookie 设置失败不阻塞登录
    // (localStorage 已存 token, 客户端 API 调用仍可用)
  }
}

/**
 * 带超时的 fetch 封装 (用于认证 API 请求)
 *
 * @param url 完整 URL
 * @param init RequestInit
 * @param timeoutMs 超时毫秒, 默认 10s
 */
export async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs: number = 10_000
): Promise<Response> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: ctrl.signal });
  } finally {
    clearTimeout(timer);
  }
}
