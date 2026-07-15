// app/api/auth/sms/route.ts
import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "../_utils";

/**
 * 短信验证码代理 API Route
 * - 代理到 AgentInsightService /api/captcha/sms?phone={phone}
 * - phone 参数经 encodeURIComponent 编码, 防止特殊字符注入
 * - 内置 10s 超时 + 网络错误处理 (由 proxyJson 统一封装)
 */
export async function GET(request: NextRequest) {
  const phone = request.nextUrl.searchParams.get("phone");
  if (!phone) {
    return NextResponse.json({ error: "缺少 phone 参数" }, { status: 400 });
  }
  try {
    // P1-11 修复: 对 phone 参数编码, 避免特殊字符注入
    const path = `/api/captcha/sms?phone=${encodeURIComponent(phone)}`;
    const { data, status } = await proxyJson(path, "GET");

    // 网络错误/超时: data 为 null
    if (data === null) {
      return NextResponse.json(
        { error: "短信发送失败" },
        { status }
      );
    }

    return NextResponse.json(data, { status });
  } catch {
    return NextResponse.json({ error: "短信发送失败" }, { status: 500 });
  }
}
