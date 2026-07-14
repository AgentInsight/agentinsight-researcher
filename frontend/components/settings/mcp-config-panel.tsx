// components/settings/mcp-config-panel.tsx
"use client";

import { useEffect, useState } from "react";
import { apiClient } from "@/lib/api-client";
import { useAuthStore } from "@/lib/auth-store";
import { useAgentStore } from "@/lib/agent-store";
import type { McpConfig } from "@/lib/types";

/**
 * MCP 配置管理面板
 * - per-Agent + per-User 隔离 (数据库 mcp_configs 表已含 agent_id 列)
 * - 切换 Agent 时刷新 MCP 配置列表
 */
export function McpConfigPanel() {
  const { getToken } = useAuthStore();
  const { currentAgent } = useAgentStore();
  const [configs, setConfigs] = useState<McpConfig[]>([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<McpConfig | null>(null);

  // 监听 currentAgent 变化, 刷新 MCP 配置列表 (per-Agent 隔离)
  useEffect(() => {
    const loadConfigs = async () => {
      setLoading(true);
      try {
        const token = getToken();
        const data = await apiClient.getMcpConfigs(token);
        setConfigs(data.configs || []);
      } catch {
        // 忽略错误
      } finally {
        setLoading(false);
      }
    };
    loadConfigs();
  }, [currentAgent, getToken]); // 切换 Agent 时刷新

  const handleSave = async (config: McpConfig) => {
    const token = getToken();
    await apiClient.saveMcpConfig(config, token);
    setEditing(null);
    // 刷新列表
    const data = await apiClient.getMcpConfigs(token);
    setConfigs(data.configs || []);
  };

  const handleDelete = async (id: string) => {
    const token = getToken();
    await apiClient.deleteMcpConfig(id, token);
    setConfigs((prev) => prev.filter((c) => c.id !== id));
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h2 className="text-lg font-semibold">MCP 工具配置</h2>
        <button
          onClick={() => setEditing({ name: "", command: "", args: [], env: {}, enabled: true, agent_id: currentAgent, user_id: "" })}
          className="bg-blue-600 text-white px-3 py-1 rounded text-sm"
        >
          + 新增
        </button>
      </div>

      {loading && <div className="text-gray-400 text-sm">加载中...</div>}

      {configs.map((c) => (
        <div key={c.id} className="border rounded p-3 flex justify-between items-center">
          <div>
            <div className="font-medium">{c.name}</div>
            <div className="text-xs text-gray-500">{c.command} {c.args.join(" ")}</div>
          </div>
          <div className="flex gap-2">
            <button onClick={() => setEditing(c)} className="text-blue-500 text-sm">编辑</button>
            <button onClick={() => c.id && handleDelete(c.id)} className="text-red-500 text-sm">删除</button>
          </div>
        </div>
      ))}

      {editing && (
        <McpConfigEditor
          config={editing}
          onSave={handleSave}
          onCancel={() => setEditing(null)}
        />
      )}
    </div>
  );
}

/** MCP 配置编辑器 */
function McpConfigEditor({
  config,
  onSave,
  onCancel,
}: {
  config: McpConfig;
  onSave: (config: McpConfig) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(config.name);
  const [command, setCommand] = useState(config.command);
  const [args, setArgs] = useState(config.args.join(" "));
  const [enabled, setEnabled] = useState(config.enabled);

  return (
    <div className="border rounded p-3 space-y-2">
      <div>
        <label className="block text-sm">名称</label>
        <input value={name} onChange={(e) => setName(e.target.value)} className="w-full border rounded p-1" />
      </div>
      <div>
        <label className="block text-sm">命令</label>
        <input value={command} onChange={(e) => setCommand(e.target.value)} className="w-full border rounded p-1" />
      </div>
      <div>
        <label className="block text-sm">参数 (空格分隔)</label>
        <input value={args} onChange={(e) => setArgs(e.target.value)} className="w-full border rounded p-1" />
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        启用
      </label>
      <div className="flex gap-2">
        <button
          onClick={() => onSave({ ...config, name, command, args: args.split(" ").filter(Boolean), enabled })}
          className="bg-blue-600 text-white px-3 py-1 rounded text-sm"
        >
          保存
        </button>
        <button onClick={onCancel} className="bg-gray-200 px-3 py-1 rounded text-sm">取消</button>
      </div>
    </div>
  );
}
