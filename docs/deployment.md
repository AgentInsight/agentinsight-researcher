# 部署指南 | Deployment Guide

[中文](#中文) | [English](#english)

---

## 中文

## 部署前置要求

### 系统要求

| 项目 | 最低要求 | 推荐 |
|------|---------|------|
| **操作系统** | Windows / macOS / Linux | Linux（Ubuntu 22.04+） |
| **Docker** | ≥24.0 | 最新稳定版 |
| **Docker Compose** | ≥2.20 | 最新稳定版 |
| **Python** | ≥3.11（仅本地开发需要） | 3.12 |
| **内存** | 4 GB | 8 GB |
| **磁盘** | 10 GB（含模型缓存） | 20 GB |
| **CPU** | 2 核 | 4 核+ |

### 资源需求说明

- **Embeddings 容器**：加载 bge-base-zh-v1.5 模型，需 ≥1 GB 内存
- **Rerank 容器**（可选）：加载 bge-reranker-v2-m3 模型，需 ≥1 GB 内存
- **Agent 容器**：FastEmbed 本地模型 + Python 运行时，需 ≥1 GB 内存
- **PostgreSQL/Redis/Qdrant/SearXNG**：合计约 1 GB 内存

### 必要凭据

部署前请准备以下凭据（详见 [README.md](../README.md) 配置说明）：

1. **AgentInsight API Key**：[平台注册](https://agentinsight.goldebridge.com/platform)获取 PublicKey + SecretKey
2. **LLM API Key**：至少一个（推荐 DeepSeek + 智谱）
3. **搜索引擎 API Key**：至少一个（推荐博查 Bocha）
4. **数据库密码**：自定义 PostgreSQL 密码 + Qdrant API Key

---

## 三套构建模式说明

项目提供三套构建文件，按部署场景选择：

| 模式 | 构建文件 | 编排文件 | 环境文件 | 构建脚本 | 适用场景 |
|------|---------|---------|---------|---------|---------|
| QA 模式（离线） | `Dockerfile.qa` | `docker-compose-qa.yaml` | `.env.qa` | `docker-build.qa.bat` | QA 测试、内网环境 |
| 生产模式（联网） | `Dockerfile` | `docker-compose.yml` | `.env` | `docker-build.sh` | 开源社区、CI、外网环境 |
| 生产模式（离线） | `Dockerfile.offline` | `docker-compose-offline.yaml` | `.env` | `docker-build.offline.sh` | 内网生产环境、离线部署 |

### QA 模式（离线）

- 所有依赖预下载到 `packages/`（wheels/debs/models/images）
- 构建时 `pip install --no-index` 离线安装
- 部署时 `docker load` 加载镜像 tarball
- 所有端口绑定 `127.0.0.1`，仅本机访问
- 适用于 QA 测试

### 生产模式（联网）

- 构建时从 PyPI 下载 Python 依赖
- 从 Docker Hub 拉取基础镜像（含 postgres 容器）
- 无需预下载 `packages/`
- 仅 `agent:8066`/`rerank:8089`/`embeddings:8088`/`qdrant:6333` 对外暴露
- 适用于开源社区贡献者快速起栈

### 生产模式（离线）

- 所有依赖预下载到 `packages/`
- 构建时 `pip install --no-index` 离线安装
- 部署时 `docker load` 加载镜像 tarball
- 模型从本地 volume 加载
- 适用于内网生产环境或离线部署

---

## 快速开始（生产联网模式）

### 第 1 步：克隆项目

```bash
git clone <仓库地址>
cd agentinsight-researcher
```

### 第 2 步：配置环境变量

```bash
copy .env.template .env
```

编辑 `.env`，填入必填项：

```env
# AgentInsight 可观测性密钥（必填）
AGENTINSIGHT_PUBLIC_KEY=pk-你的PublicKey
AGENTINSIGHT_SECRET_KEY=sk-你的SecretKey
AGENTINSIGHT_HOST=https://agentinsight.goldebridge.com

# LLM API Key（至少配置一个）
DEEPSEEK_API_KEY=sk-你的DeepSeek密钥
ZHIPU_API_KEY=你的智谱密钥

# 搜索引擎 API Key（至少配置一个）
BOCHA_API_KEY=sk-你的博查密钥

# 数据库密码（生产环境必填）
POSTGRES_PASSWORD=你的Postgres密码
REDIS_AUTH=你的Redis密码

# Qdrant 静态 API Key（生产环境必填）
QDRANT_API_KEY=sk-你的Qdrant密钥
```

### 第 3 步：启动容器栈

```bash
# 使用构建脚本（推荐，内置 -p agentinsight 项目名）
bash docker-build.sh

# 或手动启动
docker compose -p agentinsight up -d --build
```

### 第 4 步：等待全部健康

```bash
docker compose -p agentinsight ps
# 全部显示 (healthy) 即可
```

首次启动时 Embeddings 容器需下载模型，`start_period: 180s`，请耐心等待。

### 第 5 步：访问测试页面

浏览器打开 `http://localhost:8066`，即可体验研究报告生成。

### 第 6 步：验证健康检查

```bash
curl http://localhost:8066/health
# 返回 {"status":"ok","service":"agentinsight-researcher","version":"1.1.0"}
```

---

## 端口规划表

| 端口 | 服务 | 绑定方式 | 对外暴露 | 用途 |
|------|------|---------|---------|------|
| 8066 | agent | 0.0.0.0 | ✅ | API 入口 + 测试页面 |
| 8088 | embeddings | 0.0.0.0 | ✅ | TEI Embeddings 服务 |
| 8089 | rerank（可选） | 0.0.0.0 | ✅ | TEI Rerank 服务 |
| 6333 | qdrant HTTP | 0.0.0.0 | ✅ | Qdrant 向量库 HTTP API |
| 6334 | qdrant gRPC | 127.0.0.1 | ❌ | Qdrant gRPC（仅内部） |
| 5432 | postgres | 127.0.0.1 | ❌ | PostgreSQL（仅内部） |
| 6379 | redis | 127.0.0.1 | ❌ | Redis（仅内部） |
| 8099 | searxng | 127.0.0.1 | ❌ | SearXNG 元搜索（仅内部） |

> QA 模式下所有端口绑定 `127.0.0.1`，仅本机访问。

---

## 环境变量配置说明

### 必填配置

| 配置项 | 说明 | 获取方式 |
|--------|------|---------|
| `AGENTINSIGHT_PUBLIC_KEY` | AgentInsight PublicKey | [平台注册](https://agentinsight.goldebridge.com/platform) |
| `AGENTINSIGHT_SECRET_KEY` | AgentInsight SecretKey | 同上 |
| `DEEPSEEK_API_KEY` | DeepSeek API Key | [DeepSeek 平台](https://platform.deepseek.com/) |
| `BOCHA_API_KEY` | 博查搜索 API Key | [博查搜索](https://bochaai.com/) |
| `POSTGRES_PASSWORD` | PostgreSQL 密码 | 自定义 |
| `QDRANT_API_KEY` | Qdrant 静态 API Key | 自定义 |

### 关键可选配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `ENV` | `dev` | `dev`/`prod`（prod 关闭 docs/openapi） |
| `SELF_HOST` | `True` | `True`=自托管 / `False`=云托管 |
| `ENABLE_TEST_PAGE` | `dev=true / prod=false` | 是否挂载测试页面 |
| `FAST_LLM` | `zhipuai/glm-4-flash` | 快速响应 LLM |
| `SMART_LLM` | `deepseek/deepseek-v4-flash` | 智能分析 LLM |
| `STRATEGIC_LLM` | `deepseek/deepseek-v4-pro` | 战略决策 LLM |
| `RERANK_ENABLED` | `false` | 是否启用 Rerank |
| `WEBSOCKET_ENABLED` | `False` | 是否启用 WebSocket |
| `HUMAN_REVIEW_ENABLED` | `False` | 是否启用人在回路审核 |
| `MAX_UPLOAD_SIZE_MB` | `50` | 文件上传大小上限 |
| `CORS_ALLOW_ORIGINS` | `http://localhost:8066` | CORS 白名单 |

完整配置项见 `.env.template`。

---

## 健康检查验证

### 容器级健康检查

```bash
# 查看所有容器健康状态
docker compose -p agentinsight ps

# 预期输出：所有服务显示 (healthy)
# NAME                          STATUS                   PORTS
# agentinsight-agent-1          Up 2 minutes (healthy)   0.0.0.0:8066->8066/tcp
# agentinsight-postgres-1       Up 3 minutes (healthy)   127.0.0.1:5432->5432/tcp
# agentinsight-redis-1          Up 3 minutes (healthy)   127.0.0.1:6379->6379/tcp
# agentinsight-qdrant-1         Up 3 minutes (healthy)   0.0.0.0:6333->6333/tcp
# agentinsight-embeddings-1     Up 3 minutes (healthy)   0.0.0.0:8088->8088/tcp
# agentinsight-searxng-1        Up 3 minutes (healthy)   127.0.0.1:8099->8099/tcp
```

### API 级健康检查

```bash
# Agent 健康检查
curl http://localhost:8066/health
# {"status":"ok","service":"agentinsight-researcher","version":"1.1.0"}

# Agent Discovery
curl http://localhost:8066/.well-known/agent-discovery.json

# 模型列表
curl http://localhost:8066/v1/models
```

### 各服务健康检查端点

| 服务 | 健康检查方式 |
|------|------------|
| agent | `GET /health` |
| postgres | `pg_isready -U <user>` |
| redis | `redis-cli ping` |
| qdrant | `GET /healthz`（端口 6333） |
| embeddings | `GET /health`（端口 8088） |
| rerank | `GET /health`（端口 8089） |
| searxng | `wget http://127.0.0.1:8099/` |

---

## 故障排查

### 1. 容器启动失败

```bash
# 查看失败容器日志
docker compose -p agentinsight logs <service_name> --tail 200

# 常见原因：
# - .env 必填项为空
# - 端口冲突
# - 内存不足
# - 依赖服务未就绪
```

### 2. Embeddings 容器不健康

```bash
# 查看模型下载进度
docker compose -p agentinsight logs -f embeddings

# 首次启动需下载 bge-base-zh-v1.5 模型（约 400MB），start_period 为 180s
# 如超时，可延长 start_period 或预下载模型
```

### 3. Agent 容器不健康

```bash
# 查看 agent 启动日志
docker compose -p agentinsight logs agent --tail 200

# 常见原因：
# - PostgreSQL 业务表初始化失败（检查 POSTGRES_PASSWORD）
# - AgentInsight SDK 初始化失败（降级为 NoopSpan，不阻断）
# - Python 依赖导入失败（检查镜像构建）
```

### 4. 数据卷清理

```bash
# 停止并清理数据卷（慎用，会丢失所有数据）
docker compose -p agentinsight down -v

# 仅停止容器（保留数据）
docker compose -p agentinsight down
```

### 5. 查看资源使用

```bash
# 查看容器资源使用
docker stats $(docker compose -p agentinsight ps -q)

# 查看磁盘使用
docker system df
```

---

## 升级流程

### 1. 备份数据

```bash
# 备份 PostgreSQL
docker exec agentinsight-postgres-1 pg_dump -U <user> agents > backup_$(date +%Y%m%d).sql

# 备份 Qdrant（快照）
curl -X POST http://localhost:6333/snapshots
```

### 2. 拉取最新代码

```bash
git pull origin main
```

### 3. 重新构建并启动

```bash
# 使用构建脚本
bash docker-build.sh

# 或手动重建
docker compose -p agentinsight up -d --build
```

### 4. 验证升级

```bash
# 检查版本号
curl http://localhost:8066/health | grep version

# 检查所有服务健康
docker compose -p agentinsight ps
```

### 5. 回滚（如升级失败）

```bash
# 回退代码版本
git checkout <previous-tag>

# 重新构建
docker compose -p agentinsight up -d --build

# 恢复数据库（如需）
docker exec -i agentinsight-postgres-1 psql -U <user> agents < backup_YYYYMMDD.sql
```

---

## English

## Prerequisites

### System Requirements

| Item | Minimum | Recommended |
|------|---------|-------------|
| **OS** | Windows / macOS / Linux | Linux (Ubuntu 22.04+) |
| **Docker** | ≥24.0 | Latest stable |
| **Docker Compose** | ≥2.20 | Latest stable |
| **Python** | ≥3.11 (local dev only) | 3.12 |
| **Memory** | 4 GB | 8 GB |
| **Disk** | 10 GB (including model cache) | 20 GB |
| **CPU** | 2 cores | 4 cores+ |

### Resource Requirements

- **Embeddings container**: Loads bge-base-zh-v1.5 model, needs ≥1 GB memory
- **Rerank container** (optional): Loads bge-reranker-v2-m3 model, needs ≥1 GB memory
- **Agent container**: FastEmbed local model + Python runtime, needs ≥1 GB memory
- **PostgreSQL/Redis/Qdrant/SearXNG**: Combined ~1 GB memory

### Required Credentials

Prepare the following before deployment (see [README.md](../README.md) for details):

1. **AgentInsight API Key**: Register at [platform](https://agentinsight.goldebridge.com/platform) to get PublicKey + SecretKey
2. **LLM API Key**: At least one (recommend DeepSeek + Zhipu)
3. **Search engine API Key**: At least one (recommend Bocha)
4. **Database passwords**: Custom PostgreSQL password + Qdrant API Key

---

## Three Build Modes

| Mode | Build File | Compose File | Env File | Build Script | Use Case |
|------|-----------|-------------|----------|-------------|----------|
| QA (offline) | `Dockerfile.qa` | `docker-compose-qa.yaml` | `.env.qa` | `docker-build.qa.bat` | QA testing, intranet |
| Production (online) | `Dockerfile` | `docker-compose.yml` | `.env` | `docker-build.sh` | Open source, CI, external |
| Production (offline) | `Dockerfile.offline` | `docker-compose-offline.yaml` | `.env` | `docker-build.offline.sh` | Intranet production, offline |

### QA Mode (Offline)

- All dependencies pre-downloaded to `packages/` (wheels/debs/models/images)
- `pip install --no-index` offline installation at build time
- `docker load` to import image tarballs at deployment
- All ports bound to `127.0.0.1`, localhost access only
- Suitable for QA testing

### Production Mode (Online)

- Downloads Python dependencies from PyPI at build time
- Pulls base images from Docker Hub (including postgres container)
- No need to pre-download `packages/`
- Only `agent:8066`/`rerank:8089`/`embeddings:8088`/`qdrant:6333` exposed externally
- Suitable for open source contributors to quickly start

### Production Mode (Offline)

- All dependencies pre-downloaded to `packages/`
- `pip install --no-index` offline installation at build time
- `docker load` to import image tarballs at deployment
- Models loaded from local volume
- Suitable for intranet production or offline deployment

---

## Quick Start (Production Online Mode)

### Step 1: Clone the project

```bash
git clone <repository-url>
cd agentinsight-researcher
```

### Step 2: Configure environment variables

```bash
copy .env.template .env
```

Edit `.env` with required fields:

```env
# AgentInsight observability keys (required)
AGENTINSIGHT_PUBLIC_KEY=pk-your-PublicKey
AGENTINSIGHT_SECRET_KEY=sk-your-SecretKey
AGENTINSIGHT_HOST=https://agentinsight.goldebridge.com

# LLM API Key (configure at least one)
DEEPSEEK_API_KEY=sk-your-DeepSeek-key
ZHIPU_API_KEY=your-Zhipu-key

# Search engine API Key (configure at least one)
BOCHA_API_KEY=sk-your-Bocha-key

# Database password (required for production)
POSTGRES_PASSWORD=your-Postgres-password
REDIS_AUTH=your-Redis-password

# Qdrant static API Key (required for production)
QDRANT_API_KEY=sk-your-Qdrant-key
```

### Step 3: Start the container stack

```bash
# Using build script (recommended, includes -p agentinsight project name)
bash docker-build.sh

# Or start manually
docker compose -p agentinsight up -d --build
```

### Step 4: Wait for all services to be healthy

```bash
docker compose -p agentinsight ps
# All showing (healthy) means ready
```

On first startup, the Embeddings container needs to download the model; `start_period: 180s`, please be patient.

### Step 5: Access the test page

Open `http://localhost:8066` in your browser to experience research report generation.

### Step 6: Verify health check

```bash
curl http://localhost:8066/health
# Returns {"status":"ok","service":"agentinsight-researcher","version":"1.1.0"}
```

---

## Port Planning

| Port | Service | Binding | External | Purpose |
|------|---------|---------|----------|---------|
| 8066 | agent | 0.0.0.0 | ✅ | API entry + test page |
| 8088 | embeddings | 0.0.0.0 | ✅ | TEI Embeddings service |
| 8089 | rerank (optional) | 0.0.0.0 | ✅ | TEI Rerank service |
| 6333 | qdrant HTTP | 0.0.0.0 | ✅ | Qdrant vector DB HTTP API |
| 6334 | qdrant gRPC | 127.0.0.1 | ❌ | Qdrant gRPC (internal only) |
| 5432 | postgres | 127.0.0.1 | ❌ | PostgreSQL (internal only) |
| 6379 | redis | 127.0.0.1 | ❌ | Redis (internal only) |
| 8099 | searxng | 127.0.0.1 | ❌ | SearXNG meta search (internal only) |

> In QA mode, all ports bind to `127.0.0.1`, localhost access only.

---

## Environment Variables

### Required Configuration

| Config | Description | How to obtain |
|--------|-------------|---------------|
| `AGENTINSIGHT_PUBLIC_KEY` | AgentInsight PublicKey | [Platform registration](https://agentinsight.goldebridge.com/platform) |
| `AGENTINSIGHT_SECRET_KEY` | AgentInsight SecretKey | Same as above |
| `DEEPSEEK_API_KEY` | DeepSeek API Key | [DeepSeek platform](https://platform.deepseek.com/) |
| `BOCHA_API_KEY` | Bocha search API Key | [Bocha search](https://bochaai.com/) |
| `POSTGRES_PASSWORD` | PostgreSQL password | Custom |
| `QDRANT_API_KEY` | Qdrant static API Key | Custom |

### Key Optional Configuration

| Config | Default | Description |
|--------|---------|-------------|
| `ENV` | `dev` | `dev`/`prod` (prod disables docs/openapi) |
| `SELF_HOST` | `True` | `True`=self-hosted / `False`=cloud-hosted |
| `ENABLE_TEST_PAGE` | `dev=true / prod=false` | Whether to mount test page |
| `FAST_LLM` | `zhipuai/glm-4-flash` | Fast response LLM |
| `SMART_LLM` | `deepseek/deepseek-v4-flash` | Smart analysis LLM |
| `STRATEGIC_LLM` | `deepseek/deepseek-v4-pro` | Strategic decision LLM |
| `RERANK_ENABLED` | `false` | Enable Rerank |
| `WEBSOCKET_ENABLED` | `False` | Enable WebSocket |
| `HUMAN_REVIEW_ENABLED` | `False` | Enable human-in-the-loop review |
| `MAX_UPLOAD_SIZE_MB` | `50` | File upload size limit |
| `CORS_ALLOW_ORIGINS` | `http://localhost:8066` | CORS whitelist |

See `.env.template` for complete configuration.

---

## Health Check Verification

### Container-level Health Check

```bash
# View all container health status
docker compose -p agentinsight ps

# Expected output: all services showing (healthy)
```

### API-level Health Check

```bash
# Agent health check
curl http://localhost:8066/health
# {"status":"ok","service":"agentinsight-researcher","version":"1.1.0"}

# Agent Discovery
curl http://localhost:8066/.well-known/agent-discovery.json

# Model list
curl http://localhost:8066/v1/models
```

### Service Health Check Endpoints

| Service | Health Check Method |
|---------|-------------------|
| agent | `GET /health` |
| postgres | `pg_isready -U <user>` |
| redis | `redis-cli ping` |
| qdrant | `GET /healthz` (port 6333) |
| embeddings | `GET /health` (port 8088) |
| rerank | `GET /health` (port 8089) |
| searxng | `wget http://127.0.0.1:8099/` |

---

## Troubleshooting

### 1. Container startup failure

```bash
# View failed container logs
docker compose -p agentinsight logs <service_name> --tail 200

# Common causes:
# - Empty required fields in .env
# - Port conflicts
# - Insufficient memory
# - Dependencies not ready
```

### 2. Embeddings container unhealthy

```bash
# View model download progress
docker compose -p agentinsight logs -f embeddings

# First startup downloads bge-base-zh-v1.5 model (~400MB), start_period is 180s
# If timeout, extend start_period or pre-download model
```

### 3. Agent container unhealthy

```bash
# View agent startup logs
docker compose -p agentinsight logs agent --tail 200

# Common causes:
# - PostgreSQL business table init failure (check POSTGRES_PASSWORD)
# - AgentInsight SDK init failure (degrades to NoopSpan, doesn't block)
# - Python dependency import failure (check image build)
```

### 4. Data volume cleanup

```bash
# Stop and clean data volumes (use with caution, loses all data)
docker compose -p agentinsight down -v

# Stop containers only (preserve data)
docker compose -p agentinsight down
```

### 5. View resource usage

```bash
# View container resource usage
docker stats $(docker compose -p agentinsight ps -q)

# View disk usage
docker system df
```

---

## Upgrade Process

### 1. Backup data

```bash
# Backup PostgreSQL
docker exec agentinsight-postgres-1 pg_dump -U <user> agents > backup_$(date +%Y%m%d).sql

# Backup Qdrant (snapshot)
curl -X POST http://localhost:6333/snapshots
```

### 2. Pull latest code

```bash
git pull origin main
```

### 3. Rebuild and start

```bash
# Using build script
bash docker-build.sh

# Or manually rebuild
docker compose -p agentinsight up -d --build
```

### 4. Verify upgrade

```bash
# Check version
curl http://localhost:8066/health | grep version

# Check all services healthy
docker compose -p agentinsight ps
```

### 5. Rollback (if upgrade fails)

```bash
# Revert code version
git checkout <previous-tag>

# Rebuild
docker compose -p agentinsight up -d --build

# Restore database (if needed)
docker exec -i agentinsight-postgres-1 psql -U <user> agents < backup_YYYYMMDD.sql
```
