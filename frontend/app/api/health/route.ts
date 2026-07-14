// app/api/health/route.ts
import { NextResponse } from "next/server";

/**
 * 健康检查端点 (供 docker-compose healthcheck 使用)
 * 不走 middleware (已在 matcher 中排除)
 */
export async function GET() {
  return NextResponse.json({ status: "ok" });
}
