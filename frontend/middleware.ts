// middleware.ts
import { NextRequest, NextResponse } from "next/server";

/**
 * SELF_HOST 路由守卫
 * - NEXT_PUBLIC_SELF_HOST=true: 跳过登录守卫, 所有路由直接放行
 * - NEXT_PUBLIC_SELF_HOST=false: 未登录(无 auth-token cookie)重定向到 /login
 *
 * 注意: NEXT_PUBLIC_ 前缀变量在客户端和服务端(含 Edge Runtime)都可见,
 *       middleware.ts 运行在 Edge Runtime, 可正确读取此变量。
 */
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  // middleware 运行在 Edge Runtime, 可读取 NEXT_PUBLIC_ 前缀的环境变量
  const selfHost = process.env.NEXT_PUBLIC_SELF_HOST === "true";

  // SELF_HOST=true: 跳过所有登录守卫
  if (selfHost) {
    // 根路径重定向到 /chat
    if (pathname === "/") {
      return NextResponse.redirect(new URL("/chat", request.url));
    }
    return NextResponse.next();
  }

  // SELF_HOST=false: 走登录守卫
  // 从 httpOnly cookie 读取 auth-token (由 /api/auth/login 设置)
  const token = request.cookies.get("auth-token")?.value;
  const isAuthRoute = pathname.startsWith("/login") || pathname.startsWith("/register");

  // 已登录访问登录页 → 重定向 /chat
  if (isAuthRoute && token) {
    return NextResponse.redirect(new URL("/chat", request.url));
  }

  // 未登录访问受保护路由 → 重定向 /login
  // /api/health 已在 matcher 中排除, 无需在此检查
  if (!isAuthRoute && !token) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return NextResponse.next();
}

export const config = {
  // matcher: 排除 API Routes (auth/health)、静态资源、图片
  // /api/auth/* 和 /api/health 不走 middleware, 直接放行
  // 注意: /api/agent/* 代理路由也不走 middleware (避免双重认证)
  matcher: ["/((?!api/auth|api/health|api/agent|_next/static|_next/image|favicon.ico).*)"],
};
