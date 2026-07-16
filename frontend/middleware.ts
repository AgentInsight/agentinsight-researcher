// middleware.ts
import { NextRequest, NextResponse } from "next/server";

/**
 * SELF_HOST 路由守卫 (运行时读取环境变量, 不依赖构建时内联)
 *
 * 使用 process.env.SELF_HOST (非 NEXT_PUBLIC_ 前缀, 运行时从容器环境变量读取):
 * - SELF_HOST=true: 跳过登录守卫, 所有路由直接放行
 * - SELF_HOST=false: 未登录(无 auth-token cookie)重定向到 /login
 *
 * 注意: 不使用 NEXT_PUBLIC_ 前缀, 避免变量被 Next.js 构建时内联为字面量,
 *       导致运行时修改环境变量无效。middleware 运行在 Edge Runtime,
 *       可直接读取容器进程环境变量 process.env.SELF_HOST。
 */
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  // 运行时读取容器环境变量 (非 NEXT_PUBLIC_ 前缀, 不会被构建时内联)
  const selfHost = process.env.SELF_HOST?.toLowerCase() === "true";

  // SELF_HOST=true: 跳过所有登录守卫
  if (selfHost) {
    // 根路径重定向到 /agent/researcher/chat
    if (pathname === "/") {
      return NextResponse.redirect(new URL("/agent/researcher/chat", request.url));
    }
    return NextResponse.next();
  }

  // SELF_HOST=false: 走登录守卫
  // 从 httpOnly cookie 读取 auth-token (由 /api/auth/cookie 设置)
  const token = request.cookies.get("auth-token")?.value;
  const isAuthRoute = pathname.startsWith("/login") || pathname.startsWith("/register");

  // 已登录访问登录页 → 重定向 /agent/researcher/chat
  if (isAuthRoute && token) {
    return NextResponse.redirect(new URL("/agent/researcher/chat", request.url));
  }

  // 未登录访问受保护路由 → 重定向 /login
  // /api/health 已在 matcher 中排除, 无需在此检查
  if (!isAuthRoute && !token) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return NextResponse.next();
}

export const config = {
  // matcher: 排除 API Routes (auth/health/agent/config/proxy/user-info)、静态资源、图片、data 路由
  // /api/* 路径不走 middleware 登录守卫, 直接放行
  // 注意: /login 和 /register 仍走 middleware (用于已登录用户重定向到首页)
  // 注意: _next/data 排除 getStaticProps/getServerSideProps 的数据请求 (P2-14)
  matcher: ["/((?!api|_next/static|_next/image|_next/data|favicon.ico).*)"],
};
