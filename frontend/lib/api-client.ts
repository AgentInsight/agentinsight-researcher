// lib/api-client.ts
import { useAgentStore } from "./agent-store";
import type { ChatMessage, SSEEvent } from "./types";

/**
 * 多 Agent API 客户端
 * - baseUrl 从当前选中 Agent 配置读取
 * - 切换 Agent 时自动更新 baseUrl
 * - 支持解析 SSE 自定义事件 (tool_call / sources)
 */
export class ApiClient {
  /** 获取当前 Agent 的 baseUrl */
  private getBaseUrl(): string {
    // 直接读取 agent-store 的当前值 (非 React 组件内调用)
    const current = useAgentStore.getState().getCurrentAgent();
    return current.apiUrl;
  }

  /**
   * 发送聊天请求 (SSE 流式)
   * 解析 SSE 事件: data (标准) / event: tool_call / event: sources
   * 返回结构化 SSE 事件流
   */
  async *chatStream(
    messages: ChatMessage[],
    sessionId: string,
    token?: string
  ): AsyncGenerator<SSEEvent> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    // SELF_HOST=false 时携带 token
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }

    const baseUrl = this.getBaseUrl();
    const response = await fetch(`${baseUrl}/v1/chat/completions`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        model: "agentinsight-researcher",
        messages,
        stream: true,
        session_id: sessionId,
      }),
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    // SSE 解析: 支持 event: 和 data: 行
    const reader = response.body?.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (reader) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE 消息以双换行分隔
      const sseMessages = buffer.split("\n\n");
      buffer = sseMessages.pop() || "";

      for (const msg of sseMessages) {
        const lines = msg.split("\n");
        let eventData = "";
        let eventType = "message";

        for (const line of lines) {
          if (line.startsWith("event: ")) {
            eventType = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            eventData = line.slice(6);
          }
        }

        // 跳过空消息
        if (!eventData && eventType === "message") continue;

        // [DONE] 标记结束
        if (eventData === "[DONE]") return;

        // 解析 data 内容
        let parsedData: unknown = eventData;
        try {
          parsedData = JSON.parse(eventData);
        } catch {
          // 非 JSON, 保持原始字符串
        }

        yield {
          event: eventType,
          data: parsedData,
        };
      }
    }
  }

  /** 获取会话列表 (按当前 Agent 隔离) */
  async getSessions(token?: string) {
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(`${this.getBaseUrl()}/v1/sessions`, { headers });
    return res.json();
  }

  /** 创建新会话 */
  async createSession(token?: string, title?: string) {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(`${this.getBaseUrl()}/v1/sessions`, {
      method: "POST",
      headers,
      body: JSON.stringify({ title: title || "新会话" }),
    });
    return res.json();
  }

  /** 删除会话 */
  async deleteSession(sessionId: string, token?: string) {
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(`${this.getBaseUrl()}/v1/sessions/${sessionId}`, {
      method: "DELETE",
      headers,
    });
    return res.json();
  }

  /** 获取 MCP 配置列表 (按当前 Agent 隔离) */
  async getMcpConfigs(token?: string) {
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(`${this.getBaseUrl()}/v1/mcp/configs`, { headers });
    return res.json();
  }

  /** 保存 MCP 配置 */
  async saveMcpConfig(config: unknown, token?: string) {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(`${this.getBaseUrl()}/v1/mcp/configs`, {
      method: "POST",
      headers,
      body: JSON.stringify(config),
    });
    return res.json();
  }

  /** 删除 MCP 配置 */
  async deleteMcpConfig(configId: string, token?: string) {
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(`${this.getBaseUrl()}/v1/mcp/configs/${configId}`, {
      method: "DELETE",
      headers,
    });
    return res.json();
  }

  /** 提交人在回路反馈 */
  async submitFeedback(sessionId: string, feedback: string, token?: string) {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(`${this.getBaseUrl()}/v1/feedback`, {
      method: "POST",
      headers,
      body: JSON.stringify({ session_id: sessionId, feedback }),
    });
    return res.json();
  }

  /** 上传文件 */
  async uploadFile(file: File, sessionId: string, token?: string) {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("session_id", sessionId);
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(`${this.getBaseUrl()}/v1/files/upload`, {
      method: "POST",
      headers,
      body: formData,
    });
    return res.json();
  }
}

export const apiClient = new ApiClient();
