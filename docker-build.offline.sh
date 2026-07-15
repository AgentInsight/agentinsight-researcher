#!/usr/bin/env bash
# agentinsight-researcher 生产离线构建脚本
# 生产离线模式, 所有镜像从 tarball 加载, 禁止联网拉取
# 使用方式: bash docker-build.offline.sh
# 依赖: packages/images/*.tar (所有镜像必须预下载, 离线模式禁止联网拉取)
# 依赖: packages/wheels/*.whl (Python 依赖预下载)
# 依赖: packages/debs/*.deb (系统依赖预下载)
# 依赖: packages/models/ (BGE 模型权重预下载)

set -e

PROJECT_NAME="agentinsight"
COMPOSE_FILE="docker-compose-offline.yaml"

echo "========== 1. 加载本地镜像 tarball (生产离线模式) =========="
# 加载 packages/images/ 下所有 .tar 镜像 (离线模式必须全部加载, 禁止联网拉取)
if [ -d "packages/images" ]; then
    LOAD_COUNT=0
    for tar_file in packages/images/*.tar; do
        if [ -f "$tar_file" ]; then
            echo "加载镜像: $tar_file"
            docker load -i "$tar_file"
            LOAD_COUNT=$((LOAD_COUNT + 1))
        fi
    done
    if [ "$LOAD_COUNT" -gt 0 ]; then
        echo "本地镜像加载完成 (共 $LOAD_COUNT 个)"
    else
        echo "错误: packages/images/ 下未找到 .tar 文件"
        echo "生产离线模式要求所有镜像预下载"
        exit 1
    fi
else
    echo "错误: packages/images/ 目录不存在"
    echo "生产离线模式要求所有镜像预下载到 packages/images/"
    exit 1
fi

echo "========== 2. 检查离线依赖包 =========="
if [ ! -d "packages/wheels" ]; then
    echo "错误: packages/wheels 目录不存在, Python 依赖未预下载"
    exit 1
fi
if [ ! -d "packages/debs" ]; then
    echo "错误: packages/debs 目录不存在, 系统依赖未预下载"
    exit 1
fi
echo "离线依赖包检查通过"

# 检查 Node.js 二进制 (MCP 运行时, 可选但强烈推荐)
if ls packages/nodejs/node-v*-linux-x64.tar.gz >/dev/null 2>&1; then
    echo "Node.js binary found, MCP services will be available"
else
    echo "Warning: Node.js binary not found in packages/nodejs/"
    echo "  MCP services will not be available (39/40 MCPs depend on npx)"
    echo "  To enable MCP, run: bash scripts/prepare-nodejs-packages.sh"
fi

# 检查 Playwright chromium (JS 渲染抓取, 可选但强烈推荐)
if ls packages/playwright-browsers/chromium-*/ >/dev/null 2>&1; then
    echo "Playwright chromium found, JS rendering scraping will be available"
else
    echo "Warning: Playwright chromium not found in packages/playwright-browsers/"
    echo "  PlaywrightScraper will degrade to BeautifulSoup (JS rendering sites may fail)"
    echo "  To enable chromium, run: bash scripts/prepare-playwright-chromium.sh"
fi

# 检查 Frontend npm 依赖 (前端容器构建必需)
if [ -f "packages/frontend-wheels/node-modules.tar.gz" ]; then
    echo "Frontend npm dependencies found, frontend container will be fully offline"
else
    echo "Warning: Frontend npm dependencies not found in packages/frontend-wheels/"
    echo "  Frontend container build will fail (offline constraint requires pre-downloaded deps)"
    echo "  To enable offline frontend, run: bash scripts/prepare-frontend-packages.sh"
fi

echo "========== 3. 构建并启动容器 (生产/离线模式) =========="
docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" up --build -d

echo "========== 4. 清理悬空镜像 =========="
# 用 docker image ls -f dangling=true 直接获取悬空镜像 ID, 避免列解析错位
DANGLING_IMAGES=$(docker image ls -f "dangling=true" -q)
if [ -n "$DANGLING_IMAGES" ]; then
    echo "$DANGLING_IMAGES" | xargs -r docker rmi -f 2>/dev/null || true
fi
docker builder prune -a -f

echo "========== 构建完成 =========="
docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" ps
