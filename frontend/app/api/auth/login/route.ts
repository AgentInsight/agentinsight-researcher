// app/api/auth/login/route.ts
import { NextRequest, NextResponse } from "next/server";

/**
 * 登录代理 API Route (SELF_HOST=false 时使用)
 * - 代理到 AgentInsightService /api/user/login
 * - 登录成功后设置 httpOnly cookie (auth-token)
 * - 返回 token 给客户端 (客户端存储到 localStorage 供 API 调用)
 *
 * 注意: AUTH_API_BASE 为服务端-only 环境变量 (无 NEXT_PUBLIC_ 前缀)
 */
const API_BASE = process.env.AUTH_API_BASE || "https://agentinsight.goldebridge.com";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const apiResponse = await fetch(`${API_BASE}/api/user/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await apiResponse.json();

    // 登录成功: 设置 httpOnly cookie
    if (data.errorcode === 0 && data.data?.[0]?.token) {
      const token = data.data[0].token;
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

    return NextResponse.json(data, { status: apiResponse.status });
  } catch {
    return NextResponse.json(
      { errorcode: -1, message: "登录代理请求失败" },
      { status: 500 }
    );
  }
}
