<#
.SYNOPSIS
    Prepare uv/uvx binary for QA/Offline Docker builds
.DESCRIPTION
    Follows AGENTS.md chapter 12: QA/Offline mode, all deps pre-downloaded to packages/
    Downloads uv binary tarball (x86_64-linux) to packages/uv/uv-x86_64-linux.tar.gz
    Dockerfile.qa / Dockerfile.offline COPY packages/uv/ and install uv/uvx to /usr/local/bin
.NOTES
    Prerequisites: internet access (first run)
    After run: packages/uv/uv-x86_64-linux.tar.gz ready for Dockerfile.qa / Dockerfile.offline
#>

$ErrorActionPreference = "Stop"

# Project root (script in scripts/, root is one level up)
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$uvDir = Join-Path $projectRoot "packages\uv"

# Create directory
New-Item -ItemType Directory -Force -Path $uvDir | Out-Null

# uv version (check https://github.com/astral-sh/uv/releases for latest)
$uvVersion = "0.7.8"
$targetFile = Join-Path $uvDir "uv-x86_64-linux.tar.gz"

Write-Host "========== Prepare uv/uvx binary (QA/Offline) ==========" -ForegroundColor Cyan
Write-Host "Version: $uvVersion" -ForegroundColor DarkGray
Write-Host "Target:  $targetFile" -ForegroundColor DarkGray

# Check if already downloaded
if (Test-Path $targetFile) {
    $sizeMB = [math]::Round((Get-Item $targetFile).Length / 1MB, 1)
    Write-Host "[SKIP] $targetFile already exists ($sizeMB MB)" -ForegroundColor Green
    exit 0
}

# Download from GitHub releases
$downloadUrl = "https://github.com/astral-sh/uv/releases/download/$uvVersion/uv-x86_64-unknown-linux-gnu.tar.gz"
Write-Host "[1/2] Downloading uv $uvVersion from GitHub..." -ForegroundColor Yellow
Write-Host "  URL: $downloadUrl" -ForegroundColor DarkGray

try {
    Invoke-WebRequest -Uri $downloadUrl -OutFile $targetFile -UseBasicParsing
} catch {
    Write-Host "[ERROR] Download failed: $_" -ForegroundColor Red
    Write-Host "  Manual download: $downloadUrl" -ForegroundColor Yellow
    Write-Host "  Place at: $targetFile" -ForegroundColor Yellow
    exit 1
}

# Verify download
if (Test-Path $targetFile) {
    $sizeMB = [math]::Round((Get-Item $targetFile).Length / 1MB, 1)
    Write-Host "[2/2] Download complete: $sizeMB MB" -ForegroundColor Green
} else {
    Write-Host "[ERROR] File not found after download" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "========== Done ==========" -ForegroundColor Cyan
Write-Host "packages/uv/uv-x86_64-linux.tar.gz ready for Dockerfile.qa / Dockerfile.offline" -ForegroundColor Green
