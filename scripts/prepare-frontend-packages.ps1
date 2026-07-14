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

$ErrorActionPreference = "Stop"

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
