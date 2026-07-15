// app/api/auth/_utils.ts
/**
 * 认证代理工具函数 (供 app/api/auth/* 各 route 共用)
 * - 统一封装到 AgentInsightService 的 fetch 调用
 * - 内置 10s 超时 (AbortController)
 * - 自动设置 Content-Type: application/json
 * - 网络错误/超时返回 { data: null, status: 502|504 }, 调用方据 data === null 判断
 *
 * 注意: 文件名以 _ 开头, Next.js App Router 不会将其识别为路由
 */

/** AgentInsightService 后端地址 (服务端-only, 无 NEXT_PUBLIC_ 前缀) */
const API_BASE =
  process.env.AUTH_API_BASE || "https://agentinsight.goldebridge.com";

/** auth 请求默认超时 (10s) */
const DEFAULT_TIMEOUT_MS = 10_000;

export interface ProxyResult {
  /** 后端响应 JSON (已解析); 网络错误/超时/非 JSON 响应为 null */
  data: unknown;
  /** HTTP 状态码; 网络错误为 502, 超时为 504 */
  status: number;
}

/**
 * 代理 JSON 请求到 AgentInsightService
 *
 * @param path 后端路径 (如 "/api/user/login"), 会拼接 API_BASE
 * @param method HTTP 方法
 * @param body 请求体 (GET/DELETE 时忽略); 传入 undefined 表示无 body
 * @param timeoutMs 超时毫秒, 默认 10s
 * @returns { data, status } — 调用方根据 data === null 判断是否网络错误
 */
export async function proxyJson(
  path: string,
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
  body?: unknown,
  timeoutMs: number = DEFAULT_TIMEOUT_MS
): Promise<ProxyResult> {
  const url = `${API_BASE}${path}`;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const init: RequestInit = {
      method,
      headers: { "Content-Type": "application/json" },
      signal: ctrl.signal,
    };
    // GET/DELETE 无 body, 其他方法在 body 提供时序列化
    if (body !== undefined && method !== "GET" && method !== "DELETE") {
      init.body = JSON.stringify(body);
    }
    const res = await fetch(url, init);
    // 即使 HTTP 错误 (4xx/5xx) 也尝试读取 JSON, 后端错误信息通常在 body 中
    let data: unknown = null;
    try {
      data = await res.json();
    } catch {
      // 非 JSON 响应, 保持 null
    }
    return { data, status: res.status };
  } catch (err) {
    // 超时 (AbortError) 返回 504, 其他网络错误返回 502
    const isAbort = err instanceof Error && err.name === "AbortError";
    return {
      data: null,
      status: isAbort ? 504 : 502,
    };
  } finally {
    clearTimeout(timer);
  }
}
