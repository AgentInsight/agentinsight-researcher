// app/api/auth/captcha/route.ts
import { NextResponse } from "next/server";

const API_BASE = process.env.AUTH_API_BASE || "https://agentinsight.goldebridge.com";

/**
 * 验证码代理 API Route
 * - 代理到 AgentInsightService /api/captcha
 */
export async function GET() {
  try {
    const apiResponse = await fetch(`${API_BASE}/api/captcha`);
    const data = await apiResponse.json();
    return NextResponse.json(data, { status: apiResponse.status });
  } catch {
    return NextResponse.json(
      { error: "验证码获取失败" },
      { status: 500 }
    );
  }
}
