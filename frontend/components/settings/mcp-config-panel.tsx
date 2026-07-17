// components/settings/mcp-config-panel.tsx
"use client";

import { useEffect, useState, useCallback } from "react";
import { apiClient } from "@/lib/api-client";
import { useAuthStore } from "@/lib/auth-store";
import { useAgentStore } from "@/lib/agent-store";
import { TTLCache } from "@/lib/cache";
import type { McpConfig, McpTestResult } from "@/lib/types";
import {
  Plus,
  Pencil,
  Trash2,
  Copy,
  Play,
  CheckCircle,
  XCircle,
  Loader2,
  Server,
  Terminal,
  Globe,
  Power,
  Blocks,
  ArrowLeft,
} from "lucide-react";
import { Tooltip } from "@/components/ui/tooltip";

/**
 * MCP 工具配置管理面板 (参考 Linear/Stripe 设计系统)
 *
 * 功能:
 * - 选项卡: "我的配置" / "系统 MCP"
 * - 我的配置: 卡片网格 + 新增/编辑/删除/测试/启用切换
 * - 系统 MCP: 卡片列表 + 添加到我的配置
 * - 编辑器: name/transport_type/server_url/command/args/env_vars/description/enabled
 * - 测试结果展示 (success/message/tools_count/latency_ms)
 *
 * UI/UX 优化:
 * - 卡片左侧状态指示条 (启用=主色, 禁用=灰色)
 * - 服务器图标用带背景色的圆角容器
 * - hover 效果更明显 (阴影 + 边框色变化)
 * - 选项卡激活态有背景高亮 + 底部指示条
 */

/**
 * 防御性: 将 args 字段统一为 string[] (后端 JSONB 可能返回字符串)
 */
function normalizeArgs(args: unknown): string[] {
  if (Array.isArray(args)) return args as string[];
  if (typeof args === "string") {
    try {
      const parsed = JSON.parse(args);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }
  return [];
}

/**
 * 防御性: 将 env_vars 字段统一为 Record<string, string> (后端 JSONB 可能返回字符串)
 */
function normalizeEnvVars(env: unknown): Record<string, string> {
  if (env && typeof env === "object" && !Array.isArray(env)) {
    return env as Record<string, string>;
  }
  if (typeof env === "string") {
    try {
      const parsed = JSON.parse(env);
      return (parsed && typeof parsed === "object" && !Array.isArray(parsed))
        ? parsed
        : {};
    } catch {
      return {};
    }
  }
  return {};
}

type TransportType = "stdio" | "sse" | "streamable_http";

type Tab = "user" | "system";

/**
 * 任务2: MCP 配置模块级缓存 (agent 维度)
 * 避免 tab/Agent 切换时重复 fetch, 采用 stale-while-revalidate 策略
 * - 进入页面: 用缓存即时渲染, 后台静默刷新
 * - tab 切换: 不触发 fetch, 直接用已缓存的两个 tab 数据
 *
 * TTL + LRU 控制: 防止模块级 Map 永不清理导致内存泄漏 (P0-10)
 * - TTL=5 分钟, maxSize=20 (覆盖足够多 agent 切换)
 * - 保存/删除后显式 invalidate, 强制下次刷新
 */
const mcpCache = {
  user: new TTLCache<string, McpConfig[]>(5 * 60 * 1000, 20),
  system: new TTLCache<string, McpConfig[]>(5 * 60 * 1000, 20),
  getUser(key: string) {
    return this.user.get(key);
  },
  setUser(key: string, data: McpConfig[]) {
    this.user.set(key, data);
  },
  getSystem(key: string) {
    return this.system.get(key);
  },
  setSystem(key: string, data: McpConfig[]) {
    this.system.set(key, data);
  },
  invalidateUser(key: string) {
    this.user.invalidate(key);
  },
};

export function McpConfigPanel() {
  const { getToken } = useAuthStore();
  const { currentAgent } = useAgentStore();
  const [tab, setTab] = useState<Tab>("user");
  const [userConfigs, setUserConfigs] = useState<McpConfig[]>([]);
  const [systemConfigs, setSystemConfigs] = useState<McpConfig[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<McpConfig | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [testing, setTesting] = useState<number | "new" | null>(null);
  const [testResults, setTestResults] = useState<Record<number, McpTestResult>>({});

  // 任务2: 模块级缓存 (agent 维度), 避免 tab/Agent 切换时重复 fetch
  // 缓存策略: stale-while-revalidate — 先用缓存即时渲染, 后台静默刷新
  const cacheKey = currentAgent || "default";

  const loadUserConfigs = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const token = getToken();
      const data = await apiClient.getMcpConfigs(token);
      setUserConfigs(data);
      // 更新缓存
      mcpCache.setUser(cacheKey, data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载配置失败");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [getToken, cacheKey]);

  const loadSystemConfigs = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const token = getToken();
      const data = await apiClient.getSystemMcpConfigs(token);
      setSystemConfigs(data);
      // 更新缓存
      mcpCache.setSystem(cacheKey, data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载系统配置失败");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [getToken, cacheKey]);

  // 任务2: 进入页面时并行预取两个 tab 数据 + 用缓存即时渲染
  useEffect(() => {
    // 1. 用缓存即时填充 (避免 loading 闪烁)
    const cachedUser = mcpCache.getUser(cacheKey);
    const cachedSystem = mcpCache.getSystem(cacheKey);
    if (cachedUser) setUserConfigs(cachedUser);
    if (cachedSystem) setSystemConfigs(cachedSystem);

    // 2. 并行后台刷新两个 tab (silent=true, 不触发 loading)
    loadUserConfigs(true);
    loadSystemConfigs(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cacheKey]);

  // 任务2: tab 切换不再触发 fetch (数据已在进入页面时并行加载)
  // handleSwitchTab 仅切换 UI 状态, 不重新请求

  const handleSwitchTab = (t: Tab) => {
    setTab(t);
    setEditing(null);
  };

  const handleNew = () => {
    setIsNew(true);
    setEditing({
      name: "",
      transport_type: "stdio",
      command: "",
      args: [],
      env_vars: {},
      enabled: true,
      description: "",
    });
  };

  const handleEdit = (config: McpConfig) => {
    setIsNew(false);
    setEditing({ ...config });
  };

  const handleCancelEdit = () => {
    setEditing(null);
    setIsNew(false);
  };

  const handleSave = async (config: McpConfig) => {
    setError(null);
    try {
      const token = getToken();
      if (isNew || !config.id) {
        const resp = await apiClient.saveMcpConfig(config, token);
        if (resp.test_result && typeof resp.id === "number") {
          setTestResults((prev) => ({ ...prev, [resp.id as number]: resp.test_result! }));
        }
      } else {
        // 编辑已存在配置 (启用切换时跳过测试, 由后端强制测试)
        await apiClient.updateMcpConfig(config.id, config, token);
      }
      setEditing(null);
      setIsNew(false);
      // P0-10: 保存成功后失效缓存, 强制下次 fetch 拉取最新数据
      mcpCache.invalidateUser(cacheKey);
      await loadUserConfigs();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("确定删除该 MCP 配置?")) return;
    setError(null);
    try {
      const token = getToken();
      await apiClient.deleteMcpConfig(id, token);
      // P0-10: 删除成功后失效缓存, loadUserConfigs 会重新写入最新数据
      mcpCache.invalidateUser(cacheKey);
      await loadUserConfigs();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    }
  };

  const handleToggleEnabled = async (config: McpConfig) => {
    setError(null);
    try {
      const token = getToken();
      const newEnabled = !config.enabled;
      // 启用时后端会强制测试, 只有可用的服务才能启用
      await apiClient.updateMcpConfig(config.id!, {
        ...config,
        enabled: newEnabled,
      }, token);
      await loadUserConfigs();
    } catch (err) {
      setError(err instanceof Error ? err.message : "切换启用状态失败");
    }
  };

  const handleTestExisting = async (config: McpConfig) => {
    if (!config.id) return;
    setTesting(config.id);
    setError(null);
    try {
      const token = getToken();
      const result = (await apiClient.testExistingMcpConfig(config.id, token)) as McpTestResult;
      setTestResults((prev) => ({ ...prev, [config.id!]: result }));
    } catch (err) {
      setTestResults((prev) => ({
        ...prev,
        [config.id!]: {
          success: false,
          message: err instanceof Error ? err.message : "测试失败",
          error_type: "unknown",
        },
      }));
    } finally {
      setTesting(null);
    }
  };

  const handleClone = async (config: McpConfig) => {
    if (!config.id) return;
    setError(null);
    try {
      const token = getToken();
      await apiClient.cloneSystemMcpConfig(config.id, token);
      // 切换到用户配置 tab 并刷新
      setTab("user");
      await loadUserConfigs();
    } catch (err) {
      setError(err instanceof Error ? err.message : "克隆失败");
    }
  };

  // ========== 编辑模式: 只显示编辑器 ==========
  if (editing) {
    return (
      <McpConfigEditor
        config={editing}
        isNew={isNew}
        onSave={handleSave}
        onCancel={handleCancelEdit}
      />
    );
  }

  // ========== 列表模式 ==========
  return (
    <div className="flex h-full">
      {/* ===== 左侧: 竖向 Tab (Linear 风格) ===== */}
      <div
        className="flex-none w-28 flex flex-col gap-1 p-2"
        style={{
          backgroundColor: "var(--bg-card)",
          borderRight: "1px solid var(--border-color-light)",
        }}
      >
        <button
          onClick={() => handleSwitchTab("user")}
          className="w-full flex items-center justify-between px-3 py-2 text-sm font-medium rounded-md transition-all"
          style={{
            backgroundColor: tab === "user" ? "var(--bg-active)" : "transparent",
            color: tab === "user" ? "var(--brand-primary)" : "var(--text-secondary)",
          }}
        >
          <span>我的</span>
          {userConfigs.length > 0 && (
            <span
              className="text-xs px-1.5 py-0.5 rounded-full"
              style={{
                backgroundColor: tab === "user" ? "var(--brand-primary-light)" : "var(--bg-muted)",
                color: tab === "user" ? "var(--brand-primary)" : "var(--text-tertiary)",
              }}
            >
              {userConfigs.length}
            </span>
          )}
        </button>
        <button
          onClick={() => handleSwitchTab("system")}
          className="w-full flex items-center justify-between px-3 py-2 text-sm font-medium rounded-md transition-all"
          style={{
            backgroundColor: tab === "system" ? "var(--bg-active)" : "transparent",
            color: tab === "system" ? "var(--brand-primary)" : "var(--text-secondary)",
          }}
        >
          <span>仓库</span>
          {systemConfigs.length > 0 && (
            <span
              className="text-xs px-1.5 py-0.5 rounded-full"
              style={{
                backgroundColor: tab === "system" ? "var(--brand-primary-light)" : "var(--bg-muted)",
                color: tab === "system" ? "var(--brand-primary)" : "var(--text-tertiary)",
              }}
            >
              {systemConfigs.length}
            </span>
          )}
        </button>
      </div>

      {/* ===== 右侧: 内容区 (flex-1 自适应) ===== */}
      <div className="flex-1 overflow-y-auto p-6 min-h-0">
        {/* 页面标题区 */}
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2
              className="text-lg font-semibold flex items-center gap-2"
              style={{ color: "var(--text-primary)" }}
            >
              <Blocks
                className="h-5 w-5"
                style={{ color: "var(--brand-primary)" }}
              />
              MCP 服务配置
            </h2>
            <p
              className="text-xs mt-1"
              style={{ color: "var(--text-tertiary)" }}
            >
              管理 Model Context Protocol 工具服务器, 让智能体调用外部能力
            </p>
          </div>
          {tab === "user" && (
            <button
              onClick={handleNew}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm rounded-md transition-all hover:shadow-md"
              style={{
                backgroundColor: "var(--brand-primary)",
                color: "var(--text-on-brand)",
                boxShadow: "var(--shadow-sm)",
              }}
            >
              <Plus className="h-4 w-4" />
              创建 MCP 服务
            </button>
          )}
        </div>

        {/* 错误提示 (使用语义色变量) */}
        {error && (
          <div
            className="flex items-start gap-2 px-3 py-2.5 text-sm rounded-lg mb-4"
            style={{
              color: "var(--color-danger)",
              backgroundColor: "var(--color-danger-bg)",
              border: "1px solid var(--color-danger-border)",
            }}
          >
            <XCircle className="h-4 w-4 flex-shrink-0 mt-0.5" />
            <span className="flex-1">{error}</span>
            <button
              onClick={() => setError(null)}
              className="opacity-60 hover:opacity-100 transition-opacity"
              style={{ color: "var(--color-danger)" }}
              aria-label="关闭提示"
            >
              ✕
            </button>
          </div>
        )}

        {/* 加载中 */}
        {loading && (
          <div
            className="flex items-center gap-2 text-sm py-12 justify-center"
            style={{ color: "var(--text-tertiary)" }}
          >
            <Loader2 className="h-4 w-4 animate-spin" />
            加载中...
          </div>
        )}

        {/* 列表内容 */}
        {!loading && (
          <>
            {tab === "user" ? (
              userConfigs.length === 0 ? (
                <EmptyState
                  icon={<Blocks className="h-8 w-8" />}
                  title="暂无 MCP 配置"
                  description="MCP (Model Context Protocol) 让智能体调用外部工具。点击右上角按钮创建第一个 MCP 服务。"
                  action={
                    <button
                      onClick={handleNew}
                      className="inline-flex items-center gap-1.5 px-3 py-2 text-sm rounded-md transition-all hover:shadow-md"
                      style={{
                        backgroundColor: "var(--brand-primary)",
                        color: "var(--text-on-brand)",
                      }}
                    >
                      <Plus className="h-4 w-4" />
                      创建 MCP 服务
                    </button>
                  }
                />
              ) : (
                <div className="grid gap-3 sm:grid-cols-1 lg:grid-cols-2">
                  {userConfigs.map((c) => (
                    <McpConfigCard
                      key={c.id}
                      config={c}
                      testResult={c.id ? testResults[c.id] : undefined}
                      testing={testing === c.id}
                      onEdit={() => handleEdit(c)}
                      onDelete={() => c.id && handleDelete(c.id)}
                      onToggleEnabled={() => handleToggleEnabled(c)}
                      onTest={() => handleTestExisting(c)}
                    />
                  ))}
                </div>
              )
            ) : systemConfigs.length === 0 ? (
              <EmptyState
                icon={<Globe className="h-8 w-8" />}
                title="暂无系统 MCP"
                description="系统 MCP 由管理员配置, 全局共享给所有用户。"
              />
            ) : (
              <div className="grid gap-3 sm:grid-cols-1 lg:grid-cols-2">
                {systemConfigs.map((c) => (
                  <SystemMcpCard key={c.id} config={c} onClone={() => handleClone(c)} />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ============================================================================
// 子组件: MCP 配置卡片 (用户私有, 参考 Linear/Stripe 卡片设计)
// ============================================================================

function McpConfigCard({
  config,
  testResult,
  testing,
  onEdit,
  onDelete,
  onToggleEnabled,
  onTest,
}: {
  config: McpConfig;
  testResult?: McpTestResult;
  testing: boolean;
  onEdit: () => void;
  onDelete: () => void;
  onToggleEnabled: () => void;
  onTest: () => void;
}) {
  const isStdio = config.transport_type === "stdio";
  const isHttp = !isStdio;
  const transportIcon = isStdio ? Terminal : Globe;

  return (
    <div
      className="relative border rounded-lg overflow-hidden transition-all hover:shadow-md group"
      style={{
        backgroundColor: "var(--bg-card)",
        borderColor: "var(--border-color)",
      }}
    >
      {/* 左侧状态指示条 (启用=主色, 禁用=灰色) */}
      <div
        className="absolute left-0 top-0 bottom-0 w-1"
        style={{
          backgroundColor: config.enabled
            ? "var(--color-success)"
            : "var(--border-color)",
        }}
      />

      <div className="p-4 pl-5">
        {/* 头部: 图标 + 名称 + 徽章 */}
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="flex items-center gap-2.5 min-w-0 flex-1">
            {/* 服务器图标 (带背景色的圆角容器) */}
            <div
              className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0"
              style={{
                backgroundColor: config.enabled
                  ? "var(--brand-primary-light)"
                  : "var(--bg-muted)",
                color: config.enabled
                  ? "var(--brand-primary)"
                  : "var(--text-tertiary)",
              }}
            >
              {(() => {
                const Icon = transportIcon;
                return <Icon className="h-4 w-4" />;
              })()}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span
                  className="font-medium text-sm truncate"
                  style={{ color: "var(--text-primary)" }}
                >
                  {config.name}
                </span>
                <EnabledBadge enabled={config.enabled} />
              </div>
              <div className="flex items-center gap-1.5 mt-0.5">
                <TransportBadge type={config.transport_type || "stdio"} />
                {config.description && (
                  <span
                    className="text-xs truncate"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    · {config.description}
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* 详情区: 命令或URL */}
        <div
          className="text-xs space-y-1 mb-3 px-3 py-2 rounded-md font-mono"
          style={{ backgroundColor: "var(--bg-muted)" }}
        >
          {isStdio ? (
            config.command && (
              <div className="flex items-center gap-1.5">
                <Terminal
                  className="h-3 w-3 flex-shrink-0"
                  style={{ color: "var(--text-tertiary)" }}
                />
                <span
                  className="truncate"
                  style={{ color: "var(--text-secondary)" }}
                >
                  {config.command}
                  {(() => {
                    const args = normalizeArgs(config.args);
                    return args.length > 0 ? " " + args.join(" ") : null;
                  })()}
                </span>
              </div>
            )
          ) : (
            config.server_url && (
              <div className="flex items-center gap-1.5">
                <Globe
                  className="h-3 w-3 flex-shrink-0"
                  style={{ color: "var(--text-tertiary)" }}
                />
                <span
                  className="truncate"
                  style={{ color: "var(--text-secondary)" }}
                >
                  {config.server_url}
                </span>
              </div>
            )
          )}
        </div>

        {/* 测试结果 (如果有) */}
        {testResult && (
          <TestResultBanner result={testResult} />
        )}

        {/* 操作按钮栏 (底部, hover 时更明显) */}
        <div className="flex items-center gap-1 pt-3 border-t" style={{ borderColor: "var(--border-color-light)" }}>
          <Tooltip content="测试可用性">
            <button
              onClick={onTest}
              disabled={testing}
              className="inline-flex items-center gap-1 px-2.5 py-1 text-xs rounded-md transition-colors disabled:opacity-50"
              style={{
                color: "var(--text-secondary)",
              }}
            >
              {testing ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Play className="h-3.5 w-3.5" />
              )}
              测试
            </button>
          </Tooltip>
          <Tooltip content={config.enabled ? "禁用" : "启用"}>
            <button
              onClick={onToggleEnabled}
              className="inline-flex items-center gap-1 px-2.5 py-1 text-xs rounded-md transition-colors"
              style={{
                color: config.enabled
                  ? "var(--color-success)"
                  : "var(--text-secondary)",
              }}
            >
              <Power className="h-3.5 w-3.5" />
              {config.enabled ? "已启用" : "已禁用"}
            </button>
          </Tooltip>
          <div className="flex-1" />
          <Tooltip content="编辑">
            <button
              onClick={onEdit}
              className="p-1.5 rounded-md transition-colors hover:bg-hover"
              style={{ color: "var(--text-tertiary)" }}
              aria-label="编辑"
            >
              <Pencil className="h-3.5 w-3.5" />
            </button>
          </Tooltip>
          <Tooltip content="删除">
            <button
              onClick={onDelete}
              className="p-1.5 rounded-md transition-colors hover:bg-hover"
              style={{ color: "var(--text-tertiary)" }}
              aria-label="删除"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </Tooltip>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// 子组件: 系统 MCP 卡片 (只读 + 克隆)
// ============================================================================

function SystemMcpCard({
  config,
  onClone,
}: {
  config: McpConfig;
  onClone: () => void;
}) {
  const isStdio = config.transport_type === "stdio";

  return (
    <div
      className="relative border rounded-lg overflow-hidden transition-all hover:shadow-md group"
      style={{
        backgroundColor: "var(--bg-card)",
        borderColor: "var(--border-color)",
      }}
    >
      {/* 左侧指示条 (系统=蓝色) */}
      <div
        className="absolute left-0 top-0 bottom-0 w-1"
        style={{ backgroundColor: "var(--brand-primary)" }}
      />

      <div className="p-4 pl-5">
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="flex items-center gap-2.5 min-w-0 flex-1">
            <div
              className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0"
              style={{
                backgroundColor: "var(--brand-primary-light)",
                color: "var(--brand-primary)",
              }}
            >
              {isStdio ? (
                <Terminal className="h-4 w-4" />
              ) : (
                <Globe className="h-4 w-4" />
              )}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span
                  className="font-medium text-sm truncate"
                  style={{ color: "var(--text-primary)" }}
                >
                  {config.name}
                </span>
                <span
                  className="text-xs px-1.5 py-0.5 rounded"
                  style={{
                    backgroundColor: "var(--brand-primary-light)",
                    color: "var(--brand-primary)",
                  }}
                >
                  系统
                </span>
              </div>
              <div className="flex items-center gap-1.5 mt-0.5">
                <TransportBadge type={config.transport_type || "stdio"} />
                {config.description && (
                  <span
                    className="text-xs truncate"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    · {config.description}
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* 详情区 */}
        <div
          className="text-xs space-y-1 mb-3 px-3 py-2 rounded-md font-mono"
          style={{ backgroundColor: "var(--bg-muted)" }}
        >
          {isStdio ? (
            config.command && (
              <div className="flex items-center gap-1.5">
                <Terminal
                  className="h-3 w-3 flex-shrink-0"
                  style={{ color: "var(--text-tertiary)" }}
                />
                <span
                  className="truncate"
                  style={{ color: "var(--text-secondary)" }}
                >
                  {config.command}
                  {(() => {
                    const args = normalizeArgs(config.args);
                    return args.length > 0 ? " " + args.join(" ") : null;
                  })()}
                </span>
              </div>
            )
          ) : (
            config.server_url && (
              <div className="flex items-center gap-1.5">
                <Globe
                  className="h-3 w-3 flex-shrink-0"
                  style={{ color: "var(--text-tertiary)" }}
                />
                <span
                  className="truncate"
                  style={{ color: "var(--text-secondary)" }}
                >
                  {config.server_url}
                </span>
              </div>
            )
          )}
        </div>

        {/* 克隆按钮 */}
        <div className="pt-3 border-t" style={{ borderColor: "var(--border-color-light)" }}>
          <Tooltip content="添加到我的配置">
            <button
              onClick={onClone}
              className="inline-flex items-center gap-1 px-2.5 py-1 text-xs rounded-md transition-colors"
              style={{
                color: "var(--brand-primary)",
              }}
            >
              <Copy className="h-3.5 w-3.5" />
              添加到我的配置
            </button>
          </Tooltip>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// 子组件: MCP 配置编辑器 (全屏卡片式)
// ============================================================================

function McpConfigEditor({
  config,
  isNew,
  onSave,
  onCancel,
}: {
  config: McpConfig;
  isNew: boolean;
  onSave: (config: McpConfig) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(config.name);
  const [transportType, setTransportType] = useState<TransportType>(
    config.transport_type || "stdio"
  );
  const [serverUrl, setServerUrl] = useState(config.server_url || "");
  const [command, setCommand] = useState(config.command || "");
  const [argsText, setArgsText] = useState(normalizeArgs(config.args).join(" "));
  const [envVarsText, setEnvVarsText] = useState(
    Object.entries(normalizeEnvVars(config.env_vars))
      .map(([k, v]) => `${k}=${v}`)
      .join("\n")
  );
  const [description, setDescription] = useState(config.description || "");
  const [enabled, setEnabled] = useState(config.enabled);
  const [formError, setFormError] = useState<string | null>(null);

  const isStdio = transportType === "stdio";

  const handleSubmit = () => {
    setFormError(null);

    if (!name.trim()) {
      setFormError("请填写配置名称");
      return;
    }

    if (isStdio && !command.trim()) {
      setFormError("stdio 传输模式必须填写 command (启动命令)");
      return;
    }

    if (!isStdio && !serverUrl.trim()) {
      setFormError(`${transportType} 传输模式必须填写 server_url`);
      return;
    }

    // 解析 env_vars (KEY=VALUE 每行一个)
    const envVars: Record<string, string> = {};
    if (envVarsText.trim()) {
      for (const line of envVarsText.split("\n")) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        const eqIdx = trimmed.indexOf("=");
        if (eqIdx === -1) {
          setFormError(`环境变量格式错误: "${trimmed}" (应为 KEY=VALUE)`);
          return;
        }
        const k = trimmed.slice(0, eqIdx).trim();
        const v = trimmed.slice(eqIdx + 1).trim();
        if (k) envVars[k] = v;
      }
    }

    // 解析 args (空格分隔, 支持引号)
    const args = argsText.trim() ? argsText.trim().split(/\s+/) : [];

    onSave({
      ...config,
      name: name.trim(),
      transport_type: transportType,
      server_url: serverUrl.trim() || undefined,
      command: command.trim() || undefined,
      args: args.length > 0 ? args : undefined,
      env_vars: Object.keys(envVars).length > 0 ? envVars : undefined,
      description: description.trim() || undefined,
      enabled,
    });
  };

  return (
    <div className="space-y-5">
      {/* 编辑器头部: 返回按钮 + 标题 */}
      <div className="flex items-center gap-3">
        <Tooltip content="返回列表">
          <button
            onClick={onCancel}
            className="p-1.5 rounded-md transition-colors hover:bg-hover"
            style={{ color: "var(--text-secondary)" }}
            aria-label="返回列表"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
        </Tooltip>
        <div>
          <h2
            className="text-lg font-semibold flex items-center gap-2"
            style={{ color: "var(--text-primary)" }}
          >
            {isNew ? (
              <>
                <Plus
                  className="h-5 w-5"
                  style={{ color: "var(--brand-primary)" }}
                />
                新增 MCP 配置
              </>
            ) : (
              <>
                <Pencil
                  className="h-5 w-5"
                  style={{ color: "var(--brand-primary)" }}
                />
                编辑: {config.name}
              </>
            )}
          </h2>
          <p
            className="text-xs mt-1"
            style={{ color: "var(--text-tertiary)" }}
          >
            {isStdio
              ? "stdio 模式: 通过子进程启动本地 MCP 服务器"
              : `${transportType} 模式: 连接远程 MCP 服务器`}
          </p>
        </div>
      </div>

      {/* 表单卡片 */}
      <div
        className="border rounded-lg p-6 space-y-5"
        style={{
          backgroundColor: "var(--bg-card)",
          borderColor: "var(--border-color)",
        }}
      >
        {/* 名称 */}
        <FormField
          label="配置名称"
          required
          hint="用于识别此 MCP 服务的唯一名称"
        >
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例如: filesystem"
            className="w-full rounded-md border px-3 py-2 text-sm focus:outline-none transition-all"
            style={{
              borderColor: "var(--border-color)",
              backgroundColor: "var(--bg-card)",
              color: "var(--text-primary)",
            }}
            onFocus={(e) => {
              e.target.style.borderColor = "var(--brand-primary)";
              e.target.style.boxShadow = "0 0 0 3px var(--brand-primary-light)";
            }}
            onBlur={(e) => {
              e.target.style.borderColor = "var(--border-color)";
              e.target.style.boxShadow = "none";
            }}
          />
        </FormField>

        {/* 传输类型 (分段选择器) */}
        <FormField label="传输类型" hint="MCP 服务器与智能体的通信方式">
          <div className="flex gap-2">
            {(["stdio", "sse", "streamable_http"] as TransportType[]).map((t) => (
              <button
                key={t}
                onClick={() => setTransportType(t)}
                className="px-4 py-2 text-sm rounded-md border transition-all"
                style={
                  transportType === t
                    ? {
                        backgroundColor: "var(--brand-primary)",
                        color: "var(--text-on-brand)",
                        borderColor: "var(--brand-primary)",
                      }
                    : {
                        borderColor: "var(--border-color)",
                        color: "var(--text-secondary)",
                        backgroundColor: "transparent",
                      }
                }
              >
                {t === "stdio" ? "stdio (本地)" : t}
              </button>
            ))}
          </div>
        </FormField>

        {/* server_url (sse / streamable_http 必填) */}
        {!isStdio && (
          <FormField
            label="Server URL"
            required
            hint="MCP 服务器的完整 URL 地址"
          >
            <input
              value={serverUrl}
              onChange={(e) => setServerUrl(e.target.value)}
              placeholder="https://example.com/mcp/sse"
              className="w-full rounded-md border px-3 py-2 text-sm font-mono focus:outline-none transition-all"
              style={{
                borderColor: "var(--border-color)",
                backgroundColor: "var(--bg-card)",
                color: "var(--text-primary)",
              }}
              onFocus={(e) => {
                e.target.style.borderColor = "var(--brand-primary)";
                e.target.style.boxShadow = "0 0 0 3px var(--brand-primary-light)";
              }}
              onBlur={(e) => {
                e.target.style.borderColor = "var(--border-color)";
                e.target.style.boxShadow = "none";
              }}
            />
          </FormField>
        )}

        {/* command (stdio 必填) */}
        {isStdio && (
          <FormField
            label="启动命令"
            required
            hint="MCP 服务器的可执行文件路径"
          >
            <input
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              placeholder="例如: npx 或 python"
              className="w-full rounded-md border px-3 py-2 text-sm font-mono focus:outline-none transition-all"
              style={{
                borderColor: "var(--border-color)",
                backgroundColor: "var(--bg-card)",
                color: "var(--text-primary)",
              }}
              onFocus={(e) => {
                e.target.style.borderColor = "var(--brand-primary)";
                e.target.style.boxShadow = "0 0 0 3px var(--brand-primary-light)";
              }}
              onBlur={(e) => {
                e.target.style.borderColor = "var(--border-color)";
                e.target.style.boxShadow = "none";
              }}
            />
          </FormField>
        )}

        {/* args (stdio) */}
        {isStdio && (
          <FormField
            label="命令参数"
            hint="空格分隔, 支持引号包裹含空格的参数"
          >
            <input
              value={argsText}
              onChange={(e) => setArgsText(e.target.value)}
              placeholder="例如: -y @modelcontextprotocol/server-filesystem /tmp"
              className="w-full rounded-md border px-3 py-2 text-sm font-mono focus:outline-none transition-all"
              style={{
                borderColor: "var(--border-color)",
                backgroundColor: "var(--bg-card)",
                color: "var(--text-primary)",
              }}
              onFocus={(e) => {
                e.target.style.borderColor = "var(--brand-primary)";
                e.target.style.boxShadow = "0 0 0 3px var(--brand-primary-light)";
              }}
              onBlur={(e) => {
                e.target.style.borderColor = "var(--border-color)";
                e.target.style.boxShadow = "none";
              }}
            />
          </FormField>
        )}

        {/* env_vars (stdio) */}
        {isStdio && (
          <FormField
            label="环境变量"
            hint="每行一个, 格式 KEY=VALUE"
          >
            <textarea
              value={envVarsText}
              onChange={(e) => setEnvVarsText(e.target.value)}
              placeholder={"API_KEY=your-key\nANOTHER_VAR=value"}
              rows={3}
              className="w-full rounded-md border px-3 py-2 text-sm font-mono focus:outline-none transition-all resize-y"
              style={{
                borderColor: "var(--border-color)",
                backgroundColor: "var(--bg-card)",
                color: "var(--text-primary)",
              }}
              onFocus={(e) => {
                e.target.style.borderColor = "var(--brand-primary)";
                e.target.style.boxShadow = "0 0 0 3px var(--brand-primary-light)";
              }}
              onBlur={(e) => {
                e.target.style.borderColor = "var(--border-color)";
                e.target.style.boxShadow = "none";
              }}
            />
          </FormField>
        )}

        {/* description */}
        <FormField label="描述" hint="可选, 此配置的用途说明">
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="配置用途说明"
            className="w-full rounded-md border px-3 py-2 text-sm focus:outline-none transition-all"
            style={{
              borderColor: "var(--border-color)",
              backgroundColor: "var(--bg-card)",
              color: "var(--text-primary)",
            }}
            onFocus={(e) => {
              e.target.style.borderColor = "var(--brand-primary)";
              e.target.style.boxShadow = "0 0 0 3px var(--brand-primary-light)";
            }}
            onBlur={(e) => {
              e.target.style.borderColor = "var(--border-color)";
              e.target.style.boxShadow = "none";
            }}
          />
        </FormField>

        {/* enabled 开关 (自定义样式) */}
        <div
          className="flex items-center justify-between p-3 rounded-md"
          style={{ backgroundColor: "var(--bg-muted)" }}
        >
          <div>
            <div
              className="text-sm font-medium"
              style={{ color: "var(--text-primary)" }}
            >
              启用此配置
            </div>
            <div
              className="text-xs mt-0.5"
              style={{ color: "var(--text-tertiary)" }}
            >
              启用时后端会自动测试可用性, 不可用的服务无法启用
            </div>
          </div>
          <button
            onClick={() => setEnabled(!enabled)}
            className="relative inline-flex h-6 w-11 items-center rounded-full transition-colors"
            style={{
              backgroundColor: enabled
                ? "var(--brand-primary)"
                : "var(--border-color)",
            }}
            role="switch"
            aria-checked={enabled}
            aria-label="启用配置"
          >
            <span
              className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                enabled ? "translate-x-6" : "translate-x-1"
              }`}
            />
          </button>
        </div>

        {/* 表单错误 */}
        {formError && (
          <div
            className="flex items-center gap-2 px-3 py-2 text-sm rounded-md"
            style={{
              color: "var(--color-danger)",
              backgroundColor: "var(--color-danger-bg)",
              border: "1px solid var(--color-danger-border)",
            }}
          >
            <XCircle className="h-4 w-4 flex-shrink-0" />
            {formError}
          </div>
        )}
      </div>

      {/* 操作按钮 (底部固定) */}
      <div className="flex gap-2 justify-end">
        <button
          onClick={onCancel}
          className="px-4 py-2 text-sm rounded-md border transition-colors hover:bg-hover"
          style={{
            borderColor: "var(--border-color)",
            color: "var(--text-secondary)",
            backgroundColor: "var(--bg-card)",
          }}
        >
          取消
        </button>
        <button
          onClick={handleSubmit}
          className="px-4 py-2 text-sm rounded-md transition-all hover:shadow-md"
          style={{
            backgroundColor: "var(--brand-primary)",
            color: "var(--text-on-brand)",
            boxShadow: "var(--shadow-sm)",
          }}
        >
          {isNew ? "创建" : "保存"}
        </button>
      </div>
    </div>
  );
}

// ============================================================================
// 子组件: 表单字段容器 (统一标签 + 提示 + 输入框布局)
// ============================================================================

function FormField({
  label,
  required,
  hint,
  children,
}: {
  label: string;
  required?: boolean;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-sm font-medium mb-1.5">
        <span style={{ color: "var(--text-primary)" }}>{label}</span>
        {required && (
          <span
            className="ml-0.5"
            style={{ color: "var(--color-danger)" }}
          >
            *
          </span>
        )}
      </label>
      {children}
      {hint && (
        <p
          className="text-xs mt-1"
          style={{ color: "var(--text-tertiary)" }}
        >
          {hint}
        </p>
      )}
    </div>
  );
}

// ============================================================================
// 子组件: 测试结果横幅 (使用语义色变量)
// ============================================================================

function TestResultBanner({ result }: { result: McpTestResult }) {
  const isSuccess = result.success;
  return (
    <div
      className="mb-3 px-3 py-2.5 rounded-md text-xs flex items-start gap-2"
      style={{
        backgroundColor: isSuccess
          ? "var(--color-success-bg)"
          : "var(--color-danger-bg)",
        border: `1px solid ${
          isSuccess
            ? "var(--color-success-border)"
            : "var(--color-danger-border)"
        }`,
        color: isSuccess
          ? "var(--color-success)"
          : "var(--color-danger)",
      }}
    >
      {isSuccess ? (
        <CheckCircle className="h-4 w-4 flex-shrink-0 mt-0.5" />
      ) : (
        <XCircle className="h-4 w-4 flex-shrink-0 mt-0.5" />
      )}
      <div className="flex-1 min-w-0">
        <div className="font-medium">
          {isSuccess ? "测试通过" : "测试失败"}
        </div>
        <div className="opacity-90 mt-0.5">{result.message}</div>
        {isSuccess && (
          <div
            className="mt-1 flex items-center gap-3"
            style={{ color: "var(--text-tertiary)" }}
          >
            {result.tools_count !== undefined && (
              <span>工具数: {result.tools_count}</span>
            )}
            {result.latency_ms !== undefined && (
              <span>耗时: {result.latency_ms}ms</span>
            )}
          </div>
        )}
        {isSuccess && result.tools && result.tools.length > 0 && (
          <div
            className="mt-1 truncate"
            style={{ color: "var(--text-tertiary)" }}
          >
            工具: {result.tools.join(", ")}
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================================
// 子组件: 空状态 (增强版, 支持操作按钮)
// ============================================================================

function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="text-center py-16 fade-in">
      <div
        className="inline-flex items-center justify-center w-16 h-16 rounded-2xl mb-4"
        style={{
          backgroundColor: "var(--brand-primary-light)",
          color: "var(--brand-primary)",
        }}
      >
        {icon}
      </div>
      <h3
        className="text-base font-medium mb-2"
        style={{ color: "var(--text-primary)" }}
      >
        {title}
      </h3>
      <p
        className="text-sm max-w-md mx-auto"
        style={{ color: "var(--text-tertiary)" }}
      >
        {description}
      </p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

// ============================================================================
// 子组件: 传输类型徽章
// ============================================================================

function TransportBadge({ type }: { type: TransportType }) {
  const labels: Record<TransportType, string> = {
    stdio: "stdio",
    sse: "sse",
    streamable_http: "streamable_http",
  };
  return (
    <span
      className="text-xs px-1.5 py-0.5 rounded font-mono"
      style={{
        backgroundColor: "var(--bg-hover)",
        color: "var(--text-secondary)",
      }}
    >
      {labels[type]}
    </span>
  );
}

// ============================================================================
// 子组件: 启用状态徽章 (使用语义色)
// ============================================================================

function EnabledBadge({ enabled }: { enabled: boolean }) {
  return (
    <span
      className="text-xs px-1.5 py-0.5 rounded inline-flex items-center gap-1"
      style={
        enabled
          ? {
              backgroundColor: "var(--color-success-bg)",
              color: "var(--color-success)",
            }
          : {
              backgroundColor: "var(--bg-muted)",
              color: "var(--text-tertiary)",
            }
      }
    >
      <span
        className="w-1.5 h-1.5 rounded-full"
        style={{
          backgroundColor: enabled
            ? "var(--color-success)"
            : "var(--text-tertiary)",
        }}
      />
      {enabled ? "启用" : "禁用"}
    </span>
  );
}
