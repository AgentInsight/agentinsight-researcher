# agentinsight-researcher 联网构建脚本
# 联网模式: 构建时从 PyPI 下载 Python 依赖, 从 Docker Hub 拉取基础镜像
# 适用于开源社区贡献者快速起栈
# 使用方式:
#   .\scripts\build-online.ps1                 # 完整构建 (构建+起栈)
#   .\scripts\build-online.ps1 -Rebuild        # 强制重新构建 agent 镜像
#   .\scripts\build-online.ps1 -EnableRerank   # 启用 rerank 容器

param(
    [switch]$Rebuild,        # 强制重新构建 agent 镜像
    [switch]$EnableRerank,   # 启用 rerank 容器 (同时需在 .env 设置 RERANK_ENABLED=true)
    [switch]$SkipBuild       # 跳过构建 (镜像已就绪时用)
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot/..

$ComposeFile = "docker-compose.yml"
$Dockerfile = "Dockerfile"

Write-Host "========== AgentInsight Researcher 联网构建 ==========" -ForegroundColor Cyan
Write-Host "Compose: $ComposeFile | Dockerfile: $Dockerfile" -ForegroundColor DarkGray

# ========== 1. 构建 agent 镜像 (联网) ==========
if (-not $SkipBuild) {
    Write-Host "[1/3] 构建 agent 镜像 (联网模式, Dockerfile)..." -ForegroundColor Yellow
    Write-Host "  从 PyPI 下载 Python 依赖, apt-get 安装系统依赖..." -ForegroundColor DarkGray
    $buildArgs = @("build", "-f", $Dockerfile, "-t", "agentinsight-researcher:latest", ".")
    if ($Rebuild) {
        $buildArgs += "--no-cache"
        Write-Host "  强制重新构建 (--no-cache)" -ForegroundColor DarkGray
    }
    docker @buildArgs 2>&1 | ForEach-Object { Write-Host "  $_" }
    Write-Host "  agent 镜像构建完成." -ForegroundColor Green
} else {
    Write-Host "[1/3] 跳过构建." -ForegroundColor DarkGray
}

# ========== 2. 起栈 ==========
Write-Host "[2/3] 启动容器栈 (联网模式)..." -ForegroundColor Yellow
$composeArgs = @("-p", "agentinsight", "-f", $ComposeFile, "down")
docker @composeArgs 2>&1 | Out-Null

$composeArgs = @("-p", "agentinsight", "-f", $ComposeFile, "up", "-d")
if ($EnableRerank) {
    $composeArgs += @("--profile", "rerank")
    Write-Host "  启用 rerank 容器 (profile: rerank)" -ForegroundColor DarkGray
}
docker @composeArgs 2>&1 | ForEach-Object { Write-Host "  $_" }

# ========== 3. 等待健康检查 ==========
Write-Host "[3/3] 等待容器健康检查 (最多 5 分钟)..." -ForegroundColor Yellow

$deadline = (Get-Date).AddMinutes(5)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 15
    $status = docker compose -p agentinsight -f $ComposeFile ps --format json 2>&1 | ConvertFrom-Json
    $allHealthy = $true
    foreach ($s in $status) {
        if ($s.Health -ne "healthy") {
            $allHealthy = $false
            Write-Host "  $($s.Name): $($s.State) (Health: $($s.Health))" -ForegroundColor DarkYellow
        }
    }
    $expectedCount = if ($EnableRerank) { 6 } else { 5 }
    if ($allHealthy -and $status.Count -ge $expectedCount) {
        $healthy = $true
        break
    }
}

Write-Host ""
Write-Host "========== 联网构建完成 ==========" -ForegroundColor Cyan
if ($healthy) {
    Write-Host "所有容器健康!" -ForegroundColor Green
} else {
    Write-Host "部分容器未健康, 请检查 docker compose -p agentinsight -f $ComposeFile ps" -ForegroundColor Red
}

Write-Host ""
Write-Host "容器状态:" -ForegroundColor Cyan
docker compose -p agentinsight -f $ComposeFile ps
Write-Host ""
Write-Host "访问地址: http://localhost:8066" -ForegroundColor Cyan
Write-Host "健康检查: http://localhost:8066/health" -ForegroundColor Cyan
