// lib/ws-client.ts
"use client";

import { useAgentStore } from "./agent-store";
import type { WsMessage } from "./types";

/**
 * WebSocket 客户端 (人在回路)
 * - 连接 URL 按当前选中 Agent 的 baseUrl 构建
 * - 支持 8 类结构化消息接收
 * - 支持发送 ping / human_feedback 消息
 *
 * 注意: WebSocket URL 从当前 Agent 的 apiUrl 转换 (http → ws, https → wss)
 */
export class WsClient {
  private ws: WebSocket | null = null;
  private sessionId: string;
  private onMessage: (msg: WsMessage) => void;

  constructor(sessionId: string, onMessage: (msg: WsMessage) => void) {
    this.sessionId = sessionId;
    this.onMessage = onMessage;
  }

  /** 连接 WebSocket (URL 按当前 Agent 的 baseUrl 构建) */
  connect() {
    // 从当前选中 Agent 的 apiUrl 构建 WebSocket URL
    const current = useAgentStore.getState().getCurrentAgent();
    const wsBaseUrl = current.apiUrl
      .replace(/^http/, "ws")  // http → ws, https → wss
      .replace(/:\d+/, `:${new URL(current.apiUrl).port}`); // 保持端口
    const wsUrl = `${wsBaseUrl}/v1/ws/${this.sessionId}`;

    this.ws = new WebSocket(wsUrl);

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as WsMessage;
        this.onMessage(msg);
      } catch {
        // 忽略非 JSON 消息
      }
    };

    this.ws.onclose = () => {
      this.ws = null;
    };

    this.ws.onerror = () => {
      // 错误处理
    };
  }

  /** 发送消息 */
  send(msg: WsMessage) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  /** 发送 ping (保活) */
  ping() {
    this.send({ type: "ping" });
  }

  /** 提交人在回路反馈 */
  submitFeedback(feedback: string) {
    this.send({ type: "human_feedback", feedback });
  }

  /** 关闭连接 */
  close() {
    this.ws?.close();
    this.ws = null;
  }
}
