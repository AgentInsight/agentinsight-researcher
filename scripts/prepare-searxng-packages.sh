#!/usr/bin/env bash
# ============================================================================
# Prepare curl_cffi wheel for SearXNG QA/Offline Docker builds
# ----------------------------------------------------------------------------
# 严格遵循 AGENTS.md 第 12 章: QA/离线模式所有依赖预下载到 packages/
# 使用 SearXNG 镜像本身下载 curl_cffi wheel, 确保 musllinux 兼容 (Alpine 环境)
# 下载到 packages/searxng-wheels/ 供 Dockerfile.searxng 离线安装
#
# SearXNG 镜像基于 Alpine (musl libc), 必须下载 musllinux_* 标签的 wheel
# curl_cffi 依赖 cffi/pycparser 等, pip download 会自动解析全部依赖
#
# Dockerfile.searxng 通过 COPY packages/searxng-wheels/ + --no-index --find-links 离线安装
#
# Prerequisites: Docker 运行中 (用于拉取 SearXNG 镜像下载 wheel), 首次运行需联网
# After run: packages/searxng-wheels/*.whl ready for Dockerfile.searxng 离线安装
# ============================================================================

set -euo pipefail

# Project root (script in scripts/, root is one level up)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WHEELS_DIR="$PROJECT_ROOT/packages/searxng-wheels"

# Create directory
mkdir -p "$WHEELS_DIR"

echo "========== Prepare curl_cffi wheel for SearXNG (QA/Offline) =========="
echo "Target: $WHEELS_DIR"

# Check if wheels already exist
existing_whl_count=$(find "$WHEELS_DIR" -maxdepth 1 -name "*.whl" 2>/dev/null | wc -l)
if [ "$existing_whl_count" -gt 0 ]; then
    total_size_bytes=$(find "$WHEELS_DIR" -maxdepth 1 -name "*.whl" -printf '%s\n' | awk '{s+=$1} END {print s}')
    total_size_mb=$(awk "BEGIN {printf \"%.1f\", $total_size_bytes / 1048576}")
    echo "[SKIP] $WHEELS_DIR 已有 $existing_whl_count 个 wheel 文件 ($total_size_mb MB)"
    echo "  如需重新下载, 请先删除该目录下的 .whl 文件"
    exit 0
fi

# Check Docker is running
echo "[1/3] 检查 Docker 环境..."
docker_ok=false
if docker version --format "{{.Server.Version}}" >/dev/null 2>&1; then
    docker_version=$(docker version --format "{{.Server.Version}}" 2>/dev/null)
    echo "  Docker Server: $docker_version"
    docker_ok=true
fi

if [ "$docker_ok" != "true" ]; then
    echo "[ERROR] Docker 未运行或不可用"
    echo "  此脚本需要 Docker 拉取 SearXNG 镜像下载 musllinux 兼容 wheel"
    echo "  请启动 Docker 后重试"
    exit 1
fi

# SearXNG 镜像 (与 docker-compose 一致)
SEARXNG_IMAGE="docker.io/searxng/searxng:latest"
echo "[2/3] 拉取 SearXNG 镜像 (用于下载 musllinux 兼容 wheel)..."
echo "  Image: $SEARXNG_IMAGE"

if ! docker pull "$SEARXNG_IMAGE"; then
    echo "[ERROR] SearXNG 镜像拉取失败"
    echo "  手动拉取: docker pull $SEARXNG_IMAGE"
    exit 1
fi

# 使用 SearXNG 镜像本身下载 wheel, 确保 musllinux 兼容
# SearXNG venv Python: /usr/local/searxng/.venv/bin/python3
# pip download 会自动下载 curl_cffi 及所有依赖 (cffi, pycparser 等)
echo "[3/3] 下载 curl_cffi wheel 到 packages/searxng-wheels/ ..."

# 转换为 Linux 风格路径 (供 Docker volume 挂载)
wheels_dir_linux=$(echo "$WHEELS_DIR" | sed 's|\\|/|g')

# 兼容 Windows Git Bash / WSL 路径: 若为 /d/... 形式则保留, 否则原样使用
download_cmd='
set -e
/usr/local/searxng/.venv/bin/python3 -m ensurepip --upgrade 2>/dev/null || true
echo "[SearXNG] 开始下载 curl_cffi 及依赖 wheel..."
/usr/local/searxng/.venv/bin/python3 -m pip download --no-cache-dir -d /out curl_cffi
echo "[SearXNG] 下载完成"
ls -lh /out/*.whl
'

if ! docker run --rm -v "${wheels_dir_linux}:/out" "$SEARXNG_IMAGE" sh -c "$download_cmd"; then
    echo "[ERROR] curl_cffi wheel 下载失败"
    echo "  可能原因:"
    echo "    1. 网络问题 (pip download 需访问 PyPI)"
    echo "    2. SearXNG 镜像 venv 路径变化 (检查 /usr/local/searxng/.venv/)"
    echo "    3. curl_cffi 无 musllinux 兼容 wheel (需源码编译, 安装 gcc/musl-dev)"
    exit 1
fi

# Verify download
downloaded_whl_count=$(find "$WHEELS_DIR" -maxdepth 1 -name "*.whl" 2>/dev/null | wc -l)
if [ "$downloaded_whl_count" -gt 0 ]; then
    total_size_bytes=$(find "$WHEELS_DIR" -maxdepth 1 -name "*.whl" -printf '%s\n' | awk '{s+=$1} END {print s}')
    total_size_mb=$(awk "BEGIN {printf \"%.1f\", $total_size_bytes / 1048576}")
    echo ""
    echo "========== Done =========="
    echo "packages/searxng-wheels/ 已就绪: $downloaded_whl_count 个 wheel ($total_size_mb MB)"
    echo "Dockerfile.searxng 将通过 --no-index --find-links 离线安装 curl_cffi"
    echo ""
    echo "Wheel 清单:"
    find "$WHEELS_DIR" -maxdepth 1 -name "*.whl" -printf '%f\n' | while read -r whl; do
        echo "  $whl"
    done
else
    echo "[ERROR] 下载后未找到 .whl 文件"
    exit 1
fi
