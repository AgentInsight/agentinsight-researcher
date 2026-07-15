// app/api/auth/resetpassword/route.ts
import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "../_utils";

/**
 * 重置密码代理 API Route (复制自 traceability-platform)
 * - 代理到 AgentInsightService /api/user/resetpassword
 * - 内置 10s 超时 + 网络错误处理 (由 proxyJson 统一封装)
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { data, status } = await proxyJson(
      "/api/user/resetpassword",
      "POST",
      body
    );

    // 网络错误/超时: data 为 null
    if (data === null) {
      return NextResponse.json(
        { errorcode: -1, message: "重置密码代理请求失败" },
        { status }
      );
    }

    return NextResponse.json(data, { status });
  } catch {
    return NextResponse.json(
      { errorcode: -1, message: "重置密码代理请求失败" },
      { status: 500 }
    );
  }
}
