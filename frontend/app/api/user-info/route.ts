// app/api/user-info/route.ts
import { NextResponse } from "next/server";

/**
 * 返回客户端 IP 地址 (供前端显示)
 *
 * IP 获取优先级 (参考 AGENTS.md 第 8 章 IP-based 解析):
 * 1. x-forwarded-for 首项 (可信代理链)
 * 2. x-real-ip (反向代理设置)
 * 3. CF-Connecting-IP (Cloudflare)
 * 4. unknown (无法获取)
 *
 * 注意: 生产环境应配置可信代理链, 避免 IP 伪造 (AGENTS.md 第 11 章安全硬约束)
 */
export async function GET(request: Request) {
  const headers = request.headers;
  const forwarded = headers.get("x-forwarded-for");
  const realIp = headers.get("x-real-ip");
  const cfIp = headers.get("CF-Connecting-IP");

  const ip = forwarded?.split(",")[0]?.trim() || realIp || cfIp || "unknown";

  return NextResponse.json({ ip });
}
