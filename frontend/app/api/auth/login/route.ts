// app/api/auth/login/route.ts
import { NextRequest, NextResponse } from "next/server";

/**
 * 登录代理 API Route (SELF_HOST=false 时使用)
 * - 代理到 AgentInsightService /api/user/login
 * - 登录成功后设置 httpOnly cookie (auth-token)
 * - 返回 token 给客户端 (客户端存储到 localStorage 供 API 调用)
 *
 * Token 提取策略 (多源容错, 兼容后端不同返回格式):
 * 1. 响应体 data[0].token (原始格式)
 * 2. 响应体 data.token / data.access_token (扁平格式)
 * 3. 响应头 Authorization: Bearer <token> (Header 格式)
 * 4. 响应头 x-auth-token (自定义 Header)
 *
 * 注意: AUTH_API_BASE 为服务端-only 环境变量 (无 NEXT_PUBLIC_ 前缀)
 * 不使用 _utils.ts 的 proxyJson, 需要访问完整 Response 对象 (含 headers)
 */

/** AgentInsightService 后端地址 (服务端-only, 无 NEXT_PUBLIC_ 前缀) */
const API_BASE =
  process.env.AUTH_API_BASE || "https://agentinsight.goldebridge.com";

/** 请求超时 (10s) */
const TIMEOUT_MS = 10_000;

/**
 * 从多个来源提取 JWT Token, 并去除 "Bearer " 前缀
 * @returns 清理后的 token (无 "Bearer " 前缀), 未找到返回 undefined
 */
function extractToken(
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

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const url = `${API_BASE}/api/user/login`;

    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);

    let res: Response;
    try {
      res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: ctrl.signal,
      });
    } finally {
      clearTimeout(timer);
    }

    // 解析响应体 (即使 HTTP 错误也尝试读取 JSON, 后端错误信息通常在 body 中)
    let data: unknown = null;
    try {
      data = await res.json();
    } catch {
      // 非 JSON 响应, 保持 null
    }

    // 网络错误/超时: data 为 null
    if (data === null && !res.ok) {
      return NextResponse.json(
        { errorcode: -1, message: "登录代理请求失败" },
        { status: res.status }
      );
    }

    // 多源提取 Token
    const token = extractToken(data, res.headers);

    if (token) {
      // 登录成功: 设置 httpOnly cookie
      const response = NextResponse.json(data, { status: 200 });
      // 设置 httpOnly cookie, 供 middleware.ts 路由守卫读取
      // maxAge: 30 天 (与 JWT 有效期一致)
      response.cookies.set("auth-token", token, {
        httpOnly: true,
        secure: process.env.NODE_ENV === "production",
        sameSite: "lax",
        maxAge: 30 * 24 * 60 * 60, // 30 天
        path: "/",
      });
      return response;
    }

    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json(
      { errorcode: -1, message: "登录代理请求失败" },
      { status: 500 }
    );
  }
}
