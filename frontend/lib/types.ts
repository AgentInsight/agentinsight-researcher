// lib/types.ts
/**
 * TypeScript 类型定义
 * 与后端 Pydantic 类型对齐
 */

/** 聊天消息 */
export interface ChatMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
}

/** 工具调用 */
export interface ToolCall {
  id: string;
  type: "function";
  function: {
    name: string;
    arguments: string;
  };
}

/** 检索来源 (与后端 Source 对齐) */
export interface Source {
  title: string;
  url: string;
  snippet?: string;
  content?: string;
  score: number;
  source_type?: string;
}

/** SSE 事件 (含 OpenAI 标准格式 + 自定义扩展) */
export interface SSEEvent {
  /** 事件类型: message (标准 OpenAI) / tool_call / sources / progress / report */
  event: string;
  /** 事件数据 (JSON 解析后或原始字符串) */
  data: unknown;
}

/** 会话信息 (与后端 list_sessions 响应对齐) */
export interface Session {
  session_id: string;
  title: string;
  query?: string;
  status?: string;
  report_type?: string;
  report_format?: string;
  language?: string;
  created_at: string;
  updated_at: string;
  message_count?: number;
  agent_id?: string;
  user_id?: string;
}

/** 会话消息 (与后端 GET /v1/sessions/{id}/messages 响应对齐) */
export interface SessionMessage {
  id: number;
  session_id: string;
  agent_id?: string;
  user_id?: string;
  role: "user" | "assistant";
  content: string;
  message_metadata?: Record<string, unknown>;
  created_at: string;
}

/** 会话消息列表响应 */
export interface SessionMessagesResponse {
  messages: SessionMessage[];
  total: number;
  has_more: boolean;
  limit: number;
  offset: number;
}

/** 报告配置 (与后端 GET/PUT /v1/sessions/{id}/config 对齐) */
export interface ReportConfig {
  report_type?: string;
  report_format?: string;
  language?: string;
}

/** MCP 配置 (与后端 MCPConfigResponse 对齐) */
export interface McpConfig {
  id?: number;
  name: string;
  server_url?: string;
  transport_type?: "stdio" | "sse" | "streamable_http";
  command?: string;
  args?: string[];
  env_vars?: Record<string, string>;
  enabled: boolean;
  is_system?: boolean;
  description?: string;
  created_at?: string;
  updated_at?: string;
}

/** MCP 配置测试结果 */
export interface McpTestResult {
  success: boolean;
  message: string;
  error_type?: string | null;
  tools_count?: number;
  tools?: string[];
  latency_ms?: number;
}

/** MCP 配置保存响应 (含测试结果) */
export interface McpSaveResponse extends McpConfig {
  test_result?: McpTestResult;
}

/** 报告下载链接 */
export interface DownloadLink {
  format: string;
  url: string;
  filename: string;
}

/** WebSocket 消息 (8 类结构化消息) */
export type WsMessage =
  | { type: "ping" }
  | { type: "pong" }
  | { type: "logs"; message: string; session_id?: string }
  | { type: "content"; content: string }
  | { type: "node_progress"; node: string; label?: string; progress: string }
  | { type: "sources"; sources: Source[] }
  | { type: "tool_call"; tool_name: string; input: unknown; output: unknown }
  | { type: "report"; report_md: string; report_id?: string }
  | { type: "human_feedback_request"; message: string; session_id?: string }
  | { type: "error"; message: string }
  | { type: "human_feedback"; feedback: string };
