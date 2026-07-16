// lib/api-client.ts
import type {
  ChatMessage,
  SSEEvent,
  Session,
  SessionMessagesResponse,
  ReportConfig,
  McpConfig,
  McpSaveResponse,
  Source,
} from "./types";
import { useAgentStore } from "./agent-store";
import { DEPLOYMENT_MODE, getAgentByName } from "./agents.config";

/**
 * API 客户端 (支持双模式: 服务器Nginx / 本地直连)
 *
 * 部署模式 (由 NEXT_PUBLIC_DEPLOYMENT_MODE 环境变量控制):
 *   - server (默认): 通过 Nginx 按 agentName 路径分发
 *       baseUrl: /agent/{agentName} (相对路径, 浏览器同源访问 Nginx)
 *       示例: /agent/agentinsight-researcher/v1/sessions
 *   - local: 本地开发无 Nginx, 浏览器直连后端端口
 *       baseUrl: http://localhost:{localPort} (从 agents.config.ts 读取端口)
 *       示例: http://localhost:8066/v1/sessions
 *
 * 支持解析 OpenAI SSE 格式 + 自定义 delta 扩展字段
 *
 * 后端约定 (详见 src/api/routes.py):
 * - SSE 使用 OpenAI data: 行格式 (无 event: 命名事件)
 * - 自定义字段嵌入 delta 对象: delta.progress / delta.sources / delta.report_id / delta.file_path
 */
export class ApiClient {
  /**
   * 获取 baseUrl (双模式)
   * - server 模式: /agent/{agentName} (Nginx 按 agentName 路径分发)
   * - local 模式: http://localhost:{localPort} (浏览器直连后端端口)
   *
   * agentName 从 agent-store 运行时获取, 默认 "agentinsight-researcher"
   * localPort 从 agents.config.ts 的 AgentConfig 读取
   */
  private getBaseUrl(): string {
    const agentName = this.getCurrentAgentName();

    if (DEPLOYMENT_MODE === "local") {
      // 本地开发模式: 浏览器直连后端端口 (无 Nginx)
      const agent = getAgentByName(agentName);
      const port = agent?.localPort ?? 8066;
      return `http://localhost:${port}`;
    }

    // 服务器模式: 通过 Nginx 按 agentName 路径分发
    return `/agent/${agentName}`;
  }

  /** 从 agent-store 获取当前 agent name (运行时) */
  private getCurrentAgentName(): string {
    try {
      return useAgentStore.getState().getCurrentAgent().name;
    } catch {
      // SSR 或 store 未初始化时降级到默认 agent
      return "agentinsight-researcher";
    }
  }

  /** 构建请求头 */
  private buildHeaders(token?: string, json = false): Record<string, string> {
    const headers: Record<string, string> = {};
    if (json) headers["Content-Type"] = "application/json";
    if (token) headers["Authorization"] = `Bearer ${token}`;
    return headers;
  }

  /**
   * 带超时的 fetch 封装
   * - 默认超时 30 秒
   * - 支持外部 signal (如果有外部 signal, 绑定到内部 ctrl, 任一触发即 abort)
   * - 注意: 流式请求 (chatStream) 不应使用此方法, 因为流可能持续很长时间
   */
  private async fetchWithTimeout(
    url: string,
    init: RequestInit,
    timeoutMs = 30_000
  ): Promise<Response> {
    const ctrl = new AbortController();
    const externalSignal = init.signal;
    if (externalSignal) {
      // 外部 signal 已 abort 则立即 abort, 否则监听 abort 事件转发
      if (externalSignal.aborted) ctrl.abort();
      else externalSignal.addEventListener("abort", () => ctrl.abort(), { once: true });
    }
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      return await fetch(url, { ...init, signal: ctrl.signal });
    } finally {
      clearTimeout(timer);
    }
  }

  /**
   * 发送聊天请求 (SSE 流式)
   * 解析 OpenAI 兼容 SSE: data: {choices:[{delta:{...}}]}
   * 自定义 delta 字段: progress / sources / report_id / file_path / report_format
   */
  async *chatStream(
    messages: ChatMessage[],
    sessionId: string,
    token?: string,
    options?: {
      report_type?: string;
      report_format?: string;
      language?: string;
      uploaded_files?: string[];
      signal?: AbortSignal;
    }
  ): AsyncGenerator<SSEEvent> {
    const baseUrl = this.getBaseUrl();
    const response = await fetch(`${baseUrl}/v1/chat/completions`, {
      method: "POST",
      headers: this.buildHeaders(token, true),
      body: JSON.stringify({
        model: this.getCurrentAgentName(),
        messages,
        stream: true,
        session_id: sessionId,
        ...(options?.report_type ? { report_type: options.report_type } : {}),
        ...(options?.report_format ? { report_format: options.report_format } : {}),
        ...(options?.language ? { language: options.language } : {}),
        ...(options?.uploaded_files?.length ? { uploaded_files: options.uploaded_files } : {}),
      }),
      signal: options?.signal,
    });

    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new Error(`HTTP ${response.status}: ${response.statusText}${text ? ` - ${text}` : ""}`);
    }

    // SSE 解析: 仅 data: 行 (OpenAI 兼容)
    const reader = response.body?.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (reader) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const sseMessages = buffer.split("\n\n");
      buffer = sseMessages.pop() || "";

      for (const msg of sseMessages) {
        const lines = msg.split("\n");
        let eventData = "";
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            eventData += line.slice(6);
          } else if (line.startsWith("data:")) {
            eventData += line.slice(5);
          }
        }

        if (!eventData) continue;
        if (eventData === "[DONE]") return;

        let parsedData: unknown = eventData;
        try {
          parsedData = JSON.parse(eventData);
        } catch {
          // 非 JSON, 保持原始字符串
        }

        // OpenAI 标准格式: 统一为 message 事件
        // 自定义字段通过 delta 对象传递
        yield {
          event: "message",
          data: parsedData,
        };
      }
    }
  }

  // ========== 会话管理 ==========

  /** 获取会话列表 (后端返回数组, 非对象) */
  async getSessions(token?: string): Promise<Session[]> {
    const res = await this.fetchWithTimeout(`${this.getBaseUrl()}/v1/sessions`, {
      headers: this.buildHeaders(token),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    // 后端返回 list[dict] 数组, 兼容 {sessions: [...]} 格式
    return Array.isArray(data) ? data : data.sessions || [];
  }

  /** 创建新会话 (后端返回会话对象) */
  async createSession(token?: string, title?: string): Promise<Session> {
    const res = await this.fetchWithTimeout(`${this.getBaseUrl()}/v1/sessions`, {
      method: "POST",
      headers: this.buildHeaders(token, true),
      body: JSON.stringify({ title: title || "新会话" }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  /** 删除会话 */
  async deleteSession(sessionId: string, token?: string): Promise<void> {
    await this.fetchWithTimeout(`${this.getBaseUrl()}/v1/sessions/${sessionId}`, {
      method: "DELETE",
      headers: this.buildHeaders(token),
    });
  }

  /** 更新会话标题 */
  async renameSession(sessionId: string, title: string, token?: string): Promise<void> {
    await this.fetchWithTimeout(`${this.getBaseUrl()}/v1/sessions/${sessionId}`, {
      method: "PATCH",
      headers: this.buildHeaders(token, true),
      body: JSON.stringify({ title }),
    });
  }

  /** 获取会话消息 (分页) */
  async getSessionMessages(
    sessionId: string,
    token?: string,
    limit = 50,
    offset = 0
  ): Promise<SessionMessagesResponse> {
    const res = await this.fetchWithTimeout(
      `${this.getBaseUrl()}/v1/sessions/${sessionId}/messages?limit=${limit}&offset=${offset}`,
      { headers: this.buildHeaders(token) }
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  // ========== 报告配置 ==========

  /** 获取会话报告配置 */
  async getSessionConfig(sessionId: string, token?: string): Promise<ReportConfig> {
    const res = await this.fetchWithTimeout(`${this.getBaseUrl()}/v1/sessions/${sessionId}/config`, {
      headers: this.buildHeaders(token),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  /** 更新会话报告配置 */
  async updateSessionConfig(
    sessionId: string,
    config: ReportConfig,
    token?: string
  ): Promise<void> {
    await this.fetchWithTimeout(`${this.getBaseUrl()}/v1/sessions/${sessionId}/config`, {
      method: "PUT",
      headers: this.buildHeaders(token, true),
      body: JSON.stringify(config),
    });
  }

  // ========== MCP 配置 (URL 已修正) ==========

  /** 获取 MCP 配置列表 (后端返回数组, 非对象) */
  async getMcpConfigs(token?: string): Promise<McpConfig[]> {
    const res = await this.fetchWithTimeout(`${this.getBaseUrl()}/v1/mcp`, {
      headers: this.buildHeaders(token),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return Array.isArray(data) ? data : data.configs || [];
  }

  /** 获取系统 MCP 配置列表 */
  async getSystemMcpConfigs(token?: string): Promise<McpConfig[]> {
    const res = await this.fetchWithTimeout(`${this.getBaseUrl()}/v1/mcp/system`, {
      headers: this.buildHeaders(token),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return Array.isArray(data) ? data : data.configs || [];
  }

  /** 保存/新建 MCP 配置 (POST /v1/mcp) */
  async saveMcpConfig(config: McpConfig, token?: string): Promise<McpSaveResponse> {
    const res = await this.fetchWithTimeout(`${this.getBaseUrl()}/v1/mcp`, {
      method: "POST",
      headers: this.buildHeaders(token, true),
      body: JSON.stringify(config),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  /** 更新 MCP 配置 (PUT /v1/mcp/{id}) */
  async updateMcpConfig(
    configId: number,
    config: Partial<McpConfig>,
    token?: string
  ): Promise<McpSaveResponse> {
    const res = await this.fetchWithTimeout(`${this.getBaseUrl()}/v1/mcp/${configId}`, {
      method: "PUT",
      headers: this.buildHeaders(token, true),
      body: JSON.stringify(config),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  /** 删除 MCP 配置 (DELETE /v1/mcp/{id}) */
  async deleteMcpConfig(configId: number, token?: string): Promise<void> {
    await this.fetchWithTimeout(`${this.getBaseUrl()}/v1/mcp/${configId}`, {
      method: "DELETE",
      headers: this.buildHeaders(token),
    });
  }

  /** 测试 MCP 配置 (POST /v1/mcp/test, 不入库) */
  async testMcpConfig(config: Partial<McpConfig>, token?: string): Promise<unknown> {
    const res = await this.fetchWithTimeout(`${this.getBaseUrl()}/v1/mcp/test`, {
      method: "POST",
      headers: this.buildHeaders(token, true),
      body: JSON.stringify(config),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  /** 测试已保存的 MCP 配置 (POST /v1/mcp/{id}/test) */
  async testExistingMcpConfig(configId: number, token?: string): Promise<unknown> {
    const res = await this.fetchWithTimeout(`${this.getBaseUrl()}/v1/mcp/${configId}/test`, {
      method: "POST",
      headers: this.buildHeaders(token),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  /** 克隆系统 MCP 到用户列表 (POST /v1/mcp/system/{id}/clone) */
  async cloneSystemMcpConfig(configId: number, token?: string): Promise<McpConfig> {
    const res = await this.fetchWithTimeout(`${this.getBaseUrl()}/v1/mcp/system/${configId}/clone`, {
      method: "POST",
      headers: this.buildHeaders(token),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  // ========== 人在回路 ==========

  /** 提交人在回路反馈 */
  async submitFeedback(sessionId: string, feedback: string, token?: string): Promise<void> {
    await this.fetchWithTimeout(`${this.getBaseUrl()}/v1/feedback`, {
      method: "POST",
      headers: this.buildHeaders(token, true),
      body: JSON.stringify({ session_id: sessionId, feedback }),
    });
  }

  // ========== 文件上传 ==========

  /** 上传文件 (POST /v1/files) */
  async uploadFile(file: File, token?: string): Promise<{ file_id: string; filename: string }> {
    const formData = new FormData();
    formData.append("file", file);
    const res = await this.fetchWithTimeout(`${this.getBaseUrl()}/v1/files`, {
      method: "POST",
      headers: this.buildHeaders(token),
      body: formData,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  // ========== 报告下载 ==========

  /** 构建报告下载 URL */
  getReportDownloadUrl(reportId: string, format: string = "markdown"): string {
    return `${this.getBaseUrl()}/v1/reports/${reportId}/download?format=${format}`;
  }

  /** 获取会话报告列表 */
  async getSessionReports(sessionId: string, token?: string): Promise<Array<{
    report_id: string;
    session_id: string;
    query: string;
    report_format: string;
    created_at: string;
  }>> {
    const res = await this.fetchWithTimeout(`${this.getBaseUrl()}/v1/reports/session/${sessionId}`, {
      headers: this.buildHeaders(token),
    });
    if (!res.ok) return [];
    return res.json();
  }
}

export const apiClient = new ApiClient();
