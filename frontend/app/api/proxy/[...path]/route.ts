// app/api/proxy/[...path]/route.ts
import { NextRequest } from "next/server";

/**
 * 通用 HTTP 代理 (按 agent 路径分发到不同后端 Agent API)
 *
 * 方案B: Nginx 按 agent 路径分发
 * - 客户端请求路径格式: /api/proxy/{agentName}/{后端路径}
 *   例如: /api/proxy/agentinsight-researcher/v1/chat/completions
 * - 从路径第一段提取 agentName, 根据它选择对应后端
 * - 后端地址从运行时环境变量读取:
 *   1. AGENT_{NAME_UPPER}_API_URL (agentName 的 "-" 替换为 "_", 大写)
 *   2. AGENT_RESEARCHER_API_URL (向后兼容, 仅 agentinsight-researcher)
 *   3. 默认值 http://agent-researcher:8066
 *
 * 支持透传:
 * - 所有 HTTP 方法 (GET/POST/PUT/PATCH/DELETE)
 * - 请求头 (Authorization/Content-Type 等)
 * - 请求 body (流式透传 ReadableStream, 不缓冲到内存)
 * - SSE 流式响应 (chat/completions stream=true)
 * - 二进制响应 (文件下载/PDF/DOCX)
 *
 * 不支持的: WebSocket 升级 (由 Nginx 按 /v1/ws/{agentName}/{sessionId} 直连后端)
 */
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** 默认后端地址 (agentinsight-researcher) */
const DEFAULT_BACKEND_URL = "http://agent-researcher:8066";

/** GET/DELETE 请求超时 (30s) */
const SHORT_TIMEOUT_MS = 30_000;
/** POST/PUT/PATCH 非流式请求超时 (5min, 适配大文件上传/长报告生成) */
const LONG_TIMEOUT_MS = 5 * 60_000;

/**
 * 根据 agentName 解析后端 URL
 * 优先级: AGENT_{NAME_UPPER}_API_URL > AGENT_RESEARCHER_API_URL (兼容) > 默认值
 */
function resolveBackendUrl(agentName: string): string {
  // 1. AGENT_{NAME_UPPER}_API_URL: agentName 的 "-" 替换为 "_", 大写
  //    例如: agentinsight-researcher → AGENT_AGENTINSIGHT_RESEARCHER_API_URL
  //         agentinsight-writer     → AGENT_AGENTINSIGHT_WRITER_API_URL
  const envKey = `AGENT_${agentName.replace(/-/g, "_").toUpperCase()}_API_URL`;
  const envUrl = process.env[envKey];
  if (envUrl) return envUrl;

  // 2. 向后兼容: AGENT_RESEARCHER_API_URL (仅 agentinsight-researcher)
  if (agentName === "agentinsight-researcher") {
    const legacyUrl = process.env.AGENT_RESEARCHER_API_URL;
    if (legacyUrl) return legacyUrl;
  }

  // 3. 默认值
  return DEFAULT_BACKEND_URL;
}

/**
 * 判断是否为流式请求 (chat/completions, 不设超时)
 * 流式响应可能持续很长时间, 设置超时会中断流
 */
function isStreamingRequest(method: string, restSegments: string): boolean {
  if (method !== "POST") return false;
  return restSegments.endsWith("v1/chat/completions") || restSegments.includes("chat/completions");
}

/** 创建带超时的 AbortController, 返回 [controller, cleanupFn] */
function createTimeoutController(timeoutMs: number): [AbortController, () => void] {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  return [ctrl, () => clearTimeout(timer)];
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  return proxyRequest(request, await params);
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  return proxyRequest(request, await params);
}

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  return proxyRequest(request, await params);
}

export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  return proxyRequest(request, await params);
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  return proxyRequest(request, await params);
}

/**
 * 代理请求到对应后端
 * 路径格式: /api/proxy/{agentName}/{rest...}
 * 第一段是 agentName, 剩余段转发给后端
 */
async function proxyRequest(
  request: NextRequest,
  params: { path: string[] }
): Promise<Response> {
  // 路径格式: [agentName, ...restSegments]
  // 例如: ["agentinsight-researcher", "v1", "chat", "completions"]
  if (params.path.length === 0) {
    return new Response("Bad Request: missing agent name in path", {
      status: 400,
    });
  }

  const agentName = params.path[0];
  const restSegments = params.path.slice(1).join("/");
  const backendUrl = resolveBackendUrl(agentName);
  const search = request.nextUrl.search;
  const targetUrl = `${backendUrl}/${restSegments}${search}`;

  // 透传请求头 (排除 host 和 content-length, fetch 会基于 body 重算)
  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("content-length");

  // GET/DELETE 无 body, 其他方法直接透传 ReadableStream (流式, 不缓冲到内存)
  const hasBody = request.method !== "GET" && request.method !== "DELETE";
  const body: BodyInit | null = hasBody ? request.body : null;

  // 超时策略:
  // - 流式请求 (chat/completions): 不设超时, 避免长流被中断
  // - GET/DELETE: 30s 超时
  // - POST/PUT/PATCH 非流式: 5min 超时 (适配大文件上传/长报告生成)
  const streaming = isStreamingRequest(request.method, restSegments);
  let ctrl: AbortController | null = null;
  let cleanupTimeout: (() => void) | null = null;
  if (!streaming) {
    const timeoutMs =
      request.method === "GET" || request.method === "DELETE"
        ? SHORT_TIMEOUT_MS
        : LONG_TIMEOUT_MS;
    [ctrl, cleanupTimeout] = createTimeoutController(timeoutMs);
  }

  try {
    const fetchInit: RequestInit & { duplex?: "half" } = {
      method: request.method,
      headers,
      body,
      signal: ctrl?.signal,
    };
    // 流式 body 需要 duplex: "half" (Node.js undici 要求)
    if (body) {
      fetchInit.duplex = "half";
    }

    const backendRes = await fetch(targetUrl, fetchInit);

    // 透传响应头
    const resHeaders = new Headers(backendRes.headers);
    resHeaders.delete("content-encoding"); // 移除压缩编码, 避免双重压缩

    // SSE 流式: 透传 ReadableStream
    if (backendRes.body) {
      return new Response(backendRes.body, {
        status: backendRes.status,
        statusText: backendRes.statusText,
        headers: resHeaders,
      });
    }

    return new Response(null, {
      status: backendRes.status,
      statusText: backendRes.statusText,
      headers: resHeaders,
    });
  } catch (err) {
    // 超时 (AbortError) 返回 504, 其他错误返回 502
    const isAbort = err instanceof Error && err.name === "AbortError";
    return new Response(
      isAbort ? "Gateway Timeout" : "Bad Gateway",
      { status: isAbort ? 504 : 502 }
    );
  } finally {
    if (cleanupTimeout) cleanupTimeout();
  }
}
