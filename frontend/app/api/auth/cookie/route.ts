// app/api/auth/cookie/route.ts
import { NextRequest, NextResponse } from "next/server";

/**
 * 极简 Cookie 管理 API Route (不转发任何网络请求)
 *
 * 用途:
 *   前端直连 AgentInsightService 登录后, 从响应体提取 JWT Token,
 *   调用本 route 设置 httpOnly cookie, 供 middleware.ts 服务端路由守卫读取。
 *
 * 设计理由:
 *   - 浏览器原生 fetch 无法设置 httpOnly cookie (仅服务端可设)
 *   - middleware.ts (Edge Runtime) 只能读 cookie, 无法读 localStorage
 *   - 因此需要一个极简的服务端 route 仅用于设置/清除 cookie, 不做网络转发
 *
 * 与原 /api/auth/login 的区别:
 *   - 原 login route: 转发请求到后端 + 提取 token + 设置 cookie (3 步)
 *   - 本 cookie route: 仅设置/清除 cookie (1 步, 无网络请求)
 *   - 前端组件直接 fetch 后端, 从响应提取 token, 再调本 route 设置 cookie
 *
 * 支持两种部署环境:
 *   - 本地 Docker (无 Nginx): 前端直连 https://agentinsight.goldebridge.com (跨域)
 *   - 服务器 (有 Nginx): 前端通过 Nginx 同源代理 /auth-api/* 或直连
 *   两种环境 cookie 设置逻辑相同 (本 route 不涉及网络)
 */

/**
 * POST /api/auth/cookie
 * 设置 httpOnly cookie (auth-token)
 *
 * 请求体: { token: string }
 * 响应: { success: true }
 */
export async function POST(request: NextRequest) {
  try {
    const { token } = await request.json();

    if (!token || typeof token !== "string") {
      return NextResponse.json(
        { error: "缺少 token 参数" },
        { status: 400 }
      );
    }

    const response = NextResponse.json({ success: true });

    // 设置 httpOnly cookie, 供 middleware.ts 路由守卫读取
    // maxAge: 30 天 (与 JWT 有效期一致)
    //
    // secure 判断: 不能仅依赖 NODE_ENV=production
    // 原因: 用户可能通过 Nginx 反代以 HTTP 访问 (如 http://43.139.209.145/),
    //       此时 NODE_ENV=production 但实际协议是 HTTP,
    //       secure cookie 不会被浏览器存储 → middleware 检测不到 token → 重定向回 /login
    // 修复: 检查 X-Forwarded-Proto 头判断实际协议
    const forwardedProto = request.headers.get("x-forwarded-proto");
    const isHttps =
      request.nextUrl.protocol === "https:" || forwardedProto === "https";

    response.cookies.set("auth-token", token, {
      httpOnly: true,
      secure: isHttps,
      sameSite: "lax",
      maxAge: 30 * 24 * 60 * 60, // 30 天
      path: "/",
    });

    return response;
  } catch {
    return NextResponse.json(
      { error: "设置 cookie 失败" },
      { status: 500 }
    );
  }
}

/**
 * DELETE /api/auth/cookie
 * 清除 httpOnly cookie (auth-token)
 *
 * 响应: { success: true }
 */
export async function DELETE() {
  const response = NextResponse.json({ success: true });
  response.cookies.delete("auth-token");
  return response;
}
