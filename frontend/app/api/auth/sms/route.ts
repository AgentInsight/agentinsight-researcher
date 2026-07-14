// app/api/auth/sms/route.ts
import { NextRequest, NextResponse } from "next/server";

const API_BASE = process.env.AUTH_API_BASE || "https://agentinsight.goldebridge.com";

/**
 * 短信验证码代理 API Route
 */
export async function GET(request: NextRequest) {
  const phone = request.nextUrl.searchParams.get("phone");
  if (!phone) {
    return NextResponse.json({ error: "缺少 phone 参数" }, { status: 400 });
  }
  try {
    const apiResponse = await fetch(`${API_BASE}/api/captcha/sms?phone=${phone}`);
    const data = await apiResponse.json();
    return NextResponse.json(data, { status: apiResponse.status });
  } catch {
    return NextResponse.json({ error: "短信发送失败" }, { status: 500 });
  }
}
