// app/api/auth/logout/route.ts
import { NextResponse } from "next/server";

/**
 * 登出 API Route
 * - 清除 httpOnly cookie (auth-token)
 */
export async function POST() {
  const response = NextResponse.json({ success: true });
  response.cookies.delete("auth-token");
  return response;
}
