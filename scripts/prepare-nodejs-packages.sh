#!/usr/bin/env bash
# 准备 Node.js 22 LTS 二进制和 MCP npm 包 (QA/Offline 离线模式)
#
# 严格遵循 AGENTS.md 第 12 章: QA/Offline 离线模式, 所有依赖预下载到 packages/
# 本脚本完成两件事:
# 1. 下载 Node.js 22 LTS 二进制 tarball 到 packages/nodejs/
# 2. 用 Docker 容器 (node:22-slim) 预装 40 个 MCP npm 包到全局, 导出为 tarball 到 packages/npm-pkgs/
#
# 运行前提: 已安装 Docker, 能联网下载 (首次准备)
# 运行后: packages/nodejs/ 和 packages/npm-pkgs/ 就绪, 可供 Dockerfile.qa / Dockerfile.offline 离线构建

set -e

# 项目根目录 (脚本位于 scripts/, 项目根在上一级)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PACKAGES_DIR="$PROJECT_ROOT/packages"
NODEJS_DIR="$PACKAGES_DIR/nodejs"
NPM_PKGS_DIR="$PACKAGES_DIR/npm-pkgs"

# 创建目录
mkdir -p "$NODEJS_DIR" "$NPM_PKGS_DIR"

# Node.js 22 LTS version (use .tar.gz, python:3.12-slim has no xz)
# v22.23.1 (2026-06-23): fix npm 10.9.8 minizlib incompatibility with Node 22.11
NODE_VERSION="v22.23.1"
NODE_TARBALL_NAME="node-$NODE_VERSION-linux-x64.tar.gz"
NODE_URL="https://nodejs.org/dist/$NODE_VERSION/$NODE_TARBALL_NAME"
NODE_TARBALL_PATH="$NODEJS_DIR/$NODE_TARBALL_NAME"

echo "========== 1. 下载 Node.js $NODE_VERSION 二进制 =========="
if [ -f "$NODE_TARBALL_PATH" ]; then
    echo "已存在: $NODE_TARBALL_NAME, 跳过下载"
else
    echo "下载: $NODE_URL"
    curl -fsSL -o "$NODE_TARBALL_PATH" "$NODE_URL"
    echo "下载完成: $NODE_TARBALL_PATH"
fi

# 40 个 MCP npm 包列表 (与 scripts/init.sql 中系统 MCP 一致)
NPM_PACKAGES=(
    # ===== 核心保留 18 个 =====
    "@modelcontextprotocol/server-fetch"
    "@modelcontextprotocol/server-filesystem"
    "@modelcontextprotocol/server-sequentialthinking"
    "@modelcontextprotocol/server-github"
    "@notionhq/notion-mcp-server"
    "mcp-obsidian"
    "@sooperset/mcp-atlassian"
    "@elastic/mcp-server-elasticsearch"
    "@phuongcao/mcp-server-wikipedia"
    "mcp-hacker-news"
    "mcp-server-newsapi"
    "mcp-server-stackoverflow"
    "mcp-server-neo4j"
    "mcp-server-duckdb"
    "mcp-server-alpha-vantage"
    "mcp-server-wolfram-alpha"
    "mcp-server-deepl"
    "mcp-server-rss"
    # ===== 推荐 22 个 =====
    "@modelcontextprotocol/server-git"
    "@modelcontextprotocol/server-gitlab"
    "@anthropic-ai/chrome-mcp"
    "mcp-server-npm-search"
    "mcp-server-sourcegraph"
    "mcp-server-filesystem-search"
    "@modelcontextprotocol/server-gdrive"
    "mcp-server-airtable"
    "@anaisbetts/mcp-youtube"
    "@enescinar/twitter-mcp"
    "mcp-server-reddit"
    "mcp-server-mongodb"
    "@supabase/mcp-server-supabase"
    "mcp-server-bigquery"
    "mcp-server-clickhouse"
    "mcp-server-snowflake"
    "mcp-server-mapbox"
    "mcp-server-openweather"
    "@modelcontextprotocol/server-aws-kb-retrieval"
    "mcp-server-calculator"
    "mcp-server-markdown"
    "mcp-server-pdf-tools"
)

PKG_COUNT=${#NPM_PACKAGES[@]}
echo ""
echo "========== 2. 用 Docker 容器预装 $PKG_COUNT 个 MCP npm 包 =========="

# 检查 Docker 是否可用
if ! docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
    echo "错误: Docker 不可用, 无法预装 npm 包"
    echo "请先启动 Docker, 然后重新运行本脚本"
    exit 1
fi

DOCKER_VERSION=$(docker version --format '{{.Server.Version}}')
echo "Docker 可用 (Server 版本: $DOCKER_VERSION)"

# 检查 node:22-slim 镜像是否存在, 不存在则拉取
if [ -z "$(docker image ls node:22-slim -q)" ]; then
    echo "拉取 node:22-slim 镜像 (用于预装 npm 包)..."
    docker pull node:22-slim
fi

# 创建临时容器预装 npm 包
TEMP_CONTAINER="npm-preparer-$$"
GLOBAL_TARBALL="node-global.tar.gz"
GLOBAL_TARBALL_PATH="$NPM_PKGS_DIR/$GLOBAL_TARBALL"

echo "创建临时容器: $TEMP_CONTAINER"
docker run --name "$TEMP_CONTAINER" -d node:22-slim tail -f /dev/null

# 确保清理临时容器
trap "docker rm -f $TEMP_CONTAINER >/dev/null 2>&1 || true" EXIT

# 在容器内逐个 npm install -g 安装 (单个失败不阻塞其他, 与 .ps1 行为一致)
echo "在容器内预装 $PKG_COUNT 个 npm 包到全局 (逐个安装)..."
echo "  (首次安装可能需要 5-10 分钟, 取决于网络速度)"

SUCCESS_COUNT=0
FAILED_PACKAGES=()
for pkg in "${NPM_PACKAGES[@]}"; do
    echo "  Installing: $pkg"
    if docker exec "$TEMP_CONTAINER" npm install -g "$pkg" >/dev/null 2>&1; then
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    else
        FAILED_PACKAGES+=("$pkg")
        echo "    FAILED: $pkg"
    fi
done

echo "Installed: $SUCCESS_COUNT / $PKG_COUNT packages"
if [ ${#FAILED_PACKAGES[@]} -gt 0 ]; then
    echo "Failed packages (${#FAILED_PACKAGES[@]}):"
    for fp in "${FAILED_PACKAGES[@]}"; do
        echo "  - $fp"
    done
fi

# 验证安装结果
echo "已安装的全局包:"
docker exec "$TEMP_CONTAINER" npm ls -g --depth=0 2>/dev/null | head -45

# 导出 /usr/local/lib/node_modules 为 tarball
echo "导出全局 node_modules 为 tarball..."
docker exec "$TEMP_CONTAINER" sh -c "tar -czf /tmp/$GLOBAL_TARBALL -C /usr/local/lib node_modules"

# 从容器复制到宿主机
docker cp "$TEMP_CONTAINER:/tmp/$GLOBAL_TARBALL" "$GLOBAL_TARBALL_PATH"

TARBALL_SIZE=$(du -h "$GLOBAL_TARBALL_PATH" | cut -f1)
echo "导出完成: $GLOBAL_TARBALL_PATH ($TARBALL_SIZE)"

echo ""
echo "========== 准备完成 =========="
echo "Node.js 二进制: $NODE_TARBALL_PATH"
echo "npm 全局包:     $GLOBAL_TARBALL_PATH"
echo ""
echo "下一步: 运行 docker-build.qa.bat 或 docker-build.offline.sh 构建容器"
