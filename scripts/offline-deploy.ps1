# agentinsight-researcher QA 离线部署脚本
# 严格遵循 AGENTS.md 第 12 章: 禁止部署时联网拉镜像/装依赖/下模型
# 所有依赖预下载到 packages/ (wheels/debs/models/images)

param(
    [switch]$SkipLoadImages,  # 跳过镜像加载 (镜像已加载时用)
    [switch]$SkipCopyModels,  # 跳过模型复制 (模型已复制时用)
    [switch]$Rebuild          # 强制重新构建 agent 镜像
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot/..

$ComposeFile = "docker-compose-qa.yaml"
$ProjectName = "agentinsight-qa"

Write-Host "========== AgentInsight Researcher QA 离线部署 ==========" -ForegroundColor Cyan
Write-Host "Compose: $ComposeFile | Project: $ProjectName" -ForegroundColor DarkGray

# ========== 1. 加载 Docker 镜像 tarball ==========
if (-not $SkipLoadImages) {
    Write-Host "[1/4] 加载 Docker 镜像 tarball..." -ForegroundColor Yellow
    $images = @(
        "packages/images/postgres-16-alpine.tar",
        "packages/images/redis-7-alpine.tar",
        "packages/images/qdrant-v1.18.0.tar",
        "packages/images/text-embeddings-inference-cpu-1.5.tar"
    )
    foreach ($img in $images) {
        if (Test-Path $img) {
            Write-Host "  加载 $img..."
            docker load -i $img 2>&1 | ForEach-Object { Write-Host "    $_" }
        } else {
            Write-Host "  跳过 $img (文件不存在)" -ForegroundColor DarkGray
        }
    }
    Write-Host "  镜像加载完成." -ForegroundColor Green
} else {
    Write-Host "[1/4] 跳过镜像加载." -ForegroundColor DarkGray
}

# ========== 2. 复制模型到 Docker volume ==========
if (-not $SkipCopyModels) {
    Write-Host "[2/4] 复制模型到 Docker volume..." -ForegroundColor Yellow

    # embeddings 模型
    if (Test-Path "packages/models/bge-large-zh-v1.5") {
        Write-Host "  复制 bge-large-zh-v1.5 到 embeddings_models volume..."
        docker rm -f emb-copier 2>$null | Out-Null
        docker run -d --name emb-copier -v "${ProjectName}_embeddings_models:/data" python:3.12-slim sleep 3600 2>&1 | Out-Null
        docker cp packages/models/bge-large-zh-v1.5 emb-copier:/data/ 2>&1 | Out-Null
        docker rm -f emb-copier 2>&1 | Out-Null
        Write-Host "  embeddings 模型复制完成." -ForegroundColor Green
    }

    # rerank 模型
    if (Test-Path "packages/models/bge-reranker-v2-m3") {
        Write-Host "  复制 bge-reranker-v2-m3 到 rerank_models volume..."
        docker rm -f rer-copier 2>$null | Out-Null
        docker run -d --name rer-copier -v "${ProjectName}_rerank_models:/data" python:3.12-slim sleep 3600 2>&1 | Out-Null
        docker cp packages/models/bge-reranker-v2-m3 rer-copier:/data/ 2>&1 | Out-Null
        docker rm -f rer-copier 2>&1 | Out-Null
        Write-Host "  rerank 模型复制完成." -ForegroundColor Green
    }
} else {
    Write-Host "[2/4] 跳过模型复制." -ForegroundColor DarkGray
}

# ========== 3. 构建 agent 镜像 (QA 离线) ==========
Write-Host "[3/4] 构建 agent 镜像 (QA 离线模式)..." -ForegroundColor Yellow
if ($Rebuild) {
    docker compose -p $ProjectName -f $ComposeFile --env-file .env.qa build --no-cache agent 2>&1 | ForEach-Object { Write-Host "  $_" }
} else {
    docker compose -p $ProjectName -f $ComposeFile --env-file .env.qa build agent 2>&1 | ForEach-Object { Write-Host "  $_" }
}
Write-Host "  agent 镜像构建完成." -ForegroundColor Green

# ========== 4. 起栈 ==========
Write-Host "[4/4] 启动容器栈 (QA 离线模式)..." -ForegroundColor Yellow
docker compose -p $ProjectName -f $ComposeFile --env-file .env.qa down 2>&1 | Out-Null
docker compose -p $ProjectName -f $ComposeFile --env-file .env.qa up -d 2>&1 | ForEach-Object { Write-Host "  $_" }

Write-Host ""
Write-Host "========== QA 部署完成 ==========" -ForegroundColor Cyan
Write-Host "等待容器健康检查 (最多 5 分钟)..." -ForegroundColor Yellow

$deadline = (Get-Date).AddMinutes(5)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 15
    $status = docker compose -p $ProjectName -f $ComposeFile ps --format json 2>&1 | ConvertFrom-Json
    $allHealthy = $true
    foreach ($s in $status) {
        if ($s.Health -ne "healthy") {
            $allHealthy = $false
            Write-Host "  $($s.Name): $($s.State) (Health: $($s.Health))" -ForegroundColor DarkYellow
        }
    }
    if ($allHealthy -and $status.Count -ge 6) {
        $healthy = $true
        break
    }
}

if ($healthy) {
    Write-Host "所有容器健康!" -ForegroundColor Green
} else {
    Write-Host "部分容器未健康,请检查 docker compose -p $ProjectName -f $ComposeFile ps" -ForegroundColor Red
}

Write-Host ""
Write-Host "容器状态:" -ForegroundColor Cyan
docker compose -p $ProjectName -f $ComposeFile ps
Write-Host ""
Write-Host "访问地址: http://localhost:8066" -ForegroundColor Cyan
Write-Host "健康检查: http://localhost:8066/health" -ForegroundColor Cyan
