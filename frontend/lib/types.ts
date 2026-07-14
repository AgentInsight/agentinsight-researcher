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

/** 检索来源 */
export interface Source {
  title: string;
  url: string;
  content: string;
  score: number;
  source_type?: string;
}

/** SSE 事件 (含自定义事件) */
export interface SSEEvent {
  /** 事件类型: message (标准) / tool_call / sources / progress / report */
  event: string;
  /** 事件数据 (JSON 解析后或原始字符串) */
  data: unknown;
}

/** 会话信息 */
export interface Session {
  session_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  agent_id: string;
  user_id: string;
}

/** MCP 配置 */
export interface McpConfig {
  id?: string;
  name: string;
  command: string;
  args: string[];
  env: Record<string, string>;
  enabled: boolean;
  agent_id: string;
  user_id: string;
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
  | { type: "logs"; data: string }
  | { type: "content"; data: string }
  | { type: "node_progress"; data: { node: string; progress: string } }
  | { type: "sources"; data: Source[] }
  | { type: "tool_call"; data: ToolCall }
  | { type: "report"; data: { format: string; content: string } }
  | { type: "human_feedback_request"; data: { node: string; content: string } }
  | { type: "error"; data: string }
  | { type: "human_feedback"; feedback: string };
