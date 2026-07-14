// app/page.tsx
import { redirect } from "next/navigation";

/**
 * 首页: 重定向到 /chat
 * 多 Agent 预留, 当前默认选中 agentinsight-researcher
 */
export default function HomePage() {
  redirect("/chat");
}
