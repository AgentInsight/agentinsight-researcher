<#
.SYNOPSIS
    Prepare frontend npm dependencies for offline/QA mode
.DESCRIPTION
    QA/Offline mode, all frontend deps pre-downloaded to packages/frontend-wheels/
    This script does two things:
    1. Run npm install in frontend/ to generate complete node_modules
    2. Pack node_modules as tarball to packages/frontend-wheels/node-modules.tar.gz
    Requirement: Node.js 20+ and npm installed on host machine
.NOTES
    Prerequisites: Node.js 20+, npm, internet access (first run)
    After run: packages/frontend-wheels/node-modules.tar.gz ready for frontend/Dockerfile.qa / Dockerfile.offline
#>

$ErrorActionPreference = "Continue"

# Project root (script in scripts/, root is one level up)
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$frontendDir = Join-Path $projectRoot "frontend"
$packagesDir = Join-Path $projectRoot "packages"
$frontendWheelsDir = Join-Path $packagesDir "frontend-wheels"
$nodeModulesTarball = Join-Path $frontendWheelsDir "node-modules.tar.gz"

# Create directories
New-Item -ItemType Directory -Force -Path $frontendWheelsDir | Out-Null

Write-Host "========== 1. Check frontend package.json ==========" -ForegroundColor Cyan
$packageJsonPath = Join-Path $frontendDir "package.json"
if (-not (Test-Path $packageJsonPath)) {
    Write-Host "Error: frontend/package.json not found at $packageJsonPath" -ForegroundColor Red
    exit 1
}
Write-Host "Found: $packageJsonPath" -ForegroundColor Green

Write-Host "========== 2. Install npm dependencies (frontend/) ==========" -ForegroundColor Cyan
Push-Location $frontendDir
try {
    # Use --legacy-peer-deps for React 19 + Next.js 15 compatibility
    Write-Host "Running: npm install --legacy-peer-deps"
    & npm install --legacy-peer-deps
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Error: npm install failed" -ForegroundColor Red
        exit 1
    }

    # Verify node_modules exists
    $nodeModulesPath = Join-Path $frontendDir "node_modules"
    if (-not (Test-Path $nodeModulesPath)) {
        Write-Host "Error: node_modules not created at $nodeModulesPath" -ForegroundColor Red
        exit 1
    }
    Write-Host "node_modules created successfully" -ForegroundColor Green

    # ========== 补装 Linux 平台原生模块 (Docker Alpine 构建) ==========
    # Windows npm install 只安装 Windows 版本的原生模块 (lightningcss/esbuild/sharp 等)
    # Docker Alpine (linux-x64-musl) 需要对应的 Linux 版本二进制
    # 方案: 用 npm pack 下载 Linux 版本的 optional 依赖 tarball, 手动提取 .node 文件
    Write-Host "========== 2.5. Install Linux platform native binaries ==========" -ForegroundColor Cyan

    # --- lightningcss (Tailwind CSS v4 依赖, 构建时必需) ---
    $lightningcssDir = Join-Path $frontendDir "node_modules\lightningcss"
    $lightningcssLinuxBinary = Join-Path $lightningcssDir "lightningcss.linux-x64-musl.node"
    if (-not (Test-Path $lightningcssLinuxBinary)) {
        Write-Host "Downloading @lightningcss/lightningcss-linux-x64-musl..."
        # 用 node -p 读取版本号 (比 ConvertFrom-Json 更可靠, 避免 BOM/编码问题)
        $lcVersion = (& node -p "require('./node_modules/lightningcss/package.json').version").Trim()
        Write-Host "  lightningcss version: $lcVersion"
        if ([string]::IsNullOrWhiteSpace($lcVersion)) {
            Write-Host "  Error: cannot read lightningcss version" -ForegroundColor Red
            exit 1
        }

        # npm pack 下载 tarball (npm pack 输出 tarball 文件名到 stdout)
        # 注意: 包名无 @lightningcss/ scope 前缀 (lightningcss optionalDependencies 直接用无 scope 包名)
        $packResult = & npm pack "lightningcss-linux-x64-musl@$lcVersion" 2>&1
        # 从输出中提取 tarball 文件名 (最后一行是文件名)
        $tarballFile = ($packResult | Where-Object { $_ -match '\.tgz$' } | Select-Object -Last 1).Trim()
        Write-Host "  tarball: $tarballFile"
        if ($tarballFile -and (Test-Path $tarballFile)) {
            # 解压 tarball 到临时目录 (用 New-Item 直接创建, 避免 Join-Path 问题)
            $tempDir = "tmp-lc-extract-" + [System.Guid]::NewGuid().ToString("N").Substring(0, 8)
            New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
            & tar -xzf $tarballFile -C $tempDir
            # 复制 .node 文件
            $nodeFile = Get-ChildItem $tempDir -Filter "*.node" -Recurse | Select-Object -First 1
            if ($nodeFile) {
                Copy-Item $nodeFile.FullName $lightningcssLinuxBinary -Force
                Write-Host "  lightningcss.linux-x64-musl.node installed" -ForegroundColor Green
            } else {
                Write-Host "  Warning: .node file not found in tarball" -ForegroundColor Yellow
            }
            Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue
            Remove-Item $tarballFile -Force -ErrorAction SilentlyContinue
        } else {
            Write-Host "  Warning: npm pack failed for @lightningcss/lightningcss-linux-x64-musl" -ForegroundColor Yellow
            Write-Host "  Pack output: $packResult" -ForegroundColor Yellow
        }
    } else {
        Write-Host "lightningcss.linux-x64-musl.node already exists" -ForegroundColor Green
    }

    # --- esbuild (Next.js/SWC 依赖, 构建时必需) ---
    $esbuildDir = Join-Path $frontendDir "node_modules\@esbuild\linux-x64"
    if (-not (Test-Path $esbuildDir)) {
        $esbuildPkgDir = Join-Path $frontendDir "node_modules\esbuild"
        if (Test-Path $esbuildPkgDir) {
            $esbuildVersion = (& node -p "require('./node_modules/esbuild/package.json').version").Trim()
            Write-Host "Downloading @esbuild/linux-x64@$esbuildVersion..."
            $esbuildTargetDir = Join-Path $frontendDir "node_modules\@esbuild\linux-x64"
            New-Item -ItemType Directory -Force -Path (Join-Path $frontendDir "node_modules\@esbuild") | Out-Null
            $packResult = & npm pack "@esbuild/linux-x64@$esbuildVersion" 2>&1
            $tarballFile = ($packResult | Where-Object { $_ -match '\.tgz$' } | Select-Object -Last 1).Trim()
            if ($tarballFile -and (Test-Path $tarballFile)) {
                New-Item -ItemType Directory -Force -Path $esbuildTargetDir | Out-Null
                & tar -xzf $tarballFile -C $esbuildTargetDir --strip-components=1
                Write-Host "  @esbuild/linux-x64 installed" -ForegroundColor Green
                Remove-Item $tarballFile -Force -ErrorAction SilentlyContinue
            }
        }
    } else {
        Write-Host "@esbuild/linux-x64 already exists" -ForegroundColor Green
    }

    # --- @tailwindcss/oxide (Tailwind CSS v4 原生模块) ---
    $oxideLinuxDir = Join-Path $frontendDir "node_modules\@tailwindcss\oxide-linux-x64-musl"
    if (-not (Test-Path $oxideLinuxDir)) {
        $oxidePkgDir = Join-Path $frontendDir "node_modules\@tailwindcss\oxide"
        if (Test-Path $oxidePkgDir) {
            $oxideVersion = (& node -p "require('./node_modules/@tailwindcss/oxide/package.json').version").Trim()
            Write-Host "Downloading @tailwindcss/oxide-linux-x64-musl@$oxideVersion..."
            $packResult = & npm pack "@tailwindcss/oxide-linux-x64-musl@$oxideVersion" 2>&1
            $tarballFile = ($packResult | Where-Object { $_ -match '\.tgz$' } | Select-Object -Last 1).Trim()
            if ($tarballFile -and (Test-Path $tarballFile)) {
                New-Item -ItemType Directory -Force -Path $oxideLinuxDir | Out-Null
                & tar -xzf $tarballFile -C $oxideLinuxDir --strip-components=1
                Write-Host "  @tailwindcss/oxide-linux-x64-musl installed" -ForegroundColor Green
                Remove-Item $tarballFile -Force -ErrorAction SilentlyContinue
            }
        }
    } else {
        Write-Host "@tailwindcss/oxide-linux-x64-musl already exists" -ForegroundColor Green
    }

    Write-Host "========== 3. Pack node_modules as tarball ==========" -ForegroundColor Cyan
    # Pack node_modules directory as tarball (strip root 'node_modules' dir for Docker extraction)
    # tar -czf <output> -C <base-dir> node_modules  →  archive contains node_modules/...
    # Dockerfile: tar -xzf ... -C node_modules --strip-components=1  →  extracts contents into node_modules/
    & tar -czf $nodeModulesTarball -C $frontendDir node_modules
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Error: tar failed to create $nodeModulesTarball" -ForegroundColor Red
        exit 1
    }

    $tarballSize = (Get-Item $nodeModulesTarball).Length / 1MB
    Write-Host "Done: $nodeModulesTarball ($('{0:N2}' -f $tarballSize) MB)" -ForegroundColor Green
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "========== Summary ==========" -ForegroundColor Cyan
Write-Host "Frontend npm dependencies prepared for offline mode:"
Write-Host "  Tarball: $nodeModulesTarball"
Write-Host "  Size:    $('{0:N2}' -f $tarballSize) MB"
Write-Host ""
Write-Host "Ready for: frontend/Dockerfile.qa, frontend/Dockerfile.offline"
Write-Host "Note: node:20-alpine image also needs to be saved to packages/images/node-20-alpine.tar"
