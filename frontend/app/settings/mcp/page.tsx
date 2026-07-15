// app/settings/mcp/page.tsx
import { redirect } from "next/navigation";

/**
 * MCP 设置页 (已迁移为 mcp/researcher/setting 主区域)
 *
 * MCP 配置现在通过左侧导航栏选择 "MCP 服务" 模式访问
 * 此路由保留作为兼容入口, 自动重定向到 /mcp/researcher/setting
 */
export default function McpSettingsPage() {
  redirect("/mcp/researcher/setting");
}
