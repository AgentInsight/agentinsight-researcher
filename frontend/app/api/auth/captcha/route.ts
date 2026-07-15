// app/api/auth/captcha/route.ts
import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "../_utils";

/**
 * 验证码代理 API Route (复制自 traceability-platform)
 * - 代理到 AgentInsightService /api/captcha?mobile={mobile}
 * - mobile 参数用于后端基于手机号生成验证码
 * - 内置 10s 超时 + 网络错误处理 (由 proxyJson 统一封装)
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const mobile = searchParams.get("mobile") || "";
    // 对 mobile 参数编码, 避免特殊字符注入
    const path = mobile
      ? `/api/captcha?mobile=${encodeURIComponent(mobile)}`
      : "/api/captcha";
    const { data, status } = await proxyJson(path, "GET");

    // 网络错误/超时: data 为 null
    if (data === null) {
      return NextResponse.json(
        { error: "验证码获取失败" },
        { status }
      );
    }

    return NextResponse.json(data, { status });
  } catch {
    return NextResponse.json(
      { error: "验证码获取失败" },
      { status: 500 }
    );
  }
}
