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

# 无 NEXT_PUBLIC_ 构建参数 (所有变量运行时注入, 不内联)
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
