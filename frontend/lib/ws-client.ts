// lib/ws-client.ts
"use client";

import type { WsMessage } from "./types";

/**
 * WebSocket 客户端 (人在回路)
 * - 连接 URL 基于当前页面 host 构建 (通过 Nginx 反向代理)
 * - 支持 8 类结构化消息接收
 * - 支持发送 ping / human_feedback 消息
 * - 心跳保活 (30s) + 指数退避重连 (最多 5 次) + RAF 消息批量处理
 *
 * 架构 (方案B: Nginx 按 agent 路径分发):
 *   浏览器 → Nginx (同源同端口) → /v1/ws/{agentName}/{sessionId} → 对应 Agent 后端
 *
 * Nginx 根据 URL 中的 {agentName} 段路由到不同后端:
 *   /v1/ws/agentinsight-researcher/{sessionId} → agent-researcher:8066
 *   /v1/ws/agentinsight-writer/{sessionId}     → agent-writer:8067
 *
 * Next.js App Router route handler 不支持 WebSocket 升级,
 * 因此 WebSocket 由 Nginx 直接代理到后端 (绕过 Next.js)。
 * 浏览器只需连接与页面同源的 Nginx 地址, 无需知道后端端口。
 */
export class WsClient {
  private ws: WebSocket | null = null;
  private sessionId: string;
  private agentName: string;
  private onMessage: (msg: WsMessage) => void;

  // 重连相关
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private shouldReconnect = true;
  private readonly maxReconnectAttempts = 5;

  // 心跳相关
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private readonly heartbeatIntervalMs = 30_000;

  // 消息批量处理 (RAF)
  private pendingMessages: WsMessage[] = [];
  private rafId: number | null = null;

  constructor(
    sessionId: string,
    onMessage: (msg: WsMessage) => void,
    agentName: string = "agentinsight-researcher"
  ) {
    this.sessionId = sessionId;
    this.onMessage = onMessage;
    this.agentName = agentName;
  }

  /** 连接 WebSocket (通过 Nginx 同源同端口访问, 按 agentName 路由) */
  connect() {
    // 使用与页面相同的 host:port (Nginx 地址), Nginx 按 agentName 路由到对应后端
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host; // 含端口, 如 localhost:80 或者 example.com
    // URL 格式: /v1/ws/{agentName}/{sessionId}
    // Nginx 通过 location ~ ^/v1/ws/([^/]+)/ 捕获 agentName 并路由到对应后端
    const wsUrl = `${protocol}//${host}/v1/ws/${this.agentName}/${this.sessionId}`;

    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      // 连接成功: 重置重连计数并启动心跳
      this.reconnectAttempts = 0;
      this.startHeartbeat();
    };

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as WsMessage;
        // 心跳响应直接忽略, 不入队
        if (msg.type === "pong") return;
        // 批量处理: 推入待处理队列, 用 RAF 合并到下一帧统一回调
        this.pendingMessages.push(msg);
        if (this.rafId === null) {
          this.rafId = requestAnimationFrame(() => {
            this.rafId = null;
            const batch = this.pendingMessages;
            this.pendingMessages = [];
            for (const m of batch) this.onMessage(m);
          });
        }
      } catch {
        // 忽略非 JSON 消息
      }
    };

    this.ws.onclose = () => {
      this.ws = null;
      this.stopHeartbeat();
      // 仅在未主动关闭时尝试重连
      if (this.shouldReconnect) this.scheduleReconnect();
    };

    this.ws.onerror = () => {
      // WebSocket 错误信息 (onclose 会随后触发, 重连逻辑由 onclose 处理)
      console.warn(`[WsClient] ${this.agentName}/${this.sessionId} error`);
    };
  }

  /** 启动心跳定时器 (每 30s 发送一次 ping) */
  private startHeartbeat() {
    this.stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) this.ping();
    }, this.heartbeatIntervalMs);
  }

  /** 停止心跳定时器 */
  private stopHeartbeat() {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
  }

  /**
   * 调度重连 (指数退避)
   * delay = min(1000 * 2^attempts, 30_000), 最多重连 5 次
   */
  private scheduleReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) return;
    const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 30_000);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectAttempts++;
      this.connect();
    }, delay);
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

  /** 关闭连接 (主动关闭, 不触发重连) */
  close() {
    this.shouldReconnect = false;
    this.stopHeartbeat();
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    // 取消未处理的 RAF 回调
    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
    this.pendingMessages = [];
    this.ws?.close();
    this.ws = null;
  }
}
