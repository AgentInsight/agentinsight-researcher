# frontend/Dockerfile
# 多 Agent 预留, 当前单 Agent (agentinsight-researcher)

# ---- builder 阶段 ----
FROM node:20-alpine AS builder

WORKDIR /app

# 先复制 package.json 和 lockfile, 利用 Docker 层缓存
COPY package.json package-lock.json* ./
RUN npm ci

# 复制源代码并构建
COPY . .

# NEXT_PUBLIC_ 构建参数 (Next.js 构建时内联到客户端 bundle, 运行时修改无效)
# - NEXT_PUBLIC_AUTH_API_BASE: 认证 API 地址 (浏览器直连), 默认 https://agentinsight.goldebridge.com
# - NEXT_PUBLIC_DEPLOYMENT_MODE: Agent API 部署模式 (server|local), 默认 server
#   server: 通过 Nginx 按 agentName 路径分发 (/agent/{agentName}/*)
#   local: 浏览器直连后端端口 (http://localhost:{port}/*, 无 Nginx)
# 本地 Docker 构建时传入: --build-arg NEXT_PUBLIC_DEPLOYMENT_MODE=local
# 不传 ARG 时, 代码 fallback 到默认值 (server + https://agentinsight.goldebridge.com)
ARG NEXT_PUBLIC_AUTH_API_BASE
ARG NEXT_PUBLIC_DEPLOYMENT_MODE
# 确保 .bin 执行权限 (某些环境下 npm ci 后 .bin 权限丢失)
RUN chmod -R +x node_modules/.bin && npm run build

# ---- runtime 阶段 ----
FROM node:20-alpine AS runtime

WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# standalone 产物已包含最小化 node_modules
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000

# standalone 模式下入口为 server.js
CMD ["node", "server.js"]
