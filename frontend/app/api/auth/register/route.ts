// app/api/auth/register/route.ts
import { NextRequest, NextResponse } from "next/server";

const API_BASE = process.env.AUTH_API_BASE || "https://agentinsight.goldebridge.com";

/**
 * 注册代理 API Route
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const apiResponse = await fetch(`${API_BASE}/api/user`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await apiResponse.json();
    return NextResponse.json(data, { status: apiResponse.status });
  } catch {
    return NextResponse.json({ errorcode: -1, message: "注册代理请求失败" }, { status: 500 });
  }
}
