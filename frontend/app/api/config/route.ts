// app/api/config/route.ts
import { NextResponse } from "next/server";

/**
 * 运行时配置接口 (供客户端组件读取)
 *
 * 返回容器运行时环境变量 (非 NEXT_PUBLIC_ 前缀, 不被构建时内联):
 * - selfHost: SELF_HOST 环境变量 (true=跳过登录, false=启用登录守卫)
 *
 * 客户端组件无法直接读取非 NEXT_PUBLIC_ 前缀的环境变量,
 * 通过此 API Route 在运行时获取配置。
 */
export async function GET() {
  return NextResponse.json({
    selfHost: process.env.SELF_HOST?.toLowerCase() === "true",
  });
}
