<#
.SYNOPSIS
    Prepare curl_cffi wheel for SearXNG QA/Offline Docker builds
.DESCRIPTION
    严格遵循 AGENTS.md 第 12 章: QA/离线模式所有依赖预下载到 packages/
    使用 SearXNG 镜像本身下载 curl_cffi wheel, 确保 musllinux 兼容 (Alpine 环境)
    下载到 packages/searxng-wheels/ 供 Dockerfile.searxng 离线安装

    SearXNG 镜像基于 Alpine (musl libc), 必须下载 musllinux_* 标签的 wheel
    curl_cffi 依赖 cffi/pycparser 等, pip download 会自动解析全部依赖

    Dockerfile.searxng 通过 COPY packages/searxng-wheels/ + --no-index --find-links 离线安装
.NOTES
    Prerequisites: Docker Desktop 运行中 (用于拉取 SearXNG 镜像下载 wheel), 首次运行需联网
    After run: packages/searxng-wheels/*.whl ready for Dockerfile.searxng 离线安装
#>

$ErrorActionPreference = "Stop"

# Project root (script in scripts/, root is one level up)
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$wheelsDir = Join-Path $projectRoot "packages\searxng-wheels"

# Create directory
New-Item -ItemType Directory -Force -Path $wheelsDir | Out-Null

Write-Host "========== Prepare curl_cffi wheel for SearXNG (QA/Offline) ==========" -ForegroundColor Cyan
Write-Host "Target: $wheelsDir" -ForegroundColor DarkGray

# Check if wheels already exist
$existingWhls = Get-ChildItem -Path $wheelsDir -Filter "*.whl" -ErrorAction SilentlyContinue
if ($existingWhls) {
    $count = $existingWhls.Count
    $totalSizeMB = [math]::Round(($existingWhls | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
    Write-Host "[SKIP] $wheelsDir 已有 $count 个 wheel 文件 ($totalSizeMB MB)" -ForegroundColor Green
    Write-Host "  如需重新下载, 请先删除该目录下的 .whl 文件" -ForegroundColor DarkGray
    exit 0
}

# Check Docker is running
Write-Host "[1/3] 检查 Docker 环境..." -ForegroundColor Yellow
$dockerOk = $false
try {
    $dockerVersion = docker version --format "{{.Server.Version}}" 2>$null
    if ($LASTEXITCODE -eq 0 -and $dockerVersion) {
        Write-Host "  Docker Server: $dockerVersion" -ForegroundColor DarkGray
        $dockerOk = $true
    }
} catch {
    # docker version 失败
}

if (-not $dockerOk) {
    Write-Host "[ERROR] Docker 未运行或不可用" -ForegroundColor Red
    Write-Host "  此脚本需要 Docker 拉取 SearXNG 镜像下载 musllinux 兼容 wheel" -ForegroundColor Yellow
    Write-Host "  请启动 Docker Desktop 后重试" -ForegroundColor Yellow
    exit 1
}

# SearXNG 镜像 (与 docker-compose 一致)
$searxngImage = "docker.io/searxng/searxng:latest"
Write-Host "[2/3] 拉取 SearXNG 镜像 (用于下载 musllinux 兼容 wheel)..." -ForegroundColor Yellow
Write-Host "  Image: $searxngImage" -ForegroundColor DarkGray

docker pull $searxngImage
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] SearXNG 镜像拉取失败" -ForegroundColor Red
    Write-Host "  手动拉取: docker pull $searxngImage" -ForegroundColor Yellow
    exit 1
}

# 使用 SearXNG 镜像本身下载 wheel, 确保 musllinux 兼容
# SearXNG venv Python: /usr/local/searxng/.venv/bin/python3
# pip download 会自动下载 curl_cffi 及所有依赖 (cffi, pycparser 等)
Write-Host "[3/3] 下载 curl_cffi wheel 到 packages/searxng-wheels/ ..." -ForegroundColor Yellow

$wheelsDirLinux = $wheelsDir -replace '\\', '/'
$downloadCmd = @"
set -e
/usr/local/searxng/.venv/bin/python3 -m ensurepip --upgrade 2>/dev/null || true
echo '[SearXNG] 开始下载 curl_cffi 及依赖 wheel...'
/usr/local/searxng/.venv/bin/python3 -m pip download --no-cache-dir -d /out curl_cffi
echo '[SearXNG] 下载完成'
ls -lh /out/*.whl
"@

docker run --rm -v "${wheelsDirLinux}:/out" $searxngImage sh -c $downloadCmd
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] curl_cffi wheel 下载失败" -ForegroundColor Red
    Write-Host "  可能原因:" -ForegroundColor Yellow
    Write-Host "    1. 网络问题 (pip download 需访问 PyPI)" -ForegroundColor Yellow
    Write-Host "    2. SearXNG 镜像 venv 路径变化 (检查 /usr/local/searxng/.venv/)" -ForegroundColor Yellow
    Write-Host "    3. curl_cffi 无 musllinux 兼容 wheel (需源码编译, 安装 gcc/musl-dev)" -ForegroundColor Yellow
    exit 1
}

# Verify download
$downloadedWhls = Get-ChildItem -Path $wheelsDir -Filter "*.whl" -ErrorAction SilentlyContinue
if ($downloadedWhls) {
    $count = $downloadedWhls.Count
    $totalSizeMB = [math]::Round(($downloadedWhls | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
    Write-Host ""
    Write-Host "========== Done ==========" -ForegroundColor Cyan
    Write-Host "packages/searxng-wheels/ 已就绪: $count 个 wheel ($totalSizeMB MB)" -ForegroundColor Green
    Write-Host "Dockerfile.searxng 将通过 --no-index --find-links 离线安装 curl_cffi" -ForegroundColor Green
    Write-Host ""
    Write-Host "Wheel 清单:" -ForegroundColor DarkGray
    $downloadedWhls | ForEach-Object { Write-Host "  $($_.Name)" -ForegroundColor DarkGray }
} else {
    Write-Host "[ERROR] 下载后未找到 .whl 文件" -ForegroundColor Red
    exit 1
}
