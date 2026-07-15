// app/settings/page.tsx
import { redirect } from "next/navigation";

/**
 * 设置页 (已迁移为 slide-in 面板)
 *
 * 设置功能现在通过聊天页右上角的设置图标打开 (SettingsPanel 组件)
 * 此路由保留作为兼容入口, 自动重定向到 /agent/researcher/chat
 */
export default function SettingsPage() {
  redirect("/agent/researcher/chat");
}
