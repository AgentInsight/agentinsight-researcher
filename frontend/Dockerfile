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

# 构建时需要 NEXT_PUBLIC_ 环境变量 (嵌入到客户端产物)
# 这些变量在运行时不可修改, 必须构建时注入
ARG NEXT_PUBLIC_SELF_HOST=true
ARG NEXT_PUBLIC_AGENT_RESEARCHER_API_URL=http://localhost:8066
ENV NEXT_PUBLIC_SELF_HOST=${NEXT_PUBLIC_SELF_HOST}
ENV NEXT_PUBLIC_AGENT_RESEARCHER_API_URL=${NEXT_PUBLIC_AGENT_RESEARCHER_API_URL}

RUN npm run build

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
