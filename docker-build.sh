#!/usr/bin/env bash
# agentinsight-researcher 生产构建脚本 (联网模式)
# 严格遵循 AGENTS.md 第 12 章: 生产使用联网模式, QA 使用离线模式
# 使用方式: bash docker-build.sh
# 依赖: packages/images/*.tar (基础镜像预下载, 避免构建时拉取失败)

set -e

echo "========== 1. 加载本地镜像 tarball =========="
# 加载 packages/images/ 下所有 .tar 镜像 (基础镜像预下载, 避免构建时拉取失败)
# 联网模式: 若 tarball 不存在则跳过, Docker Compose 将从 Docker Hub 拉取
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
        echo "警告: packages/images/ 下未找到 .tar 文件, 将从 Docker Hub 拉取"
    fi
else
    echo "警告: packages/images/ 目录不存在, 将从 Docker Hub 拉取所有镜像"
fi

echo "========== 2. 构建并启动容器 (生产/联网模式) =========="
docker compose -p agentinsight -f docker-compose.yml up --build -d

echo "========== 3. 清理悬空镜像 =========="
# 用 docker image ls -f dangling=true 直接获取悬空镜像 ID, 避免列解析错位
DANGLING_IMAGES=$(docker image ls -f "dangling=true" -q)
if [ -n "$DANGLING_IMAGES" ]; then
    echo "$DANGLING_IMAGES" | xargs -r docker rmi -f 2>/dev/null || true
fi
docker builder prune -a -f

echo "========== 构建完成 =========="
docker compose -p agentinsight -f docker-compose.yml ps
